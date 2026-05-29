"""Job description fetching and HTML-to-text normalization.

The v0 poller stores only listing metadata (title, location, url, posted_at).
Tailoring needs the full JD body. This module dispatches to the right adapter's
fetch_detail() based on the job's `source` field, then strips HTML to plain text
using stdlib html.parser (no BeautifulSoup dependency).

Public API:
  get_jd_text(job) -> str   # cleaned plain text, "" on failure
  strip_html(html) -> str   # used by get_jd_text, exported for testing
"""
from __future__ import annotations
import logging
import re
from html import unescape
from html.parser import HTMLParser

from src.adapters import greenhouse, lever, ashby, workday, amazon_jobs

log = logging.getLogger(__name__)

_BLOCK_TAGS = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}
_SKIP_TAGS = {"script", "style"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        joined = "".join(self._parts)
        joined = unescape(joined)
        # collapse 3+ newlines to 2, and runs of inline whitespace to a single space
        joined = re.sub(r"[ \t]+", " ", joined)
        joined = re.sub(r"\n[ \t]+", "\n", joined)
        joined = re.sub(r"\n{3,}", "\n\n", joined)
        return joined.strip()


def strip_html(html_text: str) -> str:
    """Convert HTML to plain text. Safe on plain-text input (round-trips)."""
    if not html_text:
        return ""
    extractor = _TextExtractor()
    try:
        extractor.feed(html_text)
        extractor.close()
    except Exception as e:
        log.warning("html strip failed: %s", e)
        return html_text  # fall back to raw input
    return extractor.get_text()


_ADAPTER_MODULES = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "workday": workday,
    "amazon_jobs": amazon_jobs,
}


def get_jd_text(job: dict) -> str:
    """Fetch the full job description for a job and return cleaned plain text.

    Returns "" if the source is unknown, the fetch fails, or the response is empty.
    Callers must check truthiness before passing to the LLM.

    Note: dispatches via module attribute lookup (not a captured function ref) so
    tests can patch adapter.fetch_detail without monkey-patching this module.
    """
    source = job.get("source", "")
    module = _ADAPTER_MODULES.get(source)
    if module is None:
        log.warning("get_jd_text: unknown source %r", source)
        return ""
    raw = module.fetch_detail(job)
    if not raw:
        return ""
    return strip_html(raw)
