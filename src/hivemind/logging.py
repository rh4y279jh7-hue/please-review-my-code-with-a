"""Registro operacional estruturado, sem prompts, respostas ou credenciais."""

import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """Formata somente mensagens operacionais fornecidas explicitamente."""

    def format(self, record: logging.LogRecord) -> str:
        """Serializa um evento mínimo e omite detalhes de exceções."""
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
    """Configura os registros da aplicação sem alterar os SDKs externos."""
    logger = logging.getLogger("hivemind")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.handlers = [handler]
