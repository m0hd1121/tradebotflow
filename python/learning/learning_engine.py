from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import NamedTuple

from python.infra.config_manager import ConfigManager
from python.infra.database import TradeJournalDAO
from python.infra.logger import BotLogger
from python.learning.performance_analyzer import PerformanceAnalyzer
from python.models.config import LearningConfig


class Proposal(NamedTuple):
    proposal_type: str
    description: str
    param_key: str
    param_value: str
    before_expectancy: float
    after_expectancy: float
    sample_size: int


class LearningEngine:
    """Analyzes trade buckets and proposes safe-boundary parameter updates.

    Operates strictly within the frozen/adaptable boundary defined in Phase 1.
    Never touches core entry logic, stop placement, or risk limits.
    """

    def __init__(
        self,
        analyzer: PerformanceAnalyzer,
        config_manager: ConfigManager,
        dao: TradeJournalDAO,
        log: BotLogger,
        cfg: LearningConfig,
    ) -> None:
        self._analyzer = analyzer
        self._cm = config_manager
        self._dao = dao
        self._log = log
        self._cfg = cfg
        self._pending_proposal: Proposal | None = None
        self._proposal_timestamp: datetime | None = None
        self._last_cycle_trade_count = 0
        self._monitoring_since: int | None = None

    def check_trigger(self, total_closed_trades: int) -> bool:
        new_trades = total_closed_trades - self._last_cycle_trade_count
        return new_trades >= self._cfg.min_trades_per_cycle

    def run_cycle(self) -> str:
        self._log.info("learning", "Learning cycle started")
        closed = self._dao.get_closed_trades(is_backtest=False)
        if len(closed) < self._cfg.min_trades_per_cycle:
            return "INSUFFICIENT_TRADES"

        split = int(len(closed) * (1 - self._cfg.out_of_sample_pct))
        in_sample = closed[:split]
        out_sample = closed[split:]

        if not out_sample:
            return "INSUFFICIENT_OOS"

        full_snapshot = self._analyzer.run()
        base_expectancy = full_snapshot.get("expectancy_r", 0.0)

        proposals = self._analyze_buckets(in_sample, out_sample, base_expectancy)
        if not proposals:
            self._log.audit("LEARNING_NO_ACTION", {"base_expectancy": base_expectancy})
            self._last_cycle_trade_count = len(closed)
            return "NO_ACTION"

        best = max(proposals, key=lambda p: p.after_expectancy - p.before_expectancy)
        if best.after_expectancy - best.before_expectancy < self._cfg.min_improvement_r:
            self._log.audit("LEARNING_NO_ACTION", {"best_improvement": best.after_expectancy - best.before_expectancy})
            self._last_cycle_trade_count = len(closed)
            return "IMPROVEMENT_BELOW_THRESHOLD"

        self._pending_proposal = best
        self._proposal_timestamp = datetime.now(timezone.utc)
        self._dao._conn().__enter__().execute(
            """INSERT INTO pending_params
               (proposed_at, proposal_type, description, before_expectancy,
                after_expectancy, sample_size, details_json, status)
               VALUES (?,?,?,?,?,?,?,'PENDING')""",
            (
                self._proposal_timestamp.isoformat(), best.proposal_type,
                best.description, best.before_expectancy, best.after_expectancy,
                best.sample_size, json.dumps({"param_key": best.param_key, "param_value": best.param_value}),
            )
        )
        self._log.audit("LEARNING_PROPOSAL", {
            "type": best.proposal_type, "description": best.description,
            "before": best.before_expectancy, "after": best.after_expectancy,
        })
        self._last_cycle_trade_count = len(closed)
        return "PROPOSAL_PENDING"

    def apply_pending_if_ready(self) -> bool:
        if not self._pending_proposal or not self._proposal_timestamp:
            return False
        elapsed = (datetime.now(timezone.utc) - self._proposal_timestamp).total_seconds() / 3600
        if elapsed < self._cfg.review_window_hours:
            return False

        p = self._pending_proposal
        cfg = self._cm.get()

        if p.param_key == "disable_session" and hasattr(cfg.strategy, "disabled_sessions"):
            pass  # Applied via DAO param store for validation engine to read
        self._dao.set_param(p.param_key, p.param_value, cfg.version)
        self._cm.write_ea_config()

        self._log.audit("PARAM_UPDATE_ACTIVE", {
            "param_key": p.param_key, "param_value": p.param_value,
            "before_expectancy": p.before_expectancy, "after_expectancy": p.after_expectancy,
        })
        self._monitoring_since = self._dao.get_closed_trades().__len__()
        self._pending_proposal = None
        return True

    def check_rollback(self) -> bool:
        if self._monitoring_since is None:
            return False
        current_closed = self._dao.get_closed_trades(is_backtest=False)
        new_since = current_closed[self._monitoring_since:]
        if len(new_since) < self._cfg.rollback_monitor_trades:
            return False

        r_vals = [t.get("r_used", t.get("modelled_r", 0)) for t in new_since]
        wins = [r for r in r_vals if r > 0]
        losses = [r for r in r_vals if r < 0]
        win_rate = len(wins) / len(r_vals) if r_vals else 0
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 1
        new_expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss

        full = self._analyzer.run()
        baseline = full.get("expectancy_r", 0.0)

        if new_expectancy < baseline - 0.05:
            self._log.audit("ROLLBACK_TRIGGERED", {
                "new_expectancy": new_expectancy, "baseline": baseline,
            })
            self._cm.rollback_to_version(self._cm.get().version - 1)
            self._monitoring_since = None
            return True
        return False

    def _analyze_buckets(
        self, in_sample: list[dict], out_sample: list[dict], base_expectancy: float
    ) -> list[Proposal]:
        proposals: list[Proposal] = []

        for bucket_key in ("session", "setup_grade", "day_of_week"):
            groups: dict[str, list[dict]] = {}
            for t in in_sample:
                val = t.get(bucket_key, "unknown")
                groups.setdefault(val, []).append(t)

            for val, group_trades in groups.items():
                if len(group_trades) < self._cfg.min_trades_per_bucket:
                    continue
                r_vals = [t.get("r_used", t.get("modelled_r", 0)) for t in group_trades]
                wins = [r for r in r_vals if r > 0]
                losses = [r for r in r_vals if r < 0]
                win_rate = len(wins) / len(r_vals) if r_vals else 0
                avg_win = sum(wins) / len(wins) if wins else 0
                avg_loss = abs(sum(losses) / len(losses)) if losses else 1
                expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss

                if expectancy >= 0:
                    continue

                oos_without = [
                    t for t in out_sample if t.get(bucket_key) != val
                ]
                if not oos_without:
                    continue
                oos_r = [t.get("r_used", t.get("modelled_r", 0)) for t in oos_without]
                oos_wins = [r for r in oos_r if r > 0]
                oos_losses = [r for r in oos_r if r < 0]
                oos_wr = len(oos_wins) / len(oos_r) if oos_r else 0
                oos_aw = sum(oos_wins) / len(oos_wins) if oos_wins else 0
                oos_al = abs(sum(oos_losses) / len(oos_losses)) if oos_losses else 1
                oos_expectancy = oos_wr * oos_aw - (1 - oos_wr) * oos_al

                if oos_expectancy > base_expectancy + self._cfg.min_improvement_r:
                    proposals.append(Proposal(
                        proposal_type=f"DISABLE_BUCKET:{bucket_key}",
                        description=f"Disable {bucket_key}={val}; in-sample expectancy={expectancy:.3f}R",
                        param_key=f"disabled_{bucket_key}_{val}",
                        param_value="true",
                        before_expectancy=base_expectancy,
                        after_expectancy=oos_expectancy,
                        sample_size=len(group_trades),
                    ))
        return proposals
