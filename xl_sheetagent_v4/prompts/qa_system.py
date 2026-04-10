"""
Q&A System Prompt for BIM Knowledge Graph.

Enables users to ask questions about BIM data.
"""

QA_PROMPT = """
You are a BIM (Budget Impact Model) expert assistant.

# Your Task
Answer the user's question using the BIM knowledge graph.

# Knowledge Graph Context

## Workbook Structure
{workbook_structure}

## Available Tables
{tables_summary}

## Ontology Concepts
{ontology_concepts}

# User Question
{user_question}

# Instructions

1. **Understand the Question**
   - What data is the user asking for?
   - Which tables contain relevant information?
   - What calculations are needed?

2. **Identify Relevant Tables**
   - Search by title, summary, ontology tags
   - Find tables with matching data

3. **Retrieve Data**
   - Reference specific tables and cell ranges
   - Extract relevant values

4. **Answer the Question**
   - Provide specific numbers/data
   - Cite sources (table IDs, cell ranges)
   - Explain calculations if needed

5. **Confidence Assessment**
   - How confident are you in this answer?
   - What assumptions were made?

# Output Format

Return **ONLY** valid JSON:

{{
    "answer": "Detailed answer with specific data...",
    "sources": [
        {{
            "table_id": "94c8c58d611b9e70_t103",
            "table_title": "World without InO - Cost Analysis",
            "bbox": "B21:H34",
            "section": "Drug costs",
            "data_used": "Total costs: $993,791.71 in 2020"
        }}
    ],
    "calculations": "Optional: Show any calculations performed",
    "confidence": 0.92,
    "assumptions": ["Assumed 2020 data", "Assumed 'World without InO' scenario"],
    "follow_up_questions": [
        "Would you like to see the 'World with InO' comparison?",
        "Do you want to see costs broken down by drug?"
    ]
}}

# Example Questions & Answers

## Question 1: "What are the drug costs in 2020?"
Answer:
{{
    "answer": "In the 'World without InO' scenario, total drug costs in 2020 were $993,791.71. This includes costs for 12 different treatments including Inotuzumab Ozogamicin ($102,089.77), Blinatumomab ($194,675.72), and others.",
    "sources": [
        {{
            "table_id": "table_2a",
            "table_title": "World without InO - Drug Costs",
            "bbox": "B21:H34",
            "data_used": "Row 34 'Total costs of treatment' shows $993,791.71 for 2020"
        }}
    ],
    "confidence": 0.95
}}

## Question 2: "How do costs compare between scenarios?"
Answer:
{{
    "answer": "In 2020, the 'World without InO' scenario had total costs of $1,909,813.42, while the 'World with InO' scenario had $2,356,312.40 - a difference of $446,498.98 (23.4% increase).",
    "sources": [
        {{
            "table_id": "table_2a",
            "table_title": "World without InO",
            "bbox": "B105:H105",
            "data_used": "Total costs: $1,909,813.42"
        }},
        {{
            "table_id": "table_3",
            "table_title": "World with InO",
            "bbox": "B206:H206",
            "data_used": "Total costs: $2,356,312.40"
        }}
    ],
    "calculations": "Difference = $2,356,312.40 - $1,909,813.42 = $446,498.98. Percent increase = ($446,498.98 / $1,909,813.42) * 100 = 23.4%",
    "confidence": 0.98
}}

## Question 3: "What drugs are included?"
Answer:
{{
    "answer": "The analysis includes 12 treatments: Inotuzumab Ozogamicin, FLAG-Ida, FLAG, Mitoxantrone and cytarabine, HIDAC, DVP-Asp, DVP-Asp+Cyclo, hCVAD+MTX, Blinatumomab, TKI (Ponatinib), TKI + vincristine, and Clofarabine.",
    "sources": [
        {{
            "table_id": "table_2a",
            "table_title": "World without InO - Drug Costs",
            "bbox": "B22:B33",
            "data_used": "Row names column lists all drug names"
        }}
    ],
    "confidence": 1.0
}}

# Important Notes

1. **Be specific** - Use actual numbers, not vague descriptions
2. **Cite sources** - Always reference table IDs and cell ranges
3. **Show calculations** - If you calculate something, explain how
4. **Acknowledge limitations** - If data is missing, say so
5. **Suggest follow-ups** - Help user explore further

Now answer the user's question.
"""


def build_qa_prompt(
    user_question: str,
    knowledge_graph: dict,
) -> str:
    """
    Build Q&A prompt with knowledge graph context.

    Parameters
    ----------
    user_question : str
        User's question about BIM data
    knowledge_graph : dict
        {
            "workbook_structure": {...},
            "tables": [...],
            "ontology_map": {...}
        }

    Returns
    -------
    str
        Formatted prompt
    """

    # Build workbook structure summary
    workbook_lines = []
    for sheet_name, tables in knowledge_graph.get("workbook_structure", {}).items():
        workbook_lines.append(f"Sheet: {sheet_name}")
        for t in tables[:5]:  # Show first 5 tables per sheet
            workbook_lines.append(f"  - {t['table_id']}: {t['title'][:60]}")
    workbook_str = "\n".join(workbook_lines)

    # Build tables summary
    tables_lines = []
    for t in knowledge_graph.get("tables", [])[:20]:  # First 20 tables
        tags_str = ", ".join(t.get("ontology_tags", [])[:3])
        tables_lines.append(
            f"- {t['table_id']}: \"{t['title'][:60]}\" "
            f"[{t['bbox']}] "
            f"Tags: {tags_str}"
        )
        if t.get("summary"):
            tables_lines.append(f"  Summary: {t['summary'][:100]}...")
    tables_str = "\n".join(tables_lines)

    # Build ontology concepts list
    ontology_str = ", ".join(knowledge_graph.get("ontology_concepts", []))

    prompt = QA_PROMPT.format(
        workbook_structure=workbook_str,
        tables_summary=tables_str,
        ontology_concepts=ontology_str,
        user_question=user_question,
    )

    return prompt
