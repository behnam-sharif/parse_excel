"""
Stage UIs were consolidated into the monorepo orchestrator.

From the xlgen_v3 repository root, run:

    streamlit run apps/orchestrator/app.py

The xl_image stage remains importable headless via ``xl_image.run_image`` and
``xl_image.run`` — it does not depend on Streamlit.
"""

from __future__ import annotations


def main() -> None:
    print(
        "The Streamlit UI for xlgen_v3 lives at apps/orchestrator/app.py.\n"
        "Run: streamlit run apps/orchestrator/app.py"
    )


if __name__ == "__main__":
    main()
