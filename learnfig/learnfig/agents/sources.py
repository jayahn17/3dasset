"""Gather source text from files (txt/md/yaml/pdf) for the reader."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Iterable, List, Optional


def pdf_text(path: Path, pages: Optional[str] = None) -> str:
    first = last = None
    if pages:
        a, _, b = pages.partition("-")
        first, last = int(a), int(b or a)
    if shutil.which("pdftotext"):
        cmd = ["pdftotext", "-layout"]
        if first:
            cmd += ["-f", str(first), "-l", str(last)]
        cmd += [str(path), "-"]
        return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("reading PDFs needs the pdftotext CLI (poppler) or `pip install pypdf`") from exc
    reader = PdfReader(str(path))
    lo = (first or 1) - 1
    hi = last or len(reader.pages)
    return "\n".join((reader.pages[i].extract_text() or "") for i in range(lo, min(hi, len(reader.pages))))


def gather(sources: Iterable[str], pages: Optional[str] = None, max_chars: int = 120000) -> str:
    chunks: List[str] = []
    for s in sources:
        p = Path(s)
        if not p.exists():
            chunks.append(f"[inline] {s}")
            continue
        if p.suffix.lower() == ".pdf":
            text = pdf_text(p, pages)
        else:
            text = p.read_text(encoding="utf-8", errors="replace")
        chunks.append(f"### SOURCE: {p.name}" + (f" (pages {pages})" if pages and p.suffix.lower() == ".pdf" else "") + "\n" + text)
    text = "\n\n".join(chunks)
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n[... truncated at {max_chars} characters; narrow the page range for more detail ...]"
    return text
