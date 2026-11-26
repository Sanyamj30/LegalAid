"""Script to resume or execute vector re-indexing directly without re-fetching from India Code."""
from __future__ import annotations

from pathlib import Path
import sys

_backend_src = Path(__file__).resolve().parent.parent / "backend" / "src"
if str(_backend_src) not in sys.path:
    sys.path.insert(0, str(_backend_src))

from legalaid.services.vector.reindex import main

if __name__ == "__main__":
    main()
