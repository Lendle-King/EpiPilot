import json
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


def test_marketplace_and_source_tree_skill_do_not_drift() -> None:
    marketplace_skill = ROOT / "plugins/epipilot/skills/epistemic-research/SKILL.md"
    source_skill = ROOT / "skills/epistemic-research/SKILL.md"
    assert marketplace_skill.read_text(encoding="utf-8") == source_skill.read_text(encoding="utf-8")
