"""MCP server exposing six FDM print-planning tools (24-679 HW5).

HW4 tools:  estimate_print_job, check_cantilever, check_container, compare_failure_costs
New tools:  plan_unattended_print, check_deviated_part

The server only computes. It never talks to a printer, never edits G-code, and never calls an LLM; choosing a tool,
asking the user for missing measurements and explaining the result are the client's job.

Run:  fdm-mcp            (stdio, for Claude Desktop / Claude Code / MCP Inspector)
      fdm-mcp --http     (Streamable HTTP on http://127.0.0.1:8000/mcp)
"""
from __future__ import annotations

import sys
from typing import Annotated, Any, Literal, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from . import new_tools, print_tools
from .print_tools import ToolInputError

mcp = MCPServer(
    "fdm-print-tools",
    instructions=(
        "Deterministic calculators for planning desktop FDM (material-extrusion) prints. Every number in an answer "
        "must come from a tool result. Ask the user for measurements they have not given instead of guessing; tools "
        "report any defaults they applied in `defaults_used`. Pass `human_checks` on to the user."),
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


# ---------------------------------------------------------------- shared output schema
class Quantity(BaseModel):
    label: str
    value: float | str
    unit: str


class Check(BaseModel):
    name: str
    status: Literal["OK", "CAUTION", "FAIL"]
    detail: str


class ToolRecord(BaseModel):
    """Same shape for every tool, so a client can render or check any of them the same way."""
    tool: str
    verdict: Literal["OK", "CAUTION", "FAIL"] = Field(description="Worst status among the checks.")
    decision: Optional[str] = Field(None, description="Recommended action (new tools only).")
    summary: str
    results: dict[str, Quantity]
    checks: list[Check]
    features: Optional[list[dict[str, Any]]] = Field(None, description="Per-feature table (check_deviated_part).")
    inputs: dict[str, Any] = Field(description="Every input actually used, after defaults.")
    defaults_used: list[str] = Field(description="Inputs the user did not give and the default applied.")
    assumptions: list[str]
    warnings: list[str]
    human_checks: list[str] = Field(default_factory=list, description="What a person must verify before acting.")


GENERIC_HUMAN_CHECKS = {
    "estimate_print_job": ["Compare the time and filament with your slicer's preview before quoting a cost.",
                           "Supports, brim and purge are not included; add them for overhanging parts."],
    "check_cantilever": ["Proof-load one printed part before relying on it; printed strength varies by printer.",
                         "Check that the thickness you entered is in the direction the load bends the arm."],
    "check_container": ["Fill-test the cup with water over a sink before using it.",
                        "Food safety is not assessed; do not use it for food or drink without checking the material."],
    "compare_failure_costs": ["Replace the default failure probability with your printer's own record.",
                              "recoverable_share is the project's hypothesis, not a measured value."],
}


def _run(fn, **kwargs) -> ToolRecord:
    try:
        rec = fn(**kwargs)
    except ToolInputError as e:
        raise ToolError(f"Invalid input: {e}") from e
    rec.setdefault("human_checks", GENERIC_HUMAN_CHECKS.get(rec["tool"], []))
    return ToolRecord.model_validate(rec)


Material = Annotated[Literal["PLA", "PETG", "ABS"], Field(description="Filament (values from manufacturer data sheets).")]


def F(lo, hi, unit, desc, default=None):
    """Optional number with a documented unit and range."""
    return Annotated[Optional[float], Field(default, ge=lo, le=hi, description=f"{desc} [{unit}, {lo:g}–{hi:g}]")]


# ---------------------------------------------------------------- HW4 tool 1
@mcp.tool(title="Estimate a print job", annotations=READ_ONLY)
def estimate_print_job(
    material: Material = "PLA",
    shape: Annotated[Literal["box", "cylinder", "custom"], Field(
        description="box: size_x/y/z are outer sizes; cylinder: size_x = diameter, size_z = height; "
                    "custom: give volume_cm3 and surface_area_cm2 from CAD")] = "custom",
    size_x_mm: F(1, 350, "mm", "Box length or cylinder diameter") = None,
    size_y_mm: F(1, 350, "mm", "Box width") = None,
    size_z_mm: F(0.2, 350, "mm", "Height") = None,
    volume_cm3: F(0.05, 20000, "cm³", "Solid part volume (custom shape)") = None,
    surface_area_cm2: F(0.5, 20000, "cm²", "Part surface area (custom shape)") = None,
    infill_percent: F(0, 100, "%", "Sparse infill density; default 15") = None,
    wall_loops: Annotated[Optional[int], Field(None, ge=1, le=10, description="Wall lines; default 2")] = None,
    line_width_mm: F(0.2, 1.2, "mm", "Extrusion line width; default 0.42") = None,
    layer_height_mm: F(0.04, 0.6, "mm", "Layer height, at most 80 % of line width; default 0.2") = None,
    print_speed_mm_s: F(5, 600, "mm/s", "Print speed; default 150") = None,
    max_flow_mm3_s: F(1, 60, "mm³/s", "Hot-end volumetric flow limit; default 21") = None,
    motion_efficiency: F(0.1, 1, "-", "Share of time spent extruding; default 0.6") = None,
    printer_power_W: F(10, 2000, "W", "Average printer power; default 110") = None,
    filament_price_per_kg: F(0, 500, "$/kg", "Filament price; default 23") = None,
    electricity_price_per_kWh: F(0, 2, "$/kWh", "Electricity price; default 0.17") = None,
) -> ToolRecord:
    """Filament mass and length, print time, energy and cost for one FDM print (HW4 tool).
    Errors: unknown shape, missing dimensions, impossible volume/area pair, layer height > 80 % of line width."""
    return _run(print_tools.estimate_print_job, **locals())


# ---------------------------------------------------------------- HW4 tool 2
@mcp.tool(title="Check a printed cantilever", annotations=READ_ONLY)
def check_cantilever(
    length_mm: Annotated[float, Field(ge=5, le=400, description="Arm length from root to load [mm, 5–400]")],
    width_mm: Annotated[float, Field(ge=1, le=200, description="Arm width [mm, 1–200]")],
    thickness_mm: Annotated[float, Field(ge=0.8, le=100, description="Depth in the bending direction [mm, 0.8–100]")],
    material: Material = "PLA",
    load_N: F(0.01, 5000, "N", "End load as a force (give this or load_kg)") = None,
    load_kg: F(0.001, 500, "kg", "End load as a hung mass (give this or load_N)") = None,
    orientation: Annotated[Literal["flat", "upright"], Field(
        description="flat: layers run along the arm (strong); upright: load pulls layers apart (weak)")] = "flat",
    target_safety_factor: F(1, 10, "-", "Required safety factor; default 2") = None,
    interlayer_factor: F(0.1, 1, "-", "Strength across layers / in-plane strength; default 0.5") = None,
) -> ToolRecord:
    """Bending stress, safety factor, breaking load and tip deflection of a printed bracket, hook or clip
    loaded at its end (HW4 tool). Errors: no load given, values outside their ranges."""
    return _run(print_tools.check_cantilever, **locals())


# ---------------------------------------------------------------- HW4 tool 3
@mcp.tool(title="Check a printed cup or container", annotations=READ_ONLY)
def check_container(
    inner_diameter_mm: Annotated[float, Field(ge=5, le=300, description="Inside diameter [mm, 5–300]")],
    height_mm: Annotated[float, Field(ge=5, le=300, description="Liquid height [mm, 5–300]")],
    wall_thickness_mm: Annotated[float, Field(ge=0.3, le=20, description="Wall thickness [mm, 0.3–20]")],
    material: Material = "PLA",
    liquid_temp_C: F(-20, 100, "°C", "Liquid temperature; default 20") = None,
    liquid_density_kg_m3: F(500, 2000, "kg/m³", "Liquid density; default 1000") = None,
    line_width_mm: F(0.2, 1.2, "mm", "Extrusion line width; default 0.42") = None,
    interlayer_factor: F(0.1, 1, "-", "Strength across layers / in-plane strength; default 0.5") = None,
) -> ToolRecord:
    """Capacity, wall stress from the liquid, watertightness (wall lines) and heat margin of a printed
    cylindrical cup (HW4 tool). Errors: values outside their ranges."""
    return _run(print_tools.check_container, **locals())


# ---------------------------------------------------------------- HW4 tool 4
@mcp.tool(title="Compare failure costs with and without monitoring", annotations=READ_ONLY)
def compare_failure_costs(
    print_time_h: Annotated[float, Field(ge=0.05, le=500, description="Time of one attempt [h, 0.05–500]")],
    filament_g: Annotated[float, Field(ge=0.1, le=10000, description="Filament of one attempt [g, 0.1–10000]")],
    failure_probability: F(0, 0.9, "-", "Chance one attempt fails; default 0.15") = None,
    mean_failure_point: F(0, 1, "-", "Mean share of the job done when it fails; default 0.3") = None,
    detection_delay_min: F(0, 600, "min", "Camera detection delay; default 10") = None,
    recoverable_share: F(0, 1, "-", "Share of detected failures repaired in place (hypothesis); default 0.4") = None,
    filament_price_per_kg: F(0, 500, "$/kg", "Filament price; default 23") = None,
    machine_cost_per_h: F(0, 200, "$/h", "Machine time cost; default 0.5") = None,
    jobs: Annotated[Optional[int], Field(None, ge=1, le=1_000_000, description="Good parts to total over; default 100")] = None,
) -> ToolRecord:
    """Expected waste per good part for A) unmonitored, B) detect-and-stop and C) detect-and-adapt (HW4 tool).
    Errors: values outside their ranges."""
    return _run(print_tools.compare_failure_costs, **locals())


# ---------------------------------------------------------------- New tool 5
Clock = Annotated[str, Field(pattern=r"^([01]?\d|2[0-3]):[0-5]\d$", description="24-hour clock time 'HH:MM'")]


@mcp.tool(title="Plan an unattended (overnight) print", annotations=READ_ONLY)
def plan_unattended_print(
    print_time_h: Annotated[float, Field(ge=0.05, le=200, description="Slicer print time [h, 0.05–200]")],
    filament_g: Annotated[float, Field(ge=0.1, le=10000, description="Slicer filament mass [g, 0.1–10000]")],
    start_time: Clock,
    away_from: Annotated[str, Field(pattern=r"^([01]?\d|2[0-3]):[0-5]\d$",
                                    description="When nobody will be watching from, e.g. bedtime 'HH:MM'")],
    back_at: Annotated[str, Field(pattern=r"^([01]?\d|2[0-3]):[0-5]\d$",
                                  description="When someone checks again, e.g. wake-up 'HH:MM'")],
    detection: Annotated[Literal["none", "auto_stop"], Field(
        description="none: you find failures yourself; auto_stop: printer camera detection pauses the job")] = "none",
    failure_probability: F(0, 0.9, "-", "Chance this attempt fails; default 0.15 (assumption)") = None,
    early_failure_share: F(0, 1, "-", "Share of failures in the first layers; default 0.5 (assumption)") = None,
    early_window_h: F(0.05, 5, "h", "How long the 'first layers' last; default 0.5") = None,
    detection_coverage: F(0, 1, "-", "Share of failures auto_stop catches; default 0.6 (assumption)") = None,
    detection_delay_min: F(0, 600, "min", "Delay before auto_stop pauses; default 10") = None,
    check_interval_min: F(1, 480, "min", "How often you look while present; default 60") = None,
    filament_price_per_kg: F(0, 500, "$/kg", "Filament price; default 23") = None,
    machine_cost_per_h: F(0, 200, "$/h", "Machine time cost; default 0.5") = None,
    earliest_start: Annotated[Optional[str], Field(None, pattern=r"^([01]?\d|2[0-3]):[0-5]\d$",
                                                   description="Search start times from (with latest_start)")] = None,
    latest_start: Annotated[Optional[str], Field(None, pattern=r"^([01]?\d|2[0-3]):[0-5]\d$",
                                                 description="Search start times until (with earliest_start)")] = None,
) -> ToolRecord:
    """NEW. For a print that runs partly while nobody is watching: finish time, share unattended, expected time
    until a failure is noticed, expected wasted filament/hours/$, and optionally the best start time in a window
    so the first layers are watched. Errors: bad clock times, away_from == back_at, only one of the search bounds."""
    return _run(new_tools.plan_unattended_print, **locals())


# ---------------------------------------------------------------- New tool 6
class Feature(BaseModel):
    name: str = Field(description="What the feature is, e.g. 'mounting hole'")
    role: Literal["cosmetic", "fit", "load_bearing"] = Field(
        description="cosmetic: shells/walls/looks; fit: holes, slots, mating faces; load_bearing: carries load")
    nominal_mm: float = Field(ge=0.1, le=500, description="Design size [mm, 0.1–500]")
    measured_mm: float = Field(ge=0, le=500, description="Measured or predicted as-printed size [mm, 0–500]")
    tolerance_mm: Optional[float] = Field(None, ge=0.01, le=10, description=(
        "Allowed |measured − nominal| [mm, 0.01–10]; REQUIRED for fit and load_bearing, default 1.0 for cosmetic"))


class Beam(BaseModel):
    material: Literal["PLA", "PETG", "ABS"] = "PLA"
    length_mm: float = Field(ge=5, le=400)
    width_mm: float = Field(ge=1, le=200)
    nominal_thickness_mm: float = Field(ge=0.8, le=100)
    measured_thickness_mm: float = Field(ge=0.8, le=100)
    load_N: Optional[float] = Field(None, ge=0.01, le=5000)
    load_kg: Optional[float] = Field(None, ge=0.001, le=500)
    orientation: Literal["flat", "upright"] = "flat"
    target_safety_factor: Optional[float] = Field(None, ge=1, le=10)


@mcp.tool(title="Check a deviated or repaired part", annotations=READ_ONLY)
def check_deviated_part(
    features: Annotated[list[Feature], Field(min_length=1, max_length=30,
                                             description="Features to compare with the design")],
    beam: Annotated[Optional[Beam], Field(None, description="Load-bearing arm to re-check with check_cantilever")] = None,
    repaired_in_load_path: Annotated[bool, Field(description="Layers were changed or restarted where load passes")] = False,
    reprint_time_h: F(0.05, 500, "h", "Printer time for a reprint, to show what scrapping costs") = None,
    reprint_filament_g: F(0.1, 10000, "g", "Filament for a reprint") = None,
    filament_price_per_kg: F(0, 500, "$/kg", "Filament price; default 23") = None,
    machine_cost_per_h: F(0, 200, "$/h", "Machine time cost; default 0.5") = None,
) -> ToolRecord:
    """NEW. Decide KEEP / TEST_BEFORE_USE / REPRINT for a part that came out (or was repaired) different from its
    design, feature by feature against designer tolerances, and re-check a load-bearing arm's safety factor at
    its as-printed thickness. Errors: a fit or load_bearing feature without tolerance_mm, bad role, values out of range."""
    return _run(new_tools.check_deviated_part,
                features=[f.model_dump() for f in features],
                beam=beam.model_dump(exclude_none=True) if beam else None,
                repaired_in_load_path=repaired_in_load_path, reprint_time_h=reprint_time_h,
                reprint_filament_g=reprint_filament_g, filament_price_per_kg=filament_price_per_kg,
                machine_cost_per_h=machine_cost_per_h)


def main():
    if "--http" in sys.argv:
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
