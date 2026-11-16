#!/usr/bin/env python3
"""Run the synthetic regression suite and optionally save traceable results."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bom_change_assistant import BUILD_ID, __version__


def test_ids(suite: unittest.TestSuite) -> list[str]:
    result = []
    for test in suite:
        result.extend(test_ids(test) if isinstance(test, unittest.TestSuite) else [test.id()])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="Write a JSON verification record")
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    identifiers = test_ids(suite)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    paths = [ROOT / "app.py", ROOT / "pyproject.toml"]
    for directory in ("src", "tests", "scripts"):
        paths.extend(p for p in (ROOT / directory).rglob("*")
                     if p.is_file() and p.suffix in {".py", ".js", ".cjs", ".html", ".css"})
    record = {
        "build": BUILD_ID,
        "version": __version__,
        "upstream_base": "0a57eb6ecff98df3164737e05edb059e8b21628f",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "platform": platform.system(),
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "passed": result.wasSuccessful(),
        "data": "Synthetic fixtures and temporary databases only",
        "test_ids": identifiers,
        "source_sha256": {
            p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(set(paths))
        },
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Verification record: {args.report}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
