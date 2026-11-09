# fdm-mcp: FDM print-planning tools over MCP

An [MCP](https://modelcontextprotocol.io) server with six deterministic calculators for people who run desktop FDM
(material-extrusion) 3D printers. Four tools come from my 24-679 Homework 4 agent; two are new in Homework 5 and
were chosen from user interviews (see the HW5 report).

| Tool | Origin | What it answers |
|------|--------|-----------------|
| `estimate_print_job` | HW4 | How much filament, time, energy and money will this print take? |
| `check_cantilever` | HW4 | Will a printed bracket, hook or clip hold its load? |
| `check_container` | HW4 | Can a printed cup hold a liquid (pressure, seal, heat)? |
| `compare_failure_costs` | HW4 | What do failures waste per good part, unmonitored vs detect-and-stop vs detect-and-adapt? |
| `plan_unattended_print` | **new** | If I start this job before bed, how long can a failure go unseen, what will it waste, and is there a better start time? |
| `check_deviated_part` | **new** | A part came out (or was repaired) off-design: KEEP, TEST_BEFORE_USE or REPRINT? |

The server only computes. It never talks to a printer, never edits G-code and never calls an LLM.

## Setup

Requires Python 3.10+ and the MCP Python SDK 2.x.

```bash
git clone <this repo> && cd fdm-mcp-server
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q                      # 20 tests, all through an MCP client
```

**MCP Inspector** (Node 22+):

```bash
npx @modelcontextprotocol/inspector .venv/bin/fdm-mcp
```

Open the URL it prints (it includes the session token), switch the server on, then go to **Tools**.

**Claude Desktop / Claude Code**: add to the client's MCP config:

```json
{ "mcpServers": { "fdm-print-tools": { "command": "/ABSOLUTE/PATH/fdm-mcp-server/.venv/bin/fdm-mcp" } } }
```

or `claude mcp add fdm-print-tools -- /ABSOLUTE/PATH/fdm-mcp-server/.venv/bin/fdm-mcp`.

**Streamable HTTP** instead of stdio: `fdm-mcp --http` (serves `http://127.0.0.1:8000/mcp`).

**Recorded demo**: `python demo/client_demo.py` launches the server over stdio, makes the five calls in the report
and writes `demo/transcript.json` / `.md`. Inspector screenshots are in `demo/screenshots/`.

## Contracts

Every tool returns the same structured record (`outputSchema` = `ToolRecord`):

```text
tool, verdict (OK | CAUTION | FAIL), decision (new tools), summary,
results {key: {label, value, unit}}, checks [{name, status, detail}], features (check_deviated_part),
inputs (everything used, after defaults), defaults_used, assumptions, warnings, human_checks
```

**Errors.** Invalid input never crashes the server; the call returns `isError: true` with a readable message.
There are two layers. (1) The **schema** carries types, enums, `HH:MM` patterns and min/max for every number, so
the SDK rejects out-of-range or wrongly typed arguments before the tool runs (pydantic message naming the field).
(2) The **tool** enforces rules that a schema cannot express and answers `Invalid input: …`, for example a layer
height above 80 % of the line width, a missing load, `away_from == back_at`, or a fit feature without a tolerance.

All tools are annotated `readOnlyHint: true, idempotentHint: true, openWorldHint: false`.

### HW4 tools (unchanged maths; see the HW4 report for derivations and sources)

| Tool | Required inputs (unit, range) | Main optional inputs (default) | Key results |
|------|------------------|--------------|-------------|
| `estimate_print_job` | `shape` box/cylinder/custom + sizes (mm, 1–350) or `volume_cm3` (0.05–20 000) and `surface_area_cm2` (0.5–20 000) | material PLA/PETG/ABS, infill 0–100 % (15), walls 1–10 (2), line width 0.2–1.2 mm (0.42), layer 0.04–0.6 mm (0.2), speed 5–600 mm/s (150), flow limit 1–60 mm³/s (21), η 0.1–1 (0.6), power 10–2000 W (110) | filament g and m, print time h, energy kWh, cost $ |
| `check_cantilever` | `length_mm` 5–400, `width_mm` 1–200, `thickness_mm` 0.8–100, and `load_N` 0.01–5000 or `load_kg` 0.001–500 | orientation flat/upright, target SF 1–10 (2), interlayer factor 0.1–1 (0.5) | stress MPa, safety factor, breaking load N, deflection mm, thickness for target |
| `check_container` | `inner_diameter_mm` 5–300, `height_mm` 5–300, `wall_thickness_mm` 0.3–20 | liquid −20–100 °C (20), density 500–2000 kg/m³ (1000) | capacity mL, hoop stress MPa, wall lines, heat margin °C |
| `compare_failure_costs` | `print_time_h` 0.05–500, `filament_g` 0.1–10 000 | p fail 0–0.9 (0.15), failure point 0–1 (0.3), detection delay 0–600 min (10), recoverable share 0–1 (0.4) | wasted g, h, $ per good part for A/B/C and savings |

### `plan_unattended_print` (new)

*Purpose.* Interviewees leave long prints running overnight or in an empty lab and find failures hours later. This
tool shows how exposed a particular start time is and searches a window for a better one.

| Input | Type / unit | Range | Default |
|-------|-------------|-------|---------|
| `print_time_h`, `filament_g` | float, h / g | 0.05–200 / 0.1–10 000 | required |
| `start_time`, `away_from`, `back_at` | `"HH:MM"` 24 h | 00:00–23:59; away window may cross midnight, must not be empty | required |
| `detection` | `"none"` \| `"auto_stop"` | | none |
| `failure_probability` | float | 0–0.9 | 0.15 (assumption) |
| `early_failure_share`, `early_window_h` | float, – / h | 0–1 / 0.05–5 | 0.5, 0.5 h (assumption) |
| `detection_coverage`, `detection_delay_min` | float, – / min | 0–1 / 0–600 | 0.6, 10 (auto_stop only) |
| `check_interval_min` | float, min | 1–480 | 60 |
| `earliest_start`, `latest_start` | `"HH:MM"` | both or neither | none (no search) |

*Method.* Failure time is drawn from a mixture: `early_failure_share` in the first `early_window_h`, the rest uniform
over the job (2 000-point quadrature). A failure is noticed at the next look while someone is present (half the
check interval on average), at `back_at` while nobody is, or `detection_delay_min` later for the `detection_coverage`
share caught by auto-stop. A failed job keeps extruding until stopped or the G-code ends.

*Results.* finish time, unattended share and hours, chance a failure happens unattended, mean and worst-case hours
until known, idle hours before restart, expected wasted g / h / $ for this attempt, and with a search window the
recommended start, its finish time and its expected waste. Checks: first layers watched, unseen failure time (> 2 h),
no automatic stop. *Errors:* bad clock strings (schema), `away_from == back_at`, only one search bound (tool).

### `check_deviated_part` (new)

*Purpose.* Interviewees said that whether an off-design part still works is "very part specific", that only the
designer knows which features can take error, and that a repaired part that looks fine may not be strong enough.

| Input | Type / unit | Range | Default |
|-------|-------------|-------|---------|
| `features[]` | 1–30 × {`name`, `role` cosmetic/fit/load_bearing, `nominal_mm`, `measured_mm`, `tolerance_mm`} | mm 0.1–500, tolerance 0.01–10 | tolerance **required** for fit and load_bearing; 1.0 mm for cosmetic |
| `beam` | {material, length_mm, width_mm, nominal_thickness_mm, measured_thickness_mm, load_N or load_kg, orientation, target_safety_factor} | as `check_cantilever` | optional |
| `repaired_in_load_path` | bool | | false |
| `reprint_time_h`, `reprint_filament_g` | float, h / g | 0.05–500 / 0.1–10 000 | optional, shows what scrapping costs |

*Method.* Each feature passes if |measured − nominal| ≤ tolerance. Out-of-tolerance cosmetic features give CAUTION;
fit or load-bearing ones give FAIL. If `beam` is given, `check_cantilever` runs at the design and as-printed
thickness (strength ∝ t²). *Decision:* REPRINT if any functional feature fails or the arm's safety factor drops below
1; TEST_BEFORE_USE if a load-bearing feature has no strength check, its safety factor is below target, or the repair
crossed the load path; otherwise KEEP. *Errors:* a fit/load-bearing feature without `tolerance_mm`, bad role,
out-of-range numbers.

## Responsibilities

| Client (LLM host, Inspector, a person) | Server (this repo) |
|------|------|
| Understand the request, choose the tool, ask the user for missing measurements | Validate every input against its schema and domain rules |
| Never compute numbers itself; quote results with units | Compute deterministically; same input → same output |
| Show `defaults_used`, `warnings` and `human_checks` to the user | Report every default, assumption and warning it applied |
| Decide what to do (print, keep, reprint), with the user | Recommend only; it has no access to printers or files |
| Hold API keys and conversation state | Holds no state, secrets or network access |

## License

MIT. Material values come from Bambu Lab PLA/PETG Basic TDS v2.0 and the Stratasys ABS-M30 data sheet.
