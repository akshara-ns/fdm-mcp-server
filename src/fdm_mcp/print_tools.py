"""Analytical tools for planning FDM (material-extrusion) 3D prints.

Four deterministic tools, each a plain function that takes numbers with units and returns a structured record:

  estimate_print_job      material, filament length, print time, energy and cost of one print
  check_cantilever        will a printed bracket/hook hold a load? (bending, with layer orientation)
  check_container         can a printed cup hold a liquid? (pressure, wall, heat)
  compare_failure_costs   expected waste per good part: unmonitored vs detect-and-stop vs detect-and-adapt

Every record has the same shape, so the agent and the UI can treat the tools alike:
  {"tool", "verdict", "summary", "results": {key: {"label", "value", "unit"}}, "checks": [...],
   "inputs": {...}, "defaults_used": [...], "assumptions": [...], "warnings": [...]}

Out-of-range input raises ToolInputError with a message written for the user.
"""
from __future__ import annotations

import math

G = 9.81                       # m/s^2
FILAMENT_DIAMETER_MM = 1.75

# Values for printed specimens from manufacturer technical data sheets (TDS); full citations in the notebook (§3.5):
#   PLA, PETG: Bambu Lab PLA Basic / PETG Basic TDS v2.0 (ISO 527 tensile, 100 % infill, annealed specimens;
#              heat deflection temperature ISO 75 at 0.45 MPa).
#   ABS:       Stratasys ABS-M30 data sheet (ASTM D638 yield, XZ "on edge" and ZX "upright"; HDT at 66 psi = 0.45 MPa).
# "z_strength_tds_MPa" is the data sheet's across-layer value. Those coupons are solid and annealed, the best case;
# as-printed parts are weaker across layers, so the checks use xy_strength x INTERLAYER_FACTOR instead (see below).
MATERIALS = {
    "PLA":  {"density": 1.24, "E_GPa": 2.68, "xy_strength_MPa": 39.0, "z_strength_tds_MPa": 35.0,
             "hdt_C": 57.0, "price_per_kg": 23.0},
    "PETG": {"density": 1.25, "E_GPa": 1.46, "xy_strength_MPa": 48.0, "z_strength_tds_MPa": 39.0,
             "hdt_C": 69.0, "price_per_kg": 23.0},
    "ABS":  {"density": 1.05, "E_GPa": 2.40, "xy_strength_MPa": 30.8, "z_strength_tds_MPa": 27.5,
             "hdt_C": 103.8, "price_per_kg": 23.0},
}

# Design strength across layers as a share of the in-plane strength. Ahn et al. (2002) measured FDM parts failing
# along inter-road bonds at 10-73 % of the moulded strength; 0.5 is a conservative design choice for as-printed
# parts (thin walls, sparse infill, imperfect bonding), not a measured constant. It is an input of the tools.
INTERLAYER_FACTOR = 0.5


class ToolInputError(ValueError):
    """Raised for input outside a tool's valid range; the message is shown to the user."""


# ----------------------------------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------------------------------
def _material(name):
    key = str(name or "PLA").strip().upper()
    if key not in MATERIALS:
        raise ToolInputError(f"Material '{name}' is not supported. Choose one of: {', '.join(MATERIALS)}.")
    return key, MATERIALS[key]


def _check_range(name, value, lo, hi, unit=""):
    if value is None:
        raise ToolInputError(f"{name} is required.")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ToolInputError(f"{name} must be a number (got {value!r}).")
    if not (lo <= value <= hi) or math.isnan(value):
        raise ToolInputError(f"{name} = {value:g} {unit} is outside the valid range {lo:g}–{hi:g} {unit}.".replace("  ", " "))
    return value


def _r(x, nd=2):
    return round(float(x), nd)


def _res(label, value, unit, nd=2):
    return {"label": label, "value": _r(value, nd), "unit": unit}


def _worst(statuses):
    for s in ("FAIL", "CAUTION", "OK"):
        if s in statuses:
            return s
    return "OK"


def _defaults(given: dict, defaults: dict, used: list):
    """Fill None arguments from `defaults`, recording each default that was applied."""
    out = {}
    for k, d in defaults.items():
        v = given.get(k)
        if v is None:
            out[k] = d
            used.append(f"{k} = {d}")
        else:
            out[k] = v
    return out


# ----------------------------------------------------------------------------------------------------
# Tool 1: material, time, energy and cost of a print
# ----------------------------------------------------------------------------------------------------
def estimate_print_job(material: str = "PLA", shape: str = "custom",
                       size_x_mm: float = None, size_y_mm: float = None, size_z_mm: float = None,
                       volume_cm3: float = None, surface_area_cm2: float = None,
                       infill_percent: float = None, wall_loops: int = None, line_width_mm: float = None,
                       layer_height_mm: float = None, print_speed_mm_s: float = None,
                       max_flow_mm3_s: float = None, motion_efficiency: float = None,
                       printer_power_W: float = None, filament_price_per_kg: float = None,
                       electricity_price_per_kWh: float = None) -> dict:
    """Estimate filament used, print time, energy and cost for one FDM print.

    Geometry: shape='box' (size_x/y/z = outer dimensions, mm), shape='cylinder' (size_x = diameter,
    size_z = height, mm), or shape='custom' (volume_cm3 and surface_area_cm2 from the CAD/slicer).
    The part is modelled as a solid shell (wall_loops x line_width thick, over the whole surface) filled
    with sparse infill. Print time = material volume / effective volumetric flow.
    """
    used = []
    mat, props = _material(material)
    p = _defaults(locals(), {"infill_percent": 15, "wall_loops": 2, "line_width_mm": 0.42,
                             "layer_height_mm": 0.2, "print_speed_mm_s": 150, "max_flow_mm3_s": 21,
                             "motion_efficiency": 0.6, "printer_power_W": 110,
                             "filament_price_per_kg": props["price_per_kg"],
                             "electricity_price_per_kWh": 0.17}, used)
    shape = str(shape or "custom").lower()
    if shape == "box":
        x = _check_range("size_x_mm", size_x_mm, 1, 350, "mm")
        y = _check_range("size_y_mm", size_y_mm, 1, 350, "mm")
        z = _check_range("size_z_mm", size_z_mm, 0.2, 350, "mm")
        V = x * y * z / 1000.0
        A = 2 * (x * y + y * z + x * z) / 100.0
    elif shape == "cylinder":
        d = _check_range("size_x_mm (diameter)", size_x_mm, 1, 350, "mm")
        z = _check_range("size_z_mm (height)", size_z_mm, 0.2, 350, "mm")
        V = math.pi * d ** 2 / 4 * z / 1000.0
        A = (math.pi * d * z + 2 * math.pi * d ** 2 / 4) / 100.0
    elif shape == "custom":
        V = _check_range("volume_cm3", volume_cm3, 0.05, 20000, "cm³")
        A = _check_range("surface_area_cm2", surface_area_cm2, 0.5, 20000, "cm²")
        a_min = (36 * math.pi * V ** 2) ** (1 / 3)       # a sphere has the least area for a given volume
        if A < a_min * 0.999:
            raise ToolInputError(f"A part of {V:g} cm³ needs at least {a_min:.1f} cm² of surface (a sphere); "
                                 f"{A:g} cm² is impossible. Check the units (cm² vs mm²).")
    else:
        raise ToolInputError("shape must be 'box', 'cylinder' or 'custom'.")

    infill = _check_range("infill_percent", p["infill_percent"], 0, 100, "%") / 100
    loops = int(_check_range("wall_loops", p["wall_loops"], 1, 10))
    w = _check_range("line_width_mm", p["line_width_mm"], 0.2, 1.2, "mm")
    h = _check_range("layer_height_mm", p["layer_height_mm"], 0.04, 0.6, "mm")
    if h > 0.8 * w:
        raise ToolInputError(f"layer_height_mm {h:g} is more than 80 % of line_width_mm {w:g}; the layers would not bond.")
    v = _check_range("print_speed_mm_s", p["print_speed_mm_s"], 5, 600, "mm/s")
    qmax = _check_range("max_flow_mm3_s", p["max_flow_mm3_s"], 1, 60, "mm³/s")
    eta = _check_range("motion_efficiency", p["motion_efficiency"], 0.1, 1.0)
    P = _check_range("printer_power_W", p["printer_power_W"], 10, 2000, "W")
    fp = _check_range("filament_price_per_kg", p["filament_price_per_kg"], 0, 500, "$/kg")
    ep = _check_range("electricity_price_per_kWh", p["electricity_price_per_kWh"], 0, 2, "$/kWh")

    shell_mm = loops * w
    V_shell = min(V, A * shell_mm / 10.0)                 # cm² x mm / 10 = cm³
    V_mat = V_shell + infill * (V - V_shell)
    mass_g = V_mat * props["density"]
    fil_area_cm2 = math.pi * (FILAMENT_DIAMETER_MM / 10) ** 2 / 4
    fil_len_m = V_mat / fil_area_cm2 / 100
    q_nominal = w * h * v
    q_eff = min(q_nominal, qmax) * eta
    t_h = V_mat * 1000 / q_eff / 3600
    energy_kWh = P * t_h / 1000
    cost_mat = mass_g / 1000 * fp
    cost_energy = energy_kWh * ep

    checks = [{"name": "Flow limit", "status": "CAUTION" if q_nominal > qmax else "OK",
               "detail": (f"Requested flow {q_nominal:.1f} mm³/s exceeds the hot end's {qmax:g} mm³/s; the "
                          f"printer slows down to the limit." if q_nominal > qmax else
                          f"Requested flow {q_nominal:.1f} mm³/s is within the {qmax:g} mm³/s limit.")}]
    warnings = []
    if V_shell >= V:
        warnings.append("The walls fill the whole part, so infill has no effect (the part prints solid).")
    if t_h > 24:
        warnings.append("Over a day of printing: one failure costs a lot, so monitoring matters (see compare_failure_costs).")

    return {
        "tool": "estimate_print_job",
        "verdict": _worst([c["status"] for c in checks]),
        "summary": (f"{mat} part of {V:.1f} cm³: about {mass_g:.0f} g of filament ({fil_len_m:.1f} m), "
                    f"{t_h:.2f} h of printing, {energy_kWh:.2f} kWh, about ${cost_mat + cost_energy:.2f} in material and energy."),
        "results": {
            "part_volume_cm3": _res("Part volume (solid)", V, "cm³"),
            "surface_area_cm2": _res("Surface area", A, "cm²"),
            "material_volume_cm3": _res("Plastic deposited (shell + infill)", V_mat, "cm³"),
            "solid_fraction_pct": _res("Plastic as a share of the solid part", 100 * V_mat / V, "%", 1),
            "filament_mass_g": _res("Filament mass", mass_g, "g", 1),
            "filament_length_m": _res(f"Filament length ({FILAMENT_DIAMETER_MM} mm)", fil_len_m, "m"),
            "effective_flow_mm3_s": _res("Effective volumetric flow", q_eff, "mm³/s"),
            "print_time_h": _res("Print time", t_h, "h"),
            "print_time_min": _res("Print time in minutes", t_h * 60, "min", 0),
            "energy_kWh": _res("Electrical energy", energy_kWh, "kWh", 3),
            "material_cost_usd": _res("Filament cost", cost_mat, "$"),
            "energy_cost_usd": _res("Electricity cost", cost_energy, "$", 3),
            "total_cost_usd": _res("Material + energy cost", cost_mat + cost_energy, "$"),
        },
        "checks": checks,
        "inputs": {"material": mat, "shape": shape, "infill_percent": infill * 100, "wall_loops": loops,
                   "line_width_mm": w, "layer_height_mm": h, "print_speed_mm_s": v, "max_flow_mm3_s": qmax,
                   "motion_efficiency": eta, "printer_power_W": P, "filament_price_per_kg": fp,
                   "electricity_price_per_kWh": ep},
        "defaults_used": used,
        "assumptions": [
            "Shell = wall_loops x line_width over the whole surface (top/bottom skins are taken as equally thick).",
            "Infill fills the rest of the volume at infill_percent; supports, brim and purge are not included.",
            "Print time = plastic volume / (min(line_width x layer_height x speed, max flow) x motion_efficiency); "
            "motion_efficiency lumps acceleration, travel and layer changes. Heat-up time is not included.",
            f"Filament diameter {FILAMENT_DIAMETER_MM} mm; average printer power is constant over the print.",
        ],
        "warnings": warnings,
    }


# ----------------------------------------------------------------------------------------------------
# Tool 2: printed cantilever (bracket, hook, clip) under an end load
# ----------------------------------------------------------------------------------------------------
def check_cantilever(material: str = "PLA", length_mm: float = None, width_mm: float = None,
                     thickness_mm: float = None, load_N: float = None, load_kg: float = None,
                     orientation: str = "flat", target_safety_factor: float = None,
                     interlayer_factor: float = None) -> dict:
    """Check a printed rectangular cantilever (shelf bracket arm, hook, clip) with a load at its free end.

    orientation='flat': the arm lies on the bed, so bending stress runs along the printed lines (strong).
    orientation='upright': the arm was printed standing up, so bending stress pulls layers apart (weak).
    thickness_mm is the depth in the direction the load bends the arm. Load as load_N or load_kg (mass hung).
    """
    used = []
    mat, props = _material(material)
    L = _check_range("length_mm", length_mm, 5, 400, "mm")
    b = _check_range("width_mm", width_mm, 1, 200, "mm")
    t = _check_range("thickness_mm", thickness_mm, 0.8, 100, "mm")
    if load_N is None and load_kg is None:
        raise ToolInputError("Give the load as load_N (newtons) or load_kg (mass hung on the end).")
    F = _check_range("load_N", load_N, 0.01, 5000, "N") if load_N is not None else \
        _check_range("load_kg", load_kg, 0.001, 500, "kg") * G
    orient = str(orientation or "flat").lower()
    if orient not in ("flat", "upright"):
        raise ToolInputError("orientation must be 'flat' (layers along the arm) or 'upright' (layers across the arm).")
    d = _defaults({"target_safety_factor": target_safety_factor, "interlayer_factor": interlayer_factor},
                  {"target_safety_factor": 2.0, "interlayer_factor": INTERLAYER_FACTOR}, used)
    sf_target = _check_range("target_safety_factor", d["target_safety_factor"], 1, 10)
    k_z = _check_range("interlayer_factor", d["interlayer_factor"], 0.1, 1.0)
    z_strength = props["xy_strength_MPa"] * k_z

    strength = props["xy_strength_MPa"] if orient == "flat" else z_strength
    E = props["E_GPa"] * 1000                              # MPa
    I = b * t ** 3 / 12                                    # mm^4
    M = F * L                                              # N mm
    sigma = M * (t / 2) / I                                # MPa = 6FL/(b t^2)
    sf = strength / sigma
    defl = F * L ** 3 / (3 * E * I)                        # mm
    F_fail = strength * b * t ** 2 / (6 * L)
    t_req = math.sqrt(6 * F * L * sf_target / (b * strength))
    other = z_strength if orient == "flat" else props["xy_strength_MPa"]
    sf_other = other / sigma

    checks = [
        {"name": "Strength", "status": "FAIL" if sf < 1 else ("CAUTION" if sf < sf_target else "OK"),
         "detail": f"Safety factor {sf:.2f} against the {strength:g} MPa {'in-plane' if orient == 'flat' else 'interlayer'} "
                   f"strength (target {sf_target:g})."},
        {"name": "Stiffness", "status": "CAUTION" if defl > 0.02 * L else "OK",
         "detail": f"Tip deflection {defl:.2f} mm is {100 * defl / L:.1f} % of the length (CAUTION above 2 %)."},
    ]
    warnings = []
    if L / t < 4:
        warnings.append("Length is less than 4x the thickness: simple beam theory underestimates shear; treat as rough.")
    if defl > 0.1 * L:
        warnings.append("Deflection over 10 % of the length: outside small-deflection theory; the result is unreliable.")

    return {
        "tool": "check_cantilever",
        "verdict": _worst([c["status"] for c in checks]),
        "summary": (f"{mat} arm {L:g} x {b:g} x {t:g} mm printed {orient}, {F:.1f} N at the tip: bending stress "
                    f"{sigma:.2f} MPa, safety factor {sf:.2f}, tip deflection {defl:.2f} mm. It breaks at about {F_fail:.0f} N."),
        "results": {
            "load_N": _res("End load", F, "N"),
            "bending_moment_Nmm": _res("Bending moment at the root", M, "N·mm", 1),
            "max_bending_stress_MPa": _res("Maximum bending stress (root surface)", sigma, "MPa"),
            "allowable_strength_MPa": _res(f"Strength used ({'along lines, XY' if orient == 'flat' else 'across layers, Z'})", strength, "MPa", 1),
            "safety_factor": _res("Safety factor", sf, "-"),
            "safety_factor_if_other_orientation": _res(f"Safety factor if printed {'upright' if orient == 'flat' else 'flat'}", sf_other, "-"),
            "breaking_load_N": _res("Estimated breaking load", F_fail, "N", 1),
            "breaking_load_kg": _res("Estimated breaking load as mass", F_fail / G, "kg", 2),
            "tip_deflection_mm": _res("Tip deflection", defl, "mm"),
            "thickness_for_target_mm": _res(f"Thickness needed for safety factor {sf_target:g}", t_req, "mm"),
        },
        "checks": checks,
        "inputs": {"material": mat, "length_mm": L, "width_mm": b, "thickness_mm": t, "load_N": _r(F, 3),
                   "orientation": orient, "target_safety_factor": sf_target, "interlayer_factor": k_z},
        "defaults_used": used,
        "assumptions": [
            "Euler-Bernoulli cantilever, fixed root, point load at the tip: sigma = 6FL/(b t^2), deflection = FL^3/(3EI).",
            "Solid printed section (enough walls/100 % infill); sparse infill makes the part weaker than this.",
            "In-plane strength from the material data sheet; strength across layers = in-plane x interlayer_factor.",
            "Tensile strength is used as the bending limit (conservative: printed flexural strength is higher).",
            "Static load at room temperature: no creep, fatigue or impact. The fixed root is perfectly rigid.",
        ],
        "warnings": warnings,
    }


# ----------------------------------------------------------------------------------------------------
# Tool 3: printed cup or container holding a liquid
# ----------------------------------------------------------------------------------------------------
def check_container(material: str = "PLA", inner_diameter_mm: float = None, height_mm: float = None,
                    wall_thickness_mm: float = None, liquid_temp_C: float = None,
                    liquid_density_kg_m3: float = None, line_width_mm: float = None,
                    interlayer_factor: float = None) -> dict:
    """Check a printed upright cylindrical cup/container: capacity, wall stress from the liquid, wall
    thickness for watertightness, and whether the liquid is hot enough to soften the plastic."""
    used = []
    mat, props = _material(material)
    D = _check_range("inner_diameter_mm", inner_diameter_mm, 5, 300, "mm")
    H = _check_range("height_mm", height_mm, 5, 300, "mm")
    t = _check_range("wall_thickness_mm", wall_thickness_mm, 0.3, 20, "mm")
    p = _defaults({"liquid_temp_C": liquid_temp_C, "liquid_density_kg_m3": liquid_density_kg_m3,
                   "line_width_mm": line_width_mm, "interlayer_factor": interlayer_factor},
                  {"liquid_temp_C": 20, "liquid_density_kg_m3": 1000, "line_width_mm": 0.42,
                   "interlayer_factor": INTERLAYER_FACTOR}, used)
    T = _check_range("liquid_temp_C", p["liquid_temp_C"], -20, 100, "°C")
    rho = _check_range("liquid_density_kg_m3", p["liquid_density_kg_m3"], 500, 2000, "kg/m³")
    w = _check_range("line_width_mm", p["line_width_mm"], 0.2, 1.2, "mm")
    k_z = _check_range("interlayer_factor", p["interlayer_factor"], 0.1, 1.0)

    r = D / 2
    cap_mL = math.pi * r ** 2 * H / 1000
    p_Pa = rho * G * H / 1000                              # pressure at the bottom of the wall
    p_MPa = p_Pa / 1e6
    hoop = p_MPa * r / t                                   # acts around the cup, along the printed lines
    axial = p_MPa * r / (2 * t)                            # upper bound on the stress across layers
    sf_hoop = props["xy_strength_MPa"] / hoop
    sf_axial = props["xy_strength_MPa"] * k_z / axial
    loops = t / w
    soft = props["hdt_C"]

    checks = [
        {"name": "Wall stress from the liquid", "status": "OK" if min(sf_hoop, sf_axial) >= 2 else "FAIL",
         "detail": f"Hoop stress {hoop:.4f} MPa (safety factor {sf_hoop:.0f}); the liquid pressure alone is not a risk."},
        {"name": "Watertight wall", "status": "OK" if loops >= 3 else ("CAUTION" if loops >= 2 else "FAIL"),
         "detail": f"The wall is about {loops:.1f} lines of {w:g} mm. At least 3 lines are needed to seal reliably; "
                   f"1 line leaks."},
        {"name": "Heat", "status": "FAIL" if T >= soft else ("CAUTION" if T >= soft - 10 else "OK"),
         "detail": f"Liquid at {T:g} °C vs {mat} softening point {soft:g} °C."},
    ]
    warnings = []
    if t > 0.1 * r:
        warnings.append("Wall thicker than a tenth of the radius: the thin-wall formula overestimates the stress (conservative).")
    if mat == "PLA" and T >= soft:
        warnings.append("Hot drinks soften PLA: the cup can sag or distort even though the pressure stress is tiny.")

    return {
        "tool": "check_container",
        "verdict": _worst([c["status"] for c in checks]),
        "summary": (f"{mat} cup, {D:g} mm inside x {H:g} mm, {t:g} mm wall: holds {cap_mL:.0f} mL; liquid "
                    f"pressure gives only {hoop:.4f} MPa hoop stress; heat check: {checks[2]['status']}, "
                    f"wall seal check: {checks[1]['status']}."),
        "results": {
            "capacity_mL": _res("Capacity when full", cap_mL, "mL", 0),
            "bottom_pressure_Pa": _res("Liquid pressure at the bottom", p_Pa, "Pa", 0),
            "hoop_stress_MPa": _res("Hoop stress in the wall (along lines)", hoop, "MPa", 4),
            "axial_stress_MPa": _res("Stress across layers (upper bound)", axial, "MPa", 4),
            "safety_factor_hoop": _res("Safety factor, hoop", sf_hoop, "-", 0),
            "safety_factor_layers": _res("Safety factor, across layers", sf_axial, "-", 0),
            "wall_lines": _res("Wall thickness in printed lines", loops, "lines", 1),
            "wall_for_3_lines_mm": _res("Wall thickness for 3 lines (watertight)", 3 * w, "mm", 2),
            "softening_point_C": _res(f"{mat} softening point (HDT)", soft, "°C", 0),
            "temperature_margin_C": _res("Margin below the softening point", soft - T, "°C", 0),
        },
        "checks": checks,
        "inputs": {"material": mat, "inner_diameter_mm": D, "height_mm": H, "wall_thickness_mm": t,
                   "liquid_temp_C": T, "liquid_density_kg_m3": rho, "line_width_mm": w, "interlayer_factor": k_z},
        "defaults_used": used,
        "assumptions": [
            "Thin-walled open cylinder, full to the brim; pressure = rho g H at the bottom, hoop stress = p r / t.",
            "The floor is supported by the table, so it carries no bending stress.",
            "Watertightness is judged by wall lines (3 or more), a rule of thumb; real sealing also depends on flow and temperature.",
            "Softening point is the heat deflection temperature; prolonged contact near it causes creep. Food safety is not assessed.",
        ],
        "warnings": warnings,
    }


# ----------------------------------------------------------------------------------------------------
# Tool 4: what failures cost, with and without monitoring
# ----------------------------------------------------------------------------------------------------
def compare_failure_costs(print_time_h: float = None, filament_g: float = None, failure_probability: float = None,
                          mean_failure_point: float = None, detection_delay_min: float = None,
                          recoverable_share: float = None, filament_price_per_kg: float = None,
                          machine_cost_per_h: float = None, jobs: int = None) -> dict:
    """Expected waste per GOOD part for three strategies when each attempt fails with probability p:
    A) unmonitored: a failure is found at the end, after the full time and filament are spent;
    B) detect and stop: a camera stops the print shortly after the failure; restart from scratch;
    C) detect and adapt: as B, but a share of detected failures is repaired in place and finishes as a usable part.
    Attempts repeat until one succeeds (geometric distribution: expected failed attempts = p/(1-p))."""
    used = []
    T = _check_range("print_time_h", print_time_h, 0.05, 500, "h")
    m = _check_range("filament_g", filament_g, 0.1, 10000, "g")
    p = _defaults({"failure_probability": failure_probability, "mean_failure_point": mean_failure_point,
                   "detection_delay_min": detection_delay_min, "recoverable_share": recoverable_share,
                   "filament_price_per_kg": filament_price_per_kg, "machine_cost_per_h": machine_cost_per_h,
                   "jobs": jobs},
                  {"failure_probability": 0.15, "mean_failure_point": 0.3, "detection_delay_min": 10,
                   "recoverable_share": 0.4, "filament_price_per_kg": 23, "machine_cost_per_h": 0.5,
                   "jobs": 100}, used)
    pf = _check_range("failure_probability", p["failure_probability"], 0, 0.9)
    phi = _check_range("mean_failure_point", p["mean_failure_point"], 0, 1)
    delay_h = _check_range("detection_delay_min", p["detection_delay_min"], 0, 600, "min") / 60
    rec = _check_range("recoverable_share", p["recoverable_share"], 0, 1)
    fp = _check_range("filament_price_per_kg", p["filament_price_per_kg"], 0, 500, "$/kg")
    mc = _check_range("machine_cost_per_h", p["machine_cost_per_h"], 0, 200, "$/h")
    n_jobs = int(_check_range("jobs", p["jobs"], 1, 1e6))

    # Waste in one failed attempt: time (h) and filament (g).
    detected_frac = min(1.0, phi + delay_h / T)            # share of the job done before the printer stops
    waste_A = (T, m)                                       # spaghetti keeps extruding to the end
    waste_B = (T * detected_frac, m * detected_frac)       # extrusion stops shortly after the failure

    def per_good(p_fail, waste):
        k = p_fail / (1 - p_fail)                          # expected failed attempts per good part
        return k, k * waste[0], k * waste[1]

    kA, hA, gA = per_good(pf, waste_A)
    kB, hB, gB = per_good(pf, waste_B)
    pC = pf * (1 - rec)                                    # failures that still end in a restart
    kC, hC, gC = per_good(pC, waste_B)

    def cost(h, g):
        return g / 1000 * fp + h * mc

    rows = {"A_unmonitored": (kA, hA, gA), "B_detect_and_stop": (kB, hB, gB), "C_detect_and_adapt": (kC, hC, gC)}
    results = {}
    for key, (k, h, g) in rows.items():
        results[f"{key}_failed_attempts_per_good_part"] = _res(f"{key}: failed attempts per good part", k, "-", 3)
        results[f"{key}_wasted_hours_per_good_part"] = _res(f"{key}: wasted printer hours per good part", h, "h", 3)
        results[f"{key}_wasted_filament_g_per_good_part"] = _res(f"{key}: wasted filament per good part", g, "g", 1)
        results[f"{key}_waste_cost_per_good_part_usd"] = _res(f"{key}: waste cost per good part", cost(h, g), "$", 3)
    for key, (k, h, g) in (("B_vs_A", rows["B_detect_and_stop"]), ("C_vs_A", rows["C_detect_and_adapt"])):
        results[f"saved_{key}_filament_g_per_good_part"] = _res(f"Filament saved per good part, {key.replace('_', ' ')}", gA - g, "g", 1)
        results[f"saved_{key}_hours_per_good_part"] = _res(f"Printer hours saved per good part, {key.replace('_', ' ')}", hA - h, "h", 3)
        results[f"saved_{key}_cost_per_good_part_usd"] = _res(f"Waste cost saved per good part, {key.replace('_', ' ')}", cost(hA, gA) - cost(h, g), "$", 2)
    results["hours_saved_B_vs_A_per_jobs_h"] = _res(f"Printer hours saved by B vs A over {n_jobs} good parts", (hA - hB) * n_jobs, "h", 1)
    results["filament_saved_B_vs_A_per_jobs_g"] = _res(f"Filament saved by B vs A over {n_jobs} good parts", (gA - gB) * n_jobs, "g", 0)
    results["filament_saved_C_vs_A_per_jobs_g"] = _res(f"Filament saved by C vs A over {n_jobs} good parts", (gA - gC) * n_jobs, "g", 0)
    results["hours_saved_C_vs_A_per_jobs_h"] = _res(f"Printer hours saved by C vs A over {n_jobs} good parts", (hA - hC) * n_jobs, "h", 1)

    warnings = []
    if phi + delay_h / T >= 1:
        warnings.append("The detection delay is as long as the rest of the print, so detection saves nothing here.")
    if pf >= 0.5:
        warnings.append("A failure rate of 50 % or more points to a process problem worth fixing before monitoring.")

    return {
        "tool": "compare_failure_costs",
        "verdict": "OK",
        "summary": (f"With p = {pf:g} per attempt, each good part wastes on average {gA:.1f} g and {hA:.2f} h unmonitored, "
                    f"{gB:.1f} g and {hB:.2f} h with detect-and-stop, and {gC:.1f} g and {hC:.2f} h with detect-and-adapt."),
        "results": results,
        "checks": [],
        "inputs": {"print_time_h": T, "filament_g": m, "failure_probability": pf, "mean_failure_point": phi,
                   "detection_delay_min": delay_h * 60, "recoverable_share": rec, "filament_price_per_kg": fp,
                   "machine_cost_per_h": mc, "jobs": n_jobs},
        "defaults_used": used,
        "assumptions": [
            "Attempts are independent with the same failure probability; repeat until one succeeds (geometric).",
            "Unmonitored failures are found at the end of the job and waste its full time and filament.",
            "A detected failure stops the printer delay minutes after it happens at mean_failure_point of the job.",
            "Detect-and-adapt: recoverable_share of failures finish as a usable part with no extra waste. This share "
            "is the project's hypothesis, not a measured value.",
            "Waste cost = filament + machine time; staff time is not counted.",
        ],
        "warnings": warnings,
    }


TOOLS = {f.__name__: f for f in (estimate_print_job, check_cantilever, check_container, compare_failure_costs)}
