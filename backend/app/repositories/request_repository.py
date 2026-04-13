from typing import Any, Optional
from uuid import uuid4

from pymongo.collection import Collection
from pymongo import ReturnDocument

from app.models.status import BuildStatus
from app.utils.time import now_ist


class RequestRepository:
    def __init__(self, collection: Collection[Any]) -> None:
        self._collection = collection

    @staticmethod
    def _history_entry(kind: str, prompt: str, timestamp: Any) -> dict[str, Any]:
        return {
            "kind": kind,
            "prompt": prompt,
            "timestamp": timestamp,
        }

    def create(self, prompt: str) -> dict:
        request_id = uuid4().hex
        now = now_ist()
        document = {
            "_id": request_id,
            "prompt": prompt,
            "initialPrompt": prompt,
            "latestPrompt": prompt,
            "promptHistory": [self._history_entry("initial", prompt, now)],
            "refinedPrompt": None,
            "status": BuildStatus.PENDING.value,
            "error": None,
            "tokenUsage": self._default_token_usage(),
            "createdAt": now,
            "updatedAt": now,
        }
        self._collection.insert_one(document)
        return self._to_model(document)

    def get_by_id(self, request_id: str) -> Optional[dict]:
        document = self._collection.find_one({"_id": request_id})
        return self._to_model(document) if document else None

    def list_recent(self, limit: int = 200) -> list[dict]:
        docs = self._collection.find({}).sort("createdAt", -1).limit(limit)
        return [self._to_model(doc) for doc in docs]

    def set_refined_prompt(self, request_id: str, refined_prompt: str) -> None:
        self._collection.update_one(
            {"_id": request_id},
            {
                "$set": {
                    "refinedPrompt": refined_prompt,
                    "updatedAt": now_ist(),
                }
            },
        )

    def prepare_for_rebuild(self, request_id: str, prompt: str) -> Optional[dict]:
        now = now_ist()
        existing = self._collection.find_one({"_id": request_id})
        if existing is None:
            return None

        initial_prompt = str(existing.get("initialPrompt") or existing.get("prompt") or "").strip()
        latest_prompt = str(prompt or "").strip()
        raw_history = existing.get("promptHistory")
        history: list[dict[str, Any]] = []
        if isinstance(raw_history, list):
            for item in raw_history:
                if not isinstance(item, dict):
                    continue
                entry_prompt = str(item.get("prompt") or "").strip()
                if not entry_prompt:
                    continue
                history.append(
                    {
                        "kind": str(item.get("kind") or "unknown"),
                        "prompt": entry_prompt,
                        "timestamp": item.get("timestamp") or now,
                    }
                )
        if not history and initial_prompt:
            created_at = existing.get("createdAt") or now
            history.append(self._history_entry("initial", initial_prompt, created_at))

        if latest_prompt:
            previous_prompt = str(existing.get("latestPrompt") or existing.get("prompt") or "").strip()
            if previous_prompt != latest_prompt:
                last_recorded_prompt = str(history[-1].get("prompt") or "").strip() if history else ""
                if last_recorded_prompt != latest_prompt:
                    history.append(self._history_entry("modification", latest_prompt, now))

        # Keep history bounded so repeated modify-chat sessions stay concise.
        if len(history) > 12:
            initial_entry = history[0]
            history = [initial_entry, *history[-11:]]

        updated = self._collection.find_one_and_update(
            {"_id": request_id},
            {
                "$set": {
                    "prompt": latest_prompt,
                    "initialPrompt": initial_prompt or latest_prompt,
                    "latestPrompt": latest_prompt,
                    "promptHistory": history,
                    "refinedPrompt": None,
                    "status": BuildStatus.PENDING.value,
                    "error": None,
                    "updatedAt": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return self._to_model(updated) if updated else None

    def update_status(self, request_id: str, status: BuildStatus, error: Optional[str] = None) -> None:
        update_fields: dict[str, Any] = {
            "status": status.value,
            "updatedAt": now_ist(),
            "error": error,
        }
        self._collection.update_one({"_id": request_id}, {"$set": update_fields})

    def increment_token_usage(
        self,
        request_id: str,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        total_tokens: int | None,
        token_source: str | None,
    ) -> None:
        resolved_prompt = self._normalize_optional_int(prompt_tokens)
        resolved_completion = self._normalize_optional_int(completion_tokens)
        resolved_total = self._normalize_optional_int(total_tokens)
        if resolved_total is None and resolved_prompt is not None and resolved_completion is not None:
            resolved_total = resolved_prompt + resolved_completion
        if resolved_total is None:
            resolved_total = 0

        resolved_source = self._normalize_token_source(token_source)
        inc_fields: dict[str, int] = {
            "tokenUsage.promptTokens": resolved_prompt or 0,
            "tokenUsage.completionTokens": resolved_completion or 0,
            "tokenUsage.totalTokens": resolved_total,
            "tokenUsage.trackedRuns": 1,
        }
        if resolved_source == "parsed":
            inc_fields["tokenUsage.parsedCount"] = 1
        elif resolved_source == "mixed":
            inc_fields["tokenUsage.mixedCount"] = 1
        else:
            inc_fields["tokenUsage.estimatedCount"] = 1

        self._collection.update_one(
            {"_id": request_id},
            {
                "$inc": inc_fields,
                "$set": {"updatedAt": now_ist()},
            },
        )

    def summarize_token_usage(self) -> dict[str, int]:
        rows = list(
            self._collection.aggregate(
                [
                    {
                        "$group": {
                            "_id": None,
                            "requestCount": {"$sum": {"$ifNull": ["$tokenUsage.trackedRuns", 0]}},
                            "promptTokens": {"$sum": {"$ifNull": ["$tokenUsage.promptTokens", 0]}},
                            "completionTokens": {"$sum": {"$ifNull": ["$tokenUsage.completionTokens", 0]}},
                            "totalTokens": {"$sum": {"$ifNull": ["$tokenUsage.totalTokens", 0]}},
                            "parsedCount": {"$sum": {"$ifNull": ["$tokenUsage.parsedCount", 0]}},
                            "estimatedCount": {"$sum": {"$ifNull": ["$tokenUsage.estimatedCount", 0]}},
                            "mixedCount": {"$sum": {"$ifNull": ["$tokenUsage.mixedCount", 0]}},
                        }
                    }
                ]
            )
        )
        row = rows[0] if rows else {}
        return {
            "requestCount": int(row.get("requestCount") or 0),
            "promptTokens": int(row.get("promptTokens") or 0),
            "completionTokens": int(row.get("completionTokens") or 0),
            "totalTokens": int(row.get("totalTokens") or 0),
            "parsedCount": int(row.get("parsedCount") or 0),
            "estimatedCount": int(row.get("estimatedCount") or 0),
            "mixedCount": int(row.get("mixedCount") or 0),
        }

    def delete(self, request_id: str) -> bool:
        result = self._collection.delete_one({"_id": request_id})
        return result.deleted_count > 0

    @staticmethod
    def _to_model(document: dict) -> dict:
        raw_usage = document.get("tokenUsage")
        usage = RequestRepository._normalize_token_usage(raw_usage if isinstance(raw_usage, dict) else None)
        return {
            "id": document["_id"],
            "prompt": document["prompt"],
            "initialPrompt": document.get("initialPrompt"),
            "latestPrompt": document.get("latestPrompt"),
            "promptHistory": document.get("promptHistory", []),
            "refinedPrompt": document.get("refinedPrompt"),
            "status": document["status"],
            "error": document.get("error"),
            "tokenUsage": usage,
            "createdAt": document["createdAt"],
            "updatedAt": document["updatedAt"],
        }

    @staticmethod
    def _default_token_usage() -> dict[str, int]:
        return {
            "promptTokens": 0,
            "completionTokens": 0,
            "totalTokens": 0,
            "parsedCount": 0,
            "estimatedCount": 0,
            "mixedCount": 0,
            "trackedRuns": 0,
        }

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
    def _normalize_token_usage(value: dict[str, Any] | None) -> dict[str, int]:
        default = RequestRepository._default_token_usage()
        if not value:
            return default
        normalized = dict(default)
        for field in normalized:
            resolved = RequestRepository._normalize_optional_int(value.get(field))
            normalized[field] = resolved if resolved is not None else 0
        return normalized
