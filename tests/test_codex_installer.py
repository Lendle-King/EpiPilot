from epipilot.integrations.codex.installer import (
    BootstrapError,
    CodexPluginInstaller,
    InstallConfig,
    ProcessResult,
)


class FakeRunner:
    def __init__(self, responses: dict[tuple[str, ...], ProcessResult]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv: tuple[str, ...], *, timeout_seconds: float) -> ProcessResult:
        assert timeout_seconds == 600.0
        self.calls.append(argv)
        try:
            return self.responses[argv]
        except KeyError as exc:
            raise AssertionError(f"unexpected command: {argv}") from exc


def _ok(argv: tuple[str, ...], stdout: str = "") -> ProcessResult:
    return ProcessResult(argv=argv, returncode=0, stdout=stdout, stderr="")


def _commands() -> dict[str, tuple[str, ...]]:
    runtime = (
        "/usr/bin/uv",
        "tool",
        "install",
        "--force",
        "git+https://github.com/Lendle-King/EpiPilot.git@feature",
    )
    marketplace = (
        "/usr/bin/codex",
        "plugin",
        "marketplace",
        "add",
        "Lendle-King/EpiPilot",
        "--ref",
        "feature",
        "--json",
    )
    upgrade = (
        "/usr/bin/codex",
        "plugin",
        "marketplace",
        "upgrade",
        "epipilot",
        "--json",
    )
    add_plugin = (
        "/usr/bin/codex",
        "plugin",
        "add",
        "epipilot@epipilot",
        "--json",
    )
    list_plugins = ("/usr/bin/codex", "plugin", "list", "--json")
    return {
        "runtime": runtime,
        "marketplace": marketplace,
        "upgrade": upgrade,
        "add_plugin": add_plugin,
        "list_plugins": list_plugins,
    }


def _responses(*, enabled: bool = True) -> dict[tuple[str, ...], ProcessResult]:
    commands = _commands()
    return {
        commands["runtime"]: _ok(commands["runtime"]),
        commands["marketplace"]: _ok(
            commands["marketplace"],
            '{"marketplaceName":"epipilot","installedRoot":"/cache/epipilot","alreadyAdded":false}',
        ),
        commands["upgrade"]: _ok(commands["upgrade"], "{}"),
        commands["add_plugin"]: _ok(
            commands["add_plugin"],
            '{"pluginId":"epipilot@epipilot","name":"epipilot",'
            '"marketplaceName":"epipilot","version":"0.2.1",'
            '"installedPath":"/cache/plugin","authPolicy":"ON_INSTALL"}',
        ),
        commands["list_plugins"]: _ok(
            commands["list_plugins"],
            '{"installed":[{"pluginId":"epipilot","name":"epipilot",'
            '"marketplaceName":"epipilot","version":"0.2.1","installed":true,'
            f'"enabled":{str(enabled).lower()}}}],"available":[]}}',
        ),
    }


def test_bootstrap_installs_runtime_marketplace_and_plugin() -> None:
    commands = _commands()
    runner = FakeRunner(_responses())
    installer = CodexPluginInstaller(
        codex_executable="/usr/bin/codex",
        uv_executable="/usr/bin/uv",
        runner=runner,
    )

    report = installer.install(InstallConfig(ref="feature"))

    assert runner.calls == [
        commands["runtime"],
        commands["marketplace"],
        commands["upgrade"],
        commands["add_plugin"],
        commands["list_plugins"],
    ]
    assert report.plugin_version == "0.2.1"
    assert report.installed is True
    assert report.enabled is True
    assert report.runtime_installed is True
    assert report.restart_required is True


def test_bootstrap_can_skip_runtime_install() -> None:
    commands = _commands()
    responses = _responses()
    responses.pop(commands["runtime"])
    runner = FakeRunner(responses)
    installer = CodexPluginInstaller(
        codex_executable="/usr/bin/codex",
        uv_executable=None,
        runner=runner,
    )

    report = installer.install(InstallConfig(ref="feature", install_runtime=False))

    assert commands["runtime"] not in runner.calls
    assert report.runtime_installed is False


def test_bootstrap_fails_closed_when_codex_reports_disabled_plugin() -> None:
    runner = FakeRunner(_responses(enabled=False))
    installer = CodexPluginInstaller(
        codex_executable="/usr/bin/codex",
        uv_executable="/usr/bin/uv",
        runner=runner,
    )

    try:
        installer.install(InstallConfig(ref="feature"))
    except BootstrapError as exc:
        assert "not installed and enabled" in str(exc)
    else:
        raise AssertionError("disabled plugin must fail bootstrap verification")
