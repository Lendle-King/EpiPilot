import hashlib
import subprocess
from pathlib import Path

from epipilot.engineering.models import WorkStatus
from epipilot.engineering.service import ManagedWork
from epipilot.runtime.sqlite_event_store import SqliteEventStore


def deliver_report(
    db: Path, project: str, root: Path, operation: str, markdown: str, receipt: str | None = None
) -> tuple[str, str]:
    service = ManagedWork(SqliteEventStore(db), project, root)
    key = hashlib.sha256(operation.encode()).hexdigest()[:24]
    path = f"docs/auto_iteration/round_{key}.md"
    lease = service.acquire(operation, ("docs/auto_iteration",))
    if lease.status not in {WorkStatus.INTEGRATED, WorkStatus.PUBLISHING, WorkStatus.PUSHED}:
        target = Path(lease.cwd) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_text() != markdown:
            raise ValueError("report operation content changed")
        target.write_text(markdown)
        if receipt is not None:
            archive = target.with_suffix(".json")
            if archive.exists() and archive.read_text() != receipt:
                raise ValueError("receipt operation content changed")
            archive.write_text(receipt)
        lease = service.complete(operation)
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"{lease.integrated_commit}:{path}"],
        capture_output=True,
        check=True,
    )
    blob = result.stdout.decode("utf-8")
    if blob != markdown:
        raise ValueError("integrated report content mismatch")
    service.release(operation)
    return path, lease.integrated_commit
