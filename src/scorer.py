"""Rule-based job scoring.

Takes a Job dict and a role config dict, returns (score, reasons) where:
  - score is 0-100
  - reasons is a list of strings describing what contributed

This is intentionally simple — no ML, no LLM. The goal is to ship v0 fast
and learn what the regex misses, then upgrade to AI scoring in v0.1.
"""
from __future__ import annotations
import re
from typing import Any


def _matches_any(text: str, patterns: list[str]) -> bool:
    if not text or not patterns:
        return False
    text_low = text.lower()
    for p in patterns:
        try:
            if re.search(p, text_low, re.IGNORECASE):
                return True
        except re.error:
            if p.lower() in text_low:
                return True
    return False


def _matches_substring(text: str, needles: list[str]) -> bool:
    if not text or not needles:
        return False
    text_low = text.lower()
    return any(n.lower() in text_low for n in needles)


_US_LOCATION_HINTS = [
    "remote", "united states", "usa", " us", "us-", "u.s.",
    "ca", "ny", "tx", "wa", "ma", "il", "ga", "co", "fl", "or", "az", "nc", "va",
    "san francisco", "new york", "boston", "seattle", "austin", "chicago",
    "denver", "atlanta", "los angeles", "portland", "miami", "dallas", "houston",
    "raleigh", "charlotte", "philadelphia", "washington dc", "san jose",
    "san diego", "phoenix", "minneapolis", "pittsburgh", "detroit",
    "california", "new york", "texas", "washington", "massachusetts",
    "illinois", "georgia", "colorado", "florida", "oregon", "arizona",
]


def _is_us_or_remote(location: str) -> bool:
    if not location:
        return False
    low = " " + location.lower() + " "
    return any(hint in low for hint in _US_LOCATION_HINTS)


def score_job(job: dict, config: dict) -> tuple[int, list[str]]:
    """Compute a 0-100 match score for a job against the role config.

    Returns (score, reasons). A job below `excluded_company_penalty` weight
    or matching an excluded title pattern returns score=0 with a single reason.
    """
    title = job.get("title", "") or ""
    location = job.get("location", "") or ""
    company = job.get("company", "") or ""
    reasons: list[str] = []

    excluded_titles = config.get("excluded_title_patterns", []) or []
    if _matches_any(title, excluded_titles):
        return 0, ["excluded: title matches junior/intern/etc."]

    excluded_companies = config.get("excluded_companies", []) or []
    if company and company.lower() in (c.lower() for c in excluded_companies):
        return 0, [f"excluded: company {company} on blocklist"]

    weights = config.get("score_weights", {}) or {}
    title_patterns = config.get("title_patterns", []) or []
    if not _matches_any(title, title_patterns):
        return 0, ["no title match"]
    score = int(weights.get("title_match", 30))
    reasons.append(f"title match (+{weights.get('title_match', 30)})")

    seniority = config.get("seniority_keywords", {}) or {}
    sorted_buckets = sorted(
        seniority.items(),
        key=lambda kv: int(weights.get(kv[0], 0)),
        reverse=True,
    )
    for bucket_name, patterns in sorted_buckets:
        if _matches_any(title, patterns):
            bonus = int(weights.get(bucket_name, 0))
            if bonus > 0:
                score += bonus
                reasons.append(f"{bucket_name.replace('_', ' ')} (+{bonus})")
            break

    excluded_locations = config.get("excluded_locations", []) or []
    preferred_locations = config.get("preferred_locations", []) or []
    if location and _matches_substring(location, excluded_locations):
        penalty = int(weights.get("excluded_location_penalty", -40))
        score += penalty
        reasons.append(f"excluded location: {location} ({penalty})")
    elif location and _matches_substring(location, preferred_locations):
        bonus = int(weights.get("preferred_location_bonus", 10))
        score += bonus
        reasons.append(f"preferred location: {location} (+{bonus})")
    if location and _is_us_or_remote(location):
        bonus = int(weights.get("remote_or_us", 20))
        score += bonus
        reasons.append(f"remote/US eligible (+{bonus})")

    score = max(0, min(100, score))
    return score, reasons


def is_recent(posted_at_iso: str, max_age_days: int, now=None) -> bool:
    """True if posted_at is within max_age_days of now.

    Empty / unparseable posted_at returns True — we don't know the age, so we
    keep the job rather than penalize missing data. Future-dated stamps also
    return True (treat as "just posted").
    """
    if not posted_at_iso or max_age_days <= 0:
        return True
    from datetime import datetime, timezone, timedelta
    try:
        dt = datetime.fromisoformat(str(posted_at_iso).replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return True
    n = now or datetime.now(timezone.utc)
    cutoff = n - timedelta(days=int(max_age_days))
    return dt >= cutoff


def classify(score: int, config: dict) -> str:
    """Bucket a score into 'hot' | 'standard' | 'low' | 'drop'."""
    hot = int(config.get("hot_match_threshold", 85))
    notify = int(config.get("notification_threshold", 70))
    if score >= hot:
        return "hot"
    if score >= notify:
        return "standard"
    if score > 0:
        return "low"
    return "drop"
