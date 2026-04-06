from __future__ import annotations

from dataclasses import dataclass

from app.services.youtube_service import YouTubeService


@dataclass
class _StubSettings:
    youtube_data_api_key: str | None = None
    youtube_shorts_queries: str = ""
    youtube_shorts_region_code_normalized: str | None = None
    youtube_shorts_preferred_categories: str = ""
    youtube_shorts_category_boost_factor: int = 3


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    def json(self) -> dict:
        return self._payload


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get(self, url: str, params: dict, timeout: int) -> _FakeResponse:
        self.calls.append({"url": url, "params": dict(params), "timeout": timeout})
        page_token = params.get("pageToken")
        if page_token == "token-1":
            return _FakeResponse(
                {
                    "items": [
                        {
                            "id": {"videoId": "video-b"},
                            "snippet": {"title": "B again", "channelTitle": "Channel B"},
                        },
                        {
                            "id": {"videoId": "video-c"},
                            "snippet": {"title": "Video C", "channelTitle": "Channel C"},
                        },
                    ],
                    "nextPageToken": "token-2",
                }
            )
        if page_token == "token-2":
            return _FakeResponse(
                {
                    "items": [
                        {
                            "id": {"videoId": "video-d"},
                            "snippet": {"title": "Video D", "channelTitle": "Channel D"},
                        }
                    ]
                }
            )

        return _FakeResponse(
            {
                "items": [
                    {
                        "id": {"videoId": "video-a"},
                        "snippet": {"title": "Video A", "channelTitle": "Channel A"},
                    },
                    {
                        "id": {"videoId": "video-b"},
                        "snippet": {"title": "Video B", "channelTitle": "Channel B"},
                    },
                ],
                "nextPageToken": "token-1",
            }
        )


class _PublicFeedService(YouTubeService):
    def __init__(self) -> None:
        super().__init__(settings=_StubSettings(youtube_data_api_key="", youtube_shorts_queries="Tech::fast gadgets"))
        self.initial_calls = 0
        self.continuation_calls = 0

    def _fetch_public_initial_page(self, query_seed):  # type: ignore[override]
        _ = query_seed
        self.initial_calls += 1
        return {
            "items": [
                {"id": "pub-a", "title": "A", "channel": "C1", "category": "Tech"},
                {"id": "pub-b", "title": "B", "channel": "C2", "category": "Tech"},
            ],
            "continuation": "cont-1",
            "apiKey": "public-api-key",
            "clientVersion": "2.0.0",
        }

    def _fetch_public_continuation_page(self, continuation, api_key, client_version, default_category):  # type: ignore[override]
        _ = (api_key, client_version, default_category)
        self.continuation_calls += 1
        if continuation == "cont-1":
            return {
                "items": [
                    {"id": "pub-b", "title": "B again", "channel": "C2", "category": "Tech"},
                    {"id": "pub-c", "title": "C", "channel": "C3", "category": "Tech"},
                    {"id": "pub-d", "title": "D", "channel": "C4", "category": "Tech"},
                ],
                "continuation": "cont-2",
            }
        return {
            "items": [
                {"id": "pub-e", "title": "E", "channel": "C5", "category": "Tech"},
            ],
            "continuation": None,
        }


class _BrokenPublicFeedService(YouTubeService):
    def __init__(self) -> None:
        super().__init__(settings=_StubSettings(youtube_data_api_key="", youtube_shorts_queries="Tech::fast gadgets"))

    def _fetch_public_initial_page(self, query_seed):  # type: ignore[override]
        _ = query_seed
        raise RuntimeError("public source unavailable")

    def _fetch_public_continuation_page(self, continuation, api_key, client_version, default_category):  # type: ignore[override]
        _ = (continuation, api_key, client_version, default_category)
        raise RuntimeError("public source unavailable")


def test_fetch_shorts_feed_without_api_key_uses_public_feed_continuation() -> None:
    service = _PublicFeedService()

    first = service.fetch_shorts_feed(cursor=None, limit=2)
    second = service.fetch_shorts_feed(cursor=first["nextCursor"], limit=2)

    assert first["source"] == "youtube"
    assert [item["id"] for item in first["items"]] == ["pub-a", "pub-b"]
    assert [item["id"] for item in second["items"]] == ["pub-c", "pub-d"]
    assert service.initial_calls == 1
    assert service.continuation_calls >= 1


def test_fetch_shorts_feed_falls_back_when_public_sources_fail() -> None:
    service = _BrokenPublicFeedService()

    result = service.fetch_shorts_feed(cursor=None, limit=3)

    assert result["source"] == "fallback"
    assert len(result["items"]) == 3


def test_fetch_shorts_feed_uses_cursor_and_dedupes_seen_ids() -> None:
    session = _FakeSession()
    service = YouTubeService(
        settings=_StubSettings(
            youtube_data_api_key="test-key",
            youtube_shorts_queries="Tech::fast gadgets",
            youtube_shorts_region_code_normalized="US",
        ),
        session=session,
    )

    first = service.fetch_shorts_feed(cursor=None, limit=2)
    second = service.fetch_shorts_feed(cursor=first["nextCursor"], limit=2)

    first_ids = [item["id"] for item in first["items"]]
    second_ids = [item["id"] for item in second["items"]]

    assert first["source"] == "youtube"
    assert first_ids == ["video-a", "video-b"]
    assert second_ids == ["video-c", "video-d"]
    assert not set(first_ids).intersection(second_ids)

    first_call_params = session.calls[0]["params"]
    assert first_call_params["regionCode"] == "US"
    assert "shorts" in str(first_call_params["q"]).lower()


def test_fetch_shorts_feed_recovers_from_invalid_cursor() -> None:
    service = _PublicFeedService()

    result = service.fetch_shorts_feed(cursor="not-a-valid-cursor", limit=2)

    assert len(result["items"]) == 2
    assert result["nextCursor"]


def test_query_seed_weighting_boosts_preferred_categories() -> None:
    service = YouTubeService(
        settings=_StubSettings(
            youtube_data_api_key="",
            youtube_shorts_queries="Tech::a,Food::b,Travel::c,Nature::d",
            youtube_shorts_preferred_categories="Tech,Food,Travel",
            youtube_shorts_category_boost_factor=3,
        )
    )

    categories = [seed.category for seed in service._query_seeds]  # noqa: SLF001
    assert categories.count("Tech") == 3
    assert categories.count("Food") == 3
    assert categories.count("Travel") == 3
    assert categories.count("Nature") == 1
