from app.services.memory_service import MemoryService


def test_memory_service_remembers_explicit_note_and_tracks_message_type(tmp_path) -> None:
    memory_path = tmp_path / "memory.md"
    service = MemoryService(memory_path)

    service.update_from_user_message("Please remember that I prefer concise final answers.", mode="general")
    service.update_from_user_message("Can you improve the tool builder?", mode="tool_builder")

    content = memory_path.read_text(encoding="utf-8")

    assert "# Agent Memory" in content
    assert "- I prefer concise final answers" in content
    assert "- **general**: 1 message;" in content
    assert "- **tool_builder**: 1 message;" in content
    assert "Can you improve the tool builder?" in content
    assert "## Chat Analysis Summary" in content
    assert "- **memory and preferences**: 1 message;" in content
    assert "- **tool building**: 1 message;" in content


def test_memory_service_deduplicates_remembered_notes(tmp_path) -> None:
    memory_path = tmp_path / "memory.md"
    service = MemoryService(memory_path)

    service.update_from_user_message("remember that my timezone is Asia/Kolkata", mode="general")
    service.update_from_user_message("remember that my timezone is Asia/Kolkata", mode="general")

    content = memory_path.read_text(encoding="utf-8")

    assert content.count("- my timezone is Asia/Kolkata") == 1
    assert "- **general**: 2 messages;" in content


def test_memory_service_stores_arbitrary_requested_fact_without_content_gate(tmp_path) -> None:
    memory_path = tmp_path / "memory.md"
    service = MemoryService(memory_path)

    service.update_from_user_message("store RCB is a choker team", mode="general")

    content = memory_path.read_text(encoding="utf-8")

    assert "- RCB is a choker team" in content


def test_memory_service_accepts_broad_memory_request_phrasings(tmp_path) -> None:
    memory_path = tmp_path / "memory.md"
    service = MemoryService(memory_path)

    examples = [
        "remember RCB is a choker team",
        "memorize that I like compact answers",
        "note down that deploys should be verified",
        "keep this in memory: use gpt-5.5 when available",
        "save this fact: my preferred port range starts at 3001",
        "store this fact that staging uses Mumbai region",
    ]
    for example in examples:
        service.update_from_user_message(example, mode="general")

    content = memory_path.read_text(encoding="utf-8")

    assert "- RCB is a choker team" in content
    assert "- I like compact answers" in content
    assert "- deploys should be verified" in content
    assert "- use gpt-5.5 when available" in content
    assert "- my preferred port range starts at 3001" in content
    assert "- staging uses Mumbai region" in content


def test_memory_service_prompt_context_returns_existing_markdown(tmp_path) -> None:
    memory_path = tmp_path / "memory.md"
    memory_path.write_text("# Agent Memory\n\n## Remembered Notes\n- Use compact summaries.\n", encoding="utf-8")
    service = MemoryService(memory_path)

    assert "Use compact summaries" in service.prompt_context()


def test_memory_service_analyzes_chat_categories_and_signals(tmp_path) -> None:
    memory_path = tmp_path / "memory.md"
    service = MemoryService(memory_path)

    service.update_from_user_message(
        "Can you inspect the React frontend bug, fix the test, commit and push?",
        mode="general",
    )
    service.update_from_user_message(
        "Can you go through the agent memory file and make sure it survives restart?",
        mode="general",
    )

    content = memory_path.read_text(encoding="utf-8")

    assert "- **debugging and fixes**: 1 message;" in content
    assert "- **software development**: 1 message;" in content
    assert "- **memory and preferences**: 1 message;" in content
    assert "signals: mode:general, implementation, investigation" in content
    assert "durability" in content


def test_memory_service_initializes_durable_memory_file(tmp_path) -> None:
    memory_path = tmp_path / "persisted" / "memory.md"
    service = MemoryService(memory_path)

    service.ensure_initialized()

    content = memory_path.read_text(encoding="utf-8")
    assert content.startswith("# Agent Memory")
    assert "## Remembered Notes" in content
    assert "## Chat Analysis Summary" in content
