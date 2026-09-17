"""Read committed repository metadata without importing or executing project code."""

from __future__ import annotations

import ast
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from epipilot.web.models import Card, DocumentView, RepositoryView, SourceRef

_SOURCE_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java"}
_EXCLUDED = {"node_modules", "vendor", "dist", "build", ".git", ".venv"}
_SENSITIVE = re.compile(r"(?:secret|credential|token|password|private.key|\.env)", re.I)


@dataclass(slots=True)
class RepositoryInspector:
    root: Path
    max_files: int = 200
    max_blob_bytes: int = 131_072
    _cached: RepositoryView | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.root = self.root.resolve()
        if self.max_files < 1 or self.max_blob_bytes < 1:
            raise ValueError("repository inspection limits must be positive")

    def _git(self, *args: str) -> bytes:
        environment = dict(os.environ)
        environment.update(GIT_OPTIONAL_LOCKS="0", GIT_NO_REPLACE_OBJECTS="1")
        try:
            result = subprocess.run(
                ["git", "--no-pager", "-c", "core.fsmonitor=false", "-C", str(self.root), *args],
                capture_output=True,
                timeout=10,
                check=True,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError("repository must contain a readable Git commit") from error
        return result.stdout

    def inspect(self) -> RepositoryView:
        revision = self._git("rev-parse", "--verify", "HEAD^{commit}").decode().strip()
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision):
            raise ValueError("invalid repository commit")
        branch = self._git("rev-parse", "--abbrev-ref", "HEAD").decode().strip()
        dirty = bool(self._git("status", "--porcelain", "--untracked-files=normal"))
        if self._cached is not None and self._cached.revision == revision:
            return self._cached.model_copy(update={"dirty": dirty, "branch": branch})

        entries = self._git("ls-tree", "-rz", "-l", revision).split(b"\0")
        candidates: list[tuple[str, str, int]] = []
        total = 0
        for entry in entries:
            if not entry:
                continue
            total += 1
            metadata, encoded_path = entry.split(b"\t", 1)
            mode, kind, oid, size = metadata.split()
            path = encoded_path.decode("utf-8", errors="replace")
            parts = PurePosixPath(path).parts
            if mode not in {b"100644", b"100755"} or kind != b"blob":
                continue
            if any(part in _EXCLUDED for part in parts) or _SENSITIVE.search(path):
                continue
            suffix = PurePosixPath(path).suffix.lower()
            if suffix == ".md" or suffix in _SOURCE_SUFFIXES:
                candidates.append((path, oid.decode("ascii"), int(size)))

        candidates.sort(key=lambda entry: (entry[0].lower() != "readme.md", entry[0]))
        documents: list[DocumentView] = []
        modules: list[Card] = []
        warnings: list[str] = []
        selected = candidates[: self.max_files]
        for path, oid, size in selected:
            if size > self.max_blob_bytes:
                warnings.append(f"跳过过大文件：{path}")
                continue
            raw = self._git("cat-file", "blob", oid)
            if b"\0" in raw:
                warnings.append(f"跳过二进制内容：{path}")
                continue
            text = raw.decode("utf-8", errors="replace")
            if PurePosixPath(path).suffix.lower() == ".md":
                documents.append(DocumentView(
                    path=path, text=text[:16_000], truncated=len(text) > 16_000,
                    revision=revision,
                ))
                continue
            summary = "已定位源码文件；尚未建立行为级解释。"
            details = {"分析方式": "静态文件检查，不代表运行验证", "文件": path}
            if path.endswith(".py"):
                try:
                    tree = ast.parse(text)
                    summary = (ast.get_docstring(tree) or summary)[:800]
                    names = [node.name for node in tree.body
                             if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
                    imports = [node.module for node in ast.walk(tree)
                               if isinstance(node, ast.ImportFrom) and node.module]
                    details["公开结构"] = ", ".join(names[:20]) or "未发现顶层类或函数"
                    details["静态依赖（非调用图）"] = ", ".join(dict.fromkeys(imports))[:800] or "未记录"
                except (SyntaxError, ValueError, RecursionError):
                    details["分析限制"] = "Python AST 解析未成功，不能据此推断代码有效。"
            modules.append(Card(
                id=f"module-{len(modules)}", title=path, status="static_inspection",
                summary=summary, details=details,
                refs=(SourceRef(kind="source_code", location=path, revision=revision),),
            ))
        truncated = len(candidates) > len(selected)
        if truncated:
            warnings.append(f"仅扫描前 {self.max_files} 个候选文件，项目地图不完整。")
        warnings.append("项目地图基于已提交版本；未提交修改不纳入源码解释。")
        view = RepositoryView(
            name=self.root.name, revision=revision, branch=branch, dirty=dirty,
            total_files=total, scanned_files=len(selected), truncated=truncated,
            documents=tuple(documents), modules=tuple(modules), warnings=tuple(warnings),
        )
        self._cached = view
        return view
