import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env()

DB_PATH = Path(os.getenv("HANDOFF_DB", str(ROOT / "handoff.db")))

SLA_MINUTES = {
    ("nurse", "clinician"): 30,
    ("clinician", "lab"): 10,
    ("lab", "clinician"): 5,
    ("clinician", "reception"): 10,
    ("reception", "clinician"): 15,
    ("reception", "patient"): 15,
}
DEFAULT_SLA_MINUTES = 10
IDLE_OWNER_MINUTES = 10
MAX_REASONABLE_SEGMENT_MINUTES = 600
