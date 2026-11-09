# fdm-mcp

An [MCP](https://modelcontextprotocol.io) server with six calculators for people who run desktop FDM 3D printers.
It answers questions like *"how much will this print cost?"*, *"will this bracket hold?"* and *"is it safe to start
this 14-hour job before bed?"*.

The server only computes. It never talks to a printer, never edits G-code and never calls an LLM, so the same input
always gives the same answer.

Built for 24-679 *Designing & Prototyping AI Systems* (CMU), Homework 5.

## The tools

| Tool | Question it answers |
|------|---------------------|
| `estimate_print_job` | How much filament, time, energy and money will this print take? |
| `check_cantilever` | Will a printed bracket, hook or clip hold its load? |
| `check_container` | Can a printed cup or container hold a liquid? |
| `compare_failure_costs` | How much do failed prints waste per good part, with and without camera monitoring? |
| `plan_unattended_print` **(new)** | If I start this job before bed, how long could a failure go unseen, what would it waste, and is there a better start time? |
| `check_deviated_part` **(new)** | This part came out (or was repaired) off-design. Should I keep it, test it first, or reprint it? |

The first four come from my Homework 4 agent. The two new ones came out of user interviews: people leave long prints
running overnight and find failures hours later, and only the designer knows which features of a part can be off.

## Quick start

Requires Python 3.10+.

```bash
git clone https://github.com/akshara-ns/fdm-mcp-server && cd fdm-mcp-server
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q                     # 20 tests, all run through an MCP client
```

**Try it in MCP Inspector** (needs Node 22+):

```bash
npx @modelcontextprotocol/inspector .venv/bin/fdm-mcp
```

Open the URL it prints (it includes a session token), switch the server's toggle on, then go to **Tools**.

**Use it from an MCP client** such as Claude Desktop or Claude Code, by adding this to the client's MCP config:

```json
{ "mcpServers": { "fdm-print-tools": { "command": "/ABSOLUTE/PATH/fdm-mcp-server/.venv/bin/fdm-mcp" } } }
```

**Over HTTP** instead of stdio: `fdm-mcp --http` serves `http://127.0.0.1:8000/mcp`.

**Recorded demo:** `python demo/client_demo.py` starts the server, makes five calls (one HW4 tool, both new tools and
two invalid inputs) and saves them to `demo/transcript.json` and `demo/transcript.md`. Inspector screenshots are in
`demo/screenshots/`.

## Example

Calling `plan_unattended_print` for a 14-hour, 300 g job started at 22:30, with nobody around from 23:30 to 08:00,
and asking for the best start time between 08:00 and 23:00:

```json
{ "print_time_h": 14, "filament_g": 300, "start_time": "22:30", "away_from": "23:30", "back_at": "08:00",
  "earliest_start": "08:00", "latest_start": "23:00" }
```

returns (shortened):

```text
verdict   CAUTION
decision  start at 08:00
summary   A 14 h job started at 22:30 finishes at 12:30 (+1 d); 61% of it runs unattended. If it fails
          (15% chance), on average 1.6 h pass before anyone knows; expected waste for this attempt is
          16.9 g and 0.79 printer-hours ($0.78). Best start in your window: 08:00 (expected waste $0.61).
results   worst_case_hours_unseen 8.5 h, recommended_mean_hours_until_known 0.5 h, ...
checks    First layers watched: OK · Unseen failure time: OK · No automatic stop: CAUTION
defaults  failure_probability = 0.15, early_failure_share = 0.5, check_interval_min = 60, ...
human_checks  Watch the first layer yourself before leaving, whatever the tool says. ...
```

## What every tool returns

All six tools return the same structured record (`ToolRecord`, published as the tool's `outputSchema`):

| Field | Meaning |
|-------|---------|
| `verdict` | `OK`, `CAUTION` or `FAIL` |
| `decision` | new tools only, e.g. `start at 08:00` or `TEST_BEFORE_USE` |
| `summary` | one-paragraph answer in plain language |
| `results` | each value as `{label, value, unit}` |
| `checks` | `{name, status, detail}` for each pass/fail test |
| `inputs`, `defaults_used` | every input actually used, and which ones were defaults |
| `assumptions`, `warnings` | what the maths assumes, and anything unusual about this input |
| `human_checks` | what a person should verify before acting on the result |

**Invalid input never crashes the server.** The call returns `isError: true` with a readable message, at one of two
levels:

1. **Schema.** Every input has a type, and numbers have a minimum and maximum. Choices are enums and times must
   match `HH:MM`. The SDK rejects a bad value before the tool runs, naming the field.
2. **Tool.** Rules a schema cannot express return `Invalid input: …`. Examples are a layer height above 80 % of the
   line width, no load given, `away_from` equal to `back_at`, or a fit feature without a tolerance.

## Tool reference

Units and valid ranges for every input. Inputs without a default are required.

### `estimate_print_job`

Filament mass and length, print time, energy and cost for one print. Give a box or cylinder size, or the volume and
surface area from your CAD tool.

<details><summary>Inputs</summary>

| Input | Unit | Range | Default |
|---|---|---|---|
| `material` | PLA / PETG / ABS | | PLA |
| `shape` | box / cylinder / custom | | custom |
| `size_x_mm`, `size_y_mm` | mm (cylinder: x = diameter) | 1–350 | for box or cylinder |
| `size_z_mm` | mm | 0.2–350 | for box or cylinder |
| `volume_cm3` | cm³ | 0.05–20 000 | for custom |
| `surface_area_cm2` | cm² | 0.5–20 000 | for custom |
| `infill_percent` | % | 0–100 | 15 |
| `wall_loops` | count | 1–10 | 2 |
| `line_width_mm` | mm | 0.2–1.2 | 0.42 |
| `layer_height_mm` | mm, at most 80 % of line width | 0.04–0.6 | 0.2 |
| `print_speed_mm_s` | mm/s | 5–600 | 150 |
| `max_flow_mm3_s` | mm³/s | 1–60 | 21 |
| `motion_efficiency` | share of time extruding | 0.1–1 | 0.6 |
| `printer_power_W` | W | 10–2000 | 110 |
| `filament_price_per_kg` | $/kg | 0–500 | 23 |
| `electricity_price_per_kWh` | $/kWh | 0–2 | 0.17 |

</details>

**Results:** filament (g, m), print time (h), energy (kWh), cost ($).

### `check_cantilever`

Bending stress, safety factor, breaking load and tip deflection of a printed arm loaded at its end.

<details><summary>Inputs</summary>

| Input | Unit | Range | Default |
|---|---|---|---|
| `length_mm`, `width_mm`, `thickness_mm` | mm | 5–400, 1–200, 0.8–100 | required |
| `load_N` or `load_kg` | N or kg | 0.01–5000 or 0.001–500 | one is required |
| `material` | PLA / PETG / ABS | | PLA |
| `orientation` | flat (layers along the arm) / upright (load pulls layers apart) | | flat |
| `target_safety_factor` | – | 1–10 | 2 |
| `interlayer_factor` | strength across layers ÷ in-plane | 0.1–1 | 0.5 |

</details>

**Results:** stress (MPa), safety factor, breaking load (N), deflection (mm), thickness needed for the target.

### `check_container`

Capacity, wall stress from the liquid, watertightness and heat margin of a printed cup.

<details><summary>Inputs</summary>

| Input | Unit | Range | Default |
|---|---|---|---|
| `inner_diameter_mm`, `height_mm` | mm | 5–300 | required |
| `wall_thickness_mm` | mm | 0.3–20 | required |
| `material` | PLA / PETG / ABS | | PLA |
| `liquid_temp_C` | °C | −20–100 | 20 |
| `liquid_density_kg_m3` | kg/m³ | 500–2000 | 1000 |
| `line_width_mm` | mm | 0.2–1.2 | 0.42 |
| `interlayer_factor` | – | 0.1–1 | 0.5 |

</details>

**Results:** capacity (mL), hoop stress (MPa), wall lines (at least 3 for a watertight wall), margin to the heat
deflection temperature (°C).

### `compare_failure_costs`

Expected waste per good part for three strategies: A) unmonitored, B) camera detects and stops, C) camera detects
and adapts the print.

<details><summary>Inputs</summary>

| Input | Unit | Range | Default |
|---|---|---|---|
| `print_time_h` | h | 0.05–500 | required |
| `filament_g` | g | 0.1–10 000 | required |
| `failure_probability` | chance one attempt fails | 0–0.9 | 0.15 |
| `mean_failure_point` | share of the job done when it fails | 0–1 | 0.3 |
| `detection_delay_min` | min | 0–600 | 10 |
| `recoverable_share` | share of detected failures fixed in place (a hypothesis) | 0–1 | 0.4 |
| `filament_price_per_kg` | $/kg | 0–500 | 23 |
| `machine_cost_per_h` | $/h | 0–200 | 0.5 |
| `jobs` | good parts to total over | 1–1 000 000 | 100 |

</details>

**Results:** wasted grams, hours and dollars per good part for A, B and C, and the savings over `jobs` parts.

### `plan_unattended_print` (new)

For a job that runs partly while nobody is watching: when it finishes, how much of it is unattended, how long a
failure would go unseen, the expected waste, and optionally the best start time in a window.

<details><summary>Inputs</summary>

| Input | Unit | Range | Default |
|---|---|---|---|
| `print_time_h` | h | 0.05–200 | required |
| `filament_g` | g | 0.1–10 000 | required |
| `start_time`, `away_from`, `back_at` | `"HH:MM"`, 24 h (the away window may cross midnight) | 00:00–23:59 | required |
| `detection` | none / auto_stop (printer camera pauses the job) | | none |
| `failure_probability` | – | 0–0.9 | 0.15 (assumption) |
| `early_failure_share` | share of failures in the first layers | 0–1 | 0.5 (assumption) |
| `early_window_h` | h | 0.05–5 | 0.5 |
| `detection_coverage` | share of failures auto_stop catches | 0–1 | 0.6 (assumption) |
| `detection_delay_min` | min | 0–600 | 10 |
| `check_interval_min` | how often you look while present, min | 1–480 | 60 |
| `filament_price_per_kg` | $/kg | 0–500 | 23 |
| `machine_cost_per_h` | $/h | 0–200 | 0.5 |
| `earliest_start`, `latest_start` | `"HH:MM"`, give both or neither | | no search |

</details>

**How it works.** The failure time is a mix of early failures in the first layers and failures spread evenly over the
job. A failure is noticed at the next look while someone is present (half the check interval on average), at
`back_at` while nobody is, or after `detection_delay_min` if auto-stop catches it. A failed job keeps extruding until
it is stopped or the G-code ends. With a search window, start times are tried every 15 minutes, and the one with the
lowest expected waste cost among those whose first layers are watched is recommended.

**Results:** finish time, unattended share and hours, chance a failure happens unattended, mean and worst-case hours
until known, expected wasted g / h / $, and the recommended start with its finish time and expected waste.

### `check_deviated_part` (new)

Decides **KEEP**, **TEST_BEFORE_USE** or **REPRINT** for a part that differs from its design.

<details><summary>Inputs</summary>

| Input | Unit | Range | Default |
|---|---|---|---|
| `features` | list of 1–30 features, each with: | | required |
| &nbsp;&nbsp;`name` | text, e.g. "mounting hole" | | required |
| &nbsp;&nbsp;`role` | cosmetic / fit / load_bearing | | required |
| &nbsp;&nbsp;`nominal_mm` | mm | 0.1–500 | required |
| &nbsp;&nbsp;`measured_mm` | mm | 0–500 | required |
| &nbsp;&nbsp;`tolerance_mm` | allowed \|measured − nominal\|, mm | 0.01–10 | **required for fit and load_bearing**; 1.0 for cosmetic |
| `beam` | a load-bearing arm to re-check: `material`, `length_mm`, `width_mm`, `nominal_thickness_mm`, `measured_thickness_mm`, `load_N` or `load_kg`, `orientation`, `target_safety_factor` | as `check_cantilever` | optional |
| `repaired_in_load_path` | true if layers were changed or restarted where load passes | | false |
| `reprint_time_h`, `reprint_filament_g` | h, g | 0.05–500, 0.1–10 000 | optional, shows what scrapping costs |
| `filament_price_per_kg` | $/kg | 0–500 | 23 |
| `machine_cost_per_h` | $/h | 0–200 | 0.5 |

</details>

**How it decides.** A feature passes if |measured − nominal| ≤ tolerance. With a `beam`, the cantilever check runs
at both the design and the as-printed thickness (strength scales with thickness²).

- **REPRINT** if a fit or load-bearing feature is out of tolerance, or the arm's safety factor falls below 1.
- **TEST_BEFORE_USE** if a load-bearing feature has no strength check, the safety factor is below the target, or the
  repair crossed the load path.
- **KEEP** otherwise.

**Results:** each feature's deviation and share of tolerance used, design vs as-printed safety factor, strength
retained, reprint cost, and the decision.

## Client and server responsibilities

| The client (LLM host, Inspector or a person) | The server (this repo) |
|------|------|
| Understands the request and chooses the tool | Validates every input |
| Asks the user for missing measurements instead of guessing | Computes deterministically: same input, same output |
| Never computes numbers itself, quotes results with units | Reports every default, assumption and warning it used |
| Shows `defaults_used`, `warnings` and `human_checks` | Recommends only, with no access to printers or files |
| Decides with the user what to do, and holds API keys and chat history | Holds no state, secrets or network access |

All tools are annotated `readOnlyHint`, `idempotentHint` and not `openWorldHint`.

## Before acting on a result

These are screening estimates, not guarantees. No tool models hardware faults, bed adhesion, moisture or how well a
repaired region bonds, and those cause most real failures. In particular:

- **Unattended planner:** watch the first layer anyway, check that unattended printing is allowed and that the printer
  has thermal-runaway protection, and replace the default 15 % failure rate with your own printer's record.
- **Deviated part:** measure the features yourself, since the tool trusts the numbers it gets. Confirm tolerances with
  whoever designed the part, and load-test any TEST_BEFORE_USE part, or a sacrificial copy, before relying on it.

## Repository layout

```text
src/fdm_mcp/server.py       MCP server: tool definitions, schemas, ToolRecord, stdio / HTTP entry point
src/fdm_mcp/print_tools.py  the four HW4 calculators and material data
src/fdm_mcp/new_tools.py    plan_unattended_print and check_deviated_part
tests/test_server.py        20 tests through an in-process MCP client
demo/                       client_demo.py, its recorded transcript, Inspector screenshots
```

## Licence and sources

MIT licence. Material values come from the Bambu Lab PLA Basic and PETG Basic technical data sheets (v2.0) and the
Stratasys ABS-M30 data sheet.
