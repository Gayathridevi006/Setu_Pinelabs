import sys
from pathlib import Path

# Ensure project root is on the path when running pytest from any directory
sys.path.insert(0, str(Path(__file__).parent.parent))
