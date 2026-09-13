#!/usr/bin/env python3
"""
tests/check_core_sync.py - Drift guard between the Antigravity CLI's shared core
(`scripts/`, `assets/`) and its duplicated copy inside the Gemini Enterprise agent
package (`gemini-enterprise/app/core/`, `gemini-enterprise/app/assets/`).

Why this exists: the Gemini Enterprise agent is deployed via `agents-cli`, whose
Docker build context is scoped to `gemini-enterprise/` (see
`gemini-enterprise/agents-cli-manifest.yaml` and `gemini-enterprise/Dockerfile`).
That build cannot see anything outside that directory, so the shared logic cannot
be symlinked in from `scripts/` / `assets/` - it has to exist as real, committed
files inside `gemini-enterprise/`. Since both copies are hand-edited and a change
can legitimately start on either side, this script does not try to auto-generate
or auto-fix either copy. It just fails loudly, with a readable diff, whenever the
two sides drift apart, whichever side changed.

Usage:
    python3 tests/check_core_sync.py

Run this after editing anything under scripts/, assets/, gemini-enterprise/app/core/,
or gemini-enterprise/app/assets/, and reconcile any reported diff:
  - If the change is a genuine bug fix or behavior improvement, port it to the
    other side too.
  - If the two products genuinely need to diverge here on purpose, add a short,
    justified entry to KNOWN_DIVERGENCES below instead of leaving the check red.
"""

import difflib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
ASSETS_DIR = ROOT / "assets"
ENTERPRISE_CORE = ROOT / "gemini-enterprise" / "app" / "core"
ENTERPRISE_ASSETS = ROOT / "gemini-enterprise" / "app" / "assets"

# Shared Python modules expected to exist as a near-identical pair.
PYTHON_PAIRS = [
    "__init__.py",
    "audio_utils.py",
    "canonicalizer.py",
    "diarization.py",
    "gcs_utils.py",
    "gemini_engine.py",
    "glossary.py",
    "html_generator.py",
    "meeting_transcribe.py",
]

# Shared asset files expected to exist as a near-identical pair.
# Each entry is (path relative to assets/, path relative to gemini-enterprise/app/assets/).
ASSET_PAIRS = [
    ("prompts/audio_glossary_prompt.md", "prompts/audio_glossary_prompt.md"),
    ("prompts/minutes_prompt.md", "prompts/minutes_prompt.md"),
    ("audio_player_template.html", "audio_player_template.html"),
    ("video_player_template.html", "video_player_template.html"),
]

# Known, justified, mechanical differences between the two copies. Each entry
# is a (pattern, replacement, reason) triple applied to BOTH sides' text
# before diffing, so the check only flags differences beyond these. Add to
# this list only for a real, deliberate product difference - and say why -
# never just to silence an unreviewed drift.
# NOTE: order matters. The two block-level substitutions below must run
# BEFORE the generic import-line substitution, since that generic rule would
# otherwise partially rewrite the block text first and break the block match.
KNOWN_DIVERGENCES = [
    (
        re.compile(
            r"    try:\n"
            r"        from scripts\.audio_utils import is_youtube_url, extract_youtube_id\n"
            r"    except ModuleNotFoundError:\n"
            r"        import sys\n"
            r"        sys\.path\.insert\(0, str\(Path\(__file__\)\.resolve\(\)\.parent\.parent\)\)\n"
            r"        from scripts\.audio_utils import is_youtube_url, extract_youtube_id\n"
        ),
        "    STANDALONE_IMPORT_FALLBACK_BLOCK\n",
        "html_generator.py's `__main__` guard needs a different standalone-"
        "import fallback per product: scripts/ adds the repo root to "
        "sys.path so `python3 scripts/html_generator.py ...` works run "
        "directly outside package context; gemini-enterprise's ADK package "
        "instead falls back to a bare same-directory import matching how "
        "agents-cli executes it. Bootstrapping difference only, not logic.",
    ),
    (
        re.compile(
            r"    try:\n"
            r"        from \.audio_utils import is_youtube_url, extract_youtube_id\n"
            r"    except \(ImportError, ModuleNotFoundError\):\n"
            r"        from audio_utils import is_youtube_url, extract_youtube_id\n"
        ),
        "    STANDALONE_IMPORT_FALLBACK_BLOCK\n",
        "See the scripts/-side entry above for why this differs.",
    ),
    (
        re.compile(r"^(\s*)from \.(\w+) import", re.MULTILINE),
        r"\1from scripts.\2 import",
        "gemini-enterprise/app/core is an installable package using relative "
        "imports; scripts/ is imported as an absolute package rooted at the "
        "repo. Packaging necessity, not a behavior difference.",
    ),
]


def _normalize(text: str) -> str:
    for pattern, replacement, _reason in KNOWN_DIVERGENCES:
        text = pattern.sub(replacement, text)
    return text


def _diff(a_path: Path, b_path: Path) -> str:
    a_text = _normalize(a_path.read_text(encoding="utf-8"))
    b_text = _normalize(b_path.read_text(encoding="utf-8"))
    if a_text == b_text:
        return ""
    return "".join(difflib.unified_diff(
        a_text.splitlines(keepends=True),
        b_text.splitlines(keepends=True),
        fromfile=str(a_path.relative_to(ROOT)),
        tofile=str(b_path.relative_to(ROOT)),
    ))


def get_designated_models() -> set[str]:
    """Parse approved designated models from .env.example as the baseline specification."""
    env_example = ROOT / ".env.example"
    models = set()
    if env_example.exists():
        for line in env_example.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("TRANSCRIBE_MODEL=") or line.startswith("SUMMARY_MODEL="):
                val = line.split("=", 1)[1].strip()
                if val:
                    models.add(val)
    return models


def check_model_invariants() -> list[str]:
    """
    Ensure no code mistakenly introduces or falls back to models outside the designated models
    configured in .env.example.
    Prevents the agent from autonomously switching, inventing, or downgrading to non-designated models.
    """
    designated_models = get_designated_models()
    errors = []
    
    # Matches any gemini model string (e.g. gemini-3.5-transcribe, gemini-1.5-flash, etc.)
    model_pattern = re.compile(r"gemini-[0-9]+(?:\.[0-9]+)?-[a-z0-9\-]+")

    # Scan python source code and evaluation scripts
    check_dirs = [SCRIPTS_DIR, ENTERPRISE_CORE, ROOT / "gemini-enterprise" / "app", ROOT / "gemini-enterprise" / "tests"]
    for d in check_dirs:
        if not d.exists():
            continue
        for p in d.rglob("*.py"):
            text = p.read_text(encoding="utf-8")
            for match in model_pattern.finditer(text):
                found_model = match.group(0)
                if found_model not in designated_models:
                    errors.append(
                        f"[UNAPPROVED MODEL] {p.relative_to(ROOT)}: Found non-designated model '{found_model}'. "
                        f"Only designated models ({', '.join(sorted(designated_models))}) are allowed. "
                        f"Never use models outside the designated configurations."
                    )
    return errors


def main() -> int:
    failures = []

    # 1. Model invariant validation
    model_errors = check_model_invariants()
    if model_errors:
        designated = get_designated_models()
        print("[FAIL] Model Invariant Check Failed:\n")
        print("\n".join(model_errors))
        print(
            f"\n[RULE] Only designated models ({', '.join(sorted(designated))}) may be used. "
            "Never invent, substitute, or downgrade to non-designated models. "
            "To upgrade or change models, update .env / .env.example.\n"
        )
        return 1

    # 2. Dual-copy sync validation
    for filename in PYTHON_PAIRS:
        a = SCRIPTS_DIR / filename
        b = ENTERPRISE_CORE / filename
        if not a.exists() or not b.exists():
            failures.append(f"{a.relative_to(ROOT)} or {b.relative_to(ROOT)} is missing.")
            continue
        diff = _diff(a, b)
        if diff:
            failures.append(diff)

    for rel_a, rel_b in ASSET_PAIRS:
        a = ASSETS_DIR / rel_a
        b = ENTERPRISE_ASSETS / rel_b
        if not a.exists() or not b.exists():
            failures.append(f"{a.relative_to(ROOT)} or {b.relative_to(ROOT)} is missing.")
            continue
        diff = _diff(a, b)
        if diff:
            failures.append(diff)

    if failures:
        print("[FAIL] scripts/assets and gemini-enterprise have drifted apart:\n")
        print("\n".join(failures))
        print(
            "\n[CANONICAL SOURCE OF TRUTH]\n"
            "  'scripts/' and 'assets/' are the primary upstream source of truth.\n"
            "  'gemini-enterprise/app/core/' and 'app/assets/' are downstream sync mirrors.\n"
            "  NEVER downgrade or edit 'scripts/' to match stale downstream copies.\n"
            "  Always port upstream improvements from 'scripts/' to 'gemini-enterprise/',\n"
            "  and never strip '-preview' from Vertex AI model IDs.\n"
        )
        return 1

    print("[OK] scripts/assets and gemini-enterprise core are in sync and model invariants hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
