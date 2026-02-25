"""Naval helpers — ship availability, capacity, fleet timing.

Ported from ikabot's helpers/naval.py and helpers/pedirInfo.py.
"""

import json
import random
import re
import time

from autoIkabot.config import ACTION_REQUEST_PLACEHOLDER
from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)


def getAvailableShips(session) -> int:
    """Return the number of free merchant (trade) ships.

    Parameters
    ----------
    session : Session
        The game session.

    Returns
    -------
    int
        Number of available trade ships.
    """
    html = session.get()
    match = re.search(r'GlobalMenu_freeTransporters">(\d+)<', html)
    return int(match.group(1)) if match else 0


def getAvailableFreighters(session) -> int:
    """Return the number of free freighter ships.

    Parameters
    ----------
    session : Session
        The game session.

    Returns
    -------
    int
        Number of available freighters.
    """
    html = session.get()
    match = re.search(r'GlobalMenu_freeFreighters">(\d+)<', html)
    return int(match.group(1)) if match else 0


def getShipCapacity(session) -> tuple:
    """Get per-ship cargo capacity for trade ships and freighters.

    Parameters
    ----------
    session : Session
        The game session.

    Returns
    -------
    tuple[int, int]
        (trade_ship_capacity, freighter_capacity).
    """
    html = session.post("view=merchantNavy")
    try:
        ship_capacity = html.split('singleTransporterCapacity":')[1].split(
            ',"singleFreighterCapacity'
        )[0]
        freighter_capacity = html.split('singleFreighterCapacity":')[1].split(
            ',"draftEffect'
        )[0]
        return int(ship_capacity), int(freighter_capacity)
    except (IndexError, ValueError):
        logger.warning("Could not parse ship capacity, using defaults")
        return 500, 500


def getMinimumWaitingTime(session) -> int:
    """Return seconds until the nearest fleet arrives.

    Adds a small random offset to avoid race conditions.

    Parameters
    ----------
    session : Session
        The game session.

    Returns
    -------
    int
        Seconds to wait (0 if no fleets in transit).
    """
    html = session.get()
    city_id_match = re.search(r"currentCityId:\s(\d+),", html)
    if city_id_match is None:
        return 0
    city_id = city_id_match.group(1)

    url = (
        "view=militaryAdvisor&oldView=city&oldBackgroundView=city"
        "&backgroundView=city&currentCityId={}&actionRequest={}&ajax=1"
    ).format(city_id, ACTION_REQUEST_PLACEHOLDER)
    posted = session.post(url)

    try:
        postdata = json.loads(posted, strict=False)
        movements = postdata[1][1][2]["viewScriptParams"][
            "militaryAndFleetMovements"
        ]
        current_time = int(postdata[0][1]["time"])
        delivered_times = []
        for mv in movements:
            if mv.get("isOwnArmyOrFleet"):
                remaining = int(mv["eventTime"]) - current_time
                delivered_times.append(remaining)
        if delivered_times:
            return min(delivered_times) + random.randint(0, 60)
    except Exception:
        logger.warning("Could not parse fleet movements for wait time")

    return 0


def waitForArrival(session, useFreighters: bool = False, max_wait: int = 7200) -> int:
    """Wait until at least one ship is available, then return the count.

    Parameters
    ----------
    session : Session
        The game session.
    useFreighters : bool
        If True, wait for freighters; otherwise trade ships.
    max_wait : int
        Maximum seconds to wait before returning 0 (default 2 hours).

    Returns
    -------
    int
        Number of available ships once at least one arrives, or 0 on timeout.
    """
    getter = getAvailableFreighters if useFreighters else getAvailableShips
    available = getter(session)
    start = time.time()
    while available == 0:
        if time.time() - start > max_wait:
            logger.warning("waitForArrival timed out after %ds", max_wait)
            return 0
        wait_time = getMinimumWaitingTime(session)
        if wait_time <= 0:
            wait_time = 60
        logger.info("No ships available, waiting %ds", wait_time)
        time.sleep(wait_time)
        available = getter(session)
    return available
