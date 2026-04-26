import asyncio
import base64
import json
import logging
import os
import platform
import tempfile
import time
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from playwright.async_api import Browser, Error as PlaywrightError
from playwright.async_api import Playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

_MAX_CAPTURE_WIDTH = 3840
_MAX_CAPTURE_HEIGHT = 3840
_MIN_CAPTURE_WIDTH = 320
_MIN_CAPTURE_HEIGHT = 240
_MAX_NAVIGATION_TIMEOUT_MS = 120_000
_MAX_POST_LOAD_DELAY_MS = 15_000
_MAX_TOTAL_CAPTURE_SECONDS = 150
_MAX_RECORDING_DURATION_SECONDS = 120
_MAX_TOTAL_RECORDING_SECONDS = 180
_BOT_CHALLENGE_KEYWORDS = (
    "captcha",
    "recaptcha",
    "hcaptcha",
    "verify you are human",
    "verify that you are human",
    "are you a robot",
    "unusual traffic",
    "automated queries",
    "bot detection",
    "access denied",
    "checking your browser",
    "just a moment",
    "cloudflare",
    "sorry! something went wrong",
    "sorry something went wrong",
    "enter the characters you see below",
    "type the characters you see in this image",
)

_PLATFORM_MACHINE = platform.machine().lower().strip()
_PLATFORM_PROFILE = ""
_DEFAULT_CAPTURE_CONCURRENCY = 2
_MAX_CAPTURE_CONCURRENCY = 8
_LAUNCH_ARGS: list[str] = []
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("toolhub.browser")


def _log_event(event: str, **fields) -> None:
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    logger.info(json.dumps(payload, sort_keys=True, default=str))


def _is_truthy(value: str | None) -> bool:
    normalized = (value or "").strip().lower()
    return normalized not in {"", "0", "false", "no", "off"}


def _is_arm_machine(machine: str) -> bool:
    candidate = (machine or "").strip().lower()
    return candidate.startswith(("arm", "aarch")) or "arm64" in candidate


def _resolve_platform_profile() -> str:
    requested = (os.getenv("SCREENSHOT_PLATFORM_PROFILE", "auto") or "").strip().lower()
    if requested in {"pi", "raspberry-pi", "arm"}:
        return "pi"
    if requested in {"standard", "x86", "amd64"}:
        return "standard"
    if _is_arm_machine(_PLATFORM_MACHINE):
        return "pi"
    return "standard"


def _build_launch_args(platform_profile: str) -> list[str]:
    args: list[str] = [
        "--disable-dev-shm-usage",
        "--disable-setuid-sandbox",
        "--no-sandbox",
        "--no-zygote",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-breakpad",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-sync",
        "--metrics-recording-only",
        "--mute-audio",
        "--no-first-run",
    ]
    if platform_profile == "pi":
        # Conservative defaults for Raspberry Pi/ARM hosts.
        args.extend(
            [
                "--renderer-process-limit=2",
                "--disable-features=BackForwardCache,MediaRouter,Translate",
            ]
        )
    extra_args = (os.getenv("SCREENSHOT_EXTRA_CHROMIUM_ARGS", "") or "").strip()
    if extra_args:
        args.extend(item.strip() for item in extra_args.split(",") if item.strip())

    deduped: list[str] = []
    seen: set[str] = set()
    for arg in args:
        if arg in seen:
            continue
        seen.add(arg)
        deduped.append(arg)
    return deduped


_PLATFORM_PROFILE = _resolve_platform_profile()
if _PLATFORM_PROFILE == "pi":
    _DEFAULT_CAPTURE_CONCURRENCY = 1
    _MAX_CAPTURE_CONCURRENCY = 4
_LAUNCH_ARGS = _build_launch_args(_PLATFORM_PROFILE)

try:
    _configured_concurrency = int(
        os.getenv("SCREENSHOT_CONCURRENCY", str(_DEFAULT_CAPTURE_CONCURRENCY)) or _DEFAULT_CAPTURE_CONCURRENCY
    )
except ValueError:
    _configured_concurrency = _DEFAULT_CAPTURE_CONCURRENCY
_safe_concurrency = max(1, min(_MAX_CAPTURE_CONCURRENCY, _configured_concurrency))
capture_semaphore = asyncio.Semaphore(_safe_concurrency)

_browser_lock = asyncio.Lock()
_playwright_instance: Playwright | None = None
_browser_instance: Browser | None = None


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    _log_event(
        "browser_service_starting",
        profile=_PLATFORM_PROFILE,
        machine=_PLATFORM_MACHINE or "unknown",
        concurrency=_safe_concurrency,
        warmStart=_is_truthy(os.getenv("SCREENSHOT_WARM_START", "1")),
    )
    if _is_truthy(os.getenv("SCREENSHOT_WARM_START", "1")):
        await _ensure_browser()
    try:
        yield
    finally:
        _log_event("browser_service_stopping")
        await _shutdown_browser_runtime()


app = FastAPI(title="ToolHub Browser Screenshot Service", lifespan=_lifespan)


class ScreenshotRequest(BaseModel):
    url: str = Field(min_length=1, max_length=4096)
    width: int = Field(default=1366, ge=_MIN_CAPTURE_WIDTH, le=_MAX_CAPTURE_WIDTH)
    height: int = Field(default=900, ge=_MIN_CAPTURE_HEIGHT, le=_MAX_CAPTURE_HEIGHT)
    fullPage: bool = True
    waitUntil: Literal["load", "domcontentloaded", "networkidle", "commit"] = "networkidle"
    navigationTimeoutMs: int = Field(default=30_000, ge=2_000, le=_MAX_NAVIGATION_TIMEOUT_MS)
    postLoadDelayMs: int = Field(default=700, ge=0, le=_MAX_POST_LOAD_DELAY_MS)


class ScreenshotResponse(BaseModel):
    contentType: str = "image/png"
    imageBase64: str
    pageTitle: str | None = None
    finalUrl: str | None = None
    isBlocked: bool = False
    blockReason: str | None = None
    width: int
    height: int


class BrowserRecordingRequest(BaseModel):
    url: str = Field(min_length=1, max_length=4096)
    width: int = Field(default=1366, ge=_MIN_CAPTURE_WIDTH, le=_MAX_CAPTURE_WIDTH)
    height: int = Field(default=900, ge=_MIN_CAPTURE_HEIGHT, le=_MAX_CAPTURE_HEIGHT)
    durationSeconds: int = Field(default=10, ge=1, le=_MAX_RECORDING_DURATION_SECONDS)
    waitUntil: Literal["load", "domcontentloaded", "networkidle", "commit"] = "networkidle"
    navigationTimeoutMs: int = Field(default=30_000, ge=2_000, le=_MAX_NAVIGATION_TIMEOUT_MS)
    postLoadDelayMs: int = Field(default=700, ge=0, le=_MAX_POST_LOAD_DELAY_MS)


class BrowserRecordingResponse(BaseModel):
    contentType: str = "video/webm"
    videoBase64: str
    pageTitle: str | None = None
    finalUrl: str | None = None
    isBlocked: bool = False
    blockReason: str | None = None
    width: int
    height: int
    durationSeconds: int


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "profile": _PLATFORM_PROFILE,
        "machine": _PLATFORM_MACHINE or "unknown",
        "concurrency": str(_safe_concurrency),
    }


@app.post("/screenshot", response_model=ScreenshotResponse)
async def screenshot(payload: ScreenshotRequest) -> ScreenshotResponse:
    request_id = uuid4().hex[:12]
    started_at = time.monotonic()
    normalized_url = payload.url.strip()
    if not normalized_url.lower().startswith(("http://", "https://")):
        _log_event("screenshot_rejected", requestId=request_id, reason="unsupported_url")
        raise HTTPException(status_code=400, detail="Only http:// and https:// URLs are supported.")

    _log_event(
        "screenshot_requested",
        requestId=request_id,
        url=normalized_url,
        width=payload.width,
        height=payload.height,
        fullPage=payload.fullPage,
        waitUntil=payload.waitUntil,
        navigationTimeoutMs=payload.navigationTimeoutMs,
        postLoadDelayMs=payload.postLoadDelayMs,
    )
    async with capture_semaphore:
        try:
            image_bytes, title, final_url, block_reason = await asyncio.wait_for(
                _capture_page(payload=payload, normalized_url=normalized_url, request_id=request_id),
                timeout=_MAX_TOTAL_CAPTURE_SECONDS,
            )
        except PlaywrightTimeoutError as exc:
            _log_event("screenshot_failed", requestId=request_id, errorType="playwright_timeout", error=str(exc))
            raise HTTPException(status_code=504, detail=f"Timed out while loading page: {exc}") from exc
        except TimeoutError as exc:
            _log_event("screenshot_failed", requestId=request_id, errorType="total_timeout", error=str(exc))
            raise HTTPException(status_code=504, detail="Timed out while capturing screenshot.") from exc
        except HTTPException:
            raise
        except Exception as exc:  # pylint: disable=broad-except
            _log_event("screenshot_failed", requestId=request_id, errorType=type(exc).__name__, error=str(exc))
            raise HTTPException(status_code=502, detail=f"Unable to capture screenshot: {exc}") from exc

    encoded = base64.b64encode(image_bytes).decode("ascii")
    _log_event(
        "screenshot_completed",
        requestId=request_id,
        finalUrl=final_url,
        pageTitle=(title or "")[:120],
        isBlocked=bool(block_reason),
        blockReason=block_reason,
        imageBytes=len(image_bytes),
        elapsedMs=int((time.monotonic() - started_at) * 1000),
    )
    return ScreenshotResponse(
        imageBase64=encoded,
        pageTitle=title.strip()[:160] if title else None,
        finalUrl=final_url,
        isBlocked=bool(block_reason),
        blockReason=block_reason,
        width=payload.width,
        height=payload.height,
    )


@app.post("/record", response_model=BrowserRecordingResponse)
async def record(payload: BrowserRecordingRequest) -> BrowserRecordingResponse:
    request_id = uuid4().hex[:12]
    started_at = time.monotonic()
    normalized_url = payload.url.strip()
    if not normalized_url.lower().startswith(("http://", "https://")):
        _log_event("recording_rejected", requestId=request_id, reason="unsupported_url")
        raise HTTPException(status_code=400, detail="Only http:// and https:// URLs are supported.")

    _log_event(
        "recording_requested",
        requestId=request_id,
        url=normalized_url,
        width=payload.width,
        height=payload.height,
        durationSeconds=payload.durationSeconds,
        waitUntil=payload.waitUntil,
        navigationTimeoutMs=payload.navigationTimeoutMs,
        postLoadDelayMs=payload.postLoadDelayMs,
    )
    async with capture_semaphore:
        try:
            video_bytes, title, final_url, block_reason = await asyncio.wait_for(
                _record_page(payload=payload, normalized_url=normalized_url, request_id=request_id),
                timeout=_MAX_TOTAL_RECORDING_SECONDS,
            )
        except PlaywrightTimeoutError as exc:
            _log_event("recording_failed", requestId=request_id, errorType="playwright_timeout", error=str(exc))
            raise HTTPException(status_code=504, detail=f"Timed out while loading page: {exc}") from exc
        except TimeoutError as exc:
            _log_event("recording_failed", requestId=request_id, errorType="total_timeout", error=str(exc))
            raise HTTPException(status_code=504, detail="Timed out while recording browser session.") from exc
        except HTTPException:
            raise
        except Exception as exc:  # pylint: disable=broad-except
            _log_event("recording_failed", requestId=request_id, errorType=type(exc).__name__, error=str(exc))
            raise HTTPException(status_code=502, detail=f"Unable to record browser session: {exc}") from exc

    encoded = base64.b64encode(video_bytes).decode("ascii")
    _log_event(
        "recording_completed",
        requestId=request_id,
        finalUrl=final_url,
        pageTitle=(title or "")[:120],
        isBlocked=bool(block_reason),
        blockReason=block_reason,
        videoBytes=len(video_bytes),
        durationSeconds=payload.durationSeconds,
        elapsedMs=int((time.monotonic() - started_at) * 1000),
    )
    return BrowserRecordingResponse(
        videoBase64=encoded,
        pageTitle=title.strip()[:160] if title else None,
        finalUrl=final_url,
        isBlocked=bool(block_reason),
        blockReason=block_reason,
        width=payload.width,
        height=payload.height,
        durationSeconds=payload.durationSeconds,
    )


async def _capture_page(
    payload: ScreenshotRequest,
    normalized_url: str,
    request_id: str,
) -> tuple[bytes, str | None, str | None, str | None]:
    last_error: Exception | None = None
    for attempt_index in range(2):
        browser = await _ensure_browser()
        context = None
        try:
            context = await browser.new_context(
                viewport={"width": payload.width, "height": payload.height},
                device_scale_factor=1,
            )
            page = await context.new_page()
            _log_event("screenshot_navigation_started", requestId=request_id, attempt=attempt_index + 1, waitUntil=payload.waitUntil)
            response = await page.goto(
                normalized_url,
                wait_until=payload.waitUntil,
                timeout=payload.navigationTimeoutMs,
            )
            response_status = response.status if response is not None else None
            if payload.postLoadDelayMs > 0:
                await page.wait_for_timeout(payload.postLoadDelayMs)
            title = await page.title()
            final_url = page.url
            block_reason = await _detect_bot_challenge(page=page, title=title, final_url=final_url)
            image_bytes = await page.screenshot(type="png", full_page=payload.fullPage)
            _log_event(
                "screenshot_attempt_completed",
                requestId=request_id,
                attempt=attempt_index + 1,
                finalUrl=final_url,
                isBlocked=bool(block_reason),
                responseStatus=response_status,
                imageBytes=len(image_bytes),
            )
            return image_bytes, title, final_url, block_reason
        except PlaywrightError as exc:
            last_error = exc
            _log_event(
                "screenshot_attempt_failed",
                requestId=request_id,
                attempt=attempt_index + 1,
                errorType=type(exc).__name__,
                error=str(exc),
            )
            await _shutdown_browser_runtime()
            if attempt_index == 1:
                raise
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:  # pylint: disable=broad-except
                    pass

    if last_error is not None:
        raise last_error
    raise RuntimeError("Unable to capture screenshot because the browser runtime is unavailable.")


async def _record_page(
    payload: BrowserRecordingRequest,
    normalized_url: str,
    request_id: str,
) -> tuple[bytes, str | None, str | None, str | None]:
    last_error: Exception | None = None
    for attempt_index in range(2):
        browser = await _ensure_browser()
        context = None
        try:
            with tempfile.TemporaryDirectory(prefix="toolhub-browser-recording-") as temp_dir:
                context = await browser.new_context(
                    viewport={"width": payload.width, "height": payload.height},
                    device_scale_factor=1,
                    record_video_dir=temp_dir,
                    record_video_size={"width": payload.width, "height": payload.height},
                )
                page = await context.new_page()
                _log_event(
                    "recording_navigation_started",
                    requestId=request_id,
                    attempt=attempt_index + 1,
                    waitUntil=payload.waitUntil,
                    durationSeconds=payload.durationSeconds,
                )
                response = await page.goto(
                    normalized_url,
                    wait_until=payload.waitUntil,
                    timeout=payload.navigationTimeoutMs,
                )
                response_status = response.status if response is not None else None
                if payload.postLoadDelayMs > 0:
                    await page.wait_for_timeout(payload.postLoadDelayMs)
                title = await page.title()
                final_url = page.url
                block_reason = await _detect_bot_challenge(page=page, title=title, final_url=final_url)
                _log_event(
                    "recording_started",
                    requestId=request_id,
                    attempt=attempt_index + 1,
                    finalUrl=final_url,
                    isBlocked=bool(block_reason),
                    responseStatus=response_status,
                    durationSeconds=payload.durationSeconds,
                )
                await page.wait_for_timeout(payload.durationSeconds * 1000)
                video = page.video
                await context.close()
                context = None
                if video is None:
                    raise RuntimeError("Browser did not produce a video recording.")
                video_path = Path(await video.path())
                video_bytes = video_path.read_bytes()
                if not video_bytes:
                    raise RuntimeError("Browser produced an empty video recording.")
                _log_event(
                    "recording_attempt_completed",
                    requestId=request_id,
                    attempt=attempt_index + 1,
                    finalUrl=final_url,
                    isBlocked=bool(block_reason),
                    responseStatus=response_status,
                    videoBytes=len(video_bytes),
                )
                return video_bytes, title, final_url, block_reason
        except PlaywrightError as exc:
            last_error = exc
            _log_event(
                "recording_attempt_failed",
                requestId=request_id,
                attempt=attempt_index + 1,
                waitUntil=payload.waitUntil,
                errorType=type(exc).__name__,
                error=str(exc),
            )
            await _shutdown_browser_runtime()
            if attempt_index == 1:
                raise
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:  # pylint: disable=broad-except
                    pass

    if last_error is not None:
        raise last_error
    raise RuntimeError("Unable to record browser session because the browser runtime is unavailable.")


async def _detect_bot_challenge(page, title: str | None, final_url: str | None) -> str | None:
    signals: list[str] = []
    title_text = (title or "").strip()
    url_text = (final_url or "").strip()
    try:
        body_text = await page.locator("body").inner_text(timeout=1500)
    except Exception:  # pylint: disable=broad-except
        body_text = ""
    haystack = " ".join([title_text, url_text, body_text[:4000]]).lower()
    for keyword in _BOT_CHALLENGE_KEYWORDS:
        if keyword in haystack:
            signals.append(keyword)
    if not signals:
        return None
    unique_signals = ", ".join(dict.fromkeys(signals[:4]))
    return f"Page appears to require human verification or is blocking automated browsing ({unique_signals})."


async def _ensure_browser() -> Browser:
    global _browser_instance  # pylint: disable=global-statement
    global _playwright_instance  # pylint: disable=global-statement

    async with _browser_lock:
        if _browser_instance is not None and _browser_instance.is_connected():
            return _browser_instance
        if _playwright_instance is None:
            _log_event("playwright_starting")
            _playwright_instance = await async_playwright().start()
        _log_event("browser_launching", profile=_PLATFORM_PROFILE, args=_LAUNCH_ARGS)
        _browser_instance = await _playwright_instance.chromium.launch(
            headless=True,
            args=_LAUNCH_ARGS,
        )
        _log_event("browser_launched")
        return _browser_instance


async def _shutdown_browser_runtime() -> None:
    global _browser_instance  # pylint: disable=global-statement
    global _playwright_instance  # pylint: disable=global-statement

    async with _browser_lock:
        if _browser_instance is not None:
            try:
                _log_event("browser_closing")
                await _browser_instance.close()
            except Exception:  # pylint: disable=broad-except
                pass
            _browser_instance = None
        if _playwright_instance is not None:
            try:
                _log_event("playwright_stopping")
                await _playwright_instance.stop()
            except Exception:  # pylint: disable=broad-except
                pass
            _playwright_instance = None
