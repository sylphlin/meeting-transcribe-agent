"""
scripts/srt_speaker_resolver.py - Speaker candidate classification for subtitle streams.

The deterministic subtitle parser extracts candidate speaker tokens from caption cues.
This module decides which tokens are real participants and merges name variants.
The primary path sends the distinct candidate set (not the full subtitle text) to Gemini once.
A rule-based guardrail is the fallback when the cloud client is not available.
"""

import json
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional

# Result contract: candidate token -> canonical name, or None when the token is not a speaker.
SpeakerResolution = Dict[str, Optional[str]]
SpeakerResolver = Callable[[Dict[str, List[str]]], SpeakerResolution]

_NON_SPEAKER_WORDS = {
    "music", "applause", "laughter", "laughs", "cheering", "coughing", "cough",
    "silence", "inaudible", "crosstalk", "groan", "sigh", "sighs", "noise",
    "background", "indistinct", "unintelligible", "pause", "beep", "ringing",
    "note", "notes", "agenda", "slide", "chapter", "caption", "subtitle",
    "translator", "translation",
}
_NON_SPEAKER_PHRASES = re.compile(r'^(?:e\.g\.?|i\.e\.?|etc\.?|http|https|ftp|www)\b', re.IGNORECASE)
_INVALID_TOKENS = {"-", "--", "none", "n/a", "null", "nil", "()", "[]", "q", "a"}
_MAX_NAME_CHARS = 40
_MAX_NAME_WORDS = 6
_SAMPLE_LINES_PER_CANDIDATE = 3
_SAMPLE_LINE_MAX_CHARS = 120


def is_hard_rejected_token(token: str) -> bool:
    """
    Return True when a token can never be a speaker name.
    These checks are safe for all languages and do not need the LLM.
    """
    clean = (token or "").strip()
    if not clean:
        return True
    low = clean.lower()
    if low in _INVALID_TOKENS or clean.isdigit():
        return True
    if "://" in clean or "\\" in clean:
        return True
    if len(clean) > _MAX_NAME_CHARS or len(clean.split()) > _MAX_NAME_WORDS:
        return True
    if re.search(r'[.!?。！？]\s*$', clean) and not re.search(r'\b[A-Z]\.$', clean):
        return True
    if _NON_SPEAKER_PHRASES.match(low):
        return True
    return False


def is_plausible_speaker_token(token: str) -> bool:
    """
    Rule-based guardrail used when no LLM resolver is available.
    Reject sound descriptions and labels with whole-word matching so names such as
    `Isabella` or `Soundarya` stay accepted.
    """
    if is_hard_rejected_token(token):
        return False
    low = token.strip().lower()
    words = re.findall(r"[a-z]+", low)
    if words and all(w in _NON_SPEAKER_WORDS for w in words):
        return False
    if len(words) == 1 and words[0] in _NON_SPEAKER_WORDS:
        return False
    return True


def resolve_with_rules(candidates: Dict[str, List[str]]) -> SpeakerResolution:
    """Deterministic fallback resolver. Keeps the token text as the canonical name."""
    return {
        token: (token.strip() if is_plausible_speaker_token(token) else None)
        for token in candidates
    }


def _load_prompt_template() -> str:
    prompt_path = Path(__file__).parent.parent / "assets" / "prompts" / "speaker_candidate_prompt.md"
    return prompt_path.read_text(encoding="utf-8")


def _trim_samples(candidates: Dict[str, List[str]]) -> Dict[str, List[str]]:
    trimmed: Dict[str, List[str]] = {}
    for token, lines in candidates.items():
        picked: List[str] = []
        for line in lines:
            line = " ".join(str(line).split())
            if not line:
                continue
            picked.append(line[:_SAMPLE_LINE_MAX_CHARS])
            if len(picked) >= _SAMPLE_LINES_PER_CANDIDATE:
                break
        trimmed[token] = picked
    return trimmed


def resolve_with_gemini(
    client,
    candidates: Dict[str, List[str]],
    model: Optional[str] = None,
) -> SpeakerResolution:
    """
    Classify the distinct candidate tokens with one Gemini call.
    The response is validated against the input set. Tokens that the model omits or invents are ignored.
    Raises on transport or parsing errors so the caller can fall back to rules.
    """
    import os
    from google.genai import types

    if not candidates:
        return {}
    model = model or os.environ.get("SUMMARY_MODEL") or "gemini-3.8-flash"
    payload = json.dumps(_trim_samples(candidates), ensure_ascii=False, indent=2)
    prompt = _load_prompt_template().replace("{candidates_json}", payload)

    schema = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "token": {"type": "STRING"},
                "is_speaker": {"type": "BOOLEAN"},
                "canonical_name": {"type": "STRING"},
            },
            "required": ["token", "is_speaker", "canonical_name"],
        },
    }
    config_kwargs = {
        "response_mime_type": "application/json",
        "response_schema": schema,
    }
    if hasattr(types, "ThinkingConfig"):
        config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)

    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(**config_kwargs),
    )
    rows = json.loads(resp.text or "[]")
    if not isinstance(rows, list):
        raise ValueError("Speaker candidate response is not a JSON array.")

    resolution: SpeakerResolution = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        token = str(row.get("token", "")).strip()
        if token not in candidates:
            continue
        if not bool(row.get("is_speaker")):
            resolution[token] = None
            continue
        canonical = re.sub(r'[*`_]', '', str(row.get("canonical_name", "")).strip()).strip()
        resolution[token] = canonical or token

    # Tokens the model skipped fall back to the rule-based decision.
    for token in candidates:
        if token not in resolution:
            resolution[token] = token.strip() if is_plausible_speaker_token(token) else None
    return resolution


def build_speaker_resolver(
    client=None,
    client_factory: Optional[Callable[[], object]] = None,
    model: Optional[str] = None,
    cache_path: Optional[Path] = None,
) -> SpeakerResolver:
    """
    Build a resolver callable with in-process and optional on-disk caching.
    The Gemini client is created lazily on first use through `client_factory`.
    When the client is not available, the resolver uses the rule-based guardrail.
    """
    state = {"client": client, "warned": False}
    memo: Dict[str, SpeakerResolution] = {}

    def _cache_key(candidates: Dict[str, List[str]]) -> str:
        return json.dumps(sorted(candidates.keys()), ensure_ascii=False)

    def _load_disk(key: str) -> Optional[SpeakerResolution]:
        if not cache_path or not Path(cache_path).is_file():
            return None
        try:
            data = json.loads(Path(cache_path).read_text(encoding="utf-8"))
            entry = data.get(key)
            return dict(entry) if isinstance(entry, dict) else None
        except Exception:
            return None

    def _save_disk(key: str, value: SpeakerResolution) -> None:
        if not cache_path:
            return
        try:
            path = Path(cache_path)
            data = {}
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8")) or {}
            data[key] = value
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _get_client():
        if state["client"] is not None:
            return state["client"]
        if client_factory is None:
            return None
        try:
            state["client"] = client_factory()
        except Exception as e:
            if not state["warned"]:
                print(f"[*] Note: Gemini speaker candidate classification not available ({e}). Using rule-based guardrail.")
                state["warned"] = True
            state["client"] = None
        return state["client"]

    def resolver(candidates: Dict[str, List[str]]) -> SpeakerResolution:
        if not candidates:
            return {}
        key = _cache_key(candidates)
        if key in memo:
            return memo[key]
        cached = _load_disk(key)
        if cached is not None:
            memo[key] = cached
            return cached

        active_client = _get_client()
        result: Optional[SpeakerResolution] = None
        if active_client is not None:
            try:
                print(f"[*] Classifying {len(candidates)} subtitle speaker candidates with Gemini...")
                result = resolve_with_gemini(active_client, candidates, model=model)
            except Exception as e:
                print(f"[!] Warning: Speaker candidate classification failed ({e}). Using rule-based guardrail.")
                result = None
        if result is None:
            result = resolve_with_rules(candidates)
        else:
            _save_disk(key, result)
        memo[key] = result
        return result

    return resolver
