"""A car fact in chat needs a source, or an explicit not-sure."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

NOT_SURE = "I'm not sure — I don't have a source for that."

_CITATION = re.compile(r"\(src:\s*(?:[^()]|\([^()]*\))+\)", re.I)
_SRC_BLOCK = re.compile(r"\(src:\s*((?:[^()]|\([^()]*\))+)\)", re.I)
_MD_FILE = re.compile(r"[a-z0-9_.-]+\.md", re.I)
_URL = re.compile(r"https?://(?:[^\s()<>\]\"']|\([^\s()<>\]\"']*\))+", re.I)
_MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")
_COMMAS = re.compile(r"\b(\d{1,3}(?:,\d{3})+(?:\.\d+)?)\b")
_UNITS = re.compile(
    r"(?:\b(\d[\d,]*(?:\.\d+)?)\s*"
    r"(?:kwh|kw|km|mi|miles|kg|lbs|hp|nm|lb-ft|mph|mpg|usd|dollars|"
    r"volts?|min|minutes|sec|seconds)\b|"
    r"\b(\d[\d,]*(?:\.\d+)?)v\b|"
    r"(\d+(?:\.\d+)?)%)",
    re.I,
)
_SPACED_UPPER_VOLTS = re.compile(r"\b(\d{2,}[\d,]*(?:\.\d+)?)\s+V\b")
_TRACKING_PARAMS = {"fbclid", "gclid", "msclkid", "ref"}


def _norm_url(url: str) -> str:
    return url.strip().rstrip(".,;:!?\"'").rstrip("/").lower()


def _canon_url(url: str) -> str:
    cleaned = url.strip().rstrip(".,;:!?\"'")
    try:
        parts = urlsplit(cleaned)
    except ValueError:
        return _norm_url(url)
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    kept_query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    query_str = urlencode(sorted(kept_query))
    base = f"{scheme}://{netloc}{path}"
    return f"{base}?{query_str}".lower() if query_str else base.lower()


def _strip_citations_and_urls(text: str) -> str:
    without_src = _SRC_BLOCK.sub(" ", text)
    return _URL.sub(" ", without_src)


def has_citation(text: str) -> bool:
    return _CITATION.search(text) is not None or _URL.search(text) is not None


def has_precise_number(text: str) -> bool:
    prose = _strip_citations_and_urls(text)
    return any(
        pattern.search(prose) for pattern in (_MONEY, _COMMAS, _UNITS, _SPACED_UPPER_VOLTS)
    )


def is_unsure(text: str) -> bool:
    return "not sure" in text.lower()


def verify_reply_sources(
    text: str,
    allowed_files: Iterable[str],
    allowed_urls: Iterable[str],
) -> bool:
    """Verify every cited .md filename or https:// URL came from the retrieved context."""
    if is_unsure(text) and not has_precise_number(text):
        return True
    allowed_file_set = {Path(str(f)).name.lower() for f in allowed_files if str(f).strip()}
    raw_allowed_urls = [str(u) for u in allowed_urls if str(u).strip()]
    allowed_url_set = {_norm_url(u) for u in raw_allowed_urls}
    allowed_canon_set = {_canon_url(u) for u in raw_allowed_urls}
    src_blocks = _SRC_BLOCK.findall(text)
    urls = _URL.findall(text)
    if not src_blocks and not urls:
        return False
    for block in src_blocks:
        cited_files = _MD_FILE.findall(block)
        cited_urls = _URL.findall(block)
        if not cited_files and not cited_urls:
            return False
        if any(name.lower() not in allowed_file_set for name in cited_files):
            return False
    for raw_url in urls:
        if (
            _norm_url(raw_url) not in allowed_url_set
            and _canon_url(raw_url) not in allowed_canon_set
        ):
            return False
    return True


def numbers_grounded_in_context(reply_text: str, context_text: str) -> bool:
    """Verify every precise number/price/unit value in reply_text appears in context_text."""
    prose = _strip_citations_and_urls(reply_text)
    if not has_precise_number(prose):
        return True
    nums: list[str] = []
    for m in _MONEY.finditer(prose):
        nums.append(m.group(1).replace(",", ""))
    for m in _COMMAS.finditer(prose):
        nums.append(m.group(1).replace(",", ""))
    for m in _UNITS.finditer(prose):
        raw = m.group(1) or m.group(2) or m.group(3)
        if raw:
            nums.append(raw.replace(",", ""))
    for m in _SPACED_UPPER_VOLTS.finditer(prose):
        nums.append(m.group(1).replace(",", ""))
    if not nums:
        return True
    norm_ctx = re.sub(r"(?<=\d),(?=\d)", "", context_text or "")
    for num in dict.fromkeys(nums):
        candidates = [num]
        trimmed = re.sub(r"\.0+$", "", num)
        if trimmed != num:
            candidates.append(trimmed)
        if not any(
            re.search(rf"(?<!\d){re.escape(cand)}(?!\d)", norm_ctx) is not None
            for cand in candidates
        ):
            return False
    return True


def reply_status(text: str) -> str:
    """Return allow or regenerate."""
    if is_unsure(text) and not has_precise_number(text):
        return "allow"
    if has_citation(text):
        return "allow"
    return "regenerate"


def _candidate_valid(
    text: str,
    *,
    allowed_files: Iterable[str] | None,
    allowed_urls: Iterable[str] | None,
    context_text: str | None,
) -> bool:
    if reply_status(text) != "allow":
        return False
    if is_unsure(text) and not has_precise_number(text):
        return True
    if allowed_files is not None or allowed_urls is not None:
        if not verify_reply_sources(text, allowed_files or (), allowed_urls or ()):
            return False
    if context_text is not None and not numbers_grounded_in_context(text, context_text):
        return False
    return True


def finalize_reply(
    first: str,
    second: str | None = None,
    *,
    allowed_files: Iterable[str] | None = None,
    allowed_urls: Iterable[str] | None = None,
    context_text: str | None = None,
) -> str:
    if _candidate_valid(
        first,
        allowed_files=allowed_files,
        allowed_urls=allowed_urls,
        context_text=context_text,
    ):
        return first
    if second is not None and _candidate_valid(
        second,
        allowed_files=allowed_files,
        allowed_urls=allowed_urls,
        context_text=context_text,
    ):
        return second
    return NOT_SURE
