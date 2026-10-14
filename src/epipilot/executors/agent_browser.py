"""Constrained, human-supervised agent-browser launcher.

No DOM reading, ChatGPT submission, scraping or service-to-service requests are
performed by this adapter.  It merely opens a visible browser for a human to use.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass

_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CHATGPT_URL = "https://chatgpt.com/"


class BrowserLaunchError(RuntimeError):
    """The local browser CLI could not start or complete a command."""


def session_for_task(task_id: str) -> str:
    """Stable, non-disclosing session name for one EpiPilot handoff."""
    if not task_id.strip():
        raise ValueError("task id must not be empty")
    return "epipilot-" + hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True, slots=True)
class AgentBrowserLauncher:
    """Open/close a headed ChatGPT session, never automate site interactions.

    Persistent browser restore files can contain authentication material; they
    remain entirely owned by agent-browser, never by EpiPilot's event store.
    """

    binary: str = "agent-browser"
    timeout_seconds: float = 45.0

    def __post_init__(self) -> None:
        if not self.binary or not self.binary.strip() or "\x00" in self.binary:
            raise ValueError("browser executable must be nonempty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout must be positive")

    async def open(self, session: str) -> None:
        self._check_session(session)
        # Do NOT add --allowed-domains: upstream forbids it with --restore.
        await self._run("--session", session, "--restore", "--headed", "open", _CHATGPT_URL)

    async def close(self, session: str) -> None:
        self._check_session(session)
        await self._run("--session", session, "--restore", "close")

    async def _run(self, *args: str) -> None:
        try:
            process = await asyncio.create_subprocess_exec(
                self.binary,
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise BrowserLaunchError("agent-browser executable unavailable") from exc
        try:
            # Discard output: CLI output may include site content or tokens.
            _stdout, _stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout_seconds
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise BrowserLaunchError("agent-browser command timed out") from exc
        if process.returncode != 0:
            raise BrowserLaunchError(f"agent-browser exited with code {process.returncode}")

    @staticmethod
    def _check_session(session: str) -> None:
        if not _SESSION_RE.fullmatch(session):
            raise ValueError("invalid browser session identifier")
