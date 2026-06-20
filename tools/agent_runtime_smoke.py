from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any
from urllib import error, request

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fusion_memory.agent_installer import HERMES_PROVIDER, OPENCLAW_PLUGIN, VALID_TARGETS, _action_for  # noqa: E402


DEFAULT_TIMEOUT_SECONDS = 5
DEFAULT_SCOPE = {"workspace_id": "agent-runtime-smoke", "user_id": "smoke-user", "agent_id": "fusion_memory"}


def run_smoke(target: str, *, memory_url: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    report = _base_report(target)
    if target not in VALID_TARGETS:
        report["message"] = "Unknown Agent target. Choose one of: openclaw, hermes, fusion-agent."
        return report

    host_available, host_message = _host_available(target)
    plugin_available, plugin_message = _plugin_available(target)
    report["host_available"] = host_available
    report["plugin_available"] = plugin_available

    if not host_available:
        report["message"] = host_message
        return report
    if not plugin_available:
        report["message"] = plugin_message
        return report

    try:
        _post_json(
            memory_url,
            "/add",
            {
                "input": {
                    "role": "user",
                    "content": f"Fusion Memory runtime smoke for {target} can write memories.",
                },
                "scope": DEFAULT_SCOPE,
                "metadata": {"source": "agent_runtime_smoke", "target": target},
            },
            timeout=timeout,
        )
        report["write_smoke"] = True

        search = _post_json(
            memory_url,
            "/search",
            {
                "query": f"Which target did the Fusion Memory runtime smoke write for {target}?",
                "scope": DEFAULT_SCOPE,
                "options": {"limit": 3},
            },
            timeout=timeout,
        )
        report["retrieve_smoke"] = bool(search.get("candidates"))
        report["ok"] = bool(report["write_smoke"] and report["retrieve_smoke"])
        report["message"] = (
            f"{_display_name(target)} runtime smoke completed."
            if report["ok"]
            else f"{_display_name(target)} reached Fusion Memory, but retrieval returned no candidates. Run fusion-memory doctor."
        )
        return report
    except (OSError, TimeoutError, error.URLError, ValueError, json.JSONDecodeError):
        report["message"] = (
            f"{_display_name(target)} adapter is present, but Fusion Memory did not answer the smoke request. "
            "Start the service and run fusion-memory doctor."
        )
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a beginner-safe Fusion Memory Agent runtime smoke check")
    parser.add_argument("--target", required=True, choices=VALID_TARGETS)
    parser.add_argument("--memory-url", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)

    report = run_smoke(args.target, memory_url=args.memory_url, timeout=args.timeout)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if report.get("ok") else 1


def _base_report(target: str) -> dict[str, Any]:
    return {
        "target": target,
        "host_available": False,
        "plugin_available": False,
        "write_smoke": False,
        "retrieve_smoke": False,
        "ok": False,
        "message": "",
    }


def _host_available(target: str) -> tuple[bool, str]:
    if target == "openclaw":
        if shutil.which("openclaw"):
            return True, "OpenClaw host is available."
        return False, "OpenClaw was not found on PATH. Install OpenClaw, then run fusion-memory install-agent --target openclaw."
    if target == "hermes":
        if shutil.which("hermes"):
            return True, "Hermes host is available."
        return False, "Hermes was not found on PATH. Install Hermes, then run fusion-memory install-agent --target hermes."

    root = Path(_action_for("fusion-agent")["path"])
    if root.exists():
        return True, "Fusion-Agent checkout is available."
    return False, f"Fusion-Agent checkout was not found at {root}. Set FUSION_AGENT_ROOT or clone Fusion-Agent before running the smoke."


def _plugin_available(target: str) -> tuple[bool, str]:
    if target == "openclaw":
        ok = (OPENCLAW_PLUGIN / "openclaw.plugin.json").exists()
        return (
            ok,
            "OpenClaw Fusion Memory plugin files are present."
            if ok
            else "OpenClaw Fusion Memory plugin files are missing. Run fusion-memory install-agent --target openclaw.",
        )
    if target == "hermes":
        hermes_plugin = Path(os.getenv("HERMES_HOME", Path.home() / ".hermes")).expanduser() / "plugins" / "fusion_memory"
        ok = (hermes_plugin / "__init__.py").exists() or (HERMES_PROVIDER / "__init__.py").exists()
        return (
            ok,
            "Hermes Fusion Memory provider files are present."
            if ok
            else "Hermes Fusion Memory provider is missing. Run fusion-memory install-agent --target hermes.",
        )

    root = Path(_action_for("fusion-agent")["path"])
    ok = root.exists()
    return (
        ok,
        "Fusion-Agent memory integration can be checked in the Fusion-Agent checkout."
        if ok
        else "Fusion-Agent checkout is missing. Set FUSION_AGENT_ROOT or clone Fusion-Agent.",
    )


def _post_json(memory_url: str, path: str, payload: dict[str, Any], *, timeout: int) -> dict[str, Any]:
    base = memory_url.rstrip("/")
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        f"{base}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as response:
        data = response.read().decode("utf-8")
    decoded = json.loads(data)
    if not isinstance(decoded, dict):
        raise ValueError("Fusion Memory response was not a JSON object")
    return decoded


def _display_name(target: str) -> str:
    return {
        "openclaw": "OpenClaw",
        "hermes": "Hermes",
        "fusion-agent": "Fusion-Agent",
    }[target]


if __name__ == "__main__":
    raise SystemExit(main())
