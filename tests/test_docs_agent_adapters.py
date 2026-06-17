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
