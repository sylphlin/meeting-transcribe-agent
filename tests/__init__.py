"""Initialize unit test package path for meeting-transcribe-agent."""

import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent / "skills" / "meeting-transcribe-agent"
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))
