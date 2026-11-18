from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("BOM_ASSISTANT_DATA_DIR", PROJECT_ROOT / "data"))
DATABASE_PATH = Path(
    os.environ.get("BOM_ASSISTANT_DB", DATA_DIR / "bom_assistant.sqlite3")
)
REPORT_DIR = Path(os.environ.get("BOM_ASSISTANT_REPORT_DIR", PROJECT_ROOT / "reports"))

HOST = os.environ.get("BOM_ASSISTANT_HOST", "127.0.0.1")
PORT = int(os.environ.get("BOM_ASSISTANT_PORT", "8765"))
MAX_UPLOAD_BYTES = int(os.environ.get("BOM_ASSISTANT_MAX_UPLOAD_MB", "32")) * 1024 * 1024

MAX_ENUMERATED_PATHS = int(os.environ.get("BOM_MAX_PATHS", "2000"))
