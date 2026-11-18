"""Puts the repo root (for `vsearch`) and eval/ (a folder of scripts, not a package) on sys.path."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT, ROOT / "eval"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
