"""One-command bootstrap installer for the EpiPilot Codex plugin."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

DEFAULT_REPOSITORY = "Lendle-King/EpiPilot"
DEFAULT_MARKETPLACE = "epipilot"
DEFAULT_PLUGIN = "epipilot"
DEFAULT_REF = "main"


class BootstrapError(RuntimeError):
    """Raised when the Codex plugin bootstrap cannot complete safely."""


@dataclass(frozen=True, slots=True)
class ProcessResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def run(self, argv: tuple[str, ...], *, timeout_seconds: float) -> ProcessResult:
        """Execute one argv command without a shell."""
        ...


@dataclass(frozen=True, slots=True)
class SubprocessRunner:
    def run(self, argv: tuple[str, ...], *, timeout_seconds: float) -> ProcessResult:
        completed = subprocess.run(
            argv,
            capture_output=True,
            check=False,
            shell=False,
            text=True,
            timeout=timeout_seconds,
        )
        return ProcessResult(
            argv=argv,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


@dataclass(frozen=True, slots=True)
class InstallConfig:
    repository: str = DEFAULT_REPOSITORY
    ref: str | None = DEFAULT_REF
    marketplace_name: str = DEFAULT_MARKETPLACE
    plugin_name: str = DEFAULT_PLUGIN
    install_runtime: bool = True
    timeout_seconds: float = 600.0

    def __post_init__(self) -> None:
        for name, value in (
            ("repository", self.repository),
            ("marketplace_name", self.marketplace_name),
            ("plugin_name", self.plugin_name),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be empty")
        if self.ref is not None and not self.ref.strip():
            raise ValueError("ref must be non-empty when supplied")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    @property
    def runtime_spec(self) -> str:
        base = f"git+https://github.com/{self.repository}.git"
        return f"{base}@{self.ref}" if self.ref is not None else base

    @property
    def plugin_selector(self) -> str:
        return f"{self.plugin_name}@{self.marketplace_name}"


@dataclass(frozen=True, slots=True)
class InstallReport:
    repository: str
    ref: str | None
    marketplace_name: str
    plugin_name: str
    plugin_version: str
    installed: bool
    enabled: bool
    runtime_installed: bool
    restart_required: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "repository": self.repository,
            "ref": self.ref,
            "marketplace_name": self.marketplace_name,
            "plugin_name": self.plugin_name,
            "plugin_version": self.plugin_version,
            "installed": self.installed,
            "enabled": self.enabled,
            "runtime_installed": self.runtime_installed,
            "restart_required": self.restart_required,
        }


@dataclass(slots=True)
class CodexPluginInstaller:
    codex_executable: str
    uv_executable: str | None
    runner: CommandRunner

    def install(self, config: InstallConfig) -> InstallReport:
        if config.install_runtime:
            if self.uv_executable is None:
                raise BootstrapError(
                    "uv is required for one-command runtime installation; install uv or rerun "
                    "with --skip-runtime-install after installing EpiPilot another way"
                )
            self._checked(
                (
                    self.uv_executable,
                    "tool",
                    "install",
                    "--force",
                    config.runtime_spec,
                ),
                config,
            )

        add_marketplace = [
            self.codex_executable,
            "plugin",
            "marketplace",
            "add",
            config.repository,
        ]
        if config.ref is not None:
            add_marketplace.extend(("--ref", config.ref))
        add_marketplace.append("--json")
        marketplace_payload = self._json(tuple(add_marketplace), config)
        marketplace_name = _require_string(marketplace_payload, "marketplaceName")
        if marketplace_name != config.marketplace_name:
            raise BootstrapError(
                f"Codex registered marketplace {marketplace_name!r}, expected "
                f"{config.marketplace_name!r}"
            )

        self._checked(
            (
                self.codex_executable,
                "plugin",
                "marketplace",
                "upgrade",
                config.marketplace_name,
                "--json",
            ),
            config,
        )

        plugin_payload = self._json(
            (
                self.codex_executable,
                "plugin",
                "add",
                config.plugin_selector,
                "--json",
            ),
            config,
        )
        installed_marketplace = _require_string(plugin_payload, "marketplaceName")
        if installed_marketplace != config.marketplace_name:
            raise BootstrapError(
                f"plugin was installed from marketplace {installed_marketplace!r}, expected "
                f"{config.marketplace_name!r}"
            )

        listing = self._json(
            (self.codex_executable, "plugin", "list", "--json"),
            config,
        )
        entry = _find_installed_plugin(
            listing,
            plugin_name=config.plugin_name,
            marketplace_name=config.marketplace_name,
        )
        return InstallReport(
            repository=config.repository,
            ref=config.ref,
            marketplace_name=config.marketplace_name,
            plugin_name=config.plugin_name,
            plugin_version=_require_string(entry, "version"),
            installed=_require_bool(entry, "installed"),
            enabled=_require_bool(entry, "enabled"),
            runtime_installed=config.install_runtime,
        )

    def _checked(self, argv: tuple[str, ...], config: InstallConfig) -> ProcessResult:
        try:
            result = self.runner.run(argv, timeout_seconds=config.timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            raise BootstrapError(f"command timed out: {_display_argv(argv)}") from exc
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
            raise BootstrapError(
                f"command failed ({result.returncode}): {_display_argv(argv)}\n{detail}"
            )
        return result

    def _json(self, argv: tuple[str, ...], config: InstallConfig) -> dict[str, object]:
        result = self._checked(argv, config)
        try:
            parsed = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise BootstrapError(
                f"command did not return valid JSON: {_display_argv(argv)}"
            ) from exc
        return _json_object(parsed, source=_display_argv(argv))


def _json_object(value: object, *, source: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise BootstrapError(f"expected JSON object from {source}")
    if not all(isinstance(key, str) for key in value):
        raise BootstrapError(f"expected string JSON keys from {source}")
    return {str(key): item for key, item in value.items()}


def _require_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise BootstrapError(f"expected non-empty string field {key!r}")
    return value


def _require_bool(payload: dict[str, object], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise BootstrapError(f"expected boolean field {key!r}")
    return value


def _find_installed_plugin(
    payload: dict[str, object],
    *,
    plugin_name: str,
    marketplace_name: str,
) -> dict[str, object]:
    installed = payload.get("installed")
    if not isinstance(installed, list):
        raise BootstrapError("Codex plugin list JSON is missing the installed array")
    for raw_entry in installed:
        if not isinstance(raw_entry, dict):
            continue
        entry = {str(key): value for key, value in raw_entry.items() if isinstance(key, str)}
        if entry.get("name") == plugin_name and entry.get("marketplaceName") == marketplace_name:
            if entry.get("installed") is not True or entry.get("enabled") is not True:
                raise BootstrapError(
                    f"{plugin_name}@{marketplace_name} exists but is not installed and enabled"
                )
            return entry
    raise BootstrapError(
        f"Codex did not report {plugin_name}@{marketplace_name} as an installed plugin"
    )


def _display_argv(argv: tuple[str, ...]) -> str:
    return " ".join(argv)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="epipilot-install-codex",
        description=(
            "Persist the EpiPilot runtime, register its Codex marketplace, install the plugin, "
            "and verify that Codex reports it installed and enabled."
        ),
    )
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument(
        "--ref",
        default=DEFAULT_REF,
        help="Git ref used for both the runtime and marketplace (default: main).",
    )
    parser.add_argument(
        "--skip-runtime-install",
        action="store_true",
        help=(
            "Skip `uv tool install`; use only when epipilot-mcp is already persistently "
            "installed."
        ),
    )
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    codex = shutil.which("codex")
    uv = shutil.which("uv")
    if codex is None:
        return _emit_failure("Codex CLI was not found on PATH", json_output=args.json_output)
    if uv is None and not args.skip_runtime_install:
        return _emit_failure(
            "uv was not found on PATH; install uv or pass --skip-runtime-install",
            json_output=args.json_output,
        )

    config = InstallConfig(
        repository=args.repository,
        ref=args.ref,
        install_runtime=not args.skip_runtime_install,
        timeout_seconds=args.timeout,
    )
    installer = CodexPluginInstaller(
        codex_executable=codex,
        uv_executable=uv,
        runner=SubprocessRunner(),
    )
    try:
        report = installer.install(config)
    except (BootstrapError, ValueError) as exc:
        return _emit_failure(str(exc), json_output=args.json_output)

    if args.json_output:
        print(json.dumps({"ok": True, **report.as_dict()}, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"Installed {report.plugin_name}@{report.marketplace_name} "
            f"({report.plugin_version}); runtime_installed={report.runtime_installed}."
        )
        print("Start a new Codex thread so the plugin skill and MCP tools are loaded.")
    return 0


def _emit_failure(message: str, *, json_output: bool) -> int:
    if json_output:
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False, sort_keys=True))
    else:
        print(f"EpiPilot Codex installation failed: {message}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
