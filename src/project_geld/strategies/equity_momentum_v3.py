from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from project_geld.strategies.base import TARGET_COLUMNS, close_matrix


def _cross_sectional_zscore(values: pd.DataFrame) -> pd.DataFrame:
    """Winsorized cross-sectional z-scores calculated independently each day."""

    lower = values.quantile(0.05, axis=1)
    upper = values.quantile(0.95, axis=1)
    clipped = values.clip(lower=lower, upper=upper, axis=0)
    mean = clipped.mean(axis=1)
    standard_deviation = clipped.std(axis=1).replace(0, np.nan)
    return clipped.sub(mean, axis=0).div(standard_deviation, axis=0)


def _capped_inverse_volatility_weights(
    volatility: pd.Series, gross_exposure: float, cap: float
) -> pd.Series:
    """Allocate gross exposure by inverse volatility without breaching a name cap."""

    inverse = 1 / volatility.replace(0, np.nan)
    inverse = inverse.replace([np.inf, -np.inf], np.nan).dropna()
    if inverse.empty or gross_exposure <= 0:
        return pd.Series(dtype=float)

    gross = min(gross_exposure, cap * len(inverse))
    weights = pd.Series(0.0, index=inverse.index)
    remaining = list(inverse.index)
    remaining_gross = gross
    while remaining and remaining_gross > 1e-12:
        raw = inverse.reindex(remaining)
        allocation = raw / raw.sum() * remaining_gross
        capped = allocation[allocation >= cap - 1e-12]
        if capped.empty:
            weights.loc[remaining] = allocation
            break
        weights.loc[capped.index] = cap
        remaining_gross -= cap * len(capped)
        capped_names = set(capped.index)
        remaining = [symbol for symbol in remaining if symbol not in capped_names]
    return weights[weights.gt(0)]


def _asof_feature_matrix(
    features: pd.DataFrame, field: str, close: pd.DataFrame
) -> pd.DataFrame:
    if field not in features:
        return pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    frame = features[["available_at", "symbol", field]].copy()
    frame["available_at"] = pd.to_datetime(frame["available_at"], utc=True) + pd.offsets.Day(1)
    frame["symbol"] = frame["symbol"].astype(str).str.upper()
    frame[field] = pd.to_numeric(frame[field], errors="coerce")
    frame = frame.dropna(subset=[field]).drop_duplicates(
        ["available_at", "symbol"], keep="last"
    )
    if frame.empty:
        return pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    pivot = frame.pivot(index="available_at", columns="symbol", values=field)
    combined_index = close.index.union(pivot.index).sort_values()
    return (
        pivot.reindex(combined_index)
        .ffill()
        .reindex(index=close.index, columns=close.columns)
    )


@dataclass(frozen=True)
class EquityMomentumV3:
    """Diversified market-residual momentum with defensive exposure control."""

    benchmark_symbol: str = "SPY"
    formation_lookback: int = 252
    medium_lookback: int = 126
    skip_recent: int = 21
    beta_lookback: int = 126
    volatility_lookback: int = 60
    correlation_lookback: int = 126
    fast_window: int = 50
    slow_window: int = 200
    max_symbols: int = 40
    exit_rank: int = 80
    max_position_weight: float = 0.04
    maximum_annualized_volatility: float = 0.60
    max_pairwise_correlation: float = 0.85
    target_portfolio_volatility: float = 0.15
    max_per_sector: int = 8
    sector_map: dict[str, str] = field(default_factory=dict)
    rebalance_every: int = 21
    bullish_exposure: float = 1.0
    mixed_exposure: float = 0.75
    bearish_exposure: float = 0.25
    breadth_threshold: float = 0.50
    regime_enabled: bool = True
    membership_periods: dict[str, list[list[str | None]]] = field(default_factory=dict)
    residual_factor_symbols: list[str] = field(default_factory=list)
    external_features_file: str | None = None
    price_score_weight: float = 1.0
    quality_score_weight: float = 0.0
    earnings_score_weight: float = 0.0
    weighting_method: str = "inverse_downside_volatility"
    score_tilt_strength: float = 0.20
    beta_penalty: float = 0.50
    name: str = "momentum_v3"

    def __post_init__(self) -> None:
        if self.formation_lookback <= self.skip_recent:
            raise ValueError("formation_lookback must exceed skip_recent.")
        if self.medium_lookback <= self.skip_recent:
            raise ValueError("medium_lookback must exceed skip_recent.")
        if self.max_symbols < 1 or self.exit_rank < self.max_symbols:
            raise ValueError("exit_rank must be at least max_symbols, both positive.")
        if self.rebalance_every < 1:
            raise ValueError("rebalance_every must be positive.")
        if not 0 < self.max_position_weight <= 1:
            raise ValueError("max_position_weight must be in (0, 1].")
        if not 0 <= self.max_pairwise_correlation <= 1:
            raise ValueError("max_pairwise_correlation must be in [0, 1].")
        if self.target_portfolio_volatility <= 0:
            raise ValueError("target_portfolio_volatility must be positive.")
        if self.max_per_sector < 1:
            raise ValueError("max_per_sector must be positive.")
        if any(
            weight < 0
            for weight in [
                self.price_score_weight,
                self.quality_score_weight,
                self.earnings_score_weight,
            ]
        ):
            raise ValueError("Score weights cannot be negative.")
        if self.price_score_weight + self.quality_score_weight + self.earnings_score_weight <= 0:
            raise ValueError("At least one score weight must be positive.")
        if self.weighting_method not in {
            "inverse_downside_volatility",
            "benchmark_aware",
        }:
            raise ValueError("Unknown weighting_method.")
        exposures = [self.bullish_exposure, self.mixed_exposure, self.bearish_exposure]
        if any(not 0 <= exposure <= 1 for exposure in exposures):
            raise ValueError("Regime exposures must be in [0, 1].")

    @property
    def warmup_bars(self) -> int:
        return max(
            self.formation_lookback,
            self.beta_lookback,
            self.correlation_lookback,
            self.slow_window,
        ) + 1

    @property
    def context_symbols(self) -> list[str]:
        return list(
            dict.fromkeys(
                [
                    self.benchmark_symbol.upper(),
                    *(symbol.upper() for symbol in self.residual_factor_symbols),
                ]
            )
        )

    def _membership(self, close: pd.DataFrame) -> pd.DataFrame:
        if not self.membership_periods:
            return close.notna()
        membership = pd.DataFrame(False, index=close.index, columns=close.columns)
        for raw_symbol, periods in self.membership_periods.items():
            symbol = raw_symbol.upper()
            if symbol not in membership.columns:
                continue
            for start, end in periods:
                start_ts = pd.Timestamp(start)
                if start_ts.tzinfo is None:
                    start_ts = start_ts.tz_localize("UTC")
                end_ts = pd.Timestamp(end) if end is not None else close.index.max()
                if end_ts.tzinfo is None:
                    end_ts = end_ts.tz_localize("UTC")
                active = membership.index.to_series().between(
                    start_ts, end_ts, inclusive="both"
                )
                membership.loc[active, symbol] = True
        return membership

    def _gross_exposure(
        self,
        timestamp: pd.Timestamp,
        benchmark_close: pd.Series,
        benchmark_slow: pd.Series,
        breadth: pd.Series,
    ) -> float:
        if not self.regime_enabled:
            return self.bullish_exposure
        benchmark_above_trend = bool(
            pd.notna(benchmark_slow.at[timestamp])
            and benchmark_close.at[timestamp] > benchmark_slow.at[timestamp]
        )
        broad_strength = bool(
            pd.notna(breadth.at[timestamp])
            and breadth.at[timestamp] >= self.breadth_threshold
        )
        if benchmark_above_trend and broad_strength:
            return self.bullish_exposure
        if benchmark_above_trend or broad_strength:
            return self.mixed_exposure
        return self.bearish_exposure

    def generate_targets(self, bars: pd.DataFrame) -> pd.DataFrame:
        close = close_matrix(bars)
        benchmark = self.benchmark_symbol.upper()
        if benchmark not in close.columns:
            raise ValueError(
                f"Momentum V3 requires benchmark context bars for {benchmark}."
            )
        missing_factors = {
            symbol.upper() for symbol in self.residual_factor_symbols
        } - set(close.columns)
        if missing_factors:
            raise ValueError(
                "Momentum V3 is missing residual factor context bars for: "
                + ", ".join(sorted(missing_factors))
            )

        returns = close.pct_change(fill_method=None)
        benchmark_returns = returns[benchmark]
        benchmark_variance = benchmark_returns.rolling(self.beta_lookback).var()
        beta = returns.rolling(self.beta_lookback).cov(benchmark_returns).div(
            benchmark_variance, axis=0
        )

        long_momentum = (
            close.shift(self.skip_recent) / close.shift(self.formation_lookback) - 1
        )
        medium_momentum = (
            close.shift(self.skip_recent) / close.shift(self.medium_lookback) - 1
        )
        if self.residual_factor_symbols:
            residual_returns = returns.copy()
            factor_returns = [benchmark_returns]
            factor_returns.extend(
                returns[symbol.upper()] - benchmark_returns
                for symbol in self.residual_factor_symbols
            )
            for factor_return in factor_returns:
                factor_variance = factor_return.rolling(self.beta_lookback).var()
                factor_beta = returns.rolling(self.beta_lookback).cov(
                    factor_return
                ).div(factor_variance, axis=0)
                residual_returns = residual_returns.sub(
                    factor_beta.mul(factor_return, axis=0)
                )
            log_residual = np.log1p(residual_returns.clip(lower=-0.999999))
            residual_long = (
                log_residual.shift(self.skip_recent)
                .rolling(self.formation_lookback - self.skip_recent)
                .sum()
            )
            residual_medium = (
                log_residual.shift(self.skip_recent)
                .rolling(self.medium_lookback - self.skip_recent)
                .sum()
            )
        else:
            benchmark_long = long_momentum[benchmark]
            benchmark_medium = medium_momentum[benchmark]
            residual_long = long_momentum.sub(beta.mul(benchmark_long, axis=0))
            residual_medium = medium_momentum.sub(beta.mul(benchmark_medium, axis=0))

        annualized_volatility = (
            returns.rolling(self.volatility_lookback).std() * np.sqrt(252)
        )
        downside_returns = returns.clip(upper=0)
        downside_volatility = (
            downside_returns.pow(2).rolling(self.volatility_lookback).mean().pow(0.5)
            * np.sqrt(252)
        )
        fast = close.rolling(self.fast_window).mean()
        slow = close.rolling(self.slow_window).mean()
        trend_strength = close.div(slow).sub(1).div(
            annualized_volatility.replace(0, np.nan)
        )

        price_score = (
            0.50 * _cross_sectional_zscore(residual_long)
            + 0.25 * _cross_sectional_zscore(residual_medium)
            + 0.15 * _cross_sectional_zscore(trend_strength)
            - 0.10 * _cross_sectional_zscore(downside_volatility)
        )
        quality_score = pd.DataFrame(0.0, index=close.index, columns=close.columns)
        earnings_score = pd.DataFrame(0.0, index=close.index, columns=close.columns)
        if self.quality_score_weight or self.earnings_score_weight:
            if self.external_features_file is None:
                raise ValueError("External features are required for quality or earnings scores.")
            features = pd.read_csv(self.external_features_file)
            quality_parts = [
                _cross_sectional_zscore(
                    _asof_feature_matrix(features, field, close)
                )
                * direction
                for field, direction in [
                    ("gross_profitability", 1),
                    ("cash_profitability", 1),
                    ("accruals", -1),
                    ("leverage", -1),
                    ("share_growth", -1),
                ]
            ]
            quality_score = sum(quality_parts) / len(quality_parts)
            earnings_parts = [
                _cross_sectional_zscore(
                    _asof_feature_matrix(features, field, close).clip(-5, 5)
                )
                for field in ["revenue_growth", "earnings_growth"]
            ]
            earnings_score = sum(earnings_parts) / len(earnings_parts)
        score = (
            self.price_score_weight * price_score
            + self.quality_score_weight * quality_score.fillna(0.0)
            + self.earnings_score_weight * earnings_score.fillna(0.0)
        )
        membership = self._membership(close)
        membership.loc[:, benchmark] = False
        eligible = (
            membership
            & long_momentum.gt(0)
            & close.gt(slow)
            & fast.gt(slow)
            & annualized_volatility.le(self.maximum_annualized_volatility)
            & score.notna()
        )
        breadth_denominator = membership.sum(axis=1).replace(0, np.nan)
        breadth = (membership & close.gt(slow)).sum(axis=1).div(breadth_denominator)

        selected: list[str] = []
        current_weights: dict[str, float] = {}
        rows: list[dict] = []
        benchmark_slow = slow[benchmark]
        benchmark_close = close[benchmark]
        for index, timestamp in enumerate(close.index):
            if index % self.rebalance_every == 0:
                ranked = (
                    score.loc[timestamp]
                    .where(eligible.loc[timestamp])
                    .replace([np.inf, -np.inf], np.nan)
                    .dropna()
                    .sort_values(ascending=False)
                )
                ranks = {symbol: rank for rank, symbol in enumerate(ranked.index, 1)}
                retained = [
                    symbol
                    for symbol in selected
                    if symbol in ranks and ranks[symbol] <= self.exit_rank
                ]
                candidate_pool = list(
                    dict.fromkeys(
                        retained
                        + list(ranked.head(max(self.exit_rank * 3, self.max_symbols * 5)).index)
                    )
                )
                window = returns.loc[:timestamp, candidate_pool].tail(
                    self.correlation_lookback
                )
                correlation = window.corr(min_periods=max(20, self.correlation_lookback // 2))

                selected = []
                sector_counts: dict[str, int] = {}
                for symbol in candidate_pool:
                    if len(selected) >= self.max_symbols:
                        break
                    sector = self.sector_map.get(symbol, symbol)
                    if sector_counts.get(sector, 0) >= self.max_per_sector:
                        continue
                    correlations = correlation.loc[symbol, selected] if selected else []
                    if len(selected) and pd.Series(correlations).gt(
                        self.max_pairwise_correlation
                    ).any():
                        continue
                    selected.append(symbol)
                    sector_counts[sector] = sector_counts.get(sector, 0) + 1
                if len(selected) < self.max_symbols:
                    for symbol in ranked.index:
                        sector = self.sector_map.get(symbol, symbol)
                        if (
                            symbol not in selected
                            and sector_counts.get(sector, 0) < self.max_per_sector
                        ):
                            selected.append(symbol)
                            sector_counts[sector] = sector_counts.get(sector, 0) + 1
                        if len(selected) >= self.max_symbols:
                            break

                gross = self._gross_exposure(
                    timestamp, benchmark_close, benchmark_slow, breadth
                )
                allocation_volatility = downside_volatility.loc[timestamp].reindex(selected)
                if self.weighting_method == "benchmark_aware":
                    day_score = score.loc[timestamp].reindex(selected).clip(-3, 3)
                    day_beta = beta.loc[timestamp].reindex(selected)
                    multiplier = np.exp(
                        self.score_tilt_strength * day_score
                        - self.beta_penalty * day_beta.sub(1.0).abs()
                    )
                    allocation_volatility = allocation_volatility.div(multiplier)
                weights = _capped_inverse_volatility_weights(
                    allocation_volatility,
                    gross,
                    self.max_position_weight,
                )
                covariance = (
                    window.reindex(columns=weights.index).cov(min_periods=20) * 252
                ).fillna(0.0)
                if len(weights):
                    vector = weights.reindex(covariance.index).to_numpy()
                    forecast_variance = float(vector @ covariance.to_numpy() @ vector)
                    forecast_volatility = np.sqrt(max(forecast_variance, 0.0))
                    if forecast_volatility > self.target_portfolio_volatility:
                        weights *= self.target_portfolio_volatility / forecast_volatility
                current_weights = {symbol: float(weight) for symbol, weight in weights.items()}
                selected = list(current_weights)

            for symbol in close.columns:
                value = score.at[timestamp, symbol]
                rows.append(
                    {
                        "timestamp": timestamp,
                        "symbol": symbol,
                        "target_weight": current_weights.get(symbol, 0.0),
                        "score": float(value) if pd.notna(value) else float("nan"),
                    }
                )
        return pd.DataFrame(rows, columns=TARGET_COLUMNS)
