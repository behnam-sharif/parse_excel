"""Root conftest.py — adds xlgen_v3 root to sys.path for test discovery."""

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
