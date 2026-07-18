from __future__ import annotations

import argparse
from datetime import date
import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Match broad-backtest missing-price exits to Alpaca corporate actions."
    )
    parser.add_argument("--research-dir", default="artifacts/research-broad")
    args = parser.parse_args()
    directory = Path(args.research_dir)
    trades = pd.read_csv(directory / "momentum-v2" / "trades.csv")
    forced = trades[
        trades["exit_reason"].eq("missing_price_forced_exit")
    ].copy()
    symbols = sorted(forced["symbol"].unique())

    load_dotenv()
    api_key = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
    secret_key = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
    from alpaca.data.historical.corporate_actions import CorporateActionsClient
    from alpaca.data.requests import CorporateActionsRequest

    client = CorporateActionsClient(api_key, secret_key, raw_data=True)
    actions = client.get_corporate_actions(
        CorporateActionsRequest(
            symbols=symbols,
            start=date(2016, 1, 1),
            end=date(2026, 12, 31),
            limit=1000,
        )
    )
    relevant_types = {
        "cash_mergers",
        "stock_mergers",
        "stock_and_cash_mergers",
        "worthless_removals",
        "redemptions",
        "name_changes",
    }
    matched: dict[str, list[dict]] = {symbol: [] for symbol in symbols}
    for action_type, records in actions.items():
        if action_type not in relevant_types:
            continue
        for record in records:
            record_symbols = {
                str(value).upper()
                for key, value in record.items()
                if "symbol" in key and value
            }
            for symbol in record_symbols.intersection(matched):
                matched[symbol].append({"action_type": action_type, **record})
    rows = []
    for trade in forced.itertuples(index=False):
        records = matched.get(trade.symbol, [])
        if not records:
            rows.append(
                {
                    "symbol": trade.symbol,
                    "forced_exit_timestamp": trade.timestamp,
                    "matched_action": "unmatched",
                }
            )
            continue
        for record in records:
            rows.append(
                {
                    "symbol": trade.symbol,
                    "forced_exit_timestamp": trade.timestamp,
                    "matched_action": record.get("action_type"),
                    "process_date": record.get("process_date"),
                    "effective_date": record.get("effective_date"),
                    "cash_rate": record.get("rate", record.get("cash_rate")),
                    "acquirer_symbol": record.get("acquirer_symbol"),
                }
            )
    result = pd.DataFrame(rows)
    result.to_csv(directory / "forced-exit-corporate-action-audit.csv", index=False)
    print(result.to_string(index=False))


if __name__ == "__main__":
    main()
