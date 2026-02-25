"""Route execution — send resources between cities.

Ported from ikabot's helpers/planRoutes.py.
"""

import json
import math
import time
from decimal import Decimal

from autoIkabot.config import ACTION_REQUEST_PLACEHOLDER, CITY_URL, MATERIALS_NAMES
from autoIkabot.helpers.game_parser import getCity
from autoIkabot.helpers.naval import (
    getAvailableShips,
    getAvailableFreighters,
    getMinimumWaitingTime,
    getShipCapacity,
    waitForArrival,
)
from autoIkabot.utils.logging import get_logger
from autoIkabot.utils.process import sleep_with_heartbeat

logger = get_logger(__name__)

# Maximum retries for sendGoods before giving up
_SEND_GOODS_MAX_RETRIES = 20


def sendGoods(
    session,
    origin_city_id,
    destination_city_id,
    island_id,
    ships: int,
    send: list,
    useFreighters: bool = False,
) -> None:
    """Execute a single shipment between two cities.

    Parameters
    ----------
    session : Session
        The game session.
    origin_city_id : str or int
        ID of the origin city.
    destination_city_id : str or int
        ID of the destination city.
    island_id : str or int
        ID of the destination island.
    ships : int
        Number of ships to use.
    send : list[int]
        Resources to send [wood, wine, marble, crystal, sulfur].
    useFreighters : bool
        Use freighters instead of trade ships.
    """
    for attempt in range(_SEND_GOODS_MAX_RETRIES):
        try:
            html = session.get()
            current_city = getCity(html)
            city = getCity(session.get(CITY_URL + str(origin_city_id)))
        except Exception as e:
            logger.warning("sendGoods: failed to fetch city data (attempt %d): %s", attempt + 1, e)
            sleep_with_heartbeat(session, 30)
            continue

        curr_id = current_city["id"]

        # Switch to origin city
        data = {
            "action": "header",
            "function": "changeCurrentCity",
            "actionRequest": ACTION_REQUEST_PLACEHOLDER,
            "oldView": "city",
            "cityId": str(origin_city_id),
            "backgroundView": "city",
            "currentCityId": curr_id,
            "ajax": "1",
        }
        session.post(params=data)

        # Build transport request
        data = {
            "action": "transportOperations",
            "function": "loadTransportersWithFreight",
            "destinationCityId": str(destination_city_id),
            "islandId": str(island_id),
            "oldView": "",
            "position": "",
            "avatar2Name": "",
            "city2Name": "",
            "type": "",
            "activeTab": "",
            "transportDisplayPrice": "0",
            "premiumTransporter": "0",
            "capacity": "5",
            "max_capacity": "5",
            "jetPropulsion": "0",
            "backgroundView": "city",
            "currentCityId": str(origin_city_id),
            "templateView": "transport",
            "currentTab": "tabSendTransporter",
            "actionRequest": ACTION_REQUEST_PLACEHOLDER,
            "ajax": "1",
        }

        if useFreighters:
            data["usedFreightersShips"] = ships
            data["transporters"] = "0"
        else:
            data["transporters"] = ships

        # Add resource amounts
        for i in range(len(send)):
            if city["availableResources"][i] > 0:
                key = "cargo_resource" if i == 0 else f"cargo_tradegood{i}"
                data[key] = send[i]

        resp = session.post(params=data)
        try:
            resp_data = json.loads(resp, strict=False)
            if resp_data[3][1][0]["type"] == 10:
                return  # Success
            elif resp_data[3][1][0]["type"] == 11:
                # Need to wait for ships
                wait_time = getMinimumWaitingTime(session)
                if wait_time <= 0:
                    wait_time = 60
                logger.info("Ships busy, waiting %ds before retry", wait_time)
                sleep_with_heartbeat(session, wait_time)
        except (json.JSONDecodeError, IndexError, KeyError):
            logger.warning("Unexpected response from sendGoods (attempt %d), retrying", attempt + 1)

        sleep_with_heartbeat(session, 5)

    raise Exception(f"sendGoods failed after {_SEND_GOODS_MAX_RETRIES} attempts")


def executeRoutes(session, routes: list, useFreighters: bool = False) -> None:
    """Execute a list of resource transport routes.

    Each route is a tuple:
        (origin_city, destination_city, island_id, wood, wine, marble, crystal, sulfur)

    Parameters
    ----------
    session : Session
        The game session.
    routes : list[tuple]
        List of route tuples.
    useFreighters : bool
        Use freighters instead of trade ships.
    """
    ship_capacity, freighter_capacity = getShipCapacity(session)

    for route in routes:
        (origin_city, destination_city, island_id, *toSend) = route
        destination_city_id = destination_city["id"]

        while sum(toSend) > 0:
            ships_available = waitForArrival(session, useFreighters)
            if useFreighters:
                storage_in_ships = ships_available * freighter_capacity
            else:
                storage_in_ships = ships_available * ship_capacity

            # Refresh city data
            html = session.get(CITY_URL + str(origin_city["id"]))
            origin_city = getCity(html)
            html = session.get(CITY_URL + str(destination_city_id))
            destination_city = getCity(html)
            foreign = str(destination_city["id"]) != str(destination_city_id)
            if not foreign:
                storage_in_city = destination_city["freeSpaceForResources"]

            send = []
            for i in range(len(toSend)):
                if not foreign:
                    min_val = min(
                        origin_city["availableResources"][i],
                        toSend[i],
                        storage_in_ships,
                        storage_in_city[i],
                    )
                else:
                    min_val = min(
                        origin_city["availableResources"][i],
                        toSend[i],
                        storage_in_ships,
                    )
                send.append(min_val)
                storage_in_ships -= send[i]
                toSend[i] -= send[i]

            resources_to_send = sum(send)
            if resources_to_send == 0:
                logger.info("No space available, waiting 1 hour")
                sleep_with_heartbeat(session, 60 * 60)
                continue

            capacity = freighter_capacity if useFreighters else ship_capacity
            ships_needed = int(
                math.ceil(Decimal(resources_to_send) / Decimal(capacity))
            )
            sendGoods(
                session,
                origin_city["id"],
                destination_city_id,
                island_id,
                ships_needed,
                send,
                useFreighters,
            )
