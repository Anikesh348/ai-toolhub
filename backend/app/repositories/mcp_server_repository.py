from typing import Any
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.collection import Collection

from app.utils.time import now_ist


class McpServerRepository:
    def __init__(self, collection: Collection[Any]) -> None:
        self._collection = collection

    def create(self, payload: dict[str, Any]) -> dict:
        now = now_ist()
        document = {
            "_id": uuid4().hex,
            **payload,
            "createdAt": now,
            "updatedAt": now,
            "lastTestedAt": None,
            "lastStatus": "untested",
            "lastMessage": "Connection has not been tested yet.",
        }
        self._collection.insert_one(document)
        return self._to_model(document)

    def list_all(self) -> list[dict]:
        docs = self._collection.find({}).sort("createdAt", -1)
        return [self._to_model(doc) for doc in docs]

    def list_enabled(self) -> list[dict]:
        docs = self._collection.find({"enabled": True}).sort("createdAt", 1)
        return [self._to_model(doc) for doc in docs]

    def get_by_id(self, server_id: str) -> dict | None:
        document = self._collection.find_one({"_id": server_id})
        return self._to_model(document) if document else None

    def update(self, server_id: str, payload: dict[str, Any]) -> dict | None:
        document = self._collection.find_one_and_update(
            {"_id": server_id},
            {"$set": {**payload, "updatedAt": now_ist()}},
            return_document=ReturnDocument.AFTER,
        )
        return self._to_model(document) if document else None

    def mark_tested(self, server_id: str, status: str, message: str) -> dict | None:
        document = self._collection.find_one_and_update(
            {"_id": server_id},
            {
                "$set": {
                    "lastTestedAt": now_ist(),
                    "lastStatus": status,
                    "lastMessage": message,
                    "updatedAt": now_ist(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        return self._to_model(document) if document else None

    def delete(self, server_id: str) -> bool:
        result = self._collection.delete_one({"_id": server_id})
        return result.deleted_count == 1

    @staticmethod
    def _to_model(document: dict[str, Any]) -> dict:
        return {
            "id": document["_id"],
            "name": document.get("name", ""),
            "description": document.get("description"),
            "transport": document.get("transport", "stdio"),
            "enabled": bool(document.get("enabled", True)),
            "command": document.get("command"),
            "args": list(document.get("args") or []),
            "url": document.get("url"),
            "dockerProfile": document.get("dockerProfile"),
            "dockerServers": list(document.get("dockerServers") or []),
            "env": dict(document.get("env") or {}),
            "createdAt": document["createdAt"],
            "updatedAt": document["updatedAt"],
            "lastTestedAt": document.get("lastTestedAt"),
            "lastStatus": document.get("lastStatus", "untested"),
            "lastMessage": document.get("lastMessage", "Connection has not been tested yet."),
        }
