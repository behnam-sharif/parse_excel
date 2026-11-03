"""Run this script to create the parse_excel directory structure."""
import os

base = r"C:\Users\behnamsharif\Documents\parse_excel"
dirs = [
    "apps/orchestrator",
    "scripts",
    "xl_init/tools",
    "xl_parse/parse_tools",
    "xl_table/tests/fixtures",
    "xl_image/render",
    "xl_image/features",
    "xl_image/io",
    "xl_image/qc",
    "xl_image/utils",
    "xl_image/adaptive",
    "xl_image/tests",
    "core",
    "data/input",
    "data/output",
    "config",
    "tests",
    "docs",
]

for d in dirs:
    path = os.path.join(base, d)
    os.makedirs(path, exist_ok=True)
    print(f"Created: {path}")

print("\nAll directories created successfully.")
