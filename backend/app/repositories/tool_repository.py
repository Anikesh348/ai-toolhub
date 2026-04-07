from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from pymongo.collection import Collection

from app.models.status import ToolStatus
from app.utils.time import now_ist


class ToolRepository:
    def __init__(self, collection: Collection[Any]) -> None:
        self._collection = collection

    def create(self, request_id: str, name: str, docker_image: str, runtime_name: str) -> dict:
        tool_id = uuid4().hex
        now = now_ist()
        document = {
            "_id": tool_id,
            "toolId": tool_id,
            "requestId": request_id,
            "name": name,
            "runtimeName": runtime_name,
            "dockerImage": docker_image,
            "containerId": None,
            "port": None,
            "uiPort": None,
            "ports": {},
            "status": ToolStatus.DEPLOYING.value,
            "crashAlertSent": False,
            "monitorIgnoreUntil": None,
            "createdAt": now,
            "updatedAt": now,
        }
        self._collection.insert_one(document)
        return self._to_model(document)

    def list_all(self) -> list[dict]:
        return [self._to_model(doc) for doc in self._collection.find({}).sort("createdAt", -1)]

    def get_by_id(self, tool_id: str) -> Optional[dict]:
        doc = self._collection.find_one({"toolId": tool_id})
        return self._to_model(doc) if doc else None

    def get_by_request_id(self, request_id: str) -> Optional[dict]:
        doc = self._collection.find_one({"requestId": request_id})
        return self._to_model(doc) if doc else None

    def get_by_request_ids(self, request_ids: list[str]) -> dict[str, dict]:
        if not request_ids:
            return {}
        docs = self._collection.find({"requestId": {"$in": request_ids}})
        mapping: dict[str, dict] = {}
        for doc in docs:
            model = self._to_model(doc)
            mapping[model["requestId"]] = model
        return mapping

    def delete(self, tool_id: str) -> bool:
        result = self._collection.delete_one({"toolId": tool_id})
        return result.deleted_count > 0

    def get_running_without_crash_alert(self) -> list[dict]:
        now = now_ist()
        docs = self._collection.find(
            {
                "status": ToolStatus.RUNNING.value,
                "crashAlertSent": False,
                "$or": [
                    {"monitorIgnoreUntil": None},
                    {"monitorIgnoreUntil": {"$lte": now}},
                    {"monitorIgnoreUntil": {"$exists": False}},
                ],
            }
        )
        return [self._to_model(doc) for doc in docs]

    def get_ports_in_use(self, exclude_tool_id: str | None = None) -> set[int]:
        query: dict[str, Any] = {"status": {"$in": [ToolStatus.RUNNING.value, ToolStatus.DEPLOYING.value]}}
        if exclude_tool_id:
            query["toolId"] = {"$ne": exclude_tool_id}

        docs = self._collection.find(query, {"port": 1, "ports": 1})
        ports: set[int] = set()
        for doc in docs:
            port = doc.get("port")
            if port is not None:
                ports.add(int(port))

            per_service_ports = doc.get("ports")
            if isinstance(per_service_ports, dict):
                for value in per_service_ports.values():
                    if value is None:
                        continue
                    ports.add(int(value))
        return ports

    def update_deployment(
        self,
        tool_id: str,
        container_id: str,
        port: int,
        status: ToolStatus,
        ports: dict[str, int] | None = None,
        ui_port: int | None = None,
        monitor_ignore_until: datetime | None = None,
    ) -> None:
        assigned_ui_port = ui_port if ui_port is not None else port
        updates: dict[str, Any] = {
            "containerId": container_id,
            "port": assigned_ui_port,
            "uiPort": assigned_ui_port,
            "status": status.value,
            "updatedAt": now_ist(),
        }
        if ports is not None:
            updates["ports"] = ports
        if monitor_ignore_until is not None:
            updates["monitorIgnoreUntil"] = monitor_ignore_until

        self._collection.update_one(
            {"toolId": tool_id},
            {
                "$set": updates,
            },
        )

    def prepare_rebuild(self, tool_id: str, request_id: str, docker_image: str) -> None:
        self._collection.update_one(
            {"toolId": tool_id},
            {
                "$set": {
                    "requestId": request_id,
                    "dockerImage": docker_image,
                    "status": ToolStatus.DEPLOYING.value,
                    "crashAlertSent": False,
                    "monitorIgnoreUntil": None,
                    "updatedAt": now_ist(),
                }
            },
        )

    def assign_port(self, tool_id: str, port: int) -> None:
        self.assign_ports(tool_id=tool_id, ports={"app": port}, ui_port=port)

    def assign_ports(self, tool_id: str, ports: dict[str, int], ui_port: int | None = None) -> None:
        effective_ui_port = ui_port
        if effective_ui_port is None and ports:
            effective_ui_port = next(iter(ports.values()))

        self._collection.update_one(
            {"toolId": tool_id},
            {
                "$set": {
                    "ports": ports,
                    "port": effective_ui_port,
                    "uiPort": effective_ui_port,
                    "updatedAt": now_ist(),
                }
            },
        )

    def update_status(
        self,
        tool_id: str,
        status: ToolStatus,
        crash_alert_sent: Optional[bool] = None,
        clear_runtime: bool = False,
    ) -> None:
        updates: dict[str, Any] = {
            "status": status.value,
            "updatedAt": now_ist(),
        }
        if crash_alert_sent is not None:
            updates["crashAlertSent"] = crash_alert_sent
        if clear_runtime:
            updates["containerId"] = None
        self._collection.update_one({"toolId": tool_id}, {"$set": updates})

    @staticmethod
    def _to_model(document: dict) -> dict:
        return {
            "toolId": document["toolId"],
            "requestId": document["requestId"],
            "name": document["name"],
            "runtimeName": document.get("runtimeName", document["name"]),
            "dockerImage": document["dockerImage"],
            "containerId": document.get("containerId"),
            "port": document.get("port"),
            "uiPort": document.get("uiPort", document.get("port")),
            "ports": document.get("ports") or {},
            "status": document["status"],
            "crashAlertSent": document.get("crashAlertSent", False),
            "monitorIgnoreUntil": document.get("monitorIgnoreUntil"),
            "createdAt": document["createdAt"],
            "updatedAt": document["updatedAt"],
        }
