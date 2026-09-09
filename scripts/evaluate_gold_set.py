from __future__ import annotations

import argparse
import json
from pathlib import Path

from eastmed_pipeline.gold_evaluation import GoldEvaluationError, evaluate_files


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate multilingual claim extraction")
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--include-unreviewed",
        action="store_true",
        help="development only; report will be marked release_evidence=false",
    )
    args = parser.parse_args()
    try:
        report = evaluate_files(
            args.gold,
            args.predictions,
            include_unreviewed=args.include_unreviewed,
        )
    except GoldEvaluationError as exc:
        raise SystemExit(str(exc)) from exc
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
