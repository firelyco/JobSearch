"""Schema tests for config/profile.json.example.

These tests guard the example file shape so future edits can't quietly
break tailor.py (which assumes specific fields like person.name,
experience[].achievements[].id, etc.).

Run with: python -m unittest tests.test_profile_schema
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
import unittest
from pathlib import Path


EXAMPLE_PATH = Path(__file__).parent.parent / "config" / "profile.json.example"


class TestProfileExampleSchema(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.profile = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))

    def test_file_is_valid_json(self):
        self.assertIsInstance(self.profile, dict)

    def test_top_level_keys(self):
        for key in ("schema_version", "person", "experience", "skills", "education", "preferences"):
            self.assertIn(key, self.profile, f"missing top-level key {key!r}")

    def test_person_required_fields(self):
        person = self.profile["person"]
        for key in ("name", "email", "location", "linkedin", "headline_variants"):
            self.assertIn(key, person)
        self.assertIsInstance(person["headline_variants"], dict)
        # at least the default variant must exist
        self.assertIn("default", person["headline_variants"])

    def test_experience_is_nonempty_list(self):
        exp = self.profile["experience"]
        self.assertIsInstance(exp, list)
        self.assertGreater(len(exp), 0, "example must have at least one experience entry")

    def test_each_experience_has_required_fields(self):
        for i, e in enumerate(self.profile["experience"]):
            for key in ("id", "company", "title", "start", "achievements"):
                self.assertIn(key, e, f"experience[{i}] missing {key!r}")
            self.assertIsInstance(e["achievements"], list)

    def test_each_achievement_has_id(self):
        """tailor.py groups bullets by achievement_id; every achievement
        in the profile must have an id or it can't be referenced."""
        for i, e in enumerate(self.profile["experience"]):
            for j, ach in enumerate(e.get("achievements", [])):
                self.assertIn("id", ach, f"experience[{i}].achievements[{j}] missing id")
                self.assertTrue(ach["id"], f"experience[{i}].achievements[{j}].id is empty")

    def test_achievement_ids_are_globally_unique(self):
        """A bullet's achievement_id is the only key linking it back to source —
        duplicates would silently merge in the renderer."""
        seen: dict[str, str] = {}
        for e in self.profile["experience"]:
            exp_id = e.get("id", "?")
            for ach in e.get("achievements", []):
                aid = ach.get("id", "")
                if not aid:
                    continue
                if aid in seen:
                    self.fail(f"duplicate achievement id {aid!r} in {exp_id} and {seen[aid]}")
                seen[aid] = exp_id

    def test_experience_ids_are_unique(self):
        ids = [e.get("id", "") for e in self.profile["experience"]]
        self.assertEqual(len(ids), len(set(ids)), f"duplicate experience ids: {ids}")

    def test_skills_is_dict_of_lists(self):
        skills = self.profile["skills"]
        self.assertIsInstance(skills, dict)
        for category, items in skills.items():
            self.assertIsInstance(items, list, f"skills.{category} should be a list")

    def test_preferences_has_target_titles(self):
        prefs = self.profile["preferences"]
        self.assertIn("target_titles", prefs)
        self.assertIsInstance(prefs["target_titles"], list)
        self.assertGreater(len(prefs["target_titles"]), 0)

    def test_renderer_accepts_example_shape(self):
        """End-to-end: feed the example through render_docx with a stub
        tailor_result. Catches any latent renderer assumption the example
        violates (e.g., missing location field that the renderer doesn't
        gracefully handle)."""
        try:
            import docx  # noqa: F401
        except ImportError:
            self.skipTest("python-docx not installed locally")

        import tempfile
        from src.render_docx import render_resume

        # Pick the first achievement id we find
        first_ach_id = self.profile["experience"][0]["achievements"][0]["id"]
        tailor_result = {
            "headline": "Test headline",
            "bullets": [{
                "achievement_id": first_ach_id,
                "text": "Test bullet for schema check",
                "verified": True,
                "reason": "",
            }],
            "gaps": [],
        }
        tmp = Path(tempfile.mkdtemp()) / "schema_check.docx"
        out = render_resume(self.profile, tailor_result, tmp)
        self.assertTrue(out.exists())


if __name__ == "__main__":
    unittest.main()
