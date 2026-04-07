# === feature_classifier.py ===

import os
import json
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv
from openpyxl.utils import range_boundaries

_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(dotenv_path=str(_ROOT / ".env"))
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def format_text_prompt(feature_data: dict, content: dict) -> str:
    """
    Formats the prompt for GPT to classify a feature.

    Args:
        feature_data (dict): Information about the feature (type, box range)
        content (dict): Cell data from the feature

    Returns:
        str: Formatted prompt for GPT
    """
    system_prompt = (
        "You are a Health Economics expert and analyzing a sheet block in a Budget Impact Excel model.\n"
        "The user will give you:\n"
        "- The block's range (e.g., A1:C20)\n"
        "- A list of cell values and formulas within that range.\n\n"
        "Your task: describe what this sheet likely contains (e.g., assumptions, outputs, metadata, empty).\n"
        "For each feature or section you identify, include the cell address where it starts and where it ends (e.g., 'The header section starting at A1 and ending at A10 contains...').\n"
        "This helps track where each important element is located in the sheet.\n\n"
        "IMPORTANT: Always end your description with a single sentence starting with 'Overall' that summarizes the main purpose and content of the sheet.\n"
        "Example: 'Overall, this sheet contains the main input parameters and assumptions for the budget impact model.'"
    )

    user_prompt = {
        "block_info": feature_data,
        "content": content
    }

    return f"{system_prompt}\n\nInput Data:\n{json.dumps(user_prompt, indent=2)}"

def classify_feature_llm(feature_data: dict, content: dict) -> str:
    """
    Uses GPT to classify and describe a feature (sheet or block) based on its content.
    First identifies features and their ranges, then uses GPT to describe them.

    Args:
        feature_data (dict): Information about the feature (type, box range)
        content (dict): Cell data from the feature

    Returns:
        str: GPT's description of the feature with structured feature ranges
    """
    # Get the target range
    target_range = feature_data.get("box")
    if not target_range:
        raise ValueError("No target range provided in feature_data")

    print(f"\n=== Feature Classifier Debug ===")
    print(f"Target Range: {target_range}")

    # Filter cell data to only include cells within the target range
    min_col, min_row, max_col, max_row = range_boundaries(target_range)
    print(f"Range boundaries: min_col={min_col}, min_row={min_row}, max_col={max_col}, max_row={max_row}")

    filtered_cell_data = []
    original_cell_count = len(content.get("cell_data", []))

    for cell in content.get("cell_data", []):
        coord = cell["cell"]
        col = ord(coord[0]) - ord('A') + 1
        row = int(''.join(filter(str.isdigit, coord)))

        if min_col <= col <= max_col and min_row <= row <= max_row:
            filtered_cell_data.append(cell)

    print(f"Original cell count: {original_cell_count}")
    print(f"Filtered cell count: {len(filtered_cell_data)}")
    print(f"First few filtered cells: {filtered_cell_data[:3] if filtered_cell_data else 'None'}")
    print("===============================\n")

    # Update content with filtered cell data
    content["cell_data"] = filtered_cell_data

    # First identify features and their ranges
    features = []
    current_feature = None

    for cell in filtered_cell_data:
        if cell.get("value") and isinstance(cell["value"], str):
            value = cell["value"].strip()
            if value and (not current_feature or value.endswith(":")):
                if current_feature:
                    # End previous feature
                    current_feature["end_cell"] = cell["cell"]
                    features.append(current_feature)
                # Start new feature
                current_feature = {
                    "name": value,
                    "start_cell": cell["cell"],
                    "end_cell": None
                }

    # Add last feature if exists
    if current_feature:
        current_feature["end_cell"] = filtered_cell_data[-1]["cell"]
        features.append(current_feature)

    # Format the prompt with feature information
    feature_info = "\n".join([
        f"{i+1}. {feature['name']} ({feature['start_cell']}:{feature['end_cell']})"
        for i, feature in enumerate(features)
    ])

    prompt = f"""
    Analyze the following Excel sheet data and provide a detailed description.

    Identified Features:
    {feature_info}

    Cell Data:
    {json.dumps(filtered_cell_data, indent=2)}

    Please provide a description that:
    1. Starts with "Based on the provided information, the sheet block likely contains the following sections:"
    2. Lists each feature with its cell range
    3. Describes what each feature contains and its purpose
    4. Uses bullet points for detailed information about each feature

    Format your response like this:
    Based on the provided information, the sheet block likely contains the following sections:

    1. [Feature Name] ([Start Cell]:[End Cell]):
       - [Detailed description of what this feature contains and its purpose]
       - [Additional details if needed]

    2. [Next Feature] ([Start Cell]:[End Cell]):
       - [Description]
    """

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=2000
    )

    usage = response.usage
    token_info = {
        "prompt_tokens": usage.prompt_tokens if usage else 0,
        "completion_tokens": usage.completion_tokens if usage else 0,
        "total_tokens": usage.total_tokens if usage else 0,
    }

    return response.choices[0].message.content, token_info
