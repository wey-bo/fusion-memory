from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tools.agent_runtime_smoke as smoke


class AgentRuntimeSmokeTests(unittest.TestCase):
    def test_missing_openclaw_host_is_beginner_safe(self) -> None:
        with patch("tools.agent_runtime_smoke.shutil.which", return_value=None):
            report = smoke.run_smoke("openclaw", memory_url="http://127.0.0.1:8765")

        self.assertFalse(report["ok"])
        self.assertFalse(report["host_available"])
        self.assertIn("OpenClaw", report["message"])
        self.assertNotIn("Traceback", json.dumps(report))

    def test_cli_writes_output_json(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch("tools.agent_runtime_smoke.run_smoke", return_value={"ok": True, "target": "hermes"}),
        ):
            out = Path(tmp) / "smoke.json"
            code = smoke.main(["--target", "hermes", "--memory-url", "http://127.0.0.1:8765", "--output", str(out)])

            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["target"], "hermes")

    def test_script_invocation_writes_beginner_safe_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "smoke.json"
            missing_checkout = Path(tmp) / "missing-fusion-agent"
            env = {**os.environ, "FUSION_AGENT_ROOT": str(missing_checkout)}
            completed = subprocess.run(
                [
                    sys.executable,
                    "tools/agent_runtime_smoke.py",
                    "--target",
                    "fusion-agent",
                    "--memory-url",
                    "http://127.0.0.1:8765",
                    "--output",
                    str(out),
                ],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(completed.returncode, 1)
            self.assertTrue(out.exists(), completed.stderr)
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertFalse(report["ok"])
            self.assertIn("Fusion-Agent", report["message"])
            self.assertNotIn("Traceback", completed.stderr + json.dumps(report))


if __name__ == "__main__":
    unittest.main()
