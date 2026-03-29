from pathlib import Path, PurePosixPath

from app.utils.config import Settings


class OperatorAccessService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def allowed_paths(self) -> list[str]:
        allowed = [self._normalize_host_path(path) for path in self._settings.allowed_paths]
        if allowed:
            return allowed
        return ["/"]

    def denied_paths(self) -> list[str]:
        return [self._normalize_host_path(path) for path in self._settings.denied_paths]

    def build_mounts(self, primary_path: str | None = None) -> dict[str, dict[str, str]]:
        mounts: dict[str, dict[str, str]] = {}
        allowed_paths = self.allowed_paths()

        for allowed_path in allowed_paths:
            if allowed_path == "/" and self._is_macos_host():
                # Docker Desktop on macOS does not allow mounting "/" directly.
                # We map concrete host roots instead via _root_supplemental_mounts.
                continue
            if allowed_path == self._normalize_host_path(self._settings.codex_workspace_host):
                # Keep CODEX_WORKSPACE_HOST dedicated mount intact for auth/session state.
                continue
            mounts[allowed_path] = {
                "bind": self.to_container_path(allowed_path),
                "mode": "rw",
            }

        if "/" in allowed_paths:
            for supplemental_path in self._root_supplemental_mounts(primary_path):
                if supplemental_path in mounts:
                    continue
                mounts[supplemental_path] = {
                    "bind": self.to_container_path(supplemental_path),
                    "mode": "rw",
                }

        placeholder_root = Path(self._settings.codex_workspace_host) / ".operator-denied"
        placeholder_root.mkdir(parents=True, exist_ok=True)
        for index, denied_path in enumerate(self.denied_paths()):
            placeholder = placeholder_root / str(index)
            placeholder.mkdir(parents=True, exist_ok=True)
            mounts[str(placeholder)] = {
                "bind": self.to_container_path(denied_path),
                "mode": "ro",
            }

        return mounts

    def normalize_cwd(self, cwd: str | None) -> str:
        if cwd:
            normalized = self._normalize_host_path(cwd)
            if self.is_denied(normalized):
                raise ValueError(f"Path `{normalized}` is denied by OPERATOR_DENIED_PATHS")
            return normalized
        for project_path in self.project_paths():
            if Path(project_path).exists():
                return project_path
        allowed = self.allowed_paths()[0]
        if allowed == "/" and not self.is_denied("/Users"):
            return "/Users"
        return allowed

    def project_paths(self) -> list[str]:
        return [self._normalize_host_path(path) for path in self._settings.project_paths]

    def resolve_project_path(self, project_hint: str | None) -> str | None:
        if not project_hint:
            return None
        hint = project_hint.strip().lower()
        if not hint:
            return None

        for project_path in self.project_paths():
            if not Path(project_path).exists():
                continue
            name = PurePosixPath(project_path).name.lower()
            if hint == name or hint in name or name in hint:
                return project_path
        return None

    def is_denied(self, path: str) -> bool:
        normalized = self._normalize_host_path(path)
        for denied in self.denied_paths():
            if normalized == denied or normalized.startswith(f"{denied}/"):
                return True
        return False

    def to_container_path(self, host_path: str) -> str:
        normalized = self._normalize_host_path(host_path)
        return f"/host{normalized}"

    def policy_summary(self) -> str:
        allowed = ", ".join(self.allowed_paths()) or "(none)"
        denied = ", ".join(self.denied_paths()) or "(none)"
        return f"Allowed host paths: {allowed}\nDenied host paths: {denied}"

    def _root_supplemental_mounts(self, primary_path: str | None = None) -> list[str]:
        # Docker Desktop exposes host files via shared roots (for example /Users on macOS).
        # When OPERATOR_ALLOWED_PATHS is empty, we map only concrete roots that are likely accessible.
        derived_roots: set[str] = set()
        if primary_path:
            derived = self._top_level_root(primary_path)
            if derived:
                derived_roots.add(derived)
        for project_path in self.project_paths():
            derived = self._top_level_root(project_path)
            if derived:
                derived_roots.add(derived)

        if self._is_macos_host():
            # Keep macOS mounts narrow: only roots needed for the current task/projects.
            # This avoids Docker Desktop "path is not shared from the host" errors.
            candidates = set(derived_roots)
            if not candidates:
                candidates.add("/Users")
        else:
            candidates = {"/Users", "/home", "/Volumes", "/private", "/tmp", "/var", "/etc"}
            candidates.update(derived_roots)

        return sorted(candidate for candidate in candidates if Path(candidate).exists())

    @staticmethod
    def _top_level_root(path: str) -> str | None:
        normalized = OperatorAccessService._normalize_host_path(path)
        parts = PurePosixPath(normalized).parts
        if len(parts) < 2:
            return None
        return f"/{parts[1]}"

    def _is_macos_host(self) -> bool:
        # Backend runs inside a Linux container, so platform.system() reflects the container OS,
        # not the actual host. Infer host OS from configured host paths instead.
        probe_paths = [
            self._settings.codex_workspace_host,
            *self._settings.allowed_paths,
            *self._settings.project_paths,
        ]
        normalized = [self._normalize_host_path(path) for path in probe_paths if path and path.strip()]
        if any(path == "/Users" or path.startswith("/Users/") for path in normalized):
            return True
        if any(path == "/Volumes" or path.startswith("/Volumes/") for path in normalized):
            return True
        if any(path == "/private" or path.startswith("/private/") for path in normalized):
            return True
        return False

    @staticmethod
    def _normalize_host_path(path: str) -> str:
        raw = path.strip()
        if not raw:
            return "/"
        pure = PurePosixPath(raw)
        if not pure.is_absolute():
            pure = PurePosixPath("/") / pure
        return str(pure)
