from __future__ import annotations

import importlib
import socket
import sys
from pathlib import Path

import pytest
from aiohttp import web


ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "workspace" / "tools"
SYSTEMS_DIR = ROOT / "workspace" / "systems"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
if str(SYSTEMS_DIR) not in sys.path:
    sys.path.insert(0, str(SYSTEMS_DIR))

from _config import MemoryConfig

memory_add = importlib.import_module("memory_add")
memory_search = importlib.import_module("memory_search")
memory_answer_context = importlib.import_module("memory_answer_context")
system = importlib.import_module("system")


def _cfg(base_url: str, session_id: str | None = None) -> MemoryConfig:
    return MemoryConfig(
        base_url=base_url,
        timeout_seconds=2.0,
        workspace_id="dolphin",
        user_id="webo",
        agent_id="dolphin",
        session_id=session_id,
    )


@pytest.mark.anyio
async def test_memory_add_posts_expected_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(await request.json())
        return web.json_response({"ok": True, "saved": True})

    app = web.Application()
    app.router.add_post("/add", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()
    monkeypatch.setattr(memory_add, "CONFIG", _cfg(f"http://127.0.0.1:{port}"))

    try:
        result = await memory_add.memory_add("remember this", source="user-preference")
        assert "\"saved\": true" in result.lower()
        assert seen[0]["metadata"]["source"] == "user-preference"
        assert seen[0]["input"]["content"] == "remember this"
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_memory_search_clamps_limit_and_formats_pack(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(await request.json())
        return web.json_response(
            {
                "facts": [{"text": "alpha"}],
                "events": [{"summary": "beta"}],
                "source_spans": [{"content": "gamma"}],
            }
        )

    app = web.Application()
    app.router.add_post("/search", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()
    monkeypatch.setattr(memory_search, "CONFIG", _cfg(f"http://127.0.0.1:{port}"))

    try:
        result = await memory_search.memory_search("alpha", limit=999)
        assert seen[0]["options"]["limit"] == 32
        assert seen[0]["options"]["allow_cross_session"] is True
        assert "alpha" in result
        assert "beta" in result
        assert "gamma" in result
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_memory_answer_context_uses_fixed_budget_and_cross_session_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(await request.json())
        return web.json_response({"current_views": [{"text": "pack"}]})

    app = web.Application()
    app.router.add_post("/answer-context", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()
    monkeypatch.setattr(memory_answer_context, "CONFIG", _cfg(f"http://127.0.0.1:{port}"))

    try:
        result = await memory_answer_context.memory_answer_context("alpha")
        assert seen[0]["budget"]["limit"] == 8
        assert seen[0]["budget"]["allow_cross_session"] is True
        assert "pack" in result
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_tools_return_unavailable_message_on_http_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.json_response({"message": "boom"}, status=500)

    app = web.Application()
    app.router.add_post("/search", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    site = web.SockSite(runner, sock)
    await site.start()
    monkeypatch.setattr(memory_search, "CONFIG", _cfg(f"http://127.0.0.1:{port}"))

    try:
        result = await memory_search.memory_search("alpha")
        assert '"ok": false' in result
        assert "Fusion Memory is not available" in result
    finally:
        await runner.cleanup()


@pytest.mark.anyio
async def test_system_prompt_mentions_three_tools() -> None:
    prompt = await system.system_prompt_builder()
    assert "memory_add" in prompt
    assert "memory_search" in prompt
    assert "memory_answer_context" in prompt
