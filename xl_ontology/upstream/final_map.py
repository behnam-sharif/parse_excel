"""Upstream loader: final_table_map.json (V6 finalize output).

Flattens parent entries — each parent contributes zero DAG nodes per
spec §7.3 of xl_sheetagent (parents are navigation-only). The
ontology agents only ever see leaves: standalone entries and the
children flattened out of parent entries.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class FinalMapBbox:
    r1: int
    c1: int
    r2: int
    c2: int

    @classmethod
    def from_dict(cls, d: dict) -> "FinalMapBbox":
        return cls(r1=d["r1"], c1=d["c1"], r2=d["r2"], c2=d["c2"])

    def n_rows(self) -> int:
        return max(0, self.r2 - self.r1 + 1)

    def n_cols(self) -> int:
        return max(0, self.c2 - self.c1 + 1)

    def n_cells(self) -> int:
        return self.n_rows() * self.n_cols()


@dataclass
class FootnoteRef:
    row: int
    col: int
    text: str


@dataclass
class FinalMapEntry:
    """One leaf entry from final_table_map.json. Either a standalone or
    a flattened child of a parent. Parents themselves contribute zero
    entries to the index."""

    kind: str  # "standalone" | "child"
    table_id: str
    coordinates: str
    bbox: FinalMapBbox
    sheet_name: str
    title: Optional[str] = None
    headers: List[List[str]] = field(default_factory=list)
    row_names: Optional[List[str]] = None
    core_region: Optional[FinalMapBbox] = None
    node_type: str = "data_table"
    parent_path: List[str] = field(default_factory=list)
    parent_table_id: Optional[str] = None  # set when kind=="child"
    footnotes_notes_refs: List[FootnoteRef] = field(default_factory=list)
    source_flags: List[str] = field(default_factory=list)


class FinalMapIndex:
    """Indexed view of final_table_map.json. Keyed by table_id."""

    def __init__(self, entries: List[FinalMapEntry]) -> None:
        self._entries: List[FinalMapEntry] = entries
        self._by_id: Dict[str, FinalMapEntry] = {e.table_id: e for e in entries}
        self._by_sheet: Dict[str, List[FinalMapEntry]] = {}
        for e in entries:
            self._by_sheet.setdefault(e.sheet_name, []).append(e)
        for entries_in_sheet in self._by_sheet.values():
            entries_in_sheet.sort(key=lambda e: (e.bbox.r1, e.bbox.c1))

    @classmethod
    def load(cls, path: Path | str) -> "FinalMapIndex":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_raw(raw)

    @classmethod
    def from_raw(cls, raw: dict) -> "FinalMapIndex":
        entries: List[FinalMapEntry] = []
        for sheet_block in raw.values():
            if not isinstance(sheet_block, dict) or "entries" not in sheet_block:
                continue
            for entry_dict in sheet_block["entries"]:
                kind = entry_dict.get("kind", "standalone")
                if kind == "parent":
                    parent_id = entry_dict["table_id"]
                    for child in entry_dict.get("children", []):
                        entries.append(_parse_entry(child, "child", parent_id))
                    continue
                entries.append(_parse_entry(entry_dict, "standalone", None))
        return cls(entries)

    def get(self, table_id: str) -> Optional[FinalMapEntry]:
        return self._by_id.get(table_id)

    def all_entries(self) -> List[FinalMapEntry]:
        return list(self._entries)

    def entries_for_sheet(self, sheet_name: str) -> List[FinalMapEntry]:
        return list(self._by_sheet.get(sheet_name, []))

    def sheet_names(self) -> List[str]:
        return list(self._by_sheet.keys())

    def __len__(self) -> int:
        return len(self._entries)


def _parse_entry(d: dict, kind: str, parent_id: Optional[str]) -> FinalMapEntry:
    return FinalMapEntry(
        kind=kind,
        table_id=d["table_id"],
        coordinates=d.get("coordinates", ""),
        bbox=FinalMapBbox.from_dict(d["bbox"]),
        sheet_name=d.get("sheet_name", ""),
        title=d.get("title"),
        headers=list(d.get("headers", []) or []),
        row_names=d.get("row_names"),
        core_region=(
            FinalMapBbox.from_dict(d["core_region"])
            if d.get("core_region")
            else None
        ),
        node_type=d.get("node_type", "data_table"),
        parent_path=list(d.get("parent_path", []) or []),
        parent_table_id=parent_id,
        footnotes_notes_refs=[
            FootnoteRef(
                row=int(f.get("row", 0)),
                col=int(f.get("col", 0)),
                text=str(f.get("text", "")),
            )
            for f in d.get("footnotes_notes_refs", []) or []
        ],
        source_flags=list(d.get("source_flags", []) or []),
    )
