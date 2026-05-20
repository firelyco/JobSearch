"""Common types and utilities shared across ATS adapters.

Every adapter returns a list of dicts with this normalized shape:

    {
      "id": str,            # unique within (source, company)
      "source": str,        # 'greenhouse' | 'lever' | 'ashby' | 'workday'
      "company": str,       # human-readable company name
      "title": str,
      "location": str,
      "url": str,
      "posted_at": str,     # ISO 8601, or empty string if unavailable
      "updated_at": str,    # ISO 8601, or empty string
    }

The unique key for dedupe is (source, company, id).
"""
from __future__ import annotations
import logging
from typing import TypedDict

log = logging.getLogger(__name__)


class Job(TypedDict):
    id: str
    source: str
    company: str
    title: str
    location: str
    url: str
    posted_at: str
    updated_at: str


def safe_str(value, default: str = "") -> str:
    """Coerce any value to str, returning default for None / empty."""
    if value is None:
        return default
    try:
        result = str(value).strip()
        return result if result else default
    except Exception:
        return default
