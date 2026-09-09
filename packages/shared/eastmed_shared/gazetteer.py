from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from eastmed_schema.enums import Corridor, EventType

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "gazetteer.json"

_EVENT_TYPE_TERMS: dict[EventType, tuple[str, ...]] = {
    EventType.SECURITY_INCIDENT: (
        "attack",
        "explosion",
        "drone",
        "missile",
        "piracy",
        "seized",
        "security incident",
        "επίθεση",
        "έκρηξη",
    ),
    EventType.PORT_DISRUPTION: (
        "port closed",
        "port closure",
        "terminal closed",
        "berth unavailable",
        "congestion",
        "operations suspended",
        "λιμάνι κλειστό",
        "αναστολή λειτουργίας",
    ),
    EventType.LABOR_ACTION: (
        "strike",
        "industrial action",
        "work stoppage",
        "union action",
        "απεργία",
    ),
    EventType.REGULATORY_SANCTIONS: (
        "sanction",
        "embargo",
        "export ban",
        "designation",
        "κύρωση",
        "εμπάργκο",
    ),
    EventType.WEATHER_HAZARD: (
        "gale",
        "storm",
        "cyclone",
        "heavy seas",
        "strong winds",
        "καταιγίδα",
        "θυελλώδεις άνεμοι",
    ),
    EventType.INFRASTRUCTURE: (
        "pipeline",
        "cable damage",
        "infrastructure failure",
        "power outage",
        "fire at terminal",
        "αγωγός",
        "διακοπή ρεύματος",
    ),
    EventType.INSURANCE_MARKET: (
        "war risk premium",
        "insurance premium",
        "underwriter",
        "additional premium",
        "ασφάλιστρο",
    ),
    EventType.NAVIGATION_WARNING: (
        "navtex",
        "navigation warning",
        "notice to mariners",
        "restricted area",
        "vessel traffic suspended",
        "αγγελία προς ναυτιλλομένους",
    ),
}


@dataclass(frozen=True)
class GazetteerSignals:
    corridors: tuple[Corridor, ...]
    ports: tuple[dict[str, str], ...]
    event_types: tuple[EventType, ...]


def _normalized(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _contains(text: str, term: str) -> bool:
    normalized_term = _normalized(term)
    return bool(re.search(rf"(?<!\w){re.escape(normalized_term)}(?!\w)", text))


@lru_cache(maxsize=1)
def load_gazetteer() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(_CONFIG_PATH.read_text(encoding="utf-8")))


def detect_gazetteer_signals(title: str | None, extracted_text: str) -> GazetteerSignals:
    text = _normalized(f"{title or ''}\n{extracted_text}")
    config = load_gazetteer()
    corridor_ids: set[Corridor] = set()
    ports: list[dict[str, str]] = []

    for item in config["corridors"]:
        if any(_contains(text, alias) for alias in item["aliases"]):
            corridor_ids.add(Corridor(item["id"]))

    for item in config["ports"]:
        aliases = [item["name"], item["unlocode"], *item["aliases"]]
        matched_alias = next((alias for alias in aliases if _contains(text, alias)), None)
        if matched_alias is None:
            continue
        corridor = Corridor(item["corridor"])
        corridor_ids.update((corridor, Corridor.PORT_SPECIFIC))
        ports.append(
            {
                "name": str(item["name"]),
                "unlocode": str(item["unlocode"]),
                "corridor": corridor.value,
                "matched_alias": str(matched_alias),
            }
        )

    event_types = tuple(
        event_type
        for event_type, terms in _EVENT_TYPE_TERMS.items()
        if any(_contains(text, term) for term in terms)
    )
    ordered_corridors = tuple(item for item in Corridor if item in corridor_ids)
    return GazetteerSignals(
        corridors=ordered_corridors,
        ports=tuple(ports),
        event_types=event_types,
    )
