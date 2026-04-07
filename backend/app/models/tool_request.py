from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.models.status import BuildStatus


class ToolRequest(BaseModel):
    id: str
    prompt: str
    initialPrompt: Optional[str] = None
    latestPrompt: Optional[str] = None
    promptHistory: list[dict] = Field(default_factory=list)
    refinedPrompt: Optional[str]
    status: BuildStatus
    error: Optional[str]
    createdAt: datetime
    updatedAt: datetime
