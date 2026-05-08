"""B.1 — fold Pass-1 verdicts back into the in-memory ``FinalMapIndex``.

V6 finalize tags every leaf with a ``node_type`` (`input` / `calc` / `result`
/ `mixed` / `metadata` / `data_table` / `setting`).  V6's classifier is
heuristic and frequently lands on `mixed` when the table actually has a
clear role.  Pass-1 of xl_ontology re-classifies DAG-attached tables
deterministically and writes its verdicts to ``ontology_table_tags.json``
(``ontology_role`` ∈ ``input`` / ``calculation`` / ``result`` /
``overall_evidence`` / ``unclassified`` / ``out_of_scope_for_pass_1``).

Until this fix, Pass-2 tools (``read_table_full_content``, ``list_tables_on_sheet``)
read ``entry.node_type`` straight from V6 — so even after Pass-1 declared
a table ``input/population``, the Pass-2 LLM saw ``node_type: "mixed"``
in the tool result and was confused.

This module mutates the in-memory ``FinalMapIndex.entries`` after Pass-1
completes (and BEFORE Pass-2 starts).  Rules:

  1. Untagged tables → leave V6 ``node_type`` alone.  They were not in
     the DAG, or Pass-1 chose not to classify them.

  2. ``ontology_role == "out_of_scope_for_pass_1"`` → leave V6 alone.
     Same intent as untagged.

  3. ``ontology_role == "unclassified"`` → leave V6 alone.  Pass-1
     looked but couldn't decide; the V6 default is the safer fallback.

  4. ``ontology_role == "overall_evidence"`` → leave V6 alone.  These
     are cover-sheet metadata tables; their V6 ``metadata`` /
     ``data_table`` tag is more accurate than coercing them to
     ``input``.

  5. ``ontology_role`` ∈ ``{input, calculation, result}`` → overwrite
     ``node_type`` with the family form (``input`` / ``calc`` /
     ``result``).  ``calculation`` maps to ``calc`` to match V6's
     vocabulary so downstream tools keep working uniformly.

  6. **Never** fold the subtype (``population``, ``drug_cost``,
     ``incremental_budget_impact``, …) into ``node_type``.  The role
     family is the only thing Pass-2 / DAG / orchestrator consumers
     downstream care about.  Subtypes live on the tag record itself
     (``ontology_table_tags.json``) for callers who need them.

The mutation is in-memory only.  The on-disk
``final_table_map_repaired.json`` is preserved as the upstream artifact;
re-running Pass-2 with a different ontology pack will produce a
different fold-back from the same on-disk file.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from xl_ontology.schemas import TableTagRecord, TableTagsFile
from xl_ontology.upstream.final_map import FinalMapIndex


logger = logging.getLogger(__name__)


# Maps Pass-1 ``ontology_role`` → V6 ``node_type`` family.  Values that
# don't appear here keep the V6 tag (rule 2/3/4 above).
_ONTOLOGY_ROLE_TO_NODE_TYPE: Dict[str, str] = {
    "input": "input",
    "calculation": "calc",
    "result": "result",
}


def apply_pass1_node_types_to_final_map(
    final_map: FinalMapIndex,
    tags: TableTagsFile,
) -> Dict[str, Any]:
    """Mutate ``final_map`` in place, replacing each entry's
    ``node_type`` with Pass-1's verdict when one exists and is
    foldable.  Returns a small report dict suitable for trace + audit.

    Args:
      final_map: in-memory final_map index, will be mutated.
      tags:      Pass-1 ``TableTagsFile`` (the assembled in-memory copy
                 the runner already builds — no JSON re-read needed).

    Returns:
      ``{
          "n_total_entries": int,
          "n_overwritten": int,
          "n_unchanged_no_tag": int,
          "n_unchanged_non_foldable_role": int,
          "n_unchanged_already_match": int,
          "overwrites": List[{table_id, before, after, ontology_role}],
        }``
    """
    by_id: Dict[str, TableTagRecord] = {t.table_id: t for t in tags.tables}

    n_overwritten = 0
    n_unchanged_no_tag = 0
    n_unchanged_non_foldable = 0
    n_unchanged_already_match = 0
    overwrites: list = []

    for entry in final_map.all_entries():
        tag = by_id.get(entry.table_id)
        if tag is None:
            n_unchanged_no_tag += 1
            continue

        new_node_type = _ONTOLOGY_ROLE_TO_NODE_TYPE.get(tag.ontology_role)
        if new_node_type is None:
            # ontology_role ∈ {overall_evidence, unclassified,
            # out_of_scope_for_pass_1} — V6 tag is preserved.
            n_unchanged_non_foldable += 1
            continue

        before = entry.node_type
        if before == new_node_type:
            n_unchanged_already_match += 1
            continue

        entry.node_type = new_node_type
        n_overwritten += 1
        overwrites.append({
            "table_id": entry.table_id,
            "before": before,
            "after": new_node_type,
            "ontology_role": tag.ontology_role,
            "ontology_subtype": tag.ontology_subtype,
        })

    report: Dict[str, Any] = {
        "n_total_entries": len(final_map),
        "n_overwritten": n_overwritten,
        "n_unchanged_no_tag": n_unchanged_no_tag,
        "n_unchanged_non_foldable_role": n_unchanged_non_foldable,
        "n_unchanged_already_match": n_unchanged_already_match,
        "overwrites": overwrites,
    }
    logger.info(
        "B.1 fold-back: %d entries; overwrote=%d, no_tag=%d, "
        "non_foldable=%d, already_match=%d",
        report["n_total_entries"], n_overwritten, n_unchanged_no_tag,
        n_unchanged_non_foldable, n_unchanged_already_match,
    )
    return report


__all__ = ["apply_pass1_node_types_to_final_map"]
