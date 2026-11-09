"""Two new analytical tools added for HW5, chosen from our user interviews.

  plan_unattended_print   When will an overnight / unattended job fail unseen, how much does it waste before
                          anyone finds out, and which start time keeps the first layers watched?
  check_deviated_part     A part came out (or was repaired) off-design: feature by feature, against tolerances the
                          designer gives, is it still usable, and does a load-bearing arm keep its safety factor?

Both return the same record shape as the HW4 tools in print_tools.py, plus "decision" and "human_checks".
"""
from __future__ import annotations

import math

from .print_tools import (MATERIALS, ToolInputError, _check_range, _defaults, _r, _res, _worst,
                          check_cantilever)


# ----------------------------------------------------------------------------------------------------
# Tool 5: unattended / overnight print exposure
# ----------------------------------------------------------------------------------------------------
def _clock(name, s):
    """'HH:MM' (24 h) -> hours after midnight."""
    try:
        hh, mm = str(s).strip().split(":")
        hh, mm = int(hh), int(mm)
    except Exception:
        raise ToolInputError(f"{name} must be a 24-hour time 'HH:MM' (got {s!r}).")
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ToolInputError(f"{name} = {s!r} is not a valid time; use 00:00–23:59.")
    return hh + mm / 60


def _fmt(h):
    """hours after day-0 midnight -> 'HH:MM (+d)'."""
    day, rem = divmod(h, 24)
    mins = int(round(rem * 60))
    if mins == 24 * 60:
        day, mins = day + 1, 0
    return f"{mins // 60:02d}:{mins % 60:02d}" + (f" (+{int(day)} d)" if day >= 1 else "")


def _exposure(start, T, m, win_a, win_len, p, early_share, early_h, detection, cover, delay_h, check_h, fp, mc,
              n=2000):
    """Expected outcomes of one attempt started at clock time `start` (hours)."""
    end = start + T

    def unattended(t):
        return ((t - win_a) % 24) < win_len

    def human_discovery(t):
        if unattended(t):
            return t + (win_len - ((t - win_a) % 24))       # found when the person is back
        return t + check_h / 2                             # found at the next look, on average

    # Failure-time density over the job: early_share inside the first early_h, the rest spread uniformly.
    e = min(early_h, T)
    pts = []
    for i in range(n):
        u = (i + 0.5) / n
        pts.append((start + u * e, early_share / n))
        pts.append((start + u * T, (1 - early_share) / n))

    w_sum = g_sum = d_sum = un_sum = blk_sum = 0.0
    worst = 0.0
    for tf, w in pts:
        t_h = human_discovery(tf)
        if detection == "auto_stop":
            t_auto = tf + delay_h
            stop = cover * min(t_auto, end) + (1 - cover) * min(t_h, end)
            found = cover * min(t_auto, t_h) + (1 - cover) * t_h
        else:
            stop = min(t_h, end)
            found = t_h
        frac = (stop - start) / T
        w_sum += w * frac
        d_sum += w * (found - tf)
        un_sum += w * (1.0 if unattended(tf) else 0.0)
        blk_sum += w * max(0.0, found - end)               # finished/stopped but nobody can restart it yet
        worst = max(worst, t_h - tf)                       # longest a failure can go unseen
    waste_frac = w_sum
    g = p * waste_frac * m
    h = p * waste_frac * T
    # share of the print that runs while nobody is around
    steps = 1000
    un_share = sum(unattended(start + (k + 0.5) / steps * T) for k in range(steps)) / steps
    first_watched = not any(unattended(start + (k + 0.5) / 50 * e) for k in range(50))
    return {"end": end, "waste_frac": waste_frac, "g": g, "h": h, "cost": g / 1000 * fp + h * mc,
            "delay_h": d_sum, "p_unattended": un_sum, "blocked_h": blk_sum, "un_share": un_share,
            "first_watched": first_watched, "worst_h": worst, "end_unattended": unattended(end)}


def plan_unattended_print(print_time_h: float = None, filament_g: float = None, start_time: str = None,
                          away_from: str = None, back_at: str = None, detection: str = "none",
                          failure_probability: float = None, early_failure_share: float = None,
                          early_window_h: float = None, detection_coverage: float = None,
                          detection_delay_min: float = None, check_interval_min: float = None,
                          filament_price_per_kg: float = None, machine_cost_per_h: float = None,
                          earliest_start: str = None, latest_start: str = None) -> dict:
    """Exposure of one print that runs partly while nobody is watching (overnight, or a lab left empty).

    The person is away from `away_from` to `back_at` every day (the window may cross midnight). A failure while
    they are present is noticed at their next look (on average half of check_interval_min); while they are away
    it is noticed when they come back. With detection='auto_stop' (camera detection that pauses the printer),
    a share `detection_coverage` of failures stops `detection_delay_min` after it happens. A failed job keeps
    extruding until it is stopped or the G-code ends. If earliest_start and latest_start are given, every
    start time between them (15-minute steps) is evaluated and the one with the lowest expected waste cost
    among those whose first layers are watched is recommended.
    """
    used = []
    T = _check_range("print_time_h", print_time_h, 0.05, 200, "h")
    m = _check_range("filament_g", filament_g, 0.1, 10000, "g")
    if start_time is None or away_from is None or back_at is None:
        raise ToolInputError("start_time, away_from and back_at are required ('HH:MM', 24-hour clock).")
    s = _clock("start_time", start_time)
    a = _clock("away_from", away_from)
    b = _clock("back_at", back_at)
    win_len = (b - a) % 24
    if win_len == 0:
        raise ToolInputError("away_from and back_at are the same time; give the period when nobody is watching.")
    det = str(detection or "none").lower()
    if det not in ("none", "auto_stop"):
        raise ToolInputError("detection must be 'none' (you find failures yourself) or 'auto_stop' "
                             "(the printer's camera detection pauses the job).")
    p = _defaults({"failure_probability": failure_probability, "early_failure_share": early_failure_share,
                   "early_window_h": early_window_h, "detection_coverage": detection_coverage,
                   "detection_delay_min": detection_delay_min, "check_interval_min": check_interval_min,
                   "filament_price_per_kg": filament_price_per_kg, "machine_cost_per_h": machine_cost_per_h},
                  {"failure_probability": 0.15, "early_failure_share": 0.5, "early_window_h": 0.5,
                   "detection_coverage": 0.6, "detection_delay_min": 10, "check_interval_min": 60,
                   "filament_price_per_kg": 23, "machine_cost_per_h": 0.5}, used)
    if det == "none":
        used[:] = [u for u in used if not u.startswith(("detection_coverage", "detection_delay_min"))]
    pf = _check_range("failure_probability", p["failure_probability"], 0, 0.9)
    es = _check_range("early_failure_share", p["early_failure_share"], 0, 1)
    eh = _check_range("early_window_h", p["early_window_h"], 0.05, 5, "h")
    cov = _check_range("detection_coverage", p["detection_coverage"], 0, 1)
    dly = _check_range("detection_delay_min", p["detection_delay_min"], 0, 600, "min") / 60
    chk = _check_range("check_interval_min", p["check_interval_min"], 1, 480, "min") / 60
    fp = _check_range("filament_price_per_kg", p["filament_price_per_kg"], 0, 500, "$/kg")
    mc = _check_range("machine_cost_per_h", p["machine_cost_per_h"], 0, 200, "$/h")

    args = (T, m, a, win_len, pf, es, eh, det, cov, dly, chk, fp, mc)
    r = _exposure(s, *args)

    best = None
    if earliest_start is not None or latest_start is not None:
        if earliest_start is None or latest_start is None:
            raise ToolInputError("Give both earliest_start and latest_start to search for a start time.")
        e0 = _clock("earliest_start", earliest_start)
        span = (_clock("latest_start", latest_start) - e0) % 24
        cands = []
        for k in range(int(span * 4) + 1):
            st = e0 + k * 0.25
            rr = _exposure(st, *args, n=400)
            cands.append((not rr["first_watched"], round(rr["cost"], 4), k, st, rr))
        cands.sort()
        _, _, _, best_s, best_r = cands[0]
        best = {"start": best_s, **best_r}

    checks = [
        {"name": "First layers watched", "status": "OK" if r["first_watched"] else "CAUTION",
         "detail": (f"The first {min(eh, T):g} h (where {es:.0%} of failures are assumed to happen) runs while you "
                    f"are present." if r["first_watched"] else
                    f"The first {min(eh, T):g} h runs while nobody is watching; most adhesion failures start there.")},
        {"name": "Unseen failure time", "status": "CAUTION" if r["delay_h"] > 2 else "OK",
         "detail": f"If it fails, on average {r['delay_h']:.1f} h pass before anyone knows (CAUTION above 2 h)."},
    ]
    if det == "none" and r["un_share"] > 0.5:
        checks.append({"name": "No automatic stop", "status": "CAUTION",
                       "detail": f"{r['un_share']:.0%} of the print runs unattended with nothing that can stop a failure."})

    results = {
        "finish_time": {"label": "Finish time (clock)", "value": _fmt(r["end"]), "unit": "HH:MM"},
        "unattended_share_pct": _res("Share of the print that runs unattended", 100 * r["un_share"], "%", 0),
        "unattended_hours": _res("Unattended printing hours", r["un_share"] * T, "h"),
        "p_failure_while_unattended_pct": _res("If it fails: chance the failure happens while nobody is there",
                                               100 * r["p_unattended"], "%", 0),
        "mean_hours_until_known": _res("If it fails: mean time until someone knows", r["delay_h"], "h"),
        "mean_idle_hours_after_stop": _res("If it fails: mean idle printer time before it can be restarted",
                                           r["blocked_h"], "h"),
        "worst_case_hours_unseen": _res("Worst case: longest a failure can go unnoticed", r["worst_h"], "h"),
        "expected_wasted_filament_g": _res("Expected wasted filament for this attempt", r["g"], "g", 1),
        "expected_wasted_hours": _res("Expected wasted printer hours for this attempt", r["h"], "h"),
        "expected_waste_cost_usd": _res("Expected waste cost for this attempt", r["cost"], "$"),
    }
    if best is not None:
        results.update({
            "recommended_start": {"label": "Recommended start time in your window", "value": _fmt(best["start"]),
                                  "unit": "HH:MM"},
            "recommended_finish": {"label": "Finish time with the recommended start", "value": _fmt(best["end"]),
                                   "unit": "HH:MM"},
            "recommended_expected_waste_cost_usd": _res("Expected waste cost with the recommended start",
                                                        best["cost"], "$"),
            "recommended_mean_hours_until_known": _res("Mean time until a failure is known, recommended start",
                                                       best["delay_h"], "h"),
        })
    warnings = []
    if r["end_unattended"]:
        warnings.append("The job ends while you are away: a finished part waits on the bed and the printer stays idle.")
    if best is not None and not best["first_watched"]:
        warnings.append("No start time in your window lets you watch the first layers.")
    warnings.append("Hardware damage (a jammed or cracked nozzle) and fire risk are not modelled; follow your "
                    "space's rules on unattended printing.")

    summary = (f"A {T:g} h job started at {_fmt(s)} finishes at {_fmt(r['end'])}; {r['un_share']:.0%} of it runs "
               f"unattended. If it fails ({pf:.0%} chance), on average {r['delay_h']:.1f} h pass before anyone knows; "
               f"expected waste for this attempt is {r['g']:.1f} g and {r['h']:.2f} printer-hours "
               f"(${r['cost']:.2f}).")
    if best is not None:
        summary += f" Best start in your window: {_fmt(best['start'])} (expected waste ${best['cost']:.2f})."

    return {
        "tool": "plan_unattended_print",
        "verdict": _worst([c["status"] for c in checks]),
        "decision": (f"start at {_fmt(best['start'])}" if best is not None else
                     ("watch the first layers before leaving" if not r["first_watched"] else "start as planned")),
        "summary": summary,
        "results": results,
        "checks": checks,
        "inputs": {"print_time_h": T, "filament_g": m, "start_time": start_time, "away_from": away_from,
                   "back_at": back_at, "detection": det, "failure_probability": pf, "early_failure_share": es,
                   "early_window_h": eh, "detection_coverage": cov if det == "auto_stop" else None,
                   "detection_delay_min": dly * 60 if det == "auto_stop" else None, "check_interval_min": chk * 60,
                   "filament_price_per_kg": fp, "machine_cost_per_h": mc,
                   "earliest_start": earliest_start, "latest_start": latest_start},
        "defaults_used": used,
        "assumptions": [
            "One attempt fails with probability failure_probability; early_failure_share of failures happen in the "
            "first early_window_h (bed adhesion), the rest uniformly over the job. Both are assumptions, not data.",
            "While present you notice a failure at your next look (half of check_interval_min on average); while away, "
            "when you come back. The same away window repeats every day.",
            "A failed job keeps extruding (spaghetti) until it is stopped or the G-code ends; the whole attempt is "
            "restarted, never resumed.",
            "Auto-stop catches detection_coverage of failures detection_delay_min after they start; the rest behave as "
            "with no detection.",
            "Waste cost = filament + machine time; staff time, energy for re-heating and hardware damage are not counted.",
        ],
        "warnings": warnings,
        "human_checks": [
            "Watch the first layer yourself (or check the camera) before leaving, whatever the tool says.",
            "Check that unattended printing is allowed in your space and that the printer has thermal-runaway protection.",
            "Replace the default failure_probability with your own printer's record if you have one.",
        ],
    }


# ----------------------------------------------------------------------------------------------------
# Tool 6: is a deviated (or repaired) part still usable?
# ----------------------------------------------------------------------------------------------------
ROLES = ("cosmetic", "fit", "load_bearing")
COSMETIC_DEFAULT_TOL_MM = 1.0


def check_deviated_part(features: list = None, beam: dict = None, repaired_in_load_path: bool = False,
                        reprint_time_h: float = None, reprint_filament_g: float = None,
                        filament_price_per_kg: float = None, machine_cost_per_h: float = None) -> dict:
    """Decide whether a part that came out different from its design can still be used.

    features: 1–30 items {name, role, nominal_mm, measured_mm, tolerance_mm}. role is 'cosmetic' (outer shells,
    walls, looks), 'fit' (holes, slots, mating faces) or 'load_bearing'. tolerance_mm is the allowed |measured −
    nominal|; it is required for fit and load_bearing features because only the designer knows it, and defaults to
    1.0 mm for cosmetic ones. beam (optional): the load-bearing arm as {material, length_mm, width_mm,
    nominal_thickness_mm, measured_thickness_mm, load_N or load_kg, orientation, target_safety_factor}; the
    cantilever check is run for the design and the as-printed thickness. Decision: KEEP, TEST_BEFORE_USE or REPRINT.
    """
    used = []
    if not features:
        raise ToolInputError("Give at least one feature: {name, role, nominal_mm, measured_mm, tolerance_mm}.")
    if not isinstance(features, list) or len(features) > 30:
        raise ToolInputError("features must be a list of 1–30 items.")

    rows, statuses = [], []
    for i, f in enumerate(features, 1):
        if not isinstance(f, dict):
            f = dict(f)
        name = str(f.get("name") or f"feature {i}")
        role = str(f.get("role") or "").lower()
        if role not in ROLES:
            raise ToolInputError(f"{name}: role must be one of {', '.join(ROLES)} (got {f.get('role')!r}).")
        nom = _check_range(f"{name}: nominal_mm", f.get("nominal_mm"), 0.1, 500, "mm")
        meas = _check_range(f"{name}: measured_mm", f.get("measured_mm"), 0.0, 500, "mm")
        tol = f.get("tolerance_mm")
        if tol is None:
            if role != "cosmetic":
                raise ToolInputError(f"{name}: tolerance_mm is required for a {role} feature. Only the designer knows "
                                     f"how much error it can take; give the allowed deviation in mm.")
            tol = COSMETIC_DEFAULT_TOL_MM
            used.append(f"{name}: tolerance_mm = {COSMETIC_DEFAULT_TOL_MM} (cosmetic default)")
        tol = _check_range(f"{name}: tolerance_mm", tol, 0.01, 10, "mm")
        dev = meas - nom
        use = abs(dev) / tol
        if use <= 1:
            st = "OK"
        else:
            st = "CAUTION" if role == "cosmetic" else "FAIL"
        statuses.append(st)
        rows.append({"name": name, "role": role, "nominal_mm": nom, "measured_mm": meas, "deviation_mm": _r(dev, 3),
                     "tolerance_mm": tol, "tolerance_used_pct": _r(100 * use, 0), "status": st})

    results, checks, warnings = {}, [], []
    n_fail = sum(r["status"] == "FAIL" for r in rows)
    n_cos = sum(r["status"] == "CAUTION" for r in rows)
    checks.append({"name": "Dimensions within designer tolerances",
                   "status": "FAIL" if n_fail else ("CAUTION" if n_cos else "OK"),
                   "detail": (f"{n_fail} fit/load-bearing feature(s) out of tolerance; " if n_fail else "") +
                             (f"{n_cos} cosmetic feature(s) beyond {COSMETIC_DEFAULT_TOL_MM:g} mm or their tolerance."
                              if n_cos else ("all features within tolerance." if not n_fail else ""))})

    has_lb = any(r["role"] == "load_bearing" for r in rows)
    sf_status = None
    if beam:
        b = dict(beam)
        t0 = _check_range("beam.nominal_thickness_mm", b.get("nominal_thickness_mm"), 0.8, 100, "mm")
        t1 = _check_range("beam.measured_thickness_mm", b.get("measured_thickness_mm"), 0.8, 100, "mm")
        common = {k: b.get(k) for k in ("material", "length_mm", "width_mm", "load_N", "load_kg", "orientation",
                                        "target_safety_factor", "interlayer_factor")}
        common["material"] = common["material"] or "PLA"
        nominal = check_cantilever(thickness_mm=t0, **common)
        actual = check_cantilever(thickness_mm=t1, **common)
        sf0 = nominal["results"]["safety_factor"]["value"]
        sf1 = actual["results"]["safety_factor"]["value"]
        target = actual["inputs"]["target_safety_factor"]
        used += [f"beam: {d}" for d in actual["defaults_used"]]
        warnings += [f"beam: {w}" for w in actual["warnings"]]
        results.update({
            "beam_safety_factor_design": _res("Arm safety factor, design thickness", sf0, "-"),
            "beam_safety_factor_as_printed": _res("Arm safety factor, as-printed thickness", sf1, "-"),
            "beam_strength_retained_pct": _res("Bending strength retained (∝ thickness²)", 100 * (t1 / t0) ** 2, "%", 0),
            "beam_breaking_load_as_printed_N": _res("Estimated breaking load, as printed",
                                                     actual["results"]["breaking_load_N"]["value"], "N", 1),
            "beam_tip_deflection_as_printed_mm": _res("Tip deflection, as printed",
                                                      actual["results"]["tip_deflection_mm"]["value"], "mm"),
        })
        sf_status = "FAIL" if sf1 < 1 else ("CAUTION" if sf1 < target else "OK")
        checks.append({"name": "Load-bearing arm, as printed", "status": sf_status,
                       "detail": f"Safety factor {sf1:.2f} as printed vs {sf0:.2f} as designed (target {target:g})."})
    elif has_lb:
        warnings.append("A load-bearing feature was listed without `beam`; its strength was not checked, only its size.")

    if repaired_in_load_path:
        checks.append({"name": "Repair in the load path", "status": "CAUTION",
                       "detail": "Layers were changed or re-started where load passes; the bond there is unknown, so "
                                 "the calculated safety factor is not enough on its own."})

    # Decision
    if n_fail or sf_status == "FAIL":
        decision = "REPRINT"
    elif (has_lb and (sf_status in (None, "CAUTION"))) or repaired_in_load_path:
        decision = "TEST_BEFORE_USE"
    else:
        decision = "KEEP"

    if reprint_time_h is not None or reprint_filament_g is not None:
        p = _defaults({"filament_price_per_kg": filament_price_per_kg, "machine_cost_per_h": machine_cost_per_h},
                      {"filament_price_per_kg": 23, "machine_cost_per_h": 0.5}, used)
        rt = _check_range("reprint_time_h", reprint_time_h, 0.05, 500, "h")
        rg = _check_range("reprint_filament_g", reprint_filament_g, 0.1, 10000, "g")
        fp = _check_range("filament_price_per_kg", p["filament_price_per_kg"], 0, 500, "$/kg")
        mc = _check_range("machine_cost_per_h", p["machine_cost_per_h"], 0, 200, "$/h")
        results["reprint_cost_usd"] = _res("Cost of a reprint (filament + machine time)", rg / 1000 * fp + rt * mc, "$")
        results["reprint_time_h"] = _res("Printer time for a reprint", rt, "h")

    results["features_checked"] = _res("Features checked", len(rows), "-", 0)
    results["features_out_of_tolerance"] = _res("Fit/load-bearing features out of tolerance", n_fail, "-", 0)
    worst = max(rows, key=lambda r: r["tolerance_used_pct"] if r["role"] != "cosmetic" else -1)
    if worst["role"] != "cosmetic":
        results["worst_functional_tolerance_used_pct"] = _res(f"Tolerance used by the tightest functional feature "
                                                              f"({worst['name']})", worst["tolerance_used_pct"], "%", 0)

    reason = {"REPRINT": "a fit or load-bearing feature is out of tolerance or the arm would break",
              "TEST_BEFORE_USE": ("layers were changed in the load path, so a calculation alone cannot vouch for "
                                  "its strength" if repaired_in_load_path else
                                  "dimensions pass but the load-bearing strength is unchecked or below target"),
              "KEEP": "every feature is within the designer's tolerance" +
                      (" and the arm keeps its target safety factor" if sf_status == "OK" else "")}[decision]
    return {
        "tool": "check_deviated_part",
        "verdict": _worst([c["status"] for c in checks]),
        "decision": decision,
        "summary": f"Decision: {decision}, because {reason}. "
                   f"{len(rows)} feature(s) checked, {n_fail} functional feature(s) out of tolerance"
                   + (f"; arm safety factor {results['beam_safety_factor_as_printed']['value']:.2f} as printed"
                      if beam else "") + ".",
        "results": results,
        "features": rows,
        "checks": checks,
        "inputs": {"features": rows and [{k: r[k] for k in ("name", "role", "nominal_mm", "measured_mm",
                                                              "tolerance_mm")} for r in rows],
                   "beam": beam, "repaired_in_load_path": bool(repaired_in_load_path),
                   "reprint_time_h": reprint_time_h, "reprint_filament_g": reprint_filament_g},
        "defaults_used": used,
        "assumptions": [
            "A feature passes when |measured − nominal| ≤ tolerance_mm; tolerances come from the designer.",
            f"Cosmetic features (shells, outer walls) default to ±{COSMETIC_DEFAULT_TOL_MM:g} mm and can only give a CAUTION.",
            "The arm check reuses check_cantilever with the measured thickness; it assumes a solid section and the same "
            "material and layer bonding as the design, which a repaired region may not have.",
            "The tool judges geometry and calculated strength only, not surface finish, porosity or material behaviour.",
        ],
        "warnings": warnings,
        "human_checks": [
            "Measure the features yourself (calipers or a test fit); the tool trusts the numbers it is given.",
            "For TEST_BEFORE_USE, proof-load the part to the target load (or a sacrificial copy) before relying on it.",
            "Confirm the tolerances with whoever designed the part.",
        ],
    }
