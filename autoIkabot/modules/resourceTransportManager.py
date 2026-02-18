"""Resource Transport Manager v1.0 — autoIkabot module.

Three shipping modes:
  1. Consolidate: Multiple source cities -> One destination
  2. Distribute: One source city -> Multiple destinations
  3. Even Distribution: Balance one resource across all cities

Originally written for ikabot, refactored for autoIkabot's module system.
"""

import datetime
import json
import math
import os
import time
import traceback
from decimal import Decimal

from autoIkabot.config import CITY_URL, ISLAND_URL, MATERIALS_NAMES
from autoIkabot.helpers.formatting import addThousandSeparator, getDateTime
from autoIkabot.helpers.game_parser import getCity, getIsland, getIdsOfCities
from autoIkabot.helpers.naval import (
    getAvailableFreighters,
    getAvailableShips,
    getShipCapacity,
)
from autoIkabot.helpers.routing import executeRoutes
from autoIkabot.notifications.notify import checkTelegramData, sendToBot
from autoIkabot.ui.prompts import banner, chooseCity, enter, ignoreCities, read
from autoIkabot.utils.logging import get_logger
from autoIkabot.utils.process import report_critical_error

logger = get_logger(__name__)

# Module registration metadata (used by ui/menu.py)
MODULE_NAME = "Resource Transport Manager"
MODULE_SECTION = "Transport"
MODULE_NUMBER = 3
MODULE_DESCRIPTION = "Resource Transport Manager"


def print_module_banner(mode_name=None, mode_description=None):
    """Print the Resource Transport Manager banner."""
    print("\n")
    print("+" + "=" * 58 + "+")
    print("|       RESOURCE TRANSPORT MANAGER v1.0" + " " * 20 + "|")

    if mode_name:
        print("|" + "-" * 58 + "|")
        mode_line = f"| {mode_name:^56} |"
        print(mode_line)

        if mode_description:
            desc_line = f"| {mode_description:^56} |"
            print(desc_line)

    print("+" + "=" * 58 + "+")
    print("")


def get_lock_file_path(session, use_freighters=False):
    """Get the path to the shared shipping lock file."""
    ship_type = "freighters" if use_freighters else "merchant_ships"
    safe_server = session.servidor.replace('/', '_').replace('\\', '_')
    safe_username = session.username.replace('/', '_').replace('\\', '_')
    lock_filename = f".autoikabot_shared_{ship_type}_{safe_server}_{safe_username}.lock"
    return os.path.join(os.path.expanduser("~"), lock_filename)


def acquire_shipping_lock(session, use_freighters=False, timeout=300):
    """Try to acquire shipping lock, wait up to timeout seconds."""
    lock_file = get_lock_file_path(session, use_freighters)
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, 'w') as f:
                lock_data = {
                    'pid': os.getpid(),
                    'timestamp': time.time(),
                    'ship_type': 'freighters' if use_freighters else 'merchant_ships',
                    'server': session.servidor,
                    'username': session.username
                }
                json.dump(lock_data, f)
            return True
        except FileExistsError:
            try:
                with open(lock_file, 'r') as f:
                    lock_data = json.load(f)
                    if time.time() - lock_data['timestamp'] > 600:
                        os.remove(lock_file)
                        continue
            except Exception:
                try:
                    os.remove(lock_file)
                except Exception:
                    pass
                continue
        except Exception:
            pass

        time.sleep(5)

    return False


def release_shipping_lock(session, use_freighters=False):
    """Release the shipping lock."""
    lock_file = get_lock_file_path(session, use_freighters)
    try:
        if os.path.exists(lock_file):
            try:
                with open(lock_file, 'r') as f:
                    lock_data = json.load(f)
                    if lock_data['pid'] == os.getpid():
                        os.remove(lock_file)
            except Exception:
                try:
                    os.remove(lock_file)
                except Exception:
                    pass
    except Exception:
        pass


def readResourceAmount(resource_name):
    """Read a resource amount with validation.

    Returns None (ignore), int, 'EXIT', or 'RESTART'.
    """
    while True:
        user_input = read(msg=f"{resource_name}: ", empty=True, additionalValues=["'", "="])

        if user_input == "'":
            return 'EXIT'
        if user_input == "=":
            return 'RESTART'
        if user_input == "":
            return None

        cleaned_input = user_input.replace(",", "").replace(" ", "")

        if cleaned_input.isdigit():
            amount = int(cleaned_input)
            if amount > 0:
                print(f"  -> Set to: {addThousandSeparator(amount)}")
            return amount
        else:
            print("  Please enter a number, 0, leave blank, or press ' to exit")


# ---------------------------------------------------------------------------
# Main entry point (called by the menu system)
# ---------------------------------------------------------------------------

def resourceTransportManager(session, event, stdin_fd):
    """Resource Transport Manager — background module entry point.

    Spawned as a child process by the menu's background dispatch.
    Handles the interactive config phase, then signals the parent and
    continues running the shipment loop in the background.

    Parameters
    ----------
    session : autoIkabot.web.session.Session
    event : multiprocessing.Event
        Signalled after config is done to return control to menu.
    stdin_fd : int
        File descriptor for stdin from the parent process.
    """
    import sys
    sys.stdin = os.fdopen(stdin_fd)

    try:
        telegram_enabled = checkTelegramData(session)
        if telegram_enabled is False:
            print_module_banner()
            print("Telegram notifications are not configured.")
            print("Do you want to continue without notifications? [Y/n]")
            rta = read(values=["y", "Y", "n", "N", ""])
            if rta.lower() == "n":
                event.set()
                return
            telegram_enabled = None

        print_module_banner("Shipping Mode Selection")

        print("Select shipping mode:")
        print("(1) Consolidate/Single Shipments: Multiple cities -> One destination")
        print("(2) Distribute: One city -> Multiple destinations")
        print("(3) Even Distribution: Balance one resource across all cities")
        print("(') Back to main menu")
        shipping_mode = read(min=1, max=3, digit=True, additionalValues=["'"])
        if shipping_mode == "'":
            event.set()
            return

        if shipping_mode == 1:
            config = consolidateMode(session, telegram_enabled)
        elif shipping_mode == 2:
            config = distributeMode(session, telegram_enabled)
        else:
            config = evenDistributionMode(session, telegram_enabled)

        if config is None:
            # User cancelled during config
            event.set()
            return

        # --- Hand off: config done, switch to background ---
        from autoIkabot.utils.process import set_child_mode
        set_child_mode(session)
        event.set()

        # --- Background phase: no user interaction from here ---
        info = config["info"]
        logger.info(info.strip())
        try:
            config["run_func"]()
        except Exception:
            msg = "Error in:\n{}\nCause:\n{}".format(info, traceback.format_exc())
            sendToBot(session, msg)
            logger.exception("Error in resourceTransportManager background phase")
            report_critical_error(
                session,
                MODULE_NAME,
                f"Module crashed and stopped.\n{traceback.format_exc().splitlines()[-1]}",
            )

    except KeyboardInterrupt:
        event.set()
        return
    except Exception:
        event.set()
        raise


def consolidateMode(session, telegram_enabled):
    """Multiple source cities -> Single destination city."""
    try:
        print_module_banner("Consolidate Resources", "Send resources from multiple cities to a single destination")

        print("What type of ships do you want to use?")
        print("(1) Merchant ships")
        print("(2) Freighters")
        print("(') Exit to main menu")
        shiptype = read(min=1, max=2, digit=True, additionalValues=["'"])
        if shiptype == "'":
            return
        useFreighters = (shiptype == 2)

        print_module_banner("Consolidate Resources", "Send resources from multiple cities to a single destination")

        print("Select source city option:")
        print("(1) Single city")
        print("(2) Multiple cities")
        print("(') Exit to main menu")
        source_option = read(min=1, max=2, digit=True, additionalValues=["'"])
        if source_option == "'":
            return

        origin_cities = []
        if source_option == 1:
            print_module_banner("Consolidate Resources", "Send resources from multiple cities to a single destination")
            print("Select source city:")
            print("Island Luxury: (W) Wine | (M) Marble | (C) Crystal | (S) Sulfur")
            print("")
            origin_city = chooseCity(session)
            origin_cities.append(origin_city)
        else:
            print_module_banner("Consolidate Resources", "Send resources from multiple cities to a single destination")
            source_msg = 'Select source cities (cities to send resources from):'
            source_city_ids, source_cities_dict = ignoreCities(session, msg=source_msg)

            if not source_city_ids:
                print("No cities selected!")
                enter()
                return

            for city_id in source_city_ids:
                html = session.get(CITY_URL + city_id)
                city = getCity(html)
                origin_cities.append(city)

        print_module_banner("Consolidate Resources", "Send resources from multiple cities to a single destination")

        if len(origin_cities) == 1:
            source_cities_summary = origin_cities[0]['name']
        else:
            source_cities_summary = ', '.join([city['name'] for city in origin_cities])

        print(f"Source cities: {source_cities_summary}")
        print("")
        print("Choose sending mode:")
        print("(1) Send ALL resources EXCEPT a reserve amount (keep X, send rest)")
        print("(2) Send SPECIFIC amounts (send exactly X)")
        print("(') Exit to main menu")
        send_mode = read(min=1, max=2, digit=True, additionalValues=["'"])
        if send_mode == "'":
            return

        print_module_banner("Consolidate Resources", "Send resources from multiple cities to a single destination")
        print(f"Source cities: {source_cities_summary}")
        print("")

        if send_mode == 1:
            print("Configure resource reserves (KEEP mode):")
            print("(Enter a number to keep that amount in reserve)")
            print("(Enter 0 to send ALL of that resource)")
            print("(Leave blank to IGNORE that resource - won't send it)")
            print("(You can type with or without commas - e.g., 6000 or 6,000)")
            print("(Press '=' to restart resource configuration from beginning)")
            print("(Press ' to exit to main menu)")
            print("")
        else:
            print("Configure resource amounts to send (SEND mode):")
            print("(Enter a number to send that specific amount)")
            print("(Enter 0 or leave blank to NOT send that resource)")
            print("(You can type with or without commas - e.g., 6000 or 6,000)")
            print("(Press '=' to restart resource configuration from beginning)")
            print("(Press ' to exit to main menu)")
            print("")

            if len(origin_cities) == 1:
                html = session.get(CITY_URL + str(origin_cities[0]['id']))
                single_city_data = getCity(html)
                print(f"Available resources in {origin_cities[0]['name']}:")
                header = "  "
                for resource in MATERIALS_NAMES:
                    header += f"{resource:>12}  "
                print(header)
                separator = "  "
                for _ in MATERIALS_NAMES:
                    separator += f"{'-'*12}  "
                print(separator)
                amounts = "  "
                for i in range(len(MATERIALS_NAMES)):
                    amount = single_city_data['availableResources'][i]
                    amounts += f"{addThousandSeparator(amount):>12}  "
                print(amounts)
                print("")

        # Get resource config (with restart support)
        resource_config_complete = False
        while not resource_config_complete:
            resource_config = []
            restart = False

            for i, resource in enumerate(MATERIALS_NAMES):
                amount = readResourceAmount(resource)

                if amount == 'EXIT':
                    return

                if amount == 'RESTART':
                    print("\nRestarting resource configuration...\n")
                    restart = True
                    break

                resource_config.append(amount)

            if not restart:
                resource_config_complete = True

        print_module_banner("Consolidate Resources", "Send resources from multiple cities to a single destination")
        print(f"Source cities: {source_cities_summary}")
        print("")
        print("Resource configuration:")
        if send_mode == 1:
            for i, resource in enumerate(MATERIALS_NAMES):
                if resource_config[i] is None:
                    print(f"  {resource}: IGNORED (won't send)")
                elif resource_config[i] == 0:
                    print(f"  {resource}: Send ALL")
                else:
                    print(f"  {resource}: Keep {addThousandSeparator(resource_config[i])}, send excess")
        else:
            for i, resource in enumerate(MATERIALS_NAMES):
                if resource_config[i] is None or resource_config[i] == 0:
                    print(f"  {resource}: NOT sending")
                else:
                    print(f"  {resource}: Send {addThousandSeparator(resource_config[i])}")
        print("")

        print_module_banner("Consolidate Resources", "Send resources from multiple cities to a single destination")

        print(f"Source cities: {source_cities_summary}")
        print("")
        print("Select destination type:")
        print("(1) Internal city (choose from your cities)")
        print("(2) External city (enter island coordinates)")
        print("(') Exit to main menu")
        destination_type = read(min=1, max=2, digit=True, additionalValues=["'"])
        if destination_type == "'":
            return

        if destination_type == 2:
            coords_complete = False
            while not coords_complete:
                print_module_banner("Consolidate Resources", "Send resources from multiple cities to a single destination")
                print("Enter destination island coordinates:")
                print("(Press '=' to restart coordinate entry)")
                print("(Press ' at any prompt to exit to main menu)")

                x_coord = read(msg="X coordinate: ", digit=True, additionalValues=["'", "="])
                if x_coord == "'":
                    return

                if x_coord == "=":
                    print("\nRestarting coordinate entry...\n")
                    continue

                y_coord = read(msg="Y coordinate: ", digit=True, additionalValues=["'", "="])
                if y_coord == "'":
                    return

                if y_coord == "=":
                    print("\nRestarting coordinate entry...\n")
                    continue

                island_coords = f"xcoord={x_coord}&ycoord={y_coord}"
                html = session.get(f"view=island&{island_coords}")
                island = getIsland(html)

                cities_on_island = [city for city in island["cities"] if city["type"] == "city"]

                if len(cities_on_island) == 0:
                    print(f"Island {x_coord}:{y_coord} has no cities!")
                    enter()
                    continue

                print("")
                print(f"Island: {island['name']} [{island['x']}:{island['y']}]")
                print(f"Resource: {MATERIALS_NAMES[int(island['tradegood'])]}")
                print("")
                print("Select destination city:")
                print("(0) Exit")
                print("(=) Restart coordinate entry")
                print("(') Exit to main menu")
                print("")

                print(f"    {'City Name':<20} {'Player':<15}")
                print(f"    {'-'*20} {'-'*15}")

                for i, city in enumerate(cities_on_island):
                    city_num = i + 1
                    player_name = city.get('Name', 'Unknown')
                    city_name = city.get('name', 'Unknown')

                    if len(city_name) > 20:
                        city_name_display = city_name[:17] + "..."
                    else:
                        city_name_display = city_name

                    if len(player_name) > 15:
                        player_name_display = player_name[:12] + "..."
                    else:
                        player_name_display = player_name

                    print(f"({city_num:>2}) {city_name_display:<20} {player_name_display:<15}")

                print("")
                city_choice = read(min=0, max=len(cities_on_island), additionalValues=["'", "="])

                if city_choice == 0 or city_choice == "'":
                    return

                if city_choice == "=":
                    print("\nRestarting coordinate entry...\n")
                    continue

                destination_city_data = cities_on_island[city_choice - 1]
                destination_city_id = destination_city_data["id"]

                html = session.get(CITY_URL + str(destination_city_id))
                destination_city = getCity(html)
                destination_city["isOwnCity"] = (
                    destination_city_data.get("state", "") == ""
                    and destination_city_data.get("Name", "") == session.username
                )

                print("")
                print(f"Selected: {destination_city['name']}")
                print(f"Player: {destination_city_data.get('Name', 'Unknown')}")
                print(f"Island: {island['name']} [{island['x']}:{island['y']}]")
                print("")
                print("Confirm this destination? [Y/n]")
                print("(Press '=' to restart coordinate entry)")
                confirm = read(values=["y", "Y", "n", "N", "", "="])

                if confirm == "=":
                    print("\nRestarting coordinate entry...\n")
                    continue

                if confirm.lower() == "n":
                    print("\nReselecting city...\n")
                    continue

                coords_complete = True

        else:
            print_module_banner("Internal City Selection")
            print("Select destination city from your cities:")
            print("Island Luxury: (W) Wine | (M) Marble | (C) Crystal | (S) Sulfur")
            print("")
            destination_city = chooseCity(session)

            html = session.get(CITY_URL + str(destination_city['id']))
            destination_city = getCity(html)
            island_id = destination_city['islandId']

            html = session.get(ISLAND_URL + island_id)
            island = getIsland(html)

            destination_city["isOwnCity"] = True

        if destination_type == 2:
            player_name = destination_city_data.get('Name', 'Unknown')
        else:
            player_name = session.username

        print(f"Destination city: {destination_city['name']} (Player: {player_name})")
        print(f"Island: {island['name']} [{island['x']}:{island['y']}]")
        print("")

        # Auto-exclude destination from origins
        original_count = len(origin_cities)
        origin_cities = [city for city in origin_cities if city['id'] != destination_city['id']]
        excluded_count = original_count - len(origin_cities)

        if excluded_count > 0:
            print(f"  Automatically excluded destination city '{destination_city['name']}' from source cities")
            print("")

        if len(origin_cities) == 0:
            print("Error: No source cities remaining after excluding destination!")
            print("The destination city was your only source city.")
            enter()
            return

        # Notification preferences
        if telegram_enabled is None:
            notify_on_start = False
        else:
            print_module_banner("Notification Preferences")
            print("When do you want to receive Telegram notifications?")
            print("(1) Partial - When new scheduled shipment is dispatched")
            print("(2) All - Every Individual Shipment")
            print("(3) None - No notifications")
            print("(') Back to main menu")
            notif_choice = read(min=1, max=3, digit=True, additionalValues=["'"])
            if notif_choice == "'":
                return

            if notif_choice == 1:
                telegram_enabled = None
                notify_on_start = True
            elif notif_choice == 2:
                telegram_enabled = True
                notify_on_start = True
            else:
                telegram_enabled = None
                notify_on_start = False

        print_module_banner("Schedule Configuration")

        interval_confirmed = False
        while not interval_confirmed:
            print("How often should resources be sent (in hours)?")
            print("(0 for one-time shipment, or minimum every (1) hour for recurring)")
            print("(Press ' to return to main menu)")
            interval_hours = read(min=0, digit=True, additionalValues=["'"])
            if interval_hours == "'":
                return

            print("")
            if interval_hours == 0:
                print("You entered: One-time shipment (no recurring)")
            else:
                print(f"You entered: Every {interval_hours} hour(s)")
            print("(1) Confirm")
            print("(2) Retry - enter different time")
            confirm_choice = read(min=1, max=2, digit=True)

            if confirm_choice == 1:
                interval_confirmed = True

        print_module_banner("Configuration Summary")

        # Calculate total resources
        total_resources_to_send = [0] * len(MATERIALS_NAMES)
        grand_total = 0

        for origin_city in origin_cities:
            html = session.get(CITY_URL + str(origin_city['id']))
            origin_city_data = getCity(html)

            for i, resource in enumerate(MATERIALS_NAMES):
                if resource_config[i] is None:
                    continue

                available = origin_city_data['availableResources'][i]

                if send_mode == 1:
                    if resource_config[i] == 0:
                        sendable = available
                    else:
                        sendable = max(0, available - resource_config[i])
                else:
                    if resource_config[i] == 0:
                        sendable = 0
                    else:
                        sendable = min(resource_config[i], available)

                total_resources_to_send[i] += sendable
                grand_total += sendable

        print("Configuration:")
        print(f"  Ship type: {'Freighters' if useFreighters else 'Merchant ships'}")
        print(f"  Mode: {'Send all except reserves' if send_mode == 1 else 'Send specific amounts'}")
        print("")
        print(f"  Source cities ({len(origin_cities)}):")
        for city in origin_cities:
            print(f"    - {city['name']}")
        print("")
        print("  Destination:")
        print(f"    - {destination_city['name']} on island {island['x']}:{island['y']}")
        print("")
        print("  Resource Configuration:")
        if send_mode == 1:
            for i, resource in enumerate(MATERIALS_NAMES):
                if resource_config[i] is None:
                    print(f"    {resource:<10} IGNORED")
                elif resource_config[i] == 0:
                    print(f"    {resource:<10} Send ALL")
                else:
                    print(f"    {resource:<10} Keep {addThousandSeparator(resource_config[i])}")
        else:
            for i, resource in enumerate(MATERIALS_NAMES):
                if resource_config[i] is None or resource_config[i] == 0:
                    print(f"    {resource:<10} NOT sending")
                else:
                    print(f"    {resource:<10} Send {addThousandSeparator(resource_config[i])}")

        print("")
        print("  Total Resources to Send:")
        print(f"    {'Resource':<10} {'Amount':>15}")
        print(f"    {'-'*10} {'-'*15}")
        for i, resource in enumerate(MATERIALS_NAMES):
            if total_resources_to_send[i] > 0:
                print(f"    {resource:<10} {addThousandSeparator(total_resources_to_send[i]):>15}")
        print(f"    {'-'*10} {'-'*15}")
        print(f"    {'TOTAL':<10} {addThousandSeparator(grand_total):>15}")

        print("")
        print(f"  Interval: {interval_hours} hour(s)" if interval_hours > 0 else "  Mode: One-time shipment")

        print("")
        print("Proceed? [Y/n]")
        rta = read(values=["y", "Y", "n", "N", ""])
        if rta.lower() == "n":
            return

        enter()

    except KeyboardInterrupt:
        return

    info = f"\nAuto-send resources from {source_cities_summary} to {destination_city['name']} every {interval_hours} hour(s)\n"

    return {
        "info": info,
        "run_func": lambda: do_it(session, origin_cities, destination_city, island, interval_hours, resource_config, useFreighters, send_mode, telegram_enabled, notify_on_start),
    }


def distributeMode(session, telegram_enabled):
    """Single source city -> Multiple destination cities."""
    try:
        print_module_banner("Distribute Resources", "Send resources from one city to multiple destinations")
        print("")

        print("What type of ships do you want to use?")
        print("(1) Merchant ships")
        print("(2) Freighters")
        print("(') Exit to main menu")
        shiptype = read(min=1, max=2, digit=True, additionalValues=["'"])
        if shiptype == "'":
            return
        useFreighters = (shiptype == 2)

        print_module_banner("Distribute Resources", "Send resources from one city to multiple destinations")
        print("")

        print("Select source city:")
        print("Island Luxury: (W) Wine | (M) Marble | (C) Crystal | (S) Sulfur")
        print("")
        origin_city = chooseCity(session)

        print_module_banner("Distribute Resources", "Send resources from one city to multiple destinations")
        print("")

        print(f"Source city: {origin_city['name']}")
        print("")
        print("Note: Source city will be automatically excluded from destinations")
        print("")
        dest_msg = 'Select destination cities (cities to receive resources):'
        destination_city_ids, destination_cities_dict = ignoreCities(session, msg=dest_msg)

        source_city_id = str(origin_city['id'])
        if source_city_id in destination_city_ids:
            destination_city_ids.remove(source_city_id)
            print(f"Removed {origin_city['name']} from destinations (source city cannot send to itself)")

        if not destination_city_ids:
            print("No valid destination cities selected!")
            enter()
            return

        destination_cities = []
        for city_id in destination_city_ids:
            html = session.get(CITY_URL + city_id)
            city = getCity(html)
            destination_cities.append(city)

        print_module_banner("Distribute Resources", "Send resources from one city to multiple destinations")
        print("")

        dest_cities_summary = ', '.join([city['name'] for city in destination_cities])

        print(f"Source city: {origin_city['name']}")
        print(f"Destination cities: {dest_cities_summary}")
        print("")
        print("Configure resources to send to EACH destination city:")
        print("(Enter amount to send to each city)")
        print("(Enter 0 or leave blank to NOT send that resource)")
        print("(Press '=' to restart resource configuration from beginning)")
        print("(Press ' to exit to main menu)")
        print("")

        resource_config_complete = False
        while not resource_config_complete:
            resource_config = []
            restart = False

            for i, resource in enumerate(MATERIALS_NAMES):
                amount = readResourceAmount(resource)

                if amount == 'EXIT':
                    return

                if amount == 'RESTART':
                    print("\nRestarting resource configuration...\n")
                    restart = True
                    break

                resource_config.append(amount if amount is not None else 0)

            if not restart:
                resource_config_complete = True

        print_module_banner("Distribute Resources", "Send resources from one city to multiple destinations")
        print("")

        total_resources_needed = [amount * len(destination_cities) for amount in resource_config]
        grand_total = sum(total_resources_needed)

        print("Configuration:")
        print(f"  Ship type: {'Freighters' if useFreighters else 'Merchant ships'}")
        print(f"  Source city: {origin_city['name']}")
        print(f"  Destination cities ({len(destination_cities)}): {dest_cities_summary}")
        print("")
        print("  Resources per destination:")
        for i, resource in enumerate(MATERIALS_NAMES):
            if resource_config[i] > 0:
                print(f"    {resource:<10} {addThousandSeparator(resource_config[i]):>15}")

        print("")
        print("  Total Resources Needed:")
        print(f"    {'Resource':<10} {'Amount':>15}")
        print(f"    {'-'*10} {'-'*15}")
        for i, resource in enumerate(MATERIALS_NAMES):
            if total_resources_needed[i] > 0:
                print(f"    {resource:<10} {addThousandSeparator(total_resources_needed[i]):>15}")
        print(f"    {'-'*10} {'-'*15}")
        print(f"    {'TOTAL':<10} {addThousandSeparator(grand_total):>15}")

        print("")

        # Notification preferences
        if telegram_enabled is None:
            notify_on_start = False
        else:
            print_module_banner("Notification Preferences")
            print("When do you want to receive Telegram notifications?")
            print("(1) Partial - When new scheduled shipment is dispatched")
            print("(2) All - Every Individual Shipment")
            print("(3) None - No notifications")
            print("(') Back to main menu")
            notif_choice = read(min=1, max=3, digit=True, additionalValues=["'"])
            if notif_choice == "'":
                return

            if notif_choice == 1:
                telegram_enabled = None
                notify_on_start = True
            elif notif_choice == 2:
                telegram_enabled = True
                notify_on_start = True
            else:
                telegram_enabled = None
                notify_on_start = False

        print_module_banner("Schedule Configuration")

        interval_confirmed = False
        while not interval_confirmed:
            print("How often should resources be sent (in hours)?")
            print("(0 for one-time shipment, or minimum every (1) hour for recurring)")
            print("(Press ' to return to main menu)")
            interval_hours = read(min=0, digit=True, additionalValues=["'"])
            if interval_hours == "'":
                return

            print("")
            if interval_hours == 0:
                print("You entered: One-time shipment (no recurring)")
            else:
                print(f"You entered: Every {interval_hours} hour(s)")
            print("(1) Confirm")
            print("(2) Retry - enter different time")
            confirm_choice = read(min=1, max=2, digit=True)

            if confirm_choice == 1:
                interval_confirmed = True

        print_module_banner("Configuration Summary")

        print("Configuration summary:")
        print("")
        print("  Source city:")
        print(f"    - {origin_city['name']}")
        print("")
        print(f"  Destination cities ({len(destination_cities)}):")
        for city in destination_cities:
            print(f"    - {city['name']}")
        print("")
        print(f"  Total resources needed: {addThousandSeparator(grand_total)}")
        print(f"  Interval: {interval_hours} hour(s)" if interval_hours > 0 else "  Mode: One-time shipment")
        print("")
        print("Proceed? [Y/n]")
        rta = read(values=["y", "Y", "n", "N", ""])
        if rta.lower() == "n":
            return

        enter()

    except KeyboardInterrupt:
        return

    info = f"\nDistribute resources from {origin_city['name']} to {len(destination_cities)} cities every {interval_hours} hour(s)\n"

    return {
        "info": info,
        "run_func": lambda: do_it_distribute(session, origin_city, destination_cities, interval_hours, resource_config, useFreighters, telegram_enabled, notify_on_start),
    }


def evenDistributionMode(session, telegram_enabled):
    """Even Distribution Mode: Balance one resource across all cities."""
    try:
        print_module_banner("Even Distribution", "Balance one resource evenly across all selected cities")
        print("")
        print("What type of ships do you want to use? (Default: Trade ships)")
        print("(1) Trade ships")
        print("(2) Freighters")
        print("(') Exit to main menu")
        shiptype = read(min=1, max=2, digit=True, empty=True, additionalValues=["'"])
        if shiptype == "'":
            return
        if shiptype == '':
            shiptype = 1
        useFreighters = (shiptype == 2)

        print_module_banner("Even Distribution", "Balance one resource evenly across all selected cities")
        print("")
        print("What resource do you want to distribute?")
        print("(0) Exit")
        for i in range(len(MATERIALS_NAMES)):
            print("({:d}) {}".format(i + 1, MATERIALS_NAMES[i]))
        print("(') Exit to main menu")
        resource = read(min=0, max=5, additionalValues=["'"])
        if resource == 0 or resource == "'":
            return
        resource -= 1

        print_module_banner("Even Distribution", "Balance one resource evenly across all selected cities")
        print("")
        distribution_msg = 'Select the cities to participate in the distribution:'
        cities_ids, cities = ignoreCities(session, msg=distribution_msg)

        routes = distribute_evenly(session, resource, cities_ids, cities)

        if routes is None:
            return

        print_module_banner("Even Distribution", "Balance one resource evenly across all selected cities")
        print("")
        print("The following shipments will be made:\n")
        for route in routes:
            print(
                "{} -> {} : {} {}".format(
                    route[0]["name"],
                    route[1]["name"],
                    route[resource + 3],
                    MATERIALS_NAMES[resource],
                )
            )

        print("\nProceed? [Y/n]")
        rta = read(values=["y", "Y", "n", "N", ""])
        if rta.lower() == "n":
            return

    except KeyboardInterrupt:
        return

    info = "\nDistribute {}\n".format(MATERIALS_NAMES[resource])

    return {
        "info": info,
        "run_func": lambda: executeRoutes(session, routes, useFreighters),
    }


def distribute_evenly(session, resource_type, cities_ids, cities):
    """Calculate routes to evenly distribute one resource across cities.

    Ported verbatim from ikabot's distributeResources.py.
    """
    resourceTotal = 0

    originCities = {}
    destinationCities = {}
    allCities = {}
    for cityID in cities_ids:
        html = session.get(CITY_URL + cityID)
        city = getCity(html)

        resourceTotal += city["availableResources"][resource_type]
        allCities[cityID] = city

    if len(allCities) == 0:
        return None

    resourceAverage = resourceTotal // len(allCities)
    while True:
        len_prev = len(destinationCities)
        for cityID in allCities:
            if cityID in destinationCities:
                continue
            freeStorage = allCities[cityID]["freeSpaceForResources"][resource_type]
            storage = allCities[cityID]["storageCapacity"]
            if storage < resourceAverage:
                destinationCities[cityID] = freeStorage
                resourceTotal -= storage

        remaining = len(allCities) - len(destinationCities)
        if remaining == 0:
            break
        resourceAverage = resourceTotal // remaining

        if len_prev == len(destinationCities):
            for cityID in allCities:
                if cityID in destinationCities:
                    continue
                if allCities[cityID]["availableResources"][resource_type] > resourceAverage:
                    originCities[cityID] = (
                        allCities[cityID]["availableResources"][resource_type] - resourceAverage
                    )
                else:
                    destinationCities[cityID] = (
                        resourceAverage - allCities[cityID]["availableResources"][resource_type]
                    )
            break

    originCities = dict(sorted(originCities.items(), key=lambda item: item[1], reverse=True))
    destinationCities = dict(sorted(destinationCities.items(), key=lambda item: item[1]))

    routes = []

    for originCityID in originCities:
        for destinationCityID in destinationCities:
            if originCities[originCityID] == 0 or destinationCities[destinationCityID] == 0:
                continue

            if originCities[originCityID] > destinationCities[destinationCityID]:
                toSend = destinationCities[destinationCityID]
            else:
                toSend = originCities[originCityID]

            if toSend == 0:
                continue

            toSendArr = [0] * len(MATERIALS_NAMES)
            toSendArr[resource_type] = toSend
            route = (
                allCities[originCityID],
                allCities[destinationCityID],
                allCities[destinationCityID]["islandId"],
                *toSendArr,
            )
            routes.append(route)

            if originCities[originCityID] > destinationCities[destinationCityID]:
                originCities[originCityID] -= destinationCities[destinationCityID]
                destinationCities[destinationCityID] = 0
            else:
                destinationCities[destinationCityID] -= originCities[originCityID]
                originCities[originCityID] = 0

    return routes


def do_it(session, origin_cities, destination_city, island, interval_hours, resource_config, useFreighters, send_mode, telegram_enabled, notify_on_start):
    """Core execution loop for consolidate mode."""

    first_run = True
    next_run_time = datetime.datetime.now()
    total_shipments = 0
    consecutive_failures = 0
    ship_type_name = "freighters" if useFreighters else "merchant ships"

    while True:
        current_time = datetime.datetime.now()

        if current_time < next_run_time and not first_run:
            time.sleep(60)
            continue

        print(f"\n--- Starting shipment cycle ---")

        if notify_on_start:
            total_resources_this_cycle = [0] * len(MATERIALS_NAMES)
            grand_total_this_cycle = 0

            for origin_city in origin_cities:
                html_temp = session.get(CITY_URL + str(origin_city['id']))
                origin_city_temp = getCity(html_temp)

                for i, resource in enumerate(MATERIALS_NAMES):
                    if resource_config[i] is None:
                        continue

                    available = origin_city_temp['availableResources'][i]

                    if send_mode == 1:
                        if resource_config[i] == 0:
                            sendable = available
                        else:
                            sendable = max(0, available - resource_config[i])
                    else:
                        if resource_config[i] == 0:
                            sendable = 0
                        else:
                            sendable = min(resource_config[i], available)

                    total_resources_this_cycle[i] += sendable
                    grand_total_this_cycle += sendable

            resources_list = []
            for i, amount in enumerate(total_resources_this_cycle):
                if amount > 0:
                    resources_list.append(f"{addThousandSeparator(amount)} {MATERIALS_NAMES[i]}")

            if resources_list:
                source_cities_names = ', '.join([city['name'] for city in origin_cities])
                start_msg = f"SHIPMENT STARTING\nAccount: {session.username}\nFrom: {source_cities_names}\nTo: [{island['x']}:{island['y']}] {destination_city['name']}\nShip type: {ship_type_name}\nTotal resources: {', '.join(resources_list)}\nGrand total: {addThousandSeparator(grand_total_this_cycle)}"
                sendToBot(session, start_msg)

        print(f"  Fetching destination city data...")
        html = session.get(CITY_URL + str(destination_city['id']))
        destination_city = getCity(html)

        for city_index, origin_city in enumerate(origin_cities):
            print(f"\n  [{city_index + 1}/{len(origin_cities)}] Processing: {origin_city['name']}")
            html = session.get(CITY_URL + str(origin_city['id']))
            origin_city = getCity(html)

            toSend = [0] * len(MATERIALS_NAMES)
            total_to_send = 0

            for i, resource in enumerate(MATERIALS_NAMES):
                if resource_config[i] is None:
                    toSend[i] = 0
                    continue

                available = origin_city['availableResources'][i]

                if send_mode == 1:
                    if resource_config[i] == 0:
                        sendable = available
                    else:
                        sendable = max(0, available - resource_config[i])
                else:
                    if resource_config[i] == 0:
                        sendable = 0
                    else:
                        sendable = min(resource_config[i], available)

                if destination_city.get('isOwnCity', False):
                    destination_space = destination_city['freeSpaceForResources'][i]
                    sendable = min(sendable, destination_space)

                toSend[i] = sendable
                total_to_send += sendable

            if total_to_send > 0:
                resources_desc = ", ".join(
                    f"{addThousandSeparator(toSend[i])} {MATERIALS_NAMES[i]}"
                    for i in range(len(MATERIALS_NAMES)) if toSend[i] > 0
                )
                print(f"    Sending: {resources_desc}")

                ship_type = 'freighters' if useFreighters else 'merchant ships'
                ships_available = False
                ship_check_start = time.time()

                while not ships_available:
                    if useFreighters:
                        available_ships = getAvailableFreighters(session)
                    else:
                        available_ships = getAvailableShips(session)

                    if available_ships > 0:
                        ships_available = True
                        print(f"    Found {available_ships} {ship_type}")
                        session.setStatus(
                            f"{origin_city['name']} -> {destination_city['name']} | Found {available_ships} {ship_type}, attempting to send..."
                        )
                    else:
                        wait_time = 120
                        elapsed = int(time.time() - ship_check_start)
                        print(f"    Waiting for {ship_type}... (checked for {elapsed}s)")
                        session.setStatus(
                            f"{origin_city['name']} -> {destination_city['name']} | Waiting for {ship_type} (checked for {elapsed}s)..."
                        )
                        time.sleep(wait_time)

                max_retries = 3
                retry_count = 0
                lock_acquired = False

                while retry_count < max_retries and not lock_acquired:
                    print(f"    Acquiring shipping lock (attempt {retry_count + 1}/{max_retries})...")
                    session.setStatus(
                        f"{origin_city['name']} -> {destination_city['name']} | Waiting for shipping lock (attempt {retry_count + 1}/{max_retries})..."
                    )

                    if acquire_shipping_lock(session, use_freighters=useFreighters, timeout=300):
                        lock_acquired = True
                        print(f"    Lock acquired.")
                    else:
                        retry_count += 1
                        print(f"    Lock attempt {retry_count}/{max_retries} failed, retrying...")
                        if retry_count < max_retries and telegram_enabled:
                            msg = f"Account: {session.username}\nFrom: {origin_city['name']}\nTo: [{island['x']}:{island['y']}] {destination_city['name']}\nProblem: Failed to acquire shipping lock on attempt {retry_count}/{max_retries}\nAction: Retrying in 1 minute..."
                            sendToBot(session, msg)
                        time.sleep(60)

                if lock_acquired:
                    try:
                        route = (
                            origin_city,
                            destination_city,
                            island["id"],
                            *toSend,
                        )

                        session.setStatus(
                            f"{origin_city['name']} -> {destination_city['name']} | Sending resources..."
                        )

                        if useFreighters:
                            ships_before = getAvailableFreighters(session)
                        else:
                            ships_before = getAvailableShips(session)

                        if ships_before == 0:
                            consecutive_failures += 1
                            print(f"    Ships became unavailable, skipping")
                            if telegram_enabled:
                                msg = f"SHIPMENT DELAYED\nAccount: {session.username}\nFrom: {origin_city['name']}\nTo: [{island['x']}:{island['y']}] {destination_city['name']}\nProblem: Ships became unavailable before sending\nAction: Will retry in next cycle"
                                sendToBot(session, msg)
                            continue

                        try:
                            executeRoutes(session, [route], useFreighters)

                            if useFreighters:
                                ships_after = getAvailableFreighters(session)
                            else:
                                ships_after = getAvailableShips(session)

                            ship_capacity, freighter_capacity = getShipCapacity(session)
                            capacity = freighter_capacity if useFreighters else ship_capacity
                            ships_needed = (total_to_send + capacity - 1) // capacity

                            ships_used_actual = ships_before - ships_after

                            if ships_used_actual < ships_needed:
                                raise Exception(f"Expected to use {ships_needed} ships but only {ships_used_actual} were used")

                            total_shipments += 1
                            consecutive_failures = 0

                            resources_sent = []
                            for i, amount in enumerate(toSend):
                                if amount > 0:
                                    resources_sent.append(f"{addThousandSeparator(amount)} {MATERIALS_NAMES[i]}")

                            print(f"    SENT: {', '.join(resources_sent)} ({ships_needed} {ship_type_name})")

                            if telegram_enabled:
                                msg = f"SHIPMENT SENT\nAccount: {session.username}\nFrom: {origin_city['name']}\nTo: [{island['x']}:{island['y']}] {destination_city['name']}\nShips: {ships_needed} {ship_type_name}\nSent: {', '.join(resources_sent)}"
                                sendToBot(session, msg)

                        except Exception as send_error:
                            consecutive_failures += 1
                            error_msg = str(send_error)
                            print(f"    FAILED: {error_msg}")

                            if telegram_enabled:
                                msg = f"SHIPMENT FAILED\nAccount: {session.username}\nFrom: {origin_city['name']}\nTo: [{island['x']}:{island['y']}] {destination_city['name']}\nError: {error_msg}\nConsecutive failures: {consecutive_failures}\nAction: Will retry in next cycle"
                                sendToBot(session, msg)

                    finally:
                        release_shipping_lock(session, use_freighters=useFreighters)
                else:
                    consecutive_failures += 1
                    print(f"    Could not acquire shipping lock after {max_retries} attempts")
                    if telegram_enabled:
                        msg = f"Account: {session.username}\nFrom: {origin_city['name']}\nTo: [{island['x']}:{island['y']}] {destination_city['name']}\nProblem: Could not acquire shipping lock\nAttempts: {max_retries}\nConsecutive failures: {consecutive_failures}\nAction: Skipping this cycle"
                        sendToBot(session, msg)

                    if consecutive_failures >= 3:
                        alert_msg = f"WARNING\nAccount: {session.username}\nFrom: {origin_city['name']}\nTo: [{island['x']}:{island['y']}] {destination_city['name']}\nProblem: {consecutive_failures} consecutive shipping failures\nPlease check for issues!"
                        if telegram_enabled:
                            sendToBot(session, alert_msg)
                        report_critical_error(
                            session,
                            MODULE_NAME,
                            f"{consecutive_failures} consecutive shipping failures.\n"
                            f"{origin_city['name']} -> {destination_city['name']}",
                        )
            else:
                print(f"    No resources to send (below threshold or no space)")
                if telegram_enabled:
                    msg = f"Account: {session.username}\nFrom: {origin_city['name']}\nTo: [{island['x']}:{island['y']}] {destination_city['name']}\nStatus: No resources to send (all below thresholds or no space)"
                    sendToBot(session, msg)

        if interval_hours == 0:
            source_cities_names = ', '.join([city['name'] for city in origin_cities])
            print(f"\n  One-time shipment complete! ({total_shipments} shipments sent)")
            session.setStatus(f"One-time shipment completed: {source_cities_names} -> {destination_city['name']}")
            return

        next_run_time = datetime.datetime.now() + datetime.timedelta(hours=interval_hours)

        source_cities_names = ', '.join([city['name'] for city in origin_cities])
        print(f"\n  Cycle complete ({total_shipments} shipments). Next run: {getDateTime(next_run_time.timestamp())}")

        session.setStatus(
            f"{source_cities_names} -> {destination_city['name']} | Shipments: {total_shipments} | Next: {getDateTime(next_run_time.timestamp())}"
        )

        first_run = False
        time.sleep(60 * 60)


def do_it_distribute(session, origin_city, destination_cities, interval_hours, resource_config, useFreighters, telegram_enabled, notify_on_start):
    """Core execution loop for distribute mode."""

    first_run = True
    next_run_time = datetime.datetime.now()
    total_shipments = 0
    consecutive_failures = 0
    ship_type_name = "freighters" if useFreighters else "merchant ships"

    while True:
        current_time = datetime.datetime.now()

        if current_time < next_run_time and not first_run:
            time.sleep(60)
            continue

        print(f"\n--- Starting distribution cycle ---")

        if notify_on_start:
            total_resources_needed = [amount * len(destination_cities) for amount in resource_config]
            grand_total = sum(total_resources_needed)

            resources_list = []
            for i, amount in enumerate(total_resources_needed):
                if amount > 0:
                    resources_list.append(f"{addThousandSeparator(amount)} {MATERIALS_NAMES[i]}")

            if resources_list:
                dest_names = ', '.join([city['name'] for city in destination_cities])
                start_msg = f"SHIPMENT STARTING\nAccount: {session.username}\nFrom: {origin_city['name']}\nTo: {len(destination_cities)} cities ({dest_names})\nShip type: {ship_type_name}\nTotal resources: {', '.join(resources_list)}\nGrand total: {addThousandSeparator(grand_total)}"
                sendToBot(session, start_msg)

        print(f"  Fetching source city data...")
        html = session.get(CITY_URL + str(origin_city['id']))
        origin_city = getCity(html)

        origin_island_id = origin_city['islandId']
        html_island = session.get(ISLAND_URL + str(origin_island_id))
        origin_island = getIsland(html_island)

        for dest_index, destination_city in enumerate(destination_cities):
            print(f"\n  [{dest_index + 1}/{len(destination_cities)}] Sending to: {destination_city['name']}")
            html = session.get(CITY_URL + str(destination_city['id']))
            destination_city = getCity(html)

            dest_island_id = destination_city['islandId']
            html_dest_island = session.get(ISLAND_URL + str(dest_island_id))
            dest_island = getIsland(html_dest_island)

            toSend = [0] * len(MATERIALS_NAMES)
            total_to_send = 0

            for i, resource in enumerate(MATERIALS_NAMES):
                if resource_config[i] == 0:
                    toSend[i] = 0
                    continue

                available = origin_city['availableResources'][i]
                requested = resource_config[i]

                sendable = min(requested, available)

                if destination_city.get('isOwnCity', True):
                    destination_space = destination_city['freeSpaceForResources'][i]
                    sendable = min(sendable, destination_space)

                toSend[i] = sendable
                total_to_send += sendable

            if total_to_send > 0:
                resources_desc = ", ".join(
                    f"{addThousandSeparator(toSend[i])} {MATERIALS_NAMES[i]}"
                    for i in range(len(MATERIALS_NAMES)) if toSend[i] > 0
                )
                print(f"    Sending: {resources_desc}")

                ship_type = 'freighters' if useFreighters else 'merchant ships'
                ships_available = False
                ship_check_start = time.time()

                while not ships_available:
                    if useFreighters:
                        available_ships = getAvailableFreighters(session)
                    else:
                        available_ships = getAvailableShips(session)

                    if available_ships > 0:
                        ships_available = True
                        print(f"    Found {available_ships} {ship_type}")
                        session.setStatus(
                            f"{origin_city['name']} -> {destination_city['name']} | Found {available_ships} {ship_type}, attempting to send..."
                        )
                    else:
                        wait_time = 120
                        elapsed = int(time.time() - ship_check_start)
                        print(f"    Waiting for {ship_type}... (checked for {elapsed}s)")
                        session.setStatus(
                            f"{origin_city['name']} -> {destination_city['name']} | Waiting for {ship_type} (checked for {elapsed}s)..."
                        )
                        time.sleep(wait_time)

                max_retries = 3
                retry_count = 0
                lock_acquired = False

                while retry_count < max_retries and not lock_acquired:
                    print(f"    Acquiring shipping lock (attempt {retry_count + 1}/{max_retries})...")
                    session.setStatus(
                        f"{origin_city['name']} -> {destination_city['name']} | Waiting for shipping lock (attempt {retry_count + 1}/{max_retries})..."
                    )

                    if acquire_shipping_lock(session, use_freighters=useFreighters, timeout=300):
                        lock_acquired = True
                        print(f"    Lock acquired.")
                    else:
                        retry_count += 1
                        print(f"    Lock attempt {retry_count}/{max_retries} failed, retrying...")
                        if retry_count < max_retries and telegram_enabled:
                            msg = f"Account: {session.username}\nFrom: {origin_city['name']}\nTo: [{dest_island['x']}:{dest_island['y']}] {destination_city['name']}\nProblem: Failed to acquire shipping lock on attempt {retry_count}/{max_retries}\nAction: Retrying in 1 minute..."
                            sendToBot(session, msg)
                        time.sleep(60)

                if lock_acquired:
                    try:
                        route = (
                            origin_city,
                            destination_city,
                            dest_island["id"],
                            *toSend,
                        )

                        session.setStatus(
                            f"{origin_city['name']} -> {destination_city['name']} | Sending resources..."
                        )

                        if useFreighters:
                            ships_before = getAvailableFreighters(session)
                        else:
                            ships_before = getAvailableShips(session)

                        if ships_before == 0:
                            consecutive_failures += 1
                            print(f"    Ships became unavailable, skipping")
                            if telegram_enabled:
                                msg = f"SHIPMENT DELAYED\nAccount: {session.username}\nFrom: {origin_city['name']}\nTo: [{dest_island['x']}:{dest_island['y']}] {destination_city['name']}\nProblem: Ships became unavailable before sending\nAction: Will retry in next cycle"
                                sendToBot(session, msg)
                            continue

                        try:
                            executeRoutes(session, [route], useFreighters)

                            if useFreighters:
                                ships_after = getAvailableFreighters(session)
                            else:
                                ships_after = getAvailableShips(session)

                            ship_capacity, freighter_capacity = getShipCapacity(session)
                            capacity = freighter_capacity if useFreighters else ship_capacity
                            ships_needed = (total_to_send + capacity - 1) // capacity

                            ships_used_actual = ships_before - ships_after

                            if ships_used_actual < ships_needed:
                                raise Exception(f"Expected to use {ships_needed} ships but only {ships_used_actual} were used")

                            total_shipments += 1
                            consecutive_failures = 0

                            resources_sent = []
                            for i, amount in enumerate(toSend):
                                if amount > 0:
                                    resources_sent.append(f"{addThousandSeparator(amount)} {MATERIALS_NAMES[i]}")

                            print(f"    SENT: {', '.join(resources_sent)} ({ships_needed} {ship_type_name})")

                            if telegram_enabled:
                                msg = f"SHIPMENT SENT\nAccount: {session.username}\nFrom: {origin_city['name']}\nTo: [{dest_island['x']}:{dest_island['y']}] {destination_city['name']}\nShips: {ships_needed} {ship_type_name}\nSent: {', '.join(resources_sent)}"
                                sendToBot(session, msg)

                        except Exception as send_error:
                            consecutive_failures += 1
                            error_msg = str(send_error)
                            print(f"    FAILED: {error_msg}")

                            if telegram_enabled:
                                msg = f"SHIPMENT FAILED\nAccount: {session.username}\nFrom: {origin_city['name']}\nTo: [{dest_island['x']}:{dest_island['y']}] {destination_city['name']}\nError: {error_msg}\nConsecutive failures: {consecutive_failures}\nAction: Will retry in next cycle"
                                sendToBot(session, msg)

                    finally:
                        release_shipping_lock(session, use_freighters=useFreighters)
                else:
                    consecutive_failures += 1
                    print(f"    Could not acquire shipping lock after {max_retries} attempts")
                    if telegram_enabled:
                        msg = f"Account: {session.username}\nFrom: {origin_city['name']}\nTo: [{dest_island['x']}:{dest_island['y']}] {destination_city['name']}\nProblem: Could not acquire shipping lock\nAttempts: {max_retries}\nConsecutive failures: {consecutive_failures}\nAction: Skipping this destination"
                        sendToBot(session, msg)

                    if consecutive_failures >= 3:
                        alert_msg = f"WARNING\nAccount: {session.username}\nFrom: {origin_city['name']}\nTo: [{dest_island['x']}:{dest_island['y']}] {destination_city['name']}\nProblem: {consecutive_failures} consecutive shipping failures\nPlease check for issues!"
                        if telegram_enabled:
                            sendToBot(session, alert_msg)
                        report_critical_error(
                            session,
                            MODULE_NAME,
                            f"{consecutive_failures} consecutive shipping failures.\n"
                            f"{origin_city['name']} -> {destination_city['name']}",
                        )
            else:
                print(f"    No resources to send (insufficient or no space)")
                if telegram_enabled:
                    msg = f"Account: {session.username}\nFrom: {origin_city['name']}\nTo: [{dest_island['x']}:{dest_island['y']}] {destination_city['name']}\nStatus: No resources to send (insufficient or no space)"
                    sendToBot(session, msg)

        if interval_hours == 0:
            dest_names = ', '.join([city['name'] for city in destination_cities])
            print(f"\n  One-time distribution complete! ({total_shipments} shipments sent)")
            session.setStatus(f"One-time distribution completed: {origin_city['name']} -> {dest_names}")
            return

        next_run_time = datetime.datetime.now() + datetime.timedelta(hours=interval_hours)

        dest_names = ', '.join([city['name'] for city in destination_cities])
        print(f"\n  Cycle complete ({total_shipments} shipments). Next run: {getDateTime(next_run_time.timestamp())}")

        session.setStatus(
            f"{origin_city['name']} -> {len(destination_cities)} cities | Shipments: {total_shipments} | Next: {getDateTime(next_run_time.timestamp())}"
        )

        first_run = False
        time.sleep(60 * 60)
