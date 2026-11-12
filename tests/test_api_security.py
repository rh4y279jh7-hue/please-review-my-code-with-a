"""Launcher security checks use isolated files and never start Docker services."""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("arguments", [[], ["--lan"]])
@pytest.mark.parametrize("preexisting", [False, True])
def test_launcher_generates_private_token_without_disclosing_it(
    tmp_path: Path, arguments: list[str], preexisting: bool
) -> None:
    root = Path(__file__).resolve().parents[1]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copyfile(root / "scripts/start.sh", scripts / "start.sh")
    (tmp_path / ".env.example").write_text(
        "HIVEMIND_DEMO_MODE=true\n# HIVEMIND_API_TOKEN=\n", encoding="utf-8"
    )
    existing_token = "offline-existing-token-" * 2
    if preexisting:
        (tmp_path / ".env").write_text(
            "HIVEMIND_DEMO_MODE=true\nHIVEMIND_API_TOKEN=" + existing_token + "\n",
            encoding="utf-8",
        )
        (tmp_path / ".env").chmod(0o644)
    binary = tmp_path / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    docker.chmod(0o700)
    environment = {**os.environ, "PATH": f"{binary}{os.pathsep}{os.environ['PATH']}"}

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(scripts / "start.sh"), *arguments],
            cwd=tmp_path,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )

    first = run()
    assert first.returncode == 0, first.stderr
    configured = (tmp_path / ".env").read_text(encoding="utf-8")
    values = [
        line.partition("=")[2]
        for line in configured.splitlines()
        if line.startswith("HIVEMIND_API_TOKEN=")
    ]
    assert len(values) == 1
    assert len(values[0]) >= 32
    if preexisting:
        assert values[0] == existing_token
    assert values[0] not in first.stdout + first.stderr
    assert "HIVEMIND_DEMO_MODE=true" in configured
    assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600
    assert list(tmp_path.glob(".env.??????")) == []
    second = run()
    assert second.returncode == 0, second.stderr
    assert (tmp_path / ".env").read_text(encoding="utf-8") == configured
    assert values[0] not in second.stdout + second.stderr


@pytest.mark.parametrize("kind", ["symlink", "dangling_symlink", "directory"])
def test_launcher_rejects_nonregular_environment_files(
    tmp_path: Path, kind: str
) -> None:
    root = Path(__file__).resolve().parents[1]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copyfile(root / "scripts/start.sh", scripts / "start.sh")
    (tmp_path / ".env.example").write_text(
        "HIVEMIND_DEMO_MODE=true\n", encoding="utf-8"
    )
    target = tmp_path / "environment-target"
    content = "HIVEMIND_API_TOKEN=" + "offline-existing-token-" * 2 + "\n"
    if kind == "directory":
        (tmp_path / ".env").mkdir()
    else:
        if kind == "symlink":
            target.write_text(content, encoding="utf-8")
            target.chmod(0o644)
        (tmp_path / ".env").symlink_to(target)

    binary = tmp_path / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text(
        '#!/bin/sh\nif [ "$2" = "version" ]; then exit 0; fi\n'
        'touch "docker-started"\n',
        encoding="utf-8",
    )
    docker.chmod(0o700)
    result = subprocess.run(
        ["bash", str(scripts / "start.sh")],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{binary}{os.pathsep}{os.environ['PATH']}"},
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode != 0
    assert not (tmp_path / "docker-started").exists()
    assert "offline-existing-token-" not in result.stdout + result.stderr
    if kind == "symlink":
        assert target.read_text(encoding="utf-8") == content
        assert stat.S_IMODE(target.stat().st_mode) == 0o644
    elif kind == "dangling_symlink":
        assert not target.exists()
    else:
        assert list((tmp_path / ".env").iterdir()) == []
