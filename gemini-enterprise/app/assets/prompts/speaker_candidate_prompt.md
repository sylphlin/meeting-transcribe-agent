# Subtitle Speaker Candidate Classification

You classify tokens that a deterministic subtitle parser extracted from a meeting caption stream.
Each token appeared before a dialogue line, inside parentheses or brackets, in a WebVTT voice tag, or before a colon.
Some tokens are real participant names. Other tokens are sound descriptions, notes, labels, or URL fragments.

## Input

A JSON object. Each key is one candidate token. Each value is a list of sample dialogue lines that followed the token.

```json
{candidates_json}
```

## Task

For each candidate token, decide:

1. `is_speaker`: `true` only when the token is the name or handle of a human participant who speaks in the meeting.
   - Sound descriptions (`music`, `applause`, `laughter`, `inaudible`, `crosstalk`) are NOT speakers.
   - Notes and labels (`Note`, `Agenda`, `Q`, `A`, `Slide 3`, `e.g.`) are NOT speakers.
   - URL fragments (`https`, `http`) are NOT speakers.
   - Generic placeholders (`Unknown`, `Speaker`, `Guest`) ARE speakers when they label a person who talks. Keep them as-is.
2. `canonical_name`: the clean display name for this participant.
   - Remove formatting marks, trailing punctuation, and device suffixes such as `(iPhone)` or `- Work`.
   - When two tokens refer to the same person (`Jane Smith`, `Jane Smith (she/her)`, `Smith, Jane`), give them the same `canonical_name`.
   - Keep the original script of the name. Do not translate names.
   - When `is_speaker` is `false`, set `canonical_name` to an empty string.

## Rules

- Output ONLY the candidate tokens given in the input. Do not add new names.
- Do not guess a name from the dialogue text. Use the dialogue only to judge whether the token is a speaker.
- Return a JSON array. Each element has these keys: `token`, `is_speaker`, `canonical_name`.
