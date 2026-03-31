from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pymongo.collection import Collection
from pymongo import ReturnDocument

from app.models.status import BuildStatus


class RequestRepository:
    def __init__(self, collection: Collection[Any]) -> None:
        self._collection = collection

    def create(self, prompt: str) -> dict:
        request_id = uuid4().hex
        now = datetime.now(tz=timezone.utc)
        document = {
            "_id": request_id,
            "prompt": prompt,
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
                    "updatedAt": datetime.now(tz=timezone.utc),
                }
            },
        )

    def prepare_for_rebuild(self, request_id: str, prompt: str) -> Optional[dict]:
        now = datetime.now(tz=timezone.utc)
        updated = self._collection.find_one_and_update(
            {"_id": request_id},
            {
                "$set": {
                    "prompt": prompt,
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
            "updatedAt": datetime.now(tz=timezone.utc),
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
            "refinedPrompt": document.get("refinedPrompt"),
            "status": document["status"],
            "error": document.get("error"),
            "createdAt": document["createdAt"],
            "updatedAt": document["updatedAt"],
        }
