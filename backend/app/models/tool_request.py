from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.models.status import BuildStatus


class ToolRequest(BaseModel):
    id: str
    prompt: str
    refinedPrompt: Optional[str]
    status: BuildStatus
    error: Optional[str]
    createdAt: datetime
    updatedAt: datetime

