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

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `id="js_CityPosition0Link"` | City view | Town Hall (position 0) | |
| `id="js_CityPosition1Link"` | City view | Trading Port (position 1) | |
| `id="js_CityPosition2Link"` | City view | Trading Port (position 2) | |
| `id="js_CityPosition3Link"` | City view | Chronos' Forge (position 3) | |
| `id="js_CityPosition4Link"` | City view | Warehouse (position 4) | |
| `id="js_CityPosition5Link"` | City view | Warehouse (position 5) | |
| `id="js_CityPosition6Link"` | City view | Warehouse (position 6) | |
| `id="js_CityPosition7Link"` | City view | Warehouse (position 7) | |
| `id="js_CityPosition8Link"` | City view | Temple (position 8) | |
| `id="js_CityPosition9Link"` | City view | Academy (position 9) | |
| `id="js_CityPosition10Link"` | City view | Carpenter's Workshop (position 10) | |
| `id="js_CityPosition11Link"` | City view | Wine Press (position 11) | |
| `id="js_CityPosition12Link"` | City view | Optician (position 12) | |
| `id="js_CityPosition13Link"` | City view | Firework Test Area (position 13) | |
| `id="js_CityPosition14Link"` | City view | Town Wall (position 14) | |
| `id="js_CityPosition15Link"` | City view | Tavern (position 15) | |
| `id="js_CityPosition16Link"` | City view | Architect's Office (position 16) | |
| `id="js_CityPosition17Link"` | City view | Pirate Fortress (position 17) | |
| `id="js_CityPosition18Link"` | City view | Hideout (position 18) | |

---

## 6. Buildings

### Building Types & View Parameters

| Building Name | View Param | Positions | User Notes |
|---------------|-----------|-----------|------------|
| Town Hall | `townHall` | 0 | Always position 0 |
| Trading Port | `port` | 1, 2 | Can have multiple |
| Chronos' Forge | `chronosForge` | 3 | |
| Warehouse | `warehouse` | 4, 5, 6, 7 | Can have multiple |
| Temple | `temple` | 8 | |
| Academy | `academy` | 9 | |
| Carpenter's Workshop | `carpentering` | 10 | |
| Wine Press | `vineyard` | 11 | |
| Optician | `optician` | 12 | |
| Firework Test Area | `fireworker` | 13 | |
| Town Wall | `wall` | 14 | |
| Tavern | `tavern` | 15 | |
| Architect's Office | `architect` | 16 | |
| Pirate Fortress | `pirateFortress` | 17 | |
| Hideout | `safehouse` | 18 | |

### Building URL Pattern

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `?view={viewParam}&cityId={ID}&position={POS}` | URL query string | Opens a specific building. Example: `?view=tavern&cityId=1295&position=15` | |
| `dialog=buildingConstructionList` | URL parameter | Opens the construction queue dialog for a building | |

---

## 7. Resources & Production

### Global Resource IDs (Top Navigation Bar)

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `id="js_GlobalMenu_gold"` | Top nav | Current gold amount | |
| `id="js_GlobalMenu_gold_Total"` | Top nav tooltip | Total gold display | |
| `id="js_GlobalMenu_gold_tooltip"` | Top nav tooltip | Gold tooltip container | |
| `id="js_GlobalMenu_income"` | Top nav tooltip | Hourly gold income | |
| `id="js_GlobalMenu_upkeep"` | Top nav tooltip | Hourly gold upkeep | |
| `id="js_GlobalMenu_ambrosia"` / `id="headlineAmbrosia"` | Top nav | Ambrosia (premium currency) amount | |
| `id="js_GlobalMenu_wood"` | Top nav | Building material (wood) amount | |
| `id="js_GlobalMenu_wood_Total"` | Top nav tooltip | Total wood display | |
| `id="js_GlobalMenu_wood_tooltip"` | Top nav tooltip | Wood tooltip container | |
| `id="js_GlobalMenu_wine"` | Top nav | Wine resource amount | |
| `id="js_GlobalMenu_wine_Total"` | Top nav tooltip | Total wine display | |
| `id="js_GlobalMenu_marble"` | Top nav | Marble resource amount | |
| `id="js_GlobalMenu_max_wood"` | Top nav tooltip | Wood storage capacity | |
| `id="js_GlobalMenu_max_wine"` | Top nav tooltip | Wine storage capacity | |
| `id="js_GlobalMenu_citizens"` | Top nav | Current citizen count | |
| `id="js_GlobalMenu_population"` | Top nav | Max population | |
| `id="js_GlobalMenu_maxActionPoints"` | Top nav | Available action points | |
| `id="js_GlobalMenu_WineConsumption"` | Top nav tooltip | Wine consumption per hour | |
| `id="js_GlobalMenu_resourceProduction"` | Top nav tooltip | Resource production rate | |

### Production Bonus IDs

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `id="js_GlobalMenu_production_gold_premiumBonus"` | Gold tooltip | Premium gold bonus row | |
| `id="js_GlobalMenu_production_gold_premiumBonus_value"` | Gold tooltip | Premium gold bonus value | |
| `id="js_GlobalMenu_production_wood_premiumBonus"` | Wood tooltip | Premium wood bonus row | |
| `id="js_GlobalMenu_production_wood_premiumBonus_value"` | Wood tooltip | Premium wood bonus value | |
| `id="js_GlobalMenu_production_wood_adVideoBonus"` | Wood tooltip | Ad video wood bonus row | |
| `id="js_GlobalMenu_production_wood_adVideoBonus_value"` | Wood tooltip | Ad video wood bonus value | |
| `id="js_GlobalMenu_production_wood_heliosTowerBonus"` | Wood tooltip | Helios tower wood bonus row | |
| `id="js_GlobalMenu_production_wood_heliosTowerBonus_value"` | Wood tooltip | Helios tower wood bonus value | |
| `id="js_GlobalMenu_production_wood_godBonus"` | Wood tooltip | God (deity) wood bonus row | |
| `id="js_GlobalMenu_production_wood_godBonus_value"` | Wood tooltip | God (deity) wood bonus value | |
| `id="js_GlobalMenu_branchOffice_wood"` | Wood tooltip | Trading post wood contribution row | |
| `id="js_GlobalMenu_branchOffice_wine"` | Wine tooltip | Trading post wine contribution row | |
| `id="js_GlobalMenu_badTaxAccountant"` | Gold tooltip | Bad tax accountant bonus | |
| `id="js_GlobalMenu_godGoldResult"` | Gold tooltip | God (Plutus) gold bonus | |
| `id="js_GlobalMenu_scientistsUpkeep"` | Gold tooltip | Scientists upkeep cost | |

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
| `class="wood"` | Resource elements | Wood/building material identifier | |
| `class="wine"` | Resource elements | Wine resource identifier | |
| `class="marble"` | Resource elements | Marble resource identifier | |
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

---

## 8. Military / Troops

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| *(to be filled from website scans)* | | | |

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

---

## 20. Miscellaneous

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
