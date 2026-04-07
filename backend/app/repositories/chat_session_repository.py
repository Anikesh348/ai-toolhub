from typing import Any, Optional
from uuid import uuid4

from pymongo.collection import Collection
from pymongo import ReturnDocument

from app.utils.time import now_ist


class ChatSessionRepository:
    def __init__(self, collection: Collection[Any]) -> None:
        self._collection = collection

    def create(self, title: str, mode: str, model: str | None = None) -> dict:
        session_id = uuid4().hex
        now = now_ist()
        document = {
            "_id": session_id,
            "title": title,
            "mode": mode,
            "model": model,
            "archived": False,
            "createdAt": now,
            "updatedAt": now,
        }
        self._collection.insert_one(document)
        return self._to_model(document)

    def get_by_id(self, session_id: str) -> Optional[dict]:
        document = self._collection.find_one({"_id": session_id})
        return self._to_model(document) if document else None

    def list_recent(self, limit: int = 100, include_archived: bool = False) -> list[dict]:
        query: dict[str, Any] = {}
        if not include_archived:
            query["archived"] = False
        docs = self._collection.find(query).sort("updatedAt", -1).limit(limit)
        return [self._to_model(doc) for doc in docs]

    def update(
        self,
        session_id: str,
        title: str | None = None,
        mode: str | None = None,
        model: str | None = None,
        archived: bool | None = None,
    ) -> Optional[dict]:
        update_fields: dict[str, Any] = {"updatedAt": now_ist()}
        if title is not None:
            update_fields["title"] = title
        if mode is not None:
            update_fields["mode"] = mode
        if model is not None:
            update_fields["model"] = model
        if archived is not None:
            update_fields["archived"] = archived

        result = self._collection.find_one_and_update(
            {"_id": session_id},
            {"$set": update_fields},
            return_document=ReturnDocument.AFTER,
        )
        return self._to_model(result) if result else None

    def touch(self, session_id: str) -> None:
        self._collection.update_one(
            {"_id": session_id},
            {"$set": {"updatedAt": now_ist()}},
        )

    def delete(self, session_id: str) -> bool:
        result = self._collection.delete_one({"_id": session_id})
        return result.deleted_count == 1

    @staticmethod
    def _to_model(document: dict) -> dict:
        return {
            "id": document["_id"],
            "title": document["title"],
            "mode": document["mode"],
            "model": document.get("model"),
            "archived": document.get("archived", False),
            "createdAt": document["createdAt"],
            "updatedAt": document["updatedAt"],
        }
