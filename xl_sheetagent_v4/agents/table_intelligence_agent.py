"""
Table Intelligence Agent.

LLM-powered agent that analyzes tables and extracts rich metadata.
"""

import json
import logging
from typing import Dict, List, Any, Optional
from pathlib import Path

from ..tools import (
    read_table_data,
    detect_metadata_zones,
    detect_children,
)
from ..prompts.table_analysis import build_table_analysis_prompt
from ..schemas.enriched_table import (
    EnrichedTable,
    TableMetadata,
    ChildTable,
)

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
        table_data["metadata_zones"] = metadata_zones

        # Step 3: Call LLM for analysis
        analysis_result = self._call_llm_analysis(
            table_data,
            sheet_name,
            bbox,
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

        # Merge children (LLM takes priority)
        children_list = self._merge_children(llm_children, heuristic_children)

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
        )

        logger.debug(f"LLM prompt length: {len(prompt)} chars")

        # Call LLM
        response = self._call_llm(prompt)

        self.llm_calls += 1
        # TODO: Track tokens (needs LLM client integration)

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
                "User needs to add API key and initialize LLM client."
            )

        # Placeholder for actual LLM call
        # User will implement this based on their LLM provider
        #
        # Example for OpenAI:
        # response = self.llm_client.chat.completions.create(
        #     model=self.model,
        #     messages=[{"role": "user", "content": prompt}],
        #     max_tokens=self.max_tokens,
        #     temperature=self.temperature,
        # )
        # return response.choices[0].message.content
        #
        # Example for Anthropic:
        # response = self.llm_client.messages.create(
        #     model=self.model,
        #     max_tokens=self.max_tokens,
        #     temperature=self.temperature,
        #     messages=[{"role": "user", "content": prompt}],
        # )
        # return response.content[0].text

        raise NotImplementedError("LLM client not implemented yet")

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
                "header": [],
                "header_confidence": 0.0,
                "row_names": [],
                "row_names_confidence": 0.0,
                "summary": "Failed to parse LLM response",
                "children": [],
                "table_type": "other",
                "analysis_confidence": 0.0,
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
            header=parsed.get("header", []),
            header_confidence=parsed.get("header_confidence", 0.0),
            header_location=parsed.get("header_location"),
            row_names=parsed.get("row_names", []),
            row_names_confidence=parsed.get("row_names_confidence", 0.0),
            row_names_location=parsed.get("row_names_location"),
            data_region=parsed.get("data_region"),
            table_type=parsed.get("table_type", "other"),
            analysis_confidence=parsed.get("analysis_confidence", 0.0),
            reasoning=parsed.get("reasoning", ""),
        )

        return metadata

    def _merge_children(
        self,
        llm_children: List[Dict[str, Any]],
        heuristic_children: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Merge children from LLM and heuristics.

        LLM takes priority; heuristics fill gaps.

        Parameters
        ----------
        llm_children : List[Dict[str, Any]]
            Children from LLM
        heuristic_children : List[Dict[str, Any]]
            Children from heuristics

        Returns
        -------
        List[Dict[str, Any]]
            Merged children
        """

        # If LLM found children, use those
        if llm_children:
            logger.debug(f"Using {len(llm_children)} children from LLM")
            return llm_children

        # Otherwise use heuristics
        if heuristic_children:
            logger.debug(
                f"Using {len(heuristic_children)} children from heuristics"
            )
            return heuristic_children

        return []


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
