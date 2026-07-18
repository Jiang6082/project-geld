from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from project_geld.sec_fundamentals import SecFundamentalSource


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch filing-dated SEC fundamentals for the broad research universe."
    )
    parser.add_argument(
        "--membership", default="artifacts/research-broad/membership-periods.json"
    )
    parser.add_argument(
        "--output",
        default="artifacts/research-broad/sec-fundamental-features.csv.gz",
    )
    parser.add_argument("--cache-dir", default="data/sec-companyfacts")
    args = parser.parse_args()

    load_dotenv()
    user_agent = os.getenv("PROJECT_GELD_SEC_USER_AGENT") or os.getenv("SEC_USER_AGENT")
    if not user_agent:
        raise RuntimeError(
            "Set PROJECT_GELD_SEC_USER_AGENT to an application name and contact email."
        )
    membership = json.loads(Path(args.membership).read_text(encoding="utf-8"))
    source = SecFundamentalSource(user_agent, Path(args.cache_dir))
    features = source.fetch(sorted(membership))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    features.to_csv(output, index=False, compression="gzip")
    print(
        f"saved {len(features):,} filing-dated observations for "
        f"{features['symbol'].nunique():,} symbols to {output.resolve()}"
    )


if __name__ == "__main__":
    main()
