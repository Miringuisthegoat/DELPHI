"""One-off / cron-friendly script to run DELPHI's transfer optimizer.

Usage:
    python -m scripts.optimize_transfers --gameweek 8
    python -m scripts.optimize_transfers --gameweek 8 --horizon 5 --max-transfers 1
"""

from __future__ import annotations

import argparse

from loguru import logger

from app.core.exceptions import OptimizationError
from app.core.logging import configure_logging
from app.db.session import init_db, session_scope
from app.optimization.transfer_optimizer import TransferOptimizerService


def main() -> None:
    parser = argparse.ArgumentParser(description="Recommend the best DELPHI transfer move.")
    parser.add_argument("--gameweek", type=int, required=True, help="Gameweek to plan for.")
    parser.add_argument(
        "--horizon", type=int, default=1, help="Prediction horizon to optimize against (1, 3, or 5)."
    )
    parser.add_argument(
        "--max-transfers", type=int, default=2, help="Highest transfer count to evaluate."
    )
    args = parser.parse_args()

    configure_logging()
    init_db()
    optimizer = TransferOptimizerService()

    with session_scope() as db:
        try:
            result = optimizer.optimize(
                db,
                gameweek=args.gameweek,
                horizon=args.horizon,
                max_transfers=args.max_transfers,
            )
        except OptimizationError as exc:
            logger.warning("Optimization skipped: {}", exc)
            raise SystemExit(1) from exc

    logger.info(
        "Best move for gw {} (horizon {}): {} transfer(s), net {:+.1f} pts",
        result.gameweek,
        result.horizon,
        result.recommended.transfers,
        result.recommended.net_expected_gain,
    )
    logger.info(result.recommended.reasoning or "No transfer recommended this week.")

    for option in result.options:
        status = "OK" if option.feasible else "infeasible"
        logger.info(
            "  {} transfer(s) [{}]: net={:+.1f}",
            option.transfers,
            status,
            option.net_expected_gain if option.feasible else 0.0,
        )


if __name__ == "__main__":
    main()
