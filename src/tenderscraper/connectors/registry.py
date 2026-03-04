from __future__ import annotations

from typing import Dict, Type

from tenderscraper.connectors.base import BaseConnector
from tenderscraper.connectors.sources.ted import TedConnector
from tenderscraper.connectors.sources.tender_arena import TenderArenaConnector
from tenderscraper.connectors.sources.poptavej import PoptavejConnector

CONNECTORS: Dict[str, Type[BaseConnector]] = {
    "ted": TedConnector,
    "tender_arena": TenderArenaConnector,
    "poptavej": PoptavejConnector,
}


def get_connector(source: str) -> BaseConnector:
    try:
        cls = CONNECTORS[source]
    except KeyError as e:
        raise ValueError(f"Unknown source '{source}'. Available: {sorted(CONNECTORS)}") from e
    return cls()
