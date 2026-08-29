#!/usr/bin/env python3
"""Source-tree entry point used by the EpiPilot Codex plugin skill."""

from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PLUGIN_ROOT / "src"))

from epipilot.integrations.codex.cli import main  # noqa: E402, I001


if __name__ == "__main__":
    raise SystemExit(main())
