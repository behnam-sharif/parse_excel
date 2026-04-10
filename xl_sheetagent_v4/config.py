"""
Configuration for xl_sheetagent_v4.
"""

from dataclasses import dataclass


@dataclass
class V4Config:
    """Configuration for V4 pipeline."""

    # LLM settings
    llm_model: str = "gpt-4o"  # or "claude-opus-4"
    llm_api_key: str = None  # To be provided by user
    llm_max_tokens: int = 2000
    llm_temperature: float = 0.0

    # Analysis settings
    max_recursion_depth: int = 3
    min_child_rows: int = 5
    child_confidence_threshold: float = 0.70

    # Metadata detection
    title_scan_rows: int = 5
    header_detection_mode: str = "auto"  # auto|manual
    row_names_detection_mode: str = "auto"

    # Summary generation
    summary_max_length: int = 300  # characters
    summary_style: str = "technical"  # technical|plain

    # Ontology
    ontology_enabled: bool = True
    ontology_file: str = "ontology/bim_ontology.yaml"
    ontology_confidence_threshold: float = 0.60

    # Export
    export_tree_map: bool = True
    tree_map_format: str = "html"  # html|json
    export_knowledge_graph: bool = True

    # Performance
    max_parallel_tables: int = 4
    cache_llm_responses: bool = True


def load_v4_config() -> V4Config:
    """
    Load V4 configuration from config/stages.yaml.

    For now, returns default config.
    User will add to stages.yaml later.
    """
    # TODO: Load from YAML
    return V4Config()
