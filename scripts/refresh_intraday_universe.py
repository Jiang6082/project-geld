"""Prepare the live Intra V15 universe and a derived paper configuration."""
from __future__ import annotations

import argparse
from pathlib import Path

from project_geld.universe_refresh import refresh


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/paper-intra-v15.toml"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/paper-intra-v15"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        result = refresh(args.config, args.output, force=args.force)
    except Exception as error:
        # Do not print request objects/tracebacks that may include credential headers.
        print(f"Universe refresh failed ({type(error).__name__}); previous snapshot retained. No paper cycle started.")
        if isinstance(error, (ValueError, FileNotFoundError)):
            print(str(error))
        raise SystemExit(2) from None
    print(f"Paper configuration: {result}")


if __name__ == "__main__":
    main()
