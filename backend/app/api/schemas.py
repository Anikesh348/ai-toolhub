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


class JobLogArtifactResponse(BaseModel):
    id: str
    requestId: str
    step: str
    fileName: str
    contentType: str
    sizeBytes: int
    createdAt: datetime
    updatedAt: datetime


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


class StopJobResponse(BaseModel):
    jobId: str
    stopped: bool


class CodexAuthStatusResponse(BaseModel):
    loggedIn: bool
    provider: Optional[str] = None
    email: Optional[str] = None
    message: str
    exitCode: int


class CodexUsageWindowResponse(BaseModel):
    usedPercent: Optional[float] = None
    limitWindowSeconds: Optional[int] = None
    windowMinutes: Optional[int] = None
    resetAfterSeconds: Optional[int] = None
    resetAtEpoch: Optional[int] = None
    resetAt: Optional[datetime] = None


class CodexUsageRateLimitResponse(BaseModel):
    allowed: bool
    limitReached: bool
    primaryWindow: Optional[CodexUsageWindowResponse] = None
    secondaryWindow: Optional[CodexUsageWindowResponse] = None


class CodexUsageCreditsResponse(BaseModel):
    hasCredits: bool
    unlimited: bool
    balance: Optional[str] = None
    overageLimitReached: Optional[bool] = None


class CodexUsageAdditionalRateLimitResponse(BaseModel):
    limitName: Optional[str] = None
    meteredFeature: Optional[str] = None
    rateLimit: Optional[CodexUsageRateLimitResponse] = None


class CodexUsageStatusResponse(BaseModel):
    available: bool
    authMode: Optional[str] = None
    endpoint: Optional[str] = None
    planType: Optional[str] = None
    message: str
    fetchedAt: Optional[datetime] = None
    rateLimit: Optional[CodexUsageRateLimitResponse] = None
    codeReviewRateLimit: Optional[CodexUsageRateLimitResponse] = None
    additionalRateLimits: list[CodexUsageAdditionalRateLimitResponse] = Field(default_factory=list)
    credits: Optional[CodexUsageCreditsResponse] = None
    spendControlReached: Optional[bool] = None


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


class InstagramBrowserSessionResponse(BaseModel):
    running: bool
    authenticated: bool
    requiresLogin: bool
    containerName: str
    containerId: Optional[str] = None
    hostPort: Optional[int] = None
    viewerUrl: Optional[str] = None
    image: str
    message: str


InstagramReelsScrollAction = Literal["swipe_up", "swipe_down"]


class InstagramReelsControlResponse(BaseModel):
    ok: bool
    action: InstagramReelsScrollAction
    message: str


class YouTubeShortItemResponse(BaseModel):
    id: str
    title: str
    channel: str
    category: str


class YouTubeShortFeedResponse(BaseModel):
    items: list[YouTubeShortItemResponse]
    nextCursor: Optional[str] = None
    source: Literal["youtube", "fallback"]


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
    attachmentIds: list[str] = Field(default_factory=list, max_length=5)


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


class StopChatStreamResponse(BaseModel):
    sessionId: str
    stopped: bool


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


class ChatExecutionLogResponse(BaseModel):
    id: str
    sessionId: str
    mode: ChatMode
    model: Optional[str] = None
    userMessageId: Optional[str] = None
    assistantMessageId: Optional[str] = None
    userContent: str
    assistantContent: str
    rawLogs: str
    success: bool
    exitCode: int
    quickPath: bool
    promptTokens: Optional[int] = None
    completionTokens: Optional[int] = None
    totalTokens: int = 0
    tokenSource: Literal["parsed", "estimated", "mixed"] = "estimated"
    createdAt: datetime
    updatedAt: datetime


class ChatUsageModeResponse(BaseModel):
    mode: ChatMode
    requestCount: int = 0
    promptTokens: int = 0
    completionTokens: int = 0
    totalTokens: int = 0
    parsedCount: int = 0
    estimatedCount: int = 0
    mixedCount: int = 0


class ChatUsageCostEstimateResponse(BaseModel):
    usd: float = 0.0
    inr: float = 0.0
    usdPerMillionTokens: float = 0.0
    usdToInrRate: float = 0.0
    usdToInrSource: str = "fallback_default"
    usdToInrLive: bool = False
    usdToInrUpdatedAt: Optional[datetime] = None
    note: str = "Rough estimate for visibility only."


class ChatUsageSummaryResponse(BaseModel):
    requestCount: int = 0
    promptTokens: int = 0
    completionTokens: int = 0
    totalTokens: int = 0
    parsedCount: int = 0
    estimatedCount: int = 0
    mixedCount: int = 0
    modes: list[ChatUsageModeResponse] = Field(default_factory=list)
    costEstimate: ChatUsageCostEstimateResponse
