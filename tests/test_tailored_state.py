"""Tests for src/tailored_state.py — persistence + rate-limit window.

Run with: python -m unittest tests.test_tailored_state
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src import tailored_state


class TestLoadSave(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "tailored_jobs.json"

    def test_load_missing_returns_empty(self):
        self.assertEqual(tailored_state.load(self.tmp), {})

    def test_save_then_load_roundtrips(self):
        data = {"k": {"tailored_at": "2026-05-20T00:00:00+00:00", "run_id": "abc"}}
        tailored_state.save(self.tmp, data)
        self.assertEqual(tailored_state.load(self.tmp), data)

    def test_load_malformed_returns_empty(self):
        self.tmp.write_text("not json", encoding="utf-8")
        self.assertEqual(tailored_state.load(self.tmp), {})

    def test_load_non_dict_returns_empty(self):
        self.tmp.write_text("[1, 2, 3]", encoding="utf-8")
        self.assertEqual(tailored_state.load(self.tmp), {})


class TestRecord(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "tailored_jobs.json"

    def test_record_persists_entry(self):
        entry = tailored_state.record(
            self.tmp, "greenhouse:stripe:1",
            run_id="100", bullets_count=8, dropped_count=1,
            cost_estimate_usd=0.0567,
            artifact_name="tailored-greenhouse_stripe_1-100",
        )
        self.assertEqual(entry["run_id"], "100")
        self.assertEqual(entry["bullets_count"], 8)
        self.assertEqual(entry["dropped_count"], 1)
        self.assertEqual(entry["cost_estimate_usd"], 0.0567)
        loaded = tailored_state.load(self.tmp)
        self.assertIn("greenhouse:stripe:1", loaded)

    def test_record_overwrites_existing(self):
        tailored_state.record(self.tmp, "k", run_id="1", bullets_count=5, dropped_count=0, cost_estimate_usd=0.01)
        tailored_state.record(self.tmp, "k", run_id="2", bullets_count=7, dropped_count=2, cost_estimate_usd=0.02)
        loaded = tailored_state.load(self.tmp)
        self.assertEqual(loaded["k"]["run_id"], "2")
        self.assertEqual(loaded["k"]["bullets_count"], 7)


class TestRateLimit(unittest.TestCase):

    def _state_with(self, *, count_within_hour: int, count_outside: int = 0) -> dict:
        now = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
        recent = (now - timedelta(minutes=30)).isoformat()
        old = (now - timedelta(hours=5)).isoformat()
        state: dict = {}
        for i in range(count_within_hour):
            state[f"recent_{i}"] = {"tailored_at": recent, "run_id": str(i)}
        for i in range(count_outside):
            state[f"old_{i}"] = {"tailored_at": old, "run_id": str(i + 100)}
        return state

    def test_count_zero(self):
        now = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(tailored_state.count_in_window({}, hours=1, now=now), 0)

    def test_only_recent_counted(self):
        now = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
        state = self._state_with(count_within_hour=3, count_outside=20)
        self.assertEqual(tailored_state.count_in_window(state, hours=1, now=now), 3)

    def test_malformed_timestamps_skipped(self):
        now = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
        state = {
            "ok":   {"tailored_at": (now - timedelta(minutes=10)).isoformat()},
            "bad1": {"tailored_at": "not a date"},
            "bad2": {"tailored_at": None},
            "bad3": {},  # no tailored_at
        }
        self.assertEqual(tailored_state.count_in_window(state, hours=1, now=now), 1)

    def test_check_rate_limit_below_cap(self):
        tmp = Path(tempfile.mkdtemp()) / "s.json"
        tailored_state.save(tmp, self._state_with(count_within_hour=5))
        allowed, count = tailored_state.check_rate_limit(tmp, max_per_hour=10)
        self.assertTrue(allowed)
        # count is computed at "now" (real), not the fixture time, so old entries
        # are filtered — but the fixture creates "recent" entries 30min before
        # 2026-05-20 12:00, which is far in the past from real "now". So count is 0.
        self.assertEqual(count, 0)

    def test_check_rate_limit_at_cap(self):
        tmp = Path(tempfile.mkdtemp()) / "s.json"
        # Build a state where all entries are within the last hour of *real* now
        now = datetime.now(timezone.utc)
        recent_ts = (now - timedelta(minutes=15)).isoformat()
        state = {f"k_{i}": {"tailored_at": recent_ts, "run_id": str(i)} for i in range(10)}
        tailored_state.save(tmp, state)
        allowed, count = tailored_state.check_rate_limit(tmp, max_per_hour=10)
        self.assertFalse(allowed)
        self.assertEqual(count, 10)

    def test_check_rate_limit_well_over_cap(self):
        tmp = Path(tempfile.mkdtemp()) / "s.json"
        now = datetime.now(timezone.utc)
        recent_ts = (now - timedelta(minutes=15)).isoformat()
        state = {f"k_{i}": {"tailored_at": recent_ts} for i in range(25)}
        tailored_state.save(tmp, state)
        allowed, count = tailored_state.check_rate_limit(tmp, max_per_hour=10)
        self.assertFalse(allowed)
        self.assertEqual(count, 25)


if __name__ == "__main__":
    unittest.main()
