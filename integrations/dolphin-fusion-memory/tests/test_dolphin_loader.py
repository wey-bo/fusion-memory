from __future__ import annotations

from pathlib import Path

import pytest

from psi_agent.session.tools import load_tools_from_workspace


@pytest.mark.anyio
async def test_dolphin_loader_discovers_only_public_tools() -> None:
    workspace = Path(__file__).resolve().parents[1] / "workspace"
    tools, callables = await load_tools_from_workspace(workspace / "tools")

    assert set(tools) == {"memory_add", "memory_search", "memory_answer_context"}
    assert set(callables) == set(tools)
    assert "source" not in tools["memory_add"].parameters.get("required", [])
    assert "source" in tools["memory_add"].parameters["properties"]
    assert "_config" not in tools
    assert "_client" not in tools
