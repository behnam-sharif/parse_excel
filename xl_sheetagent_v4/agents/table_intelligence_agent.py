"""
Table Intelligence Agent.

LLM-powered agent that analyzes tables and extracts rich metadata.
"""

import json
import logging
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path

from ..tools import (
    read_table_data,
    detect_metadata_zones,
    detect_children,
)
from ..tools.table_reader import compute_structural_summary
from ..prompts.table_analysis import build_table_analysis_prompt
from ..schemas.enriched_table import (
    EnrichedTable,
    TableMetadata,
    ChildTable,
    PatternObservation,
)
from ..llm_dispatch import complete_text

logger = logging.getLogger(__name__)


class TableIntelligenceAgent:
    """
    LLM-powered agent for table analysis.

    Attributes
    ----------
    llm_client : Optional[Any]
        LLM client (OpenAI, Anthropic, etc.)
    model : str
        LLM model name
    max_tokens : int
        Max tokens for LLM response
    temperature : float
        LLM temperature
    """

    def __init__(
        self,
        llm_client: Optional[Any] = None,
        model: str = "gpt-4o",
        max_tokens: int = 2000,
        temperature: float = 0.0,
    ):
        """
        Initialize table intelligence agent.

        Parameters
        ----------
        llm_client : Any, optional
            LLM client instance
        model : str
            LLM model name
        max_tokens : int
            Max tokens for response
        temperature : float
            LLM temperature
        """
        self.llm_client = llm_client
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

        self.llm_calls = 0
        self.total_tokens = 0

        logger.info(
            f"TableIntelligenceAgent initialized: model={model}"
        )

    def analyze_table(
        self,
        table_id: str,
        sheet_name: str,
        bbox: str,
        ws: Any,
        depth: int = 0,
        max_depth: int = 3,
        all_tables_in_sheet: Optional[List] = None,
        run_dir: Optional[Path] = None,
    ) -> EnrichedTable:
        """
        Analyze a single table and extract metadata.

        Parameters
        ----------
        table_id : str
            Unique table ID
        sheet_name : str
            Sheet name
        bbox : str
            Excel bbox like "B3:H105"
        ws : Worksheet
            openpyxl worksheet
        depth : int
            Current recursion depth
        max_depth : int
            Maximum recursion depth

        Returns
        -------
        EnrichedTable
            Enriched table with metadata and children
        """

        logger.info(
            f"Analyzing table: {table_id} (bbox={bbox}, depth={depth})"
        )

        # Step 1: Read table data
        table_data = read_table_data(bbox, ws, include_formatting=True)

        grid = table_data["grid"]
        types = table_data["types"]
        formatting = table_data.get("formatting", {})
        bbox_coords = table_data["bbox_coords"]

        # Step 2: Heuristic pre-analysis
        metadata_zones = detect_metadata_zones(grid, types, formatting)

        # Step 2.1: v3a header candidates (non-numerical rows in top 20%)
        from ..tools.metadata_detector import detect_header_candidates_v3a
        header_candidates_v3a = detect_header_candidates_v3a(grid, types, bbox_coords)
        metadata_zones["header_candidates_v3a"] = header_candidates_v3a

        table_data["metadata_zones"] = metadata_zones

        # Step 2.5: Structural summary (scans ALL rows)
        structural_summary = compute_structural_summary(grid, types, formatting)
        table_data["structural_summary"] = structural_summary

        # Step 2.6: Check for missing header / inheritance
        header_inheritance = None
        if all_tables_in_sheet:
            has_header, _ = self._has_header_row(grid, types)
            if not has_header:
                logger.warning(f"Table {table_id} has no header row, checking inheritance")
                header_inheritance = self._find_header_inheritance_candidate(
                    table_id, bbox_coords, all_tables_in_sheet,
                )
                if header_inheritance:
                    logger.info(f"Will inherit header from {header_inheritance['source_table_id']}")

        # Step 2.7: Load validation inputs from upstream stages
        validation_inputs = self._load_validation_inputs(
            sheet_name, bbox, table_id, run_dir
        )

        # Step 3: Call LLM for analysis
        analysis_result = self._call_llm_analysis(
            table_data,
            sheet_name,
            bbox,
            validation_inputs=validation_inputs,
            header_inheritance=header_inheritance,
        )

        # Step 4: Parse LLM response
        parsed = self._parse_llm_response(analysis_result)

        # Step 5: Build metadata
        metadata = self._build_metadata(parsed)

        # Step 6: Detect children (from LLM + heuristics)
        llm_children = parsed.get("children", [])
        heuristic_children = detect_children(
            grid, types, bbox, bbox_coords, formatting
        )

        # Merge children (union merge: LLM + heuristic)
        children_list = self._merge_children(llm_children, heuristic_children)

        # Step 6.3: Cross-validate children with grammar cuts
        grammar_cuts = validation_inputs.get("grammar_cuts", [])
        if grammar_cuts:
            children_list = self._validate_children_with_cuts(
                children_list, grammar_cuts, bbox_coords
            )

        # Step 6.5: Build two-level hierarchy
        children_list = self._nest_children_under_parents(children_list)

        # Step 7: Recursively analyze children
        enriched_children = []
        if depth < max_depth:
            for child_data in children_list:
                child_bbox = child_data.get("bbox")
                child_section = child_data.get("section_name", "")

                if child_bbox:
                    # Recursively analyze child
                    child_id = f"{table_id}_child_{len(enriched_children)}"
                    child_table = self.analyze_table(
                        table_id=child_id,
                        sheet_name=sheet_name,
                        bbox=child_bbox,
                        ws=ws,
                        depth=depth + 1,
                        max_depth=max_depth,
                    )

                    # Convert to ChildTable
                    child_obj = ChildTable(
                        section_name=child_section,
                        bbox=child_bbox,
                        row_range=child_data.get("row_range", []),
                        confidence=child_data.get("confidence", 0.0),
                        reasoning=child_data.get("reasoning", ""),
                        metadata=child_table.metadata,
                        summary=child_table.summary,
                        children=child_table.children,
                    )

                    enriched_children.append(child_obj)

        # Step 7.5: Parse pattern observations
        raw_patterns = parsed.get("pattern_observations", [])
        pattern_observations = []
        for rp in raw_patterns:
            if isinstance(rp, dict):
                pattern_observations.append(PatternObservation(
                    pattern_type=rp.get("pattern_type", "other"),
                    confidence=rp.get("confidence", 0.0),
                    evidence=rp.get("evidence", ""),
                    applies_to_tables=rp.get("applies_to_tables", []),
                    generalizable=rp.get("generalizable", False),
                ))

        # Step 8: Build EnrichedTable
        enriched = EnrichedTable(
            table_id=table_id,
            sheet_name=sheet_name,
            bbox=bbox,
            bbox_coords=bbox_coords,
            metadata=metadata,
            summary=parsed.get("summary", ""),
            summary_confidence=parsed.get("analysis_confidence", 0.0),
            children=enriched_children,
            depth=depth,
            pattern_observations=pattern_observations,
            llm_calls=self.llm_calls,
            llm_tokens_used=self.total_tokens,
        )

        logger.info(
            f"Analyzed table {table_id}: "
            f"title='{metadata.title if metadata else 'N/A'}', "
            f"children={len(enriched_children)}"
        )

        return enriched

    def _call_llm_analysis(
        self,
        table_data: Dict[str, Any],
        sheet_name: str,
        bbox: str,
        validation_inputs: Optional[Dict[str, Any]] = None,
        header_inheritance: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Call LLM to analyze table.

        Parameters
        ----------
        table_data : Dict[str, Any]
            Table data from read_table_data
        sheet_name : str
            Sheet name
        bbox : str
            Excel bbox
        validation_inputs : Dict[str, Any], optional
            Grammar cuts and rejected splits from upstream
        header_inheritance : Dict[str, Any], optional
            Header inheritance context

        Returns
        -------
        str
            LLM response text
        """

        # Build prompt
        prompt = build_table_analysis_prompt(
            table_data=table_data,
            sheet_name=sheet_name,
            bbox_excel=bbox,
            validation_inputs=validation_inputs,
            header_inheritance=header_inheritance,
        )

        logger.debug(f"LLM prompt length: {len(prompt)} chars")

        # Call LLM
        response = self._call_llm(prompt)

        self.llm_calls += 1

        return response

    def _call_llm(self, prompt: str) -> str:
        """
        Call LLM with prompt.

        Parameters
        ----------
        prompt : str
            Formatted prompt

        Returns
        -------
        str
            LLM response text

        Raises
        ------
        NotImplementedError
            If llm_client is not set
        """

        if self.llm_client is None:
            raise NotImplementedError(
                "LLM client not configured. "
                "Set OPENAI_API_KEY (or pass an API key into your client) and pass "
                "llm_client=openai.OpenAI() into run_v4_analysis."
            )

        text, delta = complete_text(
            self.llm_client,
            self.model,
            prompt,
            self.max_tokens,
            self.temperature,
        )
        self.total_tokens += delta
        return text

    def _parse_llm_response(self, response: str) -> Dict[str, Any]:
        """
        Parse LLM JSON response.

        Parameters
        ----------
        response : str
            Raw LLM response

        Returns
        -------
        Dict[str, Any]
            Parsed JSON object
        """

        # Strip markdown code blocks if present
        response = response.strip()
        if response.startswith("```json"):
            response = response[7:]
        if response.startswith("```"):
            response = response[3:]
        if response.endswith("```"):
            response = response[:-3]

        response = response.strip()

        # Parse JSON
        try:
            parsed = json.loads(response)
            return parsed
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response: {e}")
            logger.debug(f"Response text: {response[:500]}")
            # Return empty structure
            return {
                "title": "Parse Error",
                "title_confidence": 0.0,
                "title_source": "missing",
                "header": [],
                "header_confidence": 0.0,
                "header_source": "missing",
                "row_names": [],
                "row_names_confidence": 0.0,
                "row_names_source": "missing",
                "summary": "Failed to parse LLM response",
                "children": [],
                "table_type": "other",
                "analysis_confidence": 0.0,
                "pattern_observations": [],
            }

    def _build_metadata(self, parsed: Dict[str, Any]) -> TableMetadata:
        """
        Build TableMetadata from parsed LLM response.

        Parameters
        ----------
        parsed : Dict[str, Any]
            Parsed LLM JSON

        Returns
        -------
        TableMetadata
            Metadata object
        """

        metadata = TableMetadata(
            title=parsed.get("title", ""),
            title_confidence=parsed.get("title_confidence", 0.0),
            title_source=parsed.get("title_source", "detected"),
            header=parsed.get("header", []),
            header_confidence=parsed.get("header_confidence", 0.0),
            header_source=parsed.get("header_source", "detected"),
            header_inherited_from=parsed.get("header_inherited_from"),
            header_location=parsed.get("header_location"),
            row_names=parsed.get("row_names", []),
            row_names_confidence=parsed.get("row_names_confidence", 0.0),
            row_names_source=parsed.get("row_names_source", "detected"),
            row_names_location=parsed.get("row_names_location"),
            data_region=parsed.get("data_region"),
            table_type=parsed.get("table_type", "other"),
            analysis_confidence=parsed.get("analysis_confidence", 0.0),
            reasoning=parsed.get("reasoning", ""),
        )

        # Enforce: row names are NEVER inherited
        if metadata.row_names_source == "inherited":
            logger.warning("Row names cannot be inherited — marking as missing")
            metadata.row_names_source = "missing"
            metadata.row_names = []

        return metadata

    def _has_header_row(
        self, grid: List[List[Any]], types: List[List[str]]
    ) -> Tuple[bool, Optional[int]]:
        """
        Detect if the table has its own header row.

        Checks first 3 rows for >=70% text, <30% numbers.

        Returns
        -------
        Tuple[bool, Optional[int]]
            (has_header, header_row_index)
        """
        for r in range(min(3, len(grid))):
            if r >= len(types):
                break
            row_types = types[r]
            text_count = sum(1 for t in row_types if t == "TEXT")
            num_count = sum(1 for t in row_types if t == "NUMBER")
            total = text_count + num_count
            if total == 0:
                continue
            if text_count / total >= 0.7 and num_count / total < 0.3:
                return True, r
        return False, None

    def _find_header_inheritance_candidate(
        self,
        current_table_id: str,
        current_bbox: Dict[str, int],
        all_tables_in_sheet: List,
    ) -> Optional[Dict[str, Any]]:
        """
        Find table above current table with matching column alignment.

        Parameters
        ----------
        current_table_id : str
            ID of the current table
        current_bbox : Dict[str, int]
            Current table bbox coords {r_top, c_left, r_bot, c_right}
        all_tables_in_sheet : List[EnrichedTable]
            Previously analyzed tables on this sheet

        Returns
        -------
        Optional[Dict[str, Any]]
            {source_table_id, inherited_header, row_distance, confidence}
            or None if no candidate found
        """
        current_left = current_bbox["c_left"]
        current_right = current_bbox["c_right"]
        current_top = current_bbox["r_top"]

        candidates = []
        for other_table in all_tables_in_sheet:
            if other_table.table_id == current_table_id:
                continue
            other_bbox = other_table.bbox_coords
            # Must be above
            if other_bbox["r_top"] >= current_top:
                continue
            # Columns must align (within 1 col tolerance)
            cols_align = (
                abs(other_bbox["c_left"] - current_left) <= 1
                and abs(other_bbox["c_right"] - current_right) <= 1
            )
            if cols_align and other_table.metadata and other_table.metadata.header:
                row_distance = current_top - other_bbox["r_bot"]
                confidence = 0.9 if row_distance <= 5 else 0.7
                candidates.append({
                    "source_table_id": other_table.table_id,
                    "inherited_header": other_table.metadata.header,
                    "row_distance": row_distance,
                    "confidence": confidence,
                })

        if candidates:
            closest = min(candidates, key=lambda c: c["row_distance"])
            logger.info(
                f"Found header inheritance candidate: {closest['source_table_id']} "
                f"(distance={closest['row_distance']} rows)"
            )
            return closest
        return None

    def _load_validation_inputs(
        self,
        sheet_name: str,
        bbox: str,
        table_id: str,
        run_dir: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """
        Load grammar cuts and rejected splits from upstream stages.

        Parameters
        ----------
        sheet_name : str
            Sheet name to filter on
        bbox : str
            Excel bbox
        table_id : str
            Table ID
        run_dir : Path, optional
            Run directory path

        Returns
        -------
        Dict[str, Any]
            {"grammar_cuts": [...], "rejected_splits": [...]}
        """
        validation_data: Dict[str, Any] = {
            "grammar_cuts": [],
            "rejected_splits": [],
        }

        if not run_dir:
            return validation_data

        run_dir = Path(run_dir)

        # Load grammar cuts from xl_parse
        cut_scores_path = run_dir / "parse_output" / "cut_scores.jsonl"
        if cut_scores_path.exists():
            try:
                with open(cut_scores_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        cut = json.loads(line)
                        if cut.get("sheet_name") == sheet_name:
                            validation_data["grammar_cuts"].append(cut)
                logger.debug(
                    f"Loaded {len(validation_data['grammar_cuts'])} grammar cuts "
                    f"for sheet '{sheet_name}'"
                )
            except Exception as e:
                logger.warning(f"Failed to load cut_scores.jsonl: {e}")

        # Load rejected splits from xl_table (if available)
        rejected_path = run_dir / "table_output" / "rejected_splits.jsonl"
        if rejected_path.exists():
            try:
                with open(rejected_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        split = json.loads(line)
                        if split.get("table_id") == table_id:
                            validation_data["rejected_splits"].append(split)
                logger.debug(
                    f"Loaded {len(validation_data['rejected_splits'])} rejected splits "
                    f"for table '{table_id}'"
                )
            except Exception as e:
                logger.warning(f"Failed to load rejected_splits.jsonl: {e}")

        return validation_data

    def _validate_children_with_cuts(
        self,
        children: List[Dict[str, Any]],
        grammar_cuts: List[Dict[str, Any]],
        bbox_coords: Dict[str, int],
    ) -> List[Dict[str, Any]]:
        """
        Cross-validate children boundaries against grammar cuts.

        Boosts confidence when a child boundary aligns with a grammar cut
        (within 2 rows).

        Parameters
        ----------
        children : List[Dict[str, Any]]
            Children from LLM/heuristic merge
        grammar_cuts : List[Dict[str, Any]]
            Grammar cuts from xl_parse
        bbox_coords : Dict[str, int]
            Parent bbox coords

        Returns
        -------
        List[Dict[str, Any]]
            Children with validation metadata
        """
        if not grammar_cuts:
            return children

        r_top = bbox_coords.get("r_top", 0)

        for child in children:
            row_range = child.get("row_range", [])
            if not row_range or len(row_range) < 1:
                continue

            child_start_absolute = r_top + row_range[0]

            matching_cuts = [
                c for c in grammar_cuts
                if abs(c.get("row", -999) - child_start_absolute) <= 2
            ]

            if matching_cuts:
                child["validation"] = {
                    "source": "grammar_cut",
                    "cut_type": matching_cuts[0].get("cut_type", "unknown"),
                    "cut_confidence": matching_cuts[0].get("confidence", 0.0),
                }
                original_conf = child.get("confidence", 0.5)
                child["confidence"] = min(original_conf * 1.15, 0.98)
                logger.debug(
                    f"Child '{child.get('section_name')}' validated by grammar cut "
                    f"at row {child_start_absolute}"
                )
            else:
                child["validation"] = {
                    "source": "llm_only",
                    "note": "No grammar cut at this boundary",
                }

        return children

    @staticmethod
    def _nest_children_under_parents(
        children: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Build two-level hierarchy: nest leaf children under parent sections.

        Children with ``is_parent_section=True`` become containers.
        Leaf children whose ``row_range`` falls inside a parent's range
        are nested under that parent (removed from the top-level list).

        If no parent sections exist, the flat list is returned unchanged.
        """
        parent_sections = [
            c for c in children if c.get("is_parent_section", False)
        ]
        if not parent_sections:
            return children

        leaf_sections = [
            c for c in children if not c.get("is_parent_section", False)
        ]

        # Assign leaves to parents by row range containment
        assigned = set()
        for leaf_idx, leaf in enumerate(leaf_sections):
            leaf_range = leaf.get("row_range", [])
            if not leaf_range or len(leaf_range) < 2:
                continue
            leaf_start = leaf_range[0]
            for parent in parent_sections:
                p_range = parent.get("row_range", [])
                if not p_range or len(p_range) < 2:
                    continue
                if p_range[0] <= leaf_start <= p_range[1]:
                    parent.setdefault("nested_children", []).append(leaf)
                    assigned.add(leaf_idx)
                    break

        # Unassigned leaves stay at top level alongside parents
        top_level = list(parent_sections)
        for idx, leaf in enumerate(leaf_sections):
            if idx not in assigned:
                top_level.append(leaf)

        top_level.sort(key=lambda c: (c.get("row_range") or [0])[0])
        return top_level

    @staticmethod
    def _row_range_iou(a: List[int], b: List[int]) -> float:
        """Compute intersection-over-union of two [start, end] row ranges."""
        if not a or not b or len(a) < 2 or len(b) < 2:
            return 0.0
        start = max(a[0], b[0])
        end = min(a[1], b[1])
        intersection = max(0, end - start + 1)
        union = max(a[1], b[1]) - min(a[0], b[0]) + 1
        return intersection / union if union > 0 else 0.0

    def _merge_children(
        self,
        llm_children: List[Dict[str, Any]],
        heuristic_children: List[Dict[str, Any]],
        heuristic_confidence_scale: float = 0.8,
    ) -> List[Dict[str, Any]]:
        """
        Union-merge children from LLM and heuristics.

        LLM children are the primary set. Heuristic children that don't
        overlap with any LLM child (IoU < 0.5) are added with scaled-down
        confidence.

        Parameters
        ----------
        llm_children : List[Dict[str, Any]]
            Children from LLM
        heuristic_children : List[Dict[str, Any]]
            Children from heuristics
        heuristic_confidence_scale : float
            Scale factor for heuristic-only child confidence

        Returns
        -------
        List[Dict[str, Any]]
            Merged children sorted by row_range start
        """
        # Tag LLM children
        for child in llm_children:
            child.setdefault("source", "llm")

        if not llm_children and not heuristic_children:
            return []

        if not llm_children:
            for child in heuristic_children:
                child["source"] = "heuristic"
                child["confidence"] = child.get("confidence", 0.5) * heuristic_confidence_scale
            return sorted(heuristic_children, key=lambda c: (c.get("row_range") or [0])[0])

        merged = list(llm_children)

        for h_child in heuristic_children:
            h_range = h_child.get("row_range", [])
            overlaps = False
            for l_child in llm_children:
                l_range = l_child.get("row_range", [])
                if self._row_range_iou(h_range, l_range) > 0.5:
                    overlaps = True
                    l_child["source"] = "both"
                    break

            if not overlaps:
                h_child["source"] = "heuristic"
                h_child["confidence"] = h_child.get("confidence", 0.5) * heuristic_confidence_scale
                merged.append(h_child)

        merged.sort(key=lambda c: (c.get("row_range") or [0])[0])

        logger.debug(
            f"Merged children: {len(llm_children)} LLM + "
            f"{sum(1 for c in merged if c.get('source') == 'heuristic')} heuristic-only = "
            f"{len(merged)} total"
        )
        return merged


def create_agent(
    llm_client: Optional[Any] = None,
    model: str = "gpt-4o",
    max_tokens: int = 2000,
) -> TableIntelligenceAgent:
    """
    Factory function to create table intelligence agent.

    Parameters
    ----------
    llm_client : Any, optional
        LLM client instance
    model : str
        LLM model name
    max_tokens : int
        Max tokens

    Returns
    -------
    TableIntelligenceAgent
        Initialized agent
    """

    return TableIntelligenceAgent(
        llm_client=llm_client,
        model=model,
        max_tokens=max_tokens,
    )
