"""Install the frozen official Linux ARM64 executable after integrity verification."""

import base64
import hashlib
import io
import platform
import tarfile
import urllib.request
from pathlib import Path

if platform.machine() not in {"aarch64", "arm64"}:
    raise ValueError("This frozen Mac Docker image requires Linux ARM64")
url = "https://registry.npmjs.org/@openai/codex/-/codex-0.166.0-linux-arm64.tgz"
integrity = (
    "w0ScAsBr9aZKtIFrh6HN6KU2yjKKeSCzYfbb7DB0Ccj4BYPzeZtUeQprbycLCwuX3AFQdbdjapmLho2iot74jQ=="
)
with urllib.request.urlopen(url, timeout=120) as response:
    raw = response.read(220_000_001)
if len(raw) > 220_000_000 or base64.b64encode(hashlib.sha512(raw).digest()).decode() != integrity:
    raise ValueError("Codex archive integrity mismatch")
with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
    files = {"bin/codex", "bin/codex-code-mode-host", "codex-resources/bwrap", "codex-path/rg"}
    prefix = "package/vendor/aarch64-unknown-linux-musl/"
    found = set()
    for member in archive.getmembers():
        relative = member.name.removeprefix(prefix)
        if member.name.startswith(prefix) and relative in files and member.isfile():
            handle = archive.extractfile(member)
            if handle is None or member.size > 300_000_000:
                raise ValueError("invalid Codex executable")
            target = Path("/opt/codex") / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(handle.read())
            target.chmod(0o755)
            found.add(relative)
    if found != files:
        raise ValueError("Codex executable missing")
Path("/usr/local/bin/codex").symlink_to("/opt/codex/bin/codex")
