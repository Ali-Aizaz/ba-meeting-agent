"""When a car question should also call web search."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Literal

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
    "best",
    "buy",
    "buying",
    "recommend",
    "recommendation",
    "recommendations",
    "fastest",
    "cheapest",
    "longest",
    "how much",
    "how far",
    "how fast",
    "0-60",
    "top speed",
    "towing",
)
_CYCLE_CURRENT = ("epa", "wltp")
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
    re.compile(
        r"\bdid\s+we\s+(?:decide|agree|say|discuss|pick|choose|talk|assign|create|have)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:our|the|all|any|open|pending|current|stored|saved|existing|previous|past|last|earlier)\s+"
        r"(?:decision|decisions|task|tasks|action\s+items?|meeting|meetings|summary|summaries|notes)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:what|which|show|list|get|find|check|tell\s+me|give\s+me|are\s+there|do\s+we\s+have|does\s+\w+\s+have)\b.*"
        r"\b(?:tasks?|action\s+items?|meeting\s+notes?|meetings?|summaries|to-?dos?|decisions?)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:tasks?|action\s+items?|to-?dos?)\s+"
        r"(?:for|assigned\s+to|owned\s+by|in\s+notion|in\s+the\s+database|in\s+the\s+tasks?\s+db|from|do\s+we\s+have|are\s+there|we\s+have|have\s+been)\b",
        re.I,
    ),
    re.compile(r"\b(?:tasks?\s+db|tasks?\s+database|notion\s+tasks?|notion\s+notes?)\b", re.I),
    re.compile(r"\b[a-z]+'s\s+(?:tasks?|action\s+items?|to-?dos?|assignments?)\b", re.I),
    re.compile(
        r"\bwho\s+(?:said|owns|is\s+in\s+the\s+meeting|is\s+assigned|is\s+working\s+on|is\s+responsible|is\s+handling|is\s+doing|has\s+the\s+task)\b",
        re.I,
    ),
    re.compile(
        r"\bwhat\s+(?:is|are)\s+[a-z]+\s+(?:working\s+on|assigned\s+to|doing|responsible\s+for)\b",
        re.I,
    ),
    re.compile(
        r"\bwhat\s+did\s+[a-z]+\s+(?:say|mention|ask|propose|agree|do|finish|complete)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:did|has|have|is|was|were)\s+[a-z]+\s+"
        r"(?:finish|finished|complete|completed|send|sent|prepare|prepared|review|reviewed|"
        r"update|updated|schedule|scheduled|request|requested|validate|validated|"
        r"finalize|finalized|working|assigned|benchmark|benchmarked)\b",
        re.I,
    ),
    re.compile(
        r"\bwhat\s+(?:[\w-]+\s+){0,3}(?:did|have|do)\s+we\s+"
        r"(?:set|decide|agree|say|discuss|pick|choose|target|lock|plan|budget)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:budget|shortlist|procurement|tier-1|supplier|deliverable|milestone|"
        r"blocker|timeline|kickoff|benchmark\s+sync|action\s+item|lab\s+slot|slide\s+deck)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:in|from|during|about|check)\s+(?:this|the\s+last|the\s+previous|our\s+last|our\s+previous|earlier|past|previous|last)\s+meetings?\b",
        re.I,
    ),
    re.compile(r"\b(?:previous|past|last|earlier)\s+meetings?(?:'s)?\b", re.I),
    re.compile(
        r"\b(?:in|from)\s+(?:notion|(?:the|our|my|this)\s+(?:current\s+)?"
        r"(?:car\s+|automotive\s+|meeting\s+|tasks?\s+)?(?:database|db|knowledge\s+base))\b",
        re.I,
    ),
    re.compile(
        r"\b(?:our|my|current|notion)\s+"
        r"(?:car\s+|automotive\s+|meeting\s+|tasks?\s+)?(?:database|db|knowledge\s+base)\b",
        re.I,
    ),
    re.compile(r"\bthe\s+knowledge\s+base\b", re.I),
    re.compile(r"\b(?:summarize|summarise|recap)\b", re.I),
    re.compile(r"\b(?:add|create|assign|log|track)\b.*\btasks?\b", re.I),
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
    re.compile(
        r"\b(?:what\s+(?:happened|was\s+said|was\s+discussed|are\s+we\s+discussing)|"
        r"who\s+is\s+(?:here|attending|present)|participants?|attendees?|agenda)\b",
        re.I,
    ),
)


_GENERIC_PARTICIPANTS = {
    "unknown",
    "speaker",
    "you",
    "bot",
    "techsol",
    "guest",
    "host",
    "moderator",
}
_HYBRID_COMPARE = re.compile(r"\b(?:compare|comparison|versus|vs\.?)\b", re.I)
_HYBRID_MEETING_CUE = re.compile(
    r"\b(?:we\s+discussed|we\s+set|our\s+budget|the\s+budget|our\s+target|"
    r"we\s+agreed|we\s+chose|our\s+shortlist)\b",
    re.I,
)


_CURRENT_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(m) for m in _CURRENT) + r")(?:\b|(?=\s|$))",
    re.I,
)
_ELECTRICAL_CURRENT_RE = re.compile(
    r"\b(?:electric(?:al)?|alternating|direct|ac|dc|lower|higher|high|low|"
    r"charging|resistive|eddy|peak|nominal)\s+current\b",
    re.I,
)
_MODEL_YEAR_RE = re.compile(
    r"\b20(?:1[89]|2\d|3\d)\b(?!\s*(?:kg|lbs?|rpm|km|mi|miles|wh|kwh|kw|v|volts?|nm|hp|mm)\b)",
    re.I,
)
_SPECIAL_MODELS_RE = re.compile(
    r"\b(?:model\s+[3ysx]|id\.[347]|mach-e|e-tron|bz4x)\b",
    re.I,
)
_NON_AUTO_CARD_CONTEXT = re.compile(
    r"\b(?:credit|debit|gift|sd|sim|microsd|id|identity|business|playing|poker|tarot|"
    r"greeting|post|boarding|graphics|video|sound|network|wifi|memory|key|smart|loyalty|"
    r"membership|library|report|score|yellow|red|green|rate|time|punch|flash|index|bingo|"
    r"baseball|trading|trello|jira|kanban|notion\s+cards?)\b",
    re.I,
)
_FOOD_BEEF_CONTEXT = re.compile(
    r"\b(?:recipe|cook|cooking|roast|steak|stew|burger|meat|dinner|lunch|eat|food|"
    r"grill|braise|jerky|broth|taco|noodle|soup|salad|sauce|dish)\b",
    re.I,
)
_AUTO_BEEF_CUE = re.compile(
    r"\b(?:which\s+beef|best\s+beef|buy\s+a?\s*beef|beef\s+should\s+i\s+buy|"
    r"beef\s+vs|vs\.?\s+beef|phev|hev|hybrid|electric|battery|range|charging|"
    r"car|cars|vehicle|vehicles|suv)\b",
    re.I,
)
_DB_OVERVIEW_CUE = re.compile(r"\b(?:database|db|knowledge\s+base)\b", re.I)


def normalize_asr_query(query: str) -> str:
    """Normalize common automotive speech-to-text homophones ('card' -> 'car', 'beef' -> 'BEV')."""
    text = query
    if re.search(r"\bcards?\b", text, re.I) and _NON_AUTO_CARD_CONTEXT.search(text) is None:
        text = re.sub(r"\bcards\b", "cars", text, flags=re.I)
        text = re.sub(r"\bcard\b", "car", text, flags=re.I)
    if (
        re.search(r"\bbeef\b", text, re.I)
        and _FOOD_BEEF_CONTEXT.search(text) is None
        and _AUTO_BEEF_CUE.search(text) is not None
    ):
        text = re.sub(r"\bbeef\b", "BEV", text, flags=re.I)
    return text


def is_database_overview_query(query: str) -> bool:
    return _DB_OVERVIEW_CUE.search(query) is not None and is_meeting_question(query)


def _has_model_or_brand(text: str) -> bool:
    if _MODEL_YEAR_RE.search(text):
        return True
    if _SPECIAL_MODELS_RE.search(text):
        return True
    return any(re.search(rf"\b{re.escape(brand)}\b", text) for brand in _BRANDS)


def _mentions_participant(query: str, participants: Iterable[str] | None) -> bool:
    if not participants:
        return False
    q_low = query.lower()
    car_vocab = set(_CAR_WORDS) | {"car", "cars", "hybrid", "electric"}
    for name in participants:
        for token in re.findall(r"[a-z]{3,}", str(name).lower()):
            if token in _GENERIC_PARTICIPANTS or token in car_vocab:
                continue
            if re.search(rf"\b{re.escape(token)}\b", q_low):
                return True
    return False


def is_meeting_question(query: str) -> bool:
    return any(pattern.search(query) is not None for pattern in _MEETING_PATTERNS)


def has_car_topic(query: str) -> bool:
    text = query.lower()
    if _SPECIAL_MODELS_RE.search(text):
        return True
    return any(re.search(rf"\b{re.escape(word)}\b", text) for word in _CAR_WORDS)


def needs_current_fact(query: str) -> bool:
    if is_meeting_question(query):
        return False
    if _WEB_INTENT.search(query) is not None:
        return True
    text = query.lower()
    scrubbed = _ELECTRICAL_CURRENT_RE.sub(" ", text)
    if _CURRENT_RE.search(scrubbed) is not None:
        return True
    if _has_model_or_brand(text):
        return True
    return False


def classify_query_intent(
    query: str,
    hits: list[Chunk],
    participants: Iterable[str] | None = None,
) -> Literal["car", "meeting", "hybrid"]:
    """Classify a query as pure 'car', 'meeting', or 'hybrid' (car + meeting context)."""
    meeting = is_meeting_question(query) or _mentions_participant(query, participants)
    car_topic = bool(hits) or has_car_topic(query)
    if car_topic and (
        (_HYBRID_MEETING_CUE.search(query) is not None and (
            _HYBRID_COMPARE.search(query) is not None
            or _WEB_INTENT.search(query) is not None
            or needs_current_fact(query)
        ))
        or (
            meeting
            and (_HYBRID_COMPARE.search(query) is not None or _WEB_INTENT.search(query) is not None)
        )
    ):
        return "hybrid"
    if meeting:
        return "meeting"
    if car_topic:
        return "car"
    return "meeting"


def is_car_question(query: str, hits: list[Chunk]) -> bool:
    if is_meeting_question(query):
        return False
    if hits:
        return True
    return has_car_topic(query)


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
    if is_car_question(question, hits) and should_search_web(question, hits, min_score):
        web = await search_cars(question)
    return hits, web
