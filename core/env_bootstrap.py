"""
Load environment variables from .env files.

Order (each call uses override=False so existing os.environ wins):
1. Sibling repo xlgen_v2/.env — reuse OPENAI_API_KEY from v2 when v3 has none
2. This repo's .env at project root

Call load_project_dotenv() early in entry points (e.g. load_config, run_xl_init).
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_project_dotenv() -> None:
    """Populate os.environ from xlgen_v2/.env then xlgen_v3/.env without overriding set vars."""
    v2 = _PROJECT_ROOT.parent / "xlgen_v2" / ".env"
    if v2.is_file():
        load_dotenv(v2, override=False)
    local = _PROJECT_ROOT / ".env"
    if local.is_file():
        load_dotenv(local, override=False)
