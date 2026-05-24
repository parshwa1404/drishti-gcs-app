import sys
from pathlib import Path

# Put the backend directory on the path so tests can import routers, scripts, drishti etc.
sys.path.insert(0, str(Path(__file__).parent.parent))
