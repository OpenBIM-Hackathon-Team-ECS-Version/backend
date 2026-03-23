from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
SERVER_DIR = ROOT / "server"

if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from server import create_app


app = create_app()
