# Workspace map

Authoritative index of the user's Notion workspace. Verified 2026-07-26. If a write fails validation against this map, the map is stale — follow the Self-maintenance procedure in SKILL.md.

## Key pages

| Page | URL / ID |
|---|---|
| Projects (parent of all project pages) | https://www.notion.so/219d0d51fde9808da6f7cad2c654691c |
| Sessions database (page) | https://www.notion.so/f7752ab8569540a49a3d89afd3240e5a |
| Sessions **data_source_id** (use for creates) | `06c59d57-d167-4164-9e54-d33a27789071` |
| Agent Instructions (ARCHIVED — superseded by this skill) | https://www.notion.so/355d0d51fde98142a401d69504d23100 |

## Sessions database schema

| Property | Type | Values / notes |
|---|---|---|
| Title | title | `Topic — subtitle` style |
| Date | date | single date, no time; `date:Date:is_datetime` = integer `0` (a string `"0"` fails) |
| Project | select | **closed set:** `strat.bz`, `drones`, `navvy`, `hailey`, `diesel-heater`, `general`, `running` |
| Status | select | `Draft`, `Complete` — in practice always `Complete` |
| Tags | multi_select | **closed set:** `architecture`, `electrical`, `debugging`, `planning`, `firmware`, `hardware`, `infrastructure` — anything else fails validation (e.g. "cfd" once did) |

Views: "All Sessions" (Date desc) plus per-project filtered views for strat.bz, navvy, drones, hailey, diesel-heater — prefer the per-project view when checking recent sessions for a project.

## Project slug ↔ project page mapping

| Sessions `Project` slug | Projects page | Page ID |
|---|---|---|
| `strat.bz` | 📈 strat.bz | `301d0d51fde980c5bc3bf8f515ca443f` |
| `hailey` | 🚛 Hailey | `1e2d0d51fde9805bb1f5df27054c0692` |
| `navvy` | 🤖 Navvy | `32fd0d51fde98050a2a6c470f53fed32` |
| `drones` | runner · sentinel · scorpion (combined work-drones page) | — |
| `diesel-heater` | Diesel Heater Controller | — |
| `general` | catch-all, no project page | — |
| `running` | no project page (personal category) | — |

("ultrasonics" exists under Projects but is an empty stub with no Sessions slug.)

## Canonical pages index

Pages repeatedly used as reference material. Fetch by ID; do not re-search.

| When the user says… | Page | ID |
|---|---|---|
| "teaching analysis" / "business game evaluation" / "learning objectives analysis" (strat.bz) | **Evidence Review (Final) — What Makes a University Business Simulation Valuable, and Where strat.bz Should Head** | `396d0d51-fde9-811d-94b3-f32b6a5cbacd` |
| "comparison with an existing business game" / "competitor analysis" | Competitor Analysis — GoVenture CEO vs strat.bz | `396d0d51…81b1…` (search title if truncated ID fails) |
| "solo mode design" | Solo Mode — Design Grilling Session | `38fd0d51…` |
| "solo mode scope/cost" | Solo Mode — Implementation Scope & Cost Report | `396d0d51…8152…` |
| "hailey aero plan" / "CFD plan" | Aero Development Plan — Fast Design-Test System | search "Aero Development Plan" |
| "roof rack aero" | Roof Rack Aero CFD | `27ed0d51fde9809fb1b1ca159c4102f8` |

Note: Evidence Review has near-duplicate **v1** and **v2 (Refined)** siblings — always use **(Final)** unless the user says otherwise.
