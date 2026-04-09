"""
grammar_builder.py — Grammar discovery engine (V1C only).

Scans guide sheets and sample sheets for recurring formatting patterns,
then promotes observed patterns to confirmed via threshold logic.

GrammarSnapshot is frozen after this phase — READ-ONLY during enrichment.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

from ..schemas.grammar_snapshot import GrammarPattern, GrammarSnapshot
from ..tools.llm_client import call_llm_json, is_llm_available
from ..upstream_compat import UpstreamData

_log = logging.getLogger("xl_sheetagent.grammar_builder")

_GUIDE_SHEET_KEYWORDS = {"contents", "index", "toc", "summary", "guide", "navigation", "overview"}


def _detect_guide_sheets(
    approved_sheets: List[str],
    cfg,
) -> tuple[List[str], float, str]:
    """Identify guide sheets from approved_sheets by name matching.

    Returns (guide_sheets, confidence, method).
    """
    matches: List[str] = []
    for name in approved_sheets:
        lower = name.lower().strip()
        for kw in _GUIDE_SHEET_KEYWORDS:
            if kw in lower:
                matches.append(name)
                break

    if matches:
        # High confidence if exact keyword match
        conf = 0.80 if any(n.lower().strip() in _GUIDE_SHEET_KEYWORDS for n in matches) else 0.60
        return matches, conf, "guide_sheet_scan"

    return [], 0.0, "observed_only"


def _llm_guide_sheet_scan(
    approved_sheets: List[str],
    cfg,
) -> tuple[List[str], float]:
    """Use LLM to identify guide sheets when name matching is low-confidence."""
    system_prompt = (
        "You are a spreadsheet analyst. Given a list of sheet names from an "
        "Excel workbook, identify which sheet(s) serve as a table of contents, "
        "guide, index, or navigation sheet. Respond with JSON: "
        '{"guide_sheets": ["SheetName1"], "confidence": 0.8}'
    )
    user_prompt = f"Sheet names: {approved_sheets}"

    result = call_llm_json(system_prompt, user_prompt, cfg)
    guides = result.get("guide_sheets", [])
    conf = result.get("confidence", 0.50)
    if not isinstance(guides, list):
        guides = []
    # Filter to only approved sheets
    guides = [g for g in guides if g in approved_sheets]
    return guides, float(conf)


def _select_sample_sheets(
    upstream: UpstreamData,
    guide_sheets: List[str],
) -> List[str]:
    """Pick 2-4 sample sheets by table count and diversity."""
    tables_per_sheet: Dict[str, int] = defaultdict(int)
    for tv in upstream.table_objects:
        tables_per_sheet[tv.sheet_name] += 1

    # Exclude guide sheets
    candidates = [
        (name, count) for name, count in tables_per_sheet.items()
        if name not in guide_sheets
    ]
    # Sort by table count descending
    candidates.sort(key=lambda x: x[1], reverse=True)

    # Take 2-4 sheets
    return [name for name, _ in candidates[:4]]


def _scan_formatting_patterns(
    rejected_blocks: List[Dict[str, Any]],
    ws_by_sheet: Dict[str, Any],
    sample_sheets: List[str],
    guide_sheets: List[str],
) -> List[Dict[str, Any]]:
    """Scan formatting signatures from rejected_blocks and merged cells.

    Returns list of raw pattern observations.
    """
    observations: List[Dict[str, Any]] = []
    target_sheets = set(guide_sheets + sample_sheets)

    # Source 1: rejected_blocks
    for rb in rejected_blocks:
        sheet = rb.get("sheet_name", "")
        if sheet not in target_sheets:
            continue

        text = str(rb.get("text", rb.get("content", ""))).strip()
        if not text:
            continue

        r_top = rb.get("r_top", rb.get("r0", 0))
        r_bot = rb.get("r_bot", rb.get("r1", r_top))
        height = r_bot - r_top + 1
        if height > 2:
            continue

        # Extract formatting
        cf = rb.get("cell_features", {})
        sig: Dict[str, Any] = {}
        if cf:
            fill_groups = cf.get("fill_color_groups", [])
            if fill_groups:
                sig["fill_color"] = str(fill_groups[0]) if fill_groups else None
            sig["is_bold"] = cf.get("is_bold", False)
            sig["font_size"] = cf.get("font_size")
        else:
            sig["fill_color"] = rb.get("fill_color", rb.get("bg_color"))
            sig["is_bold"] = rb.get("is_bold", rb.get("bold", False))
            sig["font_size"] = rb.get("font_size")

        is_merged = rb.get("is_merged", rb.get("has_merge", False))

        observations.append({
            "sheet_name": sheet,
            "text": text[:100],
            "formatting_sig": sig,
            "is_merged": is_merged,
            "source": "rejected_block",
        })

    # Source 2: workbook merged cells scan
    for sheet_name in target_sheets:
        ws = ws_by_sheet.get(sheet_name)
        if ws is None:
            continue
        try:
            for mc in ws.merged_cells.ranges:
                if mc.max_row - mc.min_row > 1:
                    continue  # Skip tall merges
                span = mc.max_col - mc.min_col + 1
                if span < 3:
                    continue  # Skip small merges

                r = mc.min_row
                c = mc.min_col
                try:
                    cell = ws.cell(row=r, column=c)
                    val = cell.value
                    if val is None or not str(val).strip():
                        continue
                    font = cell.font
                    fill = cell.fill
                    sig = {
                        "is_bold": font.bold if font.bold else False,
                        "font_size": font.size if font.size else None,
                        "fill_color": None,
                    }
                    if fill and fill.fgColor and fill.fgColor.rgb:
                        rgb = str(fill.fgColor.rgb)
                        if rgb not in ("00000000", "FFFFFF"):
                            sig["fill_color"] = rgb

                    observations.append({
                        "sheet_name": sheet_name,
                        "text": str(val).strip()[:100],
                        "formatting_sig": sig,
                        "is_merged": True,
                        "source": "merged_cell_scan",
                    })
                except Exception:
                    continue
        except Exception:
            continue

    return observations


def _classify_pattern_type(obs: Dict[str, Any]) -> str:
    """Classify an observation as a pattern type."""
    sig = obs.get("formatting_sig", {})
    is_bold = sig.get("is_bold", False)
    has_fill = sig.get("fill_color") is not None and sig.get("fill_color") not in ("", "FFFFFF", "00000000")
    is_merged = obs.get("is_merged", False)

    if (has_fill or is_bold) and is_merged:
        return "section_header"
    if is_bold and not is_merged:
        return "table_title"
    if has_fill:
        return "section_header"
    return "repeated_label"


def _group_and_promote(
    observations: List[Dict[str, Any]],
    cfg,
) -> List[GrammarPattern]:
    """Group observations by formatting signature and promote to patterns."""
    # Group by (fill_color, is_bold, font_size, pattern_type)
    groups: Dict[str, Dict[str, Any]] = {}

    for obs in observations:
        sig = obs.get("formatting_sig", {})
        ptype = _classify_pattern_type(obs)
        key = f"{sig.get('fill_color')}|{sig.get('is_bold')}|{sig.get('font_size')}|{ptype}"

        if key not in groups:
            groups[key] = {
                "pattern_type": ptype,
                "count": 0,
                "sheets": set(),
                "evidence": dict(sig),
                "texts": [],
            }
        groups[key]["count"] += 1
        groups[key]["sheets"].add(obs["sheet_name"])
        if len(groups[key]["texts"]) < 5:
            groups[key]["texts"].append(obs.get("text", ""))

    patterns: List[GrammarPattern] = []
    for i, (key, grp) in enumerate(groups.items()):
        count = grp["count"]
        sheets = list(grp["sheets"])

        # Promotion logic
        is_confirmed = (
            count >= cfg.grammar_promotion_count
            and len(sheets) >= cfg.grammar_promotion_sheets
        )

        confidence = min(0.90, 0.30 + count * 0.10 + len(sheets) * 0.10)
        if not is_confirmed:
            confidence = min(confidence, 0.50)

        evidence = dict(grp["evidence"])
        evidence["sample_texts"] = grp["texts"]

        patterns.append(GrammarPattern(
            pattern_name=f"{grp['pattern_type']}_{i}",
            pattern_type=grp["pattern_type"],
            observed_count=count,
            observed_sheets=sheets,
            confidence=confidence,
            status="confirmed" if is_confirmed else "observed",
            evidence=evidence,
        ))

    return patterns


def build_grammar(
    upstream: UpstreamData,
    ws_by_sheet: Dict[str, Any],
    cfg,
) -> GrammarSnapshot:
    """Run Phase 0 grammar discovery.

    Returns a frozen GrammarSnapshot for use in Phase 1-2 enrichment.
    """
    approved_sheets = list(upstream.approved_sheets) if upstream.approved_sheets else []
    if not approved_sheets:
        # Fallback: use sheet names from table objects
        approved_sheets = sorted(set(tv.sheet_name for tv in upstream.table_objects))

    # Step 1: Detect guide sheets
    guide_sheets, guide_conf, method = _detect_guide_sheets(approved_sheets, cfg)

    # Step 2: LLM fallback if low confidence
    if guide_conf < 0.70 and guide_sheets:
        if is_llm_available(cfg):
            llm_guides, llm_conf = _llm_guide_sheet_scan(approved_sheets, cfg)
            if llm_guides:
                guide_sheets = llm_guides
                guide_conf = llm_conf
                method = "llm_sheet_name_scan"
    elif not guide_sheets and is_llm_available(cfg):
        llm_guides, llm_conf = _llm_guide_sheet_scan(approved_sheets, cfg)
        if llm_guides:
            guide_sheets = llm_guides
            guide_conf = llm_conf
            method = "llm_sheet_name_scan"

    if not guide_sheets:
        method = "observed_only"
        guide_conf = 0.0

    # Step 3: Select sample sheets
    sample_sheets = _select_sample_sheets(upstream, guide_sheets)

    # Step 4: Scan formatting patterns
    observations = _scan_formatting_patterns(
        upstream.rejected_blocks, ws_by_sheet, sample_sheets, guide_sheets,
    )

    _log.info(
        "Grammar discovery: %d guide sheets, %d sample sheets, %d observations",
        len(guide_sheets), len(sample_sheets), len(observations),
    )

    # Step 5: Group and promote
    patterns = _group_and_promote(observations, cfg)

    # Cap confidence on noisy guide sheets
    if guide_sheets and guide_conf < 0.70:
        for p in patterns:
            if p.status == "confirmed":
                p.confidence = min(p.confidence, 0.60)

    confirmed_count = sum(1 for p in patterns if p.status == "confirmed")
    _log.info(
        "Grammar: method=%s, guide_conf=%.2f, %d patterns (%d confirmed)",
        method, guide_conf, len(patterns), confirmed_count,
    )

    return GrammarSnapshot(
        guide_sheets=guide_sheets,
        sample_sheets=sample_sheets,
        patterns=patterns,
        discovery_method=method,
        guide_sheet_confidence=guide_conf,
        frozen=True,
    )
