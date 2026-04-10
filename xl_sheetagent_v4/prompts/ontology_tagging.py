"""
Ontology Tagging Prompt for BIM Domain.

Maps tables to BIM domain concepts.
"""

ONTOLOGY_TAGGING_PROMPT = """
You are tagging an Excel table with BIM (Budget Impact Model) ontology concepts.

# BIM Domain Ontology

## Core Concepts

1. **cost_analysis**
   - Keywords: cost, costs, price, expense, expenditure, spending
   - Description: Analysis of costs (drugs, hospitalization, treatments)

2. **drug_comparison**
   - Keywords: drug, treatment, therapy, medication, regimen
   - Description: Comparison of different drug treatments

3. **market_share**
   - Keywords: market share, adoption, utilization, penetration, uptake
   - Description: Market share projections for competing products

4. **patient_population**
   - Keywords: patients, population, eligible, enrolled, treated
   - Description: Patient counts and eligibility criteria

5. **adverse_events**
   - Keywords: adverse events, side effects, safety, toxicity, AE
   - Description: Costs and rates of adverse events

6. **hospitalization**
   - Keywords: hospitalization, hospital, inpatient, admission
   - Description: Hospitalization costs and durations

7. **subsequent_treatment**
   - Keywords: subsequent, second-line, relapse, salvage
   - Description: Costs of subsequent treatments after initial therapy

8. **stem_cell_transplant**
   - Keywords: stem cell, transplant, HSCT, allogeneic, autologous
   - Description: Stem cell transplantation procedures and costs

9. **end_of_life**
   - Keywords: end-of-life, palliative, terminal, EOL
   - Description: End-of-life care costs

10. **scenario_comparison**
    - Keywords: world with, world without, scenario, comparison, vs
    - Description: Comparing different intervention scenarios

11. **time_series**
    - Keywords: year, 2020, 2021, annual, projection, forecast
    - Description: Time-based projections (multi-year)

12. **calculation_summary**
    - Keywords: total, subtotal, sum, grand total
    - Description: Summary/calculation rows

# Table Information

- **Title**: {title}
- **Summary**: {summary}
- **Column Headers**: {headers}
- **Row Names**: {row_names}
- **Table Type**: {table_type}

# Your Task

Identify which ontology concepts apply to this table.

# Rules

1. **Select 1-4 concepts** - Most relevant ones
2. **Confidence 0.0-1.0** - How confident are you?
3. **Reasoning** - Explain why these concepts apply

# Output Format

Return **ONLY** valid JSON (no markdown):

{{
    "tags": ["cost_analysis", "drug_comparison", "time_series"],
    "confidence": 0.92,
    "reasoning": "This table compares drug costs across multiple drugs (drug_comparison) over years 2020-2024 (time_series), with cost values in dollars (cost_analysis)."
}}

# Examples

## Example 1: Drug Costs Table
Table: "Drug Costs - World without InO"
Headers: ["2020", "2021", "2022"]
Row names: ["Inotuzumab", "FLAG-Ida", "Total"]

Output:
{{
    "tags": ["cost_analysis", "drug_comparison", "time_series"],
    "confidence": 0.95,
    "reasoning": "Drug cost comparison over time"
}}

## Example 2: Market Share Table
Table: "Market shares (Total)"
Headers: ["Current", "Future"]
Row names: ["Drug A", "Drug B"]

Output:
{{
    "tags": ["market_share", "drug_comparison"],
    "confidence": 0.88,
    "reasoning": "Market share projections for competing drugs"
}}

## Example 3: Adverse Events Costs
Table: "Costs of adverse events on treatment"
Headers: ["2020", "2021"]
Row names: ["Inotuzumab", "FLAG-Ida", "Total"]

Output:
{{
    "tags": ["adverse_events", "cost_analysis", "time_series"],
    "confidence": 0.90,
    "reasoning": "Costs related to adverse events over multiple years"
}}

Now analyze the table and return your JSON response.
"""


def build_ontology_prompt(
    title: str,
    summary: str,
    headers: list,
    row_names: list,
    table_type: str,
) -> str:
    """
    Build ontology tagging prompt.

    Parameters
    ----------
    title : str
        Table title
    summary : str
        Table summary
    headers : list
        Column headers
    row_names : list
        Row names
    table_type : str
        Table type from analysis

    Returns
    -------
    str
        Formatted prompt
    """

    headers_str = ", ".join(str(h)[:30] for h in headers[:10])
    row_names_str = ", ".join(str(r)[:30] for r in row_names[:10])

    prompt = ONTOLOGY_TAGGING_PROMPT.format(
        title=title,
        summary=summary,
        headers=headers_str,
        row_names=row_names_str,
        table_type=table_type,
    )

    return prompt
