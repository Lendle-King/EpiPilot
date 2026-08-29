# Install EpiPilot from Codex

This file is the bootstrap contract for a **Codex agent that has not installed EpiPilot yet**.

## Natural-language install intent

When the user says something equivalent to:

```text
Install this Codex plugin: https://github.com/Lendle-King/EpiPilot
```

or gives a branch URL such as:

```text
Install this Codex plugin:
https://github.com/Lendle-King/EpiPilot/tree/feat/codex-epistemic-research-plugin
```

Codex should perform the installation itself. Do **not** respond by merely giving the user shell commands to copy unless the current environment does not permit Codex to execute them.

## Repository and ref resolution

Resolve the GitHub URL deterministically:

- `https://github.com/Lendle-King/EpiPilot` -> repository `Lendle-King/EpiPilot`, ref `main`;
- `https://github.com/Lendle-King/EpiPilot/tree/<ref>` -> repository `Lendle-King/EpiPilot`, ref `<ref>`;
- if the user explicitly names a ref, prefer that explicit ref;
- never silently substitute another repository or ref.

For the current development branch, the bootstrap command is:

```bash
uvx --from "git+https://github.com/Lendle-King/EpiPilot.git@feat/codex-epistemic-research-plugin" \
  epipilot-install-codex --ref feat/codex-epistemic-research-plugin --json
```

After this branch is merged to `main`, the normal command is:

```bash
uvx --from "git+https://github.com/Lendle-King/EpiPilot.git" \
  epipilot-install-codex --json
```

For a generic resolved `<ref>`, use the same ref for both the package source and installer argument:

```bash
uvx --from "git+https://github.com/Lendle-King/EpiPilot.git@<ref>" \
  epipilot-install-codex --ref <ref> --json
```

## Required agent behavior

1. Confirm the request is actually to install EpiPilot as a Codex plugin.
2. Check that `codex` exists and supports `codex plugin`.
3. Check that `uv`/`uvx` is available.
4. Resolve repository/ref from the supplied GitHub URL.
5. Run the bootstrap command with `--json` through the normal Codex shell/tool boundary.
6. Treat a non-zero exit, malformed JSON, `ok != true`, `installed != true`, or `enabled != true` as installation failure.
7. On success, tell the user installation is complete and that a **new Codex thread** is required to load the new plugin skill and MCP tools.
8. Do not claim the current already-running thread has loaded EpiPilot after installation.

The bootstrap itself persists the EpiPilot runtime, adds/upgrades the Git marketplace, installs the plugin, and verifies Codex's final installed/enabled state.

## Safety boundary

- Do not use `curl | sh`, `wget | sh`, or an opaque remote shell installer.
- Do not disable Codex sandbox/approval controls to install EpiPilot.
- Do not delete an existing conflicting marketplace configuration automatically.
- Do not rewrite unrelated Codex configuration.
- Do not infer success from process exit alone; validate the bootstrap JSON.
- If `uv` is missing, report that prerequisite instead of silently installing a system package manager or modifying the user's shell profile.

## Expected success result

A successful bootstrap returns JSON containing at least:

```json
{
  "ok": true,
  "marketplace_name": "epipilot",
  "plugin_name": "epipilot",
  "installed": true,
  "enabled": true,
  "restart_required": true
}
```

After success, the appropriate user-facing result is concise: installation completed, EpiPilot is installed and enabled, and the user should start a new Codex thread.
