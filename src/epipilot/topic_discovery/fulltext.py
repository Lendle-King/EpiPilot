"""Explicitly allowlisted open-access PDF retrieval and optional text extraction."""

from __future__ import annotations

import hashlib
import importlib
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlparse

from epipilot.topic_discovery.models import FullTextArtifact, PaperReference
from epipilot.topic_discovery.providers import HttpTransport, UrllibTransport


class PdfTextExtractor(Protocol):
    def extract(
        self,
        pdf_path: Path,
        *,
        max_pages: int,
        max_characters: int,
    ) -> tuple[str, int]: ...


class PyMuPDFTextExtractor:
    """Optional extractor backed by the PyMuPDF package (install epipilot[pdf])."""

    def extract(
        self,
        pdf_path: Path,
        *,
        max_pages: int,
        max_characters: int,
    ) -> tuple[str, int]:
        try:
            fitz = importlib.import_module("fitz")
        except ImportError as exc:
            raise RuntimeError(
                "PDF text extraction requires the optional epipilot[pdf] extra"
            ) from exc

        open_document = cast(Any, getattr(fitz, "open"))
        document = open_document(str(pdf_path))
        chunks: list[str] = []
        characters = 0
        page_count = min(int(document.page_count), max_pages)
        try:
            for index in range(page_count):
                page = document.load_page(index)
                remaining = max_characters - characters
                if remaining <= 0:
                    break
                page_text = str(page.get_text("text"))[:remaining]
                chunks.append(page_text)
                characters += len(page_text)
        finally:
            document.close()
        return "\n\n".join(chunks), page_count


def download_open_access_pdf(
    paper: PaperReference,
    output_dir: Path,
    *,
    allowed_hosts: Sequence[str],
    transport: HttpTransport | None = None,
    max_bytes: int = 25 * 1024 * 1024,
) -> FullTextArtifact:
    """Download one paper PDF only from an explicitly approved HTTPS host.

    The final URL after redirects is checked as well. The caller owns the allowlist;
    this function never fetches arbitrary URLs merely because they appear in metadata.
    """
    if paper.pdf_url is None:
        raise ValueError("paper has no known open-access PDF URL")
    if not allowed_hosts:
        raise ValueError("an explicit PDF host allowlist is required")
    _require_allowed_https_host(paper.pdf_url, allowed_hosts)

    client = transport or UrllibTransport()
    response = client.get(
        paper.pdf_url,
        headers={"Accept": "application/pdf"},
        timeout=30.0,
        max_bytes=max_bytes,
    )
    _require_allowed_https_host(response.final_url, allowed_hosts)
    if not response.body.startswith(b"%PDF-"):
        raise ValueError("downloaded content is not a PDF")
    if len(response.body) > max_bytes:
        raise ValueError("PDF exceeds configured download size limit")

    root = output_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    filename = hashlib.sha256(paper.id.encode("utf-8")).hexdigest()[:20] + ".pdf"
    destination = (root / filename).resolve()
    if not destination.is_relative_to(root):
        raise ValueError("PDF destination escaped the configured output directory")

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, prefix=".download-", delete=False) as handle:
            temporary_path = Path(handle.name)
            handle.write(response.body)
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()

    return FullTextArtifact(
        paper_id=paper.id,
        source_url=response.final_url,
        pdf_path=str(destination),
        pdf_sha256=hashlib.sha256(response.body).hexdigest(),
        pdf_bytes=len(response.body),
    )


def extract_pdf_text(
    artifact: FullTextArtifact,
    *,
    extractor: PdfTextExtractor | None = None,
    max_pages: int = 50,
    max_characters: int = 250_000,
) -> FullTextArtifact:
    """Extract a bounded text view and save it beside the PDF."""
    if max_pages < 1 or max_characters < 1:
        raise ValueError("PDF extraction limits must be positive")
    pdf_path = Path(artifact.pdf_path).resolve()
    if not pdf_path.is_file():
        raise ValueError("PDF artifact is missing")
    text, page_count = (extractor or PyMuPDFTextExtractor()).extract(
        pdf_path,
        max_pages=max_pages,
        max_characters=max_characters,
    )
    if not text.strip():
        raise ValueError("PDF extraction produced no text")
    text_path = pdf_path.with_suffix(".txt")
    encoded = text.encode("utf-8")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=pdf_path.parent,
            prefix=".extract-",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(encoded)
        os.replace(temporary_path, text_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()

    return artifact.model_copy(
        update={
            "text_path": str(text_path),
            "text_sha256": hashlib.sha256(encoded).hexdigest(),
            "text_characters": len(text),
            "page_count": page_count,
        }
    )


def _require_allowed_https_host(url: str, allowed_hosts: Sequence[str]) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname is None:
        raise ValueError("PDF URLs must use HTTPS and include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("PDF URLs must not contain embedded credentials")
    hostname = parsed.hostname.casefold().rstrip(".")
    approved = {
        host.casefold().rstrip(".")
        for host in allowed_hosts
        if host.strip()
    }
    if not any(hostname == host or hostname.endswith(f".{host}") for host in approved):
        raise ValueError(f"PDF host {hostname!r} is not in the explicit allowlist")
