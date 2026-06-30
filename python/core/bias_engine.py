from __future__ import annotations

from python.models.bar import BarSeries
from python.models.signal import BiasState
from python.core.indicator_engine import IndicatorEngine


class BiasEngine:
    """Determines H1 + M15 directional bias per §1 of the rulebook.

    Bias is BULLISH when:
      - The most recent H1 BOS was upward (price body-closed beyond the prior H1 swing high)
      - AND current price is above the H1 EMA(20)

    BEARISH = mirror. NEUTRAL if either condition fails or H1/M15 disagree.
    """

    def __init__(self, indicator_engine: IndicatorEngine, ema_period: int = 20) -> None:
        self._ie = indicator_engine
        self._ema_period = ema_period

    def compute(self, h1_series: BarSeries, m15_series: BarSeries) -> BiasState:
        h1_bias = self._timeframe_bias(h1_series)
        m15_bias = self._timeframe_bias(m15_series)
        if h1_bias == BiasState.NEUTRAL or m15_bias == BiasState.NEUTRAL:
            return BiasState.NEUTRAL
        if h1_bias != m15_bias:
            return BiasState.NEUTRAL
        return h1_bias

    def _timeframe_bias(self, series: BarSeries) -> BiasState:
        if len(series) < self._ema_period + 4:
            return BiasState.NEUTRAL

        ema_val = self._ie.ema(series, self._ema_period)
        current_close = series.latest().close

        swing_highs = self._ie.swing_highs(series, lookback=2)
        swing_lows = self._ie.swing_lows(series, lookback=2)

        last_bos_direction = self._last_bos(series, swing_highs, swing_lows)
        if last_bos_direction == "BULLISH" and current_close > ema_val:
            return BiasState.BULLISH
        if last_bos_direction == "BEARISH" and current_close < ema_val:
            return BiasState.BEARISH
        return BiasState.NEUTRAL

    def _last_bos(
        self,
        series: BarSeries,
        swing_highs: list[tuple[int, float]],
        swing_lows: list[tuple[int, float]],
    ) -> str:
        """Find the most recent break of structure by bar body-close."""
        bars = series.as_list()
        events: list[tuple[int, str]] = []

        for idx, sh_price in swing_highs:
            for i in range(idx + 1, len(bars)):
                if bars[i].close > sh_price:
                    events.append((i, "BULLISH"))
                    break

        for idx, sl_price in swing_lows:
            for i in range(idx + 1, len(bars)):
                if bars[i].close < sl_price:
                    events.append((i, "BEARISH"))
                    break

        if not events:
            return "NONE"

        latest = max(events, key=lambda x: x[0])
        return latest[1]
