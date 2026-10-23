"""Capture adapter failures that otherwise masquerade as successful empty feeds."""
import logging
import re
import threading

class AdapterDiagnostics(logging.Handler):
    def __init__(self):
        super().__init__(); self.thread = threading.get_ident(); self.failed = False
    def emit(self, record):
        if record.thread == self.thread and record.name.startswith("src.adapters"):
            message = record.getMessage()
            if record.levelno >= logging.WARNING or re.search(r"HTTP [45]\d\d|public API disabled", message):
                self.failed = True

def fetch_with_health(source, label, fn, arg):
    diagnostics = AdapterDiagnostics()
    logger = logging.getLogger("src.adapters")
    logger.addHandler(diagnostics)
    try:
        jobs = fn(arg)
        status = "partial" if jobs and diagnostics.failed else "failed" if diagnostics.failed else "ok" if jobs else "empty"
        measured = getattr(jobs, "coverage", {})
        if measured.get("enumeration_complete") is False and status not in {"failed", "partial"}:
            status = "partial" if jobs else "failed"
        return jobs, {"source": source, "label": label, "status": status, "jobs": len(jobs), **measured}
    except Exception:
        return [], {"source": source, "label": label, "status": "failed", "jobs": 0}
    finally:
        logger.removeHandler(diagnostics)
