"""Deterministic evidence extraction: every evidence file becomes a list of Observations."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from ..data import Dataset
from ..normalize import identifiers_in
from ..utils import RESOURCE_RE, canonical_system
from . import catalog, handover_parser, json_parser, log_parser, pdf_parser, sql_parser
from .observation import Observation

PARSERS = {".sql": sql_parser.parse, ".json": json_parser.parse, ".txt": handover_parser.parse,
           ".log": log_parser.parse, ".pdf": pdf_parser.parse}


class Extractor:
    def __init__(self, ds: Dataset):
        self.ds = ds
        self.obs: list[Observation] = []
        self.coverage: dict[str, list[tuple[str, datetime]]] = defaultdict(list)  # file -> (system, t)
        self.aliases: dict[str, str] = {}     # catalog alias (statement no, file name) -> resource_id
        self.display_ids: dict[str, str] = {}  # any catalog display id, incl. identifier-like ones (user_17)
        self.pdf_text: dict[str, str] = {}
        self.unparsed: list[str] = []         # files only the document gate can read (+ parse errors)

    def run(self) -> "Extractor":
        catalog.parse(self)
        for fid, m in sorted(self.ds.manifest.items()):
            path = self.ds.dataset_dir / m["file_path"]
            parser = PARSERS.get(path.suffix.lower())
            if parser:
                parser(self, fid, path, m["captured"])
            else:
                self.unparsed.append(fid)  # images: handled by the document LLM gate
        return self

    def add(self, **kw) -> Observation:
        o = Observation(**kw)
        o.system = canonical_system(o.resource_id, o.system)
        self.obs.append(o)
        return o

    def observations_from_text(self, source, kind, manifest_at, t, text, via) -> None:
        """Free text (PDF text layer, OCR): one observation per resource id or catalog alias mentioned."""
        rids = set(RESOURCE_RE.findall(text))
        rids |= {rid for alias, rid in self.aliases.items() if alias in text}
        ids = frozenset(identifiers_in(text))
        lowered = text.lower()
        for rid in sorted(rids):
            cat = None
            if rid.startswith("support_") and ("attachment" in lowered or "uploader" in lowered):
                cat = "SUPPORT_ATTACHMENT"
            self.add(source=source, source_kind=kind, manifest_at=manifest_at, t=t, system=None,
                     resource_id=rid, fields={"text": text[:4000]}, ids=ids, category=cat, via=via)
