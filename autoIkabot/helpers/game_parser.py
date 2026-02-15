"""HTML parsers for game pages (city view, island view).

Ported from ikabot's helpers/getJson.py and helpers/resources.py.
These extract structured data from the raw HTML returned by the game server.
"""

import json
import re
from typing import Any, Dict, List


def decode_unicode_escape(input_string: str) -> str:
    """Replace Unicode escape sequences (e.g. u043c) with UTF-8 characters."""
    return re.sub(
        r"u([0-9a-fA-F]{4})", lambda x: chr(int(x.group(1), 16)), input_string
    )


# ---------------------------------------------------------------------------
# Resource extraction helpers (used by getCity)
# ---------------------------------------------------------------------------

def get_available_resources(html: str) -> List[int]:
    """Extract available resources [wood, wine, marble, crystal, sulfur] from city HTML.

    Parameters
    ----------
    html : str
        City page HTML.

    Returns
    -------
    list[int]
        Five-element list of resource amounts.
    """
    resources = re.search(
        r'\\"resource\\":(\d+),\\"2\\":(\d+),\\"1\\":(\d+),\\"4\\":(\d+),\\"3\\":(\d+)}',
        html,
    )
    if resources is None:
        return [0, 0, 0, 0, 0]
    return [
        int(resources.group(1)),
        int(resources.group(3)),
        int(resources.group(2)),
        int(resources.group(5)),
        int(resources.group(4)),
    ]


def get_warehouse_capacity(html: str) -> int:
    """Extract total warehouse storage capacity from city HTML."""
    match = re.search(
        r'maxResources:\s*JSON\.parse\(\'{\\"resource\\":(\d+),', html
    )
    if match is None:
        return 0
    return int(match.group(1))


def get_free_citizens(html: str) -> int:
    """Extract free (idle) citizen count from city HTML."""
    match = re.search(r'js_GlobalMenu_citizens">(.*?)</span>', html)
    if match is None:
        return 0
    digits = re.sub(r'\D', '', match.group(1))
    return int(digits) if digits else 0


def get_wine_consumption(html: str) -> int:
    """Extract wine consumption per hour from city HTML."""
    match = re.search(r"wineSpendings:\s(\d+)", html)
    return int(match.group(1)) if match else 0


def get_resources_listed_for_sale(html: str) -> List[int]:
    """Extract resources listed for sale in branch office."""
    match = re.search(
        r'branchOfficeResources: JSON\.parse\(\'{\\"resource\\":\\"(\d+)\\",\\"1\\":\\"(\d+)\\",\\"2\\":\\"(\d+)\\",\\"3\\":\\"(\d+)\\",\\"4\\":\\"(\d+)\\"}\'\)',
        html,
    )
    if match:
        return [
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            int(match.group(4)),
            int(match.group(5)),
        ]
    return [0, 0, 0, 0, 0]


# ---------------------------------------------------------------------------
# City parser
# ---------------------------------------------------------------------------

def getCity(html: str) -> Dict[str, Any]:
    """Parse a city view page into a structured dict.

    Parameters
    ----------
    html : str
        The HTML returned by ``session.get(CITY_URL + city_id)``.

    Returns
    -------
    dict
        City data including id, name, position[], availableResources[],
        storageCapacity, freeSpaceForResources[], islandId, etc.
    """
    raw = re.search(
        r'"updateBackgroundData",\s?([\s\S]*?)\],\["updateTemplateData"', html
    )
    if raw is None:
        raise ValueError("Could not parse city data from HTML")

    city = json.loads(raw.group(1), strict=False)

    city["ownerName"] = decode_unicode_escape(city.get("ownerName", ""))
    city["x"] = int(city.get("islandXCoord", 0))
    city["y"] = int(city.get("islandYCoord", 0))
    city["cityName"] = decode_unicode_escape(city.get("name", ""))
    city["name"] = city["cityName"]

    # Parse building positions
    for i, position in enumerate(city.get("position", [])):
        position["position"] = i
        if "level" in position:
            position["level"] = int(position["level"])
        position["isBusy"] = False
        building = position.get("building", "")
        if "constructionSite" in building:
            position["isBusy"] = True
            position["building"] = building[:-17]
        elif "buildingGround " in building:
            position["name"] = "empty"
            position["type"] = building.split(" ")[-1]
            position["building"] = "empty"

    city["id"] = str(city["id"])
    city["isOwnCity"] = True
    city["availableResources"] = get_available_resources(html)
    city["storageCapacity"] = get_warehouse_capacity(html)
    city["freeCitizens"] = get_free_citizens(html)
    city["wineConsumptionPerHour"] = get_wine_consumption(html)
    city["resourcesListedForSale"] = get_resources_listed_for_sale(html)
    city["freeSpaceForResources"] = []
    for i in range(5):
        city["freeSpaceForResources"].append(
            city["storageCapacity"]
            - city["availableResources"][i]
            - city["resourcesListedForSale"][i]
        )

    return city


# ---------------------------------------------------------------------------
# Island parser
# ---------------------------------------------------------------------------

def getIsland(html: str) -> Dict[str, Any]:
    """Parse an island view page into a structured dict.

    Parameters
    ----------
    html : str
        The HTML returned by ``session.get(ISLAND_URL + island_id)``.

    Returns
    -------
    dict
        Island data including id, name, x, y, tradegood, cities[].
    """
    raw = re.search(r'ajax\.Responder, (\[\[[\S\s]*?\]\])\)\;', html)
    if raw is None:
        raise ValueError("Could not parse island data from HTML")

    island = json.loads(raw.group(1))[1][1]

    island["x"] = int(island.get("xCoord", 0))
    island["y"] = int(island.get("yCoord", 0))
    island["tipo"] = str(island.get("tradegood", ""))

    for city in island.get("cities", []):
        if not isinstance(city, dict):
            continue
        for key in ["Id", "Name", "AllyId", "AllyTag"]:
            owner_key = "owner" + key
            if owner_key in city:
                city[key] = city[owner_key]

        if "buildplace_type" in city:
            city["_type"] = city["buildplace_type"]

        if city.get("type") == "buildplace":
            city["type"] = "empty"

    return island


# ---------------------------------------------------------------------------
# City list fetcher (for UI menus)
# ---------------------------------------------------------------------------

def getIdsOfCities(session) -> tuple:
    """Get all city IDs and basic city info for the logged-in player.

    Parameters
    ----------
    session : Session
        The game session.

    Returns
    -------
    tuple
        (ids: list[str], cities: dict[str, dict]) — ordered list of city
        IDs and a dict mapping city_id -> {id, name, tradegood, ...}.
    """
    html = session.get()
    cities_raw = re.search(
        r'relatedCityData:\sJSON\.parse\(\'(.+?),\\"additionalInfo', html
    )
    if cities_raw is None:
        return ([], {})

    cities_json = cities_raw.group(1) + "}"
    cities_json = cities_json.replace("\\", "")
    cities_json = cities_json.replace("city_", "")
    cities_data = json.loads(cities_json, strict=False)

    ids = []
    cities = {}
    for city_id, city_info in sorted(
        cities_data.items(),
        key=lambda x: int(x[1].get("position", 0) if isinstance(x[1], dict) else 0),
    ):
        if not isinstance(city_info, dict):
            continue
        city_info["id"] = city_id
        city_info["name"] = decode_unicode_escape(city_info.get("name", ""))
        city_info["tradegood"] = int(city_info.get("tradegood", 0))
        ids.append(city_id)
        cities[city_id] = city_info

    return (ids, cities)
