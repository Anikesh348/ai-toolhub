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
                history.append(self._history_entry("modification", latest_prompt, now))

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

    def delete(self, request_id: str) -> bool:
        result = self._collection.delete_one({"_id": request_id})
        return result.deleted_count > 0

    @staticmethod
    def _to_model(document: dict) -> dict:
        return {
            "id": document["_id"],
            "prompt": document["prompt"],
            "initialPrompt": document.get("initialPrompt"),
            "latestPrompt": document.get("latestPrompt"),
            "promptHistory": document.get("promptHistory", []),
            "refinedPrompt": document.get("refinedPrompt"),
            "status": document["status"],
            "error": document.get("error"),
            "createdAt": document["createdAt"],
            "updatedAt": document["updatedAt"],
        }
