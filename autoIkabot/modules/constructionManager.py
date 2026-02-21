"""Construction Manager — autoIkabot module.

Unified module for building new constructions in empty slots AND upgrading
existing buildings to higher levels. Combines the functionality of ikabot's
constructBuilding.py and constructionList.py into a single module.

Phase 1: Core construction (build + upgrade)
Phase 2: Auto-transport integration (scan empire, allocate, ship)

User flow:
  1. Choose a city
  2. View all city plots (empty + occupied) with status
  3. Select positions to build/upgrade (comma-separated)
  4. For empty slots: choose building type -> place it
  5. For occupied slots: choose target level -> calculate costs -> upgrade
  6. If resources missing: scan empire -> auto-allocate -> transport
  7. Background phase: execute transport, then upgrade level by level
"""

import hashlib
import json
import math
import os
import re
import sys
import time
import traceback
from decimal import Decimal
from functools import lru_cache

import requests

from autoIkabot.config import (
    ACTION_REQUEST_PLACEHOLDER,
    CITY_URL,
    ISLAND_URL,
    COST_REDUCER_BUILDINGS,
    COST_REDUCTION_MAX,
    COST_REDUCTION_RESEARCH,
    MATERIALS_NAMES,
    MATERIALS_NAMES_TEC,
    MATERIAL_IMG_HASH,
)
from autoIkabot.helpers.formatting import addThousandSeparator, daysHoursMinutes, getDateTime
from autoIkabot.helpers.game_parser import getCity, getIdsOfCities, getIsland
from autoIkabot.helpers.naval import getAvailableFreighters, getAvailableShips, waitForArrival
from autoIkabot.helpers.routing import executeRoutes
from autoIkabot.modules.resourceTransportManager import (
    allocate_from_suppliers,
    acquire_shipping_lock,
    release_shipping_lock,
)
from autoIkabot.ui.prompts import banner, chooseCity, enter, ignoreCities, read
from autoIkabot.utils.logging import get_logger
from autoIkabot.utils.process import (
    report_critical_error,
    set_child_mode,
    sleep_with_heartbeat,
)

logger = get_logger(__name__)

# --- Module Metadata ---
MODULE_NAME = "Construction Manager"
MODULE_SECTION = "Construction"
MODULE_NUMBER = 21
MODULE_DESCRIPTION = "Build new buildings or upgrade existing ones"


# ---------------------------------------------------------------------------
# ANSI colour helpers (for building status display)
# ---------------------------------------------------------------------------

class _Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    DARK = "\033[90m"
    ENDC = "\033[0m"


# ---------------------------------------------------------------------------
# CDN image hash identification (ported from ikabot)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=32)
def _checkhash(url):
    """Download a CDN resource image and identify it by MD5 hash.

    Parameters
    ----------
    url : str
        Full URL to the .png image on the Ikariam CDN.

    Returns
    -------
    str or None
        Technical resource name ("wood", "wine", "marble", "glass", "sulfur")
        or None if the hash does not match any known resource.
    """
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
    except Exception:
        logger.warning("Failed to download CDN image: %s", url)
        return None

    md5 = hashlib.md5(r.content).hexdigest()
    for i, known_hash in enumerate(MATERIAL_IMG_HASH):
        if md5 == known_hash:
            return MATERIALS_NAMES_TEC[i]
    logger.warning("Unknown resource image hash %s for %s", md5, url)
    return None


# ---------------------------------------------------------------------------
# Cost reduction helpers (ported from ikabot)
# ---------------------------------------------------------------------------

def _get_cost_reducers(city):
    """Scan city buildings for cost-reducing buildings.

    Returns a list of 5 ints — the level of each cost-reducer building
    (0 if not present). Index matches MATERIALS_NAMES order.

    Parameters
    ----------
    city : dict
        Parsed city data from getCity().

    Returns
    -------
    list[int]
        [wood_reducer_lv, wine_reducer_lv, marble_reducer_lv,
         crystal_reducer_lv, sulfur_reducer_lv]
    """
    reducers = [0] * len(MATERIALS_NAMES)
    for building in city.get("position", []):
        if building.get("name") == "empty":
            continue
        bname = building.get("building", "")
        if bname in COST_REDUCER_BUILDINGS:
            idx = COST_REDUCER_BUILDINGS[bname]
            reducers[idx] = int(building.get("level", 0))
    return reducers


# Cache for research reduction (persists within the child process only —
# safe because each module instance runs in its own process).
_cached_research_reduction = None


def _get_research_reduction(session, city_id):
    """Get the total building-cost reduction percentage from economy research.

    Checks in-process cache first. If not cached, queries the research
    advisor and parses which cost-reduction techs have been researched.

    Parameters
    ----------
    session : Session
    city_id : str
        ID of any city (used in the API call).

    Returns
    -------
    float
        Multiplier like 0.86 (meaning 14% reduction -> pay 86% of base cost).
    """
    global _cached_research_reduction
    if _cached_research_reduction is not None:
        return _cached_research_reduction

    params = {
        "view": "noViewChange",
        "researchType": "economy",
        "backgroundView": "city",
        "currentCityId": city_id,
        "templateView": "researchAdvisor",
        "actionRequest": ACTION_REQUEST_PLACEHOLDER,
        "ajax": "1",
    }
    try:
        rta = session.post(params=params)
        rta = json.loads(rta, strict=False)
        studies = rta[2][1]["new_js_params"]
        studies = json.loads(studies, strict=False)
        studies = studies["currResearchType"]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as e:
        logger.warning("Failed to parse research data: %s", e)
        return 1.0  # no reduction on failure

    reduction_pct = 0
    for study in studies:
        try:
            if studies[study]["liClass"] != "explored":
                continue
            link = studies[study]["aHref"]
            for tech_id, pct in COST_REDUCTION_RESEARCH.items():
                if tech_id in link:
                    reduction_pct += pct
        except (KeyError, TypeError):
            continue

    result = (100 - reduction_pct) / 100
    # Cache if we've discovered the max reduction (won't change during session)
    if reduction_pct >= COST_REDUCTION_MAX:
        _cached_research_reduction = result

    return result


# ---------------------------------------------------------------------------
# Cost calculation (ported from ikabot's getResourcesNeeded)
# ---------------------------------------------------------------------------

def _get_resources_needed(session, city, building, current_level, final_level):
    """Calculate total resources needed to upgrade a building from current to final level.

    Makes HTTP requests to the game's building encyclopedia to get cost tables,
    then applies research and building cost reductions.

    Parameters
    ----------
    session : Session
    city : dict
        Parsed city data.
    building : dict
        Building position data from city["position"].
    current_level : int
        Current effective level (accounts for in-progress upgrades).
    final_level : int
        Target level.

    Returns
    -------
    list[int]
        Five-element list of total costs [wood, wine, marble, crystal, sulfur].
        Returns [-1]*5 if the user cancels due to level cap.
        Returns None on parse failure.
    """
    # Step 1: Get building encyclopedia HTML
    params = {
        "view": "buildingDetail",
        "buildingId": "0",
        "helpId": "1",
        "backgroundView": "city",
        "currentCityId": city["id"],
        "templateView": "ikipedia",
        "actionRequest": ACTION_REQUEST_PLACEHOLDER,
        "ajax": "1",
    }
    try:
        resp = session.post(params=params)
        detail = json.loads(resp, strict=False)
        building_html = detail[1][1][1]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as e:
        logger.error("Failed to fetch building encyclopedia: %s", e)
        return None

    # Step 2: Find the specific building's cost page URL
    regex = (
        r'<div class="(?:selected)? button_building '
        + re.escape(building["building"])
        + r'"\s*onmouseover="\$\(this\)\.addClass\(\'hover\'\);" '
        r'onmouseout="\$\(this\)\.removeClass\(\'hover\'\);"\s*'
        r'onclick="ajaxHandlerCall\(\'\?(.*?)\'\);'
    )
    match = re.search(regex, building_html)
    if match is None:
        logger.error("Could not find cost URL for building '%s'", building["building"])
        return None

    cost_url = match.group(1)
    cost_url += (
        "backgroundView=city&currentCityId={}&templateView=buildingDetail"
        "&actionRequest={}&ajax=1"
    ).format(city["id"], ACTION_REQUEST_PLACEHOLDER)

    try:
        resp = session.post(url=cost_url)
        costs_data = json.loads(resp, strict=False)
        html_costs = costs_data[1][1][1]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as e:
        logger.error("Failed to fetch building costs: %s", e)
        return None

    # Step 3: Get research cost reduction
    cost_multiplier = _get_research_reduction(session, city["id"])

    # Step 4: Get building-level cost reducers
    cost_reducers = _get_cost_reducers(city)

    # Step 5: Identify resource types from CDN image headers
    resource_types = re.findall(
        r'<th class="costs"><img src="(.*?)\.png"/></th>', html_costs
    )
    # Last column is typically "time" — drop it
    if resource_types:
        resource_types = resource_types[:-1]

    # Step 6: Extract per-level cost rows
    rows = re.findall(
        r'<td class="level">\d+</td>(?:\s+<td class="costs">.*?</td>)+',
        html_costs,
    )

    # Step 7: Calculate total costs with all reductions
    final_costs = [0] * len(MATERIALS_NAMES)
    levels_parsed = 0

    for row in rows:
        lv_match = re.search(r'"level">(\d+)</td>', row)
        if lv_match is None:
            continue
        lv = int(lv_match.group(1))

        if lv <= current_level:
            continue
        if lv > final_level:
            break

        levels_parsed += 1
        cost_cells = re.findall(
            r'<td class="costs"><div.*?>([\d,\.\s\xa0]*)</div></div></td>', row
        )
        # Clean up whitespace from cost strings
        cost_cells = [
            c.replace('\xa0', '').replace(' ', '') for c in cost_cells
        ]

        for i, raw_cost_str in enumerate(cost_cells):
            if i >= len(resource_types):
                break

            # Identify which resource this column is
            res_name = _checkhash("https:" + resource_types[i] + ".png")
            if res_name is None:
                continue

            # Map technical name to index
            resource_index = None
            for j, tec_name in enumerate(MATERIALS_NAMES_TEC):
                if res_name == tec_name:
                    resource_index = j
                    break
            if resource_index is None:
                continue

            # Parse cost string
            cost_str = raw_cost_str.replace(",", "").replace(".", "")
            cost = 0 if cost_str == "" else int(cost_str)

            # Apply reductions using Decimal for precision
            real_cost = Decimal(cost)
            if cost_multiplier > 0 and cost_multiplier < 1:
                original_cost = Decimal(real_cost) / Decimal(str(cost_multiplier))
                real_cost -= Decimal(original_cost) * (
                    Decimal(cost_reducers[resource_index]) / Decimal(100)
                )
            elif cost_multiplier == 1:
                # No research reduction — just building reducer
                real_cost -= Decimal(real_cost) * (
                    Decimal(cost_reducers[resource_index]) / Decimal(100)
                )

            final_costs[resource_index] += math.ceil(real_cost)

    # Handle level cap
    levels_requested = final_level - current_level
    if levels_parsed < levels_requested:
        if levels_parsed == 0:
            print("This building cannot be expanded further.")
            return [-1] * 5
        print(
            "This building only allows you to expand {:d} more levels".format(
                levels_parsed
            )
        )
        msg = "Expand {:d} levels? [Y/n]:".format(levels_parsed)
        rta = read(msg=msg, values=["Y", "y", "N", "n", ""])
        if rta.lower() == "n":
            return [-1] * 5

    return final_costs


# ---------------------------------------------------------------------------
# City slot display
# ---------------------------------------------------------------------------

def _display_city_slots(city):
    """Display all city slots (empty + occupied) with status info.

    Parameters
    ----------
    city : dict
        Parsed city data.

    Returns
    -------
    list[dict]
        The city["position"] list for reference.
    """
    positions = city.get("position", [])
    print(f"\nCity: {city['cityName']}")
    print(f"  {'Pos':<5} {'Building':<25} {'Level':<8} Status")
    print(f"  {'-' * 5} {'-' * 25} {'-' * 8} {'-' * 20}")

    for pos in positions:
        pos_num = pos.get("position", "?")

        if pos.get("building") == "empty":
            terrain = pos.get("type", "?")
            print(f"  {pos_num:<5} {_Colors.DARK}[Empty - {terrain}]{_Colors.ENDC:<25} {'-':<8}")
            continue

        name = pos.get("name", pos.get("building", "?"))
        level = pos.get("level", 0)
        is_busy = pos.get("isBusy", False)

        # Determine status and color
        is_max = pos.get("isMaxLevel", False)
        can_upgrade = pos.get("canUpgrade", False)

        if is_max:
            color = _Colors.DARK
            status = "(max level)"
        elif can_upgrade:
            color = _Colors.GREEN
            status = "(can upgrade)"
        else:
            color = _Colors.RED
            status = "(missing resources)"

        level_str = str(level)
        if is_busy:
            level_str += "+"
            status = "(upgrading)"

        print(
            f"  {pos_num:<5} {color}{name:<25}{_Colors.ENDC} {level_str:<8} {status}"
        )

    return positions


# ---------------------------------------------------------------------------
# New building placement (ported from constructBuilding.py)
# ---------------------------------------------------------------------------

def _handle_empty_slot(session, city, position):
    """Handle building a new construction in an empty slot.

    Fetches available buildings for the slot's terrain type, lets the user
    choose, and places the building.

    Parameters
    ----------
    session : Session
    city : dict
    position : dict
        The empty slot position data.

    Returns
    -------
    bool
        True if building was placed, False otherwise.
    """
    terrain_type = position.get("type", "land")
    pos_num = position["position"]

    # Fetch available buildings for this terrain type
    params = {
        "view": "buildingGround",
        "cityId": city["id"],
        "position": pos_num,
        "backgroundView": "city",
        "currentCityId": city["id"],
        "actionRequest": ACTION_REQUEST_PLACEHOLDER,
        "ajax": "1",
    }

    try:
        resp = session.post(params=params, no_index=True)
        resp_data = json.loads(resp, strict=False)
        html = resp_data[1][1]
        if isinstance(html, list):
            html = html[1] if len(html) > 1 else ""
        if not html:
            print(f"  No buildings available for {terrain_type} slot at position {pos_num}.")
            return False
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as e:
        logger.warning("Failed to fetch available buildings: %s", e)
        print("  Could not fetch available buildings for this slot.")
        return False

    # Parse available buildings from HTML
    matches = re.findall(
        r'<li class="building (.+?)">\s*<div class="buildinginfo">\s*'
        r'<div title="(.+?)"\s*class="buildingimg .+?"\s*'
        r'onclick="ajaxHandlerCall\(\'.*?buildingId=(\d+)&',
        html,
    )
    if not matches:
        print(f"  No buildings can be built at position {pos_num}.")
        return False

    print(f"\nAvailable buildings for position {pos_num} ({terrain_type}):\n")
    print("  (0) Cancel")
    for i, (_, name, _) in enumerate(matches, 1):
        print(f"  ({i}) {name}")

    choice = read(min=0, max=len(matches))
    if choice == 0:
        return False

    building_class, building_name, building_id = matches[choice - 1]

    # Place the building
    params = {
        "action": "CityScreen",
        "function": "build",
        "cityId": city["id"],
        "position": pos_num,
        "building": building_id,
        "backgroundView": "city",
        "currentCityId": city["id"],
        "templateView": "buildingGround",
        "actionRequest": ACTION_REQUEST_PLACEHOLDER,
        "ajax": "1",
    }

    try:
        resp = session.post(params=params, no_index=True)
        resp_data = json.loads(resp, strict=False)
        msg = resp_data[3][1][0]["text"]
        print(f"\n  {msg}")
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as e:
        logger.warning("Failed to parse build response: %s", e)
        print(f"\n  Build request sent for {building_name}.")

    return True


# ---------------------------------------------------------------------------
# Auto-transport integration (Phase 2)
# ---------------------------------------------------------------------------

def _handle_missing_resources(session, destination_city, missing):
    """Scan empire for missing resources, allocate, and confirm transport.

    Uses RTM's allocate_from_suppliers() to automatically find supplier
    cities and build routes. The user can exclude cities and must confirm
    the plan before execution.

    Parameters
    ----------
    session : Session
    destination_city : dict
        The city that needs resources (parsed city data).
    missing : list[int]
        Five-element list of missing resource amounts.

    Returns
    -------
    dict or None
        On success: {"routes": [...], "useFreighters": bool}
        On cancel/failure: None
    """
    print("\n  Scanning empire for resources...")

    # Let user exclude cities from supplying
    banner()
    print("Missing resources for construction:")
    for i in range(len(MATERIALS_NAMES)):
        if missing[i] > 0:
            print("  {}: {}".format(MATERIALS_NAMES[i], addThousandSeparator(missing[i])))
    print("")

    city_ids, cities = ignoreCities(
        session, msg="Exclude cities from supplying resources:"
    )

    # Gather supplier data (skip destination city)
    banner()
    print("  Scanning supplier cities...\n")
    suppliers = []
    totals = [0] * len(MATERIALS_NAMES)

    for cid in city_ids:
        if str(cid) == str(destination_city["id"]):
            continue
        try:
            html = session.get(CITY_URL + str(cid))
            city_data = getCity(html)
            suppliers.append(city_data)
            for i in range(len(MATERIALS_NAMES)):
                totals[i] += city_data["availableResources"][i]
        except Exception as e:
            logger.warning("Failed to fetch city %s: %s", cid, e)

    if not suppliers:
        print("  No supplier cities available.")
        enter()
        return None

    # Check if empire has enough
    can_complete = all(
        totals[i] >= missing[i] for i in range(len(MATERIALS_NAMES))
    )

    # Get island data for destination (needed for route tuples)
    try:
        html = session.get(ISLAND_URL + destination_city["islandId"])
        destination_island = getIsland(html)
    except Exception as e:
        logger.warning("Failed to fetch destination island: %s", e)
        destination_island = {"id": destination_city.get("islandId", "")}

    if can_complete:
        # Empire has enough — allocate and show plan
        routes = allocate_from_suppliers(
            missing, suppliers, destination_city, destination_island
        )
        if routes is None:
            print("  Could not allocate resources across suppliers.")
            enter()
            return None

        print("  Empire has enough resources to complete all upgrades.\n")
        _display_route_summary(routes)

        print("\n  Proceed with transport and construction? [Y/n]")
        rta = read(values=["y", "Y", "n", "N", ""])
        if rta.lower() == "n":
            return None

    else:
        # Empire does NOT have enough — show what's available
        print("  Your empire does not have enough resources for all upgrades.\n")
        print("  Available across empire:")
        for i in range(len(MATERIALS_NAMES)):
            if missing[i] > 0:
                avail_str = addThousandSeparator(totals[i])
                need_str = addThousandSeparator(missing[i])
                status = " (OK)" if totals[i] >= missing[i] else " (short)"
                print(
                    "    {}: {} of {} needed{}".format(
                        MATERIALS_NAMES[i], avail_str, need_str, status
                    )
                )

        print(
            "\n  Transport what's available and build/upgrade as far as possible? [Y/n]"
        )
        rta = read(values=["y", "Y", "n", "N", ""])
        if rta.lower() == "n":
            return None

        # Cap missing to what's actually available
        capped = [min(missing[i], totals[i]) for i in range(len(MATERIALS_NAMES))]
        if sum(capped) == 0:
            print("  No resources available to send.")
            enter()
            return None

        routes = allocate_from_suppliers(
            capped, suppliers, destination_city, destination_island
        )
        if routes is None:
            print("  Could not allocate resources across suppliers.")
            enter()
            return None

        print("\n  Shipment plan:")
        _display_route_summary(routes)

        print("\n  Confirm transport? [Y/n]")
        rta = read(values=["y", "Y", "n", "N", ""])
        if rta.lower() == "n":
            return None

    # Choose ship type
    print("\n  What type of ships do you want to use? (Default: Merchant ships)")
    print("  (1) Merchant ships")
    print("  (2) Freighters")
    print("  (0) Cancel")
    shiptype = read(min=0, max=2, digit=True, empty=True)
    if shiptype == 0:
        return None
    if shiptype == '' or shiptype == 1:
        useFreighters = False
    else:
        useFreighters = True

    return {"routes": routes, "useFreighters": useFreighters}


def _display_route_summary(routes):
    """Display a summary of planned transport routes.

    Parameters
    ----------
    routes : list[tuple]
        Route tuples from allocate_from_suppliers().
    """
    for route in routes:
        origin_city = route[0]
        cargo = route[3:]  # wood, wine, marble, crystal, sulfur
        origin_name = origin_city.get("name", origin_city.get("cityName", "?"))
        parts = []
        for i, amount in enumerate(cargo):
            if amount > 0:
                parts.append(
                    "{} {}".format(MATERIALS_NAMES[i], addThousandSeparator(amount))
                )
        if parts:
            print("    {} will send: {}".format(origin_name, " | ".join(parts)))


def _execute_transport(session, transport_plan):
    """Execute transport routes in the background with shipping lock.

    Acquires the shipping lock with retries, verifies ship availability,
    then executes all routes as a batch. The lock is held for the entire
    batch because all routes serve a single construction job and must
    complete together.

    Parameters
    ----------
    session : Session
    transport_plan : dict
        {"routes": [...], "useFreighters": bool}
    """
    routes = transport_plan["routes"]
    use_freighters = transport_plan["useFreighters"]

    # Acquire shipping lock with retries (matches RTM pattern)
    max_retries = 3
    lock_acquired = False
    for attempt in range(1, max_retries + 1):
        session.setStatus(
            "Acquiring shipping lock (attempt {}/{})...".format(attempt, max_retries)
        )
        if acquire_shipping_lock(session, use_freighters=use_freighters, timeout=300):
            lock_acquired = True
            break
        logger.warning(
            "Shipping lock attempt %d/%d failed", attempt, max_retries
        )
        if attempt < max_retries:
            sleep_with_heartbeat(session, 60)

    if not lock_acquired:
        msg = "Could not acquire shipping lock after {} attempts — aborting transport".format(
            max_retries
        )
        logger.error(msg)
        report_critical_error(session, MODULE_NAME, msg)
        return

    try:
        # Verify at least one ship is available before starting routes
        session.setStatus("Checking ship availability...")
        getter = getAvailableFreighters if use_freighters else getAvailableShips
        ships = getter(session)
        if ships == 0:
            session.setStatus("Waiting for ships to become available...")
            ships = waitForArrival(session, useFreighters=use_freighters)

        logger.info(
            "Starting transport: %d route(s), %d ship(s) available",
            len(routes), ships,
        )
        session.setStatus("Transporting resources for construction...")
        executeRoutes(session, routes, useFreighters=use_freighters)
    except Exception as e:
        logger.error("Transport error: %s", e)
        report_critical_error(
            session, MODULE_NAME,
            "Error transporting resources: {}".format(str(e)),
        )
    finally:
        release_shipping_lock(session, use_freighters=use_freighters)


# ---------------------------------------------------------------------------
# Background construction functions (ported from constructionList.py)
# ---------------------------------------------------------------------------

def _wait_for_construction(session, city_id, final_lvl):
    """Wait until the city's construction queue is empty.

    Polls the city view until no buildings have a "completed" timestamp,
    sleeping until each completes.

    Parameters
    ----------
    session : Session
    city_id : str
    final_lvl : int
        Target level (for status display).

    Returns
    -------
    dict
        Updated city data.
    """
    while True:
        try:
            html = session.get(CITY_URL + str(city_id))
            city = getCity(html)
        except Exception as e:
            logger.warning("Failed to fetch city during construction wait: %s", e)
            sleep_with_heartbeat(session, 60)
            continue

        construction_buildings = [
            b for b in city.get("position", []) if "completed" in b
        ]
        if len(construction_buildings) == 0:
            break

        cb = construction_buildings[0]
        completed_time = int(cb["completed"])
        now = int(time.time())
        seconds_to_wait = max(completed_time - now, 0)

        session.setStatus(
            "Waiting until {}, {} {} -> {} in {}, final lvl: {}".format(
                getDateTime(time.time() + seconds_to_wait + 10)[11:],
                cb.get("name", "?"),
                cb.get("level", "?"),
                int(cb.get("level", 0)) + 1,
                city.get("cityName", "?"),
                final_lvl,
            )
        )
        logger.debug(
            "Waiting %d seconds for %s to reach level %d",
            seconds_to_wait, cb.get("name", "?"), int(cb.get("level", 0)) + 1,
        )
        sleep_with_heartbeat(session, seconds_to_wait + 10)

    # Final fetch for fresh state
    try:
        html = session.get(CITY_URL + str(city_id))
        city = getCity(html)
    except Exception:
        pass
    return city


def _expand_building(session, city_id, building, wait_for_resources):
    """Upgrade a building level-by-level in the background.

    Parameters
    ----------
    session : Session
    city_id : str
    building : dict
        Building data with 'upgradeTo' field set.
    wait_for_resources : bool
        If True, wait for incoming ships when canUpgrade is False.
    """
    current_level = int(building["level"])
    if building.get("isBusy"):
        current_level += 1
    target = building["upgradeTo"]
    position = building["position"]
    building_name = building.get("name", building.get("building", "?"))

    levels_to_go = target - current_level
    logger.info(
        "Starting upgrade of %s from %d to %d (%d levels)",
        building_name, current_level, target, levels_to_go,
    )

    for lv in range(levels_to_go):
        city = _wait_for_construction(session, city_id, target)
        try:
            building_data = city["position"][position]
        except (IndexError, KeyError, TypeError):
            msg = "Could not find building at position {} after construction wait".format(position)
            logger.error(msg)
            report_critical_error(session, MODULE_NAME, msg)
            return

        # Wait for resources if ships are incoming
        if building_data.get("canUpgrade") is False and wait_for_resources:
            while building_data.get("canUpgrade") is False:
                sleep_with_heartbeat(session, 60)
                try:
                    from autoIkabot.helpers.naval import getMinimumWaitingTime
                    seconds = getMinimumWaitingTime(session)
                except Exception:
                    seconds = 0

                try:
                    html = session.get(CITY_URL + str(city_id))
                    city = getCity(html)
                    building_data = city["position"][position]
                except Exception:
                    pass

                if seconds == 0:
                    break
                sleep_with_heartbeat(session, seconds + 5)

        if building_data.get("canUpgrade") is False:
            msg = (
                "City: {}\nBuilding: {}\n"
                "Could not upgrade due to lack of resources.\n"
                "Missed {:d} levels (stopped at {})."
            ).format(
                city.get("cityName", "?"),
                building_name,
                levels_to_go - lv,
                int(building_data.get("level", 0)),
            )
            logger.warning(msg)
            report_critical_error(session, MODULE_NAME, msg)
            return

        # Send upgrade request
        params = {
            "action": "CityScreen",
            "function": "upgradeBuilding",
            "cityId": city_id,
            "position": position,
            "level": building_data["level"],
            "activeTab": "tabSendTransporter",
            "backgroundView": "city",
            "currentCityId": city_id,
            "templateView": building_data.get("building", ""),
            "actionRequest": ACTION_REQUEST_PLACEHOLDER,
            "ajax": "1",
        }
        session.setStatus(
            "Upgrading {} to level {} in {}".format(
                building_name,
                int(building_data.get("level", 0)) + 1,
                city.get("cityName", "?"),
            )
        )
        resp = session.post(params=params)
        try:
            resp_data = json.loads(resp, strict=False)
            # Check for error in response (type 11 = error, type 10 = success)
            if isinstance(resp_data, list) and len(resp_data) > 3:
                try:
                    server_msg = resp_data[3][1][0].get("text", "")
                    if server_msg:
                        logger.info("Server response: %s", server_msg)
                except (IndexError, KeyError, TypeError):
                    pass
        except (json.JSONDecodeError, TypeError):
            logger.warning("Could not parse upgrade response")

        # Verify upgrade started
        try:
            html = session.get(CITY_URL + str(city_id))
            city = getCity(html)
            building_data = city["position"][position]
        except Exception:
            pass

        if not building_data.get("isBusy"):
            msg = "{}: {} was not upgraded (server rejected)".format(
                city.get("cityName", "?"), building_name
            )
            logger.error(msg)
            report_critical_error(session, MODULE_NAME, msg)
            return

        logger.info(
            "%s: %s upgrading to level %d",
            city.get("cityName", "?"),
            building_name,
            int(building_data.get("level", 0)) + 1,
        )

    logger.info(
        "%s: %s finished upgrading to level %d",
        city.get("cityName", "?"),
        building_name,
        target,
    )


# ---------------------------------------------------------------------------
# Main entry point (background module)
# ---------------------------------------------------------------------------

def constructionManager(session, event, stdin_fd):
    """Construction Manager module entry point.

    Parameters
    ----------
    session : Session
        The game session.
    event : multiprocessing.Event
        Signal parent when config phase is done.
    stdin_fd : int
        File descriptor for stdin (for interactive config).
    """
    sys.stdin = os.fdopen(stdin_fd)
    try:
        banner()

        # Step 1: Choose city
        print("Select a city:")
        city = chooseCity(session)
        city_id = city["id"]

        banner()

        # Step 2: Display all slots
        positions = _display_city_slots(city)

        # Step 3: Get user selection
        print(
            "\nEnter positions to build/upgrade (comma-separated), or 0 to exit:"
        )
        raw = read(msg=">> ")
        selected_ids = [
            s.strip() for s in raw.split(",") if s.strip().isdigit()
        ]
        selected_ids = [int(s) for s in selected_ids]

        if not selected_ids or 0 in selected_ids:
            event.set()
            return

        # Separate empty vs occupied
        empty_positions = []
        upgrade_positions = []
        for sel_id in selected_ids:
            if sel_id < 0 or sel_id >= len(positions):
                print(f"  Position {sel_id} is out of range, skipping.")
                continue
            pos = positions[sel_id]
            if pos.get("building") == "empty":
                empty_positions.append(pos)
            else:
                upgrade_positions.append(pos)

        # --- Handle empty slots (new buildings) ---
        for pos in empty_positions:
            banner()
            print(f"Building in empty slot at position {pos['position']}:")
            _handle_empty_slot(session, city, pos)

        # --- Handle occupied slots (upgrades) ---
        buildings_to_upgrade = []
        total_resources_needed = [0] * len(MATERIALS_NAMES)

        for pos in upgrade_positions:
            building_name = pos.get("name", pos.get("building", "?"))
            current_level = int(pos.get("level", 0))
            if pos.get("isBusy"):
                current_level += 1

            if pos.get("isMaxLevel"):
                print(f"\n  {building_name} is already at max level, skipping.")
                continue

            banner()
            print(f"Building: {building_name}")
            print(f"Current level: {current_level}")
            final_level = read(min=current_level, max=101, msg="Upgrade to level: ")

            if final_level <= current_level:
                continue

            pos["upgradeTo"] = final_level

            # Calculate costs
            print(f"\n  Calculating costs for {building_name} lv{current_level} -> lv{final_level}...")
            resources = _get_resources_needed(
                session, city, pos, current_level, final_level
            )

            if resources is None:
                print("  Could not calculate costs. Skipping this building.")
                enter()
                continue
            if resources == [-1] * 5:
                # User cancelled at level cap prompt
                continue

            # Display costs
            print(f"\n  Resources needed for {building_name}:")
            for i, name in enumerate(MATERIALS_NAMES):
                if resources[i] > 0:
                    print(f"    {name}: {addThousandSeparator(resources[i])}")

            for i in range(len(MATERIALS_NAMES)):
                total_resources_needed[i] += resources[i]

            buildings_to_upgrade.append(pos)

        # If nothing to upgrade, we're done
        if not buildings_to_upgrade:
            if not empty_positions:
                print("\nNothing to build or upgrade.")
            enter()
            event.set()
            return

        # Show total cost summary
        if len(buildings_to_upgrade) > 1:
            print("\n  Total resources needed:")
            for i, name in enumerate(MATERIALS_NAMES):
                if total_resources_needed[i] > 0:
                    print(f"    {name}: {addThousandSeparator(total_resources_needed[i])}")

        # Check available resources — refresh city data first
        try:
            html = session.get(CITY_URL + str(city_id))
            city = getCity(html)
        except Exception:
            pass
        available = city.get("availableResources", [0] * 5)
        missing = [0] * len(MATERIALS_NAMES)
        has_missing = False
        for i in range(len(MATERIALS_NAMES)):
            if available[i] < total_resources_needed[i]:
                missing[i] = total_resources_needed[i] - available[i]
                has_missing = True

        transport_plan = None
        wait_resources = False
        if has_missing:
            banner()
            print("  Resources needed for upgrades:")
            for i, name in enumerate(MATERIALS_NAMES):
                if total_resources_needed[i] > 0:
                    print(f"    {name}: {addThousandSeparator(total_resources_needed[i])}")

            print("\n  Available in {}:".format(city.get("cityName", "?")))
            for i, name in enumerate(MATERIALS_NAMES):
                if total_resources_needed[i] > 0:
                    print(f"    {name}: {addThousandSeparator(available[i])}")

            print("\n  Missing:")
            for i in range(len(MATERIALS_NAMES)):
                if missing[i] > 0:
                    print(
                        "    {}: {}".format(
                            MATERIALS_NAMES[i],
                            addThousandSeparator(missing[i]),
                        )
                    )

            print("\n  Automatically transport resources from other cities? [Y/n]")
            rta = read(values=["y", "Y", "n", "N", ""])
            if rta.lower() == "n":
                # No transport — proceed without or cancel
                print("  Proceed with construction anyway? [Y/n]")
                rta2 = read(values=["y", "Y", "n", "N", ""])
                if rta2.lower() == "n":
                    event.set()
                    return
            else:
                # Auto-transport flow
                transport_plan = _handle_missing_resources(
                    session, city, missing
                )
                if transport_plan is None:
                    # User cancelled transport — ask if they want to proceed anyway
                    print("\n  Transport cancelled. Proceed with construction anyway? [Y/n]")
                    rta2 = read(values=["y", "Y", "n", "N", ""])
                    if rta2.lower() == "n":
                        event.set()
                        return
                else:
                    wait_resources = True
        else:
            print("\n  You have enough resources.")
            print("  Proceed? [Y/n]")
            rta = read(values=["y", "Y", "n", "N", ""])
            if rta.lower() == "n":
                event.set()
                return

    except KeyboardInterrupt:
        event.set()
        return

    # --- Transition to background phase ---
    set_child_mode(session)
    event.set()

    # Build status string
    bldg_names = [
        "{} lv{}->{}".format(
            b.get("name", b.get("building", "?")),
            int(b.get("level", 0)) + (1 if b.get("isBusy") else 0),
            b["upgradeTo"],
        )
        for b in buildings_to_upgrade
    ]
    info = "Construction: {} in {}".format(
        ", ".join(bldg_names), city.get("cityName", "?")
    )
    session.setStatus(info)

    try:
        # Execute transport first if planned
        if transport_plan is not None:
            _execute_transport(session, transport_plan)

        for building in buildings_to_upgrade:
            _expand_building(session, city_id, building, wait_resources)
    except Exception:
        msg = "Error in construction:\n{}".format(
            traceback.format_exc().splitlines()[-1]
        )
        logger.exception("constructionManager crashed")
        report_critical_error(session, MODULE_NAME, msg)
