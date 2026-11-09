"""Demo record: launch the server over stdio exactly as a desktop client would, list the tools, make the
required calls (one HW4 tool, both new tools, invalid input) and save the requests and responses.

    python demo/client_demo.py      ->  demo/transcript.json and demo/transcript.md
"""
import asyncio
import datetime as dt
import json
import pathlib
import sys

from mcp import Client, StdioServerParameters

HERE = pathlib.Path(__file__).parent

CALLS = [
    ("Original HW4 tool: the 14-hour overnight print from interview P3",
     "compare_failure_costs", {"print_time_h": 14, "filament_g": 300, "failure_probability": 0.15}),
    ("New tool 1: same job started at bedtime, then search for a better start time",
     "plan_unattended_print", {"print_time_h": 14, "filament_g": 300, "start_time": "22:30", "away_from": "23:30",
                               "back_at": "08:00", "earliest_start": "08:00", "latest_start": "23:00"}),
    ("New tool 2: a bracket whose arm was thinned by an in-print repair",
     "check_deviated_part", {
         "features": [
             {"name": "outer shell", "role": "cosmetic", "nominal_mm": 40.0, "measured_mm": 40.6},
             {"name": "M5 mounting hole", "role": "fit", "nominal_mm": 5.3, "measured_mm": 5.22, "tolerance_mm": 0.15},
             {"name": "arm thickness", "role": "load_bearing", "nominal_mm": 5.0, "measured_mm": 4.5,
              "tolerance_mm": 0.6}],
         "beam": {"material": "PLA", "length_mm": 40, "width_mm": 20, "nominal_thickness_mm": 5.0,
                  "measured_thickness_mm": 4.5, "load_kg": 2},
         "repaired_in_load_path": True, "reprint_time_h": 2.5, "reprint_filament_g": 38}),
    ("Invalid input (domain rule): a fit feature with no tolerance",
     "check_deviated_part", {"features": [{"name": "M5 mounting hole", "role": "fit", "nominal_mm": 5.3,
                                           "measured_mm": 5.22}]}),
    ("Invalid input (schema): an impossible clock time",
     "plan_unattended_print", {"print_time_h": 14, "filament_g": 300, "start_time": "25:00", "away_from": "23:30",
                               "back_at": "08:00"}),
]


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "fdm_mcp.server"])
    record = {"run_at": dt.datetime.now().isoformat(timespec="seconds"), "transport": "stdio", "calls": []}
    async with Client(params) as c:
        tools = (await c.list_tools()).tools
        record["tools"] = [t.name for t in tools]
        for title, name, args in CALLS:
            r = await c.call_tool(name, args)
            record["calls"].append({"title": title, "tool": name, "arguments": args, "is_error": r.is_error,
                                    "text": r.content[0].text if r.content else None,
                                    "structured": r.structured_content})
    (HERE / "transcript.json").write_text(json.dumps(record, indent=2, ensure_ascii=False))

    md = [f"# MCP demo transcript", "", f"Run {record['run_at']} over {record['transport']}. "
          f"Tools listed: {', '.join(record['tools'])}.", ""]
    for i, c in enumerate(record["calls"], 1):
        md += [f"## {i}. {c['title']}", "", f"`tools/call` **{c['tool']}**", "", "```json",
               json.dumps(c["arguments"], indent=1, ensure_ascii=False), "```", ""]
        if c["is_error"]:
            md += [f"**isError: true**", "", "```", c["text"], "```", ""]
        else:
            s = c["structured"]
            md += [f"verdict **{s['verdict']}**" + (f", decision **{s['decision']}**" if s.get("decision") else ""),
                   "", s["summary"], ""]
    (HERE / "transcript.md").write_text("\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    asyncio.run(main())
