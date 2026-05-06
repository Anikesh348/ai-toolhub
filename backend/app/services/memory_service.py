import re
import threading
from pathlib import Path

from app.utils.time import now_ist


class MemoryService:
    _PROFILE_BLOCK_START = "<!-- managed:user-profile:start -->"
    _PROFILE_BLOCK_END = "<!-- managed:user-profile:end -->"
    _TYPE_BLOCK_START = "<!-- managed:message-types:start -->"
    _TYPE_BLOCK_END = "<!-- managed:message-types:end -->"
    _ANALYSIS_BLOCK_START = "<!-- managed:chat-analysis:start -->"
    _ANALYSIS_BLOCK_END = "<!-- managed:chat-analysis:end -->"
    _PROFILE_LINE_RE = re.compile(
        r"^- \*\*(?P<key>[^*]+)\*\*: (?P<value>.*?)(?: \(source: (?P<source>.*?); last: (?P<last>.*?)\))?$"
    )
    _TYPE_LINE_RE = re.compile(
        r"^- \*\*(?P<kind>[^*]+)\*\*: (?P<count>\d+) message(?:s)?; last: (?P<last>[^;]+); recent: (?P<recent>.*)$"
    )
    _ANALYSIS_LINE_RE = re.compile(
        r"^- \*\*(?P<kind>[^*]+)\*\*: (?P<count>\d+) message(?:s)?; "
        r"last: (?P<last>[^;]+); signals: (?P<signals>[^;]*); recent: (?P<recent>.*)$"
    )
    _REMEMBER_PATTERNS = (
        re.compile(r"\b(?:please\s+)?remember(?:\s+that)?\s+(.+)", flags=re.IGNORECASE | re.DOTALL),
        re.compile(r"\bmemorize(?:\s+that)?\s+(.+)", flags=re.IGNORECASE | re.DOTALL),
        re.compile(r"\bnote(?:\s+down)?(?:\s+that)?\s+(.+)", flags=re.IGNORECASE | re.DOTALL),
        re.compile(r"\bkeep\s+(?:this\s+)?(?:in\s+)?memory\s*[:\-]?\s*(.+)", flags=re.IGNORECASE | re.DOTALL),
        re.compile(r"\badd\s+to\s+memory\s*:\s*(.+)", flags=re.IGNORECASE | re.DOTALL),
        re.compile(r"\bsave\s+(?:this\s+)?(?:to\s+)?memory\s*[:\-]?\s*(.+)", flags=re.IGNORECASE | re.DOTALL),
        re.compile(r"\bsave\s+(?:this\s+)?fact\s*[:\-]?\s*(?:that\s+)?(.+)", flags=re.IGNORECASE | re.DOTALL),
        re.compile(
            r"\bstore\s+(?:this\s+)?(?:fact\s+)?(?:in\s+memory\s*)?[:\-]?\s*(?:that\s+)?(.+)",
            flags=re.IGNORECASE | re.DOTALL,
        ),
    )
    _FAVORITE_KEY_RE = re.compile(r"^(?:fav|favourite|favorite|preferred)\s+(.+)$", flags=re.IGNORECASE)
    _DIRECT_FACT_PATTERNS = (
        re.compile(
            r"\bmy\s+(?P<key>fav(?:ou?rite)?|preferred)\s+(?P<object>[a-z0-9][a-z0-9 /&+\-]{1,80}?)\s+"
            r"(?:is|=|:)\s+(?P<value>[^.?!\n]{1,160})",
            flags=re.IGNORECASE,
        ),
        re.compile(
            r"\b(?P<value>[A-Za-z0-9][A-Za-z0-9 .&+\-]{1,80}?)\s+is\s+my\s+"
            r"(?P<key>fav(?:ou?rite)?|preferred)\s+(?P<object>[a-z0-9][a-z0-9 /&+\-]{1,80})",
            flags=re.IGNORECASE,
        ),
        re.compile(
            r"\bmy\s+(?P<key>timezone|time zone|city|location|name)\s+(?:is|=|:)\s+"
            r"(?P<value>[^.?!\n]{1,160})",
            flags=re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:call me|you can call me)\s+(?P<value>[^.?!\n]{1,80})",
            flags=re.IGNORECASE,
        ),
    )

    def __init__(self, memory_path: str | Path, enabled: bool = True) -> None:
        self._memory_path = Path(memory_path).expanduser()
        self._enabled = enabled
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._memory_path

    def prompt_context(self, limit: int = 8000) -> str:
        if not self._enabled:
            return ""
        content = self._read()
        if not content.strip():
            return ""
        return self._prioritized_prompt_context(content).strip()[:limit]

    def ensure_initialized(self) -> None:
        if not self._enabled:
            return
        with self._lock:
            existing = self._read()
            initialized = self._ensure_managed_sections(self._ensure_document(existing))
            initialized = self._backfill_profile_from_remembered_notes(initialized)
            if existing.strip() and initialized == existing:
                return
            self._write(initialized)

    def update_from_user_message(self, content: str, mode: str) -> None:
        if not self._enabled:
            return
        cleaned = self._clean_message(content)
        if not cleaned:
            return

        with self._lock:
            current = self._ensure_managed_sections(self._ensure_document(self._read()))
            current = self._backfill_profile_from_remembered_notes(current)
            remembered_note = self._extract_remembered_note(cleaned)
            if remembered_note:
                current = self._append_remembered_note(current, remembered_note)
            profile_facts = self._extract_profile_facts(cleaned, remembered_note=remembered_note)
            if profile_facts:
                current = self._update_user_profile(current, profile_facts)
            current = self._update_message_type_summary(current, mode=mode, message_preview=self._preview(cleaned))
            current = self._update_chat_analysis_summary(
                current,
                mode=mode,
                message=cleaned,
                message_preview=self._preview(cleaned),
            )
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
            "## Learned User Profile\n"
            f"{self._PROFILE_BLOCK_START}\n"
            "_No durable user facts learned yet._\n"
            f"{self._PROFILE_BLOCK_END}\n\n"
            "## Remembered Notes\n"
            "- Add durable user preferences, project facts, or operating instructions here.\n\n"
            "## Message Type Summary\n"
            f"{self._TYPE_BLOCK_START}\n"
            "_No messages recorded yet._\n"
            f"{self._TYPE_BLOCK_END}\n\n"
            "## Chat Analysis Summary\n"
            f"{self._ANALYSIS_BLOCK_START}\n"
            "_No chat patterns recorded yet._\n"
            f"{self._ANALYSIS_BLOCK_END}\n"
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

    def _ensure_managed_sections(self, content: str) -> str:
        updated = content
        if self._PROFILE_BLOCK_START not in updated or self._PROFILE_BLOCK_END not in updated:
            updated = self._insert_section_after_title(
                updated,
                "## Learned User Profile",
                f"{self._PROFILE_BLOCK_START}\n_No durable user facts learned yet._\n{self._PROFILE_BLOCK_END}",
            )
        if self._TYPE_BLOCK_START not in updated or self._TYPE_BLOCK_END not in updated:
            updated = (
                f"{updated.rstrip()}\n\n## Message Type Summary\n"
                f"{self._TYPE_BLOCK_START}\n_No messages recorded yet._\n{self._TYPE_BLOCK_END}\n"
            )
        if self._ANALYSIS_BLOCK_START not in updated or self._ANALYSIS_BLOCK_END not in updated:
            updated = (
                f"{updated.rstrip()}\n\n## Chat Analysis Summary\n"
                f"{self._ANALYSIS_BLOCK_START}\n_No chat patterns recorded yet._\n{self._ANALYSIS_BLOCK_END}\n"
            )
        return updated

    @staticmethod
    def _insert_section_after_title(content: str, heading: str, body: str) -> str:
        lines = content.rstrip().splitlines()
        if not lines:
            return f"# Agent Memory\n\n{heading}\n{body}\n"
        insert_at = 1 if lines[0].startswith("# ") else 0
        while insert_at < len(lines) and lines[insert_at].strip() == "":
            insert_at += 1
        next_lines = lines[:insert_at] + ["", heading, body, ""] + lines[insert_at:]
        return "\n".join(next_lines).rstrip() + "\n"

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

    @classmethod
    def _extract_profile_facts(cls, content: str, remembered_note: str | None = None) -> list[dict[str, str]]:
        candidates = [content]
        if remembered_note and remembered_note not in candidates:
            candidates.insert(0, remembered_note)

        facts: list[dict[str, str]] = []
        for candidate in candidates:
            facts.extend(cls._extract_direct_profile_facts(candidate))
            if remembered_note and candidate == remembered_note:
                facts.extend(cls._extract_explicit_preference_facts(candidate))

        deduped: dict[str, dict[str, str]] = {}
        for fact in facts:
            key = fact.get("key", "").strip()
            value = fact.get("value", "").strip()
            if not key or not value:
                continue
            deduped[key.lower()] = {**fact, "key": key, "value": value}
        return list(deduped.values())

    @classmethod
    def _extract_direct_profile_facts(cls, content: str) -> list[dict[str, str]]:
        facts: list[dict[str, str]] = []
        for pattern in cls._DIRECT_FACT_PATTERNS:
            for match in pattern.finditer(content):
                data = match.groupdict()
                raw_key = data.get("key") or ""
                raw_object = data.get("object") or ""
                value = cls._clean_fact_value(data.get("value") or "")
                key = cls._normalize_profile_key(raw_key=raw_key, raw_object=raw_object)
                if key and value and not cls._looks_like_question_value(value):
                    facts.append({"key": key, "value": value, "source": "learned from chat"})
        return facts

    @classmethod
    def _extract_explicit_preference_facts(cls, content: str) -> list[dict[str, str]]:
        lowered = content.lower()
        if not cls._contains_any(lowered, ("i like", "i love", "i prefer", "i support", "my ")):
            return []

        facts: list[dict[str, str]] = []
        preference_match = re.search(
            r"\bI\s+(?:like|love|prefer|support|follow)\s+(?P<value>[^.?!\n]{2,160})",
            content,
            flags=re.IGNORECASE,
        )
        if preference_match:
            value = cls._clean_fact_value(preference_match.group("value"))
            if value:
                facts.append({"key": "preference", "value": value, "source": "explicit memory"})
        return facts

    @classmethod
    def _normalize_profile_key(cls, raw_key: str, raw_object: str = "") -> str:
        key = cls._clean_profile_text(raw_key)
        obj = cls._clean_profile_text(raw_object)
        favorite_match = cls._FAVORITE_KEY_RE.match(key)
        if key in {"fav", "favorite", "favourite", "preferred"} and obj:
            return f"favorite {obj}"
        if favorite_match:
            favorite_object = cls._clean_profile_text(obj or favorite_match.group(1))
            return f"favorite {favorite_object}" if favorite_object else "favorite"
        if key == "time zone":
            return "timezone"
        if key in {"timezone", "city", "location", "name"}:
            return key
        return key

    @staticmethod
    def _clean_profile_text(content: str) -> str:
        text = re.sub(r"\s+", " ", content or "").strip(" .:-")
        return text[:120]

    @staticmethod
    def _clean_fact_value(content: str) -> str:
        value = re.sub(r"\s+", " ", content or "").strip(" .:-")
        value = re.sub(r"\b(?:please|thanks|thank you)$", "", value, flags=re.IGNORECASE).strip(" .:-")
        value = re.split(r"\s+(?:and also|also remember|but remember)\s+", value, maxsplit=1, flags=re.IGNORECASE)[0]
        return value[:220]

    @staticmethod
    def _looks_like_question_value(value: str) -> bool:
        return value.strip().lower() in {"what", "who", "where", "when", "why", "how", "which"}

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

    def _backfill_profile_from_remembered_notes(self, content: str) -> str:
        notes_section = self._extract_section_body(content, "## Remembered Notes")
        if not notes_section.strip():
            return content
        facts: list[dict[str, str]] = []
        for line in notes_section.splitlines():
            stripped = line.strip()
            if not stripped.startswith("- "):
                continue
            note = stripped[2:].strip()
            if self._is_placeholder_note(note):
                continue
            for fact in self._extract_profile_facts(note, remembered_note=note):
                facts.append({**fact, "source": "remembered note"})
        if not facts:
            return content
        return self._update_user_profile(content, facts)

    def _update_user_profile(self, content: str, facts: list[dict[str, str]]) -> str:
        profile = self._parse_profile_facts(content)
        now = now_ist().isoformat()
        for fact in facts:
            key = self._clean_profile_text(fact.get("key", ""))
            value = self._clean_fact_value(fact.get("value", ""))
            source = self._clean_profile_text(fact.get("source", "learned from chat"))
            if not key or not value:
                continue
            existing = profile.get(key.lower())
            if existing and existing.get("value", "").strip().lower() == value.lower():
                source = existing.get("source") or source
            profile[key.lower()] = {
                "key": key,
                "value": value,
                "source": source,
                "last": now,
            }

        block = self._render_profile_block(profile)
        pattern = re.compile(
            rf"{re.escape(self._PROFILE_BLOCK_START)}.*?{re.escape(self._PROFILE_BLOCK_END)}",
            flags=re.DOTALL,
        )
        if self._PROFILE_BLOCK_START in content and self._PROFILE_BLOCK_END in content:
            return pattern.sub(block, content, count=1)
        return self._insert_section_after_title(content, "## Learned User Profile", block)

    def _parse_profile_facts(self, content: str) -> dict[str, dict[str, str]]:
        profile: dict[str, dict[str, str]] = {}
        in_block = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped == self._PROFILE_BLOCK_START:
                in_block = True
                continue
            if stripped == self._PROFILE_BLOCK_END:
                break
            if not in_block:
                continue
            match = self._PROFILE_LINE_RE.match(stripped)
            if not match:
                continue
            key = self._clean_profile_text(match.group("key"))
            value = self._clean_fact_value(match.group("value"))
            if not key or not value:
                continue
            profile[key.lower()] = {
                "key": key,
                "value": value,
                "source": self._clean_profile_text(match.group("source") or "learned from chat"),
                "last": (match.group("last") or "").strip(),
            }
        return profile

    def _render_profile_block(self, profile: dict[str, dict[str, str]]) -> str:
        lines = [self._PROFILE_BLOCK_START]
        if not profile:
            lines.append("_No durable user facts learned yet._")
        for key in sorted(profile):
            item = profile[key]
            lines.append(
                f"- **{item['key']}**: {item['value']} "
                f"(source: {item.get('source') or 'learned from chat'}; "
                f"last: {item.get('last') or now_ist().isoformat()})"
            )
        lines.append(self._PROFILE_BLOCK_END)
        return "\n".join(lines)

    @staticmethod
    def _is_placeholder_note(note: str) -> bool:
        return note.strip().lower() == "add durable user preferences, project facts, or operating instructions here."

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

    def _update_chat_analysis_summary(self, document: str, mode: str, message: str, message_preview: str) -> str:
        stats = self._parse_analysis_stats(document)
        now = now_ist().isoformat()
        signals = self._analysis_signals(content=message, mode=mode)
        for category in self._infer_chat_categories(content=message, mode=mode):
            current = stats.get(category, {"count": 0, "last": now, "signals": [], "recent": []})
            recent = [item for item in current.get("recent", []) if item != message_preview]
            recent.insert(0, message_preview)
            merged_signals = list(dict.fromkeys([*signals, *current.get("signals", [])]))[:5]
            stats[category] = {
                "count": int(current.get("count", 0)) + 1,
                "last": now,
                "signals": merged_signals,
                "recent": recent[:3],
            }
        block = self._render_analysis_block(stats)
        if self._ANALYSIS_BLOCK_START in document and self._ANALYSIS_BLOCK_END in document:
            pattern = re.compile(
                rf"{re.escape(self._ANALYSIS_BLOCK_START)}.*?{re.escape(self._ANALYSIS_BLOCK_END)}",
                flags=re.DOTALL,
            )
            return pattern.sub(block, document, count=1)
        return f"{document.rstrip()}\n\n## Chat Analysis Summary\n{block}\n"

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

    def _parse_analysis_stats(self, content: str) -> dict[str, dict]:
        stats: dict[str, dict] = {}
        in_block = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped == self._ANALYSIS_BLOCK_START:
                in_block = True
                continue
            if stripped == self._ANALYSIS_BLOCK_END:
                break
            if not in_block:
                continue
            match = self._ANALYSIS_LINE_RE.match(stripped)
            if not match:
                continue
            signals = [
                item.strip()
                for item in match.group("signals").split(", ")
                if item.strip() and item.strip() != "none"
            ]
            recent = [item.strip() for item in match.group("recent").split(" | ") if item.strip()]
            stats[match.group("kind").strip().lower()] = {
                "count": int(match.group("count")),
                "last": match.group("last").strip(),
                "signals": signals[:5],
                "recent": recent[:3],
            }
        return stats

    def _render_analysis_block(self, stats: dict[str, dict]) -> str:
        lines = [self._ANALYSIS_BLOCK_START]
        for kind in sorted(stats):
            item = stats[kind]
            signals = ", ".join(str(value) for value in item.get("signals", []) if value) or "none"
            recent = " | ".join(str(value) for value in item.get("recent", []) if value) or "none"
            count = int(item.get("count", 0))
            noun = "message" if count == 1 else "messages"
            lines.append(
                f"- **{kind}**: {count} {noun}; last: {item.get('last')}; signals: {signals}; recent: {recent}"
            )
        lines.append(self._ANALYSIS_BLOCK_END)
        return "\n".join(lines)

    def _prioritized_prompt_context(self, content: str) -> str:
        sections = ["# Agent Memory"]
        for heading in (
            "## Learned User Profile",
            "## Remembered Notes",
            "## Chat Analysis Summary",
            "## Message Type Summary",
        ):
            section = self._extract_section(content, heading)
            if section:
                sections.append(section)
        if len(sections) == 1:
            return content
        return "\n\n".join(sections)

    def _extract_section(self, content: str, heading: str) -> str:
        body = self._extract_section_body(content, heading)
        if not body.strip():
            return ""
        if heading == "## Remembered Notes":
            body = "\n".join(
                line
                for line in body.splitlines()
                if not (line.strip().startswith("- ") and self._is_placeholder_note(line.strip()[2:]))
            ).strip()
            if not body:
                return ""
        return f"{heading}\n{body.strip()}"

    @staticmethod
    def _extract_section_body(content: str, heading: str) -> str:
        lines = content.splitlines()
        start = None
        for index, line in enumerate(lines):
            if line.strip() == heading:
                start = index + 1
                break
        if start is None:
            return ""
        end = len(lines)
        for index in range(start, len(lines)):
            if lines[index].startswith("## "):
                end = index
                break
        return "\n".join(lines[start:end]).strip()

    @classmethod
    def _infer_chat_categories(cls, content: str, mode: str) -> list[str]:
        lowered = content.lower()
        categories: list[str] = []
        if cls._extract_remembered_note(content) or cls._contains_any(
            lowered,
            ("agent memory", "memory file", "remember", "memorize", "add to memory", "save this", "store this"),
        ):
            categories.append("memory and preferences")
        if cls._contains_any(
            lowered,
            ("bug", "error", "failed", "failure", "fix", "debug", "traceback", "exception", "crash"),
        ):
            categories.append("debugging and fixes")
        if cls._contains_any(
            lowered,
            (
                "code",
                "frontend",
                "backend",
                "api",
                "test",
                "typescript",
                "python",
                "react",
                "docker",
                "commit",
                "push",
                "deploy",
            ),
        ):
            categories.append("software development")
        if cls._contains_any(
            lowered,
            ("search", "research", "latest", "compare", "source", "browse", "look up", "web"),
        ):
            categories.append("research")
        if cls._contains_any(lowered, ("image", "screenshot", "design", "ui", "ux", "visual", "photo", "mockup")):
            categories.append("visual and ux")
        if cls._contains_any(lowered, ("spreadsheet", "csv", "excel", "chart", "data", "analysis", "metric", "report")):
            categories.append("data analysis")
        if cls._contains_any(
            lowered,
            ("write", "draft", "summarize", "explain", "document", "readme", "copy", "content"),
        ):
            categories.append("writing and explanation")
        if cls._contains_any(lowered, ("plan", "roadmap", "task", "workflow", "strategy", "schedule", "reminder")):
            categories.append("planning")
        if mode == "tool_builder":
            categories.append("tool building")
        if mode in {"operator", "pi_operator"}:
            categories.append("operator tasks")
        return list(dict.fromkeys(categories or ["general conversation"]))[:4]

    @classmethod
    def _analysis_signals(cls, content: str, mode: str) -> list[str]:
        lowered = content.lower()
        signals: list[str] = [f"mode:{cls._normalize_mode(mode)}"]
        for label, terms in (
            ("explicit-memory", ("remember", "memorize", "add to memory", "save this", "store this")),
            ("implementation", ("implement", "add", "fix", "update", "change", "commit", "push")),
            ("investigation", ("why", "how", "go through", "inspect", "analyse", "analyze", "review")),
            ("preference", ("i prefer", "i like", "always", "never", "my timezone", "my preferred")),
            ("durability", ("survive restart", "persistent", "durable", "restart")),
        ):
            if cls._contains_any(lowered, terms):
                signals.append(label)
        return signals[:5]

    @staticmethod
    def _contains_any(content: str, needles: tuple[str, ...]) -> bool:
        return any(needle in content for needle in needles)

    @staticmethod
    def _normalize_mode(mode: str) -> str:
        cleaned = (mode or "general").strip().lower()
        if cleaned == "pi_operator":
            return "operator"
        if cleaned in {"general", "operator", "tool_builder"}:
            return cleaned
        return "general"
