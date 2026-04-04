from __future__ import annotations

import os
from pathlib import Path

DB_PATH = Path(
    os.environ.get("CARSEARCH_DB", str(Path.home() / ".carsearch" / "carsearch.db"))
)
HOST = os.environ.get("CARSEARCH_HOST", "0.0.0.0")
PORT = int(os.environ.get("CARSEARCH_PORT", "8000"))
LOG_LEVEL = os.environ.get("CARSEARCH_LOG_LEVEL", "INFO")
