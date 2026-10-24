"""PDF text layer via pypdf. Files without usable text are left for the document gate."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path


def parse(ex, fid: str, path: Path, manifest_at: datetime) -> None:
    try:
        from pypdf import PdfReader
        text = "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
    except Exception as exc:  # noqa: BLE001 - recorded; the document gate may still read the file
        text = ""
        ex.unparsed.append(f"{fid}:pypdf:{exc}")
    ex.pdf_text[fid] = text
    if len(text.strip()) < 40:
        ex.unparsed.append(fid)
        return
    ex.observations_from_text(fid, "file", manifest_at, manifest_at, text, via="pdf_text")
