"""
debug_trace.py — Debug trace infrastructure for xl_sheetagent (V1D).

TraceAccumulator: per-table trace of scoring calls, steps, LLM calls,
    confidence history, and decisions.
ToolCallRecorder: flat log of all LLM tool calls across the pipeline.
write_debug_outputs: persist traces and tool call records to disk.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_log = logging.getLogger("xl_sheetagent.debug_trace")


# ---------------------------------------------------------------------------
# Per-table trace
# ---------------------------------------------------------------------------

@dataclass
class TableTrace:
    """Accumulated trace for a single table."""

    table_id: str
    scoring_calls: List[Dict[str, Any]] = field(default_factory=list)
    step_entries: List[Dict[str, Any]] = field(default_factory=list)
    llm_calls: List[Dict[str, Any]] = field(default_factory=list)
    confidence_history: List[Dict[str, Any]] = field(default_factory=list)
    decisions: List[Dict[str, Any]] = field(default_factory=list)
    final_confidence: float = 0.0
    final_category: str = "unknown"


# ---------------------------------------------------------------------------
# TraceAccumulator — collects per-table events
# ---------------------------------------------------------------------------

class TraceAccumulator:
    """Collects structured trace events per table."""

    def __init__(self) -> None:
        self._traces: Dict[str, TableTrace] = {}

    def begin_table(self, table_id: str) -> None:
        """Initialise trace for *table_id* (idempotent)."""
        if table_id not in self._traces:
            self._traces[table_id] = TableTrace(table_id=table_id)

    def record_scoring_call(
        self,
        table_id: str,
        function_name: str,
        inputs: Dict[str, Any],
        output: Any,
    ) -> None:
        self.begin_table(table_id)
        self._traces[table_id].scoring_calls.append({
            "function": function_name,
            "inputs": inputs,
            "output": output,
            "ts": time.time(),
        })

    def record_step(
        self,
        table_id: str,
        step_num: str,
        event: Dict[str, Any],
    ) -> None:
        self.begin_table(table_id)
        self._traces[table_id].step_entries.append({
            "step": step_num,
            **event,
            "ts": time.time(),
        })

    def record_llm_call(
        self,
        table_id: str,
        prompt_system: str,
        prompt_user: str,
        response: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: float,
    ) -> None:
        self.begin_table(table_id)
        self._traces[table_id].llm_calls.append({
            "prompt_system": prompt_system[:500],
            "prompt_user": prompt_user[:1000],
            "response": response[:2000],
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "latency_ms": latency_ms,
            "ts": time.time(),
        })

    def record_decision(
        self,
        table_id: str,
        decision_type: str,
        details: Dict[str, Any],
    ) -> None:
        self.begin_table(table_id)
        self._traces[table_id].decisions.append({
            "decision_type": decision_type,
            **details,
            "ts": time.time(),
        })

    def record_confidence(
        self,
        table_id: str,
        stage: str,
        confidence: float,
    ) -> None:
        self.begin_table(table_id)
        self._traces[table_id].confidence_history.append({
            "stage": stage,
            "confidence": confidence,
            "ts": time.time(),
        })

    def finalize_table(
        self,
        table_id: str,
        final_confidence: float,
        final_category: str,
    ) -> None:
        """Set final confidence and category for a table trace."""
        self.begin_table(table_id)
        self._traces[table_id].final_confidence = final_confidence
        self._traces[table_id].final_category = final_category

    def get_trace(self, table_id: str) -> Optional[TableTrace]:
        return self._traces.get(table_id)

    def all_traces(self) -> Dict[str, TableTrace]:
        return dict(self._traces)


# ---------------------------------------------------------------------------
# ToolCallRecorder — flat log of all LLM calls
# ---------------------------------------------------------------------------

class ToolCallRecorder:
    """Records every LLM call across the pipeline."""

    def __init__(self) -> None:
        self._records: List[Dict[str, Any]] = []

    def record(
        self,
        table_id: str,
        system_prompt: str,
        user_prompt: str,
        response_text: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: float,
        model: str,
    ) -> None:
        self._records.append({
            "table_id": table_id,
            "system_prompt": system_prompt[:500],
            "user_prompt": user_prompt[:1000],
            "response_text": response_text[:2000],
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "latency_ms": latency_ms,
            "model": model,
            "ts": time.time(),
        })

    def all_records(self) -> List[Dict[str, Any]]:
        return list(self._records)

    def count(self) -> int:
        return len(self._records)


# ---------------------------------------------------------------------------
# Disk writer
# ---------------------------------------------------------------------------

def write_debug_outputs(
    output_path: str,
    accumulator: Optional[TraceAccumulator],
    recorder: Optional[ToolCallRecorder],
    debug_level: str = "full",
) -> int:
    """Write debug trace files to *output_path*/debug/.

    Parameters
    ----------
    debug_level : str
        "minimal" -> only tool_call_records.jsonl
        "full"    -> per-table traces + tool_call_records.jsonl

    Returns the number of trace files written.
    """
    debug_dir = os.path.join(output_path, "debug")
    os.makedirs(debug_dir, exist_ok=True)

    files_written = 0

    # Always write tool call records
    if recorder is not None:
        records = recorder.all_records()
        tcr_path = os.path.join(debug_dir, "tool_call_records.jsonl")
        with open(tcr_path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, default=str) + "\n")
        files_written += 1

    # Per-table traces only in "full" mode
    if debug_level == "full" and accumulator is not None:
        for table_id, trace in accumulator.all_traces().items():
            safe_id = table_id.replace("/", "_").replace("\\", "_").replace(" ", "_")
            trace_path = os.path.join(debug_dir, f"{safe_id}_trace.json")
            trace_dict = {
                "table_id": trace.table_id,
                "scoring_calls": trace.scoring_calls,
                "step_entries": trace.step_entries,
                "llm_calls": trace.llm_calls,
                "confidence_history": trace.confidence_history,
                "decisions": trace.decisions,
                "final_confidence": trace.final_confidence,
                "final_category": trace.final_category,
            }
            with open(trace_path, "w", encoding="utf-8") as f:
                json.dump(trace_dict, f, indent=2, default=str)
            files_written += 1

    return files_written
