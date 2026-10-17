"""Limites fixos de caracteres para respostas e referências não confiáveis."""

MAX_OUTPUT_CHARS = 16_000
MAX_CONTEXT_ENTRIES = 24
MAX_CONTEXT_ENTRY_CHARS = 8_000
MAX_CONTEXT_CHARS = 32_000
_TRUNCATION = "\n[truncated for safety]"


def bounded_text(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """Mantém no máximo ``limit`` caracteres e sinaliza conteúdo cortado."""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(_TRUNCATION))] + _TRUNCATION[:limit]


def bounded_context(
    context: list[str],
    *,
    max_entries: int = MAX_CONTEXT_ENTRIES,
    max_chars: int = MAX_CONTEXT_CHARS,
) -> list[str]:
    """Copia referências dentro dos limites por trecho, quantidade e tamanho."""
    result: list[str] = []
    remaining = max_chars
    for entry in context[:max_entries]:
        if remaining <= 0:
            break
        excerpt = bounded_text(entry, min(MAX_CONTEXT_ENTRY_CHARS, remaining))
        result.append(excerpt)
        remaining -= len(excerpt)
    return result
