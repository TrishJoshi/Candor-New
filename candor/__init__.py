"""Candor: narrative tracking and CIB detection pipeline.

Two codebases share the ``candor`` package name: this one (API + live pipeline) and the Stage 2
spatial engine in ``narrative-buckets/candor`` (schemas, ingest, spatial_engine, visualize, ...).
Its modules import each other as ``candor.<module>``, so instead of renaming either side we add
its directory to this package's search path. Our modules are found first; avoid reusing its module
names here (that is why the live pipeline lives in ``candor/live.py``, not ``candor/pipeline.py``).
"""
from pathlib import Path

_SPATIAL = Path(__file__).resolve().parent.parent / "narrative-buckets" / "candor"
if _SPATIAL.is_dir() and str(_SPATIAL) not in __path__:
    __path__.append(str(_SPATIAL))
