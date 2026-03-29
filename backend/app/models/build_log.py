from datetime import datetime

from pydantic import BaseModel


class BuildLog(BaseModel):
    id: str
    requestId: str
    step: str
    message: str
    timestamp: datetime

