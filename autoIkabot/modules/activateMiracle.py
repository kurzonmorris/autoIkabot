"""Activate Miracle v1.0 — autoIkabot module.

Activates a wonder (miracle) on one of the player's islands. Supports
repeated activation with automatic cooldown waiting.

Ported from ikabot's function/activateMiracle.py, adapted for
autoIkabot's background module architecture.
"""

import json
import os
import re
import sys
import traceback

from autoIkabot.config import (
    ACTION_REQUEST_PLACEHOLDER,
    CITY_URL,
    ISLAND_URL,
)
from autoIkabot.helpers.formatting import daysHoursMinutes, getDateTime
from autoIkabot.helpers.game_parser import (
    getCity,
    getIdsOfCities,
    getIsland,
    getIslandsIds,
)
from autoIkabot.ui.prompts import banner, enter, read
from autoIkabot.utils.logging import get_logger
from autoIkabot.utils.process import (
    report_critical_error,
    set_child_mode,
    sleep_with_heartbeat,
)

logger = get_logger(__name__)

# --- Module Metadata ---
MODULE_NAME = "Activate Miracle"
MODULE_SECTION = "Regular/Daily"
MODULE_NUMBER = 11
MODULE_DESCRIPTION = "Activate a miracle on repeat"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def obtainMiraclesAvailable(session):
    """Discover which miracles the player can activate.

    Scans all islands the player has cities on, checks for temples,
    and queries the temple view to determine wonder availability and
    cooldown status.

    Parameters
    ----------
    session : Session

    Returns
    -------
    list[dict]
        Islands with activable wonders, each augmented with
        ``ciudad``, ``available``, and optionally ``available_in``.
    """
    idsIslands = getIslandsIds(session)
    islands = []
    for idIsland in idsIslands:
        html = session.get(ISLAND_URL + idIsland)
        island = getIsland(html)
        island["activable"] = False
        islands.append(island)

    ids, cities = getIdsOfCities(session)
    for city_id in cities:
        city = cities[city_id]
        # Get the wonder for this city's island
        matching = [
            island["wonder"]
            for island in islands
            if city.get("coords") == "[{}:{}] ".format(island["x"], island["y"])
        ]
        if not matching:
            continue
        wonder = matching[0]

        # Skip if we already have this wonder type covered
        if wonder in [island["wonder"] for island in islands if island["activable"]]:
            continue

        html = session.get(CITY_URL + str(city["id"]))
        city = getCity(html)

        # Check that the city has a temple building
        temple_pos = None
        for i in range(len(city.get("position", []))):
            if city["position"][i].get("building") == "temple":
                temple_pos = str(i)
                break
        if temple_pos is None:
            continue

        city["pos"] = temple_pos

        # Query temple view for wonder status
        params = {
            "view": "temple",
            "cityId": city["id"],
            "position": city["pos"],
            "backgroundView": "city",
            "currentCityId": city["id"],
            "actionRequest": ACTION_REQUEST_PLACEHOLDER,
            "ajax": "1",
        }
        data = session.post(params=params)
        data = json.loads(data, strict=False)

        html_fragment = data[1][1][1]
        match = re.search(
            r'<div id="wonderLevelDisplay"[^>]*>\\n\s*(\d+)\s*</div>',
            html_fragment,
        )
        level = int(match.group(1)) if match else 0

        wonder_data = data[2][1]
        available = wonder_data["js_WonderViewButton"]["buttonState"] == "enabled"

        enddate = None
        currentdate = None
        if not available:
            for elem in wonder_data:
                if isinstance(wonder_data[elem], dict) and "countdown" in wonder_data[elem]:
                    enddate = wonder_data[elem]["countdown"]["enddate"]
                    currentdate = wonder_data[elem]["countdown"]["currentdate"]
                    break

        # Annotate the matching island
        for island in islands:
            if island["id"] == city["islandId"]:
                island["activable"] = True
                island["ciudad"] = city
                island["wonderActivationLevel"] = level
                island["available"] = available
                if not available and enddate is not None:
                    island["available_in"] = int(float(enddate)) - int(float(currentdate))
                break

    return [island for island in islands if island["activable"]]


def activateMiracleHttpCall(session, island):
    """Send the HTTP request to activate a wonder.

    Parameters
    ----------
    session : Session
    island : dict

    Returns
    -------
    list
        Parsed JSON response from the server.
    """
    params = {
        "action": "CityScreen",
        "cityId": island["ciudad"]["id"],
        "function": "activateWonder",
        "position": island["ciudad"]["pos"],
        "backgroundView": "city",
        "currentCityId": island["ciudad"]["id"],
        "templateView": "temple",
        "actionRequest": ACTION_REQUEST_PLACEHOLDER,
        "ajax": "1",
    }
    response = session.post(params=params)
    return json.loads(response, strict=False)


def chooseIsland(islands):
    """Display available miracles and let the user choose one.

    Parameters
    ----------
    islands : list[dict]

    Returns
    -------
    dict or None
        The chosen island, or None if the user chose to exit.
    """
    print("Which miracle do you want to activate?")
    sorted_islands = sorted(islands, key=lambda x: x.get("wonderName", ""))
    print("(0) Exit")
    for i, island in enumerate(sorted_islands, 1):
        if island["available"]:
            print("({:d}) {}".format(i, island["wonderName"]))
        else:
            print(
                "({:d}) {} (available in: {})".format(
                    i, island["wonderName"], daysHoursMinutes(island["available_in"])
                )
            )

    index = read(min=0, max=len(sorted_islands))
    if index == 0:
        return None
    return sorted_islands[index - 1]


# ---------------------------------------------------------------------------
# Background loop functions
# ---------------------------------------------------------------------------

def wait_for_miracle(session, island):
    """Block until the miracle is ready to be activated.

    Polls the temple view endpoint periodically and returns once the
    wonder button state becomes ``"enabled"``.

    Parameters
    ----------
    session : Session
    island : dict
    """
    while True:
        params = {
            "view": "temple",
            "cityId": island["ciudad"]["id"],
            "position": island["ciudad"]["pos"],
            "backgroundView": "city",
            "currentCityId": island["ciudad"]["id"],
            "actionRequest": ACTION_REQUEST_PLACEHOLDER,
            "ajax": "1",
        }
        temple_response = session.post(params=params)
        temple_response = json.loads(temple_response, strict=False)
        temple_response = temple_response[2][1]

        wait_time = None
        for elem in temple_response:
            if isinstance(temple_response[elem], dict) and "countdown" in temple_response[elem]:
                enddate = temple_response[elem]["countdown"]["enddate"]
                currentdate = temple_response[elem]["countdown"]["currentdate"]
                wait_time = int(float(enddate)) - int(float(currentdate))
                next_activation_time = __import__("time").time() + wait_time
                session.setStatus(
                    "Miracle {} activated. Available at: {}".format(
                        island["wonderName"], getDateTime(next_activation_time)
                    )
                )
                break

        if wait_time is None:
            available = (
                temple_response.get("js_WonderViewButton", {}).get("buttonState")
                == "enabled"
            )
            if available:
                return
            wait_time = 60

        logger.debug(
            "Waiting %d seconds to activate miracle %s",
            wait_time + 5, island["wonderName"],
        )
        sleep_with_heartbeat(session, wait_time + 5)


def do_it(session, island, iterations):
    """Activate the miracle *iterations* times, waiting between each.

    Parameters
    ----------
    session : Session
    island : dict
    iterations : int
    """
    iterations_left = iterations
    session.setStatus("Waiting to activate {}...".format(island["wonderName"]))

    for i in range(iterations):
        wait_for_miracle(session, island)

        response = activateMiracleHttpCall(session, island)

        if response[1][1][0] == "error":
            msg = "The miracle {} could not be activated.".format(
                island["wonderName"]
            )
            logger.error(msg)
            report_critical_error(session, MODULE_NAME, msg)
            return

        iterations_left -= 1
        session.setStatus(
            "Activated {} @{}, iterations left: {}".format(
                island["wonderName"], getDateTime(), iterations_left
            )
        )
        logger.info("Miracle %s activated successfully", island["wonderName"])


# ---------------------------------------------------------------------------
# Main entry point (background module)
# ---------------------------------------------------------------------------

def activateMiracle(session, event, stdin_fd):
    """Activate Miracle module entry point.

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

        islands = obtainMiraclesAvailable(session)
        if not islands:
            print("There are no miracles available.")
            enter()
            event.set()
            return

        island = chooseIsland(islands)
        if island is None:
            event.set()
            return

        if island["available"]:
            print("\nThe miracle {} will be activated".format(island["wonderName"]))
            print("Proceed? [Y/n]")
            confirm = read(values=["y", "Y", "n", "N", ""])
            if confirm.lower() == "n":
                event.set()
                return

            result = activateMiracleHttpCall(session, island)

            if result[1][1][0] == "error":
                print(
                    "The miracle {} could not be activated.".format(
                        island["wonderName"]
                    )
                )
                enter()
                event.set()
                return

            # Extract cooldown from response
            data = result[2][1]
            wait_time = 0
            for elem in data:
                if isinstance(data[elem], dict) and "countdown" in data[elem]:
                    enddate = data[elem]["countdown"]["enddate"]
                    currentdate = data[elem]["countdown"]["currentdate"]
                    wait_time = int(float(enddate)) - int(float(currentdate))
                    break

            print("The miracle {} was activated.".format(island["wonderName"]))
            enter()
            banner()

            while True:
                print("Do you wish to activate it again when it is finished? [y/N]")
                reactivate = read(values=["y", "Y", "n", "N", ""])
                if reactivate.lower() != "y":
                    event.set()
                    return

                iterations = read(msg="How many times?: ", digit=True, min=0)
                if iterations == 0:
                    event.set()
                    return

                duration = wait_time * iterations
                print("It will finish in: {}".format(daysHoursMinutes(duration)))
                print("Proceed? [Y/n]")
                proceed = read(values=["y", "Y", "n", "N", ""])
                if proceed.lower() == "n":
                    banner()
                    continue
                break
        else:
            print(
                "\nThe miracle {} will be activated in {}".format(
                    island["wonderName"], daysHoursMinutes(island["available_in"])
                )
            )
            print("Proceed? [Y/n]")
            confirm = read(values=["y", "Y", "n", "N", ""])
            if confirm.lower() == "n":
                event.set()
                return

            wait_time = island["available_in"]
            iterations = 1

            print("\nThe miracle will be activated.")
            enter()
            banner()

            while True:
                print("Do you wish to activate it again when it is finished? [y/N]")
                reactivate = read(values=["y", "Y", "n", "N", ""])
                again = reactivate.lower() == "y"
                if again:
                    try:
                        extra = read(msg="How many times?: ", digit=True, min=0)
                    except KeyboardInterrupt:
                        iterations = 1
                        break

                    if extra == 0:
                        iterations = 1
                        break

                    iterations = extra + 1
                    duration = wait_time * iterations
                    print(
                        "Estimated minimum duration: {}".format(
                            daysHoursMinutes(duration)
                        )
                    )
                    print("Proceed? [Y/n]")

                    try:
                        proceed = read(values=["y", "Y", "n", "N", ""])
                    except KeyboardInterrupt:
                        iterations = 1
                        break

                    if proceed.lower() == "n":
                        iterations = 1
                        banner()
                        continue
                break
    except KeyboardInterrupt:
        event.set()
        return

    set_child_mode(session)
    event.set()

    info = "Activate miracle {} {:d} times".format(island["wonderName"], iterations)
    session.setStatus(info)

    try:
        do_it(session, island, iterations)
    except Exception:
        msg = "Error activating miracle:\n{}".format(
            traceback.format_exc().splitlines()[-1]
        )
        logger.exception("activateMiracle crashed")
        report_critical_error(session, MODULE_NAME, msg)
