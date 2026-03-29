from datetime import datetime, timezone
from typing import Any

from bson import ObjectId
from pymongo.collection import Collection


class ChatMessageRepository:
    def __init__(self, collection: Collection[Any]) -> None:
        self._collection = collection

    def create(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        now = datetime.now(tz=timezone.utc)
        document: dict[str, Any] = {
            "sessionId": session_id,
            "role": role,
            "content": content,
            "metadata": metadata or {},
            "createdAt": now,
        }
        result = self._collection.insert_one(document)
        document["_id"] = result.inserted_id
        return self._to_model(document)

    def list_for_session(self, session_id: str, limit: int = 500) -> list[dict]:
        docs = self._collection.find({"sessionId": session_id}).sort("createdAt", 1).limit(limit)
        return [self._to_model(doc) for doc in docs]

    def list_recent_for_session(self, session_id: str, limit: int = 20) -> list[dict]:
        docs = self._collection.find({"sessionId": session_id}).sort("createdAt", -1).limit(limit)
        ordered = [self._to_model(doc) for doc in docs]
        ordered.reverse()
        return ordered

    def delete_for_session(self, session_id: str) -> int:
        result = self._collection.delete_many({"sessionId": session_id})
        return int(result.deleted_count)

    @staticmethod
    def _to_model(document: dict) -> dict:
        identifier = document.get("_id")
        return {
            "id": str(identifier) if isinstance(identifier, ObjectId) else "",
            "sessionId": document["sessionId"],
            "role": document["role"],
            "content": document["content"],
            "metadata": document.get("metadata", {}),
            "createdAt": document["createdAt"],
        }
