from __future__ import annotations

import os
import re

from dotenv import load_dotenv


def _profile_token(profile: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "_", profile.strip()).strip("_").upper()
    return token


def alpaca_environment_names(profile: str = "") -> tuple[list[str], list[str]]:
    token = _profile_token(profile)
    if token:
        return (
            [f"ALPACA_{token}_API_KEY", f"APCA_{token}_API_KEY_ID"],
            [f"ALPACA_{token}_SECRET_KEY", f"APCA_{token}_API_SECRET_KEY"],
        )
    return (
        ["ALPACA_API_KEY", "APCA_API_KEY_ID"],
        ["ALPACA_SECRET_KEY", "APCA_API_SECRET_KEY"],
    )


def load_alpaca_credentials(profile: str = "") -> tuple[str, str]:
    load_dotenv()
    key_names, secret_names = alpaca_environment_names(profile)
    api_key = next((os.getenv(name) for name in key_names if os.getenv(name)), None)
    secret_key = next(
        (os.getenv(name) for name in secret_names if os.getenv(name)), None
    )
    if not api_key or not secret_key:
        expected = f"{key_names[0]} and {secret_names[0]}"
        raise RuntimeError(f"Set {expected} in .env for this account profile.")
    return api_key, secret_key
