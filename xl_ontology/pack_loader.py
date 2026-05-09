"""xl_ontology.pack_loader — load + validate an ontology pack.

The single entry point is ``load_pack(packs_root, pack_name)`` which
returns a fully validated ``OntologyPack``. The loader implements
plan §5 in full:

- §5.1 Discovery and structural validation (4 nodes × 3 files +
  pack_overview.context.md required).
- §5.2 ``<node>.txt``: parse §1 IDENTITY block; keep the full body
  for prompt injection.
- §5.3 ``<node>.context.md``: full text, no structural parsing.
- §5.4 ``<node>.heuristics.md``: parse YAML front-matter chunks
  inside ``yaml`` fenced code blocks; capture each chunk's prose body.
- §5.5 ``pack_overview.context.md``: optional YAML front-matter for
  budgets, then prose body.
- §5.6 Indexing happens at the call site (loader returns the raw
  ``OntologyPack``; callers index per (node, trigger) as needed).
- §5.7 Failures raise typed exceptions: PackNotFound, PackMalformed,
  IdentityMismatch, HeuristicMalformed, UnregisteredTrigger.

Plan §13 R2 mitigation: every active/experimental heuristic's
``trigger`` field is cross-checked against
``xl_ontology.agents.base.TRIGGER_REGISTRY`` (the single canonical
source of registered triggers). An unknown trigger raises
``UnregisteredTrigger`` and the load fails — adding a heuristic with
a new trigger requires a code change AND a pack change.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Tuple, get_args

import yaml

from xl_ontology.agents.base import TRIGGER_REGISTRY
from xl_ontology.schemas import (
    ConceptPackEntry,
    Heuristic,
    NodeIdentity,
    NodeName,
    OntologyNode,
    OntologyPack,
    PackBudgets,
    PackWarning,
    SheetPackEntry,
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PackLoaderError(Exception):
    """Base for all pack loader failures."""


class PackNotFound(PackLoaderError):
    """The pack directory does not exist."""


class PackMalformed(PackLoaderError):
    """Required pack files are missing or pack structure is invalid."""


class IdentityMismatch(PackLoaderError):
    """A <node>.txt IDENTITY block disagrees with the pack/file context."""


class HeuristicMalformed(PackLoaderError):
    """A heuristic chunk is missing required front-matter or fails to parse."""


class UnregisteredTrigger(PackLoaderError):
    """A heuristic references a trigger not in KNOWN_TRIGGERS (plan §13 R2)."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NODE_NAMES: Tuple[str, ...] = get_args(NodeName)  # ("overall_model", ...)

_PACK_OVERVIEW_FILENAME = "pack_overview.context.md"
_NODE_TXT_TEMPLATE = "ontology_{node}.txt"
_NODE_CONTEXT_TEMPLATE = "ontology_{node}.context.md"
_NODE_HEURISTICS_TEMPLATE = "ontology_{node}.heuristics.md"

# Section headings inside <node>.txt look like "1. IDENTITY",
# "2. PURPOSE", etc. The regex matches the start of any such heading.
_TXT_SECTION_HEADING_RE = re.compile(r"^\d+\.\s+[A-Z]")

# Heuristic chunks live inside ```yaml ... ``` fenced blocks.
_YAML_FENCE_RE = re.compile(r"```yaml\s*\n(.*?)\n```", re.DOTALL)

# A pack-overview file may begin with a `---\n...\n---\n` YAML front-matter.
_PACK_OVERVIEW_FRONT_MATTER_RE = re.compile(
    r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def load_pack(packs_root: Path | str, pack_name: str) -> OntologyPack:
    """Load and validate an ontology pack from disk.

    Resolves ``pack_dir = packs_root / pack_name``. Both must exist.
    Plan §2.1 (b): ``packs_root`` may point at the built-in
    ``xl_ontology/packs`` directory or at any user-supplied directory.
    """
    packs_root = Path(packs_root)
    pack_dir = packs_root / pack_name

    if not pack_dir.is_dir():
        raise PackNotFound(
            f"Pack directory not found: {pack_dir} "
            f"(packs_root={packs_root}, pack_name={pack_name})"
        )

    # §5.1 — pack-level overview must exist.
    overview_path = pack_dir / _PACK_OVERVIEW_FILENAME
    if not overview_path.is_file():
        raise PackMalformed(
            f"Pack {pack_name!r} missing required file: {_PACK_OVERVIEW_FILENAME}"
        )

    # §5.1 — every node has all three files.
    missing: List[str] = []
    expected_files: set[str] = {_PACK_OVERVIEW_FILENAME}
    for node in NODE_NAMES:
        for tmpl in (
            _NODE_TXT_TEMPLATE,
            _NODE_CONTEXT_TEMPLATE,
            _NODE_HEURISTICS_TEMPLATE,
        ):
            fname = tmpl.format(node=node)
            expected_files.add(fname)
            if not (pack_dir / fname).is_file():
                missing.append(fname)
    if missing:
        raise PackMalformed(
            f"Pack {pack_name!r} missing required files: {sorted(missing)}"
        )

    # §5.1 step 4 — surface unknown extra files as PackWarnings (the
    # runner converts them into WarningEvent trace entries; plan §5.7
    # makes the warning policy explicit instead of "log a warning").
    # F.1 — the new layout adds bim.txt + ontology_bim_concepts/ +
    # ontology_bim_sheets/ at pack root.  Only the file is surfaced
    # at the pack root level (the iteration below skips directories);
    # whitelist bim.txt so it's not flagged.
    expected_files.add(_BIM_OVERVIEW_FILENAME)
    warnings: List[PackWarning] = []
    for entry in pack_dir.iterdir():
        if entry.is_file() and entry.name not in expected_files:
            warnings.append(
                PackWarning(
                    category="unknown_pack_file",
                    message=(
                        f"Unknown extra file in pack {pack_name!r}: "
                        f"{entry.name}. Loader ignored it."
                    ),
                    file_path=str(entry),
                )
            )

    # §5.5 — pack overview front-matter (optional) + prose body.
    overview_text = overview_path.read_text(encoding="utf-8")
    budgets, pack_overview_body = _parse_pack_overview(overview_text)

    # §5.2–§5.4 — load each node's three files.
    nodes: Dict[NodeName, OntologyNode] = {}
    for node_str in NODE_NAMES:
        node: NodeName = node_str  # type: ignore[assignment]
        nodes[node] = _load_node(pack_dir, node, pack_name)

    # Pack-level version: take max(node versions) as a stable string.
    # The pack-overview front-matter could override this in a future
    # iteration; for v0 we keep it derived (plan §2.12 default).
    pack_version = _max_version_string([n.identity.version for n in nodes.values()])

    # F.1 — load new directory-keyed concept + sheet trees.  Empty dicts
    # when the new layout isn't present; legacy packs continue to work.
    concepts = _load_concept_tree(pack_dir)
    sheets = _load_sheet_tree(pack_dir)
    bim_overview_text = _load_bim_overview(pack_dir)

    return OntologyPack(
        id=pack_name,
        version=pack_version,
        pack_dir=pack_dir,
        pack_overview_text=pack_overview_body,
        budgets=budgets,
        nodes=nodes,
        warnings=warnings,
        concepts=concepts,
        sheets=sheets,
        bim_overview_text=bim_overview_text,
    )


# ---------------------------------------------------------------------------
# F.1 — directory-walker loaders for the new layout
# (ontology_bim_concepts/, ontology_bim_sheets/, bim.txt)
# ---------------------------------------------------------------------------

_CONCEPT_TREE_DIRNAME = "ontology_bim_concepts"
_SHEET_TREE_DIRNAME = "ontology_bim_sheets"
_BIM_OVERVIEW_FILENAME = "bim.txt"
_CONCEPT_BRANCHES: Tuple[str, ...] = ("inputs", "calculations", "results")


def _load_concept_tree(pack_dir: Path) -> Dict[str, ConceptPackEntry]:
    """Walk ``ontology_bim_concepts/{branch}/{concept}/`` and load each
    concept's ``lexicon.json`` + ``rules_pass1.md`` + ``rules_pass2.md``
    into a ``ConceptPackEntry``.  Returns ``{}`` when the tree doesn't
    exist (legacy packs)."""
    tree_root = pack_dir / _CONCEPT_TREE_DIRNAME
    if not tree_root.is_dir():
        return {}

    out: Dict[str, ConceptPackEntry] = {}
    import json as _json
    for branch_name in _CONCEPT_BRANCHES:
        branch_dir = tree_root / branch_name
        if not branch_dir.is_dir():
            continue
        for concept_dir in sorted(branch_dir.iterdir()):
            if not concept_dir.is_dir():
                continue
            concept_id = f"{branch_name}.{concept_dir.name}"
            lexicon_path = concept_dir / "lexicon.json"
            rules_pass1_path = concept_dir / "rules_pass1.md"
            rules_pass2_path = concept_dir / "rules_pass2.md"
            terms: List[str] = []
            if lexicon_path.is_file():
                try:
                    data = _json.loads(lexicon_path.read_text(encoding="utf-8"))
                    raw_terms = data.get("terms") if isinstance(data, dict) else None
                    if isinstance(raw_terms, list):
                        terms = [str(t) for t in raw_terms]
                except Exception:
                    # Malformed lexicon.json — leave empty; F.2 will tighten this.
                    terms = []
            rules_p1 = (
                rules_pass1_path.read_text(encoding="utf-8")
                if rules_pass1_path.is_file() else ""
            )
            rules_p2 = (
                rules_pass2_path.read_text(encoding="utf-8")
                if rules_pass2_path.is_file() else ""
            )
            out[concept_id] = ConceptPackEntry(
                concept_id=concept_id,
                branch=branch_name,
                concept_name=concept_dir.name,
                folder_path=concept_dir,
                lexicon_terms=terms,
                rules_pass1_md=rules_p1,
                rules_pass2_md=rules_p2,
            )
    return out


def _load_sheet_tree(pack_dir: Path) -> Dict[str, SheetPackEntry]:
    """Walk ``ontology_bim_sheets/{type}/`` and load each sheet type's
    ``lexicon.json`` + ``context.md`` into a ``SheetPackEntry``.
    Returns ``{}`` when the tree doesn't exist (legacy packs)."""
    tree_root = pack_dir / _SHEET_TREE_DIRNAME
    if not tree_root.is_dir():
        return {}

    out: Dict[str, SheetPackEntry] = {}
    import json as _json
    for sheet_dir in sorted(tree_root.iterdir()):
        if not sheet_dir.is_dir():
            continue
        sheet_type = sheet_dir.name
        lexicon_path = sheet_dir / "lexicon.json"
        context_path = sheet_dir / "context.md"
        terms: List[str] = []
        if lexicon_path.is_file():
            try:
                data = _json.loads(lexicon_path.read_text(encoding="utf-8"))
                raw_terms = data.get("terms") if isinstance(data, dict) else None
                if isinstance(raw_terms, list):
                    terms = [str(t) for t in raw_terms]
            except Exception:
                terms = []
        context_text = (
            context_path.read_text(encoding="utf-8")
            if context_path.is_file() else ""
        )
        out[sheet_type] = SheetPackEntry(
            sheet_type=sheet_type,
            folder_path=sheet_dir,
            lexicon_terms=terms,
            context_md=context_text,
        )
    return out


def _load_bim_overview(pack_dir: Path) -> str:
    """Read ``bim.txt`` at pack root.  Empty string when absent (legacy
    packs); F.1 created this from a verbatim copy of
    ``pack_overview.context.md``."""
    p = pack_dir / _BIM_OVERVIEW_FILENAME
    if not p.is_file():
        return ""
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# §5.5 pack_overview.context.md
# ---------------------------------------------------------------------------

def _parse_pack_overview(text: str) -> Tuple[PackBudgets, str]:
    """Parse optional leading YAML front-matter into PackBudgets;
    return (budgets, prose-body). Falls back to default budgets when
    no front-matter is present (plan §2.6)."""
    m = _PACK_OVERVIEW_FRONT_MATTER_RE.match(text)
    if not m:
        return PackBudgets(), text
    yaml_text = m.group(1)
    try:
        data = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError as e:  # pragma: no cover - defensive
        raise PackMalformed(
            f"pack_overview.context.md front-matter is not valid YAML: {e}"
        ) from e
    if not isinstance(data, dict):
        raise PackMalformed(
            "pack_overview.context.md front-matter must be a mapping"
        )
    try:
        budgets = PackBudgets(**data)
    except Exception as e:
        raise PackMalformed(
            f"pack_overview.context.md front-matter rejected by PackBudgets: {e}"
        ) from e
    body = text[m.end():]
    return budgets, body


# ---------------------------------------------------------------------------
# §5.2–§5.4 per-node loading
# ---------------------------------------------------------------------------

def _load_node(pack_dir: Path, node: NodeName, pack_name: str) -> OntologyNode:
    txt_path = pack_dir / _NODE_TXT_TEMPLATE.format(node=node)
    ctx_path = pack_dir / _NODE_CONTEXT_TEMPLATE.format(node=node)
    heu_path = pack_dir / _NODE_HEURISTICS_TEMPLATE.format(node=node)

    txt_text = txt_path.read_text(encoding="utf-8")
    context_text = ctx_path.read_text(encoding="utf-8")
    heuristics_text = heu_path.read_text(encoding="utf-8")

    identity = _parse_identity_block(txt_text, txt_path)
    if identity.ontology_pack != pack_name:
        raise IdentityMismatch(
            f"{txt_path.name}: IDENTITY ontology_pack={identity.ontology_pack!r} "
            f"does not match pack directory name {pack_name!r}"
        )
    if identity.ontology_node != node:
        raise IdentityMismatch(
            f"{txt_path.name}: IDENTITY ontology_node={identity.ontology_node!r} "
            f"does not match expected node {node!r}"
        )

    heuristics = _parse_heuristics_file(heuristics_text, heu_path, node)

    return OntologyNode(
        node_name=node,
        txt_path=txt_path,
        context_md_path=ctx_path,
        heuristics_md_path=heu_path,
        txt_text=txt_text,
        context_text=context_text,
        identity=identity,
        heuristics=heuristics,
    )


# ---------------------------------------------------------------------------
# §5.2 IDENTITY parser
# ---------------------------------------------------------------------------

_IDENTITY_REQUIRED_KEYS = (
    "ontology_pack",
    "ontology_node",
    "version",
    "model_family",
    "expected_scale",
    "llm_budget_share",
)


def _parse_identity_block(text: str, path: Path) -> NodeIdentity:
    """Locate the §1 IDENTITY block and parse it as `key: value` pairs.

    Continuation lines (indented under a key) are joined into the
    value with single-space separators. Parsing stops at the next
    numbered ALL-CAPS section heading (e.g., "2. PURPOSE").
    """
    lines = text.splitlines()

    # Find the IDENTITY heading line.
    start_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("1.") and "IDENTITY" in line.upper():
            start_idx = i
            break
    if start_idx is None:
        raise PackMalformed(
            f"{path.name}: missing '1. IDENTITY' section heading"
        )

    # Skip the heading line, the underline (`-----`) if present, and
    # any leading blank lines.
    cursor = start_idx + 1
    while cursor < len(lines) and (
        not lines[cursor].strip()
        or set(lines[cursor].strip()) <= {"-"}
    ):
        cursor += 1

    # Walk identity lines until the next numbered section heading.
    identity_lines: List[str] = []
    while cursor < len(lines):
        line = lines[cursor]
        if _TXT_SECTION_HEADING_RE.match(line.strip()) and not line.startswith(" "):
            # Avoid false-positives for continuation lines that happen to
            # start with a digit; section headings are always at column 0.
            if not line.startswith(" "):
                break
        identity_lines.append(line)
        cursor += 1

    parsed = _parse_indented_kv_block(identity_lines)
    missing = [k for k in _IDENTITY_REQUIRED_KEYS if k not in parsed]
    if missing:
        raise PackMalformed(
            f"{path.name}: IDENTITY block missing keys: {sorted(missing)}"
        )

    try:
        return NodeIdentity(
            ontology_pack=parsed["ontology_pack"],
            ontology_node=parsed["ontology_node"],  # validated by Literal
            version=parsed["version"],
            model_family=parsed["model_family"],
            expected_scale=parsed["expected_scale"],
            llm_budget_share=parsed["llm_budget_share"],
        )
    except Exception as e:
        raise PackMalformed(
            f"{path.name}: IDENTITY block rejected by NodeIdentity: {e}"
        ) from e


def _parse_indented_kv_block(lines: List[str]) -> Dict[str, str]:
    """Parse a `key: value` block where continuation lines are
    indented. Returns a dict of key -> single-string value (continuation
    lines joined with spaces)."""
    result: Dict[str, str] = {}
    current_key: str | None = None
    current_parts: List[str] = []

    def commit() -> None:
        if current_key is not None:
            result[current_key] = " ".join(p.strip() for p in current_parts).strip()

    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        is_continuation = raw.startswith((" ", "\t")) and current_key is not None
        if is_continuation:
            current_parts.append(stripped)
            continue
        if ":" in raw:
            commit()
            key, _, value = raw.partition(":")
            current_key = key.strip()
            current_parts = [value.strip()] if value.strip() else []
        # Lines without a colon and not a continuation are ignored
        # (commentary inside an identity block is not expected).
    commit()
    return result


# ---------------------------------------------------------------------------
# §5.4 heuristics parser
# ---------------------------------------------------------------------------

_HEURISTIC_REQUIRED_KEYS = (
    "id",
    "applies_to",
    "concept",
    "trigger",
    "priority",
    "last_updated",
    "status",
)

_HEURISTIC_STATUS_VALUES = {"active", "experimental", "deprecated", "subsumed_by_ml"}


def _parse_heuristics_file(
    text: str, path: Path, expected_node: NodeName
) -> List[Heuristic]:
    """Parse all heuristic chunks in a heuristics.md file.

    Each chunk is a ```yaml ... ``` fence followed by a prose body.
    The body extends to the next yaml fence OR the first ``## ``
    heading at column zero (which marks the end of the heuristic
    section, e.g. "## Heuristic ordering")."""
    fences = list(_YAML_FENCE_RE.finditer(text))
    if not fences:
        # Empty heuristics file is allowed (e.g., a future pack with no
        # heuristics yet). Return [].
        return []

    heuristics: List[Heuristic] = []
    seen_ids: set[str] = set()

    for i, m in enumerate(fences):
        # Strip the leading and trailing `---` markers inside the fence,
        # if present, then parse the YAML.
        yaml_inner = m.group(1).strip()
        yaml_inner = _strip_document_markers(yaml_inner)
        try:
            front = yaml.safe_load(yaml_inner) or {}
        except yaml.YAMLError as e:
            raise HeuristicMalformed(
                f"{path.name}: heuristic chunk {i + 1} has invalid YAML "
                f"front-matter: {e}"
            ) from e
        if not isinstance(front, dict):
            raise HeuristicMalformed(
                f"{path.name}: heuristic chunk {i + 1} front-matter must be a mapping"
            )

        missing = [k for k in _HEURISTIC_REQUIRED_KEYS if k not in front]
        if missing:
            raise HeuristicMalformed(
                f"{path.name}: heuristic chunk {i + 1} missing front-matter keys: "
                f"{sorted(missing)}"
            )

        status = front["status"]
        if status not in _HEURISTIC_STATUS_VALUES:
            raise HeuristicMalformed(
                f"{path.name}: heuristic {front.get('id')!r} has unknown "
                f"status={status!r}"
            )

        applies_to = front["applies_to"]
        if applies_to != expected_node:
            raise HeuristicMalformed(
                f"{path.name}: heuristic {front.get('id')!r} applies_to={applies_to!r} "
                f"does not match file node {expected_node!r}"
            )

        h_id = front["id"]
        if h_id in seen_ids:
            raise HeuristicMalformed(
                f"{path.name}: heuristic id {h_id!r} appears more than once"
            )
        seen_ids.add(h_id)

        # Trigger registration check (plan §13 R2). Deprecated heuristics
        # are still validated for trigger registration so deprecation
        # cannot be used to bypass the check.
        trigger = front["trigger"]
        if trigger not in TRIGGER_REGISTRY:
            raise UnregisteredTrigger(
                f"{path.name}: heuristic {h_id!r} references unregistered "
                f"trigger {trigger!r}. Add a TriggerEvaluator entry to "
                f"xl_ontology.agents.base.TRIGGER_REGISTRY before adding "
                f"the heuristic."
            )

        # Body extends from end of this fence to start of next fence (or EOF),
        # truncated at the first `## ` heading.
        body_start = m.end()
        body_end = fences[i + 1].start() if i + 1 < len(fences) else len(text)
        body = _trim_heuristic_body(text[body_start:body_end])

        try:
            heuristics.append(
                Heuristic(
                    id=h_id,
                    applies_to=applies_to,
                    concept=str(front["concept"]),
                    trigger=trigger,
                    priority=int(front["priority"]),
                    last_updated=str(front["last_updated"]),
                    status=status,
                    body_text=body,
                )
            )
        except Exception as e:
            raise HeuristicMalformed(
                f"{path.name}: heuristic {h_id!r} rejected by Heuristic schema: {e}"
            ) from e

    return heuristics


def _strip_document_markers(yaml_text: str) -> str:
    """Strip leading and trailing `---` document markers if present."""
    lines = yaml_text.splitlines()
    if lines and lines[0].strip() == "---":
        lines = lines[1:]
    if lines and lines[-1].strip() == "---":
        lines = lines[:-1]
    return "\n".join(lines)


def _trim_heuristic_body(body: str) -> str:
    """Truncate a heuristic body at the first ``## `` heading at column
    zero (which marks pack-author commentary like "## Heuristic
    ordering" rather than heuristic prose)."""
    out_lines: List[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            break
        out_lines.append(line)
    return "\n".join(out_lines).strip()


# ---------------------------------------------------------------------------
# Misc utilities
# ---------------------------------------------------------------------------

def _max_version_string(versions: List[str]) -> str:
    """Return the lexicographically max version string. v0 keeps this
    cheap; future packs may use semver and this can be replaced."""
    if not versions:
        return "0.0"

    def _key(v: str) -> Tuple[int, ...]:
        parts = []
        for p in v.split("."):
            try:
                parts.append(int(p))
            except ValueError:
                parts.append(0)
        return tuple(parts)

    return max(versions, key=_key)


__all__ = [
    "HeuristicMalformed",
    "IdentityMismatch",
    "PackLoaderError",
    "PackMalformed",
    "PackNotFound",
    "UnregisteredTrigger",
    "load_pack",
]
