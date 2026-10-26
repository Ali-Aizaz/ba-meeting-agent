"""When a car question should also call web search."""

from __future__ import annotations

import re

from ba_agent.knowledge.search import Chunk, KnowledgeIndex
from ba_agent.models import WebResult

_CURRENT = ("price", "cost", "latest", "current", "today", "msrp", "warranty")
_BRANDS = (
    "toyota",
    "honda",
    "ford",
    "bmw",
    "tesla",
    "rav4",
    "camry",
    "nissan",
    "hyundai",
    "kia",
    "audi",
    "mercedes",
    "volkswagen",
    "chevy",
    "chevrolet",
    "leaf",
)
_CAR_WORDS = _BRANDS + (
    "car",
    "cars",
    "hybrid",
    "hybrids",
    "ev",
    "evs",
    "electric",
    "vehicle",
    "vehicles",
    "engine",
    "battery",
    "motor",
    "charging",
    "charger",
    "suv",
    "sedan",
    "chassis",
    "drivetrain",
    "torque",
    "horsepower",
    "mpg",
    "kwh",
    "brake",
    "brakes",
    "airbag",
    "ncap",
    "safety",
    "wheel",
    "wheels",
    "tire",
    "tires",
    "range",
    "fuel",
)


def needs_current_fact(query: str) -> bool:
    text = query.lower()
    if any(marker in text for marker in _CURRENT):
        return True
    if re.search(r"\b20\d{2}\b", text):
        return True
    return any(re.search(rf"\b{re.escape(brand)}\b", text) for brand in _BRANDS)


def is_car_question(query: str, hits: list[Chunk]) -> bool:
    if hits or needs_current_fact(query):
        return True
    text = query.lower()
    return any(re.search(rf"\b{re.escape(word)}\b", text) for word in _CAR_WORDS)


def should_search_web(query: str, hits: list[Chunk], min_score: float) -> bool:
    best = hits[0].score if hits else 0.0
    if best < min_score:
        return True
    return needs_current_fact(query)


async def gather_car_context(
    question: str,
    knowledge: KnowledgeIndex,
    search_cars,
    min_score: float,
):
    """Search local notes first. Web search receives the question string only."""
    hits = knowledge.search(question)
    web: WebResult | None = None
    if should_search_web(question, hits, min_score):
        web = await search_cars(question)
    return hits, web
