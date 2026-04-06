import base64
import json
import re
from dataclasses import dataclass
from typing import Any

import requests

from app.utils.config import Settings


_DEFAULT_QUERY_SEEDS: tuple[str, ...] = (
    "Nature::breathtaking nature views",
    "Travel::hidden travel gems",
    "Food::street food shorts",
    "Tech::latest tech hacks",
    "Science::mind blowing science facts",
    "Art::creative art process",
    "Animals::cute animals daily",
    "Fitness::quick fitness tips",
    "Comedy::funny clips compilation",
    "Music::music performance clips",
)
_DEFAULT_FALLBACK_SHORTS: tuple[dict[str, str], ...] = (
    {
        "id": "y72ZjofvLLo",
        "title": "Place On Earth That Doesn't Feel Real",
        "channel": "relax vibe",
        "category": "Nature",
    },
    {
        "id": "G9NRzrx7m4U",
        "title": "Switzerland 4K",
        "channel": "Smart info",
        "category": "Nature",
    },
    {
        "id": "86N5GBzlClU",
        "title": "Beautiful View",
        "channel": "BeautyVibe",
        "category": "Nature",
    },
    {
        "id": "8g_fxFoptOk",
        "title": "Train Food In India",
        "channel": "Adrija Roy",
        "category": "Travel",
    },
    {
        "id": "6kstnFaD9OI",
        "title": "Crazy Gadgets",
        "channel": "Tech Master Shorts",
        "category": "Tech",
    },
    {
        "id": "iuLgq7bf6QY",
        "title": "New Innovation",
        "channel": "Your Mahbub",
        "category": "Tech",
    },
    {
        "id": "Tu-uNTx5-7A",
        "title": "Matka Maggi Street Food",
        "channel": "Indori Foodist",
        "category": "Food",
    },
    {
        "id": "tDEM3P7eRRg",
        "title": "Would You Eat This?",
        "channel": "Zach Choi",
        "category": "Food",
    },
    {
        "id": "FpzNVwM5N9U",
        "title": "Super Cars In India",
        "channel": "Karthik Gilly",
        "category": "City",
    },
    {
        "id": "M1nuGREjhKw",
        "title": "School Shopping Mini Vlog",
        "channel": "nishi tiwari",
        "category": "City",
    },
    {
        "id": "-TjojsxYU6U",
        "title": "Funny Animals",
        "channel": "javeed hashim 94",
        "category": "Animals",
    },
    {
        "id": "MObqFN_Jr6U",
        "title": "How To Unload Lions",
        "channel": "The Lion Whisperer",
        "category": "Animals",
    },
    {
        "id": "X6NLp_p7QWw",
        "title": "DIY Window Clings",
        "channel": "Mukta easy drawing",
        "category": "Art",
    },
    {
        "id": "9aBz4G5OfGg",
        "title": "Digital Art",
        "channel": "WhArt",
        "category": "Art",
    },
    {
        "id": "9dMFxnpEkKc",
        "title": "Newton's Apple",
        "channel": "Sick Science!",
        "category": "Science",
    },
    {
        "id": "FdoQ4zT929Q",
        "title": "The Rarest Rainfalls",
        "channel": "MisterUniverse",
        "category": "Science",
    },
)


@dataclass(frozen=True)
class QuerySeed:
    category: str
    query: str


class YouTubeService:
    _YOUTUBE_SEARCH_ENDPOINT = "https://www.googleapis.com/youtube/v3/search"
    _YOUTUBE_WEB_RESULTS_ENDPOINT = "https://www.youtube.com/results"
    _YOUTUBE_INNERTUBE_SEARCH_ENDPOINT = "https://www.youtube.com/youtubei/v1/search"
    _CURSOR_VERSION = 2
    _MAX_LIMIT = 40
    _MIN_LIMIT = 1
    _MAX_SEEN_IDS = 5000
    _HTTP_TIMEOUT_SECONDS = 8
    _PUBLIC_SHORTS_FILTER_PARAM = "EgIYAQ=="
    _PUBLIC_CLIENT_NAME = "WEB"
    _PUBLIC_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        self._api_key = (settings.youtube_data_api_key or "").strip()
        self._region_code = settings.youtube_shorts_region_code_normalized
        preferred_categories = self._parse_preferred_categories(
            getattr(settings, "youtube_shorts_preferred_categories", "")
        )
        boost_factor = self._normalize_boost_factor(
            getattr(settings, "youtube_shorts_category_boost_factor", 3)
        )
        self._query_seeds = self._parse_query_seeds(
            settings.youtube_shorts_queries,
            preferred_categories=preferred_categories,
            boost_factor=boost_factor,
        )
        self._session = session or requests.Session()

    def fetch_shorts_feed(self, cursor: str | None, limit: int = 24) -> dict[str, Any]:
        normalized_limit = max(self._MIN_LIMIT, min(self._MAX_LIMIT, int(limit)))
        state = self._decode_cursor(cursor)

        seen_ids: list[str] = [item for item in state["seenIds"] if isinstance(item, str) and item]
        seen_set = set(seen_ids)
        query_index = state["queryIndex"] % len(self._query_seeds)
        fallback_index = state["fallbackIndex"]

        collected: list[dict[str, str]] = []
        source = "youtube"

        if self._api_key:
            page_tokens = {
                key: value
                for key, value in state["pageTokens"].items()
                if isinstance(key, str) and isinstance(value, str) and key and value
            }
            collected, query_index, page_tokens = self._collect_from_data_api(
                desired=normalized_limit,
                query_index=query_index,
                page_tokens=page_tokens,
                seen_ids=seen_ids,
                seen_set=seen_set,
            )
            next_cursor_state: dict[str, Any] = {
                "queryIndex": query_index,
                "pageTokens": page_tokens,
                "continuations": {},
                "seenIds": seen_ids,
                "fallbackIndex": fallback_index,
                "webApiKey": None,
                "webClientVersion": None,
            }
        else:
            continuations = {
                key: value
                for key, value in state["continuations"].items()
                if isinstance(key, str) and isinstance(value, str) and key and value
            }
            public_api_key = state["webApiKey"]
            public_client_version = state["webClientVersion"]
            collected, query_index, continuations, public_api_key, public_client_version = self._collect_from_public_search(
                desired=normalized_limit,
                query_index=query_index,
                continuations=continuations,
                seen_ids=seen_ids,
                seen_set=seen_set,
                public_api_key=public_api_key,
                public_client_version=public_client_version,
            )
            next_cursor_state = {
                "queryIndex": query_index,
                "pageTokens": {},
                "continuations": continuations,
                "seenIds": seen_ids,
                "fallbackIndex": fallback_index,
                "webApiKey": public_api_key,
                "webClientVersion": public_client_version,
            }

        while len(seen_ids) > self._MAX_SEEN_IDS:
            dropped = seen_ids.pop(0)
            if dropped not in seen_ids:
                seen_set.discard(dropped)

        next_cursor_state["seenIds"] = seen_ids

        if len(collected) < normalized_limit:
            source = "fallback"
            fallback_items, fallback_index = self._fallback_items(
                start_index=fallback_index,
                limit=normalized_limit - len(collected),
            )
            collected.extend(fallback_items)
            next_cursor_state["fallbackIndex"] = fallback_index

        next_cursor = self._encode_cursor(next_cursor_state)

        return {
            "items": collected,
            "nextCursor": next_cursor,
            "source": source,
        }

    def _collect_from_data_api(
        self,
        desired: int,
        query_index: int,
        page_tokens: dict[str, str],
        seen_ids: list[str],
        seen_set: set[str],
    ) -> tuple[list[dict[str, str]], int, dict[str, str]]:
        collected: list[dict[str, str]] = []
        attempts = 0
        attempt_budget = max(len(self._query_seeds) * 6, 12)

        while len(collected) < desired and attempts < attempt_budget:
            seed = self._query_seeds[query_index]
            query_index = (query_index + 1) % len(self._query_seeds)
            page_token = page_tokens.get(seed.query)

            try:
                payload = self._fetch_data_api_page(query_seed=seed, page_token=page_token, desired=desired - len(collected))
            except RuntimeError:
                attempts += 1
                continue

            next_page_token = payload.get("nextPageToken")
            if isinstance(next_page_token, str) and next_page_token.strip():
                page_tokens[seed.query] = next_page_token.strip()
            else:
                page_tokens.pop(seed.query, None)

            items = payload.get("items", [])
            for raw_item in items if isinstance(items, list) else []:
                short = self._to_short_item_from_data_api(raw_item, default_category=seed.category)
                if short is None:
                    continue

                short_id = short["id"]
                if short_id in seen_set:
                    continue

                seen_set.add(short_id)
                seen_ids.append(short_id)
                collected.append(short)
                if len(collected) >= desired:
                    break

            attempts += 1

        return collected, query_index, page_tokens

    def _collect_from_public_search(
        self,
        desired: int,
        query_index: int,
        continuations: dict[str, str],
        seen_ids: list[str],
        seen_set: set[str],
        public_api_key: str | None,
        public_client_version: str | None,
    ) -> tuple[list[dict[str, str]], int, dict[str, str], str | None, str | None]:
        collected: list[dict[str, str]] = []
        attempts = 0
        attempt_budget = max(len(self._query_seeds) * 10, 20)

        while len(collected) < desired and attempts < attempt_budget:
            seed = self._query_seeds[query_index]
            query_index = (query_index + 1) % len(self._query_seeds)

            continuation = continuations.get(seed.query)
            try:
                if continuation and public_api_key and public_client_version:
                    payload = self._fetch_public_continuation_page(
                        continuation=continuation,
                        api_key=public_api_key,
                        client_version=public_client_version,
                        default_category=seed.category,
                    )
                else:
                    payload = self._fetch_public_initial_page(seed)
                    public_api_key = payload.get("apiKey") or public_api_key
                    public_client_version = payload.get("clientVersion") or public_client_version
            except RuntimeError:
                continuations.pop(seed.query, None)
                attempts += 1
                continue

            next_continuation = payload.get("continuation")
            if isinstance(next_continuation, str) and next_continuation.strip():
                continuations[seed.query] = next_continuation.strip()
            else:
                continuations.pop(seed.query, None)

            items = payload.get("items", [])
            for short in items if isinstance(items, list) else []:
                if not isinstance(short, dict):
                    continue
                short_id = short.get("id")
                if not isinstance(short_id, str) or not short_id:
                    continue
                if short_id in seen_set:
                    continue

                seen_set.add(short_id)
                seen_ids.append(short_id)
                collected.append(short)
                if len(collected) >= desired:
                    break

            attempts += 1

        return collected, query_index, continuations, public_api_key, public_client_version

    def _fetch_data_api_page(self, query_seed: QuerySeed, page_token: str | None, desired: int) -> dict[str, Any]:
        query = query_seed.query.strip()
        if "shorts" not in query.lower():
            query = f"{query} shorts"

        params: dict[str, str | int] = {
            "part": "snippet",
            "type": "video",
            "videoDuration": "short",
            "safeSearch": "moderate",
            "order": "date",
            "maxResults": max(10, min(50, desired * 2)),
            "q": query,
            "key": self._api_key,
        }
        if self._region_code:
            params["regionCode"] = self._region_code
        if page_token:
            params["pageToken"] = page_token

        try:
            response = self._session.get(
                self._YOUTUBE_SEARCH_ENDPOINT,
                params=params,
                timeout=self._HTTP_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"YouTube API request failed: {exc}") from exc

        if response.status_code == 403:
            raise RuntimeError("YouTube API rejected the request (invalid key or quota exhausted).")
        if not response.ok:
            raise RuntimeError(f"YouTube API request failed with status {response.status_code}.")

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("YouTube API returned invalid JSON.") from exc

        if not isinstance(payload, dict):
            raise RuntimeError("YouTube API returned an unexpected payload.")
        return payload

    def _fetch_public_initial_page(self, query_seed: QuerySeed) -> dict[str, Any]:
        query = query_seed.query.strip()
        if "shorts" not in query.lower():
            query = f"{query} shorts"

        params: dict[str, str] = {
            "search_query": query,
            "sp": self._PUBLIC_SHORTS_FILTER_PARAM,
            "hl": "en",
        }
        if self._region_code:
            params["gl"] = self._region_code

        try:
            response = self._session.get(
                self._YOUTUBE_WEB_RESULTS_ENDPOINT,
                params=params,
                timeout=self._HTTP_TIMEOUT_SECONDS,
                headers={"User-Agent": self._PUBLIC_USER_AGENT},
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"YouTube public feed request failed: {exc}") from exc

        if not response.ok:
            raise RuntimeError(f"YouTube public feed request failed with status {response.status_code}.")

        html = response.text
        initial_data = self._extract_yt_initial_data(html)
        if not initial_data:
            raise RuntimeError("Unable to parse YouTube public feed payload.")

        items = self._extract_video_items_from_payload(initial_data, default_category=query_seed.category)
        continuation = self._extract_continuation_token(initial_data)
        api_key, client_version = self._extract_public_api_context(html)

        return {
            "items": items,
            "continuation": continuation,
            "apiKey": api_key,
            "clientVersion": client_version,
        }

    def _fetch_public_continuation_page(
        self,
        continuation: str,
        api_key: str,
        client_version: str,
        default_category: str,
    ) -> dict[str, Any]:
        payload = {
            "context": {
                "client": {
                    "clientName": self._PUBLIC_CLIENT_NAME,
                    "clientVersion": client_version,
                    "hl": "en",
                }
            },
            "continuation": continuation,
        }

        api_url = f"{self._YOUTUBE_INNERTUBE_SEARCH_ENDPOINT}?key={api_key}"
        try:
            response = self._session.post(
                api_url,
                json=payload,
                timeout=self._HTTP_TIMEOUT_SECONDS,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": self._PUBLIC_USER_AGENT,
                },
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"YouTube continuation request failed: {exc}") from exc

        if not response.ok:
            raise RuntimeError(f"YouTube continuation request failed with status {response.status_code}.")

        try:
            result = response.json()
        except ValueError as exc:
            raise RuntimeError("YouTube continuation payload was not valid JSON.") from exc

        if not isinstance(result, dict):
            raise RuntimeError("YouTube continuation payload was not an object.")

        return {
            "items": self._extract_video_items_from_payload(result, default_category=default_category),
            "continuation": self._extract_continuation_token(result),
        }

    @staticmethod
    def _to_short_item_from_data_api(raw_item: Any, default_category: str) -> dict[str, str] | None:
        if not isinstance(raw_item, dict):
            return None

        identifier = raw_item.get("id")
        if not isinstance(identifier, dict):
            return None

        video_id = identifier.get("videoId")
        if not isinstance(video_id, str) or not video_id.strip():
            return None

        snippet = raw_item.get("snippet")
        if not isinstance(snippet, dict):
            return None

        title = snippet.get("title")
        channel = snippet.get("channelTitle")
        if not isinstance(title, str) or not title.strip():
            return None
        if not isinstance(channel, str) or not channel.strip():
            channel = "YouTube"

        return {
            "id": video_id.strip(),
            "title": title.strip(),
            "channel": channel.strip(),
            "category": default_category,
        }

    def _extract_video_items_from_payload(self, payload: Any, default_category: str) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        seen_ids: set[str] = set()

        for node in self._iter_dict_nodes(payload):
            renderer = node.get("videoRenderer")
            if not isinstance(renderer, dict):
                continue

            item = self._to_short_item_from_video_renderer(renderer, default_category)
            if item is None:
                continue
            if item["id"] in seen_ids:
                continue

            seen_ids.add(item["id"])
            items.append(item)

        return items

    def _to_short_item_from_video_renderer(self, renderer: dict[str, Any], default_category: str) -> dict[str, str] | None:
        video_id = renderer.get("videoId")
        if not isinstance(video_id, str) or not video_id.strip():
            return None

        title = self._extract_text_value(renderer.get("title"))
        if not title:
            return None

        channel = (
            self._extract_text_value(renderer.get("ownerText"))
            or self._extract_text_value(renderer.get("longBylineText"))
            or self._extract_text_value(renderer.get("shortBylineText"))
            or "YouTube"
        )

        return {
            "id": video_id.strip(),
            "title": title,
            "channel": channel,
            "category": default_category,
        }

    @staticmethod
    def _iter_dict_nodes(node: Any):
        if isinstance(node, dict):
            yield node
            for value in node.values():
                yield from YouTubeService._iter_dict_nodes(value)
            return
        if isinstance(node, list):
            for value in node:
                yield from YouTubeService._iter_dict_nodes(value)

    @staticmethod
    def _extract_text_value(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if not isinstance(value, dict):
            return ""

        simple_text = value.get("simpleText")
        if isinstance(simple_text, str) and simple_text.strip():
            return simple_text.strip()

        runs = value.get("runs")
        if not isinstance(runs, list):
            return ""

        parts: list[str] = []
        for run in runs:
            if not isinstance(run, dict):
                continue
            text = run.get("text")
            if isinstance(text, str) and text:
                parts.append(text)

        return "".join(parts).strip()

    def _extract_continuation_token(self, payload: Any) -> str | None:
        for node in self._iter_dict_nodes(payload):
            continuation_command = node.get("continuationCommand")
            if isinstance(continuation_command, dict):
                token = continuation_command.get("token")
                if isinstance(token, str) and token.strip():
                    return token.strip()

            for continuation_key in ("nextContinuationData", "reloadContinuationData", "timedContinuationData"):
                continuation_data = node.get(continuation_key)
                if not isinstance(continuation_data, dict):
                    continue
                token = continuation_data.get("continuation") or continuation_data.get("token")
                if isinstance(token, str) and token.strip():
                    return token.strip()

        return None

    def _extract_public_api_context(self, html: str) -> tuple[str | None, str | None]:
        api_key_match = re.search(r'"INNERTUBE_API_KEY":"([^\"]+)"', html)
        client_version_match = re.search(r'"INNERTUBE_CLIENT_VERSION":"([^\"]+)"', html)

        api_key = api_key_match.group(1).strip() if api_key_match else None
        client_version = client_version_match.group(1).strip() if client_version_match else None
        return api_key or None, client_version or None

    def _extract_yt_initial_data(self, html: str) -> dict[str, Any] | None:
        marker_positions = [html.find("var ytInitialData = "), html.find("ytInitialData = ")]
        marker_index = min((index for index in marker_positions if index >= 0), default=-1)
        if marker_index < 0:
            return None

        if html.startswith("var ytInitialData = ", marker_index):
            start_index = marker_index + len("var ytInitialData = ")
        else:
            start_index = marker_index + len("ytInitialData = ")

        while start_index < len(html) and html[start_index] != "{":
            start_index += 1
        if start_index >= len(html):
            return None

        json_blob = self._extract_balanced_json_blob(html, start_index)
        if json_blob is None:
            return None

        try:
            parsed = json.loads(json_blob)
        except json.JSONDecodeError:
            return None

        return parsed if isinstance(parsed, dict) else None

    @staticmethod
    def _extract_balanced_json_blob(text: str, start_index: int) -> str | None:
        depth = 0
        in_string = False
        escaping = False

        for index in range(start_index, len(text)):
            char = text[index]

            if in_string:
                if escaping:
                    escaping = False
                elif char == "\\":
                    escaping = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
                continue

            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start_index : index + 1]

        return None

    def _parse_query_seeds(
        self,
        raw_value: str,
        preferred_categories: set[str] | None = None,
        boost_factor: int = 1,
    ) -> list[QuerySeed]:
        parsed: list[QuerySeed] = []
        for raw_item in raw_value.split(","):
            item = raw_item.strip()
            if not item:
                continue
            if "::" in item:
                category_raw, query_raw = item.split("::", 1)
                category = category_raw.strip() or "General"
                query = query_raw.strip()
            else:
                category = "General"
                query = item
            if not query:
                continue
            parsed.append(QuerySeed(category=category[:40], query=query[:200]))

        base_seeds: list[QuerySeed]
        if parsed:
            base_seeds = parsed
        else:
            fallback: list[QuerySeed] = []
            for seed in _DEFAULT_QUERY_SEEDS:
                category, query = seed.split("::", 1)
                fallback.append(QuerySeed(category=category, query=query))
            base_seeds = fallback

        if not preferred_categories or boost_factor <= 1:
            return base_seeds

        boosted: list[QuerySeed] = []
        for seed in base_seeds:
            boosted.append(seed)
            if seed.category.strip().lower() in preferred_categories:
                for _ in range(boost_factor - 1):
                    boosted.append(seed)
        return boosted

    @staticmethod
    def _parse_preferred_categories(raw_value: str) -> set[str]:
        categories: set[str] = set()
        for raw_item in raw_value.split(","):
            item = raw_item.strip().lower()
            if item:
                categories.add(item)
        return categories

    @staticmethod
    def _normalize_boost_factor(value: Any) -> int:
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            return 3
        return max(1, min(8, normalized))

    def _fallback_items(self, start_index: int, limit: int) -> tuple[list[dict[str, str]], int]:
        fallback_pool = list(_DEFAULT_FALLBACK_SHORTS)
        if not fallback_pool:
            return [], 0

        normalized_start = max(0, int(start_index))
        items: list[dict[str, str]] = []
        for offset in range(limit):
            item = fallback_pool[(normalized_start + offset) % len(fallback_pool)]
            items.append(dict(item))
        next_index = (normalized_start + limit) % len(fallback_pool)
        return items, next_index

    def _decode_cursor(self, cursor: str | None) -> dict[str, Any]:
        default_state = {
            "queryIndex": 0,
            "pageTokens": {},
            "continuations": {},
            "seenIds": [],
            "fallbackIndex": 0,
            "webApiKey": None,
            "webClientVersion": None,
        }

        if not cursor:
            return default_state

        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            decoded = base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8")
            payload = json.loads(decoded)
        except (ValueError, json.JSONDecodeError):
            return default_state

        if not isinstance(payload, dict):
            return default_state
        if payload.get("v") != self._CURSOR_VERSION:
            return default_state

        query_index = payload.get("q")
        page_tokens = payload.get("t")
        continuations = payload.get("c")
        seen_ids = payload.get("s")
        fallback_index = payload.get("f")
        web_api_key = payload.get("wk")
        web_client_version = payload.get("wv")

        return {
            "queryIndex": query_index if isinstance(query_index, int) else 0,
            "pageTokens": page_tokens if isinstance(page_tokens, dict) else {},
            "continuations": continuations if isinstance(continuations, dict) else {},
            "seenIds": seen_ids if isinstance(seen_ids, list) else [],
            "fallbackIndex": fallback_index if isinstance(fallback_index, int) else 0,
            "webApiKey": web_api_key if isinstance(web_api_key, str) and web_api_key.strip() else None,
            "webClientVersion": web_client_version
            if isinstance(web_client_version, str) and web_client_version.strip()
            else None,
        }

    def _encode_cursor(self, state: dict[str, Any]) -> str:
        payload = {
            "v": self._CURSOR_VERSION,
            "q": int(state.get("queryIndex", 0)),
            "t": state.get("pageTokens", {}),
            "c": state.get("continuations", {}),
            "s": state.get("seenIds", []),
            "f": int(state.get("fallbackIndex", 0)),
            "wk": state.get("webApiKey"),
            "wv": state.get("webClientVersion"),
        }
        encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("utf-8")
        return encoded.rstrip("=")
