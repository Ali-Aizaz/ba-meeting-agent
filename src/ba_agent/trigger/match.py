"""Wake-phrase matching. Lookalikes are rejected before a fuzzy match."""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

_EDGE = ".,!?;:\"'()[]{}“”…—-"
_SENTENCE_END_CHARS = ".?!;"
_ABBREV_WORDS = {"mr", "mrs", "ms", "dr", "vs", "st", "jr", "sr", "inc", "ltd", "co", "eg", "ie", "us", "uk"}
_IMMEDIATE_ONE_WORD = {
    "leave",
    "exit",
    "bye",
    "quit",
    "save",
    "sync",
    "summarize",
    "summary",
    "recap",
    "notes",
    "tasks",
    "actions",
    "status",
    "help",
    "hello",
    "hi",
    "hey",
}
_PREAMBLE_PHRASES = {
    ("please",),
    ("quick", "question"),
    ("one", "question"),
    ("a", "question"),
    ("let's", "see"),
    ("lets", "see"),
    ("hang", "on"),
    ("hold", "on"),
    ("wait",),
}
_POST_WAKE_COMMENTARY_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("and", "it"),
    ("and", "it's"),
    ("and", "its"),
    ("it", "basically"),
    ("it's", "going"),
    ("its", "going"),
    ("it", "is", "going"),
    ("now", "it"),
    ("now", "it's"),
    ("now", "its"),
    ("as", "you", "can", "see"),
)
_CONTINUATION_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("now", "it"),
    ("now", "it's"),
    ("now", "its"),
    ("now", "i"),
    ("now", "i'm"),
    ("now", "im"),
    ("now", "we"),
    ("now", "we're"),
    ("now", "let's"),
    ("now", "lets"),
    ("now", "you", "can"),
    ("and", "now"),
    ("so", "now"),
    ("okay", "now"),
    ("ok", "now"),
    ("and", "it", "is"),
    ("and", "it's"),
    ("and", "its"),
    ("and", "it", "basically"),
    ("and", "it", "will"),
    ("it", "basically"),
    ("it's", "going", "to"),
    ("its", "going", "to"),
    ("it", "is", "going", "to"),
    ("as", "you", "can", "see"),
    ("you", "can", "see", "that"),
    ("you", "can", "see", "here"),
    ("i", "have", "given", "it"),
    ("i", "just", "gave", "it"),
    ("i'm", "continuing", "to"),
    ("i", "am", "continuing", "to"),
    ("let's", "move", "on"),
    ("moving", "on", "to"),
    ("back", "to", "what", "i"),
)
_ACCEPTED = (
    ("hey", "ba"),
    ("hi", "ba"),
    ("high", "ba"),
    ("okay", "ba"),
    ("ok", "ba"),
    ("k", "ba"),
    ("hello", "ba"),
    ("yo", "ba"),
    ("a", "ba"),
    ("eh", "ba"),
    ("ay", "ba"),
    ("hey", "va"),
    ("hey", "pa"),
    ("hey", "via"),
    ("hey", "bia"),
    ("hey", "techsol", "ba"),
    ("techsol", "ba"),
    ("hey", "ba", "assistant"),
    ("ba", "assistant"),
    ("aba",),
    ("heyba",),
    ("kba",),
    ("hiba",),
    ("@ba",),
)
_LOOKALIKES = (
    ("hey", "bay"),
    ("hey", "bob"),
    ("hey", "bae"),
    ("bay", "area"),
    ("the", "ba", "team"),
    ("bae",),
)
_GREETINGS = ("hey", "hi", "ok", "okay", "hello", "yo")


@dataclass(frozen=True)
class WakeMatch:
    question: str
    phrase: str


def match_wake(
    text: str,
    *,
    fuzzy: bool = True,
    threshold: int = 85,
    extra_phrases: list[str] | None = None,
) -> WakeMatch | None:
    raw_tokens = text.split()
    items = _collapse(raw_tokens)
    tokens = [token for token, _end in items]
    if not tokens or tokens == ["ba"]:
        return None
    accepted = list(_ACCEPTED)
    for phrase in extra_phrases or []:
        parts = tuple(part for part in phrase.lower().split() if part)
        if parts and parts not in accepted:
            accepted.append(parts)
    if _find_phrase(tokens, _LOOKALIKES) is not None:
        return None
    exact = _find_phrase(tokens, accepted, prefer_last=True)
    if exact is not None:
        return _match_from(raw_tokens, items, exact)[0]
    if not fuzzy:
        return None
    fuzzy_hit = _fuzzy_phrase(tokens, accepted, threshold)
    if fuzzy_hit is None:
        return None
    return _match_from(raw_tokens, items, fuzzy_hit)[0]


def split_completed_wake_sentence(
    text: str,
    *,
    fuzzy: bool = True,
    threshold: int = 85,
    extra_phrases: list[str] | None = None,
) -> tuple[str, str] | None:
    """If text has a wake phrase whose sentence/clause is complete and followed by
    continued speech, return (head_with_wake, trailing_speech). Otherwise return None.
    """
    raw_tokens = text.split()
    items = _collapse(raw_tokens)
    tokens = [token for token, _end in items]
    if not tokens or tokens == ["ba"]:
        return None
    accepted = list(_ACCEPTED)
    for phrase in extra_phrases or []:
        parts = tuple(part for part in phrase.lower().split() if part)
        if parts and parts not in accepted:
            accepted.append(parts)
    if _find_phrase(tokens, _LOOKALIKES) is not None:
        return None
    found = _find_phrase(tokens, accepted, prefer_last=False)
    if found is None and fuzzy:
        found = _fuzzy_phrase(tokens, accepted, threshold)
    if found is None:
        return None
    _match, cut_raw_index = _match_from(raw_tokens, items, found)
    if cut_raw_index >= len(raw_tokens):
        return None
    head = " ".join(raw_tokens[:cut_raw_index]).strip()
    tail = " ".join(raw_tokens[cut_raw_index:]).strip()
    if not head or not tail:
        return None
    return head, tail


def _find_phrase(
    tokens: list[str],
    phrases: list[tuple[str, ...]] | tuple[tuple[str, ...], ...],
    *,
    prefer_last: bool = False,
):
    matches: list[tuple[int, int, str]] = []
    for start in range(len(tokens)):
        for phrase in phrases:
            end = start + len(phrase)
            if end <= len(tokens) and tuple(tokens[start:end]) == phrase:
                matches.append((start, end, " ".join(phrase)))
                break
    if not matches:
        return None
    if not prefer_last or len(matches) == 1:
        return matches[0]
    for candidate in reversed(matches):
        if candidate[1] < len(tokens):
            return candidate
    return matches[-1]


def _fuzzy_phrase(tokens: list[str], accepted: list[tuple[str, ...]], threshold: int):
    best: tuple[int, int, str, float] | None = None
    for start in range(len(tokens)):
        if start + 1 >= len(tokens):
            continue
        span_tokens = tokens[start : start + 2]
        span = " ".join(span_tokens)
        if _is_lookalike_span(span_tokens):
            continue
        if max(fuzz.ratio(span_tokens[0], greeting) for greeting in _GREETINGS) < threshold:
            continue
        for phrase in accepted:
            if len(phrase) != 2:
                continue
            score = fuzz.ratio(span, " ".join(phrase))
            if score >= threshold and (best is None or score > best[3]):
                best = (start, start + 2, " ".join(phrase), score)
    if best is None:
        return None
    return best[0], best[1], best[2]


def _is_lookalike_span(tokens: list[str]) -> bool:
    return _find_phrase(tokens, _LOOKALIKES) is not None


def _match_from(
    raw_tokens: list[str], items: list[tuple[str, int]], found
) -> tuple[WakeMatch, int]:
    _start, end, phrase = found
    raw_index = items[end - 1][1]
    q_tokens = raw_tokens[raw_index:]
    wake_last_raw = raw_tokens[raw_index - 1] if raw_index > 0 else ""
    kept_count = _truncate_question_len(q_tokens, wake_ended_sentence=_ends_sentence(wake_last_raw))
    cut_raw_index = raw_index + kept_count
    question = _clean_question(" ".join(raw_tokens[raw_index:cut_raw_index]))
    return WakeMatch(question=question, phrase=phrase), cut_raw_index


def _ends_sentence(raw_token: str) -> bool:
    stripped = raw_token.rstrip("\"'”’)]}")
    if not stripped or stripped[-1] not in _SENTENCE_END_CHARS:
        return False
    core = stripped.rstrip(_EDGE).lower().replace(".", "")
    if not core:
        return False
    if stripped[-1] == "." and (len(core) == 1 or core in _ABBREV_WORDS or core.isdigit()):
        return False
    return True


def _starts_with_any(norms: list[str], prefixes: tuple[tuple[str, ...], ...]) -> bool:
    for prefix in prefixes:
        if len(norms) >= len(prefix) and tuple(norms[: len(prefix)]) == prefix:
            return True
    return False


def _truncate_question_len(q_tokens: list[str], *, wake_ended_sentence: bool = False) -> int:
    if not q_tokens:
        return 0
    norms = [
        tok.lower().strip(_EDGE).replace("’", "'")
        for tok in q_tokens
    ]
    if _starts_with_any(norms, _POST_WAKE_COMMENTARY_PREFIXES):
        return 0
    if wake_ended_sentence and _starts_with_any(norms, _CONTINUATION_PREFIXES):
        return 0

    for idx, tok in enumerate(q_tokens):
        if idx >= 3 and _starts_with_any(norms[idx:], _CONTINUATION_PREFIXES):
            return idx
        if _ends_sentence(tok):
            word_count = idx + 1
            prefix_norms = tuple(n for n in norms[:word_count] if n)
            if prefix_norms in _PREAMBLE_PHRASES:
                continue
            if word_count == 1 and norms[0] not in _IMMEDIATE_ONE_WORD:
                if idx + 1 < len(norms) and _starts_with_any(
                    norms[idx + 1 :], _POST_WAKE_COMMENTARY_PREFIXES + _CONTINUATION_PREFIXES
                ):
                    return 1
                continue
            return word_count
    return len(q_tokens)


def _clean_question(question: str) -> str:
    return question.strip().strip(_EDGE).strip()


def _collapse(raw_tokens: list[str]) -> list[tuple[str, int]]:
    norms = [_normalize_token(token) for token in raw_tokens]
    items: list[tuple[str, int]] = []
    index = 0
    while index < len(norms):
        if not norms[index]:
            index += 1
            continue
        nxt = norms[index + 1] if index + 1 < len(norms) else ""
        if (norms[index], nxt) in {("bee", "a"), ("b", "a"), ("be", "a"), ("b", "ay"), ("b", "eh")}:
            items.append(("ba", index + 2))
            index += 2
            continue
        token = "ba" if norms[index] == "bea" else norms[index]
        items.append((token, index + 1))
        index += 1
    return items


def _normalize_token(token: str) -> str:
    text = token.lower().strip(_EDGE).replace(".", "").replace("’", "'")
    if text == "bea":
        return "ba"
    return text
