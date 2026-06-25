from __future__ import annotations

import json
from pathlib import Path
import sys

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from _client import post_json
from _config import CONFIG

UNAVAILABLE_MESSAGE = (
    '{"ok": false, "message": "Fusion Memory is not available. Continue without memory, then run fusion-memory doctor."}'
)


async def memory_add(content: str, source: str | None = None) -> str:
    """Store durable information in Fusion Memory.

    Args:
        content: The preference, fact, or decision to store.
        source: Optional source label for metadata.

    Returns:
        A JSON string describing the result of the add request.
    """
    try:
        data = await post_json(
            CONFIG.base_url,
            "/add",
            {
                "input": {"role": "user", "content": content},
                "scope": CONFIG.scope,
                "metadata": {"source": source or "dolphin-tool"},
            },
            CONFIG.timeout_seconds,
        )
        return json.dumps({"ok": True, "saved": True, "result": data}, ensure_ascii=False)
    except Exception:
        return UNAVAILABLE_MESSAGE
