"""Application-neutral, secret-minimizing JSONL audit events."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SENSITIVE_PATTERNS = (
    re.compile(r"(?i)\b(password|passwd|api[_ -]?key|access[_ -]?token|auth[_ -]?token|token|secret)\s*[:=]\s*\S+"),
    re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
)


def _redact(value: str) -> str:
    for pattern in SENSITIVE_PATTERNS:
        value = pattern.sub("[REDACTED]", value)
    return value


class AuditLogger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, **event: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"timestamp": datetime.now(UTC).isoformat(), **self._redact_event(event)}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")

    def _redact_event(self, value: Any) -> Any:
        if isinstance(value, str):
            return _redact(value)
        if isinstance(value, dict):
            return {key: self._redact_event(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._redact_event(item) for item in value]
        return value
