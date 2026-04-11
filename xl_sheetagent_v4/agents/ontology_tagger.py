"""
Ontology Tagger Agent.

Tags tables with BIM domain ontology concepts using LLM analysis.
"""

import json
import logging
from typing import Dict, List, Optional, Any
from pathlib import Path

from ..ontology import (
    BIMOntology,
    load_default_ontology,
)
from ..prompts.ontology_tagging import build_ontology_prompt
from ..llm_dispatch import complete_text


logger = logging.getLogger(__name__)


class OntologyTagger:
    """
    Tags tables with ontology concepts.

    Uses LLM to analyze table metadata and assign relevant BIM concepts.

    Attributes
    ----------
    ontology : BIMOntology
        Loaded BIM ontology
    llm_client : Optional[Any]
        LLM client (OpenAI, Anthropic, etc.) - to be added by user
    model : str
        LLM model name
    max_tokens : int
        Max tokens for LLM response
    temperature : float
        LLM temperature
    confidence_threshold : float
        Minimum confidence for accepting tags
    """

    def __init__(
        self,
        ontology: Optional[BIMOntology] = None,
        llm_client: Optional[Any] = None,
        model: str = "gpt-4o",
        max_tokens: int = 500,
        temperature: float = 0.0,
        confidence_threshold: float = 0.60,
    ):
        """
        Initialize ontology tagger.

        Parameters
        ----------
        ontology : BIMOntology, optional
            BIM ontology. If None, loads default.
        llm_client : Any, optional
            LLM client instance. User will add this later.
        model : str
            LLM model name
        max_tokens : int
            Max tokens for response
        temperature : float
            LLM temperature
        confidence_threshold : float
            Minimum confidence for accepting tags
        """
        self.ontology = ontology or load_default_ontology()
        self.llm_client = llm_client
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.confidence_threshold = confidence_threshold

        logger.info(
            f"OntologyTagger initialized: "
            f"model={model}, "
            f"confidence_threshold={confidence_threshold}"
        )

    def tag_table(
        self,
        title: str,
        summary: str,
        headers: List[str],
        row_names: List[str],
        table_type: str,
    ) -> Dict[str, Any]:
        """
        Tag a table with ontology concepts.

        Parameters
        ----------
        title : str
            Table title
        summary : str
            Table summary
        headers : List[str]
            Column headers
        row_names : List[str]
            Row names
        table_type : str
            Table type (e.g., "cost_analysis")

        Returns
        -------
        Dict[str, Any]
            {
                "tags": List[str],
                "confidence": float,
                "reasoning": str,
                "success": bool,
                "error": Optional[str]
            }
        """

        # Build prompt
        prompt = build_ontology_prompt(
            title=title,
            summary=summary,
            headers=headers,
            row_names=row_names,
            table_type=table_type,
        )

        logger.debug(f"Tagging table: {title}")

        # Call LLM
        try:
            llm_response = self._call_llm(prompt)
            parsed = self._parse_llm_response(llm_response)

            # Validate tags
            validated_tags = self._validate_tags(parsed.get("tags", []))

            result = {
                "tags": validated_tags,
                "confidence": parsed.get("confidence", 0.0),
                "reasoning": parsed.get("reasoning", ""),
                "success": True,
                "error": None,
            }

            # Filter by confidence threshold
            if result["confidence"] < self.confidence_threshold:
                logger.warning(
                    f"Tags below confidence threshold: "
                    f"{result['confidence']:.2f} < {self.confidence_threshold}"
                )
                result["tags"] = []

            logger.info(
                f"Tagged table '{title}': "
                f"tags={result['tags']}, confidence={result['confidence']:.2f}"
            )

            return result

        except Exception as e:
            logger.error(f"Error tagging table '{title}': {e}", exc_info=True)
            return {
                "tags": [],
                "confidence": 0.0,
                "reasoning": "",
                "success": False,
                "error": str(e),
            }

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
            If llm_client is not set (user needs to add API key)
        """

        if self.llm_client is None:
            raise NotImplementedError(
                "LLM client not configured. "
                "Set OPENAI_API_KEY and pass llm_client=openai.OpenAI() into OntologyTagger."
            )

        text, _delta = complete_text(
            self.llm_client,
            self.model,
            prompt,
            self.max_tokens,
            self.temperature,
        )
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

        Raises
        ------
        json.JSONDecodeError
            If response is not valid JSON
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
            logger.debug(f"Response text: {response}")
            raise

    def _validate_tags(self, tags: List[str]) -> List[str]:
        """
        Validate tags against ontology.

        Parameters
        ----------
        tags : List[str]
            Tags from LLM

        Returns
        -------
        List[str]
            Valid tags only
        """

        valid_tags = []
        valid_concept_ids = set(self.ontology.concepts_by_id.keys())

        for tag in tags:
            if tag in valid_concept_ids:
                valid_tags.append(tag)
            else:
                logger.warning(f"Invalid tag (not in ontology): {tag}")

        return valid_tags

    def batch_tag_tables(
        self,
        tables: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Tag multiple tables in batch.

        Parameters
        ----------
        tables : List[Dict[str, Any]]
            List of table dictionaries with keys:
            - title
            - summary
            - headers
            - row_names
            - table_type

        Returns
        -------
        List[Dict[str, Any]]
            List of tagging results
        """

        results = []
        for i, table in enumerate(tables):
            logger.info(f"Tagging table {i+1}/{len(tables)}")

            result = self.tag_table(
                title=table.get("title", ""),
                summary=table.get("summary", ""),
                headers=table.get("headers", []),
                row_names=table.get("row_names", []),
                table_type=table.get("table_type", "other"),
            )

            results.append(result)

        return results

    def get_concept_info(self, concept_id: str) -> Optional[Dict[str, Any]]:
        """
        Get information about a specific concept.

        Parameters
        ----------
        concept_id : str
            Concept ID

        Returns
        -------
        Optional[Dict[str, Any]]
            Concept information or None if not found
        """

        concept = self.ontology.concepts_by_id.get(concept_id)
        if concept is None:
            return None

        return {
            "id": concept.id,
            "name": concept.name,
            "description": concept.description,
            "keywords": concept.keywords,
            "examples": concept.examples,
        }

    def list_all_concepts(self) -> List[Dict[str, str]]:
        """
        List all available concepts.

        Returns
        -------
        List[Dict[str, str]]
            List of concept summaries
        """

        return [
            {
                "id": c.id,
                "name": c.name,
                "description": c.description,
            }
            for c in self.ontology.concepts
        ]


def create_tagger(
    llm_client: Optional[Any] = None,
    model: str = "gpt-4o",
    confidence_threshold: float = 0.60,
) -> OntologyTagger:
    """
    Factory function to create ontology tagger.

    Parameters
    ----------
    llm_client : Any, optional
        LLM client instance
    model : str
        LLM model name
    confidence_threshold : float
        Minimum confidence threshold

    Returns
    -------
    OntologyTagger
        Initialized tagger
    """

    return OntologyTagger(
        llm_client=llm_client,
        model=model,
        confidence_threshold=confidence_threshold,
    )
