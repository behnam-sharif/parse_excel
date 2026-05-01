"""xl_ontology.trace — JSONL trace writer (plan §9.4 / spec §13.4.5).

Per-event flush + fsync so the orchestrator UI can tail the file
progressively while the run is still in flight. The file lives at
``ontology_output/ontology_trace.jsonl`` (path resolved via
``core.paths.get_ontology_trace_path``).

Seed runs open the writer with ``append=False`` (overwrite). Lazy
enrichment opens with ``append=True`` so the seed-phase events are
preserved.

A second writer class, ``BuildErrorWriter``, follows the same shape
for ``ontology_build_errors.jsonl``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class _JsonlWriter:
    """Shared JSONL writer with per-event flush + fsync."""

    def __init__(self, path: Path, *, append: bool = False) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        # newline="" so we control line endings; encoding utf-8 for portability.
        self._fp = open(self._path, mode, encoding="utf-8", newline="")

    @property
    def path(self) -> Path:
        return self._path

    def emit(self, event: BaseModel | dict[str, Any]) -> None:
        if isinstance(event, BaseModel):
            payload = event.model_dump(mode="json", by_alias=True)
        else:
            payload = event
        line = json.dumps(payload, ensure_ascii=False, sort_keys=False)
        self._fp.write(line + "\n")
        self._fp.flush()
        try:
            os.fsync(self._fp.fileno())
        except OSError:  # pragma: no cover - non-fatal on some filesystems
            pass

    def close(self) -> None:
        if not self._fp.closed:
            self._fp.close()

    def __enter__(self) -> "_JsonlWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class TraceWriter(_JsonlWriter):
    """Writes ontology_trace.jsonl. See plan §9.4."""


class BuildErrorWriter(_JsonlWriter):
    """Writes ontology_build_errors.jsonl. Always created (may be
    empty) so the UI Errors tab has a stable file to read."""


class NodeTypeOverrideWriter(_JsonlWriter):
    """Writes node_type_override_log.jsonl (REVISION 3 / spec §12.11).

    Wired into ``record_classification``: whenever a candidate sets
    ``node_type_override_proposed=True`` the builder constructs a
    ``NodeTypeOverrideRecord`` and this writer emits a line. Agents
    do not call the writer directly — the coupling lives in the
    builder so an override cannot be recorded without writing the
    log line.
    """


__all__ = ["BuildErrorWriter", "NodeTypeOverrideWriter", "TraceWriter"]
