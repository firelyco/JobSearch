"""Candidate intake only. Career-fit verdicts require the private fitment skill."""
import re

def pmo_leadership(title):
    title = title or ''
    return bool(re.search(r'\b(?:pmo|(?:program|project|portfolio)\s+management\s+office)\b', title, re.I)
                and re.search(r'\b(?:vice\s+president|vp|head|director|manager|chief)\b', title, re.I))
