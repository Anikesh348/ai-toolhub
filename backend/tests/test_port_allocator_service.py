from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services.port_allocator_service import PortAllocatorService


def test_allocate_port_skips_docker_published_host_ports() -> None:
    settings = SimpleNamespace(port_range_start=3005, port_range_end=3007)
    tool_repository = Mock()
    tool_repository.get_ports_in_use.return_value = []
    port_allocation_repository = Mock()
    port_allocation_repository.get_reserved_ports.return_value = set()
    port_allocation_repository.reserve_port.return_value = True
    docker_service = Mock()
    docker_service.get_published_host_ports.return_value = {3005}

    service = PortAllocatorService(
        settings=settings,  # type: ignore[arg-type]
        tool_repository=tool_repository,  # type: ignore[arg-type]
        port_allocation_repository=port_allocation_repository,  # type: ignore[arg-type]
        docker_service=docker_service,  # type: ignore[arg-type]
    )

    with patch.object(PortAllocatorService, "_is_port_free", return_value=True), patch.object(
        PortAllocatorService,
        "_is_host_gateway_port_free",
        return_value=True,
    ), patch.object(PortAllocatorService, "_can_bind", return_value=True):
        allocated = service.allocate_port()

    assert allocated == 3006
    port_allocation_repository.reserve_port.assert_called_once_with(3006, None, None)


def test_is_port_available_false_when_docker_has_published_port() -> None:
    settings = SimpleNamespace(port_range_start=3001, port_range_end=3999)
    tool_repository = Mock()
    tool_repository.get_ports_in_use.return_value = []
    port_allocation_repository = Mock()
    docker_service = Mock()
    docker_service.get_published_host_ports.return_value = {3005}

    service = PortAllocatorService(
        settings=settings,  # type: ignore[arg-type]
        tool_repository=tool_repository,  # type: ignore[arg-type]
        port_allocation_repository=port_allocation_repository,  # type: ignore[arg-type]
        docker_service=docker_service,  # type: ignore[arg-type]
    )

    with patch.object(PortAllocatorService, "_is_port_free", return_value=True), patch.object(
        PortAllocatorService,
        "_is_host_gateway_port_free",
        return_value=True,
    ), patch.object(PortAllocatorService, "_can_bind", return_value=True):
        assert service.is_port_available(3005) is False


def test_allocate_port_retries_next_when_host_gateway_reports_used() -> None:
    settings = SimpleNamespace(port_range_start=3010, port_range_end=3012)
    tool_repository = Mock()
    tool_repository.get_ports_in_use.return_value = []
    port_allocation_repository = Mock()
    port_allocation_repository.get_reserved_ports.return_value = set()
    port_allocation_repository.reserve_port.return_value = True

    service = PortAllocatorService(
        settings=settings,  # type: ignore[arg-type]
        tool_repository=tool_repository,  # type: ignore[arg-type]
        port_allocation_repository=port_allocation_repository,  # type: ignore[arg-type]
        docker_service=None,
    )

    with patch.object(PortAllocatorService, "_is_port_free", return_value=True), patch.object(
        PortAllocatorService,
        "_is_host_gateway_port_free",
        side_effect=[False, True, True],
    ), patch.object(PortAllocatorService, "_can_bind", return_value=True):
        allocated = service.allocate_port()

    assert allocated == 3011
