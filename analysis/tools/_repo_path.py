"""analysis/tools のスクリプトから analysis パッケージを import できるよう、リポジトリの直下を sys.path に足す。"""

import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[2])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
