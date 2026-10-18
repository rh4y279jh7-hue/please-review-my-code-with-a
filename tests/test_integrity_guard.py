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
    assert not rules(diff("AGENTS.md", added=["7. Nova regra mais forte."]))
    assert "guardiao" in rules(diff("scripts/integrity_guard.py", added=["pass"]))
    assert "ci" in rules(diff(".github/workflows/ci.yml", added=["  - run: echo"]))


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
