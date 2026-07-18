from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd


FEATURE_COLUMNS = [
    "available_at",
    "symbol",
    "form",
    "fiscal_period_end",
    "gross_profitability",
    "cash_profitability",
    "accruals",
    "leverage",
    "share_growth",
    "revenue_growth",
    "earnings_growth",
]


CONCEPTS = {
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "Revenues",
    ],
    "gross_profit": ["GrossProfit"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "shares": ["EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
}


def _points(companyfacts: dict, aliases: list[str]) -> list[dict]:
    facts = companyfacts.get("facts", {}).get("us-gaap", {})
    for alias in aliases:
        concept = facts.get(alias)
        if not concept:
            continue
        rows = []
        for values in concept.get("units", {}).values():
            rows.extend(values)
        if rows:
            return rows
    return []


def _duration_days(row: dict) -> int | None:
    if not row.get("start") or not row.get("end"):
        return None
    return (pd.Timestamp(row["end"]) - pd.Timestamp(row["start"])).days


def _usable(row: dict) -> bool:
    form = str(row.get("form", ""))
    if form not in {"10-Q", "10-K", "20-F", "40-F"}:
        return False
    duration = _duration_days(row)
    if form == "10-Q" and duration is not None and not 60 <= duration <= 120:
        return False
    if form in {"10-K", "20-F", "40-F"} and duration is not None and not 300 <= duration <= 430:
        return False
    return row.get("filed") is not None and row.get("end") is not None


def extract_company_features(symbol: str, companyfacts: dict) -> pd.DataFrame:
    records: dict[tuple, dict] = {}
    for name, aliases in CONCEPTS.items():
        for raw in _points(companyfacts, aliases):
            if not _usable(raw):
                continue
            key = (
                raw.get("accn"),
                raw.get("filed"),
                raw.get("fy"),
                raw.get("fp"),
                raw.get("form"),
                raw.get("end"),
            )
            record = records.setdefault(
                key,
                {
                    "available_at": raw.get("filed"),
                    "symbol": symbol.upper(),
                    "form": raw.get("form"),
                    "fiscal_year": raw.get("fy"),
                    "fiscal_period": raw.get("fp"),
                    "fiscal_period_end": raw.get("end"),
                },
            )
            record[name] = raw.get("val")

    frame = pd.DataFrame(records.values())
    if frame.empty:
        return pd.DataFrame(columns=FEATURE_COLUMNS)
    frame = frame.sort_values(["available_at", "fiscal_period_end"]).drop_duplicates(
        ["available_at", "fiscal_period", "fiscal_year"], keep="last"
    )
    for column in CONCEPTS:
        if column not in frame:
            frame[column] = np.nan
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["gross_profit"] = frame["gross_profit"].where(
        frame["gross_profit"].notna(), frame["revenue"] - frame["cost_of_revenue"]
    )
    assets = frame["assets"].replace(0, np.nan).abs()
    frame["gross_profitability"] = frame["gross_profit"] / assets
    frame["cash_profitability"] = frame["operating_cash_flow"] / assets
    frame["accruals"] = (frame["net_income"] - frame["operating_cash_flow"]) / assets
    frame["leverage"] = frame["liabilities"] / assets

    comparable = frame.set_index(["fiscal_year", "fiscal_period"])
    share_growth = []
    revenue_growth = []
    earnings_growth = []
    for _, row in frame.iterrows():
        prior_key = (row["fiscal_year"] - 1, row["fiscal_period"])
        prior = comparable.loc[prior_key] if prior_key in comparable.index else None
        if isinstance(prior, pd.DataFrame):
            prior = prior.iloc[-1]

        def growth(column: str) -> float:
            if prior is None or pd.isna(row[column]) or pd.isna(prior[column]):
                return np.nan
            denominator = abs(float(prior[column]))
            return (float(row[column]) - float(prior[column])) / max(denominator, 1e-9)

        share_growth.append(growth("shares"))
        revenue_growth.append(growth("revenue"))
        eps_growth = growth("eps")
        earnings_growth.append(eps_growth if pd.notna(eps_growth) else growth("net_income"))
    frame["share_growth"] = share_growth
    frame["revenue_growth"] = revenue_growth
    frame["earnings_growth"] = earnings_growth
    frame = frame.replace([np.inf, -np.inf], np.nan)
    return frame[FEATURE_COLUMNS].reset_index(drop=True)


@dataclass
class SecFundamentalSource:
    user_agent: str
    cache_dir: Path = Path("data/sec-companyfacts")
    requests_per_second: float = 5.0

    def __post_init__(self) -> None:
        if not self.user_agent.strip():
            raise ValueError("SEC user agent must identify the requester and contact.")
        if not 0 < self.requests_per_second <= 10:
            raise ValueError("SEC request rate must be in (0, 10].")

    def _json(self, url: str, cache_path: Path) -> dict:
        if cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))
        request = Request(url, headers={"User-Agent": self.user_agent})
        with urlopen(request, timeout=60) as response:
            payload = response.read()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(payload)
        time.sleep(1 / self.requests_per_second)
        return json.loads(payload)

    def ticker_map(self) -> dict[str, str]:
        data = self._json(
            "https://www.sec.gov/files/company_tickers.json",
            self.cache_dir / "company_tickers.json",
        )
        return {
            str(item["ticker"]).upper(): str(item["cik_str"]).zfill(10)
            for item in data.values()
        }

    def fetch(self, symbols: list[str]) -> pd.DataFrame:
        mapping = self.ticker_map()
        frames = []
        for index, symbol in enumerate(symbols, 1):
            cik = mapping.get(symbol.upper())
            if cik is None:
                continue
            facts = self._json(
                f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
                self.cache_dir / f"CIK{cik}.json",
            )
            features = extract_company_features(symbol, facts)
            if len(features):
                frames.append(features)
            if index % 25 == 0:
                print(f"SEC fundamentals {index}/{len(symbols)}", flush=True)
        return (
            pd.concat(frames, ignore_index=True)
            if frames
            else pd.DataFrame(columns=FEATURE_COLUMNS)
        )
