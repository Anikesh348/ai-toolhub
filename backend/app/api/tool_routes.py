import asyncio
import json
from datetime import datetime
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response, StreamingResponse

from app.api.schemas import (
    CodexAuthStatusResponse,
    CodexUsageStatusResponse,
    DeleteJobResponse,
    DeleteToolResponse,
    GenerateToolRequest,
    GenerateToolResponse,
    GitSshPublicKeyResponse,
    InstagramReelsControlResponse,
    InstagramBrowserSessionResponse,
    JobEventResponse,
    JobLogArtifactResponse,
    JobResponse,
    JobSummaryResponse,
    StopJobResponse,
    ToolResponse,
    YouTubeShortFeedResponse,
    YouTubeShortSettingsResponse,
    UpdateYouTubeShortSettingsRequest,
    VerifyGitSshRequest,
    VerifyGitSshResponse,
)
from app.models.status import BuildStatus
from app.services.codex_service import CodexService
from app.services.instagram_service import InstagramService
from app.services.tool_builder_service import ToolBuilderService
from app.services.youtube_service import YouTubeService

router = APIRouter(tags=["tools"])
TERMINAL_JOB_STATUSES = {BuildStatus.RUNNING.value, BuildStatus.STOPPED.value, BuildStatus.FAILED.value}
JOB_EVENTS_POLL_INTERVAL_SECONDS = 2.5
JOB_EVENTS_MAX_LOGS_PER_TICK = 200


async def _client_disconnected(request: Request) -> bool:
    try:
        return await asyncio.wait_for(request.is_disconnected(), timeout=0.01)
    except TimeoutError:
        # `request.is_disconnected()` can block on some ASGI server/client combinations.
        return False


def get_tool_builder_service() -> ToolBuilderService:
    from app.main import app_state

    service = app_state.tool_builder_service
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Service is initializing")
    return service


def get_codex_service() -> CodexService:
    from app.main import app_state

    service = app_state.codex_service
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Service is initializing")
    return service


def get_instagram_service() -> InstagramService:
    from app.main import app_state

    service = app_state.instagram_service
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Service is initializing")
    return service


def get_youtube_service() -> YouTubeService:
    from app.main import app_state

    service = app_state.youtube_service
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Service is initializing")
    return service


def _normalized_viewer_origin(raw_value: str) -> str | None:
    value = (raw_value or "").strip()
    if not value:
        return None

    candidate = value if "://" in value else f"http://{value}"
    parsed = urlsplit(candidate)
    hostname = (parsed.hostname or "").strip()
    if not hostname:
        return None

    scheme = (parsed.scheme or "http").strip().lower()
    if scheme not in {"http", "https"}:
        scheme = "http"

    try:
        port = parsed.port
    except ValueError:
        port = None

    host = f"{hostname}:{port}" if port else hostname
    return f"{scheme}://{host}"


def _viewer_base_from_request(request: Request) -> str:
    viewer_origin = _normalized_viewer_origin(request.headers.get("x-toolhub-viewer-origin", ""))
    if viewer_origin:
        return viewer_origin

    forwarded_host = request.headers.get("x-forwarded-host", "").split(",", 1)[0].strip()
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
    scheme = forwarded_proto or request.url.scheme or "http"
    host = forwarded_host or request.headers.get("host", "").strip() or request.url.hostname or "localhost"
    return f"{scheme}://{host}"


@router.post("/tools/generate", response_model=GenerateToolResponse, status_code=status.HTTP_202_ACCEPTED)
def generate_tool(
    payload: GenerateToolRequest,
    service: ToolBuilderService = Depends(get_tool_builder_service),
) -> GenerateToolResponse:
    job = service.start_generation(payload.prompt, payload.name)
    return GenerateToolResponse(jobId=job["id"], status=job["status"])


@router.get("/tools", response_model=list[ToolResponse])
def list_tools(service: ToolBuilderService = Depends(get_tool_builder_service)) -> list[ToolResponse]:
    return [ToolResponse(**tool) for tool in service.list_tools()]


@router.get("/tools/{tool_id}", response_model=ToolResponse)
def get_tool(tool_id: str, service: ToolBuilderService = Depends(get_tool_builder_service)) -> ToolResponse:
    tool = service.get_tool(tool_id)
    if tool is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found")
    return ToolResponse(**tool)


@router.delete("/tools/{tool_id}", response_model=DeleteToolResponse)
def delete_tool(tool_id: str, service: ToolBuilderService = Depends(get_tool_builder_service)) -> DeleteToolResponse:
    deleted = service.delete_tool(tool_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found")
    return DeleteToolResponse(toolId=tool_id, deleted=True)


@router.post("/tools/{tool_id}/stop", response_model=ToolResponse)
def stop_tool(tool_id: str, service: ToolBuilderService = Depends(get_tool_builder_service)) -> ToolResponse:
    tool = service.stop_tool(tool_id)
    if tool is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found")
    return ToolResponse(**tool)


@router.post("/tools/{tool_id}/start", response_model=ToolResponse)
def start_tool(tool_id: str, service: ToolBuilderService = Depends(get_tool_builder_service)) -> ToolResponse:
    tool, error = service.start_tool(tool_id)
    if tool is None and error == "Tool not found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error)
    if tool is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error or "Unable to start tool")
    return ToolResponse(**tool)


@router.post("/tools/{tool_id}/rebuild", response_model=GenerateToolResponse, status_code=status.HTTP_202_ACCEPTED)
def rebuild_tool(tool_id: str, service: ToolBuilderService = Depends(get_tool_builder_service)) -> GenerateToolResponse:
    job, error = service.rebuild_tool(tool_id)
    if job is None and error == "Tool not found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error)
    if job is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error or "Unable to rebuild tool")
    return GenerateToolResponse(jobId=job["id"], status=job["status"])


@router.get("/codex/auth/status", response_model=CodexAuthStatusResponse)
def get_codex_auth_status(service: CodexService = Depends(get_codex_service)) -> CodexAuthStatusResponse:
    return CodexAuthStatusResponse(**service.get_login_status())


@router.post("/codex/auth/logout", response_model=CodexAuthStatusResponse)
def logout_codex_auth(service: CodexService = Depends(get_codex_service)) -> CodexAuthStatusResponse:
    return CodexAuthStatusResponse(**service.logout())


@router.get("/codex/usage", response_model=CodexUsageStatusResponse)
def get_codex_usage_status(service: CodexService = Depends(get_codex_service)) -> CodexUsageStatusResponse:
    return CodexUsageStatusResponse(**service.get_usage_status())


@router.get("/codex/auth/login/events")
def stream_codex_login_events(
    service: CodexService = Depends(get_codex_service),
) -> StreamingResponse:
    def event_generator():
        for event in service.stream_login_device_auth():
            if event.type == "log":
                chunk = service.clean_cli_stream_chunk(event.chunk or "")
                if not chunk.strip():
                    continue
                payload = {"type": "log", "chunk": chunk}
            else:
                result = event.result
                payload = {
                    "type": "done",
                    "success": bool(result and result.success),
                    "exitCode": int(result.exit_code if result else 1),
                    "logs": service.clean_cli_output(result.logs if result else ""),
                }

            yield f"data: {json.dumps(payload)}\n\n"

            if event.type == "done":
                break

    headers = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)


@router.get("/integrations/git/ssh/public-key", response_model=GitSshPublicKeyResponse)
def get_git_ssh_public_key(service: CodexService = Depends(get_codex_service)) -> GitSshPublicKeyResponse:
    try:
        payload = service.get_git_ssh_public_key()
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return GitSshPublicKeyResponse(**payload)


@router.post("/integrations/git/ssh/verify", response_model=VerifyGitSshResponse)
def verify_git_ssh_connection(
    payload: VerifyGitSshRequest,
    service: CodexService = Depends(get_codex_service),
) -> VerifyGitSshResponse:
    try:
        result = service.verify_git_ssh_connection(host=payload.host, username=payload.username)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return VerifyGitSshResponse(**result)


@router.get("/integrations/instagram/browser/session", response_model=InstagramBrowserSessionResponse)
def get_instagram_browser_session(
    request: Request,
    service: InstagramService = Depends(get_instagram_service),
) -> InstagramBrowserSessionResponse:
    try:
        session = service.get_browser_session(viewer_base_url=_viewer_base_from_request(request))
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return InstagramBrowserSessionResponse(**session)


@router.post("/integrations/instagram/browser/session", response_model=InstagramBrowserSessionResponse)
def start_instagram_browser_session(
    request: Request,
    forceRestart: bool = Query(default=False),
    width: int | None = Query(default=None, ge=640, le=3840),
    height: int | None = Query(default=None, ge=480, le=3840),
    service: InstagramService = Depends(get_instagram_service),
) -> InstagramBrowserSessionResponse:
    try:
        session = service.start_browser_session(
            viewer_base_url=_viewer_base_from_request(request),
            force_restart=forceRestart,
            viewport_width=width,
            viewport_height=height,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return InstagramBrowserSessionResponse(**session)


@router.delete("/integrations/instagram/browser/session", response_model=InstagramBrowserSessionResponse)
def stop_instagram_browser_session(
    request: Request,
    service: InstagramService = Depends(get_instagram_service),
) -> InstagramBrowserSessionResponse:
    try:
        session = service.stop_browser_session(viewer_base_url=_viewer_base_from_request(request))
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return InstagramBrowserSessionResponse(**session)


@router.post("/integrations/instagram/browser/reels/scroll", response_model=InstagramReelsControlResponse)
def control_instagram_reels_scroll(
    action: str = Query(..., pattern="^(swipe_up|swipe_down)$"),
    service: InstagramService = Depends(get_instagram_service),
) -> InstagramReelsControlResponse:
    try:
        result = service.control_reels_scroll(action=action)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return InstagramReelsControlResponse(**result)


@router.get("/integrations/youtube/shorts/feed", response_model=YouTubeShortFeedResponse)
def get_youtube_shorts_feed(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=24, ge=1, le=40),
    service: YouTubeService = Depends(get_youtube_service),
) -> YouTubeShortFeedResponse:
    try:
        payload = service.fetch_shorts_feed(cursor=cursor, limit=limit)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return YouTubeShortFeedResponse(**payload)


@router.get("/integrations/youtube/shorts/settings", response_model=YouTubeShortSettingsResponse)
def get_youtube_shorts_settings(
    service: YouTubeService = Depends(get_youtube_service),
) -> YouTubeShortSettingsResponse:
    payload = service.get_shorts_settings()
    return YouTubeShortSettingsResponse(**payload)


@router.put("/integrations/youtube/shorts/settings", response_model=YouTubeShortSettingsResponse)
def update_youtube_shorts_settings(
    payload: UpdateYouTubeShortSettingsRequest,
    service: YouTubeService = Depends(get_youtube_service),
) -> YouTubeShortSettingsResponse:
    try:
        updated = service.update_shorts_settings(
            queries=[{"category": item.category, "query": item.query} for item in payload.queries],
            preferred_categories=list(payload.preferredCategories or []),
            category_boost_factor=payload.categoryBoostFactor,
            region_code=payload.regionCode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return YouTubeShortSettingsResponse(**updated)


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str, service: ToolBuilderService = Depends(get_tool_builder_service)) -> JobResponse:
    job = service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return JobResponse(**job)


@router.delete("/jobs/{job_id}", response_model=DeleteJobResponse)
def delete_job(job_id: str, service: ToolBuilderService = Depends(get_tool_builder_service)) -> DeleteJobResponse:
    deleted = service.delete_job(job_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return DeleteJobResponse(jobId=job_id, deleted=True)


@router.post("/jobs/{job_id}/stop", response_model=StopJobResponse)
def stop_job(job_id: str, service: ToolBuilderService = Depends(get_tool_builder_service)) -> StopJobResponse:
    job, error = service.stop_job(job_id)
    if job is None and error == "Job not found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error)
    if job is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error or "Unable to stop job")
    return StopJobResponse(jobId=job_id, stopped=True)


@router.get("/jobs", response_model=list[JobSummaryResponse])
def list_jobs(service: ToolBuilderService = Depends(get_tool_builder_service)) -> list[JobSummaryResponse]:
    jobs = service.list_jobs()
    return [JobSummaryResponse(**job) for job in jobs]


@router.get("/jobs/{job_id}/log-artifacts", response_model=list[JobLogArtifactResponse])
def list_job_log_artifacts(
    job_id: str,
    service: ToolBuilderService = Depends(get_tool_builder_service),
) -> list[JobLogArtifactResponse]:
    job = service.get_job_state(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    artifacts = service.list_job_log_artifacts(job_id)
    return [JobLogArtifactResponse(**artifact) for artifact in artifacts]


@router.get("/jobs/{job_id}/log-artifacts/{artifact_id}/download")
def download_job_log_artifact(
    job_id: str,
    artifact_id: str,
    service: ToolBuilderService = Depends(get_tool_builder_service),
) -> Response:
    artifact = service.get_job_log_artifact(job_id, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Log artifact not found")

    response = Response(
        content=artifact.get("content") or "",
        media_type=str(artifact.get("contentType") or "text/plain; charset=utf-8"),
    )
    response.headers["Content-Disposition"] = f'attachment; filename="{artifact["fileName"]}"'
    return response


@router.get("/jobs/{job_id}/log-artifacts/{artifact_id}/view")
def view_job_log_artifact(
    job_id: str,
    artifact_id: str,
    service: ToolBuilderService = Depends(get_tool_builder_service),
) -> Response:
    artifact = service.get_job_log_artifact(job_id, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Log artifact not found")

    response = Response(
        content=artifact.get("content") or "",
        media_type=str(artifact.get("contentType") or "text/plain; charset=utf-8"),
    )
    response.headers["Content-Disposition"] = f'inline; filename="{artifact["fileName"]}"'
    return response


@router.get("/jobs/{job_id}/events")
async def stream_job_events(
    job_id: str,
    request: Request,
    service: ToolBuilderService = Depends(get_tool_builder_service),
) -> StreamingResponse:
    initial_job = service.get_job_state(job_id)
    if initial_job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    async def event_generator():
        last_timestamp: datetime | None = None
        while True:
            if await _client_disconnected(request):
                break

            job = service.get_job_state(job_id)
            if job is None:
                payload = {"jobId": job_id, "status": BuildStatus.FAILED.value, "error": "Job not found", "logs": []}
                yield f"data: {json.dumps(payload)}\n\n"
                break

            new_logs = service.get_job_logs_after(
                job_id,
                last_timestamp,
                limit=JOB_EVENTS_MAX_LOGS_PER_TICK,
            )
            if new_logs:
                last_timestamp = new_logs[-1]["timestamp"]

            event = JobEventResponse(
                jobId=job_id,
                status=job["status"],
                error=job.get("error"),
                updatedAt=job["updatedAt"],
                logs=new_logs,
            )
            yield f"data: {event.model_dump_json()}\n\n"

            if job["status"] in TERMINAL_JOB_STATUSES:
                break
            await asyncio.sleep(JOB_EVENTS_POLL_INTERVAL_SECONDS)

    headers = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)
