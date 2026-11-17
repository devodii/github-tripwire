"""Entry point urlwatch loads (``--hooks hooks.py``).

It only makes the repo importable and registers everything in tripwire/hooks/.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tripwire import load_all

load_all()
