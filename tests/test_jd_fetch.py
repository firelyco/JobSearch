"""Tests for HTML stripping and the get_jd_text dispatcher.

Run with: python -m unittest tests.test_jd_fetch
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import unittest
from unittest.mock import patch

from src.jd_fetch import strip_html, get_jd_text


class TestStripHtml(unittest.TestCase):

    def test_empty(self):
        self.assertEqual(strip_html(""), "")
        self.assertEqual(strip_html(None), "")

    def test_plain_text_passthrough(self):
        self.assertEqual(strip_html("just plain text"), "just plain text")

    def test_strips_tags(self):
        self.assertEqual(strip_html("<p>hello</p>"), "hello")
        self.assertEqual(strip_html("<b>bold</b> text"), "bold text")

    def test_block_tags_become_newlines(self):
        out = strip_html("<p>one</p><p>two</p>")
        self.assertIn("one", out)
        self.assertIn("two", out)
        self.assertIn("\n", out)

    def test_list_items(self):
        html = "<ul><li>first</li><li>second</li></ul>"
        out = strip_html(html)
        self.assertIn("first", out)
        self.assertIn("second", out)

    def test_drops_script_style(self):
        html = "<p>visible</p><script>alert('bad')</script><style>p{color:red}</style>"
        out = strip_html(html)
        self.assertIn("visible", out)
        self.assertNotIn("alert", out)
        self.assertNotIn("color:red", out)

    def test_unescapes_entities(self):
        self.assertEqual(strip_html("AT&amp;T"), "AT&T")
        self.assertEqual(strip_html("&lt;not a tag&gt;"), "<not a tag>")

    def test_collapses_whitespace(self):
        out = strip_html("<p>too      many   spaces</p>")
        self.assertEqual(out, "too many spaces")

    def test_collapses_excess_newlines(self):
        html = "<p>a</p><br><br><br><br><p>b</p>"
        out = strip_html(html)
        # at most one blank line between blocks
        self.assertNotIn("\n\n\n", out)

    def test_realistic_jd_snippet(self):
        html = """
        <div>
          <h2>About the role</h2>
          <p>We are looking for a <b>Senior TPM</b> to drive...</p>
          <ul><li>5+ years TPM</li><li>FAANG-tier scale</li></ul>
        </div>
        """
        out = strip_html(html)
        self.assertIn("About the role", out)
        self.assertIn("Senior TPM", out)
        self.assertIn("5+ years TPM", out)
        self.assertIn("FAANG-tier scale", out)


class TestGetJdText(unittest.TestCase):

    def test_unknown_source_returns_empty(self):
        self.assertEqual(get_jd_text({"source": "indeed"}), "")

    def test_dispatches_greenhouse(self):
        job = {"source": "greenhouse", "company": "stripe", "id": "123"}
        with patch("src.jd_fetch.greenhouse.fetch_detail", return_value="<p>hello</p>") as m:
            self.assertEqual(get_jd_text(job), "hello")
            m.assert_called_once_with(job)

    def test_dispatches_lever(self):
        job = {"source": "lever", "company": "netflix", "id": "abc"}
        with patch("src.jd_fetch.lever.fetch_detail", return_value="plain lever text"):
            self.assertEqual(get_jd_text(job), "plain lever text")

    def test_dispatches_ashby(self):
        job = {"source": "ashby", "company": "openai", "id": "x"}
        with patch("src.jd_fetch.ashby.fetch_detail", return_value="<div>ashby</div>"):
            self.assertEqual(get_jd_text(job), "ashby")

    def test_dispatches_workday(self):
        job = {"source": "workday", "company": "nvidia", "id": "/job/x"}
        with patch("src.jd_fetch.workday.fetch_detail", return_value="<p>workday body</p>"):
            self.assertEqual(get_jd_text(job), "workday body")

    def test_empty_fetch_returns_empty(self):
        job = {"source": "greenhouse", "company": "stripe", "id": "123"}
        with patch("src.jd_fetch.greenhouse.fetch_detail", return_value=""):
            self.assertEqual(get_jd_text(job), "")


if __name__ == "__main__":
    unittest.main()
