"""Wake-phrase matching. Lookalikes are rejected before a fuzzy match."""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

_EDGE = ".,!?;:\"'()[]{}“”…—-"
_ACCEPTED = (
    ("hey", "ba"),
    ("hi", "ba"),
    ("okay", "ba"),
    ("ok", "ba"),
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
    exact = _find_phrase(tokens, accepted)
    if exact is not None:
        return _match_from(raw_tokens, items, exact)
    if not fuzzy:
        return None
    fuzzy_hit = _fuzzy_phrase(tokens, accepted, threshold)
    if fuzzy_hit is None:
        return None
    return _match_from(raw_tokens, items, fuzzy_hit)


def _find_phrase(tokens: list[str], phrases: list[tuple[str, ...]] | tuple[tuple[str, ...], ...]):
    for start in range(len(tokens)):
        for phrase in phrases:
            end = start + len(phrase)
            if end <= len(tokens) and tuple(tokens[start:end]) == phrase:
                return start, end, " ".join(phrase)
    return None


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


def _match_from(raw_tokens: list[str], items: list[tuple[str, int]], found) -> WakeMatch:
    _start, end, phrase = found
    raw_index = items[end - 1][1]
    question = _clean_question(" ".join(raw_tokens[raw_index:]))
    return WakeMatch(question=question, phrase=phrase)


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
