from __future__ import annotations

import math

import numpy as np
import pandas as pd


TRADING_DAYS = 252


def drawdown(equity: pd.Series) -> pd.Series:
    return equity / equity.cummax() - 1


def calculate_metrics(equity: pd.DataFrame, trades: pd.DataFrame) -> dict[str, float]:
    if equity.empty:
        raise ValueError("Cannot calculate metrics for an empty equity curve.")
    portfolio = equity.set_index("timestamp")["equity"]
    returns = portfolio.pct_change(fill_method=None).fillna(0.0)
    benchmark_returns = equity.set_index("timestamp")["benchmark_return"].fillna(0.0)
    elapsed_days = max((portfolio.index[-1] - portfolio.index[0]).total_seconds() / 86_400, 1.0)
    years = elapsed_days / 365.25
    total_return = portfolio.iloc[-1] / portfolio.iloc[0] - 1
    cagr = (portfolio.iloc[-1] / portfolio.iloc[0]) ** (1 / years) - 1
    annual_volatility = returns.std(ddof=0) * math.sqrt(TRADING_DAYS)
    sharpe = returns.mean() / returns.std(ddof=0) * math.sqrt(TRADING_DAYS) if returns.std(ddof=0) > 0 else 0.0
    downside = returns.where(returns < 0, 0).std(ddof=0)
    sortino = returns.mean() / downside * math.sqrt(TRADING_DAYS) if downside > 0 else 0.0
    max_drawdown = float(drawdown(portfolio).min())
    benchmark_total_return = float((1 + benchmark_returns).prod() - 1)
    covariance = np.cov(returns, benchmark_returns, ddof=0)
    beta = float(covariance[0, 1] / covariance[1, 1]) if covariance[1, 1] > 0 else 0.0
    annual_alpha = float((returns.mean() - beta * benchmark_returns.mean()) * TRADING_DAYS)
    average_equity = float(portfolio.mean())
    traded_notional = float(trades["notional"].abs().sum()) if len(trades) else 0.0
    turnover = traded_notional / average_equity / years if average_equity > 0 else 0.0
    return {
        "total_return": float(total_return),
        "cagr": float(cagr),
        "annual_volatility": float(annual_volatility),
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "max_drawdown": max_drawdown,
        "calmar": float(cagr / abs(max_drawdown)) if max_drawdown < 0 else 0.0,
        "benchmark_total_return": benchmark_total_return,
        "excess_return": float(total_return - benchmark_total_return),
        "beta": beta,
        "annual_alpha": annual_alpha,
        "annual_turnover": float(turnover),
        "orders": float(len(trades)),
        "fees": float(trades["fees"].sum()) if len(trades) else 0.0,
    }
