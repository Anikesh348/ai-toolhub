import socket

from app.repositories.port_allocation_repository import PortAllocationRepository
from app.repositories.tool_repository import ToolRepository
from app.utils.config import Settings


class PortAllocatorService:
    def __init__(
        self,
        settings: Settings,
        tool_repository: ToolRepository,
        port_allocation_repository: PortAllocationRepository,
    ) -> None:
        self._settings = settings
        self._tool_repository = tool_repository
        self._port_allocation_repository = port_allocation_repository

    def allocate_port(
        self,
        reserve: bool = True,
        tool_id: str | None = None,
        request_id: str | None = None,
    ) -> int:
        allocated = self.allocate_ports(
            count=1,
            reserve=reserve,
            tool_id=tool_id,
            request_id=request_id,
        )
        if allocated:
            return allocated[0]
        raise RuntimeError("No available ports in configured range")

    def allocate_ports(
        self,
        count: int,
        reserve: bool = True,
        tool_id: str | None = None,
        request_id: str | None = None,
    ) -> list[int]:
        if count <= 0:
            return []

        used_ports = set(self._tool_repository.get_ports_in_use())
        reserved_ports = set(self._port_allocation_repository.get_reserved_ports())
        allocated: list[int] = []

        for port in range(self._settings.port_range_start, self._settings.port_range_end + 1):
            if port in used_ports or port in reserved_ports:
                continue
            if not self._is_port_free(port) or not self._can_bind(port):
                continue

            if reserve and not self._port_allocation_repository.reserve_port(port, tool_id, request_id):
                continue

            allocated.append(port)
            used_ports.add(port)
            reserved_ports.add(port)
            if len(allocated) == count:
                return allocated

        raise RuntimeError("No available ports in configured range")

    def is_port_available(self, port: int, exclude_tool_id: str | None = None) -> bool:
        if port < self._settings.port_range_start or port > self._settings.port_range_end:
            return False
        used_ports = self._tool_repository.get_ports_in_use(exclude_tool_id=exclude_tool_id)
        if port in used_ports:
            return False
        return self._is_port_free(port) and self._can_bind(port)

    @staticmethod
    def _is_port_free(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            return sock.connect_ex(("127.0.0.1", port)) != 0

    @staticmethod
    def _can_bind(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("0.0.0.0", port))
                return True
            except OSError:
                return False
