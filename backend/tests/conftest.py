import os
import sys
import tempfile
from pathlib import Path

# Isolated data dir and a non-default key/password before any app import.
os.environ["BLACKSTART_DATA_DIR"] = tempfile.mkdtemp(prefix="blackstart-test-")
os.environ["BLACKSTART_SECRET_KEY"] = "test-secret-key-0123456789-abcdef"
os.environ["BLACKSTART_ADMIN_USER"] = "admin"
os.environ["BLACKSTART_ADMIN_PASSWORD"] = "Correct-Horse-Battery-9"
os.environ["BLACKSTART_LIVE_ACTIONS"] = "false"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ADMIN_PASSWORD = os.environ["BLACKSTART_ADMIN_PASSWORD"]
