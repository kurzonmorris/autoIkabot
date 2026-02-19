#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""Resource Transport Manager v1.2 — ikabot module.

Four shipping modes:
  1. Consolidate: Multiple source cities -> One destination
  2. Distribute: One source city -> Multiple destinations
  3. Even Distribution: Balance one resource across all cities
  4. Auto Send: Request specific amounts, auto-collect from all cities

Ported from autoIkabot's resourceTransportManager for use with ikabot-7.2.5.
"""

import datetime
import json
import math
import os
import sys
import time
import traceback
from decimal import Decimal

from ikabot.config import *
from ikabot.helpers.botComm import *
from ikabot.helpers.getJson import getCity, getIsland, getIdsOfCities
from ikabot.helpers.gui import banner, enter
from ikabot.helpers.naval import getAvailableShips, getAvailableFreighters
from ikabot.helpers.pedirInfo import chooseCity, ignoreCities, read, getShipCapacity
from ikabot.helpers.planRoutes import executeRoutes
from ikabot.helpers.process import set_child_mode
from ikabot.helpers.signals import setInfoSignal
from ikabot.helpers.varios import addThousandSeparator, getDateTime


# ---------------------------------------------------------------------------
# Module metadata
# ---------------------------------------------------------------------------

MODULE_NAME = "Resource Transport Manager"


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def print_module_banner(mode_name=None, mode_description=None):
    """Print the Resource Transport Manager banner."""
    print("\n")
    print("+" + "=" * 58 + "+")
    print("|       RESOURCE TRANSPORT MANAGER v1.2" + " " * 20 + "|")

    if mode_name:
        print("|" + "-" * 58 + "|")
        mode_line = "| {:^56} |".format(mode_name)
        print(mode_line)

        if mode_description:
            desc_line = "| {:^56} |".format(mode_description)
            print(desc_line)

    print("+" + "=" * 58 + "+")
    print("")


# ---------------------------------------------------------------------------
# Shipping lock (file-based, prevents concurrent transport collisions)
# ---------------------------------------------------------------------------

def get_lock_file_path(session, use_freighters=False):
    """Get the path to the shared shipping lock file."""
    ship_type = "freighters" if use_freighters else "merchant_ships"
    safe_server = session.servidor.replace('/', '_').replace('\\', '_')
    safe_username = session.username.replace('/', '_').replace('\\', '_')
    lock_filename = ".ikabot_shared_{}_{}_{}".format(ship_type, safe_server, safe_username) + ".lock"
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


# ---------------------------------------------------------------------------
# Resource input helper
# ---------------------------------------------------------------------------

def readResourceAmount(resource_name):
    """Read a resource amount with validation.

    Returns None (ignore), int, 'EXIT', or 'RESTART'.
    """
    while True:
        user_input = read(msg="{}: ".format(resource_name), empty=True, additionalValues=["'", "="])

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
                print("  -> Set to: {}".format(addThousandSeparator(amount)))
            return amount
        else:
            print("  Please enter a number, 0, leave blank, or press ' to exit")


# ---------------------------------------------------------------------------
# Main entry point (called by the menu system)
# ---------------------------------------------------------------------------

def resourceTransportManager(session, event, stdin_fd, predetermined_input):
    """Resource Transport Manager — background module entry point.

    Spawned as a child process by the menu's background dispatch.
    Handles the interactive config phase, then signals the parent and
    continues running the shipment loop in the background.

    Parameters
    ----------
    session : ikabot.web.session.Session
    event : multiprocessing.Event
        Signalled after config is done to return control to menu.
    stdin_fd : int
        File descriptor for stdin from the parent process.
    predetermined_input : multiprocessing.managers.SyncManager.list
        Pre-recorded inputs for non-interactive replay.
    """
    sys.stdin = os.fdopen(stdin_fd)
    config.predetermined_input = predetermined_input

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
        print("(4) Auto Send: Request resources and auto-collect from all cities")
        print("(') Back to main menu")
        shipping_mode = read(min=1, max=4, digit=True, additionalValues=["'"])
        if shipping_mode == "'":
            event.set()
            return

        if shipping_mode == 1:
            result = consolidateMode(session, telegram_enabled)
        elif shipping_mode == 2:
            result = distributeMode(session, telegram_enabled)
        elif shipping_mode == 3:
            result = evenDistributionMode(session, telegram_enabled)
        else:
            result = autoSendMode(session, telegram_enabled)

        if result is None:
            # User cancelled during config
            event.set()
            return

        # --- Hand off: config done, switch to background ---
        set_child_mode(session)
        event.set()

        # --- Background phase: no user interaction from here ---
        info = result["info"]
        setInfoSignal(session, info.strip())

        max_restarts = 5
        restart_count = 0
        base_wait = 60  # seconds

        while True:
            try:
                result["run_func"]()
                break  # clean exit (one-time shipment finished)
            except KeyboardInterrupt:
                raise
            except Exception:
                restart_count += 1
                tb = traceback.format_exc()
                error_summary = tb.splitlines()[-1]

                if restart_count > max_restarts:
                    msg = (
                        "Module crashed and exhausted all {} restart attempts.\n"
                        "Error in:\n{}\nCause:\n{}"
                    ).format(max_restarts, info, error_summary)
                    sendToBot(session, msg)
                    break

                wait_seconds = min(base_wait * (2 ** (restart_count - 1)), 600)
                msg = (
                    "Module crashed (attempt {}/{}).\n"
                    "Error: {}\n"
                    "Auto-restarting in {}s..."
                ).format(restart_count, max_restarts, error_summary, wait_seconds)
                sendToBot(session, msg)
                time.sleep(wait_seconds)

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
                html = session.get(city_url + city_id)
                city = getCity(html)
                origin_cities.append(city)

        print_module_banner("Consolidate Resources", "Send resources from multiple cities to a single destination")

        if len(origin_cities) == 1:
            source_cities_summary = origin_cities[0]['name']
        else:
            source_cities_summary = ', '.join([city['name'] for city in origin_cities])

        print("Source cities: {}".format(source_cities_summary))
        print("")
        print("Choose sending mode:")
        print("(1) Send ALL resources EXCEPT a reserve amount (keep X, send rest)")
        print("(2) Send SPECIFIC amounts (send exactly X)")
        print("(') Exit to main menu")
        send_mode = read(min=1, max=2, digit=True, additionalValues=["'"])
        if send_mode == "'":
            return

        print_module_banner("Consolidate Resources", "Send resources from multiple cities to a single destination")
        print("Source cities: {}".format(source_cities_summary))
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
                html = session.get(city_url + str(origin_cities[0]['id']))
                single_city_data = getCity(html)
                print("Available resources in {}:".format(origin_cities[0]['name']))
                header = "  "
                for resource in materials_names:
                    header += "{:>12}  ".format(resource)
                print(header)
                separator = "  "
                for _ in materials_names:
                    separator += "{}  ".format('-' * 12)
                print(separator)
                amounts = "  "
                for i in range(len(materials_names)):
                    amount = single_city_data['availableResources'][i]
                    amounts += "{:>12}  ".format(addThousandSeparator(amount))
                print(amounts)
                print("")

        # Get resource config (with restart support)
        resource_config_complete = False
        while not resource_config_complete:
            resource_config = []
            restart = False

            for i, resource in enumerate(materials_names):
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
        print("Source cities: {}".format(source_cities_summary))
        print("")
        print("Resource configuration:")
        if send_mode == 1:
            for i, resource in enumerate(materials_names):
                if resource_config[i] is None:
                    print("  {}: IGNORED (won't send)".format(resource))
                elif resource_config[i] == 0:
                    print("  {}: Send ALL".format(resource))
                else:
                    print("  {}: Keep {}, send excess".format(resource, addThousandSeparator(resource_config[i])))
        else:
            for i, resource in enumerate(materials_names):
                if resource_config[i] is None or resource_config[i] == 0:
                    print("  {}: NOT sending".format(resource))
                else:
                    print("  {}: Send {}".format(resource, addThousandSeparator(resource_config[i])))
        print("")

        print_module_banner("Consolidate Resources", "Send resources from multiple cities to a single destination")

        print("Source cities: {}".format(source_cities_summary))
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

                island_coords = "xcoord={}&ycoord={}".format(x_coord, y_coord)
                html = session.get("view=island&{}".format(island_coords))
                island = getIsland(html)

                cities_on_island = [city for city in island["cities"] if city["type"] == "city"]

                if len(cities_on_island) == 0:
                    print("Island {}:{} has no cities!".format(x_coord, y_coord))
                    enter()
                    continue

                print("")
                print("Island: {} [{}:{}]".format(island['name'], island['x'], island['y']))
                print("Resource: {}".format(materials_names[int(island['tradegood'])]))
                print("")
                print("Select destination city:")
                print("(0) Exit")
                print("(=) Restart coordinate entry")
                print("(') Exit to main menu")
                print("")

                print("    {:<20} {:<15}".format('City Name', 'Player'))
                print("    {} {}".format('-' * 20, '-' * 15))

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

                    print("({:>2}) {:<20} {:<15}".format(city_num, city_name_display, player_name_display))

                print("")
                city_choice = read(min=0, max=len(cities_on_island), additionalValues=["'", "="])

                if city_choice == 0 or city_choice == "'":
                    return

                if city_choice == "=":
                    print("\nRestarting coordinate entry...\n")
                    continue

                destination_city_data = cities_on_island[city_choice - 1]
                destination_city_id = destination_city_data["id"]

                html = session.get(city_url + str(destination_city_id))
                destination_city = getCity(html)
                destination_city["isOwnCity"] = (
                    destination_city_data.get("state", "") == ""
                    and destination_city_data.get("Name", "") == session.username
                )

                print("")
                print("Selected: {}".format(destination_city['name']))
                print("Player: {}".format(destination_city_data.get('Name', 'Unknown')))
                print("Island: {} [{}:{}]".format(island['name'], island['x'], island['y']))
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

            html = session.get(city_url + str(destination_city['id']))
            destination_city = getCity(html)
            island_id = destination_city['islandId']

            html = session.get(island_url + island_id)
            island = getIsland(html)

            destination_city["isOwnCity"] = True

        if destination_type == 2:
            player_name = destination_city_data.get('Name', 'Unknown')
        else:
            player_name = session.username

        print("Destination city: {} (Player: {})".format(destination_city['name'], player_name))
        print("Island: {} [{}:{}]".format(island['name'], island['x'], island['y']))
        print("")

        # Auto-exclude destination from origins
        original_count = len(origin_cities)
        origin_cities = [city for city in origin_cities if city['id'] != destination_city['id']]
        excluded_count = original_count - len(origin_cities)

        if excluded_count > 0:
            print("  Automatically excluded destination city '{}' from source cities".format(destination_city['name']))
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
                print("You entered: Every {} hour(s)".format(interval_hours))
            print("(1) Confirm")
            print("(2) Retry - enter different time")
            confirm_choice = read(min=1, max=2, digit=True)

            if confirm_choice == 1:
                interval_confirmed = True

        print_module_banner("Configuration Summary")

        # Calculate total resources
        total_resources_to_send = [0] * len(materials_names)
        grand_total = 0

        for origin_city in origin_cities:
            html = session.get(city_url + str(origin_city['id']))
            origin_city_data = getCity(html)

            for i, resource in enumerate(materials_names):
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
        print("  Ship type: {}".format('Freighters' if useFreighters else 'Merchant ships'))
        print("  Mode: {}".format('Send all except reserves' if send_mode == 1 else 'Send specific amounts'))
        print("")
        print("  Source cities ({}):".format(len(origin_cities)))
        for city in origin_cities:
            print("    - {}".format(city['name']))
        print("")
        print("  Destination:")
        print("    - {} on island {}:{}".format(destination_city['name'], island['x'], island['y']))
        print("")
        print("  Resource Configuration:")
        if send_mode == 1:
            for i, resource in enumerate(materials_names):
                if resource_config[i] is None:
                    print("    {:<10} IGNORED".format(resource))
                elif resource_config[i] == 0:
                    print("    {:<10} Send ALL".format(resource))
                else:
                    print("    {:<10} Keep {}".format(resource, addThousandSeparator(resource_config[i])))
        else:
            for i, resource in enumerate(materials_names):
                if resource_config[i] is None or resource_config[i] == 0:
                    print("    {:<10} NOT sending".format(resource))
                else:
                    print("    {:<10} Send {}".format(resource, addThousandSeparator(resource_config[i])))

        print("")
        print("  Total Resources to Send:")
        print("    {:<10} {:>15}".format('Resource', 'Amount'))
        print("    {} {}".format('-' * 10, '-' * 15))
        for i, resource in enumerate(materials_names):
            if total_resources_to_send[i] > 0:
                print("    {:<10} {:>15}".format(resource, addThousandSeparator(total_resources_to_send[i])))
        print("    {} {}".format('-' * 10, '-' * 15))
        print("    {:<10} {:>15}".format('TOTAL', addThousandSeparator(grand_total)))

        print("")
        if interval_hours > 0:
            print("  Interval: {} hour(s)".format(interval_hours))
        else:
            print("  Mode: One-time shipment")

        print("")
        print("Proceed? [Y/n]")
        rta = read(values=["y", "Y", "n", "N", ""])
        if rta.lower() == "n":
            return

        enter()

    except KeyboardInterrupt:
        return

    info = "\nAuto-send resources from {} to {} every {} hour(s)\n".format(
        source_cities_summary, destination_city['name'], interval_hours
    )

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

        print("Source city: {}".format(origin_city['name']))
        print("")
        print("Note: Source city will be automatically excluded from destinations")
        print("")
        dest_msg = 'Select destination cities (cities to receive resources):'
        destination_city_ids, destination_cities_dict = ignoreCities(session, msg=dest_msg)

        source_city_id = str(origin_city['id'])
        if source_city_id in destination_city_ids:
            destination_city_ids.remove(source_city_id)
            print("Removed {} from destinations (source city cannot send to itself)".format(origin_city['name']))

        if not destination_city_ids:
            print("No valid destination cities selected!")
            enter()
            return

        destination_cities = []
        for city_id in destination_city_ids:
            html = session.get(city_url + city_id)
            city = getCity(html)
            destination_cities.append(city)

        print_module_banner("Distribute Resources", "Send resources from one city to multiple destinations")
        print("")

        dest_cities_summary = ', '.join([city['name'] for city in destination_cities])

        print("Source city: {}".format(origin_city['name']))
        print("Destination cities: {}".format(dest_cities_summary))
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

            for i, resource in enumerate(materials_names):
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
        print("  Ship type: {}".format('Freighters' if useFreighters else 'Merchant ships'))
        print("  Source city: {}".format(origin_city['name']))
        print("  Destination cities ({}): {}".format(len(destination_cities), dest_cities_summary))
        print("")
        print("  Resources per destination:")
        for i, resource in enumerate(materials_names):
            if resource_config[i] > 0:
                print("    {:<10} {:>15}".format(resource, addThousandSeparator(resource_config[i])))

        print("")
        print("  Total Resources Needed:")
        print("    {:<10} {:>15}".format('Resource', 'Amount'))
        print("    {} {}".format('-' * 10, '-' * 15))
        for i, resource in enumerate(materials_names):
            if total_resources_needed[i] > 0:
                print("    {:<10} {:>15}".format(resource, addThousandSeparator(total_resources_needed[i])))
        print("    {} {}".format('-' * 10, '-' * 15))
        print("    {:<10} {:>15}".format('TOTAL', addThousandSeparator(grand_total)))

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
                print("You entered: Every {} hour(s)".format(interval_hours))
            print("(1) Confirm")
            print("(2) Retry - enter different time")
            confirm_choice = read(min=1, max=2, digit=True)

            if confirm_choice == 1:
                interval_confirmed = True

        print_module_banner("Configuration Summary")

        print("Configuration summary:")
        print("")
        print("  Source city:")
        print("    - {}".format(origin_city['name']))
        print("")
        print("  Destination cities ({}):".format(len(destination_cities)))
        for city in destination_cities:
            print("    - {}".format(city['name']))
        print("")
        print("  Total resources needed: {}".format(addThousandSeparator(grand_total)))
        if interval_hours > 0:
            print("  Interval: {} hour(s)".format(interval_hours))
        else:
            print("  Mode: One-time shipment")
        print("")
        print("Proceed? [Y/n]")
        rta = read(values=["y", "Y", "n", "N", ""])
        if rta.lower() == "n":
            return

        enter()

    except KeyboardInterrupt:
        return

    info = "\nDistribute resources from {} to {} cities every {} hour(s)\n".format(
        origin_city['name'], len(destination_cities), interval_hours
    )

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
        for i in range(len(materials_names)):
            print("({:d}) {}".format(i + 1, materials_names[i]))
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
                    materials_names[resource],
                )
            )

        print("\nProceed? [Y/n]")
        rta = read(values=["y", "Y", "n", "N", ""])
        if rta.lower() == "n":
            return

    except KeyboardInterrupt:
        return

    info = "\nDistribute {}\n".format(materials_names[resource])

    return {
        "info": info,
        "run_func": lambda: executeRoutes(session, routes, useFreighters),
    }


def distribute_evenly(session, resource_type, cities_ids, cities):
    """Calculate routes to evenly distribute one resource across cities."""
    resourceTotal = 0

    originCities = {}
    destinationCities = {}
    allCities = {}
    for cityID in cities_ids:
        html = session.get(city_url + cityID)
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

            toSendArr = [0] * len(materials_names)
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


# ---------------------------------------------------------------------------
# Mode 4 — Auto Send
# ---------------------------------------------------------------------------

def autoSendMode(session, telegram_enabled):
    """Auto Send: request specific amounts, pull from all cities."""
    try:
        print_module_banner("Auto Send", "Request resources and auto-collect from all cities")

        print("What type of ships do you want to use?")
        print("(1) Merchant ships")
        print("(2) Freighters")
        print("(') Exit to main menu")
        shiptype = read(min=1, max=2, digit=True, additionalValues=["'"])
        if shiptype == "'":
            return
        useFreighters = (shiptype == 2)

        while True:
            # --- Destination selection ---
            print_module_banner("Auto Send", "Select destination city")
            print("Select the city you want to send resources TO:")
            destination_city = chooseCity(session)

            # Fetch island data for destination (needed for route tuples)
            html = session.get(island_url + destination_city["islandId"])
            destination_island = getIsland(html)

            # --- Fetch all cities and compute totals ---
            print_module_banner("Auto Send", "Scanning cities...")
            city_ids, _ = getIdsOfCities(session)

            suppliers = []
            totals = [0] * len(materials_names)

            for cid in city_ids:
                if str(cid) == str(destination_city["id"]):
                    continue
                html = session.get(city_url + str(cid))
                city_data = getCity(html)
                suppliers.append(city_data)
                for i in range(len(materials_names)):
                    totals[i] += city_data["availableResources"][i]

            if len(suppliers) == 0:
                print("Error: No supplier cities available (destination is your only city).")
                enter()
                return

            print_module_banner("Auto Send", "Available resources")
            print("  Destination: {} [{}:{}]".format(
                destination_city['name'], destination_island['x'], destination_island['y']
            ))
            print("")
            print("  Total available resources (excluding destination):")
            print("    {:<12} {:>12}".format('Resource', 'Available'))
            print("    {} {}".format('-' * 12, '-' * 12))
            for i, resource in enumerate(materials_names):
                print("    {:<12} {:>12}".format(resource, addThousandSeparator(totals[i])))
            print("")

            # --- Resource request input ---
            while True:
                print("  Enter how much of each resource you want to collect:")
                print("  (Leave blank to skip, ' to exit, = to restart)")
                print("")

                requested = [0] * len(materials_names)
                restart = False
                for i, resource in enumerate(materials_names):
                    result = readResourceAmount(resource)
                    if result == 'EXIT':
                        return
                    if result == 'RESTART':
                        restart = True
                        break
                    if result is None or result == 0:
                        requested[i] = 0
                    else:
                        requested[i] = result

                if restart:
                    break  # break inner loop, continue outer (re-select destination)

                if sum(requested) == 0:
                    print("\n  No resources requested. Nothing to do.")
                    enter()
                    return

                # Validate against available totals
                over_limit = []
                for i in range(len(materials_names)):
                    if requested[i] > totals[i]:
                        over_limit.append(
                            "    {}: requested {}, available {}".format(
                                materials_names[i],
                                addThousandSeparator(requested[i]),
                                addThousandSeparator(totals[i]),
                            )
                        )

                if over_limit:
                    print("\n  ERROR: Requested amounts exceed available resources:")
                    for line in over_limit:
                        print(line)
                    print("\n  Please re-enter resource amounts.\n")
                    continue  # re-prompt resource input

                # --- Allocate and review ---
                routes = allocate_from_suppliers(requested, suppliers, destination_city, destination_island)

                if routes is None:
                    print("\n  ERROR: Could not allocate resources across suppliers.")
                    enter()
                    return

                ship_capacity, freighter_capacity = getShipCapacity(session)
                capacity = freighter_capacity if useFreighters else ship_capacity

                choice = render_auto_send_review(
                    destination_city, destination_island, routes, useFreighters, capacity
                )

                if choice == "C":
                    return
                elif choice == "E":
                    break  # break inner loop, continue outer (re-select destination)
                else:
                    # Y — proceed
                    info = "\nAuto-send resources to {}\n".format(destination_city['name'])
                    return {
                        "info": info,
                        "run_func": lambda: do_it_auto_send(
                            session, routes, useFreighters, telegram_enabled
                        ),
                    }
            # If we broke out of inner loop (restart/edit), continue outer while True

    except KeyboardInterrupt:
        return


def allocate_from_suppliers(requested, suppliers, destination_city, destination_island):
    """Allocate requested resources across supplier cities."""
    remaining = list(requested)
    routes = []

    for supplier in suppliers:
        to_send = [0] * len(materials_names)
        has_cargo = False

        for i in range(len(materials_names)):
            if remaining[i] <= 0:
                continue
            can_give = supplier["availableResources"][i]
            give = min(remaining[i], can_give)
            to_send[i] = give
            remaining[i] -= give
            if give > 0:
                has_cargo = True

        if has_cargo:
            route = (
                supplier,
                destination_city,
                destination_island["id"],
                *to_send,
            )
            routes.append(route)

        if all(r <= 0 for r in remaining):
            break

    if any(r > 0 for r in remaining):
        return None

    return routes


def render_auto_send_review(destination_city, destination_island, routes, useFreighters, capacity):
    """Display the shipment plan for user review."""
    ship_type_name = "Freighters" if useFreighters else "Merchant ships"

    print_module_banner("Auto Send", "Review Shipment Plan")
    print("  Destination: {} [{}:{}]".format(
        destination_city['name'], destination_island['x'], destination_island['y']
    ))
    print("  Ship type:   {} (capacity: {})".format(ship_type_name, addThousandSeparator(capacity)))
    print("")
    print("  Planned Shipments:")
    print("  {:<4} {:<18}".format('#', 'From'), end="")
    for resource in materials_names:
        print(" {:>9}".format(resource), end="")
    print(" {:>7}".format('Ships'))
    print("  {:<4} {:<18}".format('--', '------------------'), end="")
    for _ in materials_names:
        print(" {:>9}".format('---------'), end="")
    print(" {:>7}".format('-------'))

    grand_totals = [0] * len(materials_names)
    total_ships = 0

    for idx, route in enumerate(routes):
        origin = route[0]
        amounts = route[3:]
        total_cargo = sum(amounts)
        ships_needed = math.ceil(total_cargo / capacity) if capacity > 0 else 0

        name = origin["name"]
        if len(name) > 18:
            name = name[:15] + "..."

        print("  {:<4} {:<18}".format(idx + 1, name), end="")
        for i in range(len(materials_names)):
            val = amounts[i] if i < len(amounts) else 0
            grand_totals[i] += val
            if val > 0:
                print(" {:>9}".format(addThousandSeparator(val)), end="")
            else:
                print(" {:>9}".format('0'), end="")
        print(" {:>7}".format(ships_needed))
        total_ships += ships_needed

    print("  {:<4} {:<18}".format('--', '------------------'), end="")
    for _ in materials_names:
        print(" {:>9}".format('---------'), end="")
    print(" {:>7}".format('-------'))

    print("  {:4} {:<18}".format('', 'TOTAL'), end="")
    for i in range(len(materials_names)):
        print(" {:>9}".format(addThousandSeparator(grand_totals[i])), end="")
    print(" {:>7}".format(total_ships))
    print("")

    print("  (Y) Proceed with shipments")
    print("  (E) Edit — re-select destination and amounts")
    print("  (C) Cancel — return to main menu")
    choice = read(values=["y", "Y", "e", "E", "c", "C", ""])
    if choice == "" or choice.upper() == "Y":
        return "Y"
    elif choice.upper() == "E":
        return "E"
    else:
        return "C"


def do_it_auto_send(session, routes, useFreighters, telegram_enabled):
    """Execute auto-send shipments (one-shot, per-route locking)."""
    ship_type_name = "freighters" if useFreighters else "merchant ships"
    total_routes = len(routes)
    completed = 0

    print("\n--- Auto Send: executing {} shipments ---\n".format(total_routes))

    for route_index, route in enumerate(routes):
        origin_city = route[0]
        destination_city = route[1]
        amounts = route[3:]
        total_cargo = sum(amounts)

        resources_desc = ", ".join(
            "{} {}".format(addThousandSeparator(amounts[i]), materials_names[i])
            for i in range(len(materials_names)) if i < len(amounts) and amounts[i] > 0
        )

        print("  [{}/{}] {} -> {}".format(
            route_index + 1, total_routes, origin_city['name'], destination_city['name']
        ))
        print("    Resources: {}".format(resources_desc))

        # Wait for ships
        setInfoSignal(session,
            "Auto Send [{}/{}] {} -> {} | Waiting for {}...".format(
                route_index + 1, total_routes, origin_city['name'], destination_city['name'], ship_type_name
            )
        )
        ship_check_start = time.time()

        while True:
            if useFreighters:
                available_ships = getAvailableFreighters(session)
            else:
                available_ships = getAvailableShips(session)

            if available_ships > 0:
                print("    Found {} {}".format(available_ships, ship_type_name))
                break
            else:
                elapsed = int(time.time() - ship_check_start)
                print("    Waiting for {}... (checked for {}s)".format(ship_type_name, elapsed))
                setInfoSignal(session,
                    "Auto Send [{}/{}] | Waiting for {} ({}s)...".format(
                        route_index + 1, total_routes, ship_type_name, elapsed
                    )
                )
                time.sleep(120)

        # Acquire shipping lock
        max_retries = 3
        retry_count = 0
        lock_acquired = False

        while retry_count < max_retries and not lock_acquired:
            print("    Acquiring shipping lock (attempt {}/{})...".format(retry_count + 1, max_retries))
            setInfoSignal(session,
                "Auto Send [{}/{}] | Waiting for shipping lock...".format(route_index + 1, total_routes)
            )
            if acquire_shipping_lock(session, use_freighters=useFreighters, timeout=300):
                lock_acquired = True
                print("    Lock acquired.")
            else:
                retry_count += 1
                if retry_count < max_retries:
                    print("    Lock attempt {}/{} failed, retrying in 60s...".format(retry_count, max_retries))
                    time.sleep(60)

        if not lock_acquired:
            error_msg = "Could not acquire shipping lock after {} attempts".format(max_retries)
            print("    FAILED: {}".format(error_msg))
            if telegram_enabled:
                msg = (
                    "AUTO SEND FAILED\n"
                    "Account: {}\n"
                    "Route [{}/{}]: {} -> {}\n"
                    "Error: {}\n"
                    "Completed: {}/{}\n"
                    "Suggestion: Run Auto Send again for remaining resources"
                ).format(
                    session.username, route_index + 1, total_routes,
                    origin_city['name'], destination_city['name'],
                    error_msg, completed, total_routes,
                )
                sendToBot(session, msg)
            break

        try:
            # Verify ships still available
            if useFreighters:
                ships_before = getAvailableFreighters(session)
            else:
                ships_before = getAvailableShips(session)

            if ships_before == 0:
                print("    Ships became unavailable, stopping")
                if telegram_enabled:
                    msg = (
                        "AUTO SEND STOPPED\n"
                        "Account: {}\n"
                        "Route [{}/{}]: {} -> {}\n"
                        "Problem: Ships became unavailable\n"
                        "Completed: {}/{}"
                    ).format(
                        session.username, route_index + 1, total_routes,
                        origin_city['name'], destination_city['name'],
                        completed, total_routes,
                    )
                    sendToBot(session, msg)
                break

            setInfoSignal(session,
                "Auto Send [{}/{}] {} -> {} | Sending...".format(
                    route_index + 1, total_routes, origin_city['name'], destination_city['name']
                )
            )

            executeRoutes(session, [route], useFreighters)
            completed += 1
            print("    SUCCESS ({}/{})".format(completed, total_routes))

            if telegram_enabled:
                msg = (
                    "Auto Send [{}/{}]\n"
                    "{} -> {}\n"
                    "Resources: {}\n"
                    "Status: Sent"
                ).format(completed, total_routes, origin_city['name'], destination_city['name'], resources_desc)
                sendToBot(session, msg)

        except Exception as send_error:
            error_msg = str(send_error)
            print("    FAILED: {}".format(error_msg))
            if telegram_enabled:
                msg = (
                    "AUTO SEND FAILED\n"
                    "Account: {}\n"
                    "Route [{}/{}]: {} -> {}\n"
                    "Error: {}\n"
                    "Completed: {}/{}\n"
                    "Suggestion: Run Auto Send again for remaining resources"
                ).format(
                    session.username, route_index + 1, total_routes,
                    origin_city['name'], destination_city['name'],
                    error_msg, completed, total_routes,
                )
                sendToBot(session, msg)
            break
        finally:
            release_shipping_lock(session, use_freighters=useFreighters)

    print("\n--- Auto Send complete: {}/{} shipments sent ---".format(completed, total_routes))
    setInfoSignal(session, "Auto Send complete: {}/{} to {}".format(completed, total_routes, destination_city['name']))


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

        print("\n--- Starting shipment cycle ---")

        if notify_on_start:
            total_resources_this_cycle = [0] * len(materials_names)
            grand_total_this_cycle = 0

            for origin_city in origin_cities:
                html_temp = session.get(city_url + str(origin_city['id']))
                origin_city_temp = getCity(html_temp)

                for i, resource in enumerate(materials_names):
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
                    resources_list.append("{} {}".format(addThousandSeparator(amount), materials_names[i]))

            if resources_list:
                source_cities_names = ', '.join([city['name'] for city in origin_cities])
                start_msg = "SHIPMENT STARTING\nAccount: {}\nFrom: {}\nTo: [{}:{}] {}\nShip type: {}\nTotal resources: {}\nGrand total: {}".format(
                    session.username, source_cities_names, island['x'], island['y'],
                    destination_city['name'], ship_type_name,
                    ', '.join(resources_list), addThousandSeparator(grand_total_this_cycle),
                )
                sendToBot(session, start_msg)

        print("  Fetching destination city data...")
        html = session.get(city_url + str(destination_city['id']))
        destination_city = getCity(html)

        for city_index, origin_city in enumerate(origin_cities):
            print("\n  [{}/{}] Processing: {}".format(city_index + 1, len(origin_cities), origin_city['name']))
            html = session.get(city_url + str(origin_city['id']))
            origin_city = getCity(html)

            toSend = [0] * len(materials_names)
            total_to_send = 0

            for i, resource in enumerate(materials_names):
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
                    "{} {}".format(addThousandSeparator(toSend[i]), materials_names[i])
                    for i in range(len(materials_names)) if toSend[i] > 0
                )
                print("    Sending: {}".format(resources_desc))

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
                        print("    Found {} {}".format(available_ships, ship_type))
                        setInfoSignal(session,
                            "{} -> {} | Found {} {}, attempting to send...".format(
                                origin_city['name'], destination_city['name'], available_ships, ship_type
                            )
                        )
                    else:
                        wait_time = 120
                        elapsed = int(time.time() - ship_check_start)
                        print("    Waiting for {}... (checked for {}s)".format(ship_type, elapsed))
                        setInfoSignal(session,
                            "{} -> {} | Waiting for {} (checked for {}s)...".format(
                                origin_city['name'], destination_city['name'], ship_type, elapsed
                            )
                        )
                        time.sleep(wait_time)

                max_retries = 3
                retry_count = 0
                lock_acquired = False

                while retry_count < max_retries and not lock_acquired:
                    print("    Acquiring shipping lock (attempt {}/{})...".format(retry_count + 1, max_retries))
                    setInfoSignal(session,
                        "{} -> {} | Waiting for shipping lock (attempt {}/{})...".format(
                            origin_city['name'], destination_city['name'], retry_count + 1, max_retries
                        )
                    )

                    if acquire_shipping_lock(session, use_freighters=useFreighters, timeout=300):
                        lock_acquired = True
                        print("    Lock acquired.")
                    else:
                        retry_count += 1
                        print("    Lock attempt {}/{} failed, retrying...".format(retry_count, max_retries))
                        if retry_count < max_retries and telegram_enabled:
                            msg = "Account: {}\nFrom: {}\nTo: [{}:{}] {}\nProblem: Failed to acquire shipping lock on attempt {}/{}\nAction: Retrying in 1 minute...".format(
                                session.username, origin_city['name'], island['x'], island['y'],
                                destination_city['name'], retry_count, max_retries,
                            )
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

                        setInfoSignal(session,
                            "{} -> {} | Sending resources...".format(origin_city['name'], destination_city['name'])
                        )

                        if useFreighters:
                            ships_before = getAvailableFreighters(session)
                        else:
                            ships_before = getAvailableShips(session)

                        if ships_before == 0:
                            consecutive_failures += 1
                            print("    Ships became unavailable, skipping")
                            if telegram_enabled:
                                msg = "SHIPMENT DELAYED\nAccount: {}\nFrom: {}\nTo: [{}:{}] {}\nProblem: Ships became unavailable before sending\nAction: Will retry in next cycle".format(
                                    session.username, origin_city['name'], island['x'], island['y'], destination_city['name'],
                                )
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
                                raise Exception("Expected to use {} ships but only {} were used".format(ships_needed, ships_used_actual))

                            total_shipments += 1
                            consecutive_failures = 0

                            resources_sent = []
                            for i, amount in enumerate(toSend):
                                if amount > 0:
                                    resources_sent.append("{} {}".format(addThousandSeparator(amount), materials_names[i]))

                            print("    SENT: {} ({} {})".format(', '.join(resources_sent), ships_needed, ship_type_name))

                            if telegram_enabled:
                                msg = "SHIPMENT SENT\nAccount: {}\nFrom: {}\nTo: [{}:{}] {}\nShips: {} {}\nSent: {}".format(
                                    session.username, origin_city['name'], island['x'], island['y'],
                                    destination_city['name'], ships_needed, ship_type_name,
                                    ', '.join(resources_sent),
                                )
                                sendToBot(session, msg)

                        except Exception as send_error:
                            consecutive_failures += 1
                            error_msg = str(send_error)
                            print("    FAILED: {}".format(error_msg))

                            if telegram_enabled:
                                msg = "SHIPMENT FAILED\nAccount: {}\nFrom: {}\nTo: [{}:{}] {}\nError: {}\nConsecutive failures: {}\nAction: Will retry in next cycle".format(
                                    session.username, origin_city['name'], island['x'], island['y'],
                                    destination_city['name'], error_msg, consecutive_failures,
                                )
                                sendToBot(session, msg)

                    finally:
                        release_shipping_lock(session, use_freighters=useFreighters)
                else:
                    consecutive_failures += 1
                    print("    Could not acquire shipping lock after {} attempts".format(max_retries))
                    if telegram_enabled:
                        msg = "Account: {}\nFrom: {}\nTo: [{}:{}] {}\nProblem: Could not acquire shipping lock\nAttempts: {}\nConsecutive failures: {}\nAction: Skipping this cycle".format(
                            session.username, origin_city['name'], island['x'], island['y'],
                            destination_city['name'], max_retries, consecutive_failures,
                        )
                        sendToBot(session, msg)

                    if consecutive_failures >= 3:
                        alert_msg = "WARNING\nAccount: {}\nFrom: {}\nTo: [{}:{}] {}\nProblem: {} consecutive shipping failures\nPlease check for issues!".format(
                            session.username, origin_city['name'], island['x'], island['y'],
                            destination_city['name'], consecutive_failures,
                        )
                        if telegram_enabled:
                            sendToBot(session, alert_msg)
            else:
                print("    No resources to send (below threshold or no space)")
                if telegram_enabled:
                    msg = "Account: {}\nFrom: {}\nTo: [{}:{}] {}\nStatus: No resources to send (all below thresholds or no space)".format(
                        session.username, origin_city['name'], island['x'], island['y'], destination_city['name'],
                    )
                    sendToBot(session, msg)

        if interval_hours == 0:
            source_cities_names = ', '.join([city['name'] for city in origin_cities])
            print("\n  One-time shipment complete! ({} shipments sent)".format(total_shipments))
            setInfoSignal(session, "One-time shipment completed: {} -> {}".format(source_cities_names, destination_city['name']))
            return

        next_run_time = datetime.datetime.now() + datetime.timedelta(hours=interval_hours)

        source_cities_names = ', '.join([city['name'] for city in origin_cities])
        print("\n  Cycle complete ({} shipments). Next run: {}".format(total_shipments, getDateTime(next_run_time.timestamp())))

        setInfoSignal(session,
            "{} -> {} | Shipments: {} | Next: {}".format(
                source_cities_names, destination_city['name'], total_shipments, getDateTime(next_run_time.timestamp())
            )
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

        print("\n--- Starting distribution cycle ---")

        if notify_on_start:
            total_resources_needed = [amount * len(destination_cities) for amount in resource_config]
            grand_total = sum(total_resources_needed)

            resources_list = []
            for i, amount in enumerate(total_resources_needed):
                if amount > 0:
                    resources_list.append("{} {}".format(addThousandSeparator(amount), materials_names[i]))

            if resources_list:
                dest_names = ', '.join([city['name'] for city in destination_cities])
                start_msg = "SHIPMENT STARTING\nAccount: {}\nFrom: {}\nTo: {} cities ({})\nShip type: {}\nTotal resources: {}\nGrand total: {}".format(
                    session.username, origin_city['name'], len(destination_cities), dest_names,
                    ship_type_name, ', '.join(resources_list), addThousandSeparator(grand_total),
                )
                sendToBot(session, start_msg)

        print("  Fetching source city data...")
        html = session.get(city_url + str(origin_city['id']))
        origin_city = getCity(html)

        origin_island_id = origin_city['islandId']
        html_island = session.get(island_url + str(origin_island_id))
        origin_island = getIsland(html_island)

        for dest_index, destination_city in enumerate(destination_cities):
            print("\n  [{}/{}] Sending to: {}".format(dest_index + 1, len(destination_cities), destination_city['name']))
            html = session.get(city_url + str(destination_city['id']))
            destination_city = getCity(html)

            dest_island_id = destination_city['islandId']
            html_dest_island = session.get(island_url + str(dest_island_id))
            dest_island = getIsland(html_dest_island)

            toSend = [0] * len(materials_names)
            total_to_send = 0

            for i, resource in enumerate(materials_names):
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
                    "{} {}".format(addThousandSeparator(toSend[i]), materials_names[i])
                    for i in range(len(materials_names)) if toSend[i] > 0
                )
                print("    Sending: {}".format(resources_desc))

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
                        print("    Found {} {}".format(available_ships, ship_type))
                        setInfoSignal(session,
                            "{} -> {} | Found {} {}, attempting to send...".format(
                                origin_city['name'], destination_city['name'], available_ships, ship_type
                            )
                        )
                    else:
                        wait_time = 120
                        elapsed = int(time.time() - ship_check_start)
                        print("    Waiting for {}... (checked for {}s)".format(ship_type, elapsed))
                        setInfoSignal(session,
                            "{} -> {} | Waiting for {} (checked for {}s)...".format(
                                origin_city['name'], destination_city['name'], ship_type, elapsed
                            )
                        )
                        time.sleep(wait_time)

                max_retries = 3
                retry_count = 0
                lock_acquired = False

                while retry_count < max_retries and not lock_acquired:
                    print("    Acquiring shipping lock (attempt {}/{})...".format(retry_count + 1, max_retries))
                    setInfoSignal(session,
                        "{} -> {} | Waiting for shipping lock (attempt {}/{})...".format(
                            origin_city['name'], destination_city['name'], retry_count + 1, max_retries
                        )
                    )

                    if acquire_shipping_lock(session, use_freighters=useFreighters, timeout=300):
                        lock_acquired = True
                        print("    Lock acquired.")
                    else:
                        retry_count += 1
                        print("    Lock attempt {}/{} failed, retrying...".format(retry_count, max_retries))
                        if retry_count < max_retries and telegram_enabled:
                            msg = "Account: {}\nFrom: {}\nTo: [{}:{}] {}\nProblem: Failed to acquire shipping lock on attempt {}/{}\nAction: Retrying in 1 minute...".format(
                                session.username, origin_city['name'], dest_island['x'], dest_island['y'],
                                destination_city['name'], retry_count, max_retries,
                            )
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

                        setInfoSignal(session,
                            "{} -> {} | Sending resources...".format(origin_city['name'], destination_city['name'])
                        )

                        if useFreighters:
                            ships_before = getAvailableFreighters(session)
                        else:
                            ships_before = getAvailableShips(session)

                        if ships_before == 0:
                            consecutive_failures += 1
                            print("    Ships became unavailable, skipping")
                            if telegram_enabled:
                                msg = "SHIPMENT DELAYED\nAccount: {}\nFrom: {}\nTo: [{}:{}] {}\nProblem: Ships became unavailable before sending\nAction: Will retry in next cycle".format(
                                    session.username, origin_city['name'], dest_island['x'], dest_island['y'], destination_city['name'],
                                )
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
                                raise Exception("Expected to use {} ships but only {} were used".format(ships_needed, ships_used_actual))

                            total_shipments += 1
                            consecutive_failures = 0

                            resources_sent = []
                            for i, amount in enumerate(toSend):
                                if amount > 0:
                                    resources_sent.append("{} {}".format(addThousandSeparator(amount), materials_names[i]))

                            print("    SENT: {} ({} {})".format(', '.join(resources_sent), ships_needed, ship_type_name))

                            if telegram_enabled:
                                msg = "SHIPMENT SENT\nAccount: {}\nFrom: {}\nTo: [{}:{}] {}\nShips: {} {}\nSent: {}".format(
                                    session.username, origin_city['name'], dest_island['x'], dest_island['y'],
                                    destination_city['name'], ships_needed, ship_type_name,
                                    ', '.join(resources_sent),
                                )
                                sendToBot(session, msg)

                        except Exception as send_error:
                            consecutive_failures += 1
                            error_msg = str(send_error)
                            print("    FAILED: {}".format(error_msg))

                            if telegram_enabled:
                                msg = "SHIPMENT FAILED\nAccount: {}\nFrom: {}\nTo: [{}:{}] {}\nError: {}\nConsecutive failures: {}\nAction: Will retry in next cycle".format(
                                    session.username, origin_city['name'], dest_island['x'], dest_island['y'],
                                    destination_city['name'], error_msg, consecutive_failures,
                                )
                                sendToBot(session, msg)

                    finally:
                        release_shipping_lock(session, use_freighters=useFreighters)
                else:
                    consecutive_failures += 1
                    print("    Could not acquire shipping lock after {} attempts".format(max_retries))
                    if telegram_enabled:
                        msg = "Account: {}\nFrom: {}\nTo: [{}:{}] {}\nProblem: Could not acquire shipping lock\nAttempts: {}\nConsecutive failures: {}\nAction: Skipping this destination".format(
                            session.username, origin_city['name'], dest_island['x'], dest_island['y'],
                            destination_city['name'], max_retries, consecutive_failures,
                        )
                        sendToBot(session, msg)

                    if consecutive_failures >= 3:
                        alert_msg = "WARNING\nAccount: {}\nFrom: {}\nTo: [{}:{}] {}\nProblem: {} consecutive shipping failures\nPlease check for issues!".format(
                            session.username, origin_city['name'], dest_island['x'], dest_island['y'],
                            destination_city['name'], consecutive_failures,
                        )
                        if telegram_enabled:
                            sendToBot(session, alert_msg)
            else:
                print("    No resources to send (insufficient or no space)")
                if telegram_enabled:
                    msg = "Account: {}\nFrom: {}\nTo: [{}:{}] {}\nStatus: No resources to send (insufficient or no space)".format(
                        session.username, origin_city['name'], dest_island['x'], dest_island['y'], destination_city['name'],
                    )
                    sendToBot(session, msg)

        if interval_hours == 0:
            dest_names = ', '.join([city['name'] for city in destination_cities])
            print("\n  One-time distribution complete! ({} shipments sent)".format(total_shipments))
            setInfoSignal(session, "One-time distribution completed: {} -> {}".format(origin_city['name'], dest_names))
            return

        next_run_time = datetime.datetime.now() + datetime.timedelta(hours=interval_hours)

        dest_names = ', '.join([city['name'] for city in destination_cities])
        print("\n  Cycle complete ({} shipments). Next run: {}".format(total_shipments, getDateTime(next_run_time.timestamp())))

        setInfoSignal(session,
            "{} -> {} cities | Shipments: {} | Next: {}".format(
                origin_city['name'], len(destination_cities), total_shipments, getDateTime(next_run_time.timestamp())
            )
        )

        first_run = False
        time.sleep(60 * 60)
