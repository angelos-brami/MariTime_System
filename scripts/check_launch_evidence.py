from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from eastmed_shared.launch_evidence import load_launch_evidence, validate_launch_evidence


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate production launch evidence")
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    checks = validate_launch_evidence(load_launch_evidence(args.evidence))
    report = {
        "ready": all(check.passed for check in checks),
        "checks": [asdict(check) for check in checks],
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if not report["ready"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
