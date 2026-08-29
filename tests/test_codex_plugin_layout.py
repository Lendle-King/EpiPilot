import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_codex_marketplace_plugin_layout_is_self_consistent() -> None:
    marketplace = json.loads(
        (ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
    )
    assert marketplace["name"] == "epipilot"
    entry = marketplace["plugins"][0]
    assert entry["name"] == "epipilot"
    assert entry["source"] == {"source": "local", "path": "./plugins/epipilot"}

    plugin_root = ROOT / "plugins/epipilot"
    manifest = json.loads((plugin_root / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert manifest["name"] == "epipilot"

    mcp = json.loads((plugin_root / "mcp.json").read_text(encoding="utf-8"))
    assert mcp["$schema"] == "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
    server = mcp["mcpServers"]["epipilot"]
    assert server["type"] == "stdio"
    assert server["command"] == "epipilot-mcp"
    assert server["cwd"] == "${PLUGIN_DATA}"


def test_plugin_versions_match_python_package() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_version = project["project"]["version"]
    manifests = (
        ROOT / ".codex-plugin/plugin.json",
        ROOT / "plugins/epipilot/plugin.json",
        ROOT / "plugins/epipilot/.codex-plugin/plugin.json",
    )

    for path in manifests:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["version"] == package_version


def test_marketplace_and_source_tree_skill_do_not_drift() -> None:
    marketplace_skill = ROOT / "plugins/epipilot/skills/epistemic-research/SKILL.md"
    source_skill = ROOT / "skills/epistemic-research/SKILL.md"
    assert marketplace_skill.read_text(encoding="utf-8") == source_skill.read_text(encoding="utf-8")


def test_natural_language_install_contract_is_discoverable() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    contract = (ROOT / "CODEX_INSTALL.md").read_text(encoding="utf-8")

    assert "Install from inside Codex" in readme
    assert "CODEX_INSTALL.md" in readme
    assert "Codex installation intent" in agents
    assert "CODEX_INSTALL.md" in agents
    assert "Natural-language install intent" in contract
    assert "epipilot-install-codex" in contract
    assert "installed" in contract and "enabled" in contract
