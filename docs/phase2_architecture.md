# Phase 2 — System Architecture Blueprint

## Overview

**System:** EUR/USD Intraday Institutional Trading Bot  
**Architecture:** Hybrid — Python analytical/learning core + MQL5 execution EA  
**Communication:** Signed file-based IPC on shared VPS filesystem (no network IPC needed; both processes co-located)  
**Deployment:** Cloud VPS (Windows), 24/5 uptime  

```
┌────────────────────────────────────────────────────────────────┐
│                         PYTHON CORE                           │
│  (research, analysis, learning, safety, signal generation)    │
│                                                                │
│  [Market Data Engine] → [Indicator Engine] → [Bias Engine]    │
│  [Level Manager] → [Signal Engine] → [Trade Validation]       │
│  [Risk Manager] → [EA Signal Dispatcher]                      │
│                                                                │
│  [Backtesting Engine] ←→ [Walk-Forward Optimizer]             │
│  [Monte Carlo Analyzer] ↗                                      │
│  [Learning Engine] ↔ [Performance Analyzer] ↔ [Trade DB]      │
│  [Safety Monitor] (watchdog thread)                           │
│  [News Filter] [Session Filter] [Config Manager]              │
│  [Logger] [Dashboard]                                         │
└─────────────────────┬──────────────────────────────────────────┘
                      │  Signed file IPC
                      │  signal.json / config.json / heartbeat.txt
                      ▼
┌────────────────────────────────────────────────────────────────┐
│                      MQL5 EXECUTION EA                        │
│  (execution-only; reads config + signals, writes events)      │
│                                                                │
│  [EA Config Reader] → [EA Signal Receiver]                    │
│  [EA Trade Executor] → [EA Trade Monitor]                     │
│  [EA Safety Gate] [EA Heartbeat/Logger]                       │
└────────────────────────────────────────────────────────────────┘
```

---

## Module Catalogue

### Python Core — Data Layer

---

#### 1. Market Data Engine
**Responsibility:** Single source of truth for all price data. Fetches, normalizes, validates, and caches OHLCV bars for M5, M15, and H1 timeframes. Detects and flags data quality issues (gaps, outliers, weekend candles). Provides a unified read interface so all downstream modules never touch raw data files directly. Supports both live-feed mode (broker API or MT5 Python package) and historical-backtest mode (CSV/binary cache). Implements lazy loading — only loads the timeframe window actually needed by the requesting module.

---

#### 2. Indicator Engine
**Responsibility:** Computes all technical indicators used by the strategy on demand, with an in-memory LRU cache keyed by (symbol, timeframe, indicator, period, bar_time). Indicators computed:
- `ATR(5)` on M5 — fast avg-range for displacement detection (simple avg of (H-L) for 5 bars)
- `ATR(14)` on M5 — Wilder's for stop-buffer computation
- `ATR(14)` on H1 — for ADR(14) gateway (summed over 14 trading days)
- `EMA(20)` on H1 and M15 — for bias direction filter
- `VWAP` on M5, anchored daily at 00:00 UTC (tick-volume weighted)
- Swing-point detection: 2-candle fractal (bar high/low > both neighbors on each side), returns swing lists for BOS and MSS detection

---

#### 3. Bias Engine
**Responsibility:** Implements the §1 top-down bias logic. On each bar, determines H1 bias by finding the most recent H1 BOS (body-close beyond prior H1 swing high/low) and comparing current price to H1 EMA(20). Independently determines M15 bias. Emits one of three states: `BULLISH`, `BEARISH`, or `NEUTRAL`. If H1 and M15 disagree, emits `NEUTRAL`. Result is cached until the next H1 or M15 bar closes; does not recompute intra-bar. Also produces the daily pre-market "arm/disarm" decision.

---

#### 4. Level Manager
**Responsibility:** Maintains, persists, and updates all key price levels:
- Previous Day High/Low (PDH/PDL) — locked at 00:00 UTC daily
- Previous Week High/Low (PWH/PWL) — locked at Monday 00:00 UTC
- Asian session range High/Low — locked at 07:00 UTC
- Daily and Weekly Open — 00:00 UTC / Monday 00:00 UTC
- Daily VWAP — delegated to Indicator Engine, read-only reference here
- Equal highs/lows — dynamic, updated on each bar; tolerance = 2 pips
- OB/FVG zones — created by Signal Engine, stored here with mitigation status

Level mitigation tracking: a zone is marked `MITIGATED` when a bar body-closes beyond its far edge after formation. Mitigated zones are excluded from §7.8 filter. All levels and zones are persisted to the Trade DB so they survive process restarts.

---

#### 5. Session Filter
**Responsibility:** Determines whether the current UTC timestamp falls inside an approved trading window. Windows defined in UTC (no DST offset needed — UTC is invariant):
- London Kill Zone: 07:00–10:00 UTC
- London/NY Overlap: 12:00–15:00 UTC
- No entries: 15:00–20:00 UTC (runner management only)
- Hard close: 20:00 UTC (all positions must be flat)
- Friday cutoff: 15:00 UTC
- Holiday calendar: standard London + NY bank holidays (configurable list, reviewed at Phase 5)

Also enforces the "no entry on the first M5 candle of a move" rule (minimum 1 bar elapsed since session open before accepting a signal).

---

#### 6. News Filter
**Responsibility:** Fetches, parses, and caches the economic calendar for the current and next trading day. Sources high-impact (red-folder) EUR and USD events. On startup and at 00:00 UTC each day, refreshes the cache with a single HTTP request; uses the cached data for the entire day. Exposes `is_blackout(utc_time)` returning `True` if within ±15 minutes of any red event (±30 minutes for NFP/FOMC/ECB rate decision). Degrades gracefully on network failure by keeping the last successful cache and logging a warning. If cache is more than 25 hours old, raises a critical alert via Safety Monitor.

---

### Python Core — Strategy Layer

---

#### 7. Signal Engine
**Responsibility:** The faithful, mechanical implementation of the 9-gate entry checklist (§4). Runs on each new M5 bar close. Gates evaluated in order (short-circuit on first failure):

1. Bias aligned (from Bias Engine)
2. Inside approved session (from Session Filter)
3. Liquidity sweep: M5 low pierces a marked level by ≥ 2 pips AND closes back above it within 2 bars
4. Displacement: next M5 close has range ≥ 1.5 × ATR(5)
5. MSS: that impulse body-closes beyond the most recent opposing M5 swing point
6. OB and/or FVG zone present: OB = last down-close candle before displacement (open→low); FVG = 3-candle gap (candle-1 high < candle-3 low)
7. Retracement into zone without breaking sweep extreme
8. Confirmation candle: bullish close inside or above zone midpoint (Conservative mode, fixed for automation)
9. R:R ≥ 1:2 using structural stop and first logical target

Also assigns setup grade: `A+` if OB and FVG overlap, `STANDARD` otherwise. Emits a `Signal` dataclass containing all computed values (entry, stop, TP1, TP2, grade, sweep level name, zone bounds) or `None`. Never modifies any rule. Never stores state between signals except the last N M5 bars needed for computation (rolling window, minimal memory footprint).

---

#### 8. Trade Validation Engine
**Responsibility:** Cross-validates a `Signal` from the Signal Engine against all §7 invalidating filters before handing off to the Risk Manager. Checks (all must pass):
- News blackout not active (News Filter)
- Stop not in no-man's-land (stop distance ≤ 1.2 × ADR(14))
- Zone not already mitigated (Level Manager)
- Displacement was a body-close, not a wick
- ADR(14) ≥ 60 pips and intraday range < 80% ADR
- Structure is readable (no overlapping wick chaos — detected by measuring avg wick/body ratio over last 10 bars; if > 2:1, flag as choppy)
- Setup grade meets minimum bar (configurable: A+ only during first 100 trades, then Standard also allowed — gated by Learning Engine config)

Returns `VALID` or `REJECTED` with a rejection reason code for the logger.

---

### Python Core — Risk Layer

---

#### 9. Risk Manager
**Responsibility:** Enforces all capital-protection rules. Two roles: (a) gate controller and (b) position sizer.

**Gate controller** — checks all of the following on every signal pass-through. Any failure blocks the trade:
- Daily P/L ≤ −2% → `DAILY_LIMIT_HIT`
- Weekly P/L ≤ −5% → `WEEKLY_LIMIT_HIT`
- Monthly P/L ≤ −6% → `MONTHLY_LIMIT_HIT`
- Equity drawdown from peak ≥ 15% → `CIRCUIT_BREAKER` (requires manual reset)
- Consecutive losses ≥ 2 → `CONSEC_LOSS_LIMIT`
- Trades today ≥ 3 → `DAILY_TRADE_COUNT`
- Time ≥ 15:00 UTC → `SESSION_CLOSED`
- Volatility spike cooldown active → `VOLATILITY_COOLDOWN`
- Drawdown-based risk reduction mode → adjusts risk % before sizing (not a block, a modulation)

**Position sizer** — once gate passes:
```
current_risk_pct = base_risk_pct × drawdown_reduction_factor
lots = (equity × current_risk_pct) / (stop_pips × pip_value_per_lot)
lots = round_down(lots, broker_lot_step)
lots = min(lots, broker_max_lot)
```
Recomputed on every trade. Never reuses a previous value. Writes the exact risk % used to the trade record for audit.

---

### Python Core — Learning & Analysis Layer

---

#### 10. Performance Analyzer
**Responsibility:** Reads the Trade Journal DB and computes the full metrics suite defined in §11:
- Win rate, avg winner R, avg loser R, expectancy, profit factor
- Max drawdown (R and %), longest losing streak, longest winning streak
- Cost-per-trade audit (avg pips paid in spread + slippage)
- Bucket breakdowns: session × setup grade × day-of-week × bias-alignment
- Equity curve data, rolling 20/50/100-trade windows

Runs in a background thread on a configurable interval (default: after each new trade closes). Results are written to the DB's `analytics` table and to a structured JSON snapshot for the Dashboard. Never modifies trade records.

---

#### 11. Learning Engine
**Responsibility:** Analyzes Performance Analyzer output to propose safe-boundary parameter updates. Operates strictly within the frozen/adaptable boundary defined in Phase 1.

**What it can propose:**
- Disable a session/grade/day-of-week bucket with negative expectancy (requires ≥ 50 trades in that bucket; improvement must be ≥ 0.05R in out-of-sample expectancy; change is logged as `BUCKET_DISABLE`)
- Tighten the choppy-structure filter threshold if chop setups consistently lose (requires ≥ 30 such trades; change is logged as `FILTER_TIGHTEN`)
- Flag whether A+ or Standard grade carries positive expectancy independently, enabling/disabling Standard setups (requires ≥ 100 per grade)
- Adjust the ADR volatility-regime window (upper/lower ADR percentile filter) if certain ADR ranges show negative expectancy (requires ≥ 40 trades in range; change logged as `ADR_REGIME_FILTER`)

**What it cannot touch:** Any frozen rule (see Phase 1 boundary map). Enforced by code design — the Learning Engine has no write access to core signal parameters.

**Process per proposed change:**
1. Compute expectancy improvement on held-out trades (most-recent 30% of sample)
2. If improvement ≥ threshold: write to `pending_params` table with full rationale, sample sizes, before/after expectancy
3. After 24-hour review window (configurable), promote to `active_params` and write new signed config
4. Monitor next 50 trades against the change; if out-of-sample performance degrades below the pre-change baseline: automatic rollback, log `ROLLBACK_TRIGGERED`

---

#### 12. Backtesting Engine
**Responsibility:** Event-driven bar-replay backtester faithful to the rulebook's anti-look-ahead requirements. Processes M5 bars sequentially, one bar at a time, calling the same Signal Engine, Trade Validation, and Risk Manager instances used in live mode (zero code divergence — this is the key reliability guarantee). Simulates the 50%/BE/runner exit model including partial closes. Records every simulated trade to a separate `backtest_trades` table with a `backtest_id` tag. Supports:
- Full in-sample / out-of-sample split (configurable date boundary)
- Walk-forward window management (driven by Walk-Forward Optimizer)
- Spread/commission simulation per configurable pip value

---

#### 13. Walk-Forward Optimizer
**Responsibility:** Manages train/test window rolling for the Backtesting Engine. Iterates across date windows (default: 6-month train, 2-month test, 1-month step), runs backtests on each, aggregates metrics to detect regime stability. Feeds results to the Learning Engine's candidate parameter evaluation. Never outputs "optimized" parameters from a train window without confirming improvement on the test window. Generates a walk-forward efficiency report (ratio of out-of-sample to in-sample performance).

---

#### 14. Monte Carlo Analyzer
**Responsibility:** Takes a sequence of historical R-multiples and re-samples them with replacement N=10,000 times to estimate:
- 95th-percentile max drawdown
- Expected longest losing streak at 5% / 50% / 95% confidence
- Probability of ruin (equity dropping below configurable floor)
- Distribution of equity curves to quantify luck vs. skill in any sample

Used before position-size unlocks and after each 100-trade review. Results stored in `mc_analysis` table.

---

### Python Core — Infrastructure Layer

---

#### 15. Configuration Manager
**Responsibility:** Single owner of all system parameters. Loads from a versioned JSON config file on startup, validates schema strictly, and provides read-only access to all modules. When the Learning Engine proposes a parameter update, Config Manager generates a new versioned config, signs it with an HMAC key, writes it to the shared IPC directory for the EA to pick up, and appends the previous version to the immutable `config_history` table. Rolling back simply promotes an older version back to `active`. No module other than Config Manager and Learning Engine may write config values.

---

#### 16. Trade Journal Database
**Responsibility:** SQLite database (single-file, zero-dependency, VPS-portable). Schema mirrors the companion workbook exactly (Trade #, Date, Day, Session, Direction, Bias Aligned, Setup Grade, Sweep Level, Entry/Stop/TP2 Price, Risk%, Stop Pips, Planned RR, TP1 Hit, Runner Exit, Exit Time, Outcome, Modelled R, P/L%, Cum/Peak/DD R, Loss/Win Run, Costs Pips, Rule Violation flag, Screenshot path, Note). Additional tables:
- `signals` — every signal generated, including rejected ones (with rejection reason)
- `config_history` — immutable log of every config version
- `pending_params` / `active_params` — Learning Engine parameter lifecycle
- `analytics` — performance analyzer snapshots
- `mc_analysis` — Monte Carlo results
- `backtest_trades` — isolated from live trades

All writes go through a thin DAO layer that enforces schema constraints. Journal is the single source of truth for all P/L and performance metrics.

---

#### 17. Safety Monitor
**Responsibility:** Runs as a background thread (or separate process) checking system health on a 30-second heartbeat cycle. Checks:
- EA heartbeat file timestamp: if stale > 60 seconds → alert (Telegram/email)
- MT5 terminal process running: if dead → attempt restart script, then alert
- Account equity vs daily/weekly/monthly/drawdown limits (reads from broker API or MT5 account info): if any limit breached → write kill-flag file the EA reads immediately
- News cache age: if > 25 hours → critical alert
- Database write health: if last DB write is unexpectedly old → alert

Never modifies trade logic. Pure surveillance and alerting.

---

#### 18. Logger
**Responsibility:** Structured JSON logging (one line per event). Five log channels: `SIGNAL`, `TRADE`, `RISK`, `LEARNING`, `SYSTEM`. Each entry contains: timestamp (UTC ISO-8601), channel, severity (DEBUG/INFO/WARN/ERROR/AUDIT), module name, event code, and a payload dict. Severity AUDIT is immutable (never rotated, never deleted — covers all learning decisions, config changes, and rollbacks). Other channels rotate daily, retain 30 days. Log files are written asynchronously via a queue to avoid blocking the signal evaluation loop.

---

#### 19. Dashboard
**Responsibility:** Lightweight read-only status view. Implemented as a terminal CLI report (Phase 6 v1) or minimal Streamlit web page (Phase 6 v2 if requested). Displays: today's arm/disarm status, current bias, signals generated vs traded, open P/L, daily/weekly/monthly P/L vs limits, drawdown vs circuit-breaker level, last trade outcome, last Learning Engine action, EA heartbeat status. Reads exclusively from the Trade DB and analytics snapshots. No write path.

---

### MQL5 Execution EA

---

#### 20. EA Config Reader
**Responsibility:** On EA init and on a file-change poll every 60 seconds, reads the config JSON from the shared IPC path. Validates the HMAC signature against a pre-shared key embedded in the EA's compiled constants (not in a file). On signature failure, rejects the config, logs the event, and continues with the previous valid config. On schema version mismatch, logs an error and enters safe-mode (no new trades, hold any open positions). Exposes a typed struct of parameters to all other EA components.

---

#### 21. EA Signal Receiver
**Responsibility:** Polls the signal JSON file in the shared IPC directory on each M5 bar close (event-driven via OnTick with bar-change detection). Reads the signal, validates: (a) HMAC signature, (b) timestamp not older than 90 seconds (stale signal rejection), (c) signal matches current bias from Config. If valid, passes to Trade Executor. If invalid, logs and ignores. Clears the signal file after reading to prevent double-execution.

---

#### 22. EA Trade Executor
**Responsibility:** Converts a validated signal into MT5 orders using `OrderSend`. Implements: lot size from signal (pre-computed by Python Risk Manager), stop loss placed at sweep extreme + buffer (recomputed from live price at execution moment — uses the signal's stop pips as max, takes the better of the two), initial SL and TP1 price attached to the order. Implements retry logic (up to 3 attempts with 500ms backoff) on `ERR_REQUOTE` or `ERR_TRADE_TIMEOUT`. On failure after 3 retries: logs `EXECUTION_FAILED`, writes event file for Python Safety Monitor. Never enters if the Safety Gate check (§25) fails.

---

#### 23. EA Trade Monitor
**Responsibility:** Runs on every tick while a position is open. Manages the full exit lifecycle:
- TP1 partial close (50%) when price reaches TP1 price: uses `OrderClose` with half the volume
- BE move: immediately after TP1 fill, modifies the stop to entry price + 1 pip via `OrderModify`
- Trailing stop: on each new confirmed M5 swing low (using the same 2-candle fractal) that is higher than the current stop → modify stop to that new low (favorable direction only, never backwards)
- Dynamic invalidation: if M5 bar body-closes below sweep low/MSS level before TP1 → close at market immediately
- TP2: close remaining position when price reaches TP2 target or +3R in absolute terms
- Time stop: at 20:00 UTC, close all positions at market regardless of status

On each exit event, writes an execution-event JSON file that Python reads to update the Trade DB.

---

#### 24. EA Safety Gate
**Responsibility:** Read-only local kill-switch. Before any `OrderSend`, checks: (a) Python Safety Monitor kill-flag file exists → abort; (b) account equity loss today exceeds the daily limit read from config → abort; (c) position count ≥ 1 already (enforces single-position-at-a-time rule). All checks happen synchronously before order placement. No network calls. Zero latency path.

---

#### 25. EA Heartbeat / Logger
**Responsibility:** Writes a heartbeat timestamp to a shared file every 30 seconds (on a timer event). Writes all EA execution events (order placed, modified, closed, rejected, error) to a structured log file that Python reads for journal reconciliation. Log format matches Python Logger's JSON schema for easy parsing. No trade decisions are made here — pure telemetry.

---

## Inter-Process Communication (IPC) Layer

All communication between Python Core and MQL5 EA uses the local VPS filesystem. No sockets, no network calls between the two halves.

| File | Direction | Purpose |
|---|---|---|
| `ipc/signal.json` | Python → EA | Current trade signal (HMAC-signed, timestamped, one-shot) |
| `ipc/config.json` | Python → EA | System parameters and filter config (HMAC-signed, versioned) |
| `ipc/kill_flag.txt` | Python → EA | Emergency halt (Safety Monitor writes, EA reads on every tick) |
| `ipc/heartbeat.txt` | EA → Python | EA liveness timestamp (updated every 30s) |
| `ipc/exec_events/` | EA → Python | Order execution events for DB reconciliation (one JSON file per event) |

HMAC key is embedded as a compile-time constant in the EA and as an environment variable on the Python side (never in a file that could be committed or logged).

---

## Memory & Performance Design Principles

- **No tick-by-tick computation.** All heavy computation (indicators, bias, levels, signal evaluation) runs on M5 bar-close events only. Between bars, both sides are idle.
- **Rolling windows only.** Indicator Engine holds only the last N bars in memory needed for computation (max 200 M5 bars ≈ ~17 hours; H1/M15 hold 50 bars each). No full history in RAM.
- **LRU cache for indicators.** Repeated reads of the same indicator at the same bar return cached results without recomputation.
- **SQLite WAL mode.** Write-Ahead Logging prevents read/write contention between Logger, Performance Analyzer, and Dashboard reads.
- **Async logging.** Logger uses a `queue.Queue` and a dedicated writer thread so log I/O never blocks the signal evaluation loop.
- **Lazy imports.** Backtesting Engine, Walk-Forward Optimizer, and Monte Carlo Analyzer are not imported at runtime in live-trading mode — loaded on demand only when explicitly invoked.
- **Single thread for signal path.** The entire live signal-evaluation pipeline (Market Data → Signal Engine → Validation → Risk → Dispatch) runs on one thread. No race conditions on the critical path. Performance Analyzer, Safety Monitor, and Logger run on separate threads.
