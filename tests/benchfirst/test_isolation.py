from pathlib import Path

import pytest

from epipilot.benchfirst.isolation import Limits, OfflineDocker


def test_docker_file_size_limit_is_bytes_not_shell_blocks(tmp_path: Path) -> None:
    paths = [tmp_path / name for name in ("harness", "source", "work")]
    for path in paths:
        path.mkdir()
    argv = OfflineDocker("sha256:" + "a" * 64).argv("unit", *paths, ("python", "--version"))
    assert "fsize=16777216:16777216" in argv


def test_offline_boundary_has_no_ambient_credentials(tmp_path: Path) -> None:
    sandbox = OfflineDocker("sha256:" + "a" * 64)
    args = sandbox.argv("test", tmp_path, tmp_path, tmp_path, ("python", "driver.py"))
    assert "--network=none" in args
    assert "--read-only" in args
    assert "--memory-swap=2048m" in args
    assert "--pull=never" in args
    assert not any("docker.sock" in x or "API_KEY" in x for x in args)
    assert args[-3:] == ("sha256:" + "a" * 64, "python", "driver.py")
    with pytest.raises(ValueError):
        OfflineDocker("python:latest")
    with pytest.raises(ValueError):
        Limits(seconds=1801)
