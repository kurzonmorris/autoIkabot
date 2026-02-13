# Ikariam Website Attributes Reference

This file documents every important HTML element, ID, class, attribute, form field, API endpoint, and JavaScript variable found on the Ikariam website. It serves as a shared reference between the developer and the AI assistant.

**How to use this file:**
- Each entry has a **Reference Code** (the actual selector/attribute/endpoint), a **Location** (where it appears), a **Purpose** (what it does), and an optional **User Notes** field.
- The User Notes field is for you (the developer) to add your own explanations, corrections, or context.
- Entries are grouped by functional area (Login, Navigation, City View, etc.).

---

## Table of Contents

1. [Login & Authentication](#1-login--authentication)
2. [Blackbox / Anti-Bot Token System](#2-blackbox--anti-bot-token-system)
3. [Lobby / Server Selection](#3-lobby--server-selection)
4. [Navigation & Menus](#4-navigation--menus)
5. [City View](#5-city-view)
6. [Buildings](#6-buildings)
7. [Resources & Production](#7-resources--production)
8. [Military / Troops](#8-military--troops)
9. [Transport / Trade](#9-transport--trade)
10. [Research](#10-research)
11. [Island View](#11-island-view)
12. [World Map](#12-world-map)
13. [Diplomacy & Alliances](#13-diplomacy--alliances)
14. [Temples & Deities](#14-temples--deities)
15. [Tavern & Museum (Happiness)](#15-tavern--museum-happiness)
16. [Spies & Espionage](#16-spies--espionage)
17. [Cookies & Session Data](#17-cookies--session-data)
18. [API Endpoints & AJAX Calls](#18-api-endpoints--ajax-calls)
19. [JavaScript Variables & Functions](#19-javascript-variables--functions)
20. [Miscellaneous](#20-miscellaneous)

---

## 1. Login & Authentication

### 1.1 Login Flow Overview (10 Phases)

The login is a multi-stage process. Credentials go to Gameforge's auth service first, then through the lobby, then into the game server.

### 1.2 Phase 1 — Get Environment IDs

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `GET https://lobby.ikariam.gameforge.com/config/configuration.js` | Lobby config endpoint | Returns JavaScript containing `gameEnvironmentId` and `platformGameId`. These are needed for the auth POST. | Extracted via regex from response body |

### 1.3 Phase 2 — Cloudflare Handshake

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `GET https://gameforge.com/js/connect.js` | Gameforge main site | Obtains initial Cloudflare `__cfduid` cookie. Also checks if a Cloudflare CAPTCHA challenge is present. | If captcha detected here, login fails with "Captcha error!" |
| `GET https://gameforge.com/config` | Gameforge main site | Updates Cloudflare tracking cookie | |

### 1.4 Phase 3 — Device Fingerprinting (Pixel Zirkus)

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `POST https://pixelzirkus.gameforge.com/do/simple` (location=VISIT) | Fingerprint service | Sends initial device fingerprint data with random `fp_eval_id` | Errors silently ignored |
| `POST https://pixelzirkus.gameforge.com/do/simple` (location=fp_eval) | Fingerprint service | Sends updated fingerprint evaluation with different `fp_eval_id` | Errors silently ignored |

### 1.5 Phase 4 — Authentication Request

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `OPTIONS https://gameforge.com/api/v1/auth/thin/sessions` | Gameforge auth | CORS preflight validation | |
| `POST https://spark-web.gameforge.com/api/v2/authProviders/mauth/sessions` | Spark auth service | **Main credential submission**. Sends email, password, locale, gameId, gameEnvironmentId, and blackbox token. Returns `token` (gf-token-production) on success. | Response code 409 with `OTP_REQUIRED` means 2FA needed |

**Auth POST payload:**
```json
{
  "identity": "user@email.com",
  "password": "password",
  "locale": "en-GB",
  "gfLang": "en",
  "gameId": "ikariam",
  "gameEnvironmentId": "<from configuration.js>",
  "blackbox": "tra:JVqc1fosb5TG..."
}
```

### 1.6 Phase 5 — 2FA / MFA Handling

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| Response code `409` + `OTP_REQUIRED` in body | Auth response | Indicates two-factor auth is required | Re-send auth POST with `otpCode` field added |

### 1.7 Phase 6 — Interactive Captcha Handling

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| Response header `gf-challenge-id` + no `token` in body | Auth response | Indicates captcha challenge required | |
| `GET https://challenge.gameforge.com/challenge/{challenge_id}` | Challenge service | Challenge landing page | |
| `GET https://image-drop-challenge.gameforge.com/challenge/{id}/en-GB` | Challenge metadata | Returns challenge metadata | |
| `GET https://image-drop-challenge.gameforge.com/challenge/{id}/en-GB/text?{timestamp}` | Challenge image | The text/instruction image for the captcha | |
| `GET https://image-drop-challenge.gameforge.com/challenge/{id}/en-GB/drag-icons?{timestamp}` | Challenge image | The draggable icon options (4 icons) | |
| `GET https://image-drop-challenge.gameforge.com/challenge/{id}/en-GB/drop-target?{timestamp}` | Challenge image | The drop target area | |
| `POST https://image-drop-challenge.gameforge.com/challenge/{id}/en-GB` with `{"answer": 0-3}` | Challenge submit | Submits captcha answer (0-indexed icon number). Response contains `status: "solved"` on success. | |

### 1.8 Phase 7 — Token Extraction

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `token` field in auth response JSON | Auth response body | The `gf-token-production` value. This is a UUID like `6bc8f992-9955-4c70-ae26-10f5c0221502`. | Cached for reuse across accounts sharing the same email |
| Manual fallback: `document.cookie.split(';').forEach(...)` | Browser console | If token extraction from response fails, user can paste it from browser dev tools | |

### 1.9 Phase 8 — Account & Server Selection

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `GET https://lobby.ikariam.gameforge.com/api/users/me/accounts` | Lobby API | Returns list of game accounts for this login. Includes account IDs, servers, last login times. | Requires `Authorization: Bearer {gf-token-production}` header |
| `GET https://lobby.ikariam.gameforge.com/api/servers` | Lobby API | Returns full list of game servers with names, languages, numbers | |

### 1.10 Phase 9 — Game Server Cookie

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `POST https://lobby.ikariam.gameforge.com/api/users/me/loginLink` | Lobby API | Returns a one-time login URL for the selected game server | See payload below |
| Login link redirect | Game server URL | Following the loginLink URL sets the game server cookies (`ikariam`, `PHPSESSID`) | |

**loginLink POST payload:**
```json
{
  "server": {"language": "en", "number": "59"},
  "clickedButton": "account_list",
  "id": "<account_id>",
  "blackbox": "tra:JVqc1fosb5TG..."
}
```

### 1.11 Phase 10 — Session Validation

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `"nologin_umod"` in response HTML | Game server response | Account is in vacation mode | |
| `"index.php?logout"` or `'<a class="logout"'` in response HTML | Game server response | Session expired, need to re-login | Up to 3 retry attempts |

### 1.12 HTTP Headers for Authentication

**Lobby / Auth headers:**
```
Accept: */*
Accept-Language: en-US,en;q=0.5
Accept-Encoding: gzip, deflate, br
Origin: https://lobby.ikariam.gameforge.com
Referer: https://lobby.ikariam.gameforge.com/
User-Agent: Mozilla/5.0...
TNT-Installation-Id: (empty string)
Content-Type: application/json
```

---

## 2. Blackbox / Anti-Bot Token System

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| Blackbox token format: `tra:JVqc1fosb5TG-E2h5Ak7bZL...` | Auth POST payload | Device fingerprinting token. Prefixed with `tra:`. Required for the auth POST and the loginLink POST. | Generated via external API |
| `getNewBlackBoxToken()` | `ikabot/helpers/apiComm.py` | Fetches a fresh blackbox token from an external API server. URL resolved via DNS TXT records. | `GET /v1/token?user_agent={user_agent}` |
| DNS TXT lookup for API server | `ikabot/helpers/apiComm.py` | The API server domain (`ikagod.twilightparadox.com`) is resolved via DNS TXT records to get the current IP/hostname | |
| Token is submitted at two points | Auth POST + loginLink POST | First during credential authentication, second when requesting the game server login link | |
| `pc_idt` cookie | Gameforge cookies | Base64-encoded device identifier used for account/device linking | Set during Pixel Zirkus fingerprinting |

---

## 3. Lobby / Server Selection

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `https://lobby.ikariam.gameforge.com/en_US/hub` | Browser URL bar | The main lobby URL. One account can play on multiple servers simultaneously. The lobby lists all available servers and any "graveyard" servers (where inactive accounts are moved). | |
| `s59` (in URL `s59-en.ikariam.gameforge.com`) | Server URL subdomain | The server number. Each game server has a unique number. Format: `s{number}` | |
| `en` (in URL `s59-en.ikariam.gameforge.com`) | Server URL subdomain | The region/language code. Determines which regional cluster the server belongs to (e.g. `en` = English). | |
| Server URL pattern: `https://s{NUM}-{REGION}.ikariam.gameforge.com/` | Browser URL | The full base URL pattern for any game server. Combine server number + region to form the base URL. | |
| Graveyard server | Lobby server list | A special server where accounts are moved after prolonged inactivity. Not a regular playable server. | |
| `data-default-product="ikariam"` | Lobby HTML `<script>` tag | Product identifier for Gameforge | |
| `data-default-language="en"` | Lobby HTML `<script>` tag | Language setting | |
| `data-default-server-id="59"` | Lobby HTML `<script>` tag | Default server number (59 = Perseus) | |
| `data-env="live"` | Lobby HTML `<script>` tag | Environment identifier (live vs test) | |
| `data-project-id="a62fcc7f-9eea-4dc3-9a2c-526e948db9e3"` | Lobby HTML `<script>` tag | Gameforge project UUID for Ikariam | |
| `data-locale="en-GB"` | Lobby HTML `<script>` tag | Locale setting | |
| Server name: `Perseus` | Server info | Human-readable name for s59-en | |
| Game version: `6.5.1-r1` (server) / `v16.0.1` (client) | Page footer / metadata | Current game and client versions | |

---

## 4. Navigation & Menus

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `id="GF_toolbar"` | Page header | Gameforge toolbar container (top bar) | |
| `id="subTitle"` | Page header | Page/view title header | |
| `id="siteNavigation"` | Page layout | Main navigation menu | |
| `id="pageContent"` | Page layout | Main content area | |
| `id="header"` | Page layout | Game header section | |
| `id="topnavi"` | Page header | Top navigation bar | |
| `id="topnaviResources"` | Page header | Resource display area in top nav | |
| `id="mmoNewsticker"` | Page header | News ticker / announcements | |
| `id="container"` | Page layout | Main game container | |
| `onclick="ajaxHandlerCall(this.href);return false;"` | All navigation links | Standard AJAX navigation pattern — all in-game links use this instead of full page loads | |
| `href="javascript:ikariam.show('avatarNotes')"` | Notes link | Direct JavaScript call pattern for some UI elements | |
| `class="noViewParameters"` | Navigation links | Links that use `ajaxHandlerCall()` without view parameters | |

### Advisor Menu IDs

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `id="js_GlobalMenu_cities"` | Top nav | Trade advisor link | |
| `id="js_GlobalMenu_citiesPremium"` | Top nav | Premium trade advisor link | |
| `id="js_GlobalMenu_military"` | Top nav | Military advisor link | |
| `id="js_GlobalMenu_militaryPremium"` | Top nav | Premium military advisor link | |
| `id="js_GlobalMenu_research"` | Top nav | Research advisor link | |
| `id="js_GlobalMenu_researchPremium"` | Top nav | Premium research advisor link | |
| `id="js_GlobalMenu_diplomacy"` | Top nav | Diplomacy advisor link | |
| `id="js_GlobalMenu_diplomacyPremium"` | Top nav | Premium diplomacy advisor link | |

### Advisor View URLs

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `?view=tradeAdvisor` | URL parameter | Trade advisor page | |
| `?view=militaryAdvisor` | URL parameter | Military advisor page | |
| `?view=researchAdvisor` | URL parameter | Research advisor page | |
| `?view=highscore` | URL parameter | Highscore rankings | |
| `?view=avatarProfile&activeTab=tab_avatarProfile` | URL parameter | Player profile page | |
| `?view=optionsAccount` | URL parameter | Account options/settings | |
| `?view=finances` | URL parameter | Financial overview | |
| `?view=inventory` | URL parameter | Player inventory | |
| `?view=merchantNavy` | URL parameter | Merchant ships management | |
| `?view=premium&linkType=1` | URL parameter | Premium shop / Ambrosia | |

---

## 5. City View

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `?view=city&cityId={ID}` | URL query string | Navigates to a specific city. Each city has a unique numeric ID. Example: `?view=city&cityId=1295` loads the city with ID 1295. | |
| `cityId` | URL parameter / API parameter | The unique numeric identifier for a city. Used throughout the game in URLs and API calls to reference a specific city. First city example: `1295`. | |
| `view=city` | URL parameter | Tells the game to render the city view for the given `cityId`. | |
| `id="city"` | Body element | Body ID when viewing a city | |
| `class="flexible"` | Body element | Flexible layout class on body | |
| `class="direction_ltr"` | Body element | Left-to-right text direction class | |
| `id="avatarPictureSmall"` | City header | Small avatar picture in header | |
| `class="avatarName"` | City header | Player name display | |
| `id="servertime"` | Page footer | Server time display (format: `DD.MM.YYYY HH:MM:SS CET`) | |
| `class="version"` | Page footer | Client version display (e.g. `v16.0.1`) | |

### Building Position Links (City View)

**IMPORTANT:** Building positions (1-24) vary from city to city and player to player. The **only fixed position is Town Hall at position 0**. Players can rearrange buildings within their city. The building at any given position must be determined at runtime by reading the page.

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `id="js_CityPosition{N}Link"` | City view | Link to building at position N (0-24). N=0 is always Town Hall. All other positions are player-configured. | |
| `id="js_CityPosition{N}Img"` | City view | Building image container at position N | |
| `id="js_CityPosition{N}CountdownText"` | City view | Upgrade countdown timer at position N (visible during construction) | |
| `id="js_CityPosition{N}SpeedupButton"` | City view | Ambrosia speed-up button at position N (title="Shorten building time") | |
| `data-id="{N}"` | Building element | Data attribute containing the position number | |
| `id="setCityBuildingsDraggable"` | City view | Toggle to enable building rearrangement mode | |
| `id="toggleBuildingInfos"` | City view | Toggle to show/hide building name labels | |

---

## 6. Buildings

**IMPORTANT:** There are **33 buildable building types** plus **3 special non-buildable constructs**. Building positions (1-24) vary per city and per player — only Town Hall (position 0) is fixed. The building at any position must be read from the page at runtime.

### All 33 Buildable Building Types

| ID | Building Name | View Param | `buildingDetail` ID | Category | User Notes |
|----|---------------|-----------|---------------------|----------|------------|
| 0 | Town Hall | `townHall` | `buildingId=0` | Core | Always position 0. Increases max citizens per level. |
| 3 | Trading Port | `port` | `buildingId=3` | Trade | Can have multiple in a city. Required for sea trade. |
| 4 | Academy | `academy` | `buildingId=4` | Research | Trains scientists, enables research. |
| 5 | Shipyard | `shipyard` | `buildingId=5` | Military | Builds warships. |
| 6 | Barracks | `barracks` | `buildingId=6` | Military | Trains land military units. |
| 7 | Warehouse | `warehouse` | `buildingId=7` | Storage | Can have multiple in a city. Protects resources from pillaging. |
| 8 | Town Wall | `wall` | `buildingId=8` | Defense | Provides defensive bonus in combat. |
| 9 | Tavern | `tavern` | `buildingId=9` | Happiness | Serves wine to increase citizen satisfaction. |
| 10 | Museum | `museum` | `buildingId=10` | Happiness | Displays cultural goods for satisfaction bonus. |
| 11 | Palace | `palace` | `buildingId=11` | Core | Only in capital city. Enables founding new colonies. |
| 12 | Embassy | `embassy` | `buildingId=12` | Diplomacy | Required to create/join alliances. |
| 13 | Trading Post | `branchOffice` | `buildingId=13` | Trade | Stores luxury goods for trade on island. |
| 15 | Workshop | `workshop` | `buildingId=15` | Military | Builds siege weapons / war machines. |
| 16 | Hideout | `safehouse` | `buildingId=16` | Espionage | Trains and houses spies. |
| 17 | Governor's Residence | `palaceColony` | `buildingId=17` | Core | In colony cities (non-capital). Reduces corruption. |
| 18 | Forester's House | `forester` | `buildingId=18` | Reduction | Reduces wood costs for buildings. |
| 19 | Stonemason | `stonemason` | `buildingId=19` | Reduction | Reduces marble costs for buildings. |
| 20 | Glassblower | `glassblowing` | `buildingId=20` | Reduction | Reduces crystal glass costs for buildings. |
| 21 | Winery | `winegrower` | `buildingId=21` | Reduction | Reduces wine costs for buildings. |
| 22 | Alchemist's Tower | `alchemist` | `buildingId=22` | Reduction | Reduces sulfur costs for buildings. |
| 23 | Carpenter's Workshop | `carpentering` | `buildingId=23` | Reduction | Reduces wood costs for units. |
| 24 | Architect's Office | `architect` | `buildingId=24` | Reduction | Reduces building upgrade time. |
| 25 | Optician | `optician` | `buildingId=25` | Reduction | Reduces crystal glass costs for units. |
| 26 | Wine Press | `vineyard` | `buildingId=26` | Reduction | Reduces wine costs for units. |
| 27 | Firework Test Area | `fireworker` | `buildingId=27` | Reduction | Reduces sulfur costs for units. |
| 28 | Temple | `temple` | `buildingId=28` | Religion | Converts citizens to priests. Enables deity miracles. |
| 29 | Dump (Depot) | `dump` | `buildingId=29` | Storage | Extra storage that can be pillaged. |
| 30 | Pirate Fortress | `pirateFortress` | `buildingId=30` | Piracy | Enables piracy missions, crew management. |
| 31 | Black Market | `blackMarket` | `buildingId=31` | Piracy | Sells pirate loot for resources/gold. |
| 32 | Sea Chart Archive | `marineChartArchive` | `buildingId=32` | Piracy | Extends piracy mission range. |
| 33 | Dockyard | `dockyard` | `buildingId=33` | Military | Related to naval fleet management. |
| 34 | Gods' Shrine | `shrineOfOlympus` | `buildingId=34` | Religion | Shrine for deity worship. |
| 35 | Chronos' Forge | `chronosForge` | `buildingId=35` | Special | Time-manipulation building. Not on the wiki. |

### 3 Special Non-Buildable Constructs

These exist in some cities but cannot be built or upgraded by the player:

| Name | Purpose | User Notes |
|------|---------|------------|
| Cinetheater | Developer advertising mechanism. Shows video ads for production bonuses. | Bonus tracked via `adVideoBonus` IDs |
| Ambrosia Fountain | Premium feature. Only appears in the capital city (same city as the Palace). | Status: `fountain_active_full` |
| Helios Tower | Provides 10% production bonus. Cannot be built — activated via Ambrosia/events. | Bonus tracked via `heliosTowerBonus` IDs |

### Building URL Patterns

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `?view={viewParam}&cityId={ID}&position={POS}` | URL query string | Opens a specific building. Example: `?view=tavern&cityId=1295&position=15` | |
| `?view=buildingDetail&buildingId={ID}&helpId=1` | URL query string | Opens the in-game help/detail page for a building type | |
| `dialog=buildingConstructionList` | URL parameter | Opens the construction queue dialog for a building | |

### Building Upgrade Mechanics

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `underConstruction` field (value `-1` = none) | JS data | Tracks whether a building is currently being upgraded | |
| `endUpgradeTime` / `startUpgradeTime` | JS data | Unix timestamps for upgrade start and completion | |
| Upgrade costs scale exponentially | Game mechanic | Each level costs significantly more resources and time than the previous | |
| Premium accounts can queue multiple builds | Game mechanic | Non-premium = 1 at a time, premium = multiple queued | |

---

## 7. Resources & Production

### Resource Types Overview

There are **5 gatherable resources**, **1 currency**, **1 premium currency**, and **population**:

| Resource | Internal Name | Deposit Name | Luxury Good? | User Notes |
|----------|--------------|-------------|-------------|------------|
| Building Material (Wood) | `resource` / `wood` | Forest | No (base resource) | Produced on every island |
| Wine | `1` / `wine` | Vines | Yes | Island-specific luxury |
| Marble | `2` / `marble` | Quarry | Yes | Island-specific luxury |
| Crystal Glass | `3` / `crystal` / `glass` | Crystal Mine | Yes | Island-specific luxury |
| Sulfur | `4` / `sulfur` | Sulphur Pit | Yes | Island-specific luxury |
| Gold | `gold` | — | No (currency) | Earned from taxes, trade |
| Ambrosia | `ambrosia` | — | No (premium currency) | Purchased with real money |

### Global Resource IDs (Top Navigation Bar)

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `id="js_GlobalMenu_gold"` | Top nav | Current gold amount | |
| `id="js_GlobalMenu_gold_Total"` | Top nav tooltip | Total gold display | |
| `id="js_GlobalMenu_gold_tooltip"` | Top nav tooltip | Gold tooltip container | |
| `id="js_GlobalMenu_gold_Calculation"` | Top nav tooltip | Total gold income/expense calculation | |
| `id="js_GlobalMenu_income"` | Top nav tooltip | Hourly gold income | |
| `id="js_GlobalMenu_upkeep"` | Top nav tooltip | Hourly gold upkeep (building costs) | |
| `id="js_GlobalMenu_ambrosia"` / `id="headlineAmbrosia"` | Top nav | Ambrosia (premium currency) amount | |
| `id="js_GlobalMenu_wood"` | Top nav | Building material (wood) amount | |
| `id="js_GlobalMenu_wood_Total"` | Top nav tooltip | Total wood display | |
| `id="js_GlobalMenu_wood_tooltip"` | Top nav tooltip | Wood tooltip container | |
| `id="js_GlobalMenu_wine"` | Top nav | Wine resource amount | |
| `id="js_GlobalMenu_wine_Total"` | Top nav tooltip | Total wine display | |
| `id="js_GlobalMenu_marble"` | Top nav | Marble resource amount | |
| `id="js_GlobalMenu_crystal"` | Top nav | Crystal glass resource amount | |
| `id="js_GlobalMenu_sulfur"` | Top nav | Sulfur resource amount | |
| `id="js_GlobalMenu_max_wood"` | Top nav tooltip | Wood storage capacity | |
| `id="js_GlobalMenu_max_wine"` | Top nav tooltip | Wine storage capacity | |
| `id="js_GlobalMenu_max_marble"` | Top nav tooltip | Marble storage capacity | |
| `id="js_GlobalMenu_max_crystal"` | Top nav tooltip | Crystal glass storage capacity | |
| `id="js_GlobalMenu_max_sulfur"` | Top nav tooltip | Sulfur storage capacity | |
| `id="js_GlobalMenu_citizens"` | Top nav | Current citizen count | |
| `id="js_GlobalMenu_population"` | Top nav | Max population | |
| `id="js_GlobalMenu_maxActionPoints"` | Top nav | Available action points | |
| `id="js_GlobalMenu_WineConsumption"` | Top nav tooltip | Wine consumption per hour | |
| `id="js_GlobalMenu_resourceProduction"` | Top nav tooltip | Wood production rate per hour | |

### Production Bonus IDs

The pattern `js_GlobalMenu_production_{resource}_{bonusType}_value` applies to all resource types (wood, wine, marble, crystal, sulfur):

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `id="js_GlobalMenu_production_{res}_premiumBonus_value"` | Resource tooltip | Premium account bonus percentage (20%) | Applies to all 5 resources |
| `id="js_GlobalMenu_production_{res}_adVideoBonus_value"` | Resource tooltip | Cinetheater (ad video) bonus percentage | |
| `id="js_GlobalMenu_production_{res}_heliosTowerBonus_value"` | Resource tooltip | Helios Tower bonus percentage (10%) | |
| `id="js_GlobalMenu_production_{res}_godBonus_value"` | Resource tooltip | God/deity bonus percentage (varies by god) | |
| `id="js_GlobalMenu_production_{res}_active_bonuses"` | Resource tooltip | Container for all active bonus rows | |
| `id="js_GlobalMenu_branchOffice_{res}"` | Resource tooltip | Trading Post contribution for this resource | Applies to wood, wine, marble, crystal, sulfur |
| `id="js_GlobalMenu_badTaxAccountant"` | Gold tooltip | Bad Tax Accountant bonus (gold income boost) | |
| `id="js_GlobalMenu_godGoldResult"` | Gold tooltip | God (Plutus) gold bonus | |
| `id="js_GlobalMenu_scientistsUpkeep"` | Gold tooltip | Scientists upkeep cost per hour | |

### Transport Resource IDs

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `id="js_GlobalMenu_freeTransporters"` | Top nav | Available merchant ships | |
| `id="js_GlobalMenu_maxTransporters"` | Top nav | Total merchant ships | |
| `id="js_GlobalMenu_freeFreighters"` | Top nav | Available freighters | |
| `id="js_GlobalMenu_maxFreighters"` | Top nav | Total freighters | |

### Resource CSS Classes

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `class="wood"` / `class="wood bonusActive"` | Resource elements | Wood/building material. `bonusActive` when production bonus is active. | |
| `class="wine"` | Resource elements | Wine resource identifier | |
| `class="marble"` / `class="marble bonusActive"` | Resource elements | Marble resource. `bonusActive` when production bonus is active. | |
| `class="glass"` | Resource elements | Crystal glass resource identifier | |
| `class="sulfur"` | Resource elements | Sulfur resource identifier | |
| `class="population"` | Resource elements | Population identifier | |
| `class="actions"` | Resource elements | Action points identifier | |
| `class="transporters"` | Resource elements | Merchant ships identifier | |
| `class="freighters"` | Resource elements | Freighters identifier | |
| `class="goldBonus"` | Resource elements | Gold display identifier | |
| `class="ambrosiaNoSpin"` | Resource elements | Premium currency (no animation) | |
| `id="resources_population"` | Resource bar | Population resource element | |
| `id="resources_wood"` | Resource bar | Wood resource element | |
| `id="resources_wine"` | Resource bar | Wine resource element | |
| `id="resources_marble"` | Resource bar | Marble resource element | |
| `id="resources_glass"` | Resource bar | Crystal glass resource element | |
| `id="resources_sulfur"` | Resource bar | Sulfur resource element | |

---

## 8. Military / Troops

### Military Views & Navigation

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `?view=militaryAdvisor&oldView={view}&cityId={id}` | URL parameter | Military advisor overview | |
| `?view=premiumMilitaryAdvisor&oldView={view}&cityId={id}` | URL parameter | Premium military advisor (more detail) | |
| `?view=cityMilitary&activeTab=tabUnits&cityId={id}` | URL parameter | City military units detail view | |
| `?view=unitdescription&unitId={id}&helpId=9` | URL parameter | Land unit detail/help page | |
| `?view=unitdescription&shipId={id}&helpId=10` | URL parameter | Ship detail/help page | |
| `?view=ikipedia&helpId=8&subHelpId=1` | URL parameter | Help: Land unit classes | |
| `?view=ikipedia&helpId=8&subHelpId=2` | URL parameter | Help: Sea unit classes | |
| `?view=ikipedia&helpId=0&subHelpId=4` | URL parameter | Help: Warfare | |
| `?view=ikipedia&helpId=0&subHelpId=5` | URL parameter | Help: Warships and Battle | |

### Military HTML Elements

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `id="js_GlobalMenu_military"` | Top nav | Military advisor link | |
| `id="js_GlobalMenu_militaryPremium"` | Top nav | Premium military advisor link | |
| `class="slot0 military"` | City menu | Military menu item | |
| `class="image_troops"` | City menu icons | Troops icon class | |
| `class="image_fireunit"` | City menu icons | Fire/siege unit icon class | |
| `<span class="namebox">Troops in the town</span>` | City menu | Troops overview label | |
| `<span class="namebox">Dismiss units</span>` | City menu | Unit dismissal label | |

---

## 9. Transport / Trade

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| *(to be filled from website scans)* | | | |

---

## 10. Research

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| *(to be filled from website scans)* | | | |

---

## 11. Island View

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `?view=island&dialog=resource` | URL parameter | Island resource view | |
| `?view=island&dialog=tradegood&type=2` | URL parameter | Island trade good view (type=2 for marble) | |
| `islandId` parameter (e.g. `3329`) | URL parameter | Unique island identifier | |

---

## 12. World Map

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `islandX` parameter (e.g. `88`) | URL parameter | World map X coordinate | |
| `islandY` parameter (e.g. `33`) | URL parameter | World map Y coordinate | |

---

## 13. Diplomacy & Alliances

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| *(to be filled from website scans)* | | | |

---

## 14. Temples & Deities

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `?view=temple&cityId={ID}&position=8` | URL parameter | Opens temple building | Position 8 in city |

---

## 15. Tavern & Museum (Happiness)

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `?view=tavern&cityId={ID}&position=15` | URL parameter | Opens tavern building | Position 15 in city |

---

## 16. Spies & Espionage

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| *(to be filled from website scans)* | | | |

---

## 17. Cookies & Session Data

### Required Cookies

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `gf-token-production` | Gameforge auth | Lobby authentication token. UUID format: `6bc8f992-9955-4c70-ae26-10f5c0221502`. Shared across all accounts under the same email. | Set after successful auth POST |
| `ikariam` | Game server | Game session cookie. Format: `{user_id}_{hex_hash}` e.g. `100128_3a65fbfa77b01d3eb7dcf5a390a934da`. | Set when following loginLink |
| `PHPSESSID` | Game server | PHP session identifier. 26-char alphanumeric e.g. `e8akcm2954afccb5lljhr9c15s`. | Set by game server |
| `cf_clearance` | Cloudflare | Cloudflare clearance token. Required to pass Cloudflare protection. | Set during Cloudflare handshake |
| `__cf_bm` | Cloudflare | Cloudflare bot management cookie | |
| `GTPINGRESSCOOKIE` | Load balancer | Session routing / ingress cookie | |
| `pc_idt` | Pixel Zirkus | Base64-encoded device identifier for fingerprinting / account-device linking | Set during Phase 3 fingerprinting |
| `gf_pz_token` | Pixel Zirkus | Pixel Zirkus tracking token. UUID format. | |
| `gf-cookie-consent-4449562312` | Gameforge | Cookie consent preferences. Value like `\|7\|1`. | |
| `ikariam_loginMode` | Game server | Login mode flag. `0` = logged in, `1` = guest. | |

### Session Expiration Detection

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `"index.php?logout"` in response HTML | Game server response | Session has expired — logout link present means server considers session dead | |
| `'<a class="logout"'` in response HTML | Game server response | Alternative session expiry check | |
| `"nologin_umod"` in response HTML | Game server response | Account is in vacation mode — cannot interact | |

---

## 18. API Endpoints & AJAX Calls

### Game Server Endpoints

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `POST /index.php` | Game server | Main action handler. All game actions go through this endpoint. | Content-Type: `application/x-www-form-urlencoded; charset=UTF-8` |
| `?view=updateGlobalData&backgroundView=city&currentCityId={ID}&templateView={view}` | Game server | Syncs global game data (resources, timers, etc.) | Called periodically and after actions |

### Standard POST Parameters

| Parameter | Type | Example | Purpose |
|-----------|------|---------|---------|
| `action` | string | `AvatarAction` | Action class name |
| `function` | string | `giveDailyActivityBonus` | Action method name |
| `cityId` | integer | `1295` | Target city ID |
| `position` | integer | `0-18` | Building position in city |
| `activeTab` | string | `multiTab1` | Active tab selector |
| `backgroundView` | string | `city` | Background view context |
| `currentCityId` | integer | `1295` | Currently active city |
| `actionRequest` | string (hex) | `45115872c6b7b9fd3350ac551d7cc868` | CSRF token (32-char hex hash) |
| `ajax` | integer | `1` | AJAX request flag (always `1`) |
| `oldView` | string | `city` | Previous view for navigation history |
| `dailyActivityBonusCitySelect` | integer | `1299` | City selection for daily bonus |
| `helpId` | integer | `0` | Help topic identifier |
| `showMe` | integer | `1` | Show current player flag (highscore) |
| `page` | string | `account` | Account page subtype |

### CSRF Token (actionRequest)

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `<input type="hidden" id="js_ChangeCityActionRequest" name="actionRequest" value="...">` | Hidden form field in HTML | CSRF protection token. Must be included in every POST request. 32-char hex hash. | Token changes after certain actions; must be re-extracted from response HTML |
| `TXT_ERROR_WRONG_REQUEST_ID` in response | Error response | Indicates the actionRequest token is stale. Must re-fetch and retry with fresh token. | |

### Required HTTP Headers for Game Requests

```
Accept: */*
Accept-Language: en-GB;q=0.9,en;q=0.8
Accept-Encoding: gzip, deflate, br
Connection: keep-alive
Content-Type: application/x-www-form-urlencoded; charset=UTF-8
DNT: 1
Origin: https://s59-en.ikariam.gameforge.com
Referer: https://s59-en.ikariam.gameforge.com/?view=city&cityId=1295
Sec-Fetch-Dest: empty
Sec-Fetch-Mode: cors
Sec-Fetch-Site: same-origin
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36
X-Requested-With: XMLHttpRequest
sec-ch-ua: "Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"
sec-ch-ua-mobile: ?0
sec-ch-ua-platform: "Windows"
```

---

## 19. JavaScript Variables & Functions

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `window.ikariam` | Global JS | Main game object | |
| `ajaxHandlerCall(url)` | Global JS function | All in-game navigation uses this. Called via `onclick="ajaxHandlerCall(this.href);return false;"` | |
| `window.dataLayer` | Global JS | Google Tag Manager data layer | |
| `mmoticker` | Global JS | News ticker object | |

### `dataSetForView` — Main Game State Object

This object is embedded in every page response and contains the current game state. Key fields:

```
dataSetForView = {
  gameName: "Ikariam",
  serverName: "Perseus",
  avatarId: "100128",              // Player's unique ID
  avatarAllyId: "11",              // Alliance ID
  serverTime: "1770994963",        // Unix timestamp
  backgroundView: "city",          // Current background view
  isOwnCity: true,                 // Whether viewing own city
  producedTradegood: "2",          // Island luxury type (1=wine, 2=marble, 3=crystal, 4=sulfur)

  // Current resources (indexed: resource=wood, 1=wine, 2=marble, 3=crystal, 4=sulfur)
  currentResources: { citizens, population, resource, 1, 2, 3, 4 },
  maxResources: { resource, 1, 2, 3, 4 },

  // Production rates
  resourceProduction: 1.259,       // Wood per second
  tradegoodProduction: 2.048,      // Luxury good per second
  wineSpendings: 1130,             // Wine consumed per hour
  upkeep: -50258,                  // Building upkeep per hour
  income: 14050.10,                // Gold income per hour
  badTaxAccountant: 2810.02,       // Tax bonus per hour
  scientistsUpkeep: -3410.32,      // Scientist costs per hour
  godGoldResult: 0,                // Deity gold bonus

  hasPremiumAccount: '1',          // '1' = premium, '0' = free

  // Current view context
  viewParams: { view, cityId, dialog, position },

  // Related cities with metadata
  relatedCityData: [
    { id, name, coords, tradegood, relationship, isCapital, ownerId, ownerName, islandId, islandName }
  ],

  // Advisor notification counts
  advisorData: { military, cities, research, diplomacy }
}
```

### Localization Strings Object

```
LocalizationStrings = {
  // Resource names
  ambrosia, gold, tradegood, wood, wine, marble, crystal, sulfur,

  // Deposit names
  forest: "Forest", vines: "Vines", quarry: "Quarry",
  crystalMine: "Crystal Mine", sulphurPit: "Sulphur pit",

  // Time format
  year: "Y", month: "M", day: "D", hour: "h", minute: "m", second: "s",
  decimalPoint: ".", thousandSeparator: ",",

  // Language
  language: "en"
}
```

---

## 20. In-Game Help System (ikipedia)

| helpId | subHelpId | Content | User Notes |
|--------|-----------|---------|------------|
| 0 | 0 | Basic Gameplay Overview | |
| 0 | 1 | Buildings, Building Material, Population | |
| 0 | 2 | Research | |
| 0 | 3 | Trade | |
| 0 | 4 | Warfare | |
| 0 | 5 | Warships and Battle | |
| 0 | 6 | Towns and Alliances | |
| 0 | 7 | Godly Protection | |
| 1 | — | Building Help (navigation to all buildings) | |
| 5 | — | Resources — Building Material | |
| 6 | — | Resources — Luxury Goods | |
| 8 | 1 | Land unit classes | |
| 8 | 2 | Sea unit classes | |
| 9 | — | Units (individual unit details via `unitId`) | |
| 10 | — | Ships (individual ship details via `shipId`) | |
| 18 | 4 | Dictatorship (government type) | |
| 20 | 1 | Units (alternative path) | |
| 20 | 2 | Ships (alternative path) | |

URL pattern: `?view=ikipedia&helpId={id}&subHelpId={sub}`
Building detail: `?view=buildingDetail&buildingId={id}&helpId=1`
Unit detail: `?view=unitdescription&unitId={id}&helpId=9`
Ship detail: `?view=unitdescription&shipId={id}&helpId=10`
Government detail: `?view=formOfRuleDetail&formId={id}&helpId={id}&subHelpId={sub}`

---

## 21. Miscellaneous

### CDN & Asset Domains

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `https://gf1.geo.gfsrv.net/cdn*` | Image/asset URLs | Gameforge CDN node 1 | |
| `https://gf2.geo.gfsrv.net/cdn*` | Image/asset URLs | Gameforge CDN node 2 | |
| `https://gf3.geo.gfsrv.net/cdn*` | Image/asset URLs | Gameforge CDN node 3 | |
| `https://s3-static.geo.gfsrv.net/` | Static assets | S3-hosted static assets | |

### External Scripts

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `https://pixelzirkus.gameforge.com/static/js/pz.js` | Script tag | Pixel Zirkus fingerprinting/tracking (product=ikariam, language=en, server-id=59) | |
| `https://www.googletagmanager.com/gtm.js?id=GTM-THNP3BQ` | Script tag | Google Tag Manager | |

### UI Style Classes

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `class="altTooltip"` | Tooltip rows | Alternating tooltip row styling | |
| `class="smallFont"` | Various | Small font size | |
| `class="rightText"` | Various | Right-aligned text | |
| `class="tooltip"` | Tooltip containers | Tooltip wrapper | |
| `class="bold"` | Various | Bold text emphasis | |
| `class="green"` | Values | Positive/success color (e.g. income) | |
| `class="red"` | Values | Negative/error color (e.g. upkeep) | |
| `class="hoverable"` | Interactive elements | Hover-enabled elements | |
| `class="plus_button"` | Premium features | Premium feature upgrade buttons | |
| `class="invisible"` | Hidden elements | Conditionally hidden elements | |
| `class="scrollbar-container"` | Scroll areas | Scrollable content container (uses PerfectScroll) | |

### User Agent Pool (34 agents used by ikabot)

User agent is selected deterministically based on email hash: `user_agents[sum(ord(c) for c in email) % len(user_agents)]`. This ensures the same email always uses the same user agent across sessions.
