"""Tests for src/merge_json.py.

Run with: python -m unittest tests.test_merge_json
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
import tempfile
import unittest
from pathlib import Path

from src import merge_json


class TestMergeJson(unittest.TestCase):

    def test_union_by_key_ours_wins(self):
        with tempfile.TemporaryDirectory() as d:
            up, ours = Path(d, "up.json"), Path(d, "ours.json")
            up.write_text(json.dumps({"a": 1, "b": "upstream"}))
            ours.write_text(json.dumps({"b": "ours", "c": 3}))
            self.assertEqual(merge_json.main([str(up), str(ours)]), 0)
            self.assertEqual(json.loads(up.read_text()), {"a": 1, "b": "ours", "c": 3})

    def test_rejects_non_object(self):
        with tempfile.TemporaryDirectory() as d:
            up, ours = Path(d, "up.json"), Path(d, "ours.json")
            up.write_text("[]")
            ours.write_text("{}")
            self.assertEqual(merge_json.main([str(up), str(ours)]), 1)


if __name__ == "__main__":
    unittest.main()
