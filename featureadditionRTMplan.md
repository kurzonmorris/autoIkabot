# Feature Addition Plan: Auto Send (Mode 4) — Resource Transport Manager v1.1

## Overview

Add a **4th shipping mode** to `resourceTransportManager.py` called **Auto Send**. The user picks a destination city, specifies how much of each resource they want, and the script automatically pulls those resources from all other cities and ships them to the destination.

This is a **one-shot** operation (like Even Distribution mode), not a recurring background loop.

---

## Decisions Made (from analysis)

These decisions were confirmed after reviewing the existing codebase and a saved game HTML file (`transporting goods.zip`) against the original feature proposal:

| # | Topic | Decision | Reason |
|---|-------|----------|--------|
| 1 | Ship capacities | Use `getShipCapacity(session)` — **never hardcode** | Capacity varies per account (580/52000 observed, not 500/50000) |
| 2 | Shipment model | **Multi-resource per city** — one route sends all 5 resources | Game POST sends `cargo_resource` + `cargo_tradegood1-4` together |
| 3 | Distance sorting | **Removed** — not needed | User decision |
| 4 | 25% fill rule | **Removed** — not needed | Adds complexity for no benefit; `executeRoutes()` handles splitting |
| 5 | API to use | `executeRoutes(session, routes, useFreighters)` with route tuples | Matches existing codebase; handles ship waiting and capacity splitting internally |
| 6 | Resource keys | **Index-based arrays** `[0]=Wood, [1]=Wine, [2]=Marble, [3]=Crystal, [4]=Sulfur` | Matches `MATERIALS_NAMES` and every other part of the module |
| 7 | Locking | **Per-route** acquire/release (not held across all routes) | Matches existing `do_it()` pattern; avoids blocking other modules |
| 8 | Execution model | **One-shot** — execute and return | Like Even Distribution mode (line 934: `run_func: lambda: executeRoutes(...)`) |
| 9 | Travel time/ETA | **Not included** | Server-computed only, would require extra HTTP requests per city pair |

---

## User Flow

```
Menu: (4) Auto Send: Request resources and auto-collect from all cities

Step 1: Ship type selection
  → (1) Merchant ships  or  (2) Freighters

Step 2: Destination city selection
  → Uses existing chooseCity(session) — picks from own cities

Step 3: Totals screen
  → Fetches all cities, shows total available resources across all non-destination cities
  → Table format: Resource | Total Available

Step 4: Resource request input
  → Sequential prompts per resource (reuse existing readResourceAmount())
  → Validates: non-negative, doesn't exceed total available
  → Supports: blank=skip, '=restart, '=exit (existing UX patterns)

Step 5: Allocation + Review screen
  → Algorithm allocates requested amounts across supplier cities
  → Shows per-shipment breakdown: From → To, resources, ships needed
  → Shows totals

Step 6: Confirmation
  → (Y)es → execute shipments
  → (E)dit → jump back to Step 2 (destination selection)
  → (C)ancel → return to main menu

Step 7: Execution (one-shot)
  → Executes routes with per-route locking
  → Reports success/failure per route
  → Returns to menu when done
```

---

## Implementation Steps

### Step 1: Update module metadata

**File:** `autoIkabot/modules/resourceTransportManager.py`

- Change docstring version from `v1.0` to `v1.1`
- Update `Four shipping modes` in docstring, add mode 4 description
- Change banner string from `RESOURCE TRANSPORT MANAGER v1.0` to `v1.1`

### Step 2: Add mode 4 to the shipping mode menu

**File:** `autoIkabot/modules/resourceTransportManager.py`
**Function:** `resourceTransportManager()` (line ~188)

- Add `print("(4) Auto Send: Request resources and auto-collect from all cities")`
- Change `read(min=1, max=3, ...)` to `read(min=1, max=4, ...)`
- Add `elif shipping_mode == 4:` branch calling `autoSendMode(session, telegram_enabled)`
- Move the existing `else:` (even distribution) to `elif shipping_mode == 3:`

### Step 3: Implement `autoSendMode()` config function

**File:** `autoIkabot/modules/resourceTransportManager.py`
**Insert after:** `evenDistributionMode()` function (after line ~935)

```python
def autoSendMode(session, telegram_enabled):
    """Auto Send: request specific amounts, pull from all cities."""
```

**3a: Ship type selection**
- Same pattern as consolidateMode lines 245-252
- Store `useFreighters` boolean

**3b: Destination city selection (inside a `while True` loop for Edit support)**
- Use `chooseCity(session)` — same as existing internal destination selection
- Fetch full city data: `session.get(CITY_URL + str(city['id']))` → `getCity(html)`
- Fetch island data for the destination (needed for route tuples)

**3c: Fetch all cities and compute totals**
- Call `getIdsOfCities(session)` to get all city IDs
- For each city that is NOT the destination:
  - `session.get(CITY_URL + city_id)` → `getCity(html)`
  - Also fetch island data: `session.get(ISLAND_URL + city['islandId'])` → `getIsland(html)`
  - Accumulate `availableResources[i]` into totals array
  - Store city data + island data in a `suppliers` list
- Display totals table:
  ```
  Total available resources (excluding destination):
    Resource       Available
    ----------   -----------
    Wood           4,447,122
    Wine           9,401,841
    ...
  ```

**3d: Resource request input**
- Print instructions (same style as consolidateMode SEND mode, lines 317-324)
- Loop through `MATERIALS_NAMES`, call `readResourceAmount()` for each
- Support `'` (exit), `=` (restart), blank (skip/zero)
- After input, validate each requested amount <= total available for that resource
  - If any exceeds: print error, ask user to re-enter (restart resource config)
- If all zeros: print message and return to menu
- Store as `requested = [int, int, int, int, int]`

**3e: Allocate resources across suppliers**
- Call `allocate_from_suppliers(requested, suppliers)` (new helper, see Step 4)
- Returns list of route tuples or `None` if allocation fails

**3f: Review screen**
- Call `render_auto_send_review(...)` (new helper, see Step 5)
- Display: destination, ship type, capacity, per-route breakdown, totals
- Prompt: `(Y)es / (E)dit / (C)ancel`
  - `Y` → break out of loop, proceed to return config
  - `E` → `continue` (re-enter while loop from destination selection)
  - `C` → `return None`

**3g: Return config dict**
- Same pattern as evenDistributionMode (line 932-935):
  ```python
  info = f"\nAuto-send resources to {destination_city['name']}\n"
  return {
      "info": info,
      "run_func": lambda: do_it_auto_send(session, routes, useFreighters, telegram_enabled),
  }
  ```

### Step 4: Implement `allocate_from_suppliers()` helper

**File:** `autoIkabot/modules/resourceTransportManager.py`
**Insert after:** `autoSendMode()`

```python
def allocate_from_suppliers(requested, suppliers, destination_city, destination_island):
    """Allocate requested resources across supplier cities.

    Parameters
    ----------
    requested : list[int]
        Five-element list of requested amounts per resource.
    suppliers : list[dict]
        Each dict has keys: 'city' (city data dict), 'island' (island data dict).
    destination_city : dict
        The destination city data.
    destination_island : dict
        The destination island data.

    Returns
    -------
    list[tuple] or None
        List of route tuples for executeRoutes(), or None if allocation impossible.
    """
```

**Algorithm:**
1. Create a `remaining = list(requested)` — what still needs to be fulfilled
2. Create `routes = []`
3. For each supplier in `suppliers`:
   - `to_send = [0, 0, 0, 0, 0]`
   - For each resource index `i`:
     - If `remaining[i] <= 0`: skip
     - `can_give = supplier['city']['availableResources'][i]`
     - `give = min(remaining[i], can_give)`
     - `to_send[i] = give`
     - `remaining[i] -= give`
   - If `sum(to_send) > 0`:
     - Build route tuple: `(supplier['city'], destination_city, destination_island['id'], *to_send)`
     - Append to `routes`
   - If `all(r <= 0 for r in remaining)`: break early — fulfilled
4. If any `remaining[i] > 0`: this shouldn't happen (validated earlier), but return `None`
5. Return `routes`

**Key design notes:**
- Each route is multi-resource (all 5 resource types in one tuple)
- No distance sorting — suppliers are iterated in the order returned by `getIdsOfCities()`
- Route tuple format matches existing code: `(origin_city_dict, dest_city_dict, island_id, wood, wine, marble, crystal, sulfur)`

### Step 5: Implement `render_auto_send_review()` helper

**File:** `autoIkabot/modules/resourceTransportManager.py`
**Insert after:** `allocate_from_suppliers()`

```python
def render_auto_send_review(destination_city, destination_island, routes, useFreighters, capacity):
    """Display the shipment plan for user review.

    Parameters
    ----------
    destination_city : dict
    destination_island : dict
    routes : list[tuple]
    useFreighters : bool
    capacity : int
        Per-ship cargo capacity (from getShipCapacity).
    """
```

**Display format:**
```
+========================================================+
|       RESOURCE TRANSPORT MANAGER v1.1                  |
|--------------------------------------------------------|
|                  Auto Send — Review                    |
+========================================================+

  Destination: CityName [X:Y]
  Ship type:   Merchant ships (capacity: 580)

  Planned Shipments:
  -------------------------------------------------------
  #  From            Wood     Wine   Marble  Crystal  Sulfur  Ships
  -- -----------  -------  -------  -------  -------  ------  -----
  1  3 Slylyos     50,000   10,000        0        0       0    104
  2  8 Essoeos          0        0   20,000        0       0     35
  -------------------------------------------------------
  TOTAL            50,000   10,000   20,000        0       0    139

  Proceed? (Y)es / (E)dit / (C)ancel
```

**Ships calculation per route:**
```python
total_cargo = sum(route[3:8])  # sum of all 5 resources in this route
ships_needed = math.ceil(total_cargo / capacity)
```

**Returns:** User's choice — `"Y"`, `"E"`, or `"C"`

### Step 6: Implement `do_it_auto_send()` execution function

**File:** `autoIkabot/modules/resourceTransportManager.py`
**Insert after:** `render_auto_send_review()`

```python
def do_it_auto_send(session, routes, useFreighters, telegram_enabled):
    """Execute auto-send shipments (one-shot, per-route locking).

    Parameters
    ----------
    session : Session
    routes : list[tuple]
    useFreighters : bool
    telegram_enabled : bool or None
    """
```

**Logic (follows existing `do_it()` pattern, simplified for one-shot):**

1. `ship_type_name = "freighters" if useFreighters else "merchant ships"`
2. `total_routes = len(routes)`
3. `completed = 0`
4. For each `route_index, route` in `enumerate(routes)`:
   a. `origin_city = route[0]`
   b. `destination_city = route[1]`
   c. Print progress: `[route_index+1/total_routes] {origin_city['name']} -> {destination_city['name']}`
   d. **Wait for ships** (same pattern as do_it lines 1131-1150):
      - Loop checking `getAvailableShips`/`getAvailableFreighters`
      - If 0, wait 120s and retry
      - Update `session.setStatus()`
   e. **Acquire lock** (same pattern as do_it lines 1152-1171):
      - `max_retries = 3`, retry with 60s delay
      - `acquire_shipping_lock(session, use_freighters=useFreighters, timeout=300)`
   f. **Execute route** inside `try/finally` (release lock in `finally`):
      - Verify ships still available (`ships_before`)
      - `executeRoutes(session, [route], useFreighters)`
      - Verify ships were consumed (`ships_after`)
      - `completed += 1`
      - Print success + send Telegram notification if enabled
   g. **On failure:**
      - Print error
      - Send Telegram notification if enabled
      - Report what completed vs what failed
      - `break` — stop executing remaining routes (resources may have changed)
5. Print final summary: `Auto-send complete: {completed}/{total_routes} shipments sent`
6. `session.setStatus(f"Auto-send complete: {completed}/{total_routes} to {destination_city['name']}")`

**Error handling on mid-execution failure:**
```
SHIPMENT FAILED
  Route 3/5: 8 Essoeos -> 1 Slylyos
  Error: [error message]
  Completed: 2/5 shipments
  Remaining: 3 shipments NOT sent
  Suggestion: Run Auto Send again to retry remaining resources
```

---

## Files Modified

| File | Changes |
|------|---------|
| `autoIkabot/modules/resourceTransportManager.py` | Version bump to 1.1, add mode 4 menu entry, add 4 new functions |

**No new files created.** Everything goes into the existing module.

## New Functions Added (4 total)

| Function | Purpose | Approx Lines |
|----------|---------|--------------|
| `autoSendMode()` | Interactive config: ship type, destination, totals, request input, review loop | ~150 |
| `allocate_from_suppliers()` | Algorithm: distribute requested amounts across supplier cities | ~40 |
| `render_auto_send_review()` | Display shipment plan table, return Y/E/C choice | ~60 |
| `do_it_auto_send()` | One-shot execution with per-route locking | ~100 |

**Total estimated addition:** ~350 lines

## Existing Functions/Helpers Reused

| Function | From | Used For |
|----------|------|----------|
| `getShipCapacity(session)` | `helpers/naval.py` | Dynamic ship capacity |
| `getAvailableShips(session)` | `helpers/naval.py` | Check ship availability before dispatch |
| `getAvailableFreighters(session)` | `helpers/naval.py` | Check freighter availability before dispatch |
| `executeRoutes(session, routes, useFreighters)` | `helpers/routing.py` | Execute the actual shipment POST |
| `getCity(html)` | `helpers/game_parser.py` | Parse city data |
| `getIsland(html)` | `helpers/game_parser.py` | Parse island data |
| `getIdsOfCities(session)` | `helpers/game_parser.py` | Get all player city IDs |
| `chooseCity(session)` | `ui/prompts.py` | Destination city picker |
| `readResourceAmount(resource_name)` | (same file) | Validated resource input with restart/exit |
| `print_module_banner()` | (same file) | Consistent module header |
| `acquire_shipping_lock()` | (same file) | File-based ship lock |
| `release_shipping_lock()` | (same file) | Release ship lock |
| `addThousandSeparator()` | `helpers/formatting.py` | Number formatting |
| `checkTelegramData()` / `sendToBot()` | `notifications/notify.py` | Telegram notifications |

## Route Tuple Format (reference)

```python
route = (
    origin_city_dict,       # dict from getCity()
    destination_city_dict,   # dict from getCity()
    island_id,               # str — destination island ID
    wood_amount,             # int
    wine_amount,             # int
    marble_amount,           # int
    crystal_amount,          # int
    sulfur_amount,           # int
)
```

## Edge Cases to Handle

| Case | Handling |
|------|----------|
| Destination is only city | Error: "No supplier cities available" → return to menu |
| All requested amounts are zero | Print message → return to menu |
| Requested exceeds available for a resource | Reject with clear message, re-prompt resource input |
| Ships unavailable during execution | Wait loop (120s intervals) matching existing pattern |
| Lock contention during execution | 3 retries with 60s delays matching existing pattern |
| Shipment fails mid-execution | Stop, report completed vs remaining, suggest re-run |
| Supplier city has zero of a requested resource | Skip that resource for that city (allocator handles naturally) |
