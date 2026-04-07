from datetime import datetime
from typing import Any, Optional

from bson import ObjectId
from pymongo.collection import Collection

from app.utils.time import now_ist


class BuildLogRepository:
    def __init__(self, collection: Collection[Any]) -> None:
        self._collection = collection

    def add_log(self, request_id: str, step: str, message: str) -> None:
        self._collection.insert_one(
            {
                "requestId": request_id,
                "step": step,
                "message": message,
                "timestamp": now_ist(),
            }
        )

    def get_logs_for_request(self, request_id: str, limit: int = 500) -> list[dict]:
        docs = (
            self._collection.find({"requestId": request_id})
            .sort("timestamp", 1)
            .limit(limit)
        )
        return [self._to_model(doc) for doc in docs]

    def get_logs_after(self, request_id: str, timestamp: Optional[datetime], limit: int = 200) -> list[dict]:
        query: dict[str, Any] = {"requestId": request_id}
        if timestamp is not None:
            query["timestamp"] = {"$gt": timestamp}
        cursor = self._collection.find(query).sort("timestamp", 1)
        if limit > 0:
            cursor = cursor.limit(limit)
        docs = cursor
        return [self._to_model(doc) for doc in docs]

    def get_latest_logs_for_requests(self, request_ids: list[str]) -> dict[str, dict]:
        if not request_ids:
            return {}
        docs = self._collection.find({"requestId": {"$in": request_ids}}).sort([("requestId", 1), ("timestamp", -1)])
        latest_by_request: dict[str, dict] = {}
        for doc in docs:
            request_id = doc["requestId"]
            if request_id in latest_by_request:
                continue
            latest_by_request[request_id] = self._to_model(doc)
        return latest_by_request

    def delete_for_request(self, request_id: str) -> None:
        self._collection.delete_many({"requestId": request_id})

    @staticmethod
    def _to_model(document: dict) -> dict:
        identifier = document.get("_id")
        return {
            "id": str(identifier) if isinstance(identifier, ObjectId) else "",
            "requestId": document["requestId"],
            "step": document["step"],
            "message": document["message"],
            "timestamp": document["timestamp"],
        }
