"""
Data-driven rule engine for split-candidate detection.

Loads rule definitions from a YAML file and evaluates them generically
against per-band signal profiles.  No rule logic is hardcoded here --
all behaviour comes from the YAML.

Public API:
    load_ruleset(path)  -> RuleSet
    evaluate_axis(ruleset, profile, axis, start_index, semantic_exclusions, cfg)
        -> List[dict]   (scored + filtered candidates)
    compute_thresholds(ruleset, profile, axis, cfg)
        -> Dict[str, float]
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import numpy as np
import yaml

from ..config import XlImageConfig

logger = logging.getLogger(__name__)

_RULES_DIR = Path(__file__).resolve().parent.parent  # xl_image/

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ThresholdDef:
    name: str
    signal: str
    floor: float
    adaptive: str            # "percentile" | "percentile_max" | "percentile_min" | "std_mult" | "fixed"
    percentile: float = 0.0
    multiplier: float = 1.0
    source_signal: str = ""  # for std_mult: compute std of this signal
    ceiling: float = 1.0     # for percentile_min: hard maximum (threshold = min(ceiling, pN))


@dataclass
class Condition:
    type: str                # "threshold" | "fixed" | "neighbor" | "prev_below" | "any_of"
    signal: str = ""
    threshold: str = ""      # name in thresholds dict (for type=threshold/prev_below)
    value: float = 0.0       # literal (for type=fixed)
    margin: float = 0.0      # for type=neighbor
    sub_conditions: List["Condition"] = field(default_factory=list)
    optional: bool = False   # if True, condition is a bonus -- does NOT gate the rule


@dataclass
class ScalarGate:
    """Hard prerequisite on a bbox-level scalar (not a per-band signal)."""
    field: str               # key in scalar_context, e.g. "n_components"
    op: str                  # "gte" | "lte" | "eq"
    value: float


@dataclass
class RuleDef:
    name: str
    axis: str                # "both" | "row" | "col"
    conditions: List[Condition]
    weight: float
    score_expr: str
    min_index: int = 0
    reason_tag_row: str = ""
    reason_tag_col: str = ""
    scalar_gates: List[ScalarGate] = field(default_factory=list)
    near_edge_exempt: bool = False  # if True, near-edge score gate is skipped for this rule
    # Optional-condition bonus: added to weighted score when any optional condition fires
    optional_bonus_weight: float = 0.0
    # Extreme bypass: if floor_signal >= floor_value AND ceiling_signal <= ceiling_value,
    # the rule fires unconditionally regardless of required conditions.
    extreme_bypass_floor_signal: str = ""
    extreme_bypass_floor_value: float = 0.0
    extreme_bypass_ceiling_signal: str = ""
    extreme_bypass_ceiling_value: float = 1.0


@dataclass
class PostScoring:
    near_edge_score_gate: float = 0.85
    top_k: int = 5
    min_gap: int = 1
    min_score_gate: float = 0.20


@dataclass
class RuleSet:
    version: str
    thresholds: Dict[str, ThresholdDef]
    rules: List[RuleDef]
    post_scoring: PostScoring
    source_path: str = ""


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------

def _parse_condition(raw: dict) -> Condition:
    subs = []
    if raw.get("sub_conditions"):
        subs = [_parse_condition(s) for s in raw["sub_conditions"]]
    return Condition(
        type=raw["type"],
        signal=raw.get("signal", ""),
        threshold=raw.get("threshold", ""),
        value=float(raw.get("value", 0.0)),
        margin=float(raw.get("margin", 0.0)),
        sub_conditions=subs,
        optional=bool(raw.get("optional", False)),
    )


def load_ruleset(path: Optional[str] = None) -> RuleSet:
    """Load a YAML rule file.  Defaults to xl_image/split_rules.yaml."""
    p = Path(path) if path else _RULES_DIR / "split_rules.yaml"
    with open(p, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    thresholds: Dict[str, ThresholdDef] = {}
    for name, td in (raw.get("thresholds") or {}).items():
        thresholds[name] = ThresholdDef(
            name=name,
            signal=td["signal"],
            floor=float(td.get("floor", 0.0)),
            adaptive=td.get("adaptive", "fixed"),
            percentile=float(td.get("percentile", 0.0)),
            multiplier=float(td.get("multiplier", 1.0)),
            source_signal=td.get("source_signal", ""),
            ceiling=float(td.get("ceiling", 1.0)),
        )

    ps_raw = raw.get("post_scoring", {})
    post_scoring = PostScoring(
        near_edge_score_gate=float(ps_raw.get("near_edge_score_gate", 0.85)),
        top_k=int(ps_raw.get("top_k", 5)),
        min_gap=int(ps_raw.get("min_gap", 1)),
        min_score_gate=float(ps_raw.get("min_score_gate", 0.20)),
    )

    rules: List[RuleDef] = []
    for rd in raw.get("rules", []):
        gates: List[ScalarGate] = []
        for g in rd.get("scalar_gates", []):
            gates.append(ScalarGate(
                field=g["field"],
                op=g.get("op", "gte"),
                value=float(g["value"]),
            ))
        rules.append(RuleDef(
            name=rd["name"],
            axis=rd.get("axis", "both"),
            conditions=[_parse_condition(c) for c in rd.get("conditions", [])],
            weight=float(rd["weight"]),
            score_expr=rd.get("score_expr", "1.0"),
            min_index=int(rd.get("min_index", 0)),
            reason_tag_row=rd.get("reason_tag_row", ""),
            reason_tag_col=rd.get("reason_tag_col", ""),
            scalar_gates=gates,
            near_edge_exempt=bool(rd.get("near_edge_exempt", False)),
            optional_bonus_weight=float(rd.get("optional_bonus_weight", 0.0)),
            extreme_bypass_floor_signal=rd.get("extreme_bypass_floor_signal", ""),
            extreme_bypass_floor_value=float(rd.get("extreme_bypass_floor_value", 0.0)),
            extreme_bypass_ceiling_signal=rd.get("extreme_bypass_ceiling_signal", ""),
            extreme_bypass_ceiling_value=float(rd.get("extreme_bypass_ceiling_value", 1.0)),
        ))

    return RuleSet(
        version=raw.get("version", "unknown"),
        thresholds=thresholds,
        rules=rules,
        post_scoring=post_scoring,
        source_path=str(p),
    )


# ---------------------------------------------------------------------------
# Signal mapping helpers
# ---------------------------------------------------------------------------

_ROW_LINE_KEY = "hline_strength"
_COL_LINE_KEY = "vline_strength"

_AXIS_ALIASES: Dict[str, Dict[str, str]] = {
    "line_strength":  {"row": "hline_strength",  "col": "vline_strength"},
    "line_coverage":  {"row": "hline_coverage",  "col": "vline_coverage"},
}


def _resolve_signal_name(signal: str, axis: str) -> str:
    """Map generic signal names to axis-specific keys."""
    alias = _AXIS_ALIASES.get(signal)
    if alias:
        return alias.get(axis, signal)
    return signal


def _build_signal_arrays(
    profile: List[Dict[str, float]],
    axis: str,
) -> Dict[str, np.ndarray]:
    """Extract all signal arrays from the profile + compute deltas."""
    line_key = _ROW_LINE_KEY if axis == "row" else _COL_LINE_KEY
    cov_key  = "hline_coverage" if axis == "row" else "vline_coverage"

    arrs: Dict[str, np.ndarray] = {
        "line_strength":      np.array([p.get(line_key, 0.0) for p in profile], dtype=float),
        line_key:             np.array([p.get(line_key, 0.0) for p in profile], dtype=float),
        "blank_ratio":        np.array([p.get("blank_ratio", 0.0) for p in profile], dtype=float),
        "brightness_mean":    np.array([p.get("brightness_mean", 1.0) for p in profile], dtype=float),
        "saturation_mean":    np.array([p.get("saturation_mean", 0.0) for p in profile], dtype=float),
        # v1.2 signals
        "component_density":  np.array([p.get("component_density", 0.0) for p in profile], dtype=float),
        "line_coverage":      np.array([p.get(cov_key, 0.0) for p in profile], dtype=float),
        cov_key:              np.array([p.get(cov_key, 0.0) for p in profile], dtype=float),
        "border_score":       np.array([p.get("border_score", 0.0) for p in profile], dtype=float),
        "symmetry_break":     np.array([p.get("symmetry_break", 0.0) for p in profile], dtype=float),
        # v1.4 signals
        "valley_score":       np.array([p.get("valley_score", 0.0) for p in profile], dtype=float),
    }
    arrs["delta_brightness"]    = _delta_array(arrs["brightness_mean"])
    arrs["delta_line_strength"] = _delta_array(arrs["line_strength"])
    arrs["delta_border_score"]  = _delta_array(arrs["border_score"])
    return arrs


# ---------------------------------------------------------------------------
# Threshold computation
# ---------------------------------------------------------------------------

def compute_thresholds(
    ruleset: RuleSet,
    signal_arrays: Dict[str, np.ndarray],
    axis: str,
    cfg: Optional[XlImageConfig] = None,
) -> Dict[str, float]:
    """Compute adaptive threshold values from signal arrays."""
    result: Dict[str, float] = {}
    for name, td in ruleset.thresholds.items():
        sig_name = _resolve_signal_name(td.signal, axis)

        floor = _floor_from_config(td, cfg) if cfg else td.floor
        arr = signal_arrays.get(sig_name)
        if arr is None or len(arr) == 0:
            result[name] = floor
            continue

        if td.adaptive == "percentile":
            result[name] = max(floor, float(np.percentile(arr, td.percentile)))
        elif td.adaptive == "percentile_max":
            result[name] = max(floor, float(np.percentile(arr, td.percentile)))
        elif td.adaptive == "percentile_min":
            ceiling = _ceiling_from_config(td, cfg) if cfg else td.ceiling
            result[name] = min(ceiling, float(np.percentile(arr, td.percentile)))
        elif td.adaptive == "std_mult":
            src = td.source_signal
            src_name = _resolve_signal_name(src, axis) if src else sig_name
            src_arr = signal_arrays.get(src_name, arr)
            result[name] = max(floor, float(np.std(src_arr) * td.multiplier)) if len(src_arr) > 1 else floor
        else:
            result[name] = floor

    return result


def _floor_from_config(td: ThresholdDef, cfg: Optional[XlImageConfig]) -> float:
    """Try to pull the floor from XlImageConfig fields; fall back to YAML floor."""
    if cfg is None:
        return td.floor
    mapping = {
        "T_blank":        "split_blank_floor",
        "T_blank_soft":   "split_blank_soft_floor",
        "T_bright_soft":  "split_bright_soft_floor",
        "T_line":         "split_line_floor",
        "T_bright":       "split_brightness_high",
        "T_db":           "split_brightness_delta_floor",
        "T_dl":           "split_line_delta_floor",
        "T_sat":          "split_saturation_floor",
        "T_comp_low":     "split_component_density_floor",
        "T_line_cov":     "split_line_coverage_floor",
        "T_sym_break":    "split_symmetry_break_floor",
        "T_delta_border": "split_delta_border_floor",
    }
    attr = mapping.get(td.name)
    if attr and hasattr(cfg, attr):
        return float(getattr(cfg, attr))
    return td.floor


def _ceiling_from_config(td: ThresholdDef, cfg: Optional[XlImageConfig]) -> float:
    """Try to pull the ceiling from XlImageConfig fields; fall back to YAML ceiling."""
    if cfg is None:
        return td.ceiling
    mapping = {
        "T_density_soft": "split_density_soft_ceiling",
    }
    attr = mapping.get(td.name)
    if attr and hasattr(cfg, attr):
        return float(getattr(cfg, attr))
    return td.ceiling


# ---------------------------------------------------------------------------
# Normalisation denominators  (max of array, guard div-by-zero)
# ---------------------------------------------------------------------------

def _norms(signal_arrays: Dict[str, np.ndarray]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for key in ("delta_brightness", "delta_line_strength", "saturation_mean",
                "blank_ratio", "brightness_mean", "line_strength",
                "hline_strength", "vline_strength",
                "component_density", "line_coverage",
                "hline_coverage", "vline_coverage",
                "border_score", "delta_border_score", "symmetry_break",
                "valley_score"):
        arr = signal_arrays.get(key)
        if arr is not None and len(arr) > 0:
            mx = float(np.max(arr))
            out[f"norm_{key}"] = mx if mx > 0 else 1.0
        else:
            out[f"norm_{key}"] = 1.0
    return out


# ---------------------------------------------------------------------------
# Condition evaluation
# ---------------------------------------------------------------------------

def _eval_condition(
    cond: Condition,
    i: int,
    n: int,
    signals_i: Dict[str, float],
    thresholds: Dict[str, float],
    signal_arrays: Dict[str, np.ndarray],
    axis: str,
) -> bool:
    sig_name = _resolve_signal_name(cond.signal, axis) if cond.signal else ""

    if cond.type == "threshold":
        val = signals_i.get(sig_name, 0.0)
        t_val = thresholds.get(cond.threshold, 0.0)
        return val > t_val

    if cond.type == "threshold_lte":
        val = signals_i.get(sig_name, 0.0)
        t_val = thresholds.get(cond.threshold, 1.0)
        return val <= t_val

    if cond.type == "fixed":
        val = signals_i.get(sig_name, 0.0)
        return val > cond.value

    if cond.type == "fixed_gte":
        val = signals_i.get(sig_name, 0.0)
        return val >= cond.value

    if cond.type == "fixed_lte":
        val = signals_i.get(sig_name, 0.0)
        return val <= cond.value

    if cond.type == "neighbor":
        val = signals_i.get(sig_name, 0.0)
        arr = signal_arrays.get(sig_name)
        if arr is None:
            return False
        neighbors = [float(arr[j]) for j in (i - 1, i + 1) if 0 <= j < n]
        if not neighbors:
            return True
        return val > max(neighbors) + cond.margin

    if cond.type == "prev_below":
        if i < 1:
            return False
        arr = signal_arrays.get(sig_name)
        if arr is None:
            return False
        prev_val = float(arr[i - 1])
        t_val = thresholds.get(cond.threshold, 0.0)
        return prev_val < t_val

    if cond.type == "any_of":
        return any(
            _eval_condition(sub, i, n, signals_i, thresholds, signal_arrays, axis)
            for sub in cond.sub_conditions
        )

    logger.warning("Unknown condition type: %s", cond.type)
    return False


# ---------------------------------------------------------------------------
# Score expression evaluation
# ---------------------------------------------------------------------------

def _eval_score_expr(
    expr: str,
    signals_i: Dict[str, float],
    norms: Dict[str, float],
) -> float:
    """Evaluate a score expression string safely."""
    ns: Dict[str, Any] = {}
    ns.update(signals_i)
    ns.update(norms)
    ns["max"] = max
    ns["min"] = min
    ns["abs"] = abs
    ns["math"] = math
    try:
        return float(eval(expr, {"__builtins__": {}}, ns))  # noqa: S307
    except Exception:
        return 1.0


# ---------------------------------------------------------------------------
# Scalar gate evaluation
# ---------------------------------------------------------------------------

def _check_scalar_gates(gates: List[ScalarGate], ctx: Dict[str, Any]) -> bool:
    """Return False if any scalar gate fails -- the rule is entirely skipped."""
    for g in gates:
        val = ctx.get(g.field)
        if val is None:
            return False
        fval = float(val)
        if g.op == "gte" and not (fval >= g.value):
            return False
        if g.op == "lte" and not (fval <= g.value):
            return False
        if g.op == "eq" and not (fval == g.value):
            return False
    return True


# ---------------------------------------------------------------------------
# Per-axis evaluation (public)
# ---------------------------------------------------------------------------

def evaluate_axis(
    ruleset: RuleSet,
    profile: List[Dict[str, float]],
    axis: str,
    start_index: int,
    semantic_exclusions: Set[int],
    cfg: Optional[XlImageConfig] = None,
    scalar_context: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Score all positions on one axis, apply top-K filtering. Returns output dicts."""
    from openpyxl.utils import get_column_letter

    n = len(profile)
    if n < 3:
        return []

    signal_arrays = _build_signal_arrays(profile, axis)
    thresholds = compute_thresholds(ruleset, signal_arrays, axis, cfg)
    norms = _norms(signal_arrays)
    sc = scalar_context or {}

    ps = ruleset.post_scoring
    if cfg:
        ps = PostScoring(
            near_edge_score_gate=cfg.split_near_edge_score_gate,
            top_k=cfg.split_top_k,
            min_gap=cfg.split_min_gap,
            min_score_gate=cfg.split_min_score_gate,
        )

    near_edge = {i for i in (0, 1, n - 2, n - 1) if 0 <= i < n}
    line_key = _ROW_LINE_KEY if axis == "row" else _COL_LINE_KEY

    # Pre-filter rules: check scalar gates once, track which rules were gate-killed
    gate_killed_rules: List[str] = []
    applicable_rules: List[RuleDef] = []
    for r in ruleset.rules:
        if r.axis not in ("both", axis):
            continue
        if not _check_scalar_gates(r.scalar_gates, sc):
            gate_killed_rules.append(r.name)
            continue
        applicable_rules.append(r)

    # Per-rule near_edge_exempt flag (from YAML)
    _NEAR_EDGE_EXEMPT = {r.name for r in applicable_rules if r.near_edge_exempt}

    scored: List[Dict[str, Any]] = []
    debug_trace: List[Dict[str, Any]] = []

    cov_key = "hline_coverage" if axis == "row" else "vline_coverage"

    for i in range(n):
        if i in semantic_exclusions:
            continue

        ls  = float(signal_arrays["line_strength"][i])
        br  = float(signal_arrays["blank_ratio"][i])
        bm  = float(signal_arrays["brightness_mean"][i])
        sm  = float(signal_arrays["saturation_mean"][i])
        db  = float(signal_arrays["delta_brightness"][i])
        dl  = float(signal_arrays["delta_line_strength"][i])
        cd  = float(signal_arrays["component_density"][i])
        lc  = float(signal_arrays["line_coverage"][i])
        bs  = float(signal_arrays["border_score"][i])
        dbs = float(signal_arrays["delta_border_score"][i])
        sb  = float(signal_arrays["symmetry_break"][i])
        vs  = float(signal_arrays["valley_score"][i])

        signals_i: Dict[str, float] = {
            "line_strength":      ls,
            line_key:             ls,
            "blank_ratio":        br,
            "brightness_mean":    bm,
            "saturation_mean":    sm,
            "delta_brightness":   db,
            "delta_line_strength": dl,
            "component_density":  cd,
            "line_coverage":      lc,
            cov_key:              lc,
            "border_score":       bs,
            "delta_border_score": dbs,
            "symmetry_break":     sb,
            "valley_score":       vs,
        }

        reasons: List[str] = []
        fired_rule_names: List[str] = []
        score_components: List[float] = []
        pos_trace: List[Dict[str, Any]] = []

        # Position-level debug flags (aggregated across all rules for this position)
        pos_extreme_blank_bypass = False
        pos_neighbor_bonus_applied = False

        for rule in applicable_rules:
            trace_entry: Dict[str, Any] = {"rule": rule.name, "triggered": False}

            if i < rule.min_index:
                trace_entry["gate_failed"] = f"min_index ({i} < {rule.min_index})"
                pos_trace.append(trace_entry)
                continue

            # Separate required vs optional conditions
            req_conds  = [c for c in rule.conditions if not c.optional]
            opt_conds  = [c for c in rule.conditions if c.optional]

            req_results = [
                _eval_condition(c, i, n, signals_i, thresholds, signal_arrays, axis)
                for c in req_conds
            ]
            all_req_pass = all(req_results)

            # Evaluate optional (bonus) conditions
            opt_results = [
                _eval_condition(c, i, n, signals_i, thresholds, signal_arrays, axis)
                for c in opt_conds
            ]
            n_opt_passed = sum(opt_results)
            neighbor_bonus_applied = n_opt_passed > 0

            # Extreme bypass: fire unconditionally if floor/ceiling thresholds are met
            extreme_blank_bypass_fired = False
            if not all_req_pass and rule.extreme_bypass_floor_signal:
                floor_val = signals_i.get(rule.extreme_bypass_floor_signal, 0.0)
                ceil_val  = signals_i.get(rule.extreme_bypass_ceiling_signal, 0.0) \
                            if rule.extreme_bypass_ceiling_signal else 0.0
                if (floor_val >= rule.extreme_bypass_floor_value
                        and ceil_val <= rule.extreme_bypass_ceiling_value):
                    all_req_pass = True
                    extreme_blank_bypass_fired = True

            if not all_req_pass:
                failed_idx = next(j for j, v in enumerate(req_results) if not v)
                fc = req_conds[failed_idx]
                trace_entry["gate_failed"] = (
                    f"condition[{failed_idx}] {fc.type}({fc.signal or fc.threshold})"
                )
                trace_entry["extreme_blank_bypass"] = False
                trace_entry["neighbor_bonus_applied"] = neighbor_bonus_applied
                pos_trace.append(trace_entry)
                continue

            tag = rule.name
            if axis == "row" and rule.reason_tag_row:
                tag = rule.reason_tag_row
            elif axis == "col" and rule.reason_tag_col:
                tag = rule.reason_tag_col

            factor = _eval_score_expr(rule.score_expr, signals_i, norms)
            # Optional bonus: small fixed bonus per optional condition that fired
            bonus = rule.optional_bonus_weight * n_opt_passed
            weighted = rule.weight * factor + bonus

            trace_entry["triggered"] = True
            trace_entry["raw_strength"] = round(factor, 4)
            trace_entry["weighted_score"] = round(weighted, 4)
            trace_entry["extreme_blank_bypass"] = extreme_blank_bypass_fired
            trace_entry["neighbor_bonus_applied"] = neighbor_bonus_applied
            pos_trace.append(trace_entry)

            # Propagate to position-level flags
            if extreme_blank_bypass_fired:
                pos_extreme_blank_bypass = True
            if neighbor_bonus_applied:
                pos_neighbor_bonus_applied = True

            reasons.append(tag)
            fired_rule_names.append(rule.name)
            score_components.append(weighted)

        if not reasons:
            # Extreme-signal bypass: create candidate from scratch
            if sb >= 0.90:
                reasons.append("symmetry_reset_extreme")
                fired_rule_names.append("symmetry_reset")
                score_components.append(0.05 * sb)

        # Mark extreme bypass: any individual signal is so strong that the
        # candidate should survive even below min_score_gate.
        extreme_bypass = (
            sb >= 0.90
            or dbs >= 0.50
        )

        if pos_trace:
            debug_trace.append({
                "rel_index": i,
                "abs_index": start_index + i,
                "in_near_edge": i in near_edge,
                "rules": pos_trace,
                "extreme_bypass": extreme_bypass,
                "extreme_blank_bypass": pos_extreme_blank_bypass,
                "neighbor_bonus_applied": pos_neighbor_bonus_applied,
            })

        # Valley audit: if this row has valley_score > 0 but produced no
        # candidate, log a debug trace so the gap is visible in output.
        if not reasons and vs > 0 and axis == "row":
            debug_trace.append({
                "rel_index": i,
                "abs_index": start_index + i,
                "in_near_edge": i in near_edge,
                "valley_score": round(vs, 4),
                "rules": pos_trace or [],
                "dropped_by": "valley_row_no_rules_fired",
            })

        if not reasons:
            continue

        final_score = min(sum(score_components), 1.0)

        out_signals: Dict[str, float] = {
            line_key:              round(ls, 4),
            "blank_ratio":         round(br, 4),
            "brightness_mean":     round(bm, 4),
            "saturation_mean":     round(sm, 4),
            "delta_brightness":    round(db, 4),
            "delta_line_strength": round(dl, 4),
            "component_density":   round(cd, 4),
            cov_key:               round(lc, 4),
            "border_score":        round(bs, 4),
            "delta_border_score":  round(dbs, 4),
            "symmetry_break":      round(sb, 4),
            "valley_score":        round(vs, 4),
        }
        if axis == "row":
            out_signals["hline_strength"] = round(ls, 4)
        else:
            out_signals["vline_strength"] = round(ls, 4)

        scored.append({
            "_rel_index": i,
            "_score": final_score,
            "_in_near_edge": i in near_edge,
            "_has_edge_exempt_rule": bool(set(fired_rule_names) & _NEAR_EDGE_EXEMPT),
            "_extreme_bypass": extreme_bypass,
            "reasons": reasons,
            "signals": out_signals,
        })

    # ------------------------------------------------------------------
    # Band collapsing: group consecutive scored positions into bands,
    # represent each band by its center row with the best score.
    # This prevents a single wide valley from consuming all top_k slots.
    # ------------------------------------------------------------------
    scored.sort(key=lambda x: x["_rel_index"])
    bands: List[List[Dict[str, Any]]] = []
    for entry in scored:
        if bands and entry["_rel_index"] - bands[-1][-1]["_rel_index"] <= ps.min_gap + 1:
            bands[-1].append(entry)
        else:
            bands.append([entry])

    collapsed: List[Dict[str, Any]] = []
    for band in bands:
        best = max(band, key=lambda x: x["_score"])
        rep = dict(best)
        rep["_band_size"] = len(band)
        if len(band) >= 3:
            center_idx = band[len(band) // 2]["_rel_index"]
            rep["_rel_index"] = center_idx
            rep["_in_near_edge"] = center_idx in near_edge
        collapsed.append(rep)

    # Top-K filtering + min-score gate on collapsed candidates
    collapsed.sort(key=lambda x: x["_score"], reverse=True)
    retained: List[Dict[str, Any]] = []
    for entry in collapsed:
        score = entry["_score"]
        idx = entry["_rel_index"]

        if not entry.get("_extreme_bypass") and score < ps.min_score_gate:
            continue

        if entry["_in_near_edge"] and score < ps.near_edge_score_gate:
            if not entry.get("_has_edge_exempt_rule"):
                entry_trace = {
                    "rel_index": idx, "abs_index": start_index + idx,
                    "score": round(score, 4), "reasons": entry["reasons"],
                    "dropped_by": "near_edge_gate",
                }
                debug_trace.append(entry_trace)
                continue

        if any(abs(idx - prev["_rel_index"]) <= ps.min_gap for prev in retained):
            continue
        retained.append(entry)
        if len(retained) >= ps.top_k:
            break

    retained.sort(key=lambda x: x["_rel_index"])
    output_key = "row" if axis == "row" else "col"
    result = []
    for entry in retained:
        rel = entry["_rel_index"]
        abs_index = start_index + rel
        excel_index = abs_index + 1
        out: Dict[str, Any] = {
            output_key: abs_index,
            "score": round(entry["_score"], 4),
            "reasons": entry["reasons"],
            "signals": entry["signals"],
        }
        if axis == "row":
            out["excel_row"] = excel_index
        else:
            out["excel_col"] = get_column_letter(excel_index)
        result.append(out)

    return result, debug_trace, gate_killed_rules


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _delta_array(arr: np.ndarray) -> np.ndarray:
    delta = np.zeros_like(arr)
    if len(arr) > 1:
        delta[1:] = np.abs(np.diff(arr))
    return delta
