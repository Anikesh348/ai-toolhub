from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.status import ToolStatus


class Tool(BaseModel):
    toolId: str
    requestId: str
    name: str
    dockerImage: str
    containerId: Optional[str]
    port: Optional[int]
    uiPort: Optional[int]
    ports: dict[str, int] = Field(default_factory=dict)
    status: ToolStatus
    crashAlertSent: bool = False
    monitorIgnoreUntil: Optional[datetime] = None
    createdAt: datetime
    updatedAt: datetime
