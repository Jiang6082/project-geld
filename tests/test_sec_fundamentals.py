import pytest

from project_geld.sec_fundamentals import extract_company_features


def fact(val, filed, fy, accn, start=None, end=None):
    row = {
        "val": val,
        "filed": filed,
        "fy": fy,
        "fp": "FY",
        "form": "10-K",
        "accn": accn,
        "end": end or f"{fy}-12-31",
    }
    if start:
        row["start"] = start
    return row


def test_companyfacts_are_converted_to_filing_dated_features():
    concepts = {}
    for tag, old, new, duration in [
        ("Assets", 100, 120, False),
        ("Liabilities", 40, 42, False),
        ("GrossProfit", 30, 42, True),
        ("NetIncomeLoss", 10, 15, True),
        ("NetCashProvidedByUsedInOperatingActivities", 12, 18, True),
        ("RevenueFromContractWithCustomerExcludingAssessedTax", 80, 100, True),
        ("EntityCommonStockSharesOutstanding", 10, 11, False),
        ("EarningsPerShareDiluted", 1.0, 1.4, True),
    ]:
        rows = []
        for year, value in [(2023, old), (2024, new)]:
            rows.append(
                fact(
                    value,
                    f"{year + 1}-02-15",
                    year,
                    f"accn-{year}",
                    start=f"{year}-01-01" if duration else None,
                )
            )
        concepts[tag] = {"units": {"USD": rows}}
    frame = extract_company_features("TEST", {"facts": {"us-gaap": concepts}})
    latest = frame.iloc[-1]
    assert latest["available_at"] == "2025-02-15"
    assert latest["gross_profitability"] == pytest.approx(42 / 120)
    assert latest["cash_profitability"] == pytest.approx(18 / 120)
    assert latest["share_growth"] == pytest.approx(0.10)
    assert latest["revenue_growth"] == pytest.approx(0.25)
    assert latest["earnings_growth"] == pytest.approx(0.40)
