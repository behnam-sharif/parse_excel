"""
Build stage dataclasses from config/stages.yaml (via PipelineConfig).

All keys for each stage section must be present in YAML — no silent Python fallbacks.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any, Dict, Mapping, Optional

from core.config import PipelineConfig


def _require_exact_keys(
    model_cls: type,
    data: Mapping[str, Any],
    label: str,
    *,
    exclude: frozenset[str] = frozenset(),
) -> Dict[str, Any]:
    """Require every field on ``model_cls`` to either be present in
    ``data`` or to have a default. Reject any unknown keys.

    Returns ``{name: data[name]}`` for the fields actually present so
    the dataclass constructor uses defaults for the rest.
    """
    from dataclasses import MISSING

    all_fields = [f for f in fields(model_cls) if f.name not in exclude]
    required = {
        f.name
        for f in all_fields
        if f.default is MISSING and f.default_factory is MISSING  # type: ignore[misc]
    }
    optional = {f.name for f in all_fields} - required

    present = set(data.keys())
    missing = required - present
    if missing:
        raise ValueError(f"{label}: missing keys: {sorted(missing)}")
    allowed = required | optional
    extra = present - allowed
    if extra:
        raise ValueError(f"{label}: unknown keys: {sorted(extra)}")
    return {name: data[name] for name in present}


def merge_stage_dict(base: Dict[str, Any], overrides: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Deep-merge overrides into base (dict values merge recursively)."""
    if not overrides:
        return dict(base)
    out = dict(base)
    for k, v in overrides.items():
        if (
            k in out
            and isinstance(out[k], dict)
            and isinstance(v, dict)
        ):
            out[k] = merge_stage_dict(out[k], v)
        else:
            out[k] = v
    return out


def xl_init_config_from_mapping(d: Mapping[str, Any]):
    from xl_init.config import XlInitConfig

    data = _require_exact_keys(XlInitConfig, d, "stages.yaml xl_init")
    return XlInitConfig(**data)


def parse_config_from_mapping(d: Mapping[str, Any]):
    from xl_parse.parse_tools.models import Config

    data = _require_exact_keys(Config, d, "stages.yaml xl_parse")
    return Config(**data)


def xl_table_config_from_mapping(d: Mapping[str, Any]):
    from core.visual_types import VisualSplitConfig
    from xl_table.config import XlTableConfig

    raw = dict(d)
    visual_raw = raw.pop("visual", None)
    if not isinstance(visual_raw, dict):
        raise ValueError("stages.yaml xl_table must contain a mapping 'visual:'")
    vdata = _require_exact_keys(
        VisualSplitConfig, visual_raw, "stages.yaml xl_table.visual"
    )
    visual = VisualSplitConfig(**vdata)

    data = _require_exact_keys(
        XlTableConfig, raw, "stages.yaml xl_table", exclude=frozenset({"visual"})
    )
    return XlTableConfig(visual=visual, **data)


def xl_init_config_from_pipeline(
    pc: PipelineConfig,
    overrides: Optional[Dict[str, Any]] = None,
):
    return xl_init_config_from_mapping(merge_stage_dict(pc.xl_init, overrides))


def parse_config_from_pipeline(
    pc: PipelineConfig,
    overrides: Optional[Dict[str, Any]] = None,
):
    return parse_config_from_mapping(merge_stage_dict(pc.xl_parse, overrides))


def xl_table_config_from_pipeline(
    pc: PipelineConfig,
    overrides: Optional[Dict[str, Any]] = None,
):
    return xl_table_config_from_mapping(merge_stage_dict(pc.xl_table, overrides))


def xl_image_config_from_mapping(d: Mapping[str, Any]):
    from xl_image.config import XlImageConfig

    data = _require_exact_keys(XlImageConfig, d, "stages.yaml xl_image")
    return XlImageConfig(**data)


def xl_image_config_from_pipeline(
    pc: PipelineConfig,
    overrides: Optional[Dict[str, Any]] = None,
):
    return xl_image_config_from_mapping(merge_stage_dict(pc.xl_image, overrides))


def sheetagent_config_from_mapping(d: Mapping[str, Any]):
    from xl_sheetagent.xl_sheetagent.config import XlSheetAgentConfig

    data = _require_exact_keys(XlSheetAgentConfig, d, "stages.yaml xl_sheetagent")
    return XlSheetAgentConfig(**data)


def sheetagent_config_from_pipeline(
    pc: PipelineConfig,
    overrides: Optional[Dict[str, Any]] = None,
):
    return sheetagent_config_from_mapping(merge_stage_dict(pc.xl_sheetagent, overrides))


def sheetagent_v6_config_from_mapping(d: Mapping[str, Any]):
    from xl_sheetagent.config import XlSheetAgentV6Config

    data = _require_exact_keys(XlSheetAgentV6Config, d, "stages.yaml xl_sheetagent_v6")
    return XlSheetAgentV6Config(**data)


def sheetagent_v6_config_from_pipeline(
    pc: PipelineConfig,
    overrides: Optional[Dict[str, Any]] = None,
):
    return sheetagent_v6_config_from_mapping(merge_stage_dict(pc.xl_sheetagent_v6, overrides))


def dag_config_from_mapping(d: Mapping[str, Any]):
    from xl_dag.config import XlDagConfig

    data = _require_exact_keys(XlDagConfig, d, "stages.yaml xl_dag")
    return XlDagConfig(**data)


def dag_config_from_pipeline(
    pc: PipelineConfig,
    overrides: Optional[Dict[str, Any]] = None,
):
    return dag_config_from_mapping(merge_stage_dict(pc.xl_dag, overrides))


def qc_repair_config_from_mapping(d: Mapping[str, Any]):
    from xl_qc_repair.config import XlQcRepairConfig

    data = _require_exact_keys(XlQcRepairConfig, d, "stages.yaml xl_qc_repair")
    return XlQcRepairConfig(**data)


def qc_repair_config_from_pipeline(
    pc: PipelineConfig,
    overrides: Optional[Dict[str, Any]] = None,
):
    raw = pc.xl_qc_repair if pc.xl_qc_repair else {}
    return qc_repair_config_from_mapping(merge_stage_dict(raw, overrides))


def ontology_config_from_mapping(d: Mapping[str, Any]):
    from xl_ontology.config import XlOntologyConfig

    data = _require_exact_keys(XlOntologyConfig, d, "stages.yaml xl_ontology")
    return XlOntologyConfig(**data)


def ontology_config_from_pipeline(
    pc: PipelineConfig,
    overrides: Optional[Dict[str, Any]] = None,
):
    return ontology_config_from_mapping(merge_stage_dict(pc.xl_ontology, overrides))
