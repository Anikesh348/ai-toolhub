from dataclasses import dataclass
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient

from app.api.chat_routes import router as chat_router
from app.api.tool_routes import router as tool_router
from app.repositories.build_log_repository import BuildLogRepository
from app.repositories.chat_message_repository import ChatMessageRepository
from app.repositories.chat_session_repository import ChatSessionRepository
from app.repositories.port_allocation_repository import PortAllocationRepository
from app.repositories.request_repository import RequestRepository
from app.repositories.tool_repository import ToolRepository
from app.services.alert_service import AlertService
from app.services.chat_service import ChatService
from app.services.codex_service import CodexService
from app.services.docker_service import DockerService
from app.services.monitor_service import ToolMonitorService
from app.services.operator_access_service import OperatorAccessService
from app.services.port_allocator_service import PortAllocatorService
from app.services.prompt_service import PromptService
from app.services.system_context_service import SystemContextService
from app.services.testing_service import TestingService
from app.services.tool_builder_service import ToolBuilderService
from app.utils.config import get_settings
from app.utils.logger import configure_logging
from app.workflows.tool_build_workflow import ToolBuildWorkflow


@dataclass
class AppState:
    mongo_client: Optional[MongoClient] = None
    tool_builder_service: Optional[ToolBuilderService] = None
    tool_monitor_service: Optional[ToolMonitorService] = None
    chat_service: Optional[ChatService] = None
    codex_service: Optional[CodexService] = None


app_state = AppState()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.app_log_level)

    app = FastAPI(title=settings.app_name)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(tool_router)
    app.include_router(chat_router)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.on_event("startup")
    def startup_event() -> None:
        mongo_client = MongoClient(settings.mongo_uri)
        db = mongo_client[settings.mongo_db_name]
        requests_collection_name = f"{settings.mongo_collection_prefix}_tool_requests"
        logs_collection_name = f"{settings.mongo_collection_prefix}_tool_build_logs"
        tools_collection_name = f"{settings.mongo_collection_prefix}_tools"
        ports_collection_name = f"{settings.mongo_collection_prefix}_tool_port_allocations"
        chat_sessions_collection_name = f"{settings.mongo_collection_prefix}_chat_sessions"
        chat_messages_collection_name = f"{settings.mongo_collection_prefix}_chat_messages"

        db[requests_collection_name].create_index("status")
        db[requests_collection_name].create_index([("createdAt", -1)])
        db[logs_collection_name].create_index([("requestId", 1), ("timestamp", 1)])
        db[tools_collection_name].create_index("toolId", unique=True)
        db[tools_collection_name].create_index("status")
        db[tools_collection_name].create_index("requestId")
        db[tools_collection_name].create_index([("createdAt", -1)])
        db[tools_collection_name].create_index([("status", 1), ("crashAlertSent", 1)])
        db[tools_collection_name].create_index("port")
        db[tools_collection_name].create_index("uiPort")
        db[ports_collection_name].create_index("port", unique=True)
        db[ports_collection_name].create_index("requestId")
        db[ports_collection_name].create_index("toolId")
        db[chat_sessions_collection_name].create_index("updatedAt")
        db[chat_sessions_collection_name].create_index("archived")
        db[chat_sessions_collection_name].create_index([("archived", 1), ("updatedAt", -1)])
        db[chat_messages_collection_name].create_index([("sessionId", 1), ("createdAt", 1)])

        request_repository = RequestRepository(db[requests_collection_name])
        build_log_repository = BuildLogRepository(db[logs_collection_name])
        tool_repository = ToolRepository(db[tools_collection_name])
        port_allocation_repository = PortAllocationRepository(db[ports_collection_name])
        chat_session_repository = ChatSessionRepository(db[chat_sessions_collection_name])
        chat_message_repository = ChatMessageRepository(db[chat_messages_collection_name])

        docker_service = DockerService(settings)
        prompt_service = PromptService()
        codex_service = CodexService(settings, docker_service)
        operator_access_service = OperatorAccessService(settings)
        system_context_service = SystemContextService()
        testing_service = TestingService(settings, docker_service)
        port_allocator_service = PortAllocatorService(settings, tool_repository, port_allocation_repository)
        alert_service = AlertService(settings)

        workflow = ToolBuildWorkflow(
            settings=settings,
            request_repository=request_repository,
            build_log_repository=build_log_repository,
            tool_repository=tool_repository,
            prompt_service=prompt_service,
            codex_service=codex_service,
            testing_service=testing_service,
            docker_service=docker_service,
            port_allocator_service=port_allocator_service,
            alert_service=alert_service,
        )
        tool_builder_service = ToolBuilderService(
            request_repository=request_repository,
            build_log_repository=build_log_repository,
            tool_repository=tool_repository,
            docker_service=docker_service,
            testing_service=testing_service,
            port_allocator_service=port_allocator_service,
            workflow=workflow,
        )
        monitor_service = ToolMonitorService(
            settings=settings,
            tool_repository=tool_repository,
            request_repository=request_repository,
            docker_service=docker_service,
            alert_service=alert_service,
        )
        monitor_service.start()
        chat_service = ChatService(
            session_repository=chat_session_repository,
            message_repository=chat_message_repository,
            codex_service=codex_service,
            docker_service=docker_service,
            operator_access_service=operator_access_service,
            system_context_service=system_context_service,
            tool_builder_service=tool_builder_service,
        )

        app_state.mongo_client = mongo_client
        app_state.tool_builder_service = tool_builder_service
        app_state.tool_monitor_service = monitor_service
        app_state.chat_service = chat_service
        app_state.codex_service = codex_service

    @app.on_event("shutdown")
    def shutdown_event() -> None:
        if app_state.tool_monitor_service:
            app_state.tool_monitor_service.stop()
        app_state.chat_service = None
        app_state.codex_service = None
        if app_state.mongo_client:
            app_state.mongo_client.close()

    return app


app = create_app()
