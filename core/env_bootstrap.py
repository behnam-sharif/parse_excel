"""
Load environment variables from .env files.

Order (each call uses override=False so existing os.environ wins):
1. This repo's .env at project root (xlgen_v3)
2. Sibling repo xlgen_v2/.env — fills vars still unset after (1)
3. xl_sheetagent/xl_sheetagent/.env — fills vars still unset after (1)–(2)

Root is loaded before v2 so a key in xlgen_v3/.env wins over an older key in xlgen_v2/.env.

Call load_project_dotenv() early in entry points (e.g. load_config, run_xl_init).
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_project_dotenv() -> None:
    """Populate os.environ from .env files without overriding set vars."""
    local = _PROJECT_ROOT / ".env"
    if local.is_file():
        load_dotenv(local, override=False)
    v2 = _PROJECT_ROOT.parent / "xlgen_v2" / ".env"
    if v2.is_file():
        load_dotenv(v2, override=False)
    sheetagent = _PROJECT_ROOT / "xl_sheetagent" / "xl_sheetagent" / ".env"
    if sheetagent.is_file():
        load_dotenv(sheetagent, override=False)
