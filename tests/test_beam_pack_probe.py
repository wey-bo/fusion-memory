from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.beam_pack_probe import _compact_mapping, _selected_ids


class BeamPackProbeTests(unittest.TestCase):
    def test_selected_ids_accepts_csv_file_and_comments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ids.txt"
            path.write_text("q2,q3 # batch\n\nq4\n", encoding="utf-8")

            ids = _selected_ids("q1,q2", str(path))

        self.assertEqual(ids, ["q1", "q2", "q3", "q4"])

    def test_compact_mapping_bounds_large_values(self) -> None:
        compact = _compact_mapping(
            {
                "small": "ok",
                "items": list(range(20)),
                "nested": {str(index): index for index in range(20)},
                "later": "x",
            },
            max_items=3,
        )

        self.assertEqual(compact["small"], "ok")
        self.assertEqual(compact["items"], list(range(12)))
        self.assertEqual(len(compact["nested"]), 12)
        self.assertTrue(compact["_truncated"])
        self.assertNotIn("later", compact)


if __name__ == "__main__":
    unittest.main()
