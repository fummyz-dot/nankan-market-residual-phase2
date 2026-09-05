"""CLI for explicit Main Recommendation actual-purchase confirmation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.operations.actual_purchase_accounting import NOT_PURCHASED, PURCHASED, confirm_main_purchase
from src.operations.live_development_store import DEFAULT_DB


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Record one explicit Main Recommendation purchase or non-purchase.")
    parser.add_argument("--recommendation-id", required=True)
    parser.add_argument("--ticket-index", required=True, type=int)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--confirm-purchased", action="store_true")
    group.add_argument("--confirm-not-purchased", action="store_true")
    stake = parser.add_mutually_exclusive_group()
    stake.add_argument("--use-recommended-stake", action="store_true")
    stake.add_argument("--stake-yen", type=int)
    parser.add_argument("--placed-at")
    parser.add_argument("--execution-odds", type=float)
    parser.add_argument("--evidence-db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args(argv)
    status = PURCHASED if args.confirm_purchased else NOT_PURCHASED
    try:
        value = confirm_main_purchase(
            recommendation_id=args.recommendation_id, ticket_index=args.ticket_index,
            confirmation_status=status, use_recommended_stake=args.use_recommended_stake,
            stake_yen=args.stake_yen, placed_at=args.placed_at, execution_odds=args.execution_odds,
            evidence_db=args.evidence_db,
        )
    except Exception as exc:
        value = {"status": getattr(exc, "code", type(exc).__name__), "detail": getattr(exc, "detail", str(exc)), "written": False}
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
