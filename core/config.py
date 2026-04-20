"""
core/config.py — Two-file configuration loader.

Loads config/stages.yaml and config/general.yaml, merges them,
and exposes a PipelineConfig with sub-configs per stage plus general settings.

YAML is the single source of truth for stage parameters. ``general.yaml`` must
list every general key explicitly (no silent fallbacks in the loader).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .env_bootstrap import load_project_dotenv


# ---------------------------------------------------------------------------
# Typed sub-configs (populated only from general.yaml — required keys)
# ---------------------------------------------------------------------------

@dataclass
class GeneralConfig:
    logging_level: str
    log_filename: str
    output_base: str
    default_input_dir: str
    default_output_base: str


@dataclass
class PipelineConfig:
    """Top-level config holding all stage sections + general."""
    general: GeneralConfig
    xl_init: Dict[str, Any]
    xl_parse: Dict[str, Any]
    xl_table: Dict[str, Any]
    xl_image: Dict[str, Any]
    xl_sheetagent: Dict[str, Any]
    xl_sheetagent_v5: Dict[str, Any]
    xl_sheetagent_v6: Dict[str, Any]


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_STAGES = _PROJECT_ROOT / "config" / "stages.yaml"
_DEFAULT_GENERAL = _PROJECT_ROOT / "config" / "general.yaml"


def _load_yaml(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _general_config_from_raw(general_raw: Dict[str, Any]) -> GeneralConfig:
    """Require logging.run.orchestrator sections with fixed keys."""
    if "logging" not in general_raw:
        raise ValueError("general.yaml: missing top-level key 'logging'")
    if "run" not in general_raw:
        raise ValueError("general.yaml: missing top-level key 'run'")
    if "orchestrator" not in general_raw:
        raise ValueError("general.yaml: missing top-level key 'orchestrator'")
    log = general_raw["logging"]
    run = general_raw["run"]
    orch = general_raw["orchestrator"]
    checks = [
        ("logging", "level", log),
        ("logging", "log_filename", log),
        ("run", "output_base", run),
        ("orchestrator", "default_input_dir", orch),
        ("orchestrator", "default_output_base", orch),
    ]
    for section_name, key, section in checks:
        if key not in section:
            raise ValueError(
                f"general.yaml: missing {section_name!r} key {key!r}"
            )
    return GeneralConfig(
        logging_level=log["level"],
        log_filename=log["log_filename"],
        output_base=run["output_base"],
        default_input_dir=orch["default_input_dir"],
        default_output_base=orch["default_output_base"],
    )


def load_config(
    stages_path: Optional[Path] = None,
    general_path: Optional[Path] = None,
) -> PipelineConfig:
    """Load both YAML files into a PipelineConfig."""
    load_project_dotenv()
    stages = _load_yaml(stages_path or _DEFAULT_STAGES)
    general_raw = _load_yaml(general_path or _DEFAULT_GENERAL)

    general = _general_config_from_raw(general_raw)

    for key in ("xl_init", "xl_parse", "xl_table", "xl_image", "xl_sheetagent", "xl_sheetagent_v5", "xl_sheetagent_v6"):
        if key not in stages or not isinstance(stages[key], dict):
            raise ValueError(f"stages.yaml: missing or invalid top-level section {key!r}")

    return PipelineConfig(
        general=general,
        xl_init=stages["xl_init"],
        xl_parse=stages["xl_parse"],
        xl_table=stages["xl_table"],
        xl_image=stages["xl_image"],
        xl_sheetagent=stages["xl_sheetagent"],
        xl_sheetagent_v5=stages["xl_sheetagent_v5"],
        xl_sheetagent_v6=stages["xl_sheetagent_v6"],
    )
