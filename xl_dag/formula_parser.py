"""
xl_dag/formula_parser.py — Reference-extraction formula parser.

Not an evaluator. Walks the token stream produced by
``openpyxl.formula.tokenizer.Tokenizer`` and returns:
  * cells referenced (same sheet or cross-sheet)
  * ranges referenced (preserving raw string form)
  * named ranges referenced
  * unresolved-reason markers (``indirect``, ``offset``, ``structured_ref``,
    ``external_workbook``, ``array_spill``)

Also produces a shallow AST used for grouping formulas by shape within a
column. Two formulas with the same AST-shape (structure + function calls +
absolute/relative pattern, coords replaced with placeholders) share a
representative formula.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

try:
    from openpyxl.formula.tokenizer import Tokenizer
except Exception:  # pragma: no cover
    Tokenizer = None  # type: ignore

from .coordinates import (
    a1_to_rc,
    col_letter_to_index,
    is_full_column_or_row,
    parse_range,
    split_sheet_prefix,
)
from .schemas import ExtractedRefs, FormulaAst


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Unresolved-reason markers (match spec §7.4)
# ---------------------------------------------------------------------------

UNRESOLVED_INDIRECT = "indirect"
UNRESOLVED_OFFSET = "offset"
UNRESOLVED_STRUCTURED = "structured_ref"
UNRESOLVED_EXTERNAL = "external_workbook"
UNRESOLVED_ARRAY_SPILL = "array_spill"


_UNRESOLVED_FUNCTIONS = {
    "INDIRECT": UNRESOLVED_INDIRECT,
    "OFFSET": UNRESOLVED_OFFSET,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_refs(formula_text: str, current_sheet: str) -> ExtractedRefs:
    """Parse ``formula_text`` and return all resolved + unresolved refs.

    ``current_sheet`` is used when a ref has no ``!`` prefix.
    """
    result = ExtractedRefs()
    if Tokenizer is None:
        return result
    if not formula_text or not isinstance(formula_text, str):
        return result

    text = formula_text.strip()
    if not text.startswith("="):
        text = "=" + text

    try:
        tok = Tokenizer(text)
    except Exception as exc:
        logger.debug("Tokenizer failed on %r: %s", formula_text, exc)
        return result

    tokens = list(tok.items)

    # Detect unresolved function usage and structured refs / external workbook
    # / array spill upfront; they also annotate the ref lists.
    _scan_unresolved(tokens, result)

    # Dedupe tracking sets.
    seen_cells: set = set()
    seen_ranges: set = set()
    seen_named: set = set()

    for t in tokens:
        t_type = t.type
        t_subtype = getattr(t, "subtype", "")
        raw = str(t.value)

        if t_type == "OPERAND" and t_subtype == "RANGE":
            _classify_range_token(
                raw, current_sheet, result, seen_cells, seen_ranges
            )
        elif t_type == "OPERAND" and t_subtype == "":
            # Undecorated operand. Unquoted names that are neither numbers
            # nor strings and that pass the tokenizer's RANGE check NOT
            # firing means they're likely named-range identifiers or
            # functions; the tokenizer tags FUNC separately, so this
            # branch is named-range candidates only.
            if _looks_like_named_range(raw):
                if raw not in seen_named:
                    seen_named.add(raw)
                    result.named_ranges.append(raw)

    # Build a shallow AST for grouping.
    result.ast = _ast_shape(tokens)

    return result


# ---------------------------------------------------------------------------
# Range token classification
# ---------------------------------------------------------------------------


def _classify_range_token(
    raw: str,
    current_sheet: str,
    result: ExtractedRefs,
    seen_cells: set,
    seen_ranges: set,
) -> None:
    """Route a RANGE token into the appropriate ExtractedRefs bucket."""
    # Structured ref: Table1[Col]
    if "[" in raw and "]" in raw and _looks_like_structured_ref(raw):
        result.unresolved.append((UNRESOLVED_STRUCTURED, raw))
        return

    # Array-spill marker: A1# (single cell followed by # sigil)
    if raw.endswith("#"):
        result.unresolved.append((UNRESOLVED_ARRAY_SPILL, raw))
        return

    sheet, local, external = split_sheet_prefix(raw)
    if external:
        result.unresolved.append((UNRESOLVED_EXTERNAL, raw))
        return

    resolved_sheet = sheet or current_sheet

    # Full-column or full-row range (A:A, 1:1) — record as a raw range but
    # don't expand to cells. Spec doesn't call these out explicitly; treat
    # as a range ref so edge builder still resolves via overlapping nodes.
    if is_full_column_or_row(local):
        if raw not in seen_ranges:
            seen_ranges.add(raw)
            result.ranges.append((resolved_sheet, -1, -1, -1, -1, raw))
        return

    parsed = parse_range(raw)
    if parsed is None:
        # Not a cell or range shape. openpyxl emits defined-name identifiers
        # with subtype=RANGE, so treat the unprefixed form as a named range.
        if sheet is None and _looks_like_named_range(local):
            if local not in result.named_ranges:
                result.named_ranges.append(local)
        return
    parsed_sheet, r1, c1, r2, c2, _ = parsed
    # Resolve final sheet: explicit prefix takes precedence over current.
    final_sheet = parsed_sheet or resolved_sheet

    if r1 == r2 and c1 == c2:
        key = (final_sheet, r1, c1)
        if key not in seen_cells:
            seen_cells.add(key)
            result.cells.append(key)
    else:
        key = (final_sheet, r1, c1, r2, c2)
        if key not in seen_ranges:
            seen_ranges.add(key)
            result.ranges.append((final_sheet, r1, c1, r2, c2, raw))


# ---------------------------------------------------------------------------
# Unresolved function / construct scanning
# ---------------------------------------------------------------------------


def _scan_unresolved(tokens: List, result: ExtractedRefs) -> None:
    """Emit UNRESOLVED markers for unsupported functions / constructs."""
    seen_reasons: set = set()
    for t in tokens:
        if getattr(t, "type", "") != "FUNC":
            continue
        name = str(t.value).rstrip("(").upper()
        reason = _UNRESOLVED_FUNCTIONS.get(name)
        if reason is not None:
            if reason not in seen_reasons:
                seen_reasons.add(reason)
                result.unresolved.append((reason, str(t.value)))


def _looks_like_structured_ref(text: str) -> bool:
    """Heuristic: ``Identifier[...]`` with letters before the bracket."""
    idx = text.find("[")
    if idx <= 0:
        return False
    lead = text[:idx]
    return any(ch.isalpha() for ch in lead) and lead.replace("_", "").isalnum()


def _looks_like_named_range(text: str) -> bool:
    """Accept tokens that look like named-range identifiers.

    Excel named-range rules: start with a letter or underscore; allow
    letters, digits, ``.``, and ``_``; cannot look like a cell address.
    """
    if not text or not isinstance(text, str):
        return False
    t = text.strip()
    if not t:
        return False
    first = t[0]
    if not (first.isalpha() or first == "_"):
        return False
    for ch in t:
        if not (ch.isalnum() or ch in "._"):
            return False
    # Reject cell-like shapes (A1, AA12, $A$1)
    if a1_to_rc(t):
        return False
    return True


# ---------------------------------------------------------------------------
# Shallow AST shape (for per-column grouping)
# ---------------------------------------------------------------------------


def _ast_shape(tokens: List) -> FormulaAst:
    """Flatten the token stream into a single ``function_call``-like node.

    Replaces every cell/range coordinate with placeholders so formulas that
    differ only in coordinates group together. Preserves function names,
    operator kinds, literal *types*, and structural tokens.
    """
    parts: List[str] = []
    for t in tokens:
        t_type = getattr(t, "type", "")
        t_subtype = getattr(t, "subtype", "")
        raw = str(t.value)

        if t_type == "OPERAND" and t_subtype == "RANGE":
            sheet, _local, _ = split_sheet_prefix(raw)
            has_colon = ":" in raw
            has_dollar = "$" in raw
            marker = "<range>" if has_colon else "<cell>"
            if sheet:
                marker = f"<xs:{marker}>"
            if has_dollar:
                marker = marker.replace("<", "<abs:")
            parts.append(marker)
        elif t_type == "OPERAND" and t_subtype == "NUMBER":
            parts.append("<num>")
        elif t_type == "OPERAND" and t_subtype == "TEXT":
            parts.append("<text>")
        elif t_type == "OPERAND" and t_subtype == "LOGICAL":
            parts.append("<bool>")
        elif t_type == "OPERAND" and t_subtype == "ERROR":
            parts.append("<err>")
        elif t_type == "FUNC":
            parts.append(f"FUNC({raw.rstrip('(').upper()}")
        elif t_type == "PAREN":
            parts.append(raw)
        elif t_type == "SEP":
            parts.append(",")
        elif t_type in ("OP-IN", "OP-PRE", "OP-POST"):
            parts.append(f"OP({raw})")
        elif t_type == "OPERAND" and t_subtype == "":
            # Named-range candidate; keep the name so different named ranges
            # don't collapse.
            parts.append(f"NAME({raw})")
        else:
            parts.append("<?>")

    return FormulaAst(node_type="shape", raw=" ".join(parts))


def ast_shape_key(ast: Optional[FormulaAst]) -> str:
    """Stable hash key used to group ASTs by shape."""
    if ast is None:
        return ""
    return ast.raw or ""
