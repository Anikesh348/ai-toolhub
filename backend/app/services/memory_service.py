import re
import threading
from pathlib import Path

from app.utils.time import now_ist


class MemoryService:
    _TYPE_BLOCK_START = "<!-- managed:message-types:start -->"
    _TYPE_BLOCK_END = "<!-- managed:message-types:end -->"
    _TYPE_LINE_RE = re.compile(
        r"^- \*\*(?P<kind>[^*]+)\*\*: (?P<count>\d+) message(?:s)?; last: (?P<last>[^;]+); recent: (?P<recent>.*)$"
    )
    _REMEMBER_PATTERNS = (
        re.compile(r"\b(?:please\s+)?remember(?:\s+that)?\s+(.+)", flags=re.IGNORECASE | re.DOTALL),
        re.compile(r"\bmemorize(?:\s+that)?\s+(.+)", flags=re.IGNORECASE | re.DOTALL),
        re.compile(r"\bnote(?:\s+down)?(?:\s+that)?\s+(.+)", flags=re.IGNORECASE | re.DOTALL),
        re.compile(r"\bkeep\s+(?:this\s+)?(?:in\s+)?memory\s*[:\-]?\s*(.+)", flags=re.IGNORECASE | re.DOTALL),
        re.compile(r"\badd\s+to\s+memory\s*:\s*(.+)", flags=re.IGNORECASE | re.DOTALL),
        re.compile(r"\bsave\s+(?:this\s+)?(?:to\s+)?memory\s*[:\-]?\s*(.+)", flags=re.IGNORECASE | re.DOTALL),
        re.compile(r"\bsave\s+(?:this\s+)?fact\s*[:\-]?\s*(?:that\s+)?(.+)", flags=re.IGNORECASE | re.DOTALL),
        re.compile(r"\bstore\s+(?:this\s+)?(?:fact\s+)?(?:in\s+memory\s*)?[:\-]?\s*(?:that\s+)?(.+)", flags=re.IGNORECASE | re.DOTALL),
    )

    def __init__(self, memory_path: str | Path, enabled: bool = True) -> None:
        self._memory_path = Path(memory_path).expanduser()
        self._enabled = enabled
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._memory_path

    def prompt_context(self, limit: int = 4000) -> str:
        if not self._enabled:
            return ""
        content = self._read()
        if not content.strip():
            return ""
        return content.strip()[:limit]

    def update_from_user_message(self, content: str, mode: str) -> None:
        if not self._enabled:
            return
        cleaned = self._clean_message(content)
        if not cleaned:
            return

        with self._lock:
            current = self._ensure_document(self._read())
            remembered_note = self._extract_remembered_note(cleaned)
            if remembered_note:
                current = self._append_remembered_note(current, remembered_note)
            current = self._update_message_type_summary(current, mode=mode, message_preview=self._preview(cleaned))
            self._write(current)

    def _read(self) -> str:
        try:
            return self._memory_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except OSError:
            return ""

    def _write(self, content: str) -> None:
        self._memory_path.parent.mkdir(parents=True, exist_ok=True)
        self._memory_path.write_text(content.rstrip() + "\n", encoding="utf-8")

    def _ensure_document(self, content: str) -> str:
        if content.strip():
            return content
        return (
            "# Agent Memory\n\n"
            "## Remembered Notes\n"
            "- Add durable user preferences, project facts, or operating instructions here.\n\n"
            "## Message Type Summary\n"
            f"{self._TYPE_BLOCK_START}\n"
            "_No messages recorded yet._\n"
            f"{self._TYPE_BLOCK_END}\n"
        )

    @classmethod
    def _extract_remembered_note(cls, content: str) -> str | None:
        for pattern in cls._REMEMBER_PATTERNS:
            match = pattern.search(content)
            if not match:
                continue
            note = cls._clean_note(match.group(1))
            if note:
                return note
        return None

    @staticmethod
    def _clean_message(content: str) -> str:
        return re.sub(r"\s+", " ", content or "").strip()

    @staticmethod
    def _clean_note(content: str) -> str:
        note = re.sub(r"\s+", " ", content or "").strip(" .")
        note = re.sub(r"^(?:that|this)\s+", "", note, flags=re.IGNORECASE).strip(" .")
        return note[:500]

    @staticmethod
    def _preview(content: str) -> str:
        preview = re.sub(r"[\r\n|]+", " ", content).strip()
        return preview[:120]

    def _append_remembered_note(self, content: str, note: str) -> str:
        bullet = f"- {note}"
        normalized_note = bullet.lower()
        existing_lines = [line.strip().lower() for line in content.splitlines()]
        if normalized_note in existing_lines:
            return content

        marker = "## Remembered Notes"
        if marker not in content:
            return f"{content.rstrip()}\n\n{marker}\n{bullet}\n"

        lines = content.splitlines()
        insert_at = len(lines)
        for index, line in enumerate(lines):
            if index == 0:
                continue
            if line.startswith("## ") and line.strip() != marker and self._seen_marker(lines[:index], marker):
                insert_at = index
                break

        while insert_at > 0 and lines[insert_at - 1].strip() == "":
            insert_at -= 1
        lines.insert(insert_at, bullet)
        return "\n".join(lines) + "\n"

    @staticmethod
    def _seen_marker(lines: list[str], marker: str) -> bool:
        return any(line.strip() == marker for line in lines)

    def _update_message_type_summary(self, content: str, mode: str, message_preview: str) -> str:
        stats = self._parse_type_stats(content)
        kind = self._normalize_mode(mode)
        now = now_ist().isoformat()
        current = stats.get(kind, {"count": 0, "last": now, "recent": []})
        recent = [item for item in current.get("recent", []) if item != message_preview]
        recent.insert(0, message_preview)
        stats[kind] = {
            "count": int(current.get("count", 0)) + 1,
            "last": now,
            "recent": recent[:3],
        }
        block = self._render_type_block(stats)
        if self._TYPE_BLOCK_START in content and self._TYPE_BLOCK_END in content:
            pattern = re.compile(
                rf"{re.escape(self._TYPE_BLOCK_START)}.*?{re.escape(self._TYPE_BLOCK_END)}",
                flags=re.DOTALL,
            )
            return pattern.sub(block, content, count=1)
        return f"{content.rstrip()}\n\n## Message Type Summary\n{block}\n"

    def _parse_type_stats(self, content: str) -> dict[str, dict]:
        stats: dict[str, dict] = {}
        in_block = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped == self._TYPE_BLOCK_START:
                in_block = True
                continue
            if stripped == self._TYPE_BLOCK_END:
                break
            if not in_block:
                continue
            match = self._TYPE_LINE_RE.match(stripped)
            if not match:
                continue
            recent = [item.strip() for item in match.group("recent").split(" | ") if item.strip()]
            stats[self._normalize_mode(match.group("kind"))] = {
                "count": int(match.group("count")),
                "last": match.group("last").strip(),
                "recent": recent[:3],
            }
        return stats

    def _render_type_block(self, stats: dict[str, dict]) -> str:
        lines = [self._TYPE_BLOCK_START]
        for kind in sorted(stats):
            item = stats[kind]
            recent = " | ".join(str(value) for value in item.get("recent", []) if value) or "none"
            count = int(item.get("count", 0))
            noun = "message" if count == 1 else "messages"
            lines.append(f"- **{kind}**: {count} {noun}; last: {item.get('last')}; recent: {recent}")
        lines.append(self._TYPE_BLOCK_END)
        return "\n".join(lines)

    @staticmethod
    def _normalize_mode(mode: str) -> str:
        cleaned = (mode or "general").strip().lower()
        if cleaned == "pi_operator":
            return "operator"
        if cleaned in {"general", "operator", "tool_builder"}:
            return cleaned
        return "general"
