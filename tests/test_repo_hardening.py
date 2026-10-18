"""Repository hardening: security controls kept in config files must not vanish.

One assertion per line, so removing a control shows up as a removed assertion.
Offline, stdlib only; never opens a real .env.
"""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Built by concatenation so this file itself never matches.
SECRET_RE = re.compile(
    "|".join(
        (
            "sk-" + "[A-Za-z0-9_-]{20,}",
            "AKIA" + "[0-9A-Z]{16}",
            "AIza" + "[0-9A-Za-z_-]{35}",
            "gh" + "[pousr]_[A-Za-z0-9]{36}",
            "github" + "_pat_[A-Za-z0-9_]{22,}",
            "xox" + "[baprs]-[A-Za-z0-9-]{10,}",
            "-----BEGIN [A-Z ]*PRIVATE " + "KEY-----",
        )
    )
)


def _ignored(name: str) -> bool:
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.excludesFile=/dev/null",
            "check-ignore",
            "--no-index",
            "-q",
            name,
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode in (0, 1)
    return result.returncode == 0


def _secret_locations(paths: list[Path]) -> list[str]:
    # Location only: pytest's assertion introspection would print the line.
    found = []
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), start=1):
            if SECRET_RE.search(line):
                found.append(f"{path}:{number}")
    return found


def _workflow_problems(text: str) -> list[str]:
    problems = []
    if "pull_request_target" in text:
        problems.append("pull_request_target")
    if re.search(r"write-all|:\s*write\b", text):
        problems.append("write")
    if not re.search(r"(?m)^permissions:", text) or "contents: read" not in text:
        problems.append("permissions")
    if "timeout-minutes:" not in text:
        problems.append("timeout")
    if "secrets." in text:
        problems.append("secrets")
    return problems


def _unpinned_uses(text: str) -> list[str]:
    # Tags can be moved; only a full-length commit SHA is immutable.
    return [
        line.strip()
        for line in text.splitlines()
        if re.match(r"\s*(?:-\s+)?uses:", line)
        and not re.search(r"@[0-9a-f]{40}(?:\s|$)", line)
    ]


def test_gitignore_keeps_secrets_and_local_state_out() -> None:
    assert _ignored(".env")
    assert _ignored(".env.local")
    assert _ignored(".env.tmp")
    assert _ignored(".envrc")
    assert _ignored("server.pem")
    assert _ignored("tls.key")
    assert _ignored("cert.p12")
    assert _ignored("cert.pfx")
    assert _ignored("id_rsa")
    assert _ignored("id_ed25519")
    assert _ignored("credentials.json")
    assert _ignored("service-account.json")
    assert _ignored(".netrc")
    assert _ignored(".pypirc")
    assert _ignored(".npmrc")
    assert _ignored("data/chroma/x")
    assert _ignored(".claude/settings.local.json")
    assert not _ignored(".env.example")
    assert not _ignored(".claude/settings.json")


def test_dockerignore_keeps_secrets_out_of_build_context() -> None:
    lines = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    }
    assert ".env" in lines
    assert ".env.*" in lines
    assert ".git" in lines
    assert "data" in lines


def test_claude_settings_only_restrict() -> None:
    settings = json.loads(
        (ROOT / ".claude" / "settings.json").read_text(encoding="utf-8")
    )
    # No hooks, env, MCP servers or apiKeyHelper.
    assert set(settings) == {"$schema", "permissions"}
    perms = settings["permissions"]
    # No allow list and no defaultMode.
    assert set(perms) == {"deny", "ask", "disableBypassPermissionsMode"}
    assert perms["disableBypassPermissionsMode"] == "disable"
    deny = set(perms["deny"])
    ask = set(perms["ask"])
    assert "Read(.env)" in deny
    assert "Edit(.env)" in deny
    assert "Read(~/.ssh/**)" in deny
    assert "Read(~/.config/gh/**)" in deny
    assert "Edit(uv.lock)" in deny
    assert "Bash(git push *--force*)" in deny
    assert "Bash(git reset --hard*)" in deny
    assert "Bash(* --no-verify*)" in deny
    assert "Bash(sudo *)" in deny
    assert "Bash(chmod *777*)" in deny
    assert "Bash(sh)" in deny
    assert "Bash(bash)" in deny
    assert "Bash(gh auth token*)" in deny
    assert "Bash(gh *integridade-aprovada*)" in deny
    assert "Bash(docker compose config)" in deny
    assert "Bash(git push *)" in ask
    assert "Edit(.github/**)" in ask
    assert "Edit(scripts/integrity_guard.py)" in ask
    assert "Edit(AGENTS.md)" in ask
    assert "Edit(.claude/**)" in ask
    assert "mcp__github__merge_pull_request" in ask
    assert "mcp__github__push_files" in ask
    assert not (ROOT / ".mcp.json").exists()


def test_review_and_update_config_present() -> None:
    codeowners = (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    assert "* @rh4y279jh7-hue" in codeowners.splitlines()
    text = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    assert 'package-ecosystem: "uv"' in text
    assert 'package-ecosystem: "github-actions"' in text


def test_workflows_keep_least_privilege() -> None:
    workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows
    for path in workflows:
        assert _workflow_problems(path.read_text(encoding="utf-8")) == [], path.name


def test_integrity_workflow_runs_base_guard() -> None:
    text = (ROOT / ".github" / "workflows" / "integrity.yml").read_text(
        encoding="utf-8"
    )
    assert "pull_request:" in text
    assert 'git show "$BASE_SHA:scripts/integrity_guard.py"' in text
    assert 'python -I "$guard"' in text
    assert "persist-credentials: false" in text


def test_ci_workflow_pins_actions_and_drops_git_credentials() -> None:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "uses:" in text
    assert _unpinned_uses(text) == []
    assert "persist-credentials: false" in text
    assert text.count("persist-credentials: false") >= text.count("actions/checkout@")


def test_pin_checker_flags_tags() -> None:
    sha = "08c6903cd8c0fde910a37f88322edcfb5dd907a8"
    assert _unpinned_uses(f"      - uses: actions/checkout@{sha} # v5.0.0\n") == []
    assert _unpinned_uses(f"    uses: actions/checkout@{sha}\n") == []
    tag = "      - uses: actions/checkout@v5.0.0\n"
    assert _unpinned_uses(tag) == ["- uses: actions/checkout@v5.0.0"]
    assert _unpinned_uses(f"    uses: actions/checkout@{sha}0\n") != []
    assert _unpinned_uses("    uses: actions/setup-python@main\n") != []


def test_workflow_checker_flags_weakening() -> None:
    problems = _workflow_problems(
        "on: pull_request_target\npermissions: write-all\njobs: {}\n"
    )
    assert "pull_request_target" in problems
    assert "write" in problems
    assert "permissions" in problems
    assert "timeout" in problems


def test_tracked_files_have_no_secret_patterns() -> None:
    names = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split("\0")
    assert _secret_locations([ROOT / n for n in names if n]) == []


def test_secret_checker_reports_location_only(tmp_path: Path) -> None:
    fake = "github" + "_pat_" + "A" * 30
    leak = tmp_path / "leak.txt"
    leak.write_text(f"ok\nTOKEN={fake}\n", encoding="utf-8")
    found = _secret_locations([leak])
    assert found == [f"{leak}:2"]
    assert all(fake not in item for item in found)
