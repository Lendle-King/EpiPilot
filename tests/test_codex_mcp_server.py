import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def test_epipilot_mcp_stdio_smoke(tmp_path: Path) -> None:
    db_path = tmp_path / "events.sqlite3"
    env = os.environ.copy()
    env["EPIPILOT_DB"] = str(db_path)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "epipilot.integrations.codex.mcp_server"],
        env=env,
    )

    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        names = {tool.name for tool in tools.tools}
        assert "epipilot_info" in names
        assert "epipilot_start_project" in names
        assert "epipilot_preregister_experiment" in names
        assert "epipilot_next" in names

        info = await session.call_tool("epipilot_info", arguments={})
        assert info.structured_content is not None
        assert info.structured_content["event_store_path"] == str(db_path.resolve())

        created = await session.call_tool(
            "epipilot_start_project",
            arguments={
                "project_id": "mcp-smoke",
                "goal": "Diagnose a bounded failure",
                "success_criteria": ["Independent evidence identifies the cause"],
            },
        )
        assert created.structured_content is not None
        assert created.structured_content["project_id"] == "mcp-smoke"

        listed = await session.call_tool("epipilot_list_projects", arguments={})
        assert listed.structured_content is not None
        assert listed.structured_content["project_ids"] == ["mcp-smoke"]

        next_step = await session.call_tool(
            "epipilot_next",
            arguments={"project_id": "mcp-smoke"},
        )
        assert next_step.structured_content is not None
        assert next_step.structured_content["kind"] == "synthesize"
