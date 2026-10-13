import sys
from pathlib import Path

# Модули энергетики/пеленгации лежат в sim/scripts (запускаются и как CLI).
ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "sim" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
