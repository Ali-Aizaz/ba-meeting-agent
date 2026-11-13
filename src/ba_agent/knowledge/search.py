"""BM25 + IDF keyword search over the Markdown knowledge base."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

_STOP = {
    "a",
    "an",
    "the",
    "is",
    "are",
    "was",
    "were",
    "what",
    "whats",
    "of",
    "to",
    "and",
    "or",
    "in",
    "on",
    "for",
    "how",
    "does",
    "do",
    "i",
    "you",
    "me",
    "we",
    "it",
    "its",
    "be",
    "about",
    "please",
    "can",
    "could",
    "tell",
    "explain",
    "describe",
    "define",
    "difference",
    "between",
    "vs",
    "versus",
    "work",
    "works",
    "working",
    "mean",
    "means",
    "with",
    "from",
    "by",
    "at",
    "this",
    "that",
    "these",
    "those",
    "why",
    "when",
    "where",
    "which",
    "who",
    "has",
    "have",
    "had",
    "did",
    "get",
    "gets",
    "got",
    "give",
    "talk",
    "say",
    "stand",
    "stands",
    "modern",
    "called",
    "used",
    "using",
    "use",
    "type",
    "types",
    "kind",
    "kinds",
}

_STEM_MAP = {
    "hybrids": "hybrid",
    "evs": "ev",
    "batteries": "battery",
    "tyres": "tire",
    "tires": "tire",
    "tyre": "tire",
    "bodies": "body",
    "braking": "brake",
    "brakes": "brake",
    "charging": "charge",
    "charger": "charge",
    "chargers": "charge",
    "charged": "charge",
    "charges": "charge",
    "keeping": "keep",
    "assisting": "assist",
    "assisted": "assist",
    "controlling": "control",
    "controlled": "control",
    "plugin": "plug",
}

_NO_STRIP_S = {
    "this",
    "does",
    "bias",
    "plus",
    "less",
    "loss",
    "mass",
    "pass",
    "gas",
    "bus",
    "adas",
    "abs",
    "bms",
    "iihs",
}

_ACRONYMS = {
    "aeb",
    "lka",
    "acc",
    "bsm",
    "esc",
    "abs",
    "ncap",
    "iihs",
    "nhtsa",
    "adas",
    "lfp",
    "nmc",
    "bms",
    "phev",
    "mhev",
    "hev",
    "bev",
    "wltp",
    "epa",
    "ecvt",
    "cvt",
    "awd",
    "fwd",
    "rwd",
    "nvh",
}

_SYNONYMS: dict[str, tuple[str, ...]] = {
    "ev": ("electric", "vehicle"),
    "bev": ("battery", "electric"),
    "phev": ("plug", "hybrid"),
    "mhev": ("mild", "hybrid"),
    "hev": ("hybrid", "electric"),
    "aeb": ("automatic", "emergency", "brake"),
    "lka": ("lane", "keep", "assist"),
    "esc": ("electronic", "stability", "control"),
    "lfp": ("lithium", "iron", "phosphate"),
    "nmc": ("nickel", "manganese", "cobalt"),
}


@dataclass
class Chunk:
    file: str
    heading: str
    text: str
    score: float

    @property
    def source(self) -> str:
        if self.heading:
            return f"{self.file}#{self.heading}"
        return self.file


@dataclass
class _IndexedChunk:
    chunk: Chunk
    tf: Counter[str]
    length: int
    token_set: set[str]
    heading_tokens: set[str]
    file_tokens: set[str]
    bigrams: set[tuple[str, str]]


class KnowledgeIndex:
    def __init__(self, root: Path, min_score: float = 0.5) -> None:
        self.root = root
        self.min_score = min_score
        self._chunks = _load(root)
        self._indexed: list[_IndexedChunk] = []
        self._idf: dict[str, float] = {}
        self._default_idf: float = 1.5
        self._avgdl: float = 1.0
        self._build_index()

    def _build_index(self) -> None:
        if not self._chunks:
            return
        df: Counter[str] = Counter()
        total_len = 0
        for chunk in self._chunks:
            file_toks = _useful_tokens(chunk.file.removesuffix(".md").replace("-", " "))
            heading_toks = _useful_tokens(chunk.heading) if chunk.heading else []
            body_toks = _useful_tokens(chunk.text)
            all_toks = body_toks + file_toks
            tf = Counter(all_toks)
            dl = max(1, len(all_toks))
            total_len += dl
            bigrams = set(zip(all_toks, all_toks[1:]))
            self._indexed.append(
                _IndexedChunk(
                    chunk=chunk,
                    tf=tf,
                    length=dl,
                    token_set=set(all_toks),
                    heading_tokens=set(heading_toks),
                    file_tokens=set(file_toks),
                    bigrams=bigrams,
                )
            )
            for tok in tf:
                df[tok] += 1
        n_docs = len(self._indexed)
        self._avgdl = total_len / max(1, n_docs)
        self._default_idf = math.log(1.0 + (n_docs + 0.5) / 0.5)
        for tok, freq in df.items():
            self._idf[tok] = math.log(1.0 + (n_docs - freq + 0.5) / (freq + 0.5))

    def search(self, query: str, limit: int = 3) -> list[Chunk]:
        q_tokens = _useful_tokens(query)
        if not q_tokens:
            return []
        q_set = set(q_tokens)
        q_bigrams = set(zip(q_tokens, q_tokens[1:]))
        expanded_set = set(q_set)
        for tok in q_set:
            if tok in _SYNONYMS:
                expanded_set.update(_SYNONYMS[tok])
        for acronym, phrase_toks in _SYNONYMS.items():
            if set(phrase_toks) <= q_set:
                expanded_set.add(acronym)

        total_q_idf = sum(self._idf.get(t, self._default_idf) for t in q_set) or 1.0
        ranked: list[Chunk] = []

        for entry in self._indexed:
            direct_overlap = q_set & entry.token_set
            syn_overlap = expanded_set & entry.token_set
            if not direct_overlap and not syn_overlap:
                continue
            bm25 = _bm25_score(
                q_set,
                syn_overlap - q_set,
                entry,
                self._idf,
                self._avgdl,
                default_idf=self._default_idf,
            )

            idf_cov = (
                sum(self._idf.get(t, self._default_idf) for t in direct_overlap) / total_q_idf
            )
            raw_cov = len(direct_overlap) / len(q_set)
            syn_cov = len(syn_overlap) / len(expanded_set)
            coverage = max(raw_cov * 0.45 + idf_cov * 0.55, 0.55 * syn_cov)
            bm25_component = 0.25 * (bm25 / (bm25 + 1.8)) * max(idf_cov, syn_cov)

            heading_hits = len(expanded_set & entry.heading_tokens)
            heading_boost = 0.10 if heading_hits > 0 else 0.0
            if entry.heading_tokens and entry.heading_tokens <= expanded_set:
                heading_boost += 0.06

            bigram_boost = 0.10 if (q_bigrams and (q_bigrams & entry.bigrams)) else 0.0
            acronym_hits = (direct_overlap | (syn_overlap & _ACRONYMS)) & _ACRONYMS
            score = coverage + bm25_component + heading_boost + bigram_boost
            if acronym_hits:
                score = max(score, 0.68 + 0.06 * min(len(acronym_hits), 3) + bm25_component * 0.5)
            name_hit = 1 if (expanded_set & entry.file_tokens) else 0
            score += name_hit * 0.02
            if score > 0.94:
                excess = score - 0.94
                score = 0.94 + 0.0599 * (excess / (excess + 0.25))

            if score < self.min_score:
                continue
            ranked.append(
                Chunk(
                    file=entry.chunk.file,
                    heading=entry.chunk.heading,
                    text=entry.chunk.text,
                    score=round(min(1.0, score), 4),
                )
            )
        ranked.sort(key=lambda item: (-item.score, item.file))
        return ranked[:limit]

    def overview_chunks(self, query: str = "", limit: int = 5) -> list[Chunk]:
        if not self._chunks:
            return []
        scrubbed = re.sub(
            r"\b(?:current|our|my|this|notion|database|db|knowledge|base|stored|saved|record|records)\b",
            " ",
            query,
            flags=re.I,
        )
        chosen: list[Chunk] = list(self.search(scrubbed, limit=limit))
        seen = {item.source for item in chosen}
        preferred_files = (
            "examples.md",
            "hybrid.md",
            "ev.md",
            "safety.md",
            "body-and-chassis.md",
        )
        ordered = sorted(
            self._chunks,
            key=lambda ch: (
                preferred_files.index(ch.file)
                if ch.file in preferred_files
                else len(preferred_files),
                ch.file,
            ),
        )
        seen_files = {item.file for item in chosen}
        for pass_idx in (0, 1):
            for chunk in ordered:
                if len(chosen) >= limit:
                    return chosen[:limit]
                if chunk.source in seen:
                    continue
                if pass_idx == 0 and chunk.file in seen_files:
                    continue
                chosen.append(
                    Chunk(
                        file=chunk.file,
                        heading=chunk.heading,
                        text=chunk.text,
                        score=max(self.min_score, 0.55),
                    )
                )
                seen.add(chunk.source)
                seen_files.add(chunk.file)
        return chosen[:limit]


def _bm25_score(
    q_set: set[str],
    syn_only: set[str],
    entry: _IndexedChunk,
    idf_map: dict[str, float],
    avgdl: float,
    *,
    default_idf: float = 1.5,
    k1: float = 1.5,
    b: float = 0.75,
) -> float:
    bm25 = 0.0
    for tok in q_set | syn_only:
        tf = entry.tf.get(tok, 0)
        if tf <= 0:
            continue
        weight = 1.0 if tok in q_set else 0.65
        idf = idf_map.get(tok, default_idf)
        denom = tf + k1 * (1.0 - b + b * (entry.length / max(1.0, avgdl)))
        bm25 += weight * idf * ((tf * (k1 + 1.0)) / max(1e-9, denom))
    return bm25


def _load(root: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    if not root.is_dir():
        return chunks
    for path in sorted(root.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for heading, body in _split(text):
            chunks.append(Chunk(file=path.name, heading=heading, text=body, score=0))
    return chunks


def _split(text: str) -> list[tuple[str, str]]:
    parts = re.split(r"\n(?=#{1,3} )", text.strip())
    chunks: list[tuple[str, str]] = []
    for part in parts:
        lines = part.strip().splitlines()
        if not lines:
            continue
        if lines[0].startswith("#"):
            heading = lines[0].lstrip("#").strip()
            body = "\n".join(lines[1:]).strip()
        else:
            heading = ""
            body = "\n".join(lines).strip()
        searchable = f"{heading}\n{body}".strip()
        if searchable:
            chunks.append((heading, searchable))
    return chunks


def _stem(word: str) -> str:
    if word in _STEM_MAP:
        return _STEM_MAP[word]
    if (
        len(word) >= 4
        and word not in _NO_STRIP_S
        and word.endswith("s")
        and not word.endswith(("ss", "us", "is", "as"))
    ):
        return word[:-1]
    return word


def _useful_tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [_stem(word) for word in words if word not in _STOP and len(word) > 1]


def _tokens(text: str) -> list[str]:
    useful = _useful_tokens(text)
    if useful:
        return useful
    return re.findall(r"[a-z0-9]+", text.lower())


def _score(query: set[str], chunk: set[str], heading: set[str] | None = None) -> float:
    if not query:
        return 0.0
    overlap = query & chunk
    if not overlap:
        return 0.0
    ratio = len(overlap) / len(query)
    if heading and heading <= query:
        ratio = max(ratio, 0.75 + 0.05 * min(len(heading), 4))
    if len(overlap) >= 2 and len(query) >= 4:
        ratio = max(ratio, min(0.85, len(overlap) / 3))
    return ratio
