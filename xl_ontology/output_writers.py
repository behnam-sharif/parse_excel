"""xl_ontology.output_writers — atomic writers for the four required
artifacts (plan §9; spec §12.1, §13.4.1).

All writes go to a sibling ``<final>.tmp`` file then ``os.replace`` to
the final path. ``os.replace`` is atomic on Windows for same-volume
targets, which holds because the temp file is always created in the
same directory as the final file (plan §13 R6).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import List

from pydantic import BaseModel

from core.paths import (
    get_agent_outputs_path,
    get_cross_agent_findings_path,
    get_model_understanding_json_path,
    get_model_understanding_md_path,
    get_ontology_output_path,
    get_summary_model_path,
    get_table_tags_path,
)
from xl_ontology.schemas import (
    AgentOutputsFile,
    CrossAgentFinding,
    ModelUnderstandingFile,
    SummaryModel,
    TableTagsFile,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_VERSION_FILE_RE = re.compile(r"^summary_model_v(\d+)\.json$")


def _atomic_write_json(path: Path, model: BaseModel) -> Path:
    """Serialise ``model`` to indent-2 JSON and atomically replace
    ``path``. Returns the final path on success."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    payload = model.model_dump(mode="json", by_alias=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:  # pragma: no cover
            pass
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------------------
# §9.1 summary_model_v{N}.json
# ---------------------------------------------------------------------------

def list_summary_model_versions(
    run_id: str, base: str = "data/output"
) -> List[int]:
    """Return all existing summary_model_v{N}.json version numbers
    under the run's ontology_output directory, sorted ascending. Empty
    list if the directory does not exist or contains no versions."""
    out_dir = get_ontology_output_path(run_id, base)
    if not out_dir.is_dir():
        return []
    versions: List[int] = []
    for child in out_dir.iterdir():
        m = _VERSION_FILE_RE.match(child.name)
        if m:
            versions.append(int(m.group(1)))
    versions.sort()
    return versions


def next_summary_model_version(
    run_id: str, base: str = "data/output", *, seed_phase: bool
) -> int:
    """Decide the version to write. Seed phase always writes v0,
    overwriting any prior seed file. Enrichment writes
    ``max(existing) + 1`` (and v1 if no prior exists, though enrichment
    is only meaningful after a seed)."""
    if seed_phase:
        return 0
    existing = list_summary_model_versions(run_id, base)
    return (max(existing) + 1) if existing else 1


def write_summary_model(
    summary: SummaryModel,
    run_id: str,
    version: int,
    base: str = "data/output",
) -> Path:
    """Atomically write summary_model_v{version}.json."""
    return _atomic_write_json(
        get_summary_model_path(run_id, version, base), summary
    )


# ---------------------------------------------------------------------------
# §9.2 ontology_agent_outputs.json
# ---------------------------------------------------------------------------

def write_agent_outputs(
    agent_outputs_file: AgentOutputsFile,
    run_id: str,
    base: str = "data/output",
) -> Path:
    """Atomically overwrite ontology_agent_outputs.json."""
    return _atomic_write_json(
        get_agent_outputs_path(run_id, base), agent_outputs_file
    )


# ---------------------------------------------------------------------------
# §9.3 ontology_table_tags.json
# ---------------------------------------------------------------------------

def write_table_tags(
    table_tags_file: TableTagsFile,
    run_id: str,
    base: str = "data/output",
) -> Path:
    """Atomically overwrite ontology_table_tags.json."""
    return _atomic_write_json(
        get_table_tags_path(run_id, base), table_tags_file
    )


# ---------------------------------------------------------------------------
# v1.B — Pass 2 (comprehension layer) writers
# ---------------------------------------------------------------------------

def write_model_understanding_md(
    prose: str, run_id: str, base: str = "data/output"
) -> Path:
    """Atomically write model_understanding.md (Pass 2 prose narrative)."""
    path = get_model_understanding_md_path(run_id, base)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(prose)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:  # pragma: no cover
            pass
    os.replace(tmp, path)
    return path


def write_model_understanding_json(
    file: ModelUnderstandingFile, run_id: str, base: str = "data/output"
) -> Path:
    """Atomically write model_understanding.json (Pass 2 structured findings)."""
    return _atomic_write_json(
        get_model_understanding_json_path(run_id, base), file
    )


def append_cross_agent_finding(
    finding: CrossAgentFinding, run_id: str, base: str = "data/output"
) -> Path:
    """Append one CrossAgentFinding row to cross_agent_findings.jsonl.

    Append-only by design — Pass 2 may emit multiple findings across
    stages and we want them ordered by emission time.
    """
    path = get_cross_agent_findings_path(run_id, base)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = finding.model_dump(mode="json", by_alias=True)
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8", newline="") as f:
        f.write(line)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:  # pragma: no cover
            pass
    return path


__all__ = [
    "append_cross_agent_finding",
    "list_summary_model_versions",
    "next_summary_model_version",
    "write_agent_outputs",
    "write_model_understanding_json",
    "write_model_understanding_md",
    "write_summary_model",
    "write_table_tags",
]
