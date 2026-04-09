#!/usr/bin/env python
"""Minimal OpenAI API check: verifies OPENAI_API_KEY after the same .env loading as the pipeline.

Loads, in order (see core.env_bootstrap.load_project_dotenv), then:
  - <repo>/data/.env (only fills vars that are still unset)

Run:
  py -3 scripts/test_openai_key.py

Optional:
  set OPENAI_TEST_MODEL=gpt-4o-mini
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

from core.env_bootstrap import load_project_dotenv


def main() -> int:
    load_project_dotenv()
    data_env = _ROOT / "data" / ".env"
    if data_env.is_file():
        load_dotenv(data_env, override=False)

    key = os.getenv("OPENAI_API_KEY")
    if not key or not key.strip():
        print(
            "FAIL: OPENAI_API_KEY is empty.\n"
            "  Use xlgen_v3/.env, xl_sheetagent/.env, data/.env, or ../xlgen_v2/.env (see core/env_bootstrap.py).\n"
            "  If you set it only in the shell, ensure no typo and restart the terminal."
        )
        return 1

    key = key.strip()
    model = os.getenv("OPENAI_TEST_MODEL", "gpt-4o-mini")

    try:
        from openai import OpenAI

        client = OpenAI(api_key=key)
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with exactly: ok"}],
            max_tokens=8,
        )
        text = (r.choices[0].message.content or "").strip()
        print("OK — API key accepted.")
        print(f"  model={model!r}  reply={text!r}")
        return 0
    except Exception as e:
        print("FAIL — OpenAI request error:")
        print(f"  {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
