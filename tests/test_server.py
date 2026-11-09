"""End-to-end tests through an in-process MCP client, plus hand checks of the new tools' numbers."""
import asyncio
import math

import pytest
from mcp import Client

from fdm_mcp.new_tools import check_deviated_part, plan_unattended_print
from fdm_mcp.print_tools import ToolInputError
from fdm_mcp.server import mcp


def call(name, args):
    async def go():
        async with Client(mcp) as c:
            return await c.call_tool(name, args)
    return asyncio.run(go())


def test_six_tools_listed_with_schemas():
    async def go():
        async with Client(mcp) as c:
            return (await c.list_tools()).tools
    tools = {t.name: t for t in asyncio.run(go())}
    assert set(tools) == {"estimate_print_job", "check_cantilever", "check_container", "compare_failure_costs",
                          "plan_unattended_print", "check_deviated_part"}
    for t in tools.values():
        assert t.output_schema and t.annotations.read_only_hint


@pytest.mark.parametrize("name,args", [
    ("estimate_print_job", {"shape": "box", "size_x_mm": 40, "size_y_mm": 20, "size_z_mm": 10}),
    ("check_cantilever", {"length_mm": 60, "width_mm": 15, "thickness_mm": 4, "load_kg": 1}),
    ("check_container", {"inner_diameter_mm": 70, "height_mm": 90, "wall_thickness_mm": 1.2, "liquid_temp_C": 80}),
    ("compare_failure_costs", {"print_time_h": 14, "filament_g": 300}),
    ("plan_unattended_print", {"print_time_h": 14, "filament_g": 300, "start_time": "22:30",
                               "away_from": "23:30", "back_at": "08:00"}),
    ("check_deviated_part", {"features": [{"name": "shell", "role": "cosmetic", "nominal_mm": 40, "measured_mm": 40.4}]}),
])
def test_valid_calls_return_structured_records(name, args):
    r = call(name, args)
    assert not r.is_error
    s = r.structured_content
    assert s["tool"] == name and s["verdict"] in ("OK", "CAUTION", "FAIL") and s["results"] and s["human_checks"]


@pytest.mark.parametrize("name,args,msg", [
    ("check_cantilever", {"length_mm": -60, "width_mm": 15, "thickness_mm": 4, "load_kg": 1}, "greater than or equal"),
    ("check_cantilever", {"length_mm": 60, "width_mm": 15, "thickness_mm": 4}, "load_N"),
    ("estimate_print_job", {"shape": "box", "size_x_mm": 40, "size_y_mm": 20, "size_z_mm": 10,
                            "layer_height_mm": 0.5}, "80 %"),
    ("plan_unattended_print", {"print_time_h": 5, "filament_g": 80, "start_time": "25:00",
                               "away_from": "23:00", "back_at": "07:00"}, "pattern"),
    ("plan_unattended_print", {"print_time_h": 5, "filament_g": 80, "start_time": "21:00",
                               "away_from": "23:00", "back_at": "23:00"}, "same time"),
    ("check_deviated_part", {"features": [{"name": "hole", "role": "fit", "nominal_mm": 5, "measured_mm": 5.1}]},
     "tolerance_mm is required"),
])
def test_invalid_inputs_are_tool_errors(name, args, msg):
    r = call(name, args)
    assert r.is_error and msg in r.content[0].text


def test_unattended_fully_attended_has_short_delay():
    # Print 10:00-12:00, away 23:00-07:00: always attended, delay = half the 60-min check interval.
    r = plan_unattended_print(print_time_h=2, filament_g=50, start_time="10:00", away_from="23:00", back_at="07:00")
    assert r["results"]["unattended_share_pct"]["value"] == 0
    assert math.isclose(r["results"]["mean_hours_until_known"]["value"], 0.5, abs_tol=0.01)


def test_unattended_whole_print_overnight_no_detection():
    # 4 h print 00:00-04:00, away 23:00-08:00, no early-failure concentration: failure at u found at 08:00.
    r = plan_unattended_print(print_time_h=4, filament_g=100, start_time="00:00", away_from="23:00", back_at="08:00",
                              early_failure_share=0, failure_probability=0.5)
    # Every failure keeps extruding to the end of the job -> waste = p * whole job.
    assert math.isclose(r["results"]["expected_wasted_filament_g"]["value"], 50, abs_tol=0.1)
    # mean delay = 8 - mean failure time 2 h = 6 h
    assert math.isclose(r["results"]["mean_hours_until_known"]["value"], 6, abs_tol=0.01)
    assert r["checks"][0]["status"] == "CAUTION"


def test_auto_stop_reduces_waste():
    base = dict(print_time_h=10, filament_g=200, start_time="22:00", away_from="23:00", back_at="07:00")
    a = plan_unattended_print(**base)
    b = plan_unattended_print(**base, detection="auto_stop")
    assert b["results"]["expected_wasted_filament_g"]["value"] < a["results"]["expected_wasted_filament_g"]["value"]


def test_start_time_search_prefers_watched_first_layers():
    r = plan_unattended_print(print_time_h=6, filament_g=120, start_time="23:30", away_from="23:00", back_at="07:00",
                              earliest_start="07:00", latest_start="22:00")
    assert r["checks"][0]["status"] == "CAUTION"        # 23:30 start is unwatched
    assert r["decision"].startswith("start at")


def test_deviated_part_decisions():
    hole = {"name": "hole", "role": "fit", "nominal_mm": 5.2, "measured_mm": 5.0, "tolerance_mm": 0.1}
    assert check_deviated_part(features=[hole])["decision"] == "REPRINT"
    hole_ok = {**hole, "measured_mm": 5.15}
    assert check_deviated_part(features=[hole_ok])["decision"] == "KEEP"
    assert check_deviated_part(features=[hole_ok], repaired_in_load_path=True)["decision"] == "TEST_BEFORE_USE"
    shell = {"name": "shell", "role": "cosmetic", "nominal_mm": 40, "measured_mm": 42}
    r = check_deviated_part(features=[shell])
    assert r["decision"] == "KEEP" and r["verdict"] == "CAUTION"


def test_deviated_beam_strength_scales_with_thickness_squared():
    beam = dict(material="PLA", length_mm=40, width_mm=20, nominal_thickness_mm=5, measured_thickness_mm=4.5,
                load_kg=2)
    arm = {"name": "arm", "role": "load_bearing", "nominal_mm": 5, "measured_mm": 4.5, "tolerance_mm": 0.6}
    r = check_deviated_part(features=[arm], beam=beam)
    sf0 = r["results"]["beam_safety_factor_design"]["value"]
    sf1 = r["results"]["beam_safety_factor_as_printed"]["value"]
    assert math.isclose(sf1 / sf0, 0.81, abs_tol=0.01)
    # hand check: sigma = 6FL/(bt^2) = 6*19.62*40/(20*25) = 9.42 MPa; SF = 39/9.42 = 4.14
    assert math.isclose(sf0, 39 / (6 * 2 * 9.81 * 40 / (20 * 25)), abs_tol=0.01)


def test_missing_tolerance_raises_directly():
    with pytest.raises(ToolInputError):
        check_deviated_part(features=[{"name": "arm", "role": "load_bearing", "nominal_mm": 5, "measured_mm": 5}])
