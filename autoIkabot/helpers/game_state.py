"""Central game state object and global data parser (Phase 5.2).

Provides:
  - GameState: holds parsed resource/transport/production data
  - parse_global_data(): parse the updateGlobalData AJAX response
  - parse_resource_bar(): parse resource bar from any game page HTML
  - parse_server_time(): extract server time from HTML
  - getProductionPerHour(): get wood + luxury production for a city
  - fetch_game_state(): convenience to fetch and parse current state
"""

import json
import re
from decimal import Decimal, getcontext
from typing import Any, Dict, Optional, Tuple

from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)

getcontext().prec = 30


class GameState:
    """Central game state snapshot parsed from updateGlobalData.

    Attributes
    ----------
    gold : int
        Current gold amount.
    gold_production : int
        Net gold production per hour (income + upkeep + scientists).
    income : int
        Gold income per hour.
    upkeep : int
        Gold upkeep per hour (negative).
    scientists_upkeep : int
        Scientists upkeep per hour (negative).
    resources : list[int]
        Current resources [wood, wine, marble, crystal, sulfur].
    storage : list[int]
        Max storage per resource [wood, wine, marble, crystal, sulfur].
    resource_production : float
        Wood production rate (per second from server, stored as per hour).
    tradegood_production : float
        Luxury good production rate (per hour).
    produced_tradegood : int
        Type of luxury good produced (1=wine, 2=marble, 3=crystal, 4=sulfur).
    wine_consumption : int
        Wine consumption per hour.
    free_transporters : int
        Available merchant ships.
    max_transporters : int
        Total merchant ships.
    free_freighters : int
        Available freighters.
    max_freighters : int
        Total freighters.
    citizens : int
        Current citizen count.
    population : int
        Max population (housing space).
    current_city_id : str
        ID of the currently active city.
    server_time : str
        Server time string (if parsed).
    raw : dict
        Raw headerData dict from the JSON response.
    """

    def __init__(self):
        self.gold: int = 0
        self.gold_production: int = 0
        self.income: int = 0
        self.upkeep: int = 0
        self.scientists_upkeep: int = 0
        self.resources: list = [0, 0, 0, 0, 0]
        self.storage: list = [0, 0, 0, 0, 0]
        self.resource_production: float = 0.0
        self.tradegood_production: float = 0.0
        self.produced_tradegood: int = 0
        self.wine_consumption: int = 0
        self.free_transporters: int = 0
        self.max_transporters: int = 0
        self.free_freighters: int = 0
        self.max_freighters: int = 0
        self.citizens: int = 0
        self.population: int = 0
        self.current_city_id: str = ""
        self.server_time: str = ""
        self.raw: Dict[str, Any] = {}


def parse_global_data(data: str) -> GameState:
    """Parse the response from ``?view=updateGlobalData&ajax=1``.

    The response is a JSON array like::

        [[..., {"headerData": {...}, ...}], ...]

    Parameters
    ----------
    data : str
        Raw response text from the updateGlobalData endpoint.

    Returns
    -------
    GameState
        Parsed game state.
    """
    state = GameState()

    try:
        json_data = json.loads(data, strict=False)
        header = json_data[0][1]["headerData"]
        state.raw = header
    except (json.JSONDecodeError, IndexError, KeyError, TypeError) as e:
        logger.warning("Could not parse updateGlobalData response: %s", e)
        return state

    # Gold
    state.gold = int(Decimal(str(header.get("gold", 0))))
    state.income = int(Decimal(str(header.get("income", 0))))
    state.upkeep = int(Decimal(str(header.get("upkeep", 0))))
    state.scientists_upkeep = int(Decimal(str(header.get("scientistsUpkeep", 0))))
    state.gold_production = int(
        Decimal(str(state.income))
        + Decimal(str(state.upkeep))
        + Decimal(str(state.scientists_upkeep))
    )

    # Resources from currentResources
    cr = header.get("currentResources", {})
    state.resources = [
        int(Decimal(str(cr.get("resource", 0)))),   # wood
        int(Decimal(str(cr.get("1", 0)))),           # wine
        int(Decimal(str(cr.get("2", 0)))),           # marble
        int(Decimal(str(cr.get("3", 0)))),           # crystal
        int(Decimal(str(cr.get("4", 0)))),           # sulfur
    ]

    # Storage capacity from maxResources
    mr = header.get("maxResources", {})
    state.storage = [
        int(Decimal(str(mr.get("resource", 0)))),
        int(Decimal(str(mr.get("1", 0)))),
        int(Decimal(str(mr.get("2", 0)))),
        int(Decimal(str(mr.get("3", 0)))),
        int(Decimal(str(mr.get("4", 0)))),
    ]

    # Production rates (per second from server → convert to per hour)
    state.resource_production = float(
        Decimal(str(header.get("resourceProduction", 0))) * 3600
    )
    state.tradegood_production = float(
        Decimal(str(header.get("tradegoodProduction", 0))) * 3600
    )
    state.produced_tradegood = int(header.get("producedTradegood", 0))

    # Wine consumption
    state.wine_consumption = int(header.get("wineSpendings", 0))

    # Transport
    state.free_transporters = int(header.get("freeTransporters", 0))
    state.max_transporters = int(header.get("maxTransporters", 0))
    state.free_freighters = int(header.get("freeFreighters", 0))
    state.max_freighters = int(header.get("maxFreighters", 0))

    # Population
    state.citizens = int(Decimal(str(cr.get("citizens", 0))))
    state.population = int(Decimal(str(cr.get("population", 0))))

    # Current city
    related = header.get("relatedCity", {})
    state.current_city_id = str(related.get("id", ""))

    return state


def parse_resource_bar(html: str) -> Dict[str, int]:
    """Parse resource bar values from any game page HTML.

    Uses the element IDs from the top navigation bar.

    Parameters
    ----------
    html : str
        Game page HTML.

    Returns
    -------
    dict
        Parsed values with keys: gold, wood, wine, marble, crystal, sulfur,
        max_wood, max_wine, max_marble, max_crystal, max_sulfur,
        citizens, population, free_transporters, max_transporters,
        free_freighters, max_freighters, resource_production, income, upkeep.
    """
    result = {}

    patterns = {
        "gold": r'js_GlobalMenu_gold">([\d,.]+)<',
        "wood": r'js_GlobalMenu_wood">([\d,.]+)<',
        "wine": r'js_GlobalMenu_wine">([\d,.]+)<',
        "marble": r'js_GlobalMenu_marble">([\d,.]+)<',
        "crystal": r'js_GlobalMenu_crystal">([\d,.]+)<',
        "sulfur": r'js_GlobalMenu_sulfur">([\d,.]+)<',
        "max_wood": r'js_GlobalMenu_max_wood">([\d,.]+)<',
        "max_wine": r'js_GlobalMenu_max_wine">([\d,.]+)<',
        "max_marble": r'js_GlobalMenu_max_marble">([\d,.]+)<',
        "max_crystal": r'js_GlobalMenu_max_crystal">([\d,.]+)<',
        "max_sulfur": r'js_GlobalMenu_max_sulfur">([\d,.]+)<',
        "citizens": r'js_GlobalMenu_citizens">([\d,.]+)<',
        "population": r'js_GlobalMenu_population">([\d,.]+)<',
        "free_transporters": r'js_GlobalMenu_freeTransporters">([\d,.]+)<',
        "max_transporters": r'js_GlobalMenu_maxTransporters">([\d,.]+)<',
        "free_freighters": r'js_GlobalMenu_freeFreighters">([\d,.]+)<',
        "max_freighters": r'js_GlobalMenu_maxFreighters">([\d,.]+)<',
        "resource_production": r'js_GlobalMenu_resourceProduction">([\d,.]+)<',
        "income": r'js_GlobalMenu_income">([\d,.]+)<',
        "upkeep": r'js_GlobalMenu_upkeep">([\d,.]+)<',
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, html)
        if match:
            # Strip thousand separators (commas, dots, spaces)
            digits = re.sub(r"[^\d]", "", match.group(1))
            result[key] = int(digits) if digits else 0
        else:
            result[key] = 0

    return result


def parse_server_time(html: str) -> str:
    """Extract server time from game HTML.

    The format is typically ``DD.MM.YYYY HH:MM:SS CET``.

    Parameters
    ----------
    html : str
        Game page HTML.

    Returns
    -------
    str
        Server time string, or empty string if not found.
    """
    match = re.search(r'id="servertime"[^>]*>(.*?)</li>', html)
    if match:
        return match.group(1).strip()
    return ""


def getProductionPerHour(
    session, city_id: str
) -> Tuple[Decimal, Decimal, int]:
    """Get wood and luxury production rates for a city.

    Parameters
    ----------
    session : Session
        The game session.
    city_id : str
        City ID to query.

    Returns
    -------
    tuple[Decimal, Decimal, int]
        (wood_production_per_hour, luxury_production_per_hour, luxury_type).
    """
    resource_search_pool = {
        1: "js_GlobalMenu_production_wine",
        2: "js_GlobalMenu_production_marble",
        3: "js_GlobalMenu_production_crystal",
        4: "js_GlobalMenu_production_sulfur",
    }

    from autoIkabot.config import CITY_URL

    html = session.get(CITY_URL + str(city_id))

    luxury_type_match = re.search(r"tradegood&type=(\d+)", html)
    if not luxury_type_match:
        logger.warning("Could not determine luxury type for city %s", city_id)
        return Decimal(0), Decimal(0), 0

    luxury_type = int(luxury_type_match.group(1))

    production_pattern = r'<td id="{}"[^>]*>\s*([\d,\s]+)\s*</td>'

    def clean_number(num_str: str) -> int:
        return int(re.sub(r"[^\d]", "", num_str))

    wood_match = re.search(
        production_pattern.format("js_GlobalMenu_resourceProduction"), html
    )
    luxury_match = re.search(
        production_pattern.format(resource_search_pool.get(luxury_type, "")),
        html,
    )

    wood_prod = clean_number(wood_match.group(1)) if wood_match else 0
    luxury_prod = clean_number(luxury_match.group(1)) if luxury_match else 0

    return Decimal(wood_prod), Decimal(luxury_prod), luxury_type


def fetch_game_state(session, city_id: Optional[str] = None) -> GameState:
    """Fetch and parse the current game state for a city.

    Navigates to the city (if specified) then calls updateGlobalData.

    Parameters
    ----------
    session : Session
        The game session.
    city_id : str, optional
        City ID to switch to first. If None, uses current city.

    Returns
    -------
    GameState
        Parsed game state.
    """
    from autoIkabot.config import CITY_URL

    if city_id:
        session.get(CITY_URL + str(city_id), no_index=True)

    data = session.get("view=updateGlobalData&ajax=1", no_index=True)
    state = parse_global_data(data)

    # Also try to get server time from the HTML
    html = session.get()
    state.server_time = parse_server_time(html)

    return state
