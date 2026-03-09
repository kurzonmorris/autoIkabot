"""Daily Tasks (v1.0) — autoIkabot module.

Combines Shrine of Olympus god-donation (ikabot option 5 / activateShrine)
and daily login bonus collection (ikabot option 6 / loginDaily) into a
single background module that runs both tasks concurrently via threads.
"""

import json
import os
import re
import sys
import time
import threading
import traceback

from autoIkabot.config import ACTION_REQUEST_PLACEHOLDER, CITY_URL
from autoIkabot.helpers.formatting import getDateTime
from autoIkabot.helpers.game_parser import getCity, getIdsOfCities
from autoIkabot.notifications.notify import sendToBot
from autoIkabot.ui.prompts import ReturnToMainMenu, banner, chooseCity, enter, read
from autoIkabot.utils.logging import get_logger
from autoIkabot.utils.process import (
    report_critical_error,
    set_child_mode,
    sleep_with_heartbeat,
)

logger = get_logger(__name__)

# --- Module Metadata ---
MODULE_NAME = "Daily Tasks"
MODULE_SECTION = "Dailies/Regular"
MODULE_NUMBER = 6
MODULE_DESCRIPTION = "Daily login bonus and shrine activation"

# ---------------------------------------------------------------------------
# ANSI colour helpers (for favour task toggle display)
# ---------------------------------------------------------------------------
_BLUE = "\033[94m"
_GREY = "\033[90m"
_ENDC = "\033[0m"

# ---------------------------------------------------------------------------
# Timing constants
# ---------------------------------------------------------------------------
_WAIT_12H = 60 * 60 * 12   # 12 hours in seconds
_WAIT_3H = 60 * 60 * 3     # 3 hours in seconds

# ---------------------------------------------------------------------------
# God name lookup
# ---------------------------------------------------------------------------
_GOD_NAMES = {
    1: "Pan",
    2: "Dionysus",
    3: "Tyche",
    4: "Plutus",
    5: "Theia",
    6: "Hephaestus",
}


def _god_name(god_id: int) -> str:
    return _GOD_NAMES.get(god_id, f"God{god_id}")


# ===========================================================================
# Utility helper
# ===========================================================================

def _time_string_to_sec(time_string: str) -> int:
    """Convert a time string like '5h 30m 45s' to total seconds."""
    hours = re.search(r"(\d+)h", time_string)
    minutes = re.search(r"(\d+)m", time_string)
    seconds = re.search(r"(\d+)s", time_string)
    h = int(hours.group(1)) * 3600 if hours else 0
    m = int(minutes.group(1)) * 60 if minutes else 0
    s = int(seconds.group(1)) if seconds else 0
    return h + m + s


# ===========================================================================
# Shrine helpers  (ported from ikabot activateShrine.py)
# ===========================================================================

def _find_shrine(session):
    """Scan all cities for the Shrine of Olympus building.

    Returns
    -------
    tuple
        ``(city_id, pos)`` of the shrine, or ``(None, None)`` if not found.
    """
    try:
        ids, _ = getIdsOfCities(session)
        for city_id in ids:
            html = session.get(CITY_URL + str(city_id))
            city = getCity(html)
            for pos, building in enumerate(city.get("position", [])):
                if building.get("building") == "shrineOfOlympus":
                    return city_id, pos
    except Exception:
        logger.exception("Error scanning cities for shrine")
    return None, None


def _get_favor(session, city_id, pos) -> int:
    """Return current favour amount from the Shrine of Olympus endpoint."""
    try:
        url = (
            f"view=shrineOfOlympus&cityId={city_id}&position={pos}"
            f"&activeTab=tabOverview&backgroundView=city"
            f"&currentCityId={city_id}&templateView=shrineOfOlympus"
            f"&actionRequest={ACTION_REQUEST_PLACEHOLDER}&ajax=1"
        )
        raw = session.get(url)
        data = json.loads(raw, strict=False)
        return int(data[2][1]["currentFavor"])
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Could not read current favour: %s", exc)
        return 0


def _donate_shrine(session, god_id: int, city_id, pos, selected_gods_str: str) -> None:
    """POST a favour donation to the selected god and update session status."""
    url = (
        f"action=DonateFavorToGod&godId={god_id}&position={pos}"
        f"&backgroundView=city&currentCityId={city_id}"
        f"&templateView=shrineOfOlympus&currentTab=tabOverview"
        f"&actionRequest={ACTION_REQUEST_PLACEHOLDER}&ajax=1"
    )
    session.post(url)
    current_favor = _get_favor(session, city_id, pos)
    ts = getDateTime()
    session.setStatus(
        f"[Shrine] Activated {selected_gods_str} @{ts}, favour: {current_favor}"
    )


def _shrine_loop(session, god_ids: tuple, mode: int, times: int) -> None:
    """Background loop for shrine activation (runs as Thread B).

    Parameters
    ----------
    session : Session
    god_ids : tuple[int]
        IDs of gods to donate to (1–6).
    mode : int
        1 = specific N times only, 2 = autonomous 70h cycle, 3 = both.
    times : int
        Number of specific activations (used when mode is 1 or 3).
    """
    selected_gods_str = ", ".join(_god_name(g) for g in god_ids)
    favor_needed = len(god_ids) * 100

    try:
        city_id, pos = _find_shrine(session)
        if city_id is None:
            sendToBot(
                session,
                "[Daily Tasks] Shrine of Olympus not found in any city — shrine loop stopped.",
            )
            return

        # Mode 1 or 3: donate a specific number of times first
        if mode in (1, 3):
            for _ in range(times):
                favor = _get_favor(session, city_id, pos)
                while favor < favor_needed:
                    session.setStatus(
                        f"[Shrine] Need {favor_needed} favour (have {favor}), retrying in 3h"
                    )
                    sleep_with_heartbeat(session, _WAIT_3H)
                    favor = _get_favor(session, city_id, pos)
                for god_id in god_ids:
                    _donate_shrine(session, god_id, city_id, pos, selected_gods_str)
                    time.sleep(2)
            if mode == 1:
                return  # specific-only mode done

        # Mode 2 or 3 (after N times): autonomous 70-hour cycle
        while True:
            favor = _get_favor(session, city_id, pos)
            while favor < favor_needed:
                session.setStatus(
                    f"[Shrine] Need {favor_needed} favour (have {favor}), retrying in 3h"
                )
                sleep_with_heartbeat(session, _WAIT_3H)
                favor = _get_favor(session, city_id, pos)

            for god_id in god_ids:
                _donate_shrine(session, god_id, city_id, pos, selected_gods_str)
                time.sleep(2)

            # Wait ~70 hours in 12-hour heartbeat chunks (6 × 12h = 72h)
            for _ in range(6):
                sleep_with_heartbeat(session, _WAIT_12H)
                current_favor = _get_favor(session, city_id, pos)
                ts = getDateTime()
                session.setStatus(
                    f"[Shrine] Last: {selected_gods_str} @{ts}, favour: {current_favor}"
                )

    except Exception:
        msg = f"[Daily Tasks] Shrine loop error:\n{traceback.format_exc()}"
        logger.error(msg)
        report_critical_error(session, MODULE_NAME, msg)


# ===========================================================================
# Login-daily helpers  (ported from ikabot loginDaily.py)
# ===========================================================================

def _is_collectable(row: str) -> bool:
    """Return True if the task row shows completed progress (left == right)."""
    try:
        left = (
            re.search(r"smallright progress details([\S\s]*?)>([\S\s]*?)<", row)
            .group(2)
            .strip()
            .replace(",", "")
        )
        right = (
            re.search(r"left small progress details([\S\s]*?)>([\S\s]*?)<", row)
            .group(2)
            .strip()
            .replace(",", "")
        )
        return "textLineThrough" not in row and left == right
    except (AttributeError, IndexError):
        return False


def _get_remaining_time_cinetheatre(features: list) -> int:
    """Return earliest remaining cooldown (seconds) across three cinetheatre features."""
    ewt = 999_999_999
    for i, feature in enumerate(["Resource", "Tradegood", "Favour"]):
        try:
            if features[i] and f"js_nextPossible{feature}" in features[i]:
                m = re.search(
                    rf'js_nextPossible{feature}\\">([\S\s]*?)<', features[i]
                )
                if m:
                    sec = _time_string_to_sec(m.group(1).strip()) + 60
                    if sec < ewt:
                        ewt = sec
        except (IndexError, AttributeError):
            continue
    return ewt


def _collect_resource_favour(session, table: list, wine_city: dict, ewt_ref: list) -> None:
    """Collect the two passive resource favour tasks if their progress is complete."""
    for row in table[:2]:
        try:
            if _is_collectable(row):
                task_id = re.search(r'taskId=([\S\s]*?)\\"', row).group(1)
                session.post(
                    f"action=CollectDailyTasksFavor&taskId={task_id}&ajax=1"
                    f"&backgroundView=city&currentCityId={wine_city['id']}"
                    f"&templateView=dailyTasks&actionRequest={ACTION_REQUEST_PLACEHOLDER}&ajax=1"
                )
                time.sleep(1)
        except (AttributeError, KeyError, IndexError):
            pass


def _look(session, table: list, wine_city: dict, ewt_ref: list) -> None:
    """Visit highscore / shop / inventory pages and collect those favour tasks."""
    _TASK_MAP = {
        "task_amount_28": (
            "28",
            f"view=premium&linkType=2&backgroundView=city"
            f"&currentCityId={wine_city['id']}&templateView=dailyTasks"
            f"&actionRequest={ACTION_REQUEST_PLACEHOLDER}&ajax=1",
        ),
        "task_amount_27": (
            "27",
            f"view=inventory&backgroundView=city"
            f"&currentCityId={wine_city['id']}&templateView=dailyTasks"
            f"&actionRequest={ACTION_REQUEST_PLACEHOLDER}&ajax=1",
        ),
        "task_amount_26": (
            "26",
            f"view=highscore&showMe=1&backgroundView=city"
            f"&currentCityId={wine_city['id']}"
            f"&actionRequest={ACTION_REQUEST_PLACEHOLDER}&ajax=1",
        ),
    }
    for row in table:
        for marker, (task_id, visit_url) in _TASK_MAP.items():
            if marker not in row:
                continue
            try:
                collect_url = (
                    f"action=CollectDailyTasksFavor&taskId={task_id}&ajax=1"
                    f"&backgroundView=city&currentCityId={wine_city['id']}"
                    f"&templateView=dailyTasks&actionRequest={ACTION_REQUEST_PLACEHOLDER}&ajax=1"
                )
                if _is_collectable(row):
                    session.post(collect_url)
                    time.sleep(1)
                else:
                    session.post(visit_url)
                    time.sleep(1)
                    session.post(collect_url)
                    time.sleep(1)
            except Exception:
                pass


def _stay_online_30_mins(session, table: list, wine_city: dict, ewt_ref: list) -> None:
    """Collect the stay-online-30-mins task if done, or schedule an earlier wakeup."""
    for row in table:
        if "task_amount_23" not in row:
            continue
        try:
            if _is_collectable(row):
                session.post(
                    f"action=CollectDailyTasksFavor&taskId=23&ajax=1"
                    f"&backgroundView=city&currentCityId={wine_city['id']}"
                    f"&templateView=dailyTasks&actionRequest={ACTION_REQUEST_PLACEHOLDER}&ajax=1"
                )
                time.sleep(1)
            else:
                if 31 * 60 < ewt_ref[0]:
                    ewt_ref[0] = 31 * 60
        except Exception:
            pass


def _complete_tasks(session, table: list, wine_city: dict, ewt_ref: list) -> None:
    """Sweep all remaining collectable tasks in the table. Should run last."""
    for row in table:
        try:
            if _is_collectable(row):
                task_id = re.search(r'taskId=([\S\s]*?)\\"', row).group(1)
                session.post(
                    f"action=CollectDailyTasksFavor&taskId={task_id}&ajax=1"
                    f"&backgroundView=city&currentCityId={wine_city['id']}"
                    f"&templateView=dailyTasks&actionRequest={ACTION_REQUEST_PLACEHOLDER}&ajax=1"
                )
                time.sleep(1)
        except (AttributeError, IndexError):
            pass


# Task registry (order matters — _complete_tasks must be last)
_FAVOUR_TASKS = {
    "Collect resource favour":          _collect_resource_favour,
    "Look at highscore/shop/inventory": _look,
    "Stay online for 30 mins":          _stay_online_30_mins,
    "Complete 2/all tasks":             _complete_tasks,
}


def _login_daily_loop(
    session,
    wine_city: dict,
    wood_city,
    luxury_city,
    active_favour_tasks: list,
) -> None:
    """Background loop for daily login bonus collection (runs as Thread A).

    Parameters
    ----------
    session : Session
    wine_city : dict
        City that receives the daily wine bonus.
    wood_city : dict or None
        City for the wood cinetheatre bonus (optional).
    luxury_city : dict or None
        City for the luxury resource cinetheatre bonus (optional).
    active_favour_tasks : list[str]
        Task names from ``_FAVOUR_TASKS`` to run each cycle.
    """
    message_sent = False

    while True:
        try:
            # mutable single-element list used as a pass-by-reference value
            ewt_ref = [24 * 60 * 60]  # default: check again in 24 hours
            favour_cinetheater_city = None

            # ------------------------------------------------------------------
            # 1. Collect daily activity bonus (wine)
            # ------------------------------------------------------------------
            try:
                session.post(CITY_URL + str(wine_city["id"]))
                session.post(
                    f"action=AvatarAction&function=giveDailyActivityBonus"
                    f"&dailyActivityBonusCitySelect={wine_city['id']}"
                    f"&startPageShown=1&detectedDevice=1&autoLogin=on"
                    f"&cityId={wine_city['id']}&activeTab=multiTab2"
                    f"&backgroundView=city&currentCityId={wine_city['id']}"
                    f"&actionRequest={ACTION_REQUEST_PLACEHOLDER}&ajax=1"
                )
            except Exception:
                logger.warning("Could not collect daily wine bonus", exc_info=True)

            # ------------------------------------------------------------------
            # 2. Cinetheatre: wood production bonus
            # ------------------------------------------------------------------
            if wood_city:
                try:
                    session.post(CITY_URL + str(wood_city["id"]))
                    html = session.post(
                        f"view=cinema&visit=1&currentCityId={wood_city['id']}"
                        f"&backgroundView=city&actionRequest={ACTION_REQUEST_PLACEHOLDER}&ajax=1"
                    )
                    features = (
                        html.split('id=\\"VideoRewards\\"')[1].split("ul>")[0].split("li>")
                    )
                    features = [f for f in features if "form" in f or "js_nextPossible" in f]
                    if "js_nextPossibleResource" in features[0]:
                        t = _get_remaining_time_cinetheatre(features)
                        if t < ewt_ref[0]:
                            ewt_ref[0] = t
                    else:
                        m = re.search(r'name=\\"videoId\\"\s*value=\\"(\d+)\\"', features[0])
                        if m:
                            video_id = m.group(1)
                            session.post(
                                f"view=noViewChange&action=AdVideoRewardAction"
                                f"&function=requestBonus&bonusId=51&videoId={video_id}"
                                f"&backgroundView=city&currentCityId={wood_city['id']}"
                                f"&templateView=cinema&actionRequest={ACTION_REQUEST_PLACEHOLDER}&ajax=1"
                            )
                            session.setStatus("Waiting 55s to watch video for wood bonus")
                            time.sleep(55)
                            session.post(
                                f"view=noViewChange&action=AdVideoRewardAction"
                                f"&function=watchVideo&videoId={video_id}"
                                f"&backgroundView=city&currentCityId={wood_city['id']}"
                                f"&templateView=cinema&actionRequest={ACTION_REQUEST_PLACEHOLDER}&ajax=1"
                            )
                    favour_cinetheater_city = wood_city
                except Exception:
                    logger.warning("Could not collect wood cinetheatre bonus", exc_info=True)
                time.sleep(1)

            # ------------------------------------------------------------------
            # 3. Cinetheatre: luxury resource bonus
            # ------------------------------------------------------------------
            if luxury_city:
                try:
                    session.post(CITY_URL + str(luxury_city["id"]))
                    html = session.post(
                        f"view=cinema&visit=1&currentCityId={luxury_city['id']}"
                        f"&backgroundView=city&actionRequest={ACTION_REQUEST_PLACEHOLDER}&ajax=1"
                    )
                    features = (
                        html.split('id=\\"VideoRewards\\"')[1].split("ul>")[0].split("li>")
                    )
                    features = [f for f in features if "form" in f or "js_nextPossible" in f]
                    if "js_nextPossibleTradegood" in features[1]:
                        t = _get_remaining_time_cinetheatre(features)
                        if t < ewt_ref[0]:
                            ewt_ref[0] = t
                    else:
                        m = re.search(r'name=\\"videoId\\"\s*value=\\"(\d+)\\"', features[1])
                        if m:
                            video_id = m.group(1)
                            session.post(
                                f"view=noViewChange&action=AdVideoRewardAction"
                                f"&function=requestBonus&bonusId=52&videoId={video_id}"
                                f"&backgroundView=city&currentCityId={luxury_city['id']}"
                                f"&templateView=cinema&actionRequest={ACTION_REQUEST_PLACEHOLDER}&ajax=1"
                            )
                            session.setStatus("Waiting 55s to watch video for luxury good bonus")
                            time.sleep(55)
                            session.post(
                                f"view=noViewChange&action=AdVideoRewardAction"
                                f"&function=watchVideo&videoId={video_id}"
                                f"&backgroundView=city&currentCityId={luxury_city['id']}"
                                f"&templateView=cinema&actionRequest={ACTION_REQUEST_PLACEHOLDER}&ajax=1"
                            )
                    favour_cinetheater_city = luxury_city
                except Exception:
                    logger.warning("Could not collect luxury cinetheatre bonus", exc_info=True)
                time.sleep(1)

            # ------------------------------------------------------------------
            # 4. Cinetheatre: favour bonus
            # ------------------------------------------------------------------
            if favour_cinetheater_city:
                try:
                    session.post(CITY_URL + str(favour_cinetheater_city["id"]))
                    html = session.post(
                        f"view=cinema&visit=1&currentCityId={favour_cinetheater_city['id']}"
                        f"&backgroundView=city&actionRequest={ACTION_REQUEST_PLACEHOLDER}&ajax=1"
                    )
                    features = (
                        html.split('id=\\"VideoRewards\\"')[1].split("ul>")[0].split("li>")
                    )
                    features = [f for f in features if "form" in f or "js_nextPossible" in f]
                    if "js_nextPossibleFavour" in features[2]:
                        t = _get_remaining_time_cinetheatre(features)
                        if t < ewt_ref[0]:
                            ewt_ref[0] = t
                    else:
                        m = re.search(r'name=\\"videoId\\"\s*value=\\"(\d+)\\"', features[2])
                        if m:
                            video_id = m.group(1)
                            session.post(
                                f"view=noViewChange&action=AdVideoRewardAction"
                                f"&function=requestBonus&bonusId=53&videoId={video_id}"
                                f"&backgroundView=city&currentCityId={favour_cinetheater_city['id']}"
                                f"&templateView=cinema&actionRequest={ACTION_REQUEST_PLACEHOLDER}&ajax=1"
                            )
                            session.setStatus(
                                "Waiting 55s to watch video for favour cultural bonus"
                            )
                            time.sleep(55)
                            session.post(
                                f"view=noViewChange&action=AdVideoRewardAction"
                                f"&function=watchVideo&videoId={video_id}"
                                f"&backgroundView=city&currentCityId={favour_cinetheater_city['id']}"
                                f"&templateView=cinema&actionRequest={ACTION_REQUEST_PLACEHOLDER}&ajax=1"
                            )
                except Exception:
                    logger.warning("Could not collect favour cinetheatre bonus", exc_info=True)
                time.sleep(1)

            # ------------------------------------------------------------------
            # 5. Refresh cinetheatre cooldown timers for scheduling
            # ------------------------------------------------------------------
            if wood_city or luxury_city or favour_cinetheater_city:
                try:
                    session.post(CITY_URL + str(wine_city["id"]))
                    html = session.post(
                        f"view=cinema&visit=1&currentCityId={wine_city['id']}"
                        f"&backgroundView=city&actionRequest={ACTION_REQUEST_PLACEHOLDER}&ajax=1"
                    )
                    features = (
                        html.split('id=\\"VideoRewards\\"')[1].split("ul>")[0].split("li>")
                    )
                    features = [f for f in features if "form" in f or "js_nextPossible" in f]
                    t = _get_remaining_time_cinetheatre(features)
                    if t < ewt_ref[0]:
                        ewt_ref[0] = t
                except Exception:
                    logger.warning("Could not read cinetheatre cooldown times", exc_info=True)
                time.sleep(1)

            # ------------------------------------------------------------------
            # 6. Favour tasks
            # ------------------------------------------------------------------
            if active_favour_tasks:
                try:
                    session.post(CITY_URL + str(wine_city["id"]))
                    html = session.post(
                        f"view=dailyTasks&backgroundView=city"
                        f"&currentCityId={wine_city['id']}"
                        f"&actionRequest={ACTION_REQUEST_PLACEHOLDER}&ajax=1"
                    )
                    match = re.search(r"currentFavor([\S\s]*?)(\d+)\s*<", html)
                    if not match:
                        logger.warning("Cannot obtain current favour amount from HTML")
                    elif match.group(2) == "2500":
                        if not message_sent:
                            sendToBot(
                                session,
                                "[Daily Tasks] Favour not collected — already at cap (2500)",
                            )
                            message_sent = True
                    else:
                        message_sent = False
                        matches = re.findall("<tr([\S\s]*?)tr>", html)
                        if len(matches) >= 12:
                            rows = [
                                matches[1], matches[2],
                                matches[4], matches[5],
                                matches[7], matches[8],
                                matches[10], matches[11],
                            ]
                            for task_name in active_favour_tasks:
                                task_fn = _FAVOUR_TASKS.get(task_name)
                                if task_fn:
                                    try:
                                        task_fn(session, rows, wine_city, ewt_ref)
                                    except Exception:
                                        logger.warning(
                                            "Error running favour task '%s'",
                                            task_name,
                                            exc_info=True,
                                        )
                        # Update wakeup time from daily tasks countdown
                        m2 = re.search(
                            r'"dailyTasksCountdown":\{"countdown":\{"enddate":(\d+),"currentdate":(\d+)',
                            html,
                        )
                        if m2:
                            sec_remaining = (int(m2.group(1)) - int(m2.group(2))) - 600
                            if sec_remaining < ewt_ref[0]:
                                ewt_ref[0] = sec_remaining
                except Exception:
                    logger.warning("Error processing favour tasks", exc_info=True)

            # ------------------------------------------------------------------
            # 7. Collect ambrosia fountain bonus from capital if active
            # ------------------------------------------------------------------
            try:
                ids, _ = getIdsOfCities(session)
                for city_id in ids:
                    html = session.post(CITY_URL + str(city_id))
                    if 'class="fountain' in html:
                        if 'class="fountain_active' in html:
                            session.post(
                                f"action=AmbrosiaFountainActions&function=collect"
                                f"&backgroundView=city&currentCityId={city_id}"
                                f"&templateView=ambrosiaFountain"
                                f"&actionRequest={ACTION_REQUEST_PLACEHOLDER}&ajax=1"
                            )
                        break
                    time.sleep(1)
            except Exception:
                logger.warning("Could not check ambrosia fountain", exc_info=True)

            # ------------------------------------------------------------------
            # 8. Sleep until next cycle
            # ------------------------------------------------------------------
            ewt = abs(ewt_ref[0])
            next_ts = getDateTime(time.time() + ewt)
            session.setStatus(
                f"[Daily] Last activity @{getDateTime()}, next @{next_ts}"
            )
            sleep_with_heartbeat(session, ewt)

        except Exception:
            msg = f"[Daily Tasks] Unexpected error in login daily loop:\n{traceback.format_exc()}"
            logger.error(msg)
            report_critical_error(session, MODULE_NAME, msg)
            sleep_with_heartbeat(session, 3600)  # retry after 1 hour


# ===========================================================================
# Interactive config helpers
# ===========================================================================

def _configure_login_daily(session):
    """Prompt the user to configure the daily login bonus section.

    Returns
    -------
    tuple
        ``(wine_city, wood_city, luxury_city, active_favour_tasks)``
    """
    print("\n--- Daily Login Bonus ---")
    print("Choose the city where the daily login bonus wine will be sent:")
    wine_city = chooseCity(session)

    wood_city = None
    luxury_city = None
    print("Do you want to automatically activate the cinetheatre bonus? (Y|N)")
    if read(values=["y", "Y", "n", "N"]).lower() == "y":
        print("Choose the city where the wood bonus will be activated:")
        wood_city = chooseCity(session)
        print("Choose the city where the luxury resource bonus will be activated:")
        luxury_city = chooseCity(session)

    active_favour_tasks = []
    print("Do you want to collect the favour automatically? (Y|N)")
    if read(values=["y", "Y", "n", "N"]).lower() == "y":
        active_favour_tasks = list(_FAVOUR_TASKS.keys())

        def _modify_tasks():
            banner()
            print("Choose which daily tasks will be done automatically.")
            print(
                f"Tasks in {_BLUE}blue{_ENDC} WILL be done, "
                f"tasks in {_GREY}grey{_ENDC} will NOT be done."
            )
            print("Press [ENTER] or type [Y] to confirm selection.")
            task_list = list(_FAVOUR_TASKS.keys())
            for i, name in enumerate(task_list):
                colour = _BLUE if name in active_favour_tasks else _GREY
                print(f"  {i + 1}) {colour}{name}{_ENDC}")
            choice = read(
                min=1,
                max=len(task_list),
                empty=True,
                digit=True,
                additionalValues=["y", "Y"],
            )
            if not choice or (isinstance(choice, str) and choice.lower() == "y"):
                return
            idx = int(choice) - 1
            name = task_list[idx]
            if name in active_favour_tasks:
                active_favour_tasks.remove(name)
            else:
                active_favour_tasks.append(name)
            _modify_tasks()

        _modify_tasks()

    return wine_city, wood_city, luxury_city, active_favour_tasks


def _configure_shrine():
    """Prompt the user to configure shrine activation (optional).

    Returns
    -------
    tuple
        ``(god_ids, mode, times)`` — or ``([], 0, 0)`` if the user skips.
    """
    print("\n--- Shrine of Olympus (optional) ---")
    print("Which God(s) would you like to activate autonomously?")
    print("  (0) Skip shrine activation")
    print("  (1) Pan        (Wood)")
    print("  (2) Dionysus   (Wine)")
    print("  (3) Tyche      (Marble)")
    print("  (4) Plutus     (Gold)")
    print("  (5) Theia      (Crystal)")
    print("  (6) Hephaestus (Sulphur)")

    god_ids = []
    while True:
        choice = read(min=0, max=6, digit=True)
        if choice == 0:
            break
        if choice not in god_ids:
            god_ids.append(choice)
            print(f"  Added {_god_name(choice)}. Select another or (0) to continue.")

    if not god_ids:
        return [], 0, 0

    gods_str = ", ".join(_god_name(g) for g in god_ids)
    print(f"Selected: {gods_str}")
    print("\nActivation mode:")
    print("  (1) Specific number of times")
    print("  (2) Autonomously every 70 hours")
    print("  (3) Both (N times, then autonomous)")
    mode = read(min=1, max=3, digit=True)

    times = 0
    if mode in (1, 3):
        print("How many times would you like to activate the selected god(s)?")
        times = read(min=1, max=10, digit=True)

    return god_ids, mode, times


# ===========================================================================
# Module entry point
# ===========================================================================

def dailyTasks(session, event, stdin_fd) -> None:
    """Background module entry point for Daily Tasks.

    Parameters
    ----------
    session : Session
        Active game session.
    event : multiprocessing.Event
        Signalled to the parent process once interactive config is complete.
    stdin_fd : int
        File descriptor for stdin inherited from the parent process.
    """
    sys.stdin = os.fdopen(stdin_fd)
    try:
        banner()
        wine_city, wood_city, luxury_city, active_favour_tasks = _configure_login_daily(
            session
        )
        god_ids, mode, times = _configure_shrine()

        print("\nDaily Tasks configured. Starting background loop.")
        enter()

    except ReturnToMainMenu:
        event.set()
        return
    except KeyboardInterrupt:
        event.set()
        return

    set_child_mode(session)
    event.set()

    # Launch concurrent background threads
    threads = []

    t_login = threading.Thread(
        target=_login_daily_loop,
        args=(session, wine_city, wood_city, luxury_city, active_favour_tasks),
        daemon=True,
    )
    threads.append(t_login)

    if god_ids:
        t_shrine = threading.Thread(
            target=_shrine_loop,
            args=(session, tuple(god_ids), mode, times),
            daemon=True,
        )
        threads.append(t_shrine)

    for t in threads:
        t.start()
    for t in threads:
        t.join()
