#!/usr/bin/env python3
"""Reproduce the synthetic component-replacement case in a temporary database."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bom_change_assistant.demo import load_demo_snapshot
from bom_change_assistant.impact_engine import ImpactEngine
from bom_change_assistant.models import ChangeRequest
from bom_change_assistant.repository import Repository
from bom_change_assistant.summaries import markdown_report, role_checklists, rule_summary


def reproduce() -> dict:
    scenario = json.loads((ROOT / "examples" / "scenario.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="bom-synthetic-") as directory:
        repository = Repository(Path(directory) / "demo.sqlite3")
        load_demo_snapshot(repository)
        result = ImpactEngine(repository).analyze(ChangeRequest(**scenario["change"]))
    actual = {
        "top_materials": sorted(result["top_materials"]),
        "upstream_paths": result["metrics"]["upstream_paths"],
        "sales_order_items": result["metrics"]["sales_order_items"],
        "production_orders": sorted(item["production_order"] for item in result["production_orders"]),
        "planning_recommendation_code": result["planning_impact"]["recommendation_code"],
        "material_readiness": result["material_validation"]["status"],
    }
    if actual != scenario["expected"]:
        raise AssertionError(f"Scenario differs from the reviewed expectation: {actual!r}")
    result["analysis_id"] = "EXAMPLE-REPLACEMENT-001"
    result["bom_version"] = scenario["change"]["bom_version"]
    result["data_classification"] = scenario["data_classification"]
    result["summary"] = rule_summary(result)
    result["checklists"] = role_checklists(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify outcomes without writing files")
    args = parser.parse_args()
    result = reproduce()
    if not args.check:
        report = (
            "> 完全虚构的演示数据。由 scripts/run_example.py 实际运行生成。\n"
            "> 数量未提供时保留待补数据；候选影响不代表实际需求或已批准的变更。\n\n"
            + markdown_report(result)
        )
        (ROOT / "examples" / "sample-report.md").write_text(report, encoding="utf-8")
        (ROOT / "examples" / "sample-result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print("Synthetic example verified: 2 products, 3 upstream paths, 2 sales lines, 1 production order.")
    print("Supply data and quality evidence are incomplete; no change is approved or executed.")


if __name__ == "__main__":
    main()
