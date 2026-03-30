from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.models.status import BuildStatus, ToolStatus


class GenerateToolRequest(BaseModel):
    prompt: str = Field(min_length=10, max_length=8000)
    name: Optional[str] = Field(default=None, max_length=120)


class GenerateToolResponse(BaseModel):
    jobId: str
    status: BuildStatus


class JobResponse(BaseModel):
    id: str
    prompt: str
    refinedPrompt: Optional[str]
    status: BuildStatus
    error: Optional[str]
    createdAt: datetime
    updatedAt: datetime
    logs: list[dict]


class JobSummaryResponse(BaseModel):
    id: str
    prompt: str
    status: BuildStatus
    error: Optional[str]
    createdAt: datetime
    updatedAt: datetime
    toolId: Optional[str]
    toolStatus: Optional[ToolStatus]
    toolName: Optional[str]
    port: Optional[int]
    uiPort: Optional[int]
    ports: Optional[dict[str, int]]
    containerId: Optional[str]
    lastStep: Optional[str]
    lastMessage: Optional[str]
    lastLogAt: Optional[datetime]


class JobEventResponse(BaseModel):
    jobId: str
    status: BuildStatus
    error: Optional[str]
    updatedAt: datetime
    logs: list[dict]


class ToolResponse(BaseModel):
    toolId: str
    requestId: str
    name: str
    runtimeName: str
    dockerImage: str
    containerId: Optional[str]
    port: Optional[int]
    uiPort: Optional[int]
    ports: Optional[dict[str, int]]
    status: ToolStatus
    createdAt: datetime
    updatedAt: datetime


class DeleteToolResponse(BaseModel):
    toolId: str
    deleted: bool


class DeleteJobResponse(BaseModel):
    jobId: str
    deleted: bool


class CodexAuthStatusResponse(BaseModel):
    loggedIn: bool
    provider: Optional[str] = None
    message: str
    exitCode: int


class GitSshPublicKeyResponse(BaseModel):
    publicKey: str
    fingerprint: Optional[str] = None
    keyPath: str
    generated: bool
    message: str


class VerifyGitSshRequest(BaseModel):
    host: str = Field(default="github.com", min_length=1, max_length=255)
    username: str = Field(default="git", min_length=1, max_length=64)


class VerifyGitSshResponse(BaseModel):
    connected: bool
    host: str
    username: str
    exitCode: int
    message: str
    logs: str


ChatMode = Literal["general", "tool_builder", "operator", "pi_operator"]
ChatRole = Literal["user", "assistant", "system", "tool"]


class CreateChatSessionRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=140)
    mode: ChatMode = "general"
    model: Optional[str] = Field(default=None, max_length=120)


class UpdateChatSessionRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=140)
    mode: Optional[ChatMode] = None
    model: Optional[str] = Field(default=None, max_length=120)
    archived: Optional[bool] = None


class ChatSessionResponse(BaseModel):
    id: str
    title: str
    mode: ChatMode
    model: Optional[str] = None
    archived: bool
    createdAt: datetime
    updatedAt: datetime


class CreateChatMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=16000)
    model: Optional[str] = Field(default=None, max_length=120)
    attachmentIds: list[str] = Field(default_factory=list, max_length=4)


class ChatMessageResponse(BaseModel):
    id: str
    sessionId: str
    role: ChatRole
    content: str
    metadata: dict = Field(default_factory=dict)
    createdAt: datetime


class SendChatMessageResponse(BaseModel):
    session: ChatSessionResponse
    userMessage: ChatMessageResponse
    assistantMessage: ChatMessageResponse


class DeleteChatSessionResponse(BaseModel):
    sessionId: str
    deleted: bool


class ChatModelsResponse(BaseModel):
    models: list[str]
    defaultModel: Optional[str] = None


class ChatAttachmentResponse(BaseModel):
    id: str
    fileName: str
    contentType: str
    size: int
    containerPath: str
    url: str
