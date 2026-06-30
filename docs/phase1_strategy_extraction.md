# Phase 1 — Strategy Extraction & Clarification Log

## Confirmed Answers

| Decision | Answer |
|---|---|
| Execution platform | **Hybrid** — Python research/learning/backtest engine + MQL5 execution EA |
| Broker / data feed | TBD — broker-agnostic interface; bind at implementation time |
| Deployment | Cloud VPS (Windows, near broker server) |
| News calendar source | Automated external feed (daily cached refresh) |
| Risk scaling model | **Flat fractional** — fixed % of current equity per trade, compounding by default |
| Additional MM layers | Monthly loss limit, max drawdown circuit breaker, drawdown-based risk reduction, volatility spike cooldown |

## Strategy Rule Defaults Applied

| # | Ambiguity | Resolved Default |
|---|---|---|
| 1 | Two ATR defs in rulebook | §4.4 displacement: **ATR(5)** = avg range of prior 5 M5 candles. §5 stop buffer: **Wilder ATR(14)** on M5. Intentionally separate. |
| 2 | TP2 pool priority | Nearest qualifying opposing pool ≥ +2R; cap at +3R if none qualifies sooner. |
| 3 | Equal highs/lows tolerance | **2 pips** (matches sweep-pierce tolerance in §4.3) |
| 4 | Zone mitigation test | Mitigated if price **body-closed** beyond the zone's far edge at any point after formation, prior to the current retest. |
| 5 | ADR(14) basis | **Trading days only** (Mon–Fri, weekends excluded) |
| 6 | VWAP volume source | **Broker tick-volume proxy** (standard FX practice) |
| 7 | Thin-liquidity holiday calendar | Standard London + NY bank holiday calendar, reviewed and approved by user at Phase 5. |
| 8 | DST / GMT session handling | Auto-convert via **fixed UTC anchor**; session windows defined in UTC, no manual quarterly flag. |

## Additional MM Parameters (proposed defaults — configurable)

| Parameter | Proposed Default | Notes |
|---|---|---|
| Monthly loss limit | **−6%** | 3× the daily limit; resets on calendar month boundary |
| Drawdown circuit breaker | **−15%** from equity peak | Full halt, requires manual reset via config flag |
| Drawdown risk-reduction trigger | At **−5% DD**: step to 0.25% risk; at **−10% DD**: to 0.125% | Recovers to prior level when drawdown retraces 50% |
| Volatility spike cooldown | If ADR(14) > 1.5× its own 20-day MA: **4-hour cooldown** after each completed trade | Guards against blow-off/news-spike regimes |

## Frozen vs. Adaptable (Self-Learning Boundary Map)

| Frozen — core strategy, never touched by learning engine | Adaptable — within logged, reversible, min-sample-gated bounds |
|---|---|
| All 9 entry gates (sweep, displacement, MSS, zone, confirmation, R:R) | Session/day-of-week/setup-grade bucket weighting |
| Stop placement formula (sweep extreme + ATR(14) buffer) | Confidence/probability score overlaid on qualifying setups |
| TP1/TP2 structure and partial-close rule (50% at +1R) | ADR volatility-regime band thresholds |
| BE-move rule (immediately on TP1 fill) | Spread/slippage tolerance gating |
| Risk % limits (0.5% baseline, 1.0% hard max) | Position size within the 0.25–1.0% envelope (never above) |
| All kill-switch rules (daily/weekly/monthly/consec/count) | Candidate entry filter flags (e.g. disable Asian-session OBs if data shows negative expectancy) |
| "No sweep, no trade" / "No body-close, no trade" | Cost-audit-driven trade frequency reduction |
