from typing import Any

from bson import ObjectId
from pymongo.collection import Collection

from app.utils.time import now_ist


class BuildLogArtifactRepository:
    def __init__(self, collection: Collection[Any]) -> None:
        self._collection = collection

    def add_artifact(
        self,
        request_id: str,
        step: str,
        file_name: str,
        content: str,
        content_type: str = "text/plain; charset=utf-8",
    ) -> None:
        cleaned_content = content or ""
        now = now_ist()
        self._collection.insert_one(
            {
                "requestId": request_id,
                "step": step,
                "fileName": file_name,
                "content": cleaned_content,
                "contentType": content_type,
                "sizeBytes": len(cleaned_content.encode("utf-8")),
                "createdAt": now,
                "updatedAt": now,
            }
        )

    def list_for_request(self, request_id: str, limit: int = 200) -> list[dict]:
        docs = (
            self._collection.find({"requestId": request_id})
            .sort([("createdAt", -1), ("_id", -1)])
            .limit(limit)
        )
        return [self._to_model(document, include_content=False) for document in docs]

    def get_by_id_for_request(self, request_id: str, artifact_id: str) -> dict | None:
        if not ObjectId.is_valid(artifact_id):
            return None
        document = self._collection.find_one({"_id": ObjectId(artifact_id), "requestId": request_id})
        if document is None:
            return None
        return self._to_model(document, include_content=True)

    def delete_for_request(self, request_id: str) -> None:
        self._collection.delete_many({"requestId": request_id})

    @staticmethod
    def _to_model(document: dict, include_content: bool) -> dict:
        identifier = document.get("_id")
        model = {
            "id": str(identifier) if isinstance(identifier, ObjectId) else "",
            "requestId": document["requestId"],
            "step": document["step"],
            "fileName": document["fileName"],
            "contentType": document.get("contentType") or "text/plain; charset=utf-8",
            "sizeBytes": int(document.get("sizeBytes") or 0),
            "createdAt": document["createdAt"],
            "updatedAt": document.get("updatedAt") or document["createdAt"],
        }
        if include_content:
            model["content"] = document.get("content") or ""
        return model
