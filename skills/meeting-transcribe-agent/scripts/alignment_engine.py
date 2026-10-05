"""
scripts/alignment_engine.py - Multilingual LCS Alignment and Timestamp Projection Engine.
Aligns Track A unchunked global speaker diarization words with Track B chunked word timestamps.
Supports CJK character-aware and Western word-level sequence matching, repetition loop suppression,
and LLM semantic paragraph timestamp re-projection.
"""

from difflib import SequenceMatcher
import re
from typing import Any, Dict, List, Optional, Tuple
import unicodedata


_SENTENCE_END_RE = re.compile(r'[.!?。！？]+["\'”’）\)]*$')
_CLAUSE_END_RE = re.compile(r'[,;:，；：、]+["\'”’）\)]*$')


def _is_cjk_char(ch: str) -> bool:
    """Return True if the character belongs to CJK Ideographs, Kana, or Hangul blocks."""
    code = ord(ch)
    return (
        0x3040 <= code <= 0x30FF  # Hiragana and Katakana
        or 0x3400 <= code <= 0x4DBF  # CJK Unified Ideographs Extension A
        or 0x4E00 <= code <= 0x9FFF  # CJK Unified Ideographs
        or 0xAC00 <= code <= 0xD7AF  # Hangul Syllables
        or 0xF900 <= code <= 0xFAFF  # CJK Compatibility Ideographs
        or 0x3100 <= code <= 0x312F  # Bopomofo
    )


def normalize_multilingual_token(text: str) -> str:
    """
    Standardize a multilingual token for sequence alignment.
    Strip all Unicode punctuation and symbols, convert to lowercase,
    and preserve CJK characters, Kana, Hangul, and alphanumeric text.
    """
    if not text:
        return ""
    cleaned = "".join(
        ch.lower()
        for ch in str(text)
        if not unicodedata.category(ch).startswith(("P", "S"))
    )
    return cleaned.strip()


def normalize_speaker_label(raw_label: Any) -> str:
    """Normalize a raw diarization speaker tag into 'Speaker N' format."""
    if raw_label is None:
        return "Speaker 1"
    s = str(raw_label).strip()
    if not s or s.lower() == "unknown":
        return "Speaker 1"
    m = re.match(r"^(?:speaker[\s_-]*|spk[\s_:-]*)?(\d+|[a-zA-Z])$", s, re.IGNORECASE)
    if m:
        return f"Speaker {m.group(1)}"
    return s


def join_multilingual_words(words: List[str]) -> str:
    """
    Join word tokens into natural text.
    Omit artificial spaces between adjacent CJK characters while keeping spaces
    between Western words and around CJK-to-Western boundaries as appropriate.
    """
    if not words:
        return ""
    out: List[str] = []
    for token in words:
        tok = str(token).strip()
        if not tok:
            continue
        if not out:
            out.append(tok)
            continue
        prev = out[-1]
        prev_last = prev[-1]
        curr_first = tok[0]
        # Attach without space when both sides are CJK or CJK punctuation
        if (
            (_is_cjk_char(prev_last) and _is_cjk_char(curr_first))
            or (_is_cjk_char(prev_last) and curr_first in "，。！？；：、）」』】》")
            or (prev_last in "（「『【《" and _is_cjk_char(curr_first))
            or (not _is_cjk_char(prev_last) and curr_first in ",.!?;:)]}")
        ):
            out.append(tok)
        else:
            out.append(" " + tok)
    return "".join(out).strip()


def deduplicate_overlap_micro_words(
    chunk_word_lists: List[Tuple[float, float, List[Dict[str, Any]]]]
) -> List[Dict[str, Any]]:
    """
    Merge micro word lists from overlapping audio chunks into a monotonic stream.
    Each tuple is (chunk_start_sec, chunk_end_sec, micro_words_with_local_offsets).
    When consecutive chunks overlap by a window (e.g. 5 seconds), split ownership
    at the midpoint of the overlap window to prevent duplicate words and time regression.
    """
    if not chunk_word_lists:
        return []

    num_chunks = len(chunk_word_lists)
    merged: List[Dict[str, Any]] = []

    for idx, (c_start, c_end, words) in enumerate(chunk_word_lists):
        min_allowed_sec = 0.0
        max_allowed_sec = float("inf")

        if idx > 0:
            prev_start, prev_end, _ = chunk_word_lists[idx - 1]
            if prev_end > c_start:
                min_allowed_sec = (c_start + prev_end) / 2.0
            else:
                min_allowed_sec = c_start

        if idx < num_chunks - 1:
            next_start, _, _ = chunk_word_lists[idx + 1]
            if c_end > next_start:
                max_allowed_sec = (next_start + c_end) / 2.0

        for w in words:
            st_raw = w.get("startOffset", w.get("start"))
            et_raw = w.get("endOffset", w.get("end"))
            st_local = AlignmentEngine.parse_offset_static(st_raw)
            et_local = AlignmentEngine.parse_offset_static(et_raw)
            st_global = st_local + c_start
            et_global = max(st_global, et_local + c_start)
            center_sec = (st_global + et_global) / 2.0

            if idx > 0 and center_sec < min_allowed_sec:
                continue
            if idx < num_chunks - 1 and center_sec >= max_allowed_sec:
                continue

            merged.append({
                "word": w.get("word", ""),
                "startOffset": f"{st_global:.3f}s",
                "endOffset": f"{et_global:.3f}s",
                "start": st_global,
                "end": et_global,
            })

    return merged


class AlignmentEngine:
    """
    Aligns Track A unchunked speaker-diarized word streams with Track B word timestamps.
    """

    def __init__(self, segment_base_seconds: float = 0.0):
        self.base_sec = float(segment_base_seconds)

    @staticmethod
    def parse_offset_static(offset_val: Any) -> float:
        """Parse an offset string ('12.340s') or numeric value into float seconds."""
        if offset_val is None:
            return 0.0
        if isinstance(offset_val, (int, float)):
            return float(offset_val)
        s = str(offset_val).strip().rstrip("s")
        if not s:
            return 0.0
        try:
            return float(s)
        except ValueError:
            return 0.0

    def _parse_offset(self, offset_str: Optional[str]) -> float:
        return self.parse_offset_static(offset_str)

    def suppress_repetition_loops(
        self,
        words: List[Dict[str, Any]],
        max_repeats: int = 3,
        max_ngram_len: int = 8,
    ) -> List[Dict[str, Any]]:
        """
        Suppress generative decoding repetition loops (for example, 'I don't know how to'
        repeated 15 times at music or noise transitions) while keeping normal speech.
        """
        if len(words) < 4:
            return list(words)

        norm_tokens = [normalize_multilingual_token(w.get("word", "")) for w in words]
        n_total = len(words)
        keep_indices: List[int] = []
        i = 0

        while i < n_total:
            loop_found = False
            # Check n-gram sizes from largest to smallest
            for ngram_size in range(min(max_ngram_len, (n_total - i) // 2), 0, -1):
                pattern = norm_tokens[i : i + ngram_size]
                if not any(pattern):
                    continue
                repeat_count = 1
                pos = i + ngram_size
                while pos + ngram_size <= n_total and norm_tokens[pos : pos + ngram_size] == pattern:
                    repeat_count += 1
                    pos += ngram_size

                # Multi-word phrases (>= 3 tokens) repeating >= 3 times are loops;
                # Short 1-2 token phrases repeating > max_repeats (e.g. >= 4 times) are loops.
                threshold = 3 if ngram_size >= 3 else (max_repeats + 1)
                if repeat_count >= threshold:
                    # Keep only the first occurrence of the repeated phrase
                    for k in range(i, i + ngram_size):
                        keep_indices.append(k)
                    i = pos
                    loop_found = True
                    break

            if not loop_found:
                keep_indices.append(i)
                i += 1

        return [words[idx] for idx in keep_indices]

    def _build_alignment_units(
        self,
        words: List[Dict[str, Any]],
        is_micro: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Expand word list into alignment units.
        Western words stay as single tokens; CJK tokens expand into individual characters
        with linearly interpolated sub-timestamps so differing CJK segmentation between
        Track A and Track B still aligns with 100% accuracy.
        """
        units: List[Dict[str, Any]] = []
        for w_idx, w in enumerate(words):
            raw_text = str(w.get("word", ""))
            norm = normalize_multilingual_token(raw_text)
            if not norm:
                continue

            st: Optional[float] = None
            et: Optional[float] = None
            if is_micro:
                st_raw = w.get("startOffset", w.get("start"))
                et_raw = w.get("endOffset", w.get("end"))
                if st_raw is not None:
                    st = self._parse_offset(st_raw) + self.base_sec
                if et_raw is not None:
                    et = self._parse_offset(et_raw) + self.base_sec
                if st is not None and et is None:
                    et = st
                elif et is not None and st is None:
                    st = et

            has_cjk = any(_is_cjk_char(ch) for ch in norm)
            if has_cjk and len(norm) > 1:
                # Split into CJK characters and contiguous non-CJK sub-tokens
                sub_tokens = re.findall(
                    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\u3100-\u312f]|[^\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\u3100-\u312f\s]+",
                    norm,
                )
                n_sub = max(1, len(sub_tokens))
                dur = (et - st) if (st is not None and et is not None and et >= st) else 0.0
                cjk_ord = 0
                for s_i, sub_tok in enumerate(sub_tokens):
                    sub_st = (st + (dur * s_i / n_sub)) if st is not None else None
                    sub_et = (st + (dur * (s_i + 1) / n_sub)) if st is not None else None
                    is_single_cjk = len(sub_tok) == 1 and _is_cjk_char(sub_tok)
                    cur_cjk_ord = cjk_ord if is_single_cjk else None
                    if is_single_cjk:
                        cjk_ord += 1
                    units.append({
                        "token": sub_tok,
                        "word_idx": w_idx,
                        "cjk_order": cur_cjk_ord,
                        "start": sub_st,
                        "end": sub_et,
                    })
            else:
                is_single_cjk = len(norm) == 1 and _is_cjk_char(norm)
                units.append({
                    "token": norm,
                    "word_idx": w_idx,
                    "cjk_order": 0 if is_single_cjk else None,
                    "start": st,
                    "end": et,
                })
        return units

    def project_timestamps(
        self,
        macro_words: List[Dict[str, Any]],
        micro_words: List[Dict[str, Any]],
        prefer_micro_cjk: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Project physical word timestamps from Track B (micro_words) onto Track A (macro_words).
        When prefer_micro_cjk is True (for example, when Track A uses cmn-Hans-CN for speaker
        diarization and Track B uses cmn-Hant-TW for Traditional Chinese word timestamps),
        also project Track B's CJK character glyphs onto Track A words.
        """
        if not macro_words:
            return []

        for w in macro_words:
            w.setdefault("start", None)
            w.setdefault("end", None)

        if not micro_words:
            return macro_words

        macro_units = self._build_alignment_units(macro_words, is_micro=False)
        micro_units = self._build_alignment_units(micro_words, is_micro=True)

        norm_macro = [u["token"] for u in macro_units]
        norm_micro = [u["token"] for u in micro_units]

        matcher = SequenceMatcher(None, norm_macro, norm_micro, autojunk=False)
        paired_indices: List[Tuple[int, int]] = []

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    paired_indices.append((i1 + k, j1 + k))
            elif tag == "replace":
                len_a = i2 - i1
                len_b = j2 - j1
                if len_a > 0 and len_b > 0:
                    all_cjk_a = all(any(_is_cjk_char(c) for c in macro_units[idx]["token"]) for idx in range(i1, i2))
                    all_cjk_b = all(any(_is_cjk_char(c) for c in micro_units[idx]["token"]) for idx in range(j1, j2))
                    if all_cjk_a and all_cjk_b and abs(len_a - len_b) <= max(2, int(0.4 * max(len_a, len_b))):
                        for k in range(len_a):
                            j_mapped = j1 + (
                                k if len_a == len_b else min(len_b - 1, int(round(k * (len_b - 1) / max(1, len_a - 1))))
                            )
                            paired_indices.append((i1 + k, j_mapped))

        cjk_replacements: Dict[int, Dict[int, str]] = {}

        for a_idx, b_idx in paired_indices:
            u_macro = macro_units[a_idx]
            u_micro = micro_units[b_idx]
            w_idx = u_macro["word_idx"]
            st = u_micro["start"]
            et = u_micro["end"]
            if st is not None:
                cur_st = macro_words[w_idx].get("start")
                if cur_st is None or st < cur_st:
                    macro_words[w_idx]["start"] = st
            if et is not None:
                cur_et = macro_words[w_idx].get("end")
                if cur_et is None or et > cur_et:
                    macro_words[w_idx]["end"] = et

            if (
                prefer_micro_cjk
                and u_macro.get("cjk_order") is not None
                and len(u_micro["token"]) == 1
                and _is_cjk_char(u_micro["token"])
            ):
                cjk_replacements.setdefault(w_idx, {})[u_macro["cjk_order"]] = u_micro["token"]

        if prefer_micro_cjk and cjk_replacements:
            for w_idx, repl_map in cjk_replacements.items():
                raw_w = str(macro_words[w_idx].get("word", ""))
                chars = list(raw_w)
                cjk_pos = 0
                for c_i, ch in enumerate(chars):
                    if _is_cjk_char(ch):
                        if cjk_pos in repl_map:
                            chars[c_i] = repl_map[cjk_pos]
                        cjk_pos += 1
                macro_words[w_idx]["word"] = "".join(chars)

        return self._interpolate_word_timestamps(macro_words)

    def _interpolate_word_timestamps(
        self,
        words: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Interpolate missing timestamps on unaligned words using anchored neighbors."""
        n = len(words)
        if n == 0:
            return words

        # Fill missing start or end if one side exists on the same word
        for w in words:
            if w.get("start") is not None and w.get("end") is None:
                w["end"] = w["start"] + 0.15
            elif w.get("end") is not None and w.get("start") is None:
                w["start"] = max(0.0, w["end"] - 0.15)

        # Find contiguous spans of unaligned words
        i = 0
        while i < n:
            if words[i].get("start") is not None:
                i += 1
                continue
            j = i
            while j < n and words[j].get("start") is None:
                j += 1

            prev_end = words[i - 1]["end"] if i > 0 and words[i - 1].get("end") is not None else 0.0
            next_start = words[j]["start"] if j < n and words[j].get("start") is not None else prev_end + (j - i) * 0.25
            if next_start < prev_end:
                next_start = prev_end

            span_count = j - i
            step = (next_start - prev_end) / span_count if span_count > 0 else 0.0
            for k in range(span_count):
                w_st = prev_end + k * step
                w_et = prev_end + (k + 1) * step
                words[i + k]["start"] = round(w_st, 3)
                words[i + k]["end"] = round(w_et, 3)

            i = j

        return words

    def aggregate_turns(
        self,
        aligned_words: List[Dict[str, Any]],
        soft_max_duration_sec: float = 65.0,
        hard_max_duration_sec: float = 110.0,
        pause_split_sec: float = 0.65,
    ) -> List[Dict[str, Any]]:
        """
        Aggregate aligned words into speaker turns.
        Automatically splits long single-speaker monologues at natural sentence and pause
        boundaries so interactive player cards remain readable and seekable.
        """
        turns: List[Dict[str, Any]] = []
        cur_turn: Optional[Dict[str, Any]] = None

        for idx, w in enumerate(aligned_words):
            raw_spk = w.get("speakerLabel", w.get("speaker", "Speaker 1"))
            spk = normalize_speaker_label(raw_spk)
            word_text = str(w.get("word", "")).strip()
            if not word_text:
                continue
            st = w.get("start")
            et = w.get("end")

            should_start_new = False
            if cur_turn is None or cur_turn["speaker"] != spk:
                should_start_new = True
            elif (
                cur_turn["start"] is not None
                and cur_turn["end"] is not None
                and soft_max_duration_sec > 0
            ):
                turn_dur = cur_turn["end"] - cur_turn["start"]
                prev_word_text = cur_turn["words"][-1] if cur_turn["words"] else ""
                gap_sec = (st - cur_turn["end"]) if (st is not None and cur_turn["end"] is not None) else 0.0

                is_sentence_end = bool(_SENTENCE_END_RE.search(prev_word_text))
                is_clause_end = bool(_CLAUSE_END_RE.search(prev_word_text))

                if turn_dur >= soft_max_duration_sec and is_sentence_end and gap_sec >= pause_split_sec:
                    should_start_new = True
                elif turn_dur >= (soft_max_duration_sec + 20.0) and is_sentence_end:
                    should_start_new = True
                elif turn_dur >= hard_max_duration_sec and (is_sentence_end or is_clause_end or gap_sec >= pause_split_sec):
                    should_start_new = True

            if should_start_new:
                if cur_turn:
                    cur_turn["text"] = join_multilingual_words(cur_turn["words"])
                    turns.append(cur_turn)
                cur_turn = {
                    "speaker": spk,
                    "words": [word_text],
                    "word_items": [{"word": word_text, "start": st, "end": et}],
                    "start": st,
                    "end": et,
                }
            else:
                cur_turn["words"].append(word_text)
                cur_turn["word_items"].append({"word": word_text, "start": st, "end": et})
                if st is not None and cur_turn["start"] is None:
                    cur_turn["start"] = st
                if et is not None:
                    cur_turn["end"] = et

        if cur_turn:
            cur_turn["text"] = join_multilingual_words(cur_turn["words"])
            turns.append(cur_turn)

        return self._smooth_turn_timestamps(turns)

    def _smooth_turn_timestamps(
        self,
        turns: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Smooth and fill missing boundary timestamps across adjacent turns."""
        for i, turn in enumerate(turns):
            if turn["start"] is None and i > 0 and turns[i - 1]["end"] is not None:
                turn["start"] = turns[i - 1]["end"]
            if turn["start"] is None:
                turn["start"] = 0.0
            if turn["end"] is None and i < len(turns) - 1 and turns[i + 1]["start"] is not None:
                turn["end"] = turns[i + 1]["start"]
            if turn["end"] is None or turn["end"] < turn["start"]:
                turn["end"] = turn["start"]
        return turns

    def reproject_semantic_paragraphs(
        self,
        turn_words: List[Dict[str, Any]],
        paragraphs: List[str],
        fallback_start: float = 0.0,
        fallback_end: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """
        Re-project LLM semantically segmented paragraphs onto physical word timestamps.
        Returns a list of dicts: [{"text": str, "start": float, "end": float}, ...].
        Guarantees zero LLM timestamp hallucination while enabling semantic paragraph splitting.
        """
        clean_paras = [p.strip() for p in paragraphs if p and p.strip()]
        if not clean_paras:
            return []
        if len(clean_paras) == 1:
            st = turn_words[0]["start"] if (turn_words and turn_words[0].get("start") is not None) else fallback_start
            et = turn_words[-1]["end"] if (turn_words and turn_words[-1].get("end") is not None) else fallback_end
            return [{"text": clean_paras[0], "start": st, "end": max(st, et)}]

        # When underlying word-level timestamps are available, project via character/token LCS
        if turn_words:
            word_units = self._build_alignment_units(turn_words, is_micro=True)
            if word_units:
                para_units: List[Dict[str, Any]] = []
                for p_idx, p_text in enumerate(clean_paras):
                    # Split paragraph into words/characters using the same unit builder
                    pseudo_words = [{"word": tok} for tok in re.findall(r"\S+", p_text)]
                    p_u = self._build_alignment_units(pseudo_words, is_micro=False)
                    for u in p_u:
                        u["para_idx"] = p_idx
                        para_units.append(u)

                norm_para = [u["token"] for u in para_units]
                norm_word = [u["token"] for u in word_units]
                matcher = SequenceMatcher(None, norm_para, norm_word, autojunk=False)

                para_bounds: List[Dict[str, Optional[float]]] = [
                    {"start": None, "end": None} for _ in clean_paras
                ]
                for block in matcher.get_matching_blocks():
                    for i in range(block.size):
                        pu = para_units[block.a + i]
                        wu = word_units[block.b + i]
                        p_idx = pu["para_idx"]
                        st = wu["start"]
                        et = wu["end"]
                        if st is not None:
                            if para_bounds[p_idx]["start"] is None or st < para_bounds[p_idx]["start"]:
                                para_bounds[p_idx]["start"] = st
                        if et is not None:
                            if para_bounds[p_idx]["end"] is None or et > para_bounds[p_idx]["end"]:
                                para_bounds[p_idx]["end"] = et

                # Ensure complete monotonic bounds across all paragraphs
                total_st = word_units[0]["start"] if word_units[0]["start"] is not None else fallback_start
                total_et = word_units[-1]["end"] if word_units[-1]["end"] is not None else fallback_end
                if para_bounds[0]["start"] is None:
                    para_bounds[0]["start"] = total_st
                if para_bounds[-1]["end"] is None:
                    para_bounds[-1]["end"] = total_et

                for idx in range(len(clean_paras)):
                    if para_bounds[idx]["start"] is None:
                        prev_et = para_bounds[idx - 1]["end"] if idx > 0 else total_st
                        para_bounds[idx]["start"] = prev_et if prev_et is not None else total_st
                    if para_bounds[idx]["end"] is None:
                        next_st = (
                            para_bounds[idx + 1]["start"]
                            if (idx + 1 < len(clean_paras) and para_bounds[idx + 1]["start"] is not None)
                            else total_et
                        )
                        para_bounds[idx]["end"] = max(para_bounds[idx]["start"], next_st)
                    # Snap contiguous boundary so paragraph i+1 starts smoothly at paragraph i end
                    if idx > 0 and para_bounds[idx - 1]["end"] is not None:
                        if para_bounds[idx]["start"] < para_bounds[idx - 1]["end"]:
                            para_bounds[idx]["start"] = para_bounds[idx - 1]["end"]
                        if para_bounds[idx]["end"] < para_bounds[idx]["start"]:
                            para_bounds[idx]["end"] = para_bounds[idx]["start"]

                return [
                    {
                        "text": clean_paras[idx],
                        "start": float(para_bounds[idx]["start"]),
                        "end": float(para_bounds[idx]["end"]),
                    }
                    for idx in range(len(clean_paras))
                ]

        # Proportional character-length fallback when word-level array is not present
        char_counts = [max(1, len(normalize_multilingual_token(p))) for p in clean_paras]
        total_chars = sum(char_counts)
        total_dur = max(0.0, fallback_end - fallback_start)
        results: List[Dict[str, Any]] = []
        cur_t = fallback_start
        for idx, p_text in enumerate(clean_paras):
            ratio = char_counts[idx] / total_chars
            p_dur = total_dur * ratio
            next_t = fallback_end if idx == len(clean_paras) - 1 else (cur_t + p_dur)
            results.append({
                "text": p_text,
                "start": round(cur_t, 3),
                "end": round(max(cur_t, next_t), 3),
            })
            cur_t = next_t
        return results
