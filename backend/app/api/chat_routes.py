import json

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, StreamingResponse

from app.api.schemas import (
    ChatAttachmentResponse,
    ChatExecutionLogResponse,
    ChatUsageSummaryResponse,
    ChatMessageResponse,
    ChatMode,
    ChatModelsResponse,
    ChatSessionResponse,
    CreateChatMessageRequest,
    CreateChatSessionRequest,
    DeleteChatSessionResponse,
    SendChatMessageResponse,
    StopChatStreamResponse,
    UpdateChatSessionRequest,
)
from app.services.chat_service import ChatService

router = APIRouter(prefix="/chat", tags=["chat"])


def get_chat_service() -> ChatService:
    from app.main import app_state

    service = app_state.chat_service
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Service is initializing")
    return service


@router.post("/sessions", response_model=ChatSessionResponse, status_code=status.HTTP_201_CREATED)
def create_chat_session(
    payload: CreateChatSessionRequest,
    service: ChatService = Depends(get_chat_service),
) -> ChatSessionResponse:
    session = service.create_session(title=payload.title, mode=payload.mode, model=payload.model)
    return ChatSessionResponse(**session)


@router.get("/sessions", response_model=list[ChatSessionResponse])
def list_chat_sessions(
    limit: int = Query(default=100, ge=1, le=500),
    service: ChatService = Depends(get_chat_service),
) -> list[ChatSessionResponse]:
    sessions = service.list_sessions(limit=limit)
    return [ChatSessionResponse(**session) for session in sessions]


@router.patch("/sessions/{session_id}", response_model=ChatSessionResponse)
def update_chat_session(
    session_id: str,
    payload: UpdateChatSessionRequest,
    service: ChatService = Depends(get_chat_service),
) -> ChatSessionResponse:
    update_model = "model" in payload.model_fields_set
    session = service.update_session(
        session_id=session_id,
        title=payload.title,
        mode=payload.mode,
        model=payload.model,
        update_model=update_model,
        archived=payload.archived,
    )
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return ChatSessionResponse(**session)


@router.delete("/sessions/{session_id}", response_model=DeleteChatSessionResponse)
def delete_chat_session(
    session_id: str,
    service: ChatService = Depends(get_chat_service),
) -> DeleteChatSessionResponse:
    deleted = service.delete_session(session_id=session_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return DeleteChatSessionResponse(sessionId=session_id, deleted=True)


@router.get("/sessions/{session_id}/messages", response_model=list[ChatMessageResponse])
def list_chat_messages(
    session_id: str,
    limit: int = Query(default=500, ge=1, le=1000),
    service: ChatService = Depends(get_chat_service),
) -> list[ChatMessageResponse]:
    session = service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    messages = service.list_messages(session_id=session_id, limit=limit)
    return [ChatMessageResponse(**message) for message in messages]


@router.get("/models", response_model=ChatModelsResponse)
def list_chat_models(
    service: ChatService = Depends(get_chat_service),
) -> ChatModelsResponse:
    models = service.list_models()
    return ChatModelsResponse(**models)


@router.get("/logs", response_model=list[ChatExecutionLogResponse])
def list_chat_execution_logs(
    limit: int = Query(default=200, ge=1, le=1000),
    session_id: str | None = Query(default=None, alias="sessionId"),
    mode: list[ChatMode] | None = Query(default=None),
    service: ChatService = Depends(get_chat_service),
) -> list[ChatExecutionLogResponse]:
    logs = service.list_execution_logs(limit=limit, session_id=session_id, modes=list(mode or []))
    return [ChatExecutionLogResponse(**item) for item in logs]


@router.get("/usage", response_model=ChatUsageSummaryResponse)
def summarize_chat_usage(
    session_id: str | None = Query(default=None, alias="sessionId"),
    mode: list[ChatMode] | None = Query(default=None),
    service: ChatService = Depends(get_chat_service),
) -> ChatUsageSummaryResponse:
    summary = service.summarize_usage(session_id=session_id, modes=list(mode or []))
    return ChatUsageSummaryResponse(**summary)


@router.post("/sessions/{session_id}/attachments", response_model=ChatAttachmentResponse, status_code=status.HTTP_201_CREATED)
async def upload_chat_attachment(
    session_id: str,
    file: UploadFile = File(...),
    service: ChatService = Depends(get_chat_service),
) -> ChatAttachmentResponse:
    raw_data = await file.read()
    attachment, error = service.create_image_attachment(
        session_id=session_id,
        file_name=file.filename or "image",
        content_type=file.content_type,
        data=raw_data,
    )
    if error:
        error_status = status.HTTP_404_NOT_FOUND if error == "Session not found" else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=error_status, detail=error)
    if not attachment:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to save attachment")
    return ChatAttachmentResponse(**attachment)


@router.get("/sessions/{session_id}/attachments/{attachment_id}")
def get_chat_attachment(
    session_id: str,
    attachment_id: str,
    service: ChatService = Depends(get_chat_service),
) -> FileResponse:
    attachment = service.get_image_attachment(session_id=session_id, attachment_id=attachment_id)
    if not attachment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    return FileResponse(
        path=str(attachment["hostPath"]),
        media_type=str(attachment["contentType"]),
        filename=str(attachment["fileName"]),
    )


@router.post("/sessions/{session_id}/messages", response_model=SendChatMessageResponse)
def create_chat_message(
    session_id: str,
    payload: CreateChatMessageRequest,
    service: ChatService = Depends(get_chat_service),
) -> SendChatMessageResponse:
    user_message, assistant_message, error = service.send_message(
        session_id=session_id,
        content=payload.content,
        model=payload.model,
        attachment_ids=payload.attachmentIds,
    )
    if error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error)

    session = service.get_session(session_id)
    if session is None or user_message is None or assistant_message is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Unable to store chat message")

    return SendChatMessageResponse(
        session=ChatSessionResponse(**session),
        userMessage=ChatMessageResponse(**user_message),
        assistantMessage=ChatMessageResponse(**assistant_message),
    )


@router.post("/sessions/{session_id}/messages/stream")
def stream_chat_message(
    session_id: str,
    payload: CreateChatMessageRequest,
    service: ChatService = Depends(get_chat_service),
) -> StreamingResponse:
    def event_generator():
        for event in service.stream_message(
            session_id=session_id,
            content=payload.content,
            model=payload.model,
            attachment_ids=payload.attachmentIds,
        ):
            payload_json = json.dumps(jsonable_encoder(event))
            yield f"data: {payload_json}\n\n"

    headers = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)


@router.post("/sessions/{session_id}/messages/stop", response_model=StopChatStreamResponse)
def stop_chat_message_stream(
    session_id: str,
    service: ChatService = Depends(get_chat_service),
) -> StopChatStreamResponse:
    stopped, error = service.stop_active_stream(session_id=session_id)
    if error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error)
    return StopChatStreamResponse(sessionId=session_id, stopped=stopped)
