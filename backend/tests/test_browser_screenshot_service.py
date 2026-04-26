from types import SimpleNamespace

from app.services.browser_screenshot_service import BrowserScreenshotService


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        browser_screenshot_internal_base_url="http://browser_screenshot:4300",
        browser_screenshot_timeout_seconds=80,
        browser_screenshot_navigation_timeout_ms=30_000,
        browser_screenshot_post_load_delay_ms=700,
        browser_screenshot_max_image_bytes=20 * 1024 * 1024,
        browser_screenshot_max_video_bytes=80 * 1024 * 1024,
    )


def test_get_session_reports_running_on_healthy_response(monkeypatch) -> None:
    class _FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> dict[str, str]:
            return {"status": "ok"}

    def _fake_get(url: str, timeout: int):  # type: ignore[no-untyped-def]
        assert url == "http://browser_screenshot:4300/health"
        assert timeout == 8
        return _FakeResponse()

    monkeypatch.setattr("app.services.browser_screenshot_service.requests.get", _fake_get)

    service = BrowserScreenshotService(settings=_settings())  # type: ignore[arg-type]
    payload = service.get_session()

    assert payload["running"] is True
    assert payload["baseUrl"] == "http://browser_screenshot:4300"


def test_capture_screenshot_decodes_payload(monkeypatch) -> None:
    class _FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "contentType": "image/png",
                "imageBase64": "iVBORw0KGgo=",
                "pageTitle": "Example",
                "finalUrl": "https://example.com/",
                "width": 1366,
                "height": 900,
            }

    def _fake_post(url: str, json: dict[str, object], timeout: int):  # type: ignore[no-untyped-def]
        assert url == "http://browser_screenshot:4300/screenshot"
        assert json["url"] == "https://example.com/"
        assert timeout == 80
        return _FakeResponse()

    monkeypatch.setattr("app.services.browser_screenshot_service.requests.post", _fake_post)

    service = BrowserScreenshotService(settings=_settings())  # type: ignore[arg-type]
    payload = service.capture_screenshot(url="https://example.com/", width=1366, height=900, full_page=True)

    assert payload["contentType"] == "image/png"
    assert payload["pageTitle"] == "Example"
    assert payload["finalUrl"] == "https://example.com/"
    assert payload["isBlocked"] is False
    assert isinstance(payload["imageBytes"], bytes)
    assert payload["imageBytes"] == b"\x89PNG\r\n\x1a\n"


def test_capture_screenshot_preserves_bot_challenge_metadata(monkeypatch) -> None:
    class _FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "contentType": "image/png",
                "imageBase64": "iVBORw0KGgo=",
                "pageTitle": "Just a moment...",
                "finalUrl": "https://example.com/",
                "isBlocked": True,
                "blockReason": "Page appears to require human verification.",
                "width": 1366,
                "height": 900,
            }

    monkeypatch.setattr("app.services.browser_screenshot_service.requests.post", lambda *_args, **_kwargs: _FakeResponse())

    service = BrowserScreenshotService(settings=_settings())  # type: ignore[arg-type]
    payload = service.capture_screenshot(url="https://example.com/", width=1366, height=900, full_page=True)

    assert payload["isBlocked"] is True
    assert payload["blockReason"] == "Page appears to require human verification."


def test_capture_screenshot_normalizes_portrait_dimensions_to_landscape(monkeypatch) -> None:
    class _FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "contentType": "image/png",
                "imageBase64": "iVBORw0KGgo=",
                "pageTitle": "Example",
                "finalUrl": "https://example.com/",
                "width": 1400,
                "height": 900,
            }

    def _fake_post(url: str, json: dict[str, object], timeout: int):  # type: ignore[no-untyped-def]
        assert url == "http://browser_screenshot:4300/screenshot"
        assert timeout == 80
        assert json["width"] == 1400
        assert json["height"] == 900
        return _FakeResponse()

    monkeypatch.setattr("app.services.browser_screenshot_service.requests.post", _fake_post)

    service = BrowserScreenshotService(settings=_settings())  # type: ignore[arg-type]
    payload = service.capture_screenshot(url="https://example.com/", width=900, height=1400, full_page=False)

    assert payload["width"] == 1400
    assert payload["height"] == 900


def test_capture_screenshot_retries_with_alternate_wait_strategy(monkeypatch) -> None:
    class _GatewayTimeoutResponse:
        status_code = 504

        @staticmethod
        def json() -> dict[str, object]:
            return {"detail": "Timed out while loading page"}

    class _OkResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "contentType": "image/png",
                "imageBase64": "iVBORw0KGgo=",
                "pageTitle": "Amazon",
                "finalUrl": "https://www.amazon.com/s?k=shoes",
                "width": 1366,
                "height": 900,
            }

    calls: list[str] = []

    def _fake_post(url: str, json: dict[str, object], timeout: int):  # type: ignore[no-untyped-def]
        assert url == "http://browser_screenshot:4300/screenshot"
        assert timeout == 80
        calls.append(str(json.get("waitUntil")))
        if len(calls) == 1:
            return _GatewayTimeoutResponse()
        return _OkResponse()

    monkeypatch.setattr("app.services.browser_screenshot_service.requests.post", _fake_post)

    service = BrowserScreenshotService(settings=_settings())  # type: ignore[arg-type]
    payload = service.capture_screenshot(
        url="https://www.amazon.com/s?k=shoes",
        width=1366,
        height=900,
        full_page=True,
    )

    assert calls[:2] == ["networkidle", "load"]
    assert payload["pageTitle"] == "Amazon"
    assert payload["finalUrl"] == "https://www.amazon.com/s?k=shoes"


def test_record_browser_session_decodes_payload(monkeypatch) -> None:
    class _FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "contentType": "video/webm",
                "videoBase64": "dmlkZW8=",
                "pageTitle": "Example",
                "finalUrl": "https://example.com/",
                "width": 1366,
                "height": 900,
                "durationSeconds": 8,
            }

    def _fake_post(url: str, json: dict[str, object], timeout: int):  # type: ignore[no-untyped-def]
        assert url == "http://browser_screenshot:4300/record"
        assert json["url"] == "https://example.com/"
        assert json["durationSeconds"] == 8
        assert json["waitUntil"] == "load"
        assert timeout == 80
        return _FakeResponse()

    monkeypatch.setattr("app.services.browser_screenshot_service.requests.post", _fake_post)

    service = BrowserScreenshotService(settings=_settings())  # type: ignore[arg-type]
    payload = service.record_browser_session(
        url="https://example.com/",
        width=1366,
        height=900,
        duration_seconds=8,
    )

    assert payload["contentType"] == "video/webm"
    assert payload["pageTitle"] == "Example"
    assert payload["finalUrl"] == "https://example.com/"
    assert payload["durationSeconds"] == 8
    assert payload["videoBytes"] == b"video"


def test_record_browser_session_retries_with_faster_wait_strategy(monkeypatch) -> None:
    class _GatewayTimeoutResponse:
        status_code = 504

        @staticmethod
        def json() -> dict[str, object]:
            return {"detail": "Timed out while loading page"}

    class _OkResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "contentType": "video/webm",
                "videoBase64": "dmlkZW8=",
                "pageTitle": "Amazon",
                "finalUrl": "https://www.amazon.com/s?k=tv+cabinet",
                "width": 1366,
                "height": 900,
                "durationSeconds": 10,
            }

    calls: list[str] = []

    def _fake_post(url: str, json: dict[str, object], timeout: int):  # type: ignore[no-untyped-def]
        assert url == "http://browser_screenshot:4300/record"
        assert timeout == 80
        calls.append(str(json.get("waitUntil")))
        if len(calls) == 1:
            return _GatewayTimeoutResponse()
        return _OkResponse()

    monkeypatch.setattr("app.services.browser_screenshot_service.requests.post", _fake_post)

    service = BrowserScreenshotService(settings=_settings())  # type: ignore[arg-type]
    payload = service.record_browser_session(
        url="https://www.amazon.com/s?k=tv+cabinet",
        width=1366,
        height=900,
        duration_seconds=10,
    )

    assert calls[:2] == ["load", "domcontentloaded"]
    assert payload["pageTitle"] == "Amazon"
    assert payload["finalUrl"] == "https://www.amazon.com/s?k=tv+cabinet"
    assert payload["videoBytes"] == b"video"
