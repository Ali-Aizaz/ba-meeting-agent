"""When a car question should also call web search."""

from __future__ import annotations

import re

from ba_agent.knowledge.search import Chunk, KnowledgeIndex
from ba_agent.models import WebResult

_CURRENT = (
    "price",
    "pricing",
    "cost",
    "latest",
    "current",
    "today",
    "msrp",
    "warranty",
    "specs",
    "specification",
    "specifications",
    "release",
    "recall",
    "recalls",
    "incentive",
    "tax credit",
    "compare",
    "comparison",
    "fastest",
    "cheapest",
    "longest",
    "how much",
    "how far",
    "how fast",
    "0-60",
    "top speed",
    "epa",
    "wltp",
    "towing",
)
_BRANDS = (
    "toyota",
    "honda",
    "ford",
    "bmw",
    "tesla",
    "rav4",
    "camry",
    "corolla",
    "prius",
    "highlander",
    "tacoma",
    "tundra",
    "nissan",
    "leaf",
    "ariya",
    "rogue",
    "altima",
    "hyundai",
    "ioniq",
    "tucson",
    "elantra",
    "sonata",
    "kia",
    "sportage",
    "telluride",
    "sorento",
    "ev6",
    "ev9",
    "audi",
    "mercedes",
    "volkswagen",
    "golf",
    "tiguan",
    "jetta",
    "passat",
    "id4",
    "chevy",
    "chevrolet",
    "bolt",
    "silverado",
    "tahoe",
    "equinox",
    "porsche",
    "taycan",
    "macan",
    "cayenne",
    "mazda",
    "cx-5",
    "cx5",
    "cx-90",
    "subaru",
    "outback",
    "forester",
    "crosstrek",
    "volvo",
    "xc90",
    "xc60",
    "xc40",
    "ex90",
    "ex30",
    "lexus",
    "acura",
    "infiniti",
    "genesis",
    "rivian",
    "r1t",
    "r1s",
    "lucid",
    "polestar",
    "byd",
    "jeep",
    "wrangler",
    "cherokee",
    "dodge",
    "ram",
    "gmc",
    "cadillac",
    "buick",
    "lincoln",
    "chrysler",
    "mini",
    "fiat",
    "ferrari",
    "lamborghini",
    "mclaren",
    "bentley",
    "jaguar",
    "peugeot",
    "renault",
    "skoda",
    "suzuki",
    "mitsubishi",
    "mustang",
    "f-150",
    "f150",
    "bronco",
    "explorer",
    "civic",
    "accord",
    "cr-v",
    "crv",
    "cybertruck",
)
_CAR_WORDS = _BRANDS + (
    "car",
    "cars",
    "auto",
    "automotive",
    "hybrid",
    "hybrids",
    "hev",
    "mhev",
    "phev",
    "bev",
    "ev",
    "evs",
    "electric",
    "vehicle",
    "vehicles",
    "engine",
    "battery",
    "batteries",
    "lfp",
    "nmc",
    "motor",
    "motors",
    "inverter",
    "charging",
    "charger",
    "supercharger",
    "suv",
    "suvs",
    "sedan",
    "sedans",
    "crossover",
    "truck",
    "trucks",
    "hatchback",
    "coupe",
    "chassis",
    "unibody",
    "monocoque",
    "suspension",
    "macpherson",
    "drivetrain",
    "awd",
    "fwd",
    "rwd",
    "4wd",
    "transmission",
    "gearbox",
    "turbo",
    "turbocharger",
    "torque",
    "horsepower",
    "hp",
    "mpg",
    "kwh",
    "brake",
    "brakes",
    "braking",
    "regenerative",
    "airbag",
    "airbags",
    "crumple",
    "adas",
    "abs",
    "aeb",
    "ncap",
    "nhtsa",
    "iihs",
    "safety",
    "wheel",
    "wheels",
    "tire",
    "tires",
    "tyre",
    "tyres",
    "range",
    "fuel",
    "diesel",
    "gasoline",
    "petrol",
)

_WEB_INTENT = re.compile(
    r"\b(?:search\s+(?:the\s+)?(?:web|internet|online)|search\s+online|search\s+for|look\s+up|on\s+the\s+(?:internet|web)|online\s+search)\b",
    re.I,
)

_MEETING_PATTERNS = (
    re.compile(r"\bwhat\s+did\s+we\b", re.I),
    re.compile(r"\bdid\s+we\s+(?:decide|agree|say|discuss|pick|choose|talk)\b", re.I),
    re.compile(
        r"\b(?:our|the|all|any|open|pending|current|stored|saved|existing)\s+"
        r"(?:decision|decisions|task|tasks|action\s+items?|meeting|meetings|summary|summaries|notes)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:what|which|show|list|get|find|check|tell\s+me|give\s+me|are\s+there)\b.*"
        r"\b(?:tasks?|action\s+items?|meeting\s+notes?|summaries|to-?dos?)\b",
        re.I,
    ),
    re.compile(
        r"\btasks?\s+(?:for|assigned\s+to|owned\s+by|in\s+notion|in\s+the\s+database|from|do\s+we\s+have)\b",
        re.I,
    ),
    re.compile(r"\b[a-z]+'s\s+(?:tasks?|action\s+items?)\b", re.I),
    re.compile(
        r"\bwho\s+(?:said|owns|is\s+in\s+the\s+meeting|is\s+assigned|is\s+working\s+on|is\s+responsible)\b",
        re.I,
    ),
    re.compile(r"\bwhat\s+did\s+[a-z]+\s+(?:say|mention|ask|propose|agree)\b", re.I),
    re.compile(
        r"\b(?:in|from|during)\s+(?:this|the\s+last|the\s+previous|our\s+last|earlier|past)\s+meetings?\b",
        re.I,
    ),
    re.compile(r"\b(?:in|from)\s+(?:notion|the\s+database)\b", re.I),
    re.compile(r"\b(?:summarize|summarise|recap)\b", re.I),
    re.compile(r"\b(?:add|create|assign|log|track)\b.*\btask\b", re.I),
    re.compile(r"\bassign\s+[a-z]+\s+to\b", re.I),
    re.compile(
        r"\b(?:please\s+)?(?:finish|complete|send|prepare|review|update|draft|write|book|schedule|fix|check)\b.+\bby\s+\w+",
        re.I,
    ),
    re.compile(
        r"\b(?:powertrain[\s-]+kickoff|chassis[\s-]+and[\s-]+safety|competitor[\s-]+benchmark)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:when\s+is\s+.+\s+due|what\s+is\s+the\s+(?:due\s+date|deadline|status)\s+(?:of|for))\b",
        re.I,
    ),
)


def is_meeting_question(query: str) -> bool:
    return any(pattern.search(query) is not None for pattern in _MEETING_PATTERNS)


def needs_current_fact(query: str) -> bool:
    if is_meeting_question(query):
        return False
    if _WEB_INTENT.search(query) is not None:
        return True
    text = query.lower()
    if any(marker in text for marker in _CURRENT):
        return True
    if re.search(r"\b20\d{2}\b", text):
        return True
    if re.search(r"\bmodel\s+[3ysx]\b", text):
        return True
    return any(re.search(rf"\b{re.escape(brand)}\b", text) for brand in _BRANDS)


def is_car_question(query: str, hits: list[Chunk]) -> bool:
    if is_meeting_question(query):
        return False
    if hits:
        return True
    if _WEB_INTENT.search(query) is not None:
        return True
    text = query.lower()
    if re.search(r"\bmodel\s+[3ysx]\b", text):
        return True
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
