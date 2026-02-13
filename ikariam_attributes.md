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

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| *(to be filled from website scans)* | | | |

---

## 2. Blackbox / Anti-Bot Token System

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| *(to be filled from website scans)* | | | |

---

## 3. Lobby / Server Selection

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `https://lobby.ikariam.gameforge.com/en_US/hub` | Browser URL bar | The main lobby URL. One account can play on multiple servers simultaneously. The lobby lists all available servers and any "graveyard" servers (where inactive accounts are moved). | |
| `s59` (in URL `s59-en.ikariam.gameforge.com`) | Server URL subdomain | The server number. Each game server has a unique number. Format: `s{number}` | |
| `en` (in URL `s59-en.ikariam.gameforge.com`) | Server URL subdomain | The region/language code. Determines which regional cluster the server belongs to (e.g. `en` = English). | |
| Server URL pattern: `https://s{NUM}-{REGION}.ikariam.gameforge.com/` | Browser URL | The full base URL pattern for any game server. Combine server number + region to form the base URL. | |
| Graveyard server | Lobby server list | A special server where accounts are moved after prolonged inactivity. Not a regular playable server. | |

---

## 4. Navigation & Menus

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| *(to be filled from website scans)* | | | |

---

## 5. City View

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| `?view=city&cityId={ID}` | URL query string | Navigates to a specific city. Each city has a unique numeric ID. Example: `?view=city&cityId=1295` loads the city with ID 1295. | |
| `cityId` | URL parameter / API parameter | The unique numeric identifier for a city. Used throughout the game in URLs and API calls to reference a specific city. First city example: `1295`. | |
| `view=city` | URL parameter | Tells the game to render the city view for the given `cityId`. | |

---

## 6. Buildings

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| *(to be filled from website scans)* | | | |

---

## 7. Resources & Production

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| *(to be filled from website scans)* | | | |

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
| *(to be filled from website scans)* | | | |

---

## 12. World Map

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| *(to be filled from website scans)* | | | |

---

## 13. Diplomacy & Alliances

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| *(to be filled from website scans)* | | | |

---

## 14. Temples & Deities

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| *(to be filled from website scans)* | | | |

---

## 15. Tavern & Museum (Happiness)

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| *(to be filled from website scans)* | | | |

---

## 16. Spies & Espionage

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| *(to be filled from website scans)* | | | |

---

## 17. Cookies & Session Data

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| *(to be filled from website scans)* | | | |

---

## 18. API Endpoints & AJAX Calls

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| *(to be filled from website scans)* | | | |

---

## 19. JavaScript Variables & Functions

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| *(to be filled from website scans)* | | | |

---

## 20. Miscellaneous

| Reference Code | Location | Purpose | User Notes |
|----------------|----------|---------|------------|
| *(to be filled from website scans)* | | | |
