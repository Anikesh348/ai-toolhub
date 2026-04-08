from typing import Any

from bson import ObjectId
from pymongo.collection import Collection

from app.utils.time import now_ist


class ChatExecutionLogRepository:
    def __init__(self, collection: Collection[Any]) -> None:
        self._collection = collection

    def create(
        self,
        session_id: str,
        mode: str,
        user_content: str,
        assistant_content: str,
        success: bool,
        exit_code: int,
        model: str | None = None,
        raw_logs: str | None = None,
        quick_path: bool = False,
        user_message_id: str | None = None,
        assistant_message_id: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        token_source: str | None = None,
    ) -> dict:
        now = now_ist()
        resolved_prompt_tokens = self._normalize_optional_int(prompt_tokens)
        resolved_completion_tokens = self._normalize_optional_int(completion_tokens)
        resolved_total_tokens = self._normalize_optional_int(total_tokens)
        if resolved_total_tokens is None and resolved_prompt_tokens is not None and resolved_completion_tokens is not None:
            resolved_total_tokens = resolved_prompt_tokens + resolved_completion_tokens
        if resolved_total_tokens is None:
            resolved_total_tokens = 0
        resolved_source = self._normalize_token_source(token_source)

        document: dict[str, Any] = {
            "sessionId": session_id,
            "mode": mode,
            "model": model,
            "userMessageId": user_message_id,
            "assistantMessageId": assistant_message_id,
            "userContent": user_content,
            "assistantContent": assistant_content,
            "rawLogs": raw_logs or "",
            "success": bool(success),
            "exitCode": int(exit_code),
            "quickPath": bool(quick_path),
            "promptTokens": resolved_prompt_tokens,
            "completionTokens": resolved_completion_tokens,
            "totalTokens": resolved_total_tokens,
            "tokenSource": resolved_source,
            "createdAt": now,
            "updatedAt": now,
        }
        result = self._collection.insert_one(document)
        document["_id"] = result.inserted_id
        return self._to_model(document)

    def list_recent(
        self,
        limit: int = 200,
        session_id: str | None = None,
        modes: list[str] | None = None,
    ) -> list[dict]:
        query = self._build_query(session_id=session_id, modes=modes)

        docs = self._collection.find(query).sort([("createdAt", -1), ("_id", -1)]).limit(max(int(limit), 1))
        return [self._to_model(document) for document in docs]

    def summarize_usage(
        self,
        session_id: str | None = None,
        modes: list[str] | None = None,
    ) -> dict[str, Any]:
        query = self._build_query(session_id=session_id, modes=modes)
        pipeline = [
            {"$match": query},
            {
                "$group": {
                    "_id": "$mode",
                    "requests": {"$sum": 1},
                    "promptTokens": {"$sum": {"$ifNull": ["$promptTokens", 0]}},
                    "completionTokens": {"$sum": {"$ifNull": ["$completionTokens", 0]}},
                    "totalTokens": {"$sum": {"$ifNull": ["$totalTokens", 0]}},
                    "parsedCount": {
                        "$sum": {"$cond": [{"$eq": ["$tokenSource", "parsed"]}, 1, 0]},
                    },
                    "estimatedCount": {
                        "$sum": {"$cond": [{"$eq": ["$tokenSource", "estimated"]}, 1, 0]},
                    },
                    "mixedCount": {
                        "$sum": {"$cond": [{"$eq": ["$tokenSource", "mixed"]}, 1, 0]},
                    },
                }
            },
            {"$sort": {"_id": 1}},
        ]
        rows = list(self._collection.aggregate(pipeline))

        modes_summary: list[dict[str, Any]] = []
        total_summary = {
            "requests": 0,
            "promptTokens": 0,
            "completionTokens": 0,
            "totalTokens": 0,
            "parsedCount": 0,
            "estimatedCount": 0,
            "mixedCount": 0,
        }
        for row in rows:
            mode = str(row.get("_id") or "general")
            requests = int(row.get("requests") or 0)
            prompt_tokens = int(row.get("promptTokens") or 0)
            completion_tokens = int(row.get("completionTokens") or 0)
            total_tokens = int(row.get("totalTokens") or 0)
            parsed_count = int(row.get("parsedCount") or 0)
            estimated_count = int(row.get("estimatedCount") or 0)
            mixed_count = int(row.get("mixedCount") or 0)
            modes_summary.append(
                {
                    "mode": mode,
                    "requests": requests,
                    "promptTokens": prompt_tokens,
                    "completionTokens": completion_tokens,
                    "totalTokens": total_tokens,
                    "parsedCount": parsed_count,
                    "estimatedCount": estimated_count,
                    "mixedCount": mixed_count,
                }
            )
            total_summary["requests"] += requests
            total_summary["promptTokens"] += prompt_tokens
            total_summary["completionTokens"] += completion_tokens
            total_summary["totalTokens"] += total_tokens
            total_summary["parsedCount"] += parsed_count
            total_summary["estimatedCount"] += estimated_count
            total_summary["mixedCount"] += mixed_count

        return {"totals": total_summary, "modes": modes_summary}

    @staticmethod
    def _build_query(session_id: str | None, modes: list[str] | None) -> dict[str, Any]:
        query: dict[str, Any] = {}
        if session_id:
            query["sessionId"] = session_id
        cleaned_modes = [mode.strip() for mode in (modes or []) if mode and mode.strip()]
        if cleaned_modes:
            query["mode"] = {"$in": cleaned_modes}
        return query

    @staticmethod
    def _normalize_optional_int(value: int | None) -> int | None:
        if value is None:
            return None
        try:
            resolved = int(value)
        except (TypeError, ValueError):
            return None
        return max(resolved, 0)

    @staticmethod
    def _normalize_token_source(value: str | None) -> str:
        normalized = (value or "").strip().lower()
        if normalized in {"parsed", "estimated", "mixed"}:
            return normalized
        return "estimated"

    @staticmethod
    def _to_model(document: dict) -> dict:
        identifier = document.get("_id")
        prompt_tokens = ChatExecutionLogRepository._normalize_optional_int(document.get("promptTokens"))
        completion_tokens = ChatExecutionLogRepository._normalize_optional_int(document.get("completionTokens"))
        total_tokens_raw = ChatExecutionLogRepository._normalize_optional_int(document.get("totalTokens"))
        total_tokens = (
            total_tokens_raw
            if total_tokens_raw is not None
            else (prompt_tokens or 0) + (completion_tokens or 0)
        )
        return {
            "id": str(identifier) if isinstance(identifier, ObjectId) else "",
            "sessionId": document["sessionId"],
            "mode": document.get("mode") or "general",
            "model": document.get("model"),
            "userMessageId": document.get("userMessageId"),
            "assistantMessageId": document.get("assistantMessageId"),
            "userContent": document.get("userContent") or "",
            "assistantContent": document.get("assistantContent") or "",
            "rawLogs": document.get("rawLogs") or "",
            "success": bool(document.get("success")),
            "exitCode": int(document.get("exitCode") or 0),
            "quickPath": bool(document.get("quickPath")),
            "promptTokens": prompt_tokens,
            "completionTokens": completion_tokens,
            "totalTokens": total_tokens,
            "tokenSource": ChatExecutionLogRepository._normalize_token_source(document.get("tokenSource")),
            "createdAt": document["createdAt"],
            "updatedAt": document.get("updatedAt") or document["createdAt"],
        }
