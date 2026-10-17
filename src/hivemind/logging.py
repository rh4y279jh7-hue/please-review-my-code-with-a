"""Structured operational logging without prompts, model output or credentials."""

import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """Format only explicitly supplied operational log messages."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize a minimal log event, omitting exception payloads."""
        return json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "event": record.getMessage(),
            },
            ensure_ascii=False,
        )


def configure_logging() -> None:
    """Configure application logs without altering external SDK loggers."""
    logger = logging.getLogger("hivemind")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.handlers = [handler]
