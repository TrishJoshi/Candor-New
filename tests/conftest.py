import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # top-level modules (profile_authenticity_scorer, build_dataset1, ...)
