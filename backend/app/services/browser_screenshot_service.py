import base64
from typing import Any

import requests

from app.utils.config import Settings


class BrowserScreenshotService:
    def __init__(self, settings: Settings) -> None:
        base_url = (settings.browser_screenshot_internal_base_url or "").strip()
        self._base_url = base_url.rstrip("/") if base_url else "http://browser_screenshot:4300"
        self._timeout_seconds = max(int(settings.browser_screenshot_timeout_seconds or 0), 10)
        self._navigation_timeout_ms = max(int(settings.browser_screenshot_navigation_timeout_ms or 0), 2_000)
        self._post_load_delay_ms = max(int(settings.browser_screenshot_post_load_delay_ms or 0), 0)
        self._max_image_bytes = max(int(settings.browser_screenshot_max_image_bytes or 0), 1024 * 1024)

    def get_session(self) -> dict[str, Any]:
        health_url = f"{self._base_url}/health"
        try:
            response = requests.get(health_url, timeout=min(self._timeout_seconds, 8))
            response.raise_for_status()
            payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            status_value = str(payload.get("status") or "").strip().lower() if isinstance(payload, dict) else ""
            healthy = status_value == "ok"
            return {
                "running": healthy,
                "baseUrl": self._base_url,
                "message": "Browser screenshot container is ready." if healthy else "Browser screenshot container is unavailable.",
            }
        except requests.RequestException as exc:
            return {
                "running": False,
                "baseUrl": self._base_url,
                "message": f"Browser screenshot container is unavailable: {exc}",
            }

    def capture_screenshot(
        self,
        url: str,
        *,
        width: int,
        height: int,
        full_page: bool,
    ) -> dict[str, Any]:
        endpoint = f"{self._base_url}/screenshot"
        attempts: list[tuple[str, int, int]] = [
            ("networkidle", self._navigation_timeout_ms, self._post_load_delay_ms),
            ("load", min(self._navigation_timeout_ms, 22_000), self._post_load_delay_ms),
            ("domcontentloaded", min(self._navigation_timeout_ms, 15_000), min(self._post_load_delay_ms, 500)),
        ]
        viewport_width, viewport_height = self._normalize_landscape_dimensions(width=width, height=height)

        body: dict[str, Any] | None = None
        last_error: str | None = None
        for wait_until, navigation_timeout_ms, post_load_delay_ms in attempts:
            payload = {
                "url": url,
                "width": viewport_width,
                "height": viewport_height,
                "fullPage": bool(full_page),
                "waitUntil": wait_until,
                "navigationTimeoutMs": int(navigation_timeout_ms),
                "postLoadDelayMs": int(post_load_delay_ms),
            }
            try:
                response = requests.post(endpoint, json=payload, timeout=self._timeout_seconds)
            except requests.RequestException as exc:
                last_error = f"Screenshot request failed: {exc}"
                continue

            if response.status_code >= 400:
                details = self._extract_error_details(response)
                last_error = f"Screenshot request failed (HTTP {response.status_code}): {details}"
                if response.status_code in {502, 503, 504}:
                    continue
                raise RuntimeError(last_error)

            try:
                parsed_body = response.json()
            except ValueError as exc:
                raise RuntimeError("Screenshot service returned non-JSON response.") from exc
            if isinstance(parsed_body, dict):
                body = parsed_body
                break
            last_error = "Screenshot service returned invalid JSON payload."

        if body is None:
            raise RuntimeError(last_error or "Screenshot request failed.")

        image_b64 = str(body.get("imageBase64") or "").strip()
        if not image_b64:
            raise RuntimeError("Screenshot response did not include image data.")
        try:
            image_bytes = base64.b64decode(image_b64, validate=True)
        except ValueError as exc:
            raise RuntimeError("Screenshot response included invalid base64 image data.") from exc
        if not image_bytes:
            raise RuntimeError("Screenshot response included empty image data.")
        if len(image_bytes) > self._max_image_bytes:
            raise RuntimeError("Screenshot image is too large to store.")

        return {
            "imageBytes": image_bytes,
            "contentType": str(body.get("contentType") or "image/png"),
            "pageTitle": str(body.get("pageTitle") or "").strip() or None,
            "finalUrl": str(body.get("finalUrl") or "").strip() or None,
            "width": int(body.get("width") or viewport_width),
            "height": int(body.get("height") or viewport_height),
        }

    @staticmethod
    def _normalize_landscape_dimensions(*, width: int, height: int) -> tuple[int, int]:
        normalized_width = max(640, min(3840, int(width)))
        normalized_height = max(480, min(3840, int(height)))
        if normalized_height > normalized_width:
            normalized_width, normalized_height = normalized_height, normalized_width
        return normalized_width, normalized_height

    @staticmethod
    def _extract_error_details(response: requests.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            text = (response.text or "").strip()
            return text[:260] if text else "No error details were returned."

        if isinstance(body, dict):
            detail = body.get("detail")
            if isinstance(detail, str) and detail.strip():
                return detail.strip()[:260]
            if detail is not None:
                serialized = str(detail).strip()
                if serialized:
                    return serialized[:260]
        serialized_body = str(body).strip()
        return serialized_body[:260] if serialized_body else "No error details were returned."
