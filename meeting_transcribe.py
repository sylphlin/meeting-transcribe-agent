#!/usr/bin/env python3
"""
meeting_transcribe.py - Transparent root entrypoint for Meeting Transcribe Agent.
Compliant with Agent Skills Specification (https://agentskills.io/specification).
"""

import sys
from pathlib import Path

# Ensure package root is in sys.path
root_dir = Path(__file__).parent.resolve()
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from scripts.meeting_transcribe import (
    main,
    generate_meeting_minutes_and_transcript,
    extract_global_consistency_glossary,
    generate_interactive_html,
)

if __name__ == "__main__":
    main()
