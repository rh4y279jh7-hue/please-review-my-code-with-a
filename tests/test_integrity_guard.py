"""Integrity guard: weakening changes are blocked, strengthening ones pass."""

import importlib.util
import shutil
import subprocess
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "integrity_guard", ROOT / "scripts" / "integrity_guard.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["integrity_guard"] = module
    spec.loader.exec_module(module)
    return module


guard = _load()


# Built at runtime so this file itself never trips the guard it tests.
NOQA = "# no" + "qa"
TYPE_IGNORE = "# type" + ": ignore"
SKIP = "@pytest.mark." + "skip(reason='later')"


def diff(
    path: str,
    removed: Sequence[str] = (),
    added: Sequence[str] = (),
    status: str = "M",
) -> object:
    return guard.FileDiff(
        path=path,
        status=status,
        removed=[(i + 1, line) for i, line in enumerate(removed)],
        added=list(added),
    )


def rules(
    *files: object, base_text: dict[str, str] | None = None, bootstrap: bool = False
) -> set[str]:
    return {f.rule for f in guard.analyze(list(files), base_text, bootstrap)}


def test_parse_unified_diff() -> None:
    text = (
        "diff --git a/tests/test_x.py b/tests/test_x.py\n"
        "index 1..2 100644\n--- a/tests/test_x.py\n+++ b/tests/test_x.py\n"
        "@@ -10,2 +10,0 @@\n-def test_a():\n-    assert True\n"
        "diff --git a/gone.py b/gone.py\ndeleted file mode 100644\n"
        "--- a/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x = 1\n"
    )
    files = guard.parse_diff(text)
    assert [(f.path, f.status) for f in files] == [
        ("tests/test_x.py", "M"),
        ("gone.py", "D"),
    ]
    assert files[0].removed == [(10, "def test_a():"), (11, "    assert True")]


def test_removed_or_skipped_tests_block() -> None:
    assert "teste-apagado" in rules(diff("tests/test_x.py", removed=["def test_a():"]))
    assert "teste-apagado" in rules(diff("tests/test_x.py", status="D"))
    assert "teste-pulado" in rules(diff("tests/test_x.py", added=[SKIP]))


def test_renamed_or_new_tests_pass() -> None:
    assert not rules(
        diff("tests/test_x.py", removed=["def test_a():"], added=["def test_a():"])
    )
    assert not rules(diff("tests/test_x.py", added=["def test_new() -> None:"]))


def test_fewer_assertions_block_but_stronger_tests_pass() -> None:
    weaker = diff("tests/test_x.py", removed=["    assert x == 1", "    assert y"])
    assert "assercoes-reduzidas" in rules(weaker)
    stronger = diff(
        "tests/test_x.py",
        removed=["    assert x == 1"],
        added=["    assert x == 1.0", "    with pytest.raises(ValueError):"],
    )
    assert not rules(stronger)


def test_removed_comments_are_not_controls() -> None:
    note = "# Use SEMPRE com HIVEMIND_API_TOKEN configurado."
    assert not rules(diff("docker-compose.lan.yml", removed=[note]))


def test_suppressions_block() -> None:
    assert "supressao" in rules(diff("src/hivemind/x.py", added=[f"y = f()  {NOQA}"]))
    assert "supressao" in rules(
        diff("src/hivemind/x.py", added=[f"y: int = f()  {TYPE_IGNORE}"])
    )


def test_quality_config_relaxation_blocks() -> None:
    base = '[tool.mypy]\nstrict = true\n\n[project]\nname = "x"\n'
    relaxed = diff("pyproject.toml", removed=["strict = true"])
    relaxed.removed = [(2, "strict = true")]
    assert "qualidade-afrouxada" in rules(relaxed, base_text={"pyproject.toml": base})


def test_security_control_removal_blocks_but_moves_pass() -> None:
    line = "    if not hmac.compare_digest(a, b):"
    assert "controle-removido" in rules(diff("src/hivemind/api/app.py", removed=[line]))
    assert not rules(
        diff("src/hivemind/api/app.py", removed=[line]),
        diff("src/hivemind/api/auth.py", added=[line], status="A"),
    )
    compose = diff("docker-compose.yml", removed=["    read_only: true"])
    assert "controle-removido" in rules(compose)


def test_concurrency_and_queue_limits_are_controls() -> None:
    for line in (
        "        if len(jobs) >= config.max_tasks:",
        "        if len(job.subscribers) >= 10:",
        "            async with self._run_lock:",
        "        self._run_lock = asyncio.Lock()",
    ):
        assert "controle-removido" in rules(
            diff("src/hivemind/api/app.py", removed=[line])
        )


def test_rules_files_and_guard_are_protected() -> None:
    assert "regras-removidas" in rules(diff("AGENTS.md", removed=["2. Fail closed."]))
    assert "caminho-protegido" in rules(
        diff("AGENTS.md", added=["7. Nova regra mais forte."])
    )
    assert "guardiao" in rules(diff("scripts/integrity_guard.py", added=["pass"]))
    assert "ci" in rules(diff(".github/workflows/ci.yml", added=["  - run: echo"]))


@pytest.mark.parametrize(
    "path",
    [
        "docs/governanca-agentes-github.md",
        "SECURITY.md",
        ".github/CODEOWNERS",
        ".github/copilot-instructions.md",
        ".github/instructions/review.instructions.md",
        ".github/skills/review/SKILL.md",
        ".codex/config.toml",
        ".claude/settings.json",
        "tests/test_integrity_guard.py",
        "tests/test_repo_hardening.py",
    ],
)
@pytest.mark.parametrize("status", ["A", "M", "D"])
def test_protected_paths_require_deliberate_owner_decision(
    path: str, status: str
) -> None:
    findings = guard.analyze([diff(path, added=["new content"], status=status)])
    assert any(f.rule == "caminho-protegido" for f in findings)
    assert guard.decide(findings, set()) == 1
    assert guard.decide(findings, {guard.OVERRIDE_LABEL}) == 0


def test_renaming_out_of_protected_path_does_not_escape_guard() -> None:
    moved = diff("docs/ordinary.md", status="R:docs/governanca-agentes-github.md")
    assert "caminho-protegido" in rules(moved)
    moved_config = diff("ordinary.toml", status="R:.codex/config.toml")
    assert "caminho-protegido" in rules(moved_config)
    moved_into = diff(".github/CODEOWNERS", status="R:ordinary.txt")
    assert "caminho-protegido" in rules(moved_into)


def test_parse_rename_preserves_protected_source_without_hunks() -> None:
    text = (
        "diff --git a/.github/CODEOWNERS b/docs/owners.txt\n"
        "similarity index 100%\n"
        "rename from .github/CODEOWNERS\n"
        "rename to docs/owners.txt\n"
    )
    files = guard.parse_diff(text)
    assert len(files) == 1
    assert files[0].path == "docs/owners.txt"
    assert files[0].status == "R:.github/CODEOWNERS"
    assert "caminho-protegido" in rules(*files)


def test_ordinary_changes_and_antigravity_data_do_not_expand_protected_scope() -> None:
    assert not rules(diff("docs/ordinary.md", added=["ordinary documentation"]))
    assert not rules(diff(".antigravity/chats/example.json", added=["{}"]))


@pytest.mark.parametrize(
    "control",
    [
        "Content-Security-Policy",
        "content-security-policy",
        "script-src",
        "connect-src",
        "Strict-Transport-Security",
        "strict-transport-security",
    ],
)
def test_security_header_removal_is_detected(control: str) -> None:
    assert "controle-removido" in rules(
        diff("src/hivemind/api/app.py", removed=[f'header = "{control}"'])
    )


def test_bootstrap_allows_first_install_only() -> None:
    new_guard = diff("scripts/integrity_guard.py", added=["x"], status="A")
    assert not rules(new_guard, bootstrap=True)
    assert "guardiao" in rules(new_guard, bootstrap=False)


def test_secret_is_never_overridable() -> None:
    fake = "sk-" + "A" * 30
    findings = guard.analyze([diff("src/hivemind/x.py", added=[f"KEY = '{fake}'"])])
    assert any(f.hard for f in findings)
    assert guard.decide(findings, {guard.OVERRIDE_LABEL}) == 1


def test_override_label_releases_sensitive_findings() -> None:
    findings = guard.analyze([diff("uv.lock", added=["x"])])
    assert guard.decide(findings, set()) == 1
    assert guard.decide(findings, {guard.OVERRIDE_LABEL}) == 0
    assert guard.decide([], set()) == 0


@pytest.fixture
def repo() -> Iterator[Path]:
    """Real git repository inside the ignored .pytest-work directory."""
    path = ROOT / ".pytest-work" / uuid4().hex
    path.mkdir(parents=True)

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    (path / "tests").mkdir()
    (path / "tests" / "test_a.py").write_text("def test_a():\n    assert 1\n")
    git("add", ".")
    git("commit", "-q", "-m", "base")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_main_end_to_end(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    monkeypatch.chdir(repo)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    git("checkout", "-q", "-b", "weaken")
    (repo / "tests" / "test_a.py").write_text("x = 1\n")
    git("commit", "-qam", "weaken")
    assert guard.main(["--base", "main", "--head", "weaken"]) == 1
    assert guard.main(["--base", "main", "--head", "weaken", "--labels", "x"]) == 1

    git("checkout", "-q", "main")
    git("checkout", "-q", "-b", "strengthen")
    (repo / "tests" / "test_b.py").write_text("def test_b():\n    assert 2\n")
    git("add", ".")
    git("commit", "-qm", "strengthen")
    assert guard.main(["--base", "main", "--head", "strengthen"]) == 0


@pytest.mark.parametrize(
    "name",
    ["probe\n.yml", "probe\t.yml", "probé.yml", 'probe "review".yml'],
    ids=["newline", "tab", "unicode", "quote-and-space"],
)
def test_real_git_quoted_protected_paths_are_decoded(repo: Path, name: str) -> None:
    relative = f".github/workflows/{name}"
    path = repo / relative
    path.parent.mkdir(parents=True)
    path.write_text("name: guarded\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", relative], cwd=repo, check=True)
    text = subprocess.run(
        ["git", "-c", "core.quotePath=true", "diff", "--cached", "--unified=0"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    files = guard.parse_diff(text)
    assert len(files) == 1
    assert files[0].path == relative
    findings = guard.analyze(files)
    assert any(f.rule == "caminho-protegido" for f in findings)
    assert guard.decide(findings, set()) == 1


def test_real_git_quoted_rename_cannot_leave_protected_scope(repo: Path) -> None:
    original = ".codex/old\n.toml"
    path = repo / original
    path.parent.mkdir()
    path.write_text('name = "guarded"\n', encoding="utf-8")
    subprocess.run(["git", "add", "--", original], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "add protected fixture"], cwd=repo, check=True
    )
    subprocess.run(["git", "mv", "--", original, "ordinary.toml"], cwd=repo, check=True)
    text = subprocess.run(
        ["git", "diff", "--cached", "--unified=0", "-M"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    files = guard.parse_diff(text)
    assert len(files) == 1
    assert files[0].path == "ordinary.toml"
    assert files[0].status == f"R:{original}"
    assert "caminho-protegido" in rules(*files)


@pytest.mark.parametrize(
    "header",
    [
        'diff --git "a/.github/broken.yml b/.github/broken.yml',
        r'diff --git "a/.github/invalid\q.yml" "b/.github/invalid\q.yml"',
        "diff --git a/folder b/name b/folder b/name",
    ],
)
def test_ambiguous_or_malformed_diff_fails_closed(header: str) -> None:
    files = guard.parse_diff(header)
    findings = guard.analyze(files)
    assert any(f.rule == "diff-invalido" and f.hard for f in findings)
    assert guard.decide(findings, set()) == 1
    assert guard.decide(findings, {guard.OVERRIDE_LABEL}) == 1


@pytest.mark.parametrize(
    "name",
    [
        "probe\n| injected | `message`.yml",
        "probe\r\n|\u202e injected | `message`.yml",
    ],
    ids=["newline", "crlf-and-bidi"],
)
def test_real_git_filename_cannot_inject_summary_markdown(
    repo: Path, name: str
) -> None:
    relative = f".github/workflows/{name}"
    path = repo / relative
    path.parent.mkdir(parents=True)
    path.write_text("name: guarded\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", relative], cwd=repo, check=True)
    text = subprocess.run(
        ["git", "diff", "--cached", "--unified=0"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    findings = guard.analyze(guard.parse_diff(text))
    code = guard.decide(findings, set())
    assert code == 1
    summary = guard.render(findings, set(), code)
    rows = [line for line in summary.splitlines() if line.startswith("|")]
    assert len(rows) == 2 + len(findings)
    assert all(line.count("|") == 4 for line in rows)
    assert "\\n" in summary
    assert "&#124;" in summary
    assert "&#96;" in summary
    assert "\r" not in summary
    assert "\u202e" not in summary


def test_untrusted_finding_details_are_rendered_as_text() -> None:
    finding = guard.Finding(
        "synthetic",
        "ordinary.txt",
        "<em>text</em>\r\n| row | `code` \u202e [link](https://example.invalid)",
    )
    summary = guard.render([finding], set(), 1)
    rows = [line for line in summary.splitlines() if line.startswith("|")]
    assert len(rows) == 3
    assert all(line.count("|") == 4 for line in rows)
    assert "<em>" not in summary
    assert "[link](https://example.invalid)" not in summary
    assert "\\r\\n" in summary
    assert "\\u202e" in summary
