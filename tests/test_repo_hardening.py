"""Repository hardening: security controls kept in config files must not vanish.

One assertion per line, so removing a control shows up as a removed assertion.
Offline, stdlib only; never opens a real .env.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

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

# Checked on the line without the CI placeholder; SECRET_RE always sees the
# whole line. After a comma only a quoted value counts, and never another
# HIVEMIND_ name, so tuples of names and setenv(name, variable) stay quiet.
PROJECT_SECRET_RE = re.compile(
    "HIVEMIND_"
    + r"[A-Z0-9_]*(?:TOKEN|KEY|SECRET|PASSWORD)[\x22\x27]?[\])]?\s*"
    + r"(?:[:=]\s*[\x22\x27]?|,\s*[\x22\x27](?!HIVEMIND_))"
    + r"[A-Za-z0-9_\-+/=.]{16,}"
)

ALLOWED_PLACEHOLDERS = ("ci-placeholder-token-with-32-characters",)

# Obviously fake, for scanner fixtures.
FAKE_VALUE = "fake-value-for-scanner-test"


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
            # Only the HIVEMIND_* check skips the placeholder itself.
            project = line
            for allowed in ALLOWED_PLACEHOLDERS:
                project = project.replace(allowed, "")
            if SECRET_RE.search(line) or PROJECT_SECRET_RE.search(project):
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
        if re.match(r"\s*(?:-\s*)?uses\s*:", line)
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


EXPECTED_DENY = {
    "Bash(* --no-verify*)",
    "Bash(* | /bin/bash*)",
    "Bash(* | /bin/sh*)",
    "Bash(* | /bin/zsh*)",
    "Bash(* | bash*)",
    "Bash(* | sh*)",
    "Bash(* | zsh*)",
    "Bash(*sudo *)",
    "Bash(/bin/bash)",
    "Bash(/bin/bash*)",
    "Bash(/bin/sh)",
    "Bash(/bin/sh*)",
    "Bash(/bin/zsh)",
    "Bash(/bin/zsh*)",
    "Bash(bash -c*)",
    "Bash(bash -s *)",
    "Bash(bash)",
    "Bash(chmod *777*)",
    "Bash(chmod a+rwx*)",
    "Bash(doas *)",
    "Bash(docker compose * config)",
    "Bash(docker compose config)",
    "Bash(gh *integridade-aprovada*)",
    "Bash(gh auth *--show-token*)",
    "Bash(gh auth token*)",
    "Bash(git commit * -n*)",
    "Bash(git commit -n*)",
    "Bash(git credential *)",
    "Bash(git push * +*)",
    "Bash(git push * -f*)",
    "Bash(git push *--force*)",
    "Bash(git push -f*)",
    "Bash(git reset * --hard*)",
    "Bash(git reset --hard*)",
    "Bash(sh -c*)",
    "Bash(sh -s *)",
    "Bash(sh)",
    "Bash(su *)",
    "Bash(su)",
    "Bash(sudo *)",
    "Bash(zsh -c*)",
    "Bash(zsh -s *)",
    "Bash(zsh)",
    "Edit(.env)",
    "Edit(uv.lock)",
    "Read(*.env)",
    "Read(*.key)",
    "Read(*.p12)",
    "Read(*.pem)",
    "Read(*.pfx)",
    "Read(.env)",
    "Read(.env.*)",
    "Read(.env.*.local)",
    "Read(.env.bak)",
    "Read(.env.development)",
    "Read(.env.local)",
    "Read(.env.old)",
    "Read(.env.production)",
    "Read(.env.staging)",
    "Read(.env.test)",
    "Read(.env.tmp)",
    "Read(.envrc)",
    "Read(//**/*.env)",
    "Read(//**/*.key)",
    "Read(//**/*.p12)",
    "Read(//**/*.pem)",
    "Read(//**/*.pfx)",
    "Read(//**/*Cookie*)",
    "Read(//**/*cookie*)",
    "Read(//**/*keychain*)",
    "Read(//**/.env*)",
    "Read(//**/credentials*.json)",
    "Read(//**/id_ecdsa*)",
    "Read(//**/id_ed25519*)",
    "Read(//**/id_rsa*)",
    "Read(//**/service-account*.json)",
    "Read(//proc/**)",
    "Read(credentials*.json)",
    "Read(id_ecdsa*)",
    "Read(id_ed25519*)",
    "Read(id_rsa*)",
    "Read(service-account*.json)",
    "Read(~/.aws/**)",
    "Read(~/.azure/**)",
    "Read(~/.claude/.credentials.json)",
    "Read(~/.config/gcloud/**)",
    "Read(~/.config/gh/**)",
    "Read(~/.docker/config.json)",
    "Read(~/.git-credentials)",
    "Read(~/.gnupg/**)",
    "Read(~/.kube/**)",
    "Read(~/.local/share/keyrings/**)",
    "Read(~/.netrc)",
    "Read(~/.npmrc)",
    "Read(~/.pypirc)",
    "Read(~/.ssh/**)",
    "Read(~/Library/Keychains/**)",
}

EXPECTED_ASK = {
    "Bash(bash *)",
    "Bash(diff *)",
    "Bash(docker compose *)",
    "Bash(env)",
    "Bash(find *)",
    "Bash(gh api *)",
    "Bash(gh auth *)",
    "Bash(gh issue edit *)",
    "Bash(gh label *)",
    "Bash(gh pr create *)",
    "Bash(gh pr edit *)",
    "Bash(gh pr merge *)",
    "Bash(gh pr ready *)",
    "Bash(gh pr review *)",
    "Bash(gh release *)",
    "Bash(gh repo *)",
    "Bash(gh secret *)",
    "Bash(gh variable *)",
    "Bash(gh workflow *)",
    "Bash(git branch -D *)",
    "Bash(git checkout *)",
    "Bash(git clean *)",
    "Bash(git commit *)",
    "Bash(git fetch *)",
    "Bash(git filter-branch *)",
    "Bash(git filter-repo *)",
    "Bash(git grep *)",
    "Bash(git merge *)",
    "Bash(git pull *)",
    "Bash(git push *)",
    "Bash(git push)",
    "Bash(git rebase *)",
    "Bash(git restore *)",
    "Bash(git stash *)",
    "Bash(git switch *)",
    "Bash(grep *)",
    "Bash(npm i *)",
    "Bash(npm i)",
    "Bash(npm install *)",
    "Bash(npm install)",
    "Bash(npx *)",
    "Bash(pip install *)",
    "Bash(printenv *)",
    "Bash(printenv)",
    "Bash(python -c *)",
    "Bash(python -m pip install *)",
    "Bash(python3 -c *)",
    "Bash(python3 -m pip install *)",
    "Bash(rm -R *)",
    "Bash(rm -fr *)",
    "Bash(rm -r *)",
    "Bash(rm -rf *)",
    "Bash(sh *)",
    "Bash(uv add *)",
    "Bash(uv lock *)",
    "Bash(uv pip install *)",
    "Bash(uv remove *)",
    "Bash(zsh *)",
    "Edit(.antigravity/**)",
    "Edit(.claude/**)",
    "Edit(.codex/**)",
    "Edit(.dockerignore)",
    "Edit(.github/**)",
    "Edit(.gitignore)",
    "Edit(AGENTS.md)",
    "Edit(CLAUDE.md)",
    "Edit(Dockerfile)",
    "Edit(GEMINI.md)",
    "Edit(SECURITY.md)",
    "Edit(docker-compose*.yml)",
    "Edit(docs/agentes.md)",
    "Edit(pyproject.toml)",
    "Edit(scripts/integrity_guard.py)",
    "Edit(scripts/start.sh)",
    "Read(/data/**)",
    "mcp__github__actions_run_trigger",
    "mcp__github__add_issue_comment",
    "mcp__github__add_reply_to_pull_request_comment",
    "mcp__github__assign_copilot_to_issue",
    "mcp__github__create_branch",
    "mcp__github__create_or_update_file",
    "mcp__github__create_pull_request",
    "mcp__github__create_repository",
    "mcp__github__delete_file",
    "mcp__github__enable_pr_auto_merge",
    "mcp__github__fork_repository",
    "mcp__github__issue_write",
    "mcp__github__merge_pull_request",
    "mcp__github__pull_request_review_write",
    "mcp__github__push_files",
    "mcp__github__request_copilot_review",
    "mcp__github__update_pull_request",
    "mcp__github__update_pull_request_branch",
}


def test_claude_settings_exact_rule_sets() -> None:
    settings = json.loads(
        (ROOT / ".claude" / "settings.json").read_text(encoding="utf-8")
    )
    perms = settings["permissions"]
    assert set(perms["deny"]) == EXPECTED_DENY
    assert set(perms["ask"]) == EXPECTED_ASK


def test_dependabot_covers_uv_and_actions_with_cooldown() -> None:
    text = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    assert len(re.findall(r'(?m)^\s*-\s*package-ecosystem:\s*"uv"\s*$', text)) == 1
    assert (
        len(re.findall(r'(?m)^\s*-\s*package-ecosystem:\s*"github-actions"\s*$', text))
        == 1
    )
    assert len(re.findall(r"(?m)^\s*default-days:\s*7\s*$", text)) == 2


def test_ci_job_has_a_real_timeout() -> None:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert re.search(r"(?m)^\s*timeout-minutes:\s*\d+\s*$", text)


def test_pin_checker_sees_spaced_uses() -> None:
    sha = "08c6903cd8c0fde910a37f88322edcfb5dd907a8"
    assert _unpinned_uses(f"      - uses : actions/checkout@{sha}\n") == []
    assert _unpinned_uses("      - uses : actions/checkout@v5.0.0\n") != []


def test_scanner_flags_unprefixed_project_token(tmp_path: Path) -> None:
    key = "HIVEMIND_" + "API_TOKEN"
    leak = tmp_path / "leak.txt"
    leak.write_text(f"ok\n{key}={FAKE_VALUE}\n", encoding="utf-8")
    assert _secret_locations([leak]) == [f"{leak}:2"]
    placeholder = tmp_path / "placeholder.txt"
    placeholder.write_text(
        f"{key}=ci-placeholder-token-with-32-characters\n", encoding="utf-8"
    )
    assert _secret_locations([placeholder]) == []


def _flagged(tmp_path: Path, line: str) -> bool:
    probe = tmp_path / "probe.txt"
    probe.write_text(f"{line}\n", encoding="utf-8")
    return _secret_locations([probe]) == [f"{probe}:1"]


# One obviously fake sample per pattern, built at runtime like SECRET_RE.
FAKE_SECRETS = {
    "sk": "sk-" + FAKE_VALUE,
    "akia": "AKIA" + "FAKEVALUEFORTEST",
    "aiza": "AIza" + "fake-" * 7,
    "gh": "gh" + "p_" + "fake" * 9,
    "github-pat": "github" + "_pat_" + "fake_" * 5,
    "xox": "xox" + "b-" + FAKE_VALUE,
    "private-key": "-----BEGIN " + "PRIVATE " + "KEY-----",
    "hivemind": "HIVEMIND_" + "OPENAI_API_KEY=" + FAKE_VALUE,
}


def test_ci_placeholder_alone_is_not_flagged(tmp_path: Path) -> None:
    key = "HIVEMIND_" + "API_TOKEN"
    for placeholder in ALLOWED_PLACEHOLDERS:
        assert not _flagged(tmp_path, f"{key}={placeholder}")
        assert not _flagged(tmp_path, f'ports="$({key}={placeholder} \\')
        assert not _flagged(tmp_path, f'"{key}": "{placeholder}"')
        assert not _flagged(tmp_path, f'monkeypatch.setenv("{key}", "{placeholder}")')


@pytest.mark.parametrize("kind", sorted(FAKE_SECRETS))
def test_placeholder_does_not_hide_a_secret_on_its_line(
    tmp_path: Path, kind: str
) -> None:
    fake = FAKE_SECRETS[kind]
    assert _flagged(tmp_path, fake)
    for placeholder in ALLOWED_PLACEHOLDERS:
        assert _flagged(tmp_path, f"{fake}  # {placeholder}")
        assert _flagged(tmp_path, f"{placeholder} {fake}")


@pytest.mark.parametrize(
    "template",
    [
        '{key}="{value}"',
        '"{key}": "{value}"',
        "{{'{key}': '{value}'}}",
        'monkeypatch.setenv("{key}", "{value}")',
        'os.environ["{key}"] = "{value}"',
    ],
    ids=["quoted-value", "json", "dict", "setenv", "environ"],
)
def test_scanner_flags_quoted_project_keys(tmp_path: Path, template: str) -> None:
    line = template.format(key="HIVEMIND_" + "API_TOKEN", value=FAKE_VALUE)
    assert _flagged(tmp_path, line)


def test_scanner_skips_short_or_missing_project_values(tmp_path: Path) -> None:
    key = "HIVEMIND_" + "API_TOKEN"
    assert not _flagged(tmp_path, f'monkeypatch.setenv("{key}", "test-token")')
    assert not _flagged(tmp_path, f'os.environ.get("{key}")')
    assert not _flagged(tmp_path, f"{key}=")


# Each prefix followed by the CI placeholder matches the vendor pattern it is
# named after. Except akia and private-key, which cannot use the placeholder's
# lowercase text, the match needs the placeholder's own characters.
PREFIXES_OF_PLACEHOLDER = {
    "sk-0": "s" + "k-",
    "sk-19": "s" + "k-" + "f" * 19,
    "akia": "AKIA" + "FAKEVALUEFORTEST",
    "aiza-34": "AIza" + "f" * 34,
    "gh-35": "gh" + "p_" + "f" * 35,
    "github-pat-21": "github" + "_pat_" + "f" * 21,
    "xox-9": "xox" + "b-" + "f" * 9,
    "private-key": "-----BEGIN " + "PRIVATE " + "KEY-----",
}


@pytest.mark.parametrize("case", sorted(PREFIXES_OF_PLACEHOLDER))
def test_vendor_patterns_see_the_placeholder_text(tmp_path: Path, case: str) -> None:
    line = PREFIXES_OF_PLACEHOLDER[case] + ALLOWED_PLACEHOLDERS[0]
    assert _flagged(tmp_path, line)


def test_placeholder_end_can_start_a_vendor_pattern(tmp_path: Path) -> None:
    # The CI placeholder ends in "s", so "k-..." right after it reads "sk-...".
    assert _flagged(tmp_path, ALLOWED_PLACEHOLDERS[0] + "k-" + FAKE_VALUE)


@pytest.mark.parametrize(
    "template",
    [
        'for name in ("{key}", "{other}"):',
        "Defina {key}, {other} e {third}.",
        'monkeypatch.setenv("{key}", valid_token_fixture)',
        'monkeypatch.setenv("{key}", secrets.token_urlsafe(32))',
        'monkeypatch.setenv("{key}", "x" * 32)',
    ],
    ids=["names", "prose", "setenv-name", "setenv-generated", "setenv-repeat"],
)
def test_scanner_ignores_names_and_variables_after_a_comma(
    tmp_path: Path, template: str
) -> None:
    line = template.format(
        key="HIVEMIND_" + "API_TOKEN",
        other="HIVEMIND_" + "OPENAI_API_KEY",
        third="HIVEMIND_" + "GOOGLE_API_KEY",
    )
    assert not _flagged(tmp_path, line)


def test_integrity_job_has_a_real_timeout() -> None:
    text = (ROOT / ".github" / "workflows" / "integrity.yml").read_text(
        encoding="utf-8"
    )
    assert re.search(r"(?m)^\s*timeout-minutes:\s*\d+\s*$", text)
