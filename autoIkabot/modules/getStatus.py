"""Game Status module — view resource totals, production, and city details.

Ported from ikabot's function/getStatus.py for autoIkabot.
"""

from decimal import Decimal, getcontext

from autoIkabot.config import CITY_URL, MATERIALS_NAMES
from autoIkabot.helpers.formatting import addThousandSeparator, daysHoursMinutes
from autoIkabot.helpers.game_parser import getCity, getIdsOfCities
from autoIkabot.helpers.game_state import fetch_game_state, getProductionPerHour
from autoIkabot.ui.prompts import banner, chooseCity, enter
from autoIkabot.utils.logging import get_logger

logger = get_logger(__name__)

getcontext().prec = 30

MODULE_NAME = "Game Status"
MODULE_SECTION = "Spy/Monitoring"
MODULE_NUMBER = 19
MODULE_DESCRIPTION = "View empire status (resources, production, buildings)"


def getStatus(session) -> None:
    """Display empire-wide status and per-city details.

    Parameters
    ----------
    session : Session
        The game session.
    """
    banner()

    (ids, _) = getIdsOfCities(session)
    total_resources = [0] * len(MATERIALS_NAMES)
    total_production = [0] * len(MATERIALS_NAMES)
    total_wine_consumption = 0
    city_population = {}
    total_housing_space = 0
    total_citizens = 0
    total_gold = 0
    total_gold_production = 0
    available_ships = 0
    total_ships = 0

    print("Fetching data for all cities...")
    for city_id in ids:
        state = fetch_game_state(session, city_id)

        # Check if this is our own city
        related = state.raw.get("relatedCity", {})
        if related.get("owncity") != 1:
            continue

        total_production[0] += state.resource_production
        if 1 <= state.produced_tradegood <= 4:
            total_production[state.produced_tradegood] += state.tradegood_production
        total_wine_consumption += state.wine_consumption

        housing_space = state.population
        city_population[city_id] = {"housing_space": housing_space}
        total_housing_space += housing_space
        total_citizens += state.citizens

        for i in range(5):
            total_resources[i] += state.resources[i]

        available_ships = state.free_transporters
        total_ships = state.max_transporters
        total_gold = state.gold
        total_gold_production = state.gold_production

    # --- Empire-wide summary ---
    print(f"\nShips {int(available_ships)}/{int(total_ships)}")
    print("\nTotal:")

    # Header row
    print(f"{'':>10}", end="|")
    for name in MATERIALS_NAMES:
        print(f"{name:>12}", end="|")
    print()

    # Available resources
    print(f"{'Available':>10}", end="|")
    for i in range(len(MATERIALS_NAMES)):
        print(f"{addThousandSeparator(total_resources[i]):>12}", end="|")
    print()

    # Production
    print(f"{'Production':>10}", end="|")
    for i in range(len(MATERIALS_NAMES)):
        print(f"{addThousandSeparator(int(total_production[i])):>12}", end="|")
    print()

    print(
        f"Housing Space: {addThousandSeparator(total_housing_space)}, "
        f"Citizens: {addThousandSeparator(total_citizens)}"
    )
    print(
        f"Gold: {addThousandSeparator(total_gold)}, "
        f"Gold production: {addThousandSeparator(total_gold_production)}"
    )
    print(f"Wine consumption: {addThousandSeparator(total_wine_consumption)}")

    # --- Per-city details ---
    print("\nOf which city do you want to see the state?")
    city = chooseCity(session)
    city_id = city["id"]
    banner()

    (wood, good, typeGood) = getProductionPerHour(session, city_id)

    type_name = MATERIALS_NAMES[typeGood] if 1 <= typeGood <= 4 else "?"
    print(f"\n  {city['cityName']} ({type_name})")
    print("  " + "=" * 40)

    resources = city["availableResources"]
    storage_cap = city["storageCapacity"]
    free_citizens = city["freeCitizens"]

    housing = city_population.get(city_id, {}).get("housing_space", 0)
    print(f"\n  Population: Housing space: {addThousandSeparator(housing)}, "
          f"Citizens: {addThousandSeparator(free_citizens)}")
    print(f"  Storage: {addThousandSeparator(storage_cap)}")

    print("  Resources:")
    for i in range(len(MATERIALS_NAMES)):
        full_marker = " [FULL]" if resources[i] >= storage_cap else ""
        print(f"    {MATERIALS_NAMES[i]}: {addThousandSeparator(resources[i])}{full_marker}")

    print(f"\n  Production:")
    print(f"    {MATERIALS_NAMES[0]}: {addThousandSeparator(wood)}/h")
    if 1 <= typeGood <= 4:
        print(f"    {MATERIALS_NAMES[typeGood]}: {addThousandSeparator(good)}/h")

    # Wine consumption and time remaining
    has_tavern = "tavern" in [
        b.get("building", "") for b in city.get("position", [])
    ]
    if has_tavern:
        consumption = city["wineConsumptionPerHour"]
        if consumption == 0:
            print("\n  WARNING: Does not consume wine!")
        else:
            if typeGood == 1 and (good * 3600) > consumption:
                elapsed = "infinity (producing more than consuming)"
            else:
                consumption_per_sec = Decimal(consumption) / Decimal(3600)
                if consumption_per_sec > 0:
                    remaining_sec = Decimal(resources[1]) / consumption_per_sec
                    elapsed = daysHoursMinutes(remaining_sec)
                else:
                    elapsed = "infinity"
            print(f"  Wine remaining for: {elapsed}")

    # Building list
    print(f"\n  Buildings:")
    for building in city.get("position", []):
        name = building.get("name", building.get("building", ""))
        if name == "empty" or not name:
            continue
        level = building.get("level", 0)
        busy = "+" if building.get("isBusy") else ""
        can_upgrade = building.get("canUpgrade", False)
        is_max = building.get("isMaxLevel", False)
        status = " [MAX]" if is_max else (" [CAN UPGRADE]" if can_upgrade else "")
        print(f"    lv:{level:>2}{busy}\t{name}{status}")

    enter()
