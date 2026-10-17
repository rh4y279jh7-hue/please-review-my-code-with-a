"""Guardião de integridade: bloqueia mudanças que enfraquecem o projeto.

Lê apenas o diff (dados) entre a base e o head de um PR; nunca executa código
do PR. No CI roda a versão deste arquivo que está na base, para que um PR não
consiga desligar a própria verificação (AGENTS.md §15).

Saída: 0 sem bloqueios; 1 com bloqueio. Achados "sensíveis" podem ser liberados
de forma deliberada pelo dono com o rótulo ``integridade-aprovada``; segredos
nunca são liberados.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field

OVERRIDE_LABEL = "integridade-aprovada"
GUARD_FILES = ("scripts/integrity_guard.py", ".github/workflows/integrity.yml")

SKIP_RE = re.compile(
    r"pytest\.mark\.(skip|skipif|xfail)|pytest\.(skip|xfail)\(|unittest\.skip|@skip\b"
)
SUPPRESS_RE = re.compile(
    r"#\s*noqa|#\s*type:\s*ignore|#\s*pragma:\s*no cover|#\s*fmt:\s*(off|skip)"
)
TEST_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+(test_\w+)")
ASSERT_RE = re.compile(r"^\s*assert\b|pytest\.raises\(")
SECRET_RE = re.compile(
    r"sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}"
    r"|gh[pousr]_[A-Za-z0-9]{36}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
SECURITY_TOKENS = (
    "compare_digest",
    "authenticate",
    "authorized(",
    "HIVEMIND_API_TOKEN",
    "SecretStr",
    "hide_input_in_errors",
    "max_length",
    "min_length",
    "ge=",
    "le=",
    "maxlen",
    "maxsize",
    "raise ValueError",
    "timeout",
    "code=1008",
    "origin",
    "read_only",
    "cap_drop",
    "no-new-privileges",
    "127.0.0.1",
    "USER ",
    "permissions:",
)
QUALITY_SECTIONS = (
    "tool.ruff",
    "tool.mypy",
    "tool.black",
    "tool.pytest",
    "tool.coverage",
)
PROTECTED_DOCS = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")
PROTECTED_DIRS = (".codex/agents/", ".claude/agents/")


@dataclass
class FileDiff:
    """Linhas removidas e adicionadas de um arquivo, com números da base."""

    path: str
    status: str = "M"
    removed: list[tuple[int, str]] = field(default_factory=list)
    added: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Finding:
    """Um achado; ``hard`` nunca pode ser liberado por rótulo."""

    rule: str
    path: str
    detail: str
    hard: bool = False


def parse_diff(text: str) -> list[FileDiff]:
    """Interpreta ``git diff --unified=0 --no-color``."""
    files: list[FileDiff] = []
    current: FileDiff | None = None
    old_line = 0
    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            path = raw.split(" b/", 1)[1] if " b/" in raw else raw.split()[-1]
            current = FileDiff(path=path)
            files.append(current)
        elif current is None:
            continue
        elif raw.startswith("deleted file mode"):
            current.status = "D"
        elif raw.startswith("new file mode"):
            current.status = "A"
        elif raw.startswith("rename from "):
            current.status = "R:" + raw[len("rename from ") :]
        elif raw.startswith("--- a/") and current.status == "D":
            current.path = raw[len("--- a/") :]
        elif raw.startswith(("--- ", "+++ ", "index ", "similarity ", "rename to ")):
            continue
        elif raw.startswith("@@"):
            match = re.match(r"@@ -(\d+)", raw)
            old_line = int(match.group(1)) if match else 0
        elif raw.startswith("-"):
            current.removed.append((old_line, raw[1:]))
            old_line += 1
        elif raw.startswith("+"):
            current.added.append(raw[1:])
    return files


def _section_of(base_text: str, line_no: int, header: re.Pattern[str]) -> str:
    """Retorna o último cabeçalho antes da linha (1-indexada) da base."""
    section = ""
    for number, line in enumerate(base_text.splitlines(), start=1):
        if number > line_no:
            break
        found = header.match(line)
        if found:
            section = found.group(1)
    return section


def analyze(
    files: list[FileDiff],
    base_text: dict[str, str] | None = None,
    bootstrap: bool = False,
) -> list[Finding]:
    """Aplica as regras de integridade a um diff já interpretado."""
    base_text = base_text or {}
    readded = {line.strip() for f in files for line in f.added if line.strip()}
    findings: list[Finding] = []

    def removed_for_real(diff: FileDiff) -> list[tuple[int, str]]:
        # Linhas apenas movidas ou reformatadas reaparecem idênticas no diff.
        return [
            (n, t) for n, t in diff.removed if t.strip() and t.strip() not in readded
        ]

    for diff in files:
        path, gone = diff.path, removed_for_real(diff)
        is_test = path.startswith("tests/") or "/tests/" in path

        for line in diff.added:
            if SECRET_RE.search(line):
                findings.append(Finding("segredo", path, "credencial adicionada", True))

        if path in GUARD_FILES and not (bootstrap and diff.status == "A"):
            findings.append(
                Finding("guardiao", path, "o próprio guardião foi alterado")
            )
            continue
        if path.startswith(".github/workflows/") and path not in GUARD_FILES:
            findings.append(Finding("ci", path, "workflow de CI alterado"))

        if is_test:
            if diff.status == "D" or diff.status.startswith("R:"):
                findings.append(
                    Finding("teste-apagado", path, "arquivo de teste removido")
                )
            kept = {m.group(1) for line in diff.added if (m := TEST_DEF_RE.match(line))}
            for _, text in diff.removed:
                match = TEST_DEF_RE.match(text)
                if match and match.group(1) not in kept:
                    findings.append(
                        Finding(
                            "teste-apagado", path, f"teste {match.group(1)} removido"
                        )
                    )
            for line in diff.added:
                if SKIP_RE.search(line):
                    findings.append(Finding("teste-pulado", path, line.strip()[:100]))
            lost = sum(1 for _, text in gone if ASSERT_RE.search(text))
            gained = sum(1 for line in diff.added if ASSERT_RE.search(line))
            if lost > gained:
                findings.append(
                    Finding(
                        "assercoes-reduzidas", path, f"-{lost} asserções, +{gained}"
                    )
                )

        if path.endswith(".py"):
            for line in diff.added:
                if SUPPRESS_RE.search(line):
                    findings.append(Finding("supressao", path, line.strip()[:100]))

        if path == "pyproject.toml":
            header = re.compile(r"^\[([^\]]+)\]")
            for number, text in gone:
                section = _section_of(base_text.get(path, ""), number, header)
                if section.startswith(QUALITY_SECTIONS):
                    findings.append(
                        Finding(
                            "qualidade-afrouxada", path, f"[{section}] -{text.strip()}"
                        )
                    )
            if any(t.strip().startswith('"') for _, t in diff.removed) or any(
                a.strip().startswith('"') for a in diff.added
            ):
                findings.append(
                    Finding("dependencias", path, "lista de dependências mudou")
                )
        if path == "uv.lock":
            findings.append(Finding("dependencias", path, "lockfile alterado"))

        if path in PROTECTED_DOCS or path.startswith(PROTECTED_DIRS):
            if diff.status == "D":
                findings.append(
                    Finding("regras-removidas", path, "arquivo de regras apagado")
                )
            for _, text in gone:
                findings.append(
                    Finding("regras-removidas", path, f"-{text.strip()[:100]}")
                )

        security_scope = (
            path.startswith("src/")
            or path.startswith("docker-compose")
            or path == "Dockerfile"
            or path.startswith(".github/workflows/")
        )
        if security_scope:
            for _, text in gone:
                if text.strip().startswith("#"):
                    continue
                if any(token in text for token in SECURITY_TOKENS):
                    findings.append(
                        Finding("controle-removido", path, f"-{text.strip()[:100]}")
                    )
    return findings


def decide(findings: list[Finding], labels: set[str]) -> int:
    """0 se pode seguir; 1 se bloqueia."""
    if any(f.hard for f in findings):
        return 1
    if findings and OVERRIDE_LABEL not in labels:
        return 1
    return 0


def render(findings: list[Finding], labels: set[str], code: int) -> str:
    """Relatório em Markdown para o log e o resumo do job."""
    lines = ["## Guardião de integridade (AGENTS.md §15)", ""]
    if not findings:
        lines.append("Nenhum enfraquecimento detectado.")
        return "\n".join(lines)
    status = "BLOQUEADO" if code else f"LIBERADO pelo rótulo `{OVERRIDE_LABEL}`"
    lines += [f"**{status}**", "", "| Regra | Arquivo | Detalhe |", "|---|---|---|"]
    for f in findings:
        rule = f"{f.rule} (não liberável)" if f.hard else f.rule
        detail = f.detail.replace("|", "\\|")
        lines.append(f"| {rule} | `{f.path}` | {detail} |")
    if code and not any(f.hard for f in findings):
        lines += [
            "",
            f"Se a mudança for deliberada e **fortalecer** o projeto, o dono "
            f"aplica o rótulo `{OVERRIDE_LABEL}`. Agentes nunca aplicam esse rótulo.",
        ]
    return "\n".join(lines)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def main(argv: list[str] | None = None) -> int:
    """Executa o guardião sobre ``base...head`` no repositório atual."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--labels", default="")
    args = parser.parse_args(argv)
    labels = {label.strip() for label in args.labels.split(",") if label.strip()}
    merge_base = _git("merge-base", args.base, args.head).strip()
    files = parse_diff(
        _git("diff", "--unified=0", "--no-color", "-M", f"{merge_base}..{args.head}")
    )
    base_text: dict[str, str] = {}
    for diff in files:
        if diff.path in ("pyproject.toml", *PROTECTED_DOCS):
            try:
                base_text[diff.path] = _git("show", f"{merge_base}:{diff.path}")
            except subprocess.CalledProcessError:
                base_text[diff.path] = ""
    try:
        _git("cat-file", "-e", f"{merge_base}:{GUARD_FILES[0]}")
        bootstrap = False
    except subprocess.CalledProcessError:
        bootstrap = True
    findings = analyze(files, base_text, bootstrap=bootstrap)
    code = decide(findings, labels)
    report = render(findings, labels, code)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(report + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
