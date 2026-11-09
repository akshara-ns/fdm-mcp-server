# MCP demo transcript

Run 2026-11-08T18:47:43 over stdio. Tools listed: estimate_print_job, check_cantilever, check_container, compare_failure_costs, plan_unattended_print, check_deviated_part.

## 1. Original HW4 tool: the 14-hour overnight print from interview P3

`tools/call` **compare_failure_costs**

```json
{
 "print_time_h": 14,
 "filament_g": 300,
 "failure_probability": 0.15
}
```

verdict **OK**

With p = 0.15 per attempt, each good part wastes on average 52.9 g and 2.47 h unmonitored, 16.5 g and 0.77 h with detect-and-stop, and 9.3 g and 0.43 h with detect-and-adapt.

## 2. New tool 1: same job started at bedtime, then search for a better start time

`tools/call` **plan_unattended_print**

```json
{
 "print_time_h": 14,
 "filament_g": 300,
 "start_time": "22:30",
 "away_from": "23:30",
 "back_at": "08:00",
 "earliest_start": "08:00",
 "latest_start": "23:00"
}
```

verdict **CAUTION**, decision **start at 08:00**

A 14 h job started at 22:30 finishes at 12:30 (+1 d); 61% of it runs unattended. If it fails (15% chance), on average 1.6 h pass before anyone knows; expected waste for this attempt is 16.9 g and 0.79 printer-hours ($0.78). Best start in your window: 08:00 (expected waste $0.61).

## 3. New tool 2: a bracket whose arm was thinned by an in-print repair

`tools/call` **check_deviated_part**

```json
{
 "features": [
  {
   "name": "outer shell",
   "role": "cosmetic",
   "nominal_mm": 40.0,
   "measured_mm": 40.6
  },
  {
   "name": "M5 mounting hole",
   "role": "fit",
   "nominal_mm": 5.3,
   "measured_mm": 5.22,
   "tolerance_mm": 0.15
  },
  {
   "name": "arm thickness",
   "role": "load_bearing",
   "nominal_mm": 5.0,
   "measured_mm": 4.5,
   "tolerance_mm": 0.6
  }
 ],
 "beam": {
  "material": "PLA",
  "length_mm": 40,
  "width_mm": 20,
  "nominal_thickness_mm": 5.0,
  "measured_thickness_mm": 4.5,
  "load_kg": 2
 },
 "repaired_in_load_path": true,
 "reprint_time_h": 2.5,
 "reprint_filament_g": 38
}
```

verdict **CAUTION**, decision **TEST_BEFORE_USE**

Decision: TEST_BEFORE_USE, because layers were changed in the load path, so a calculation alone cannot vouch for its strength. 3 feature(s) checked, 0 functional feature(s) out of tolerance; arm safety factor 3.35 as printed.

## 4. Invalid input (domain rule): a fit feature with no tolerance

`tools/call` **check_deviated_part**

```json
{
 "features": [
  {
   "name": "M5 mounting hole",
   "role": "fit",
   "nominal_mm": 5.3,
   "measured_mm": 5.22
  }
 ]
}
```

**isError: true**

```
Error executing tool check_deviated_part: Invalid input: M5 mounting hole: tolerance_mm is required for a fit feature. Only the designer knows how much error it can take; give the allowed deviation in mm.
```

## 5. Invalid input (schema): an impossible clock time

`tools/call` **plan_unattended_print**

```json
{
 "print_time_h": 14,
 "filament_g": 300,
 "start_time": "25:00",
 "away_from": "23:30",
 "back_at": "08:00"
}
```

**isError: true**

```
Error executing tool plan_unattended_print: 1 validation error for plan_unattended_printArguments
start_time
  String should match pattern '^([01]?\d|2[0-3]):[0-5]\d$' [type=string_pattern_mismatch, input_value='25:00', input_type=str]
    For further information visit https://errors.pydantic.dev/2.14/v/string_pattern_mismatch
```
