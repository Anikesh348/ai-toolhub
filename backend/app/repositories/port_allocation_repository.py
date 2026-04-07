from typing import Any

from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError

from app.utils.time import now_ist


class PortAllocationRepository:
    def __init__(self, collection: Collection[Any]) -> None:
        self._collection = collection

    def get_reserved_ports(self) -> set[int]:
        docs = self._collection.find({}, {"port": 1})
        return {int(doc["port"]) for doc in docs}

    def reserve_port(self, port: int, tool_id: str | None, request_id: str | None) -> bool:
        try:
            self._collection.insert_one(
                {
                    "port": port,
                    "toolId": tool_id,
                    "requestId": request_id,
                    "assignedAt": now_ist(),
                }
            )
            return True
        except DuplicateKeyError:
            return False
