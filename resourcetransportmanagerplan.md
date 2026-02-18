# plan.md — Feature 4: Auto Send/Request Resources (Resource Transport Manager)
## 1) Goal
Add a **4th feature** to the Resource Transport Manager: **Auto Send/Request Resources**.
The feature should:
- Ask whether to use **Merchant ships** or **Freighter**.
- Ask for a **destination city**.
- Display **total available resources across all cities** (Wood/Wine/Stone/Crystal/Sulphur).
- Ask the user **how much of each resource** they want transported.
- Build a **shipping schedule** that pulls from any city that can supply resources to fulfill the request.
- Prefer **closest cities first**, with **furthest** used last.
- Ensure ships are **efficiently filled**:
  - Merchant ship capacity: **500**
  - Freighter capacity: **50,000**
  - Only allow smaller loads if:
    - the load is **within 25% of max capacity** (≥ 75% full), **and/or**
    - it's the **end of the order** and the remaining amount is **less than a ship's capacity**.
- Present a **review screen** showing planned shipments: from where, what quantities, number of ships per shipment, and totals.
- Ask for confirmation:
  - **Yes** → execute shipping using the **existing shipping method** with **lock mechanism**
  - **Edit** → go back to destination selection
  - **Cancel** → return to main menu
---
## 2) Assumptions & Existing Components
This plan assumes the module already has:
- A concept of **cities** with:
  - city identifier / name
  - island id / coordinates / travel time info (or a way to compute distance to destination)
  - resource stock levels for: `wood, wine, marble(stone), crystal, sulphur`
- A **shipping/transport** function that can dispatch a shipment:
  - Example shape: `ship_resource(src_city, dst_city, resource_type, amount, ship_type, lock=...)`
- A **lock mechanism** used elsewhere to prevent concurrent use of ships / transport UI / account actions.
If distance/travel time computation is not currently available, add a small adapter that reuses any "route time" / "sailing duration" already used in the transport manager.
---
## 3) User Flow (CLI / Menu)
### 3.1 Menu entry
`[4] Auto send/request resources`
### 3.2 Step-by-step prompts
1) **Ship type**
- Prompt: `Use (1) Merchant ships (500) or (2) Freighter (50000)?`
- Store: `ship_type`, `capacity`
2) **Destination selection**
- Prompt: `Select destination city:`
- Accept:
  - city index from list
  - city name match (case-insensitive)
  - city id (optional)
3) **Totals screen**
- Compute totals across all *non-destination* cities (or include destination for display but exclude from supply).
- Display:
  - total per resource type
  - optionally: total "shippable" (sum of all except destination)
4) **User request input**
- Prompt for requested amounts per resource.
- Accept either:
  - one line like `wood=20000 wine=0 stone=5000 crystal=0 sulphur=2000`, or
  - sequential prompts per resource.
- Validate:
  - non-negative integers
  - total requested not exceeding total available (warn + allow partial or force correction; recommended: force correction with clear message).
5) **Build plan**
- Compute distance from each supplier city to destination.
- Allocate requested resources from cities in **ascending distance** order.
6) **Review screen**
- Show:
  - destination
  - ship type & capacity
  - per shipment line:
    - `From City → To Destination | Resource | Amount | Ships needed | Distance/ETA`
  - totals per resource and total ships used
- Prompt: `(Y)es / (E)dit / (C)ancel`
7) Execution
- Y: acquire lock and run shipments using existing shipping method
- E: jump back to destination selection (per requirement)
- C: return to main menu
---
## 4) Data Model / Structures
### 4.1 Canonical resource keys
Use normalized keys internally:
- `wood`
- `wine`
- `stone` (map to marble if the game uses marble)
- `crystal`
- `sulphur`
### 4.2 Request structure
```python
requested = {
  "wood": int,
  "wine": int,
  "stone": int,
  "crystal": int,
  "sulphur": int,
}
```
### 4.3 Supply snapshot
```python
city_supply = {
  city_id: {
    "name": str,
    "distance": float|int,   # travel time or computed distance metric
    "available": {"wood": int, "wine": int, ...}
  },
  ...
}
```
### 4.4 Shipping schedule output
Two useful layers:
1) **Allocations** (resource pull plan)
```python
allocations = [
  {"src": city_id, "dst": dest_city_id, "resource": "wood", "amount": 50000},
  ...
]
```
2) **Shipments** (split into ship-capacity sized dispatches)
```python
shipments = [
  {"src": city_id, "dst": dest_city_id, "resource": "wood", "amount": 500, "ships": 1},
  ...
]
```
---
## 5) Scheduling & Allocation Algorithm
### 5.1 Compute distance ordering
- For each non-destination city:
  - compute `distance_to_dest(city, destination)`
- Sort cities by distance ascending:
  - `suppliers = sorted(cities, key=distance)`
**Distance metric options (in order of preference):**
1) actual travel time already computed by current transport code
2) map grid distance (if coordinates exist)
3) same-island first (distance=0), otherwise fallback to island-id difference or known route lengths
### 5.2 Allocate resources from closest suppliers first
For each resource `r`:
- `remaining = requested[r]`
- Iterate suppliers in ascending distance:
  - take `take_amt = min(remaining, supplier.available[r])`
  - if `take_amt > 0`: append allocation and decrement remaining
  - stop if remaining == 0
- If remaining > 0 after all suppliers:
  - fail with error: "Insufficient {r}. Missing X."
### 5.3 Split allocations into ship-sized shipments
Given `capacity`:
- For each allocation:
  - split into chunks:
    - full chunks: `capacity` each
    - remainder: `rem = amount % capacity`
**Remainder rule (efficiency constraint):**
- Allow a remainder shipment only if:
  - `rem == 0`, OR
  - `rem >= 0.75 * capacity` (within 25% of max), OR
  - it is the **final remaining amount for that resource overall** and `rem < capacity`
Interpretation of the requirement:
- Prefer full ships always.
- Allow underfilled ships only when:
  - almost full (≥ 75%), or
  - unavoidable end-of-order remainder.
**Important edge-case:**
If you allocate across multiple cities, the "end of the order" remainder should be evaluated at the *resource-level*, not per city. So:
- Build a resource-level split plan, but keep source assignment intact.
- Practically:
  - split each allocation into full ships first
  - collect remainders across cities for that resource
  - if remainders exist:
    - if multiple remainder pieces exist, consolidate by rebalancing allocations (advanced) or dispatch remainder pieces only when they satisfy the ≥75% rule except the very last remainder.
  - **MVP approach** (recommended for first version):
    - Keep per-city remainder shipments.
    - Enforce ≥75% rule for all remainder shipments **except** the final remainder shipment for that resource, which can be smaller.
### 5.4 "Best way" heuristic
Within the constraints given, "best way" = minimize travel time by:
- pulling from closest cities first
- only reaching further cities when closer stock is insufficient
This is greedy and should be sufficient.
---
## 6) Review Screen Requirements
Display a clear plan before executing:
**Header**
- Destination: `CityName (Island/Coords if known)`
- Ship type: `Merchant` or `Freighter`
- Capacity: `500` or `50,000`
**Summary (requested vs planned)**
- Requested totals per resource
- Planned totals per resource (should match unless user requested partial)
**Shipment table**
For each shipment:
- `#`
- `From`
- `To`
- `Resource`
- `Amount`
- `Ships` (normally 1 per line if each line is a single ship; otherwise compute)
- `Distance/ETA` (if available)
**Totals**
- Total ships used
- Optional: estimated completion time if shipments are sequential (depends on lock/ship availability model)
Prompt:
- `Proceed? [Y]es / [E]dit / [C]ancel`
Behavior:
- **Edit** returns to destination selection (as specified), then re-run totals + request + plan.
---
## 7) Execution Using Existing Shipping + Lock
### 7.1 Locking
Before any dispatches:
- acquire the transport lock (same mechanism as current shipping feature)
- hold lock during all dispatch operations
- release lock even on error (use `try/finally`)
### 7.2 Dispatch order
Dispatch in the order of the shipment list:
- because we already sorted suppliers closest-first
- and split into ships
Potential improvement later:
- batch same-source shipments for fewer UI transitions.
### 7.3 Error handling during execution
If a dispatch fails (e.g., ships unavailable, transient UI error, resource changed since planning):
- stop execution
- release lock
- show:
  - which shipment failed
  - what completed successfully
  - suggest rerunning the feature to regenerate plan
---
## 8) Validation & Edge Cases
- Destination city cannot be a supplier.
- Request cannot be all zeros (if so, return to main menu).
- If totals available < requested for any resource: reject and ask user to adjust.
- If a supplier's resources change between planning and dispatch:
  - handle failure gracefully and report.
- If the user selects Freighter but no freighter is available:
  - fail early with a clear message (if availability can be detected).
- If 25% rule blocks a plan:
  - the MVP approach should still succeed because the "final remainder" exception always allows completion.
---
## 9) Implementation Steps Checklist
1) Add menu option `[4] Auto send/request resources`.
2) Implement `select_ship_type()` → returns (`ship_type`, `capacity`).
3) Implement `select_destination_city()` → returns destination city object/id.
4) Implement `compute_total_resources(cities)` → totals dict for display.
5) Implement `parse_requested_amounts()` → requested dict.
6) Implement `compute_distances_to_destination()` and sort suppliers.
7) Implement `allocate_resources(requested, suppliers)` → allocations list.
8) Implement `split_allocations_into_shipments(allocations, capacity)` → shipments list with 25% rule handling.
9) Implement `render_review_screen(shipments, summary)` → Y/E/C.
10) Implement `execute_shipments_with_lock(shipments)` using existing shipping function + lock.
---
## 10) Pseudocode (High-Level)
```python
def feature_auto_send_request():
    ship_type, capacity = select_ship_type()
    while True:
        dest = select_destination_city()
        totals = compute_total_resources(all_cities, exclude_city=dest)
        print_totals(totals)
        requested = get_requested_amounts(totals)
        if all(v == 0 for v in requested.values()):
            return  # back to main menu
        suppliers = build_suppliers(all_cities, dest)  # includes distance + availability
        allocations = allocate_resources(requested, suppliers)
        shipments = split_allocations(allocations, capacity, requested)
        choice = review_and_confirm(dest, ship_type, capacity, requested, shipments)
        if choice == "Y":
            execute_with_lock(shipments, ship_type)
            return
        elif choice == "E":
            continue  # back to destination selection
        else:  # "C"
            return
```
---
## 11) Testing Plan
### Unit tests (where feasible)
- Distance sorting (mock distances).
- Allocation correctness:
  - exact fulfillment
  - insufficient resources error
- Splitting logic:
  - exact multiples
  - remainder ≥75% allowed
  - small final remainder allowed
  - multiple remainders: only final remainder can be small (MVP rule)
### Integration tests (in a safe/test account)
- Merchant ship flow with mixed suppliers.
- Freighter flow.
- Confirm/edit/cancel navigation.
- Lock enforcement (attempt concurrent transport feature calls).
---
## 12) Future Improvements (Optional)
- Smarter remainder consolidation: merge remainders across nearby cities where possible while respecting per-city stock.
- Parallel dispatch when ships can be launched simultaneously (if module supports it).
- Persist plan preview to a log file for audit/troubleshooting.
- "Request" mode (if you intend to support inbound requests from other accounts or alliances later); for now this plan implements "send".
