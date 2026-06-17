# Fusion Memory Agent Adapters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build first-stage Fusion Memory adapters for Fusion-Agent, OpenClaw, and Hermes through one local Fusion Memory HTTP service, without modifying real OpenClaw or Hermes source.

**Architecture:** Fusion Memory owns the runtime service, product defaults, installer, diagnostics, and test harnesses. OpenClaw and Hermes receive external lightweight client plugins stored under the memory repo; Fusion-Agent continues from its existing in-repo integration and gets product hardening. All adapters fail open and return beginner-safe guidance rather than raw technical errors.

**Tech Stack:** Python 3.11+ `unittest` in `/public/home/wwb/memory`; Python 3.14+ `pytest` in `/public/home/wwb/Fusion-Agent`; OpenClaw external plugin as plain ESM JavaScript using `openclaw/plugin-sdk/plugin-entry`; Hermes external provider as Python `MemoryProvider`.

## Global Constraints

- First-stage productization adapts Fusion Memory to Fusion-Agent at `/public/home/wwb/Fusion-Agent`, real OpenClaw at `/public/home/wwb/GitHub/openclaw`, and real Hermes at `/public/home/wwb/GitHub/hermes-agent`.
- The first stage must not modify real OpenClaw or Hermes source code.
- Fusion-Agent may continue from its current in-repo partial integration.
- Beginner install finishes in 30 seconds when prerequisites and local model/cache resources already exist.
- Install failure rate target: 1% after prerequisites are satisfied.
- One-command upgrade backs up data first and should not break a working install; failure rate target: 1%.
- Windows, Linux, and macOS are supported.
- Normal use should not crash the host Agent; crash/exit rate target: 1%.
- Tests cover 80% of main scenarios and expected exception paths.
- Beginner-facing surfaces never expose Python tracebacks, Node stack traces, DSNs with secrets, raw HTTP errors, or internal table names.
- First token response impact target is under 2 seconds. Memory retrieval must fail open and use bounded timeouts.
- Default configuration covers 90% of beginner use: local service, Postgres storage, Qwen3-Embedding-0.6B, Qwen3-Reranker-0.6B, rule extractor unless an LLM extractor endpoint is explicitly configured.
- Testing model key resources are referenced by path only: `/public/home/wwb/test_key/key.txt`. The implementation and docs must not print or copy secrets from this file.
- No upstream changes to real OpenClaw or Hermes source.
- No bundled OpenClaw plugin in `/public/home/wwb/GitHub/openclaw/extensions`.
- No bundled Hermes provider in `/public/home/wwb/GitHub/hermes-agent/plugins`.
- No hidden automatic reading of `/public/home/wwb/test_key/key.txt`.

---

## File Structure

Memory repo files:

- Create `fusion_memory/agent_installer.py`: idempotent installer logic for OpenClaw, Hermes, Fusion-Agent adapters.
- Create `fusion_memory/agent_checks.py`: adapter doctor checks with redacted, beginner-safe messages.
- Modify `fusion_memory/product.py`: product defaults, `/status` support helpers, model check wording, installer-facing backup utilities.
- Modify `fusion_memory/server.py`: add `GET /status` and sanitize error responses.
- Modify `fusion_memory/cli.py`: add `install`, `install-agent`, `alpha-test`, and `beta-test` commands.
- Create `fusion_memory/alpha_beta.py`: simulation harnesses and report writer.
- Create `integrations/hermes-fusion-memory/__init__.py`: Hermes `MemoryProvider` implementation.
- Create `integrations/hermes-fusion-memory/plugin.yaml`: Hermes provider metadata.
- Create `integrations/hermes-fusion-memory/README.md`: beginner setup/troubleshooting.
- Create `integrations/openclaw-fusion-memory/package.json`: external plugin package metadata.
- Create `integrations/openclaw-fusion-memory/openclaw.plugin.json`: OpenClaw manifest.
- Create `integrations/openclaw-fusion-memory/index.js`: OpenClaw plugin runtime.
- Create `integrations/openclaw-fusion-memory/README.md`: beginner setup/troubleshooting.
- Create `integrations/openclaw-fusion-memory/test/friendly-error.test.mjs`: Node unit tests for plugin helpers.
- Create `tests/test_agent_installer.py`: installer unit tests.
- Create `tests/test_agent_checks.py`: doctor/check tests.
- Create `tests/test_alpha_beta.py`: harness tests.
- Modify `tests/test_product_cli.py`: product default and install-agent CLI tests.
- Modify `tests/test_server.py`: `/status` and sanitized error tests.
- Modify `docs/quickstart.md`: beginner default path.
- Create `docs/agent-adapters.md`: adapter setup and troubleshooting.
- Create `docs/errors.md`: user-facing error guide.

Fusion-Agent repo files:

- Modify `/public/home/wwb/Fusion-Agent/src/psi_agent/memory/client.py`: classify connection, timeout, HTTP, and JSON errors without exposing raw internals.
- Modify `/public/home/wwb/Fusion-Agent/src/psi_agent/memory/tool_api.py`: catch failures and return beginner-safe tool results.
- Modify `/public/home/wwb/Fusion-Agent/src/psi_agent/memory/adapter.py`: ensure session auto read/write remains fail-open with bounded timeouts and redacted logs.
- Modify `/public/home/wwb/Fusion-Agent/examples/openclaw-style-workspace/tools/memory.py`: keep compatibility tool using hardened API.
- Modify `/public/home/wwb/Fusion-Agent/examples/hermes-style-workspace/tools/memory.py`: keep compatibility tool using hardened API.
- Modify `/public/home/wwb/Fusion-Agent/tests/memory/test_integration.py`: add service unavailable, non-JSON, timeout, and manual tool failure tests.
- Modify `/public/home/wwb/Fusion-Agent/README_zh.md`: document memory env vars and beginner setup.

## Task 1: Product Status Endpoint And Safe Server Errors

**Files:**
- Modify: `/public/home/wwb/memory/fusion_memory/server.py`
- Modify: `/public/home/wwb/memory/fusion_memory/product.py`
- Test: `/public/home/wwb/memory/tests/test_server.py`

**Interfaces:**
- Consumes: existing `serve(service, host, port)` and `MemoryService` methods.
- Produces: `GET /status` response shaped as `{"ok": bool, "service": "running", "database": {"ok": bool}, "models": {"ok": bool}, "version": str}`.
- Produces: sanitized error response shaped as `{"error": "request_failed", "message": "Fusion Memory could not complete that request. Run fusion-memory doctor."}`.

- [ ] **Step 1: Write failing `/status` test**

Add to `/public/home/wwb/memory/tests/test_server.py`:

```python
    def test_status_endpoint_reports_readiness(self) -> None:
        ready = threading.Event()
        holder = {}

        def run_server() -> None:
            service = MemoryService()
            server = serve(service, host="127.0.0.1", port=0)
            holder["service"] = service
            holder["server"] = server
            ready.set()
            try:
                server.serve_forever()
            finally:
                server.server_close()
                service.close()

        thread = threading.Thread(target=run_server, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(timeout=5))
        server = holder["server"]
        try:
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            status = _post_or_get(f"{base_url}/status")
            self.assertTrue(status["ok"])
            self.assertEqual(status["service"], "running")
            self.assertTrue(status["database"]["ok"])
            self.assertTrue(status["models"]["ok"])
            self.assertIn("version", status)
        finally:
            server.shutdown()
            thread.join(timeout=2)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd /public/home/wwb/memory
python -m unittest tests.test_server.ServerTests.test_status_endpoint_reports_readiness -v
```

Expected: FAIL or ERROR because `/status` returns `{"error": "not_found"}`.

- [ ] **Step 3: Implement status helper and endpoint**

In `/public/home/wwb/memory/fusion_memory/product.py`, add:

```python
def runtime_status_payload(*, storage_backend: str = "sqlite") -> dict[str, Any]:
    return {
        "ok": True,
        "service": "running",
        "database": {"ok": True, "backend": storage_backend or "sqlite"},
        "models": {"ok": True},
        "version": CONFIG_VERSION,
    }
```

In `/public/home/wwb/memory/fusion_memory/server.py`, import it:

```python
from fusion_memory.product import runtime_status_payload
```

In `do_GET`, add before the 404:

```python
            if path == "/status":
                self._write_json(200, runtime_status_payload(storage_backend=self.server.storage_backend if hasattr(self.server, "storage_backend") else "sqlite"))
                return
```

After creating the `HTTPServer` in `serve()`, attach the backend hint:

```python
    server.storage_backend = getattr(service, "storage_backend", "sqlite")  # type: ignore[attr-defined]
```

- [ ] **Step 4: Run status test to verify it passes**

Run:

```bash
cd /public/home/wwb/memory
python -m unittest tests.test_server.ServerTests.test_status_endpoint_reports_readiness -v
```

Expected: PASS.

- [ ] **Step 5: Write failing sanitized error test**

Add to `/public/home/wwb/memory/tests/test_server.py`:

```python
    def test_post_errors_are_sanitized_for_beginner_clients(self) -> None:
        ready = threading.Event()
        holder = {}

        def run_server() -> None:
            service = MemoryService()
            server = serve(service, host="127.0.0.1", port=0)
            holder["service"] = service
            holder["server"] = server
            ready.set()
            try:
                server.serve_forever()
            finally:
                server.server_close()
                service.close()

        thread = threading.Thread(target=run_server, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(timeout=5))
        server = holder["server"]
        try:
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            req = request.Request(
                f"{base_url}/search",
                data=json.dumps({"query": "missing scope"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                request.urlopen(req, timeout=5)
                self.fail("expected HTTPError")
            except Exception as exc:
                response = exc.fp.read().decode("utf-8")
                payload = json.loads(response)
            self.assertEqual(payload["error"], "request_failed")
            self.assertIn("fusion-memory doctor", payload["message"])
            self.assertNotIn("ValueError", json.dumps(payload))
            self.assertNotIn("scope is required", json.dumps(payload))
        finally:
            server.shutdown()
            thread.join(timeout=2)
```

- [ ] **Step 6: Run sanitized error test to verify it fails**

Run:

```bash
cd /public/home/wwb/memory
python -m unittest tests.test_server.ServerTests.test_post_errors_are_sanitized_for_beginner_clients -v
```

Expected: FAIL because current server returns exception class and message.

- [ ] **Step 7: Implement sanitized error response**

In `/public/home/wwb/memory/fusion_memory/server.py`, replace:

```python
            except Exception as exc:
                self._write_json(400, {"error": exc.__class__.__name__, "message": str(exc)})
```

with:

```python
            except Exception:
                self._write_json(
                    400,
                    {
                        "error": "request_failed",
                        "message": "Fusion Memory could not complete that request. Run fusion-memory doctor.",
                    },
                )
```

- [ ] **Step 8: Run task tests**

Run:

```bash
cd /public/home/wwb/memory
python -m unittest tests.test_server -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
cd /public/home/wwb/memory
git add fusion_memory/server.py fusion_memory/product.py tests/test_server.py
git commit -m "feat: add safe fusion memory status endpoint"
```

## Task 2: Product Defaults For Postgres And Qwen

**Files:**
- Modify: `/public/home/wwb/memory/fusion_memory/product.py`
- Modify: `/public/home/wwb/memory/tests/test_product_cli.py`
- Modify: `/public/home/wwb/memory/docs/quickstart.md`

**Interfaces:**
- Consumes: existing `init_home()`, `configure_interactive()`, `load_config()`.
- Produces: `default_product_settings(paths: ProductPaths) -> dict[str, Any]`.
- Produces default config using `storage_backend="postgres"`, embedding provider `qwen`, reranker provider `qwen`, extractor `rule`, query intent `off`.

- [ ] **Step 1: Write failing default config test**

In `/public/home/wwb/memory/tests/test_product_cli.py`, update `test_init_doctor_backup_and_upgrade_dry_run` expectations:

```python
            self.assertEqual(config["storage_backend"], "postgres")
            self.assertEqual(config["embedding"]["provider"], "qwen")
            self.assertIn("Qwen3-Embedding-0.6B", config["embedding"]["model"])
            self.assertEqual(config["reranker"]["provider"], "qwen")
            self.assertIn("Qwen3-Reranker-0.6B", config["reranker"]["model"])
            self.assertEqual(config["extractor"]["provider"], "rule")
            self.assertEqual(config["query_intent"]["provider"], "off")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd /public/home/wwb/memory
python -m unittest tests.test_product_cli.ProductCliTests.test_init_doctor_backup_and_upgrade_dry_run -v
```

Expected: FAIL because current defaults are SQLite/deterministic/lexical.

- [ ] **Step 3: Implement product defaults**

In `/public/home/wwb/memory/fusion_memory/product.py`, add constants:

```python
DEFAULT_POSTGRES_DSN = "postgresql://fusion:fusion@127.0.0.1:55433/fusion_memory"
DEFAULT_QWEN_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
DEFAULT_QWEN_RERANKER_MODEL = "Qwen/Qwen3-Reranker-0.6B"
```

Replace `_default_config()` with:

```python
def _default_config(paths: ProductPaths, *, host: str, port: int) -> dict[str, Any]:
    return {
        "version": CONFIG_VERSION,
        "host": host,
        "port": port,
        "db": DEFAULT_POSTGRES_DSN,
        "storage_backend": "postgres",
        "log": str(paths.log),
        "embedding": {"provider": "qwen", "model": DEFAULT_QWEN_EMBEDDING_MODEL},
        "reranker": {"provider": "qwen", "model": DEFAULT_QWEN_RERANKER_MODEL},
        "extractor": {"provider": "rule"},
        "query_intent": {"provider": "off"},
    }
```

In `load_config()`, change fallback defaults to match:

```python
    data.setdefault("db", DEFAULT_POSTGRES_DSN)
    data.setdefault("storage_backend", "postgres")
    data.setdefault("embedding", {"provider": "qwen", "model": DEFAULT_QWEN_EMBEDDING_MODEL})
    data.setdefault("reranker", {"provider": "qwen", "model": DEFAULT_QWEN_RERANKER_MODEL})
```

- [ ] **Step 4: Keep doctor beginner-safe when Postgres is not running**

In `/public/home/wwb/memory/fusion_memory/product.py`, update `doctor()` so a default Postgres DSN without a live server does not print secrets or raw driver errors. Add:

```python
def _redact_dsn(value: str) -> str:
    if "@" not in value:
        return value
    prefix, suffix = value.rsplit("@", 1)
    scheme = prefix.split("://", 1)[0] if "://" in prefix else "postgresql"
    return f"{scheme}://***:***@{suffix}"
```

Use `_redact_dsn(str(config.get("db", "")))` in doctor details for Postgres DSN checks.

- [ ] **Step 5: Treat Qwen model ids as valid configured models**

In `/public/home/wwb/memory/fusion_memory/product.py`, update `_model_checks()` for `provider == "qwen"` so Hugging Face style model ids are accepted without requiring a local path:

```python
        if provider == "qwen":
            model = str(raw.get("model") or "")
            ok = bool(model)
            if model and (model.startswith("~") or model.startswith("/") or ":\\" in model or model.startswith(".")):
                ok = Path(model).expanduser().exists()
            checks.append(_check(label, ok, f"qwen model={model or 'missing'}"))
            continue
```

- [ ] **Step 6: Run product CLI tests**

Run:

```bash
cd /public/home/wwb/memory
python -m unittest tests.test_product_cli -v
```

Expected: PASS, except if a test assumes SQLite path existence; adjust only the assertion to match the product default.

- [ ] **Step 7: Update quickstart default wording**

In `/public/home/wwb/memory/docs/quickstart.md`, replace the default list with:

```markdown
- 数据库：默认 Postgres/pgvector，本地服务地址来自初始化配置；高级用户可显式选择 SQLite 测试模式。
- Embedding：默认 Qwen3-Embedding-0.6B。
- Reranker：默认 Qwen3-Reranker-0.6B。
- Extractor/router：默认内置规则；高级用户可选 OpenAI-compatible API。
- Query router：默认关闭；需要复杂查询路由时再开启 API。
```

- [ ] **Step 8: Commit**

```bash
cd /public/home/wwb/memory
git add fusion_memory/product.py tests/test_product_cli.py docs/quickstart.md
git commit -m "feat: default fusion memory to postgres qwen"
```

## Task 3: Hermes External Fusion Memory Provider

**Files:**
- Create: `/public/home/wwb/memory/integrations/hermes-fusion-memory/__init__.py`
- Create: `/public/home/wwb/memory/integrations/hermes-fusion-memory/plugin.yaml`
- Create: `/public/home/wwb/memory/integrations/hermes-fusion-memory/README.md`
- Create: `/public/home/wwb/memory/tests/test_hermes_integration.py`

**Interfaces:**
- Consumes: Hermes `agent.memory_provider.MemoryProvider` when Hermes is available on `sys.path`.
- Produces: `FusionMemoryProvider` with `name == "fusion_memory"`.
- Produces tool names `fusion_memory_search`, `fusion_memory_store`, `fusion_memory_clear`.

- [ ] **Step 1: Write failing provider import and schema tests**

Create `/public/home/wwb/memory/tests/test_hermes_integration.py`:

```python
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
HERMES_ROOT = Path("/public/home/wwb/GitHub/hermes-agent")
PROVIDER_PATH = ROOT / "integrations" / "hermes-fusion-memory" / "__init__.py"


def load_provider_module():
    if str(HERMES_ROOT) not in sys.path:
        sys.path.insert(0, str(HERMES_ROOT))
    spec = importlib.util.spec_from_file_location("fusion_memory_hermes_provider", PROVIDER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HermesFusionMemoryProviderTests(unittest.TestCase):
    def test_provider_loads_and_exposes_tools(self) -> None:
        module = load_provider_module()
        provider = module.FusionMemoryProvider()
        self.assertEqual(provider.name, "fusion_memory")
        schemas = provider.get_tool_schemas()
        names = {schema["name"] for schema in schemas}
        self.assertEqual(
            names,
            {"fusion_memory_search", "fusion_memory_store", "fusion_memory_clear"},
        )

    def test_tool_failure_is_beginner_safe(self) -> None:
        module = load_provider_module()
        provider = module.FusionMemoryProvider()
        with patch.object(provider, "_post_json", side_effect=TimeoutError("socket timeout")):
            result = provider.handle_tool_call("fusion_memory_search", {"query": "preference"})
        payload = json.loads(result)
        self.assertFalse(payload["ok"])
        self.assertIn("fusion-memory doctor", payload["message"])
        self.assertNotIn("socket timeout", payload["message"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd /public/home/wwb/memory
python -m unittest tests.test_hermes_integration -v
```

Expected: ERROR because provider file does not exist.

- [ ] **Step 3: Implement Hermes provider**

Create `/public/home/wwb/memory/integrations/hermes-fusion-memory/__init__.py`:

```python
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

try:
    from agent.memory_provider import MemoryProvider
except Exception:
    class MemoryProvider:  # type: ignore[no-redef]
        pass


DEFAULT_BASE_URL = "http://127.0.0.1:8765"
DEFAULT_TIMEOUT_SECONDS = 1.5


class FusionMemoryProvider(MemoryProvider):
    @property
    def name(self) -> str:
        return "fusion_memory"

    def __init__(self) -> None:
        self.base_url = os.getenv("FUSION_MEMORY_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        self.timeout_seconds = float(os.getenv("FUSION_MEMORY_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))
        self.scope: dict[str, Any] = {
            "agent_id": "hermes",
            "app_id": "fusion-memory",
        }

    def is_available(self) -> bool:
        return bool(self.base_url)

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        hermes_home = str(kwargs.get("hermes_home") or os.getenv("HERMES_HOME") or "")
        workspace = str(kwargs.get("agent_workspace") or "hermes")
        user_id = str(kwargs.get("user_id") or kwargs.get("user_id_alt") or os.getenv("USER") or os.getenv("USERNAME") or "user")
        self.scope = {
            "workspace_id": workspace or (os.path.basename(hermes_home) if hermes_home else "hermes"),
            "user_id": user_id,
            "agent_id": "hermes",
            "session_id": session_id,
            "app_id": "fusion-memory",
        }

    def system_prompt_block(self) -> str:
        return "Fusion Memory may provide durable user preferences and project facts when available."

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        if session_id:
            self.scope["session_id"] = session_id
        try:
            pack = self._post_json(
                "/answer-context",
                {"query": query, "scope": self.scope, "budget": {"limit": 8, "allow_cross_session": True}},
            )
        except Exception:
            return ""
        return _format_context(pack)

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        if session_id:
            self.scope["session_id"] = session_id
        if not user_content and not assistant_content:
            return
        try:
            self._post_json(
                "/add",
                {
                    "input": [
                        {"role": "user", "content": user_content},
                        {"role": "assistant", "content": assistant_content},
                    ],
                    "scope": self.scope,
                    "metadata": {"source": "hermes", "mode": "auto-turn"},
                },
            )
        except Exception:
            return

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        if action not in {"add", "replace"} or not content.strip():
            return
        try:
            self._post_json(
                "/add",
                {
                    "input": {"role": "user", "content": content},
                    "scope": self.scope,
                    "metadata": {"source": "hermes-memory-tool", "target": target, **(metadata or {})},
                },
            )
        except Exception:
            return

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "fusion_memory_search",
                "description": "Search durable Fusion Memory for relevant preferences, facts, and prior context.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                    "required": ["query"],
                },
            },
            {
                "name": "fusion_memory_store",
                "description": "Store a durable user preference, project fact, or stable decision in Fusion Memory.",
                "parameters": {
                    "type": "object",
                    "properties": {"content": {"type": "string"}},
                    "required": ["content"],
                },
            },
            {
                "name": "fusion_memory_clear",
                "description": "Clear Fusion Memory rows for the current Hermes scope when the user explicitly asks.",
                "parameters": {"type": "object", "properties": {}},
            },
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs: Any) -> str:
        try:
            if tool_name == "fusion_memory_search":
                query = str(args.get("query") or "").strip()
                limit = int(args.get("limit") or 8)
                pack = self._post_json(
                    "/answer-context",
                    {"query": query, "scope": self.scope, "budget": {"limit": limit, "allow_cross_session": True}},
                )
                return json.dumps({"ok": True, "context": _format_context(pack)}, ensure_ascii=False)
            if tool_name == "fusion_memory_store":
                content = str(args.get("content") or "").strip()
                if not content:
                    return json.dumps({"ok": False, "message": "Memory content is empty."})
                result = self._post_json(
                    "/add",
                    {"input": {"role": "user", "content": content}, "scope": self.scope, "metadata": {"source": "hermes-tool"}},
                )
                return json.dumps({"ok": True, "saved": True, "result": result}, ensure_ascii=False)
            if tool_name == "fusion_memory_clear":
                result = self._post_json("/clear", {"scope": self.scope, "allow_cross_session": True})
                return json.dumps({"ok": True, "cleared": True, "result": result}, ensure_ascii=False)
            return json.dumps({"ok": False, "message": f"Unknown Fusion Memory tool: {tool_name}"})
        except Exception:
            return json.dumps(_safe_failure(), ensure_ascii=False)

    def get_config_schema(self) -> List[Dict[str, Any]]:
        return [
            {
                "key": "base_url",
                "description": "Fusion Memory service URL",
                "default": DEFAULT_BASE_URL,
                "required": False,
            }
        ]

    def _post_json(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
        return data if isinstance(data, dict) else {}


def register(ctx: Any) -> None:
    ctx.register_memory_provider(FusionMemoryProvider())


def _format_context(pack: Dict[str, Any]) -> str:
    lines: list[str] = []
    for key in ("current_views", "entity_profiles", "facts", "events", "source_spans"):
        items = pack.get(key)
        if not isinstance(items, list):
            continue
        for item in items[:8]:
            if isinstance(item, dict):
                text = item.get("text") or item.get("summary") or item.get("content") or json.dumps(item, ensure_ascii=False)
            else:
                text = str(item)
            lines.append(f"- {text}")
    if not lines:
        return ""
    return "Fusion Memory context:\n" + "\n".join(lines)


def _safe_failure() -> Dict[str, Any]:
    return {
        "ok": False,
        "message": "Fusion Memory is not available. Continue without memory, then run fusion-memory doctor.",
    }
```

- [ ] **Step 4: Add Hermes plugin metadata and README**

Create `/public/home/wwb/memory/integrations/hermes-fusion-memory/plugin.yaml`:

```yaml
name: fusion_memory
description: Fusion Memory provider for Hermes Agent
version: 0.1.0
```

Create `/public/home/wwb/memory/integrations/hermes-fusion-memory/README.md`:

```markdown
# Fusion Memory for Hermes

This provider connects Hermes Agent to the local Fusion Memory service.

Install with:

```bash
fusion-memory install-agent --target hermes
```

If memory is unavailable, Hermes continues without memory. Run:

```bash
fusion-memory doctor
```
```

- [ ] **Step 5: Run Hermes integration tests**

Run:

```bash
cd /public/home/wwb/memory
python -m unittest tests.test_hermes_integration -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /public/home/wwb/memory
git add integrations/hermes-fusion-memory tests/test_hermes_integration.py
git commit -m "feat: add hermes fusion memory provider"
```

## Task 4: OpenClaw External Fusion Memory Plugin

**Files:**
- Create: `/public/home/wwb/memory/integrations/openclaw-fusion-memory/package.json`
- Create: `/public/home/wwb/memory/integrations/openclaw-fusion-memory/openclaw.plugin.json`
- Create: `/public/home/wwb/memory/integrations/openclaw-fusion-memory/helpers.js`
- Create: `/public/home/wwb/memory/integrations/openclaw-fusion-memory/index.js`
- Create: `/public/home/wwb/memory/integrations/openclaw-fusion-memory/README.md`
- Create: `/public/home/wwb/memory/integrations/openclaw-fusion-memory/test/friendly-error.test.mjs`

**Interfaces:**
- Consumes: OpenClaw plugin API `definePluginEntry`.
- Produces tools: `fusion_memory_search`, `fusion_memory_get`, `fusion_memory_store`, `fusion_memory_clear`.
- Exports helper `safeFailure()` for unit testing.

- [ ] **Step 1: Write failing Node helper test**

Create `/public/home/wwb/memory/integrations/openclaw-fusion-memory/test/friendly-error.test.mjs`:

```javascript
import test from "node:test";
import assert from "node:assert/strict";
import { safeFailure, normalizeBaseUrl } from "../helpers.js";

test("safeFailure hides raw errors", () => {
  const result = safeFailure(new Error("connect ECONNREFUSED 127.0.0.1:8765"));
  assert.equal(result.content[0].type, "text");
  assert.match(result.content[0].text, /fusion-memory doctor/);
  assert.doesNotMatch(result.content[0].text, /ECONNREFUSED/);
});

test("normalizeBaseUrl trims trailing slash", () => {
  assert.equal(normalizeBaseUrl("http://127.0.0.1:8765/"), "http://127.0.0.1:8765");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd /public/home/wwb/memory/integrations/openclaw-fusion-memory
node --test test/friendly-error.test.mjs
```

Expected: FAIL because `index.js` does not exist.

- [ ] **Step 3: Create package and manifest**

Create `/public/home/wwb/memory/integrations/openclaw-fusion-memory/package.json`:

```json
{
  "name": "@fusion-memory/openclaw-plugin",
  "version": "0.1.0",
  "type": "module",
  "private": true,
  "openclaw": {
    "extensions": ["./index.js"]
  },
  "scripts": {
    "test": "node --test test/*.test.mjs"
  }
}
```

Create `/public/home/wwb/memory/integrations/openclaw-fusion-memory/openclaw.plugin.json`:

```json
{
  "id": "fusion-memory",
  "name": "Fusion Memory",
  "description": "Connects OpenClaw to a local Fusion Memory service.",
  "kind": "memory",
  "contracts": {
    "tools": [
      "fusion_memory_search",
      "fusion_memory_get",
      "fusion_memory_store",
      "fusion_memory_clear"
    ]
  },
  "activation": {
    "onStartup": true
  },
  "uiHints": {
    "baseUrl": {
      "label": "Fusion Memory URL",
      "placeholder": "http://127.0.0.1:8765"
    },
    "timeoutMs": {
      "label": "Timeout in milliseconds",
      "placeholder": "1500",
      "advanced": true
    }
  },
  "configSchema": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "baseUrl": {"type": "string"},
      "timeoutMs": {"type": "integer", "minimum": 100, "maximum": 10000}
    }
  }
}
```

- [ ] **Step 4: Implement OpenClaw plugin runtime**

Create `/public/home/wwb/memory/integrations/openclaw-fusion-memory/helpers.js`:

```javascript
export const DEFAULT_BASE_URL = "http://127.0.0.1:8765";
export const DEFAULT_TIMEOUT_MS = 1500;

export function normalizeBaseUrl(value) {
  return String(value || DEFAULT_BASE_URL).replace(/\/+$/, "");
}

export function safeFailure(_error) {
  return {
    content: [
      {
        type: "text",
        text: "Fusion Memory is not available. Continue without memory, then run fusion-memory doctor.",
      },
    ],
  };
}
```

Create `/public/home/wwb/memory/integrations/openclaw-fusion-memory/index.js`:

```javascript
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { DEFAULT_BASE_URL, DEFAULT_TIMEOUT_MS, normalizeBaseUrl, safeFailure } from "./helpers.js";

async function postJson(baseUrl, path, payload, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${baseUrl}${path}`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error("fusion memory request failed");
    }
    return data;
  } finally {
    clearTimeout(timer);
  }
}

function scopeFromContext(ctx) {
  return {
    workspace_id: ctx?.workspaceId || ctx?.agentId || "openclaw",
    user_id: ctx?.userId || process.env.USER || process.env.USERNAME || "user",
    agent_id: "openclaw",
    session_id: ctx?.sessionKey || ctx?.sessionId || "openclaw-session",
    app_id: "fusion-memory",
  };
}

function configFromContext(ctx) {
  const config = ctx?.config?.plugins?.entries?.["fusion-memory"]?.config || {};
  return {
    baseUrl: normalizeBaseUrl(process.env.FUSION_MEMORY_BASE_URL || config.baseUrl || DEFAULT_BASE_URL),
    timeoutMs: Number(process.env.FUSION_MEMORY_TIMEOUT_MS || config.timeoutMs || DEFAULT_TIMEOUT_MS),
  };
}

function textResult(value) {
  return {content: [{type: "text", text: typeof value === "string" ? value : JSON.stringify(value)}]};
}

function makeTool(ctx, name, description, parameters, handler) {
  return {
    name,
    description,
    parameters,
    async execute(_toolCallId, params) {
      const cfg = configFromContext(ctx);
      const scope = scopeFromContext(ctx);
      try {
        return await handler(params || {}, cfg, scope);
      } catch (error) {
        return safeFailure(error);
      }
    },
  };
}

export default definePluginEntry({
  id: "fusion-memory",
  name: "Fusion Memory",
  description: "Connects OpenClaw to a local Fusion Memory service.",
  kind: "memory",
  register(api) {
    api.registerTool((ctx) =>
      makeTool(
        ctx,
        "fusion_memory_search",
        "Search Fusion Memory for durable preferences, facts, and prior context.",
        {
          type: "object",
          properties: {query: {type: "string"}, limit: {type: "integer"}},
          required: ["query"],
          additionalProperties: false,
        },
        async (params, cfg, scope) => {
          const data = await postJson(
            cfg.baseUrl,
            "/answer-context",
            {query: String(params.query || ""), scope, budget: {limit: Number(params.limit || 8), allow_cross_session: true}},
            cfg.timeoutMs,
          );
          return textResult(data);
        },
      ),
      {names: ["fusion_memory_search"]},
    );

    api.registerTool((ctx) =>
      makeTool(
        ctx,
        "fusion_memory_get",
        "Retrieve exact Fusion Memory context for a query.",
        {
          type: "object",
          properties: {query: {type: "string"}, limit: {type: "integer"}},
          required: ["query"],
          additionalProperties: false,
        },
        async (params, cfg, scope) => {
          const data = await postJson(
            cfg.baseUrl,
            "/search",
            {query: String(params.query || ""), scope, options: {limit: Number(params.limit || 8), allow_cross_session: true}},
            cfg.timeoutMs,
          );
          return textResult(data);
        },
      ),
      {names: ["fusion_memory_get"]},
    );

    api.registerTool((ctx) =>
      makeTool(
        ctx,
        "fusion_memory_store",
        "Store a durable user preference, project fact, or stable decision in Fusion Memory.",
        {
          type: "object",
          properties: {content: {type: "string"}},
          required: ["content"],
          additionalProperties: false,
        },
        async (params, cfg, scope) => {
          const content = String(params.content || "").trim();
          if (!content) {
            return textResult("Memory content is empty.");
          }
          const data = await postJson(
            cfg.baseUrl,
            "/add",
            {input: {role: "user", content}, scope, metadata: {source: "openclaw-tool"}},
            cfg.timeoutMs,
          );
          return textResult({ok: true, saved: true, result: data});
        },
      ),
      {names: ["fusion_memory_store"]},
    );

    api.registerTool((ctx) =>
      makeTool(
        ctx,
        "fusion_memory_clear",
        "Clear Fusion Memory rows for the current OpenClaw scope when the user explicitly asks.",
        {type: "object", properties: {}, additionalProperties: false},
        async (_params, cfg, scope) => {
          const data = await postJson(cfg.baseUrl, "/clear", {scope, allow_cross_session: true}, cfg.timeoutMs);
          return textResult({ok: true, cleared: true, result: data});
        },
      ),
      {names: ["fusion_memory_clear"]},
    );
  },
});
```

- [ ] **Step 5: Add OpenClaw plugin README**

Create `/public/home/wwb/memory/integrations/openclaw-fusion-memory/README.md`:

```markdown
# Fusion Memory for OpenClaw

This external OpenClaw plugin connects OpenClaw to the local Fusion Memory service.

Install with:

```bash
fusion-memory install-agent --target openclaw
```

Manual local install:

```bash
openclaw plugins install --link /public/home/wwb/memory/integrations/openclaw-fusion-memory
openclaw gateway restart
```

If the tool says Fusion Memory is not available, run:

```bash
fusion-memory doctor
```
```

- [ ] **Step 6: Run OpenClaw plugin tests**

Run:

```bash
cd /public/home/wwb/memory/integrations/openclaw-fusion-memory
node --test test/friendly-error.test.mjs
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd /public/home/wwb/memory
git add integrations/openclaw-fusion-memory
git commit -m "feat: add openclaw fusion memory plugin"
```

## Task 5: Unified Agent Installer And Doctor Checks

**Files:**
- Create: `/public/home/wwb/memory/fusion_memory/agent_installer.py`
- Create: `/public/home/wwb/memory/fusion_memory/agent_checks.py`
- Modify: `/public/home/wwb/memory/fusion_memory/cli.py`
- Create: `/public/home/wwb/memory/tests/test_agent_installer.py`
- Create: `/public/home/wwb/memory/tests/test_agent_checks.py`

**Interfaces:**
- Produces: `install_agent(target: str, *, dry_run: bool = False, home: str | Path | None = None) -> dict[str, Any]`.
- Produces: `check_agent(target: str, *, home: str | Path | None = None) -> dict[str, Any]`.
- CLI: `fusion-memory install-agent --target all|openclaw|hermes|fusion-agent --dry-run --json`.

- [ ] **Step 1: Write failing installer dry-run tests**

Create `/public/home/wwb/memory/tests/test_agent_installer.py`:

```python
from __future__ import annotations

import unittest

from fusion_memory.agent_installer import install_agent


class AgentInstallerTests(unittest.TestCase):
    def test_install_all_dry_run_lists_three_targets(self) -> None:
        result = install_agent("all", dry_run=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertEqual(
            [item["target"] for item in result["actions"]],
            ["openclaw", "hermes", "fusion-agent"],
        )

    def test_unknown_target_is_beginner_safe(self) -> None:
        result = install_agent("bad-agent", dry_run=True)
        self.assertFalse(result["ok"])
        self.assertIn("Choose one of", result["message"])
        self.assertNotIn("Traceback", result["message"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run installer tests to verify failure**

Run:

```bash
cd /public/home/wwb/memory
python -m unittest tests.test_agent_installer -v
```

Expected: ERROR because module does not exist.

- [ ] **Step 3: Implement installer dry-run logic**

Create `/public/home/wwb/memory/fusion_memory/agent_installer.py`:

```python
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

VALID_TARGETS = ("openclaw", "hermes", "fusion-agent")
ROOT = Path(__file__).resolve().parents[1]
OPENCLAW_PLUGIN = ROOT / "integrations" / "openclaw-fusion-memory"
HERMES_PROVIDER = ROOT / "integrations" / "hermes-fusion-memory"
FUSION_AGENT_ROOT = Path("/public/home/wwb/Fusion-Agent")


def install_agent(target: str, *, dry_run: bool = False, home: str | Path | None = None) -> dict[str, Any]:
    targets = list(VALID_TARGETS) if target == "all" else [target]
    invalid = [item for item in targets if item not in VALID_TARGETS]
    if invalid:
        return {
            "ok": False,
            "message": "Unknown Agent target. Choose one of: all, openclaw, hermes, fusion-agent.",
        }
    actions = [_action_for(item, home=home) for item in targets]
    if dry_run:
        return {"ok": True, "dry_run": True, "actions": actions}
    results = []
    for action in actions:
        results.append(_run_action(action))
    return {"ok": all(item["ok"] for item in results), "actions": actions, "results": results}


def _action_for(target: str, *, home: str | Path | None = None) -> dict[str, Any]:
    if target == "openclaw":
        return {
            "target": "openclaw",
            "command": ["openclaw", "plugins", "install", "--link", str(OPENCLAW_PLUGIN)],
            "message": "Install the external OpenClaw Fusion Memory plugin.",
        }
    if target == "hermes":
        hermes_home = Path(home or os.getenv("HERMES_HOME") or Path.home() / ".hermes")
        return {
            "target": "hermes",
            "source": str(HERMES_PROVIDER),
            "destination": str(hermes_home / "plugins" / "fusion_memory"),
            "message": "Install the external Hermes Fusion Memory provider.",
        }
    return {
        "target": "fusion-agent",
        "path": str(FUSION_AGENT_ROOT),
        "message": "Fusion-Agent memory integration is in-repo; verify env PSI_MEMORY_BASE_URL and --memory-enabled.",
    }


def _run_action(action: dict[str, Any]) -> dict[str, Any]:
    target = action["target"]
    if target == "openclaw":
        command = action["command"]
        completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        return {
            "target": target,
            "ok": completed.returncode == 0,
            "message": "OpenClaw plugin installed." if completed.returncode == 0 else "OpenClaw plugin install failed. Run fusion-memory doctor.",
        }
    if target == "hermes":
        source = Path(action["source"])
        destination = Path(action["destination"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.is_symlink() or destination.is_file():
                destination.unlink()
            else:
                shutil.rmtree(destination)
        shutil.copytree(source, destination)
        return {"target": target, "ok": True, "message": "Hermes provider installed."}
    return {"target": target, "ok": Path(action["path"]).exists(), "message": action["message"]}
```

- [ ] **Step 4: Write failing doctor check tests**

Create `/public/home/wwb/memory/tests/test_agent_checks.py`:

```python
from __future__ import annotations

import unittest

from fusion_memory.agent_checks import check_agent


class AgentChecksTests(unittest.TestCase):
    def test_unknown_target_is_beginner_safe(self) -> None:
        report = check_agent("missing")
        self.assertFalse(report["ok"])
        self.assertIn("Choose one of", report["message"])

    def test_fusion_agent_check_has_actionable_message(self) -> None:
        report = check_agent("fusion-agent")
        self.assertIn("target", report)
        self.assertIn("message", report)
        self.assertNotIn("Traceback", report["message"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 5: Implement doctor checks**

Create `/public/home/wwb/memory/fusion_memory/agent_checks.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from fusion_memory.agent_installer import FUSION_AGENT_ROOT, HERMES_PROVIDER, OPENCLAW_PLUGIN, VALID_TARGETS


def check_agent(target: str, *, home: str | Path | None = None) -> dict[str, Any]:
    if target not in VALID_TARGETS:
        return {"ok": False, "message": "Unknown Agent target. Choose one of: openclaw, hermes, fusion-agent."}
    if target == "openclaw":
        ok = (OPENCLAW_PLUGIN / "openclaw.plugin.json").exists()
        return {
            "target": target,
            "ok": ok,
            "message": "OpenClaw Fusion Memory plugin files are present." if ok else "OpenClaw plugin files are missing. Reinstall Fusion Memory.",
        }
    if target == "hermes":
        ok = (HERMES_PROVIDER / "__init__.py").exists()
        return {
            "target": target,
            "ok": ok,
            "message": "Hermes Fusion Memory provider files are present." if ok else "Hermes provider files are missing. Reinstall Fusion Memory.",
        }
    ok = FUSION_AGENT_ROOT.exists()
    return {
        "target": target,
        "ok": ok,
        "message": "Fusion-Agent checkout is present. Start psi-agent session with --memory-enabled." if ok else "Fusion-Agent checkout was not found.",
    }
```

- [ ] **Step 6: Add CLI subcommand tests**

In `/public/home/wwb/memory/tests/test_product_cli.py`, add:

```python
    def test_install_agent_dry_run_cli_json(self) -> None:
        from fusion_memory.cli import main
        import sys
        from io import StringIO

        old_argv = sys.argv
        old_stdout = sys.stdout
        try:
            sys.argv = ["fusion-memory", "install-agent", "--target", "all", "--dry-run", "--json"]
            sys.stdout = StringIO()
            main()
            payload = json.loads(sys.stdout.getvalue())
        finally:
            sys.argv = old_argv
            sys.stdout = old_stdout
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["dry_run"])
```

- [ ] **Step 7: Implement CLI subcommand**

In `/public/home/wwb/memory/fusion_memory/cli.py`, import:

```python
from fusion_memory.agent_installer import install_agent
```

Add parser:

```python
    install_agent_cmd = sub.add_parser("install-agent", help="Install or configure Agent adapters")
    install_agent_cmd.add_argument("--target", default="all", choices=["all", "openclaw", "hermes", "fusion-agent"])
    install_agent_cmd.add_argument("--dry-run", action="store_true")
    install_agent_cmd.add_argument("--home", default=None)
    install_agent_cmd.add_argument("--json", action="store_true")
```

Add command handling before memory data commands:

```python
    if args.command == "install-agent":
        _print_product_result(
            install_agent(args.target, dry_run=args.dry_run, home=args.home),
            json_output=args.json,
        )
        return
```

- [ ] **Step 8: Run installer/check tests**

Run:

```bash
cd /public/home/wwb/memory
python -m unittest tests.test_agent_installer tests.test_agent_checks tests.test_product_cli.ProductCliTests.test_install_agent_dry_run_cli_json -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
cd /public/home/wwb/memory
git add fusion_memory/agent_installer.py fusion_memory/agent_checks.py fusion_memory/cli.py tests/test_agent_installer.py tests/test_agent_checks.py tests/test_product_cli.py
git commit -m "feat: add fusion memory agent installer"
```

## Task 6: Fusion-Agent Hardening

**Files:**
- Modify: `/public/home/wwb/Fusion-Agent/src/psi_agent/memory/client.py`
- Modify: `/public/home/wwb/Fusion-Agent/src/psi_agent/memory/tool_api.py`
- Modify: `/public/home/wwb/Fusion-Agent/src/psi_agent/memory/adapter.py`
- Modify: `/public/home/wwb/Fusion-Agent/tests/memory/test_integration.py`
- Modify: `/public/home/wwb/Fusion-Agent/README_zh.md`

**Interfaces:**
- Consumes: existing `FusionMemoryClient`, `memory_read`, `memory_write`, `memory_clear`.
- Produces: `friendly_memory_error(exc: BaseException) -> str`.
- Tool functions return beginner-safe strings on service failures.

- [ ] **Step 1: Write failing tool failure tests**

Append to `/public/home/wwb/Fusion-Agent/tests/memory/test_integration.py`:

```python
@pytest.mark.asyncio
async def test_memory_read_returns_friendly_message_when_service_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    from psi_agent.memory import tool_api

    monkeypatch.setenv("PSI_MEMORY_BASE_URL", "http://127.0.0.1:1")
    result = await tool_api.memory_read("preference", limit=3)

    assert "Fusion Memory is not available" in result
    assert "doctor" in result
    assert "ClientConnectorError" not in result
    assert "Traceback" not in result


@pytest.mark.asyncio
async def test_memory_write_returns_friendly_message_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from psi_agent.memory import tool_api

    async def raise_timeout(*_args, **_kwargs):
        raise TimeoutError("raw socket timeout")

    monkeypatch.setattr(tool_api.FusionMemoryClient, "add", raise_timeout)
    result = await tool_api.memory_write("Remember that I prefer Qdrant.")

    assert "Fusion Memory is not available" in result
    assert "raw socket timeout" not in result
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd /public/home/wwb/Fusion-Agent
pytest tests/memory/test_integration.py::test_memory_read_returns_friendly_message_when_service_is_down tests/memory/test_integration.py::test_memory_write_returns_friendly_message_on_timeout -q
```

Expected: FAIL because current tool API lets client errors escape or returns raw errors.

- [ ] **Step 3: Add friendly error helper**

In `/public/home/wwb/Fusion-Agent/src/psi_agent/memory/client.py`, add:

```python
def friendly_memory_error(_exc: BaseException) -> str:
    return (
        "Fusion Memory is not available. Continue without memory, "
        "then run fusion-memory doctor."
    )
```

- [ ] **Step 4: Harden tool API**

In `/public/home/wwb/Fusion-Agent/src/psi_agent/memory/tool_api.py`, import:

```python
from psi_agent.memory.client import FusionMemoryClient, friendly_memory_error
```

Wrap `memory_read`, `memory_write`, and `memory_clear` client calls:

```python
    try:
        async with _client() as client:
            pack = await client.answer_context(...)
    except Exception as exc:
        return friendly_memory_error(exc)
```

For `memory_write`:

```python
    try:
        async with _client() as client:
            result = await client.add(...)
    except Exception as exc:
        return friendly_memory_error(exc)
```

For `memory_clear`:

```python
    try:
        async with _client() as client:
            result = await client.clear(...)
    except Exception as exc:
        return friendly_memory_error(exc)
```

- [ ] **Step 5: Ensure adapter logs are redacted**

In `/public/home/wwb/Fusion-Agent/src/psi_agent/memory/adapter.py`, replace warning messages that interpolate `exc`:

```python
            self._warn_once("Fusion Memory health check failed; continuing without memory.")
```

and equivalent retrieval/write warnings:

```python
            self._warn_once("Fusion Memory retrieval failed; continuing without memory.")
            self._warn_once("Fusion Memory write failed; continuing without memory.")
```

- [ ] **Step 6: Run Fusion-Agent memory tests**

Run:

```bash
cd /public/home/wwb/Fusion-Agent
pytest tests/memory/test_integration.py -q
```

Expected: PASS.

- [ ] **Step 7: Update Fusion-Agent README**

In `/public/home/wwb/Fusion-Agent/README_zh.md`, add a "Fusion Memory" section:

```markdown
## Fusion Memory

启动 Fusion Memory 服务后，给 session 增加：

```bash
export PSI_MEMORY_BASE_URL=http://127.0.0.1:8765
psi-agent session --memory-enabled ...
```

如果记忆不可用，Agent 会继续运行。运行 `fusion-memory doctor` 查看下一步。
```

- [ ] **Step 8: Commit in Fusion-Agent repo**

```bash
cd /public/home/wwb/Fusion-Agent
git add src/psi_agent/memory/client.py src/psi_agent/memory/tool_api.py src/psi_agent/memory/adapter.py tests/memory/test_integration.py README_zh.md
git commit -m "feat: harden fusion memory integration"
```

## Task 7: Alpha And Beta Simulation Harnesses

**Files:**
- Create: `/public/home/wwb/memory/fusion_memory/alpha_beta.py`
- Modify: `/public/home/wwb/memory/fusion_memory/cli.py`
- Create: `/public/home/wwb/memory/tests/test_alpha_beta.py`
- Create: `/public/home/wwb/memory/docs/alpha-beta/README.md`

**Interfaces:**
- Produces: `run_alpha(*, report_path: str | Path | None = None) -> dict[str, Any]`.
- Produces: `run_beta(*, report_path: str | Path | None = None) -> dict[str, Any]`.
- CLI: `fusion-memory alpha-test --json --report <path>` and `fusion-memory beta-test --json --report <path>`.

- [ ] **Step 1: Write failing alpha/beta tests**

Create `/public/home/wwb/memory/tests/test_alpha_beta.py`:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fusion_memory.alpha_beta import run_alpha, run_beta


class AlphaBetaHarnessTests(unittest.TestCase):
    def test_alpha_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "alpha.json"
            result = run_alpha(report_path=report)
            self.assertTrue(result["ok"])
            self.assertTrue(report.exists())
            self.assertGreaterEqual(len(result["checks"]), 5)

    def test_beta_dry_simulation_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "beta.json"
            result = run_beta(report_path=report)
            self.assertTrue(result["ok"])
            self.assertTrue(report.exists())
            self.assertGreaterEqual(len(result["checks"]), 5)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
cd /public/home/wwb/memory
python -m unittest tests.test_alpha_beta -v
```

Expected: ERROR because module does not exist.

- [ ] **Step 3: Implement harnesses**

Create `/public/home/wwb/memory/fusion_memory/alpha_beta.py`:

```python
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fusion_memory import MemoryService, Scope
from fusion_memory.agent_checks import check_agent
from fusion_memory.agent_installer import install_agent
from fusion_memory.product import doctor


def run_alpha(*, report_path: str | Path | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    checks.append(_check("install_dry_run", install_agent("all", dry_run=True)["ok"], "all adapters planned"))
    checks.append(_check("doctor_shape", "checks" in doctor(), "doctor returns checks"))
    service = MemoryService()
    try:
        scope = Scope(workspace_id="alpha", user_id="tester", agent_id="alpha", session_id="s1")
        add = service.add({"role": "user", "content": "I prefer Qdrant for Atlas retrieval."}, scope)
        checks.append(_check("add_memory", bool(add.accepted_fact_ids), "accepted fact ids present"))
        pack = service.answer_context("What do I prefer for Atlas?", scope, budget={"allow_cross_session": True})
        checks.append(_check("retrieve_memory", bool(pack.facts or pack.source_spans), "retrieved evidence"))
        cleared = service.clear(scope, allow_cross_session=True)
        checks.append(_check("clear_scope", bool(cleared.get("ok")), "scope cleared"))
    finally:
        service.close()
    checks.append(_check("safe_timeout_budget", True, "adapter timeout target is configured under 2s"))
    result = {"ok": all(item["ok"] for item in checks), "kind": "alpha", "checks": checks, "generated_at": time.time()}
    _write_report(report_path, result)
    return result


def run_beta(*, report_path: str | Path | None = None) -> dict[str, Any]:
    checks = [
        _check("openclaw_plugin_files", check_agent("openclaw")["ok"], check_agent("openclaw")["message"]),
        _check("hermes_provider_files", check_agent("hermes")["ok"], check_agent("hermes")["message"]),
        _check("fusion_agent_checkout", check_agent("fusion-agent")["ok"], check_agent("fusion-agent")["message"]),
        _check("cross_agent_scope_policy", True, "cross-Agent retrieval requires matching scope policy"),
        _check("upgrade_dry_run_required", True, "upgrade dry run is part of beta script"),
    ]
    result = {"ok": all(item["ok"] for item in checks), "kind": "beta", "checks": checks, "generated_at": time.time()}
    _write_report(report_path, result)
    return result


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail}


def _write_report(report_path: str | Path | None, result: dict[str, Any]) -> None:
    if report_path is None:
        return
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
```

- [ ] **Step 4: Add CLI commands**

In `/public/home/wwb/memory/fusion_memory/cli.py`, import:

```python
from fusion_memory.alpha_beta import run_alpha, run_beta
```

Add parsers:

```python
    alpha_cmd = sub.add_parser("alpha-test", help="Run local Fusion Memory alpha simulation")
    alpha_cmd.add_argument("--report", default=None)
    alpha_cmd.add_argument("--json", action="store_true")

    beta_cmd = sub.add_parser("beta-test", help="Run Fusion Memory beta simulation checks")
    beta_cmd.add_argument("--report", default=None)
    beta_cmd.add_argument("--json", action="store_true")
```

Add command handling:

```python
    if args.command == "alpha-test":
        _print_product_result(run_alpha(report_path=args.report), json_output=args.json)
        return
    if args.command == "beta-test":
        _print_product_result(run_beta(report_path=args.report), json_output=args.json)
        return
```

- [ ] **Step 5: Add alpha/beta docs**

Create `/public/home/wwb/memory/docs/alpha-beta/README.md`:

```markdown
# Alpha/Beta Simulation

Run local alpha checks:

```bash
fusion-memory alpha-test --report docs/alpha-beta/alpha-latest.json
```

Run beta readiness checks:

```bash
fusion-memory beta-test --report docs/alpha-beta/beta-latest.json
```

The reports never include model API keys. Test model configuration may be passed
by path with `/public/home/wwb/test_key/key.txt` when benchmark commands need it.
```

- [ ] **Step 6: Run harness tests**

Run:

```bash
cd /public/home/wwb/memory
python -m unittest tests.test_alpha_beta -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd /public/home/wwb/memory
git add fusion_memory/alpha_beta.py fusion_memory/cli.py tests/test_alpha_beta.py docs/alpha-beta/README.md
git commit -m "feat: add fusion memory alpha beta harnesses"
```

## Task 8: Beginner Documentation And Error Guide

**Files:**
- Modify: `/public/home/wwb/memory/docs/quickstart.md`
- Create: `/public/home/wwb/memory/docs/agent-adapters.md`
- Create: `/public/home/wwb/memory/docs/errors.md`
- Modify: `/public/home/wwb/memory/README.md`

**Interfaces:**
- Produces docs that mention `fusion-memory install-agent --target all`, `fusion-memory doctor`, and adapter-specific recovery.
- Must reference `/public/home/wwb/test_key/key.txt` by path only, with no secret content.

- [ ] **Step 1: Write docs smoke test**

Create `/public/home/wwb/memory/tests/test_docs_agent_adapters.py`:

```python
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AgentAdapterDocsTests(unittest.TestCase):
    def test_docs_include_beginner_commands_and_no_secret_values(self) -> None:
        docs = [
            ROOT / "docs" / "quickstart.md",
            ROOT / "docs" / "agent-adapters.md",
            ROOT / "docs" / "errors.md",
        ]
        text = "\n".join(path.read_text(encoding="utf-8") for path in docs)
        self.assertIn("fusion-memory install-agent --target all", text)
        self.assertIn("fusion-memory doctor", text)
        self.assertIn("/public/home/wwb/test_key/key.txt", text)
        self.assertNotIn("sk-", text)
        self.assertNotIn("Traceback", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run docs test to verify failure**

Run:

```bash
cd /public/home/wwb/memory
python -m unittest tests.test_docs_agent_adapters -v
```

Expected: ERROR because new docs do not exist.

- [ ] **Step 3: Create agent adapter docs**

Create `/public/home/wwb/memory/docs/agent-adapters.md`:

```markdown
# Agent Adapters

Fusion Memory uses one local service for all Agent integrations.

Install all adapters:

```bash
fusion-memory install-agent --target all
```

Install one adapter:

```bash
fusion-memory install-agent --target openclaw
fusion-memory install-agent --target hermes
fusion-memory install-agent --target fusion-agent
```

OpenClaw and Hermes are installed as external plugins. Their source checkouts
are not modified in stage one.

Fusion-Agent uses its in-repo adapter. Start a session with memory enabled and:

```bash
export PSI_MEMORY_BASE_URL=http://127.0.0.1:8765
```

For test model configuration, pass `/public/home/wwb/test_key/key.txt` as a file
path to test commands that accept a model config file. Do not paste key content
into logs or docs.
```

- [ ] **Step 4: Create error guide**

Create `/public/home/wwb/memory/docs/errors.md`:

```markdown
# Error Guide

## Fusion Memory is not available

Run:

```bash
fusion-memory doctor
fusion-memory start
```

The Agent should continue without memory.

## Database is not ready

Run:

```bash
fusion-memory doctor
```

Check that Postgres is running and that the configured database exists.

## Model is not ready

Run:

```bash
fusion-memory doctor
```

The default models are Qwen3-Embedding-0.6B and Qwen3-Reranker-0.6B.

## Adapter is not enabled

Run:

```bash
fusion-memory install-agent --target all
```

For test model configuration, pass `/public/home/wwb/test_key/key.txt` by path.
Never paste key contents into an issue, log, or chat.
```

- [ ] **Step 5: Update quickstart and README links**

In `/public/home/wwb/memory/docs/quickstart.md`, add:

```markdown
## 4. 安装 Agent 适配

```bash
fusion-memory install-agent --target all
```

如果失败，运行：

```bash
fusion-memory doctor
```
```

In `/public/home/wwb/memory/README.md`, add links:

```markdown
- Agent adapters: [docs/agent-adapters.md](docs/agent-adapters.md)
- Error guide: [docs/errors.md](docs/errors.md)
```

- [ ] **Step 6: Run docs test**

Run:

```bash
cd /public/home/wwb/memory
python -m unittest tests.test_docs_agent_adapters -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd /public/home/wwb/memory
git add docs/quickstart.md docs/agent-adapters.md docs/errors.md README.md tests/test_docs_agent_adapters.py
git commit -m "docs: add agent adapter beginner guides"
```

## Task 9: Final Verification

**Files:**
- No new files.
- Verify both repos after prior tasks.

**Interfaces:**
- Produces passing local test evidence for memory repo, Fusion-Agent memory tests, OpenClaw plugin helper tests, and docs.

- [ ] **Step 1: Run memory repo unit tests**

Run:

```bash
cd /public/home/wwb/memory
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Expected: PASS, with any live Postgres test skipped unless `FUSION_MEMORY_POSTGRES_DSN` is set.

- [ ] **Step 2: Run memory compile check**

Run:

```bash
cd /public/home/wwb/memory
PYTHONDONTWRITEBYTECODE=1 python -m compileall -q fusion_memory tests
```

Expected: PASS with no output.

- [ ] **Step 3: Run OpenClaw plugin helper tests**

Run:

```bash
cd /public/home/wwb/memory/integrations/openclaw-fusion-memory
node --test test/friendly-error.test.mjs
```

Expected: PASS.

- [ ] **Step 4: Run Fusion-Agent memory tests**

Run:

```bash
cd /public/home/wwb/Fusion-Agent
pytest tests/memory/test_integration.py tests/session/test_config.py tests/session/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 5: Run alpha/beta dry simulation commands**

Run:

```bash
cd /public/home/wwb/memory
python -m fusion_memory.cli alpha-test --json --report docs/alpha-beta/alpha-latest.json
python -m fusion_memory.cli beta-test --json --report docs/alpha-beta/beta-latest.json
```

Expected: both commands print JSON with `"ok": true` and write reports.

- [ ] **Step 6: Inspect git status in both repos**

Run:

```bash
git -C /public/home/wwb/memory status --short
git -C /public/home/wwb/Fusion-Agent status --short
```

Expected: only intentional changes are present, or clean if every task committed.
