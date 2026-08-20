# Security Policy

EpiPilot coordinates coding agents that may execute commands, edit repositories, access external services, and handle sensitive project context. Security boundaries are therefore part of the core architecture, not an optional hardening layer.

## Reporting a vulnerability

Please do not open a public issue for a vulnerability that could expose credentials, private data, arbitrary command execution, sandbox escape, unauthorized repository modification, or privilege escalation.

Until a dedicated security contact is configured, use GitHub's private vulnerability reporting feature for this repository when available.

A useful report includes:

- affected commit/version;
- impact and threat model;
- minimal reproduction steps;
- whether secrets or private data were exposed;
- suggested mitigation if known.

## Security expectations for contributors

Contributions must preserve these boundaries:

- executor output is untrusted input until validated;
- subprocess arguments must not be constructed through unsafe shell interpolation;
- credentials and tokens must never be persisted in source, fixtures, logs, prompts, or artifacts;
- repository and filesystem write scopes must be explicit;
- external actions with irreversible or high-risk impact require an authority check;
- sandbox/worktree isolation must not be bypassed for convenience;
- logs should prefer structured/redacted values over raw external responses;
- task completion must remain independently verifiable.

## Sensitive test data

Use synthetic test fixtures. Real user prompts, screenshots, private repositories, API responses containing secrets, or production logs must not be committed unless they have been explicitly sanitized and approved for public release.

## Supported versions

EpiPilot is pre-1.0. Security fixes are applied to the current development line. A formal supported-version matrix will be introduced before the first stable release.
