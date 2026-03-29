import os
import platform
import socket
from datetime import datetime, timezone
from typing import Any

import psutil


class SystemContextService:
    def snapshot(self) -> dict[str, Any]:
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        battery = psutil.sensors_battery()
        load_averages: tuple[float, float, float] | None = None
        if hasattr(os, "getloadavg"):
            try:
                load_averages = os.getloadavg()
            except OSError:
                load_averages = None

        uptime_seconds = max(int(datetime.now(tz=timezone.utc).timestamp() - psutil.boot_time()), 0)

        return {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "isContainer": self._is_container_runtime(),
            "cpu": {
                "percent": round(psutil.cpu_percent(interval=0.15), 1),
                "physicalCores": psutil.cpu_count(logical=False),
                "logicalCores": psutil.cpu_count(logical=True),
                "loadAverages": [round(value, 2) for value in load_averages] if load_averages else None,
            },
            "memory": {
                "totalBytes": memory.total,
                "usedBytes": memory.used,
                "availableBytes": memory.available,
                "percent": round(memory.percent, 1),
            },
            "disk": {
                "path": "/",
                "totalBytes": disk.total,
                "usedBytes": disk.used,
                "freeBytes": disk.free,
                "percent": round(disk.percent, 1),
            },
            "battery": (
                {
                    "percent": round(float(battery.percent), 1) if battery.percent is not None else None,
                    "isPlugged": bool(battery.power_plugged),
                    "secondsLeft": int(battery.secsleft) if isinstance(battery.secsleft, int) else None,
                }
                if battery
                else None
            ),
            "uptimeSeconds": uptime_seconds,
        }

    def format_for_prompt(self) -> str:
        snapshot = self.snapshot()
        battery = snapshot["battery"]
        cpu = snapshot["cpu"]
        memory = snapshot["memory"]
        disk = snapshot["disk"]

        battery_summary = "Battery telemetry unavailable"
        if battery:
            battery_state = "plugged in" if battery["isPlugged"] else "on battery"
            battery_pct = f"{battery['percent']}%" if battery["percent"] is not None else "unknown%"
            battery_summary = f"{battery_pct}, {battery_state}"

        load_avg = cpu.get("loadAverages")
        load_summary = ", ".join(str(value) for value in load_avg) if load_avg else "unavailable"

        lines = [
            "Runtime telemetry (current environment):",
            f"- Hostname: {snapshot['hostname']}",
            f"- Platform: {snapshot['platform']}",
            f"- CPU usage: {cpu['percent']}% (cores: {cpu['physicalCores']} physical / {cpu['logicalCores']} logical)",
            f"- Load average (1m,5m,15m): {load_summary}",
            (
                f"- Memory: {memory['percent']}% used "
                f"({self._bytes_to_gb(memory['usedBytes'])}GB / {self._bytes_to_gb(memory['totalBytes'])}GB)"
            ),
            (
                f"- Disk (/): {disk['percent']}% used "
                f"({self._bytes_to_gb(disk['usedBytes'])}GB / {self._bytes_to_gb(disk['totalBytes'])}GB)"
            ),
            f"- Battery: {battery_summary}",
            f"- Uptime: {self._format_uptime(snapshot['uptimeSeconds'])}",
        ]
        if snapshot["isContainer"]:
            lines.append(
                "- Runtime note: Agent is running inside a container, so host-only signals may be limited by container boundaries."
            )
        return "\n".join(lines)

    @staticmethod
    def _is_container_runtime() -> bool:
        return os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")

    @staticmethod
    def _bytes_to_gb(value: int) -> str:
        return f"{(value / (1024 ** 3)):.2f}"

    @staticmethod
    def _format_uptime(seconds: int) -> str:
        days, rem = divmod(seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, _ = divmod(rem, 60)
        if days > 0:
            return f"{days}d {hours}h {minutes}m"
        return f"{hours}h {minutes}m"
