#!/usr/bin/env python3
"""
Live Trader — Places actual orders on Angel One when conditions are met.

Entry condition : Model B >= 90 AND vol_ratio >= 1.5
Live sizing     : max 2 concurrent live trades per account, ₹10,000 alloc each
Live SL/Target  : -0.65% / +2%  (independent of paper trading's -1% / +2%)

Exits are enforced by Angel One's own GTT (Good-Till-Triggered) rules —
two per position (SL-sell, target-sell), created at entry time so
protection is active from day one, including same-day. GTT rules live
on Angel's servers and fire even if our bot/VM is down. We still run a
lightweight periodic reconciliation to detect which rule fired and
cancel its untriggered sibling. If GTT rule creation fails for a
position, we fall back to polling curr_prices and placing a manual
SELL ourselves — so no position is ever left silently unprotected.

Paper trading (paper_trader.py) is untouched — live orders are additive.
"""

import json
import os
from datetime import datetime
from live_accounts import get_account_manager
from angel_api import AngelOneAPI

# GTT rule status values (see angel_api.get_gtt_rule_details docstring)
GTT_STILL_ACTIVE = {"NEW", "ACTIVE"}
GTT_TRIGGERED = {"SENTTOEXCHANGE"}

# Buffer below trigger price for the GTT's resulting limit order, so it
# actually fills once triggered instead of sitting unfilled.
GTT_LIMIT_BUFFER_PCT = 0.3


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


class LiveTrader:
    def __init__(self):
        self.mgr = get_account_manager()
        self.conditions = self.mgr.get_live_conditions()
        self.live_risk = self.mgr.get_live_risk()
        self.risk = self.mgr.get_risk_controls()
        self.dry_run = self.mgr.is_dry_run()
        self.live_positions = {}  # {account_id: {symbol: {...}}}
        self._load_live_positions()

    def _load_live_positions(self):
        if os.path.exists("live_positions.json"):
            try:
                with open("live_positions.json") as f:
                    self.live_positions = json.load(f)
            except Exception as e:
                log(f"⚠ Failed to load live positions: {e}")

    def _save_live_positions(self):
        try:
            with open("live_positions.json", "w") as f:
                json.dump(self.live_positions, f, indent=2)
        except Exception as e:
            log(f"⚠ Failed to save live positions: {e}")

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _all_open_symbols(self):
        """Flat set of symbols currently held live, across all accounts."""
        syms = set()
        for positions in self.live_positions.values():
            syms.update(positions.keys())
        return syms

    def get_live_symbols(self):
        return list(self._all_open_symbols())

    def _account_open_count(self, account_id):
        return len(self.live_positions.get(str(account_id), {}))

    def _angel_for(self, acc):
        angel = AngelOneAPI()
        angel.api_key = acc["api_key"]
        angel.client_id = acc["client_id"]
        angel.password = acc["password"]
        angel.totp_secret = acc["totp_secret"]
        angel.ensure_session()
        return angel

    def check_conditions(self, signal):
        """Entry gate: Model B >= 90 AND vol_ratio >= 1.5."""
        if not self.mgr.is_enabled():
            return False, "Live trading disabled"

        model_b = signal.get("model_b", 0) or 0
        vol_ratio = signal.get("vol_ratio", 0) or 0

        if model_b < self.conditions["model_b_min"]:
            return False, f"Model B {model_b} < {self.conditions['model_b_min']}"

        if vol_ratio < self.conditions["vol_ratio_min"]:
            return False, f"vol_ratio {vol_ratio} < {self.conditions['vol_ratio_min']}"

        return True, "Conditions met"

    def get_best_account(self, needed_capital):
        """
        Find an enabled account with room under its own per-account position
        cap AND enough capital headroom. Accounts are tried in config order,
        so an account fills up to its cap before the next one is used.
        """
        max_live = self.live_risk["max_live_positions_per_account"]
        any_capital_ok = False
        for acc in self.mgr.get_enabled_accounts():
            if needed_capital > acc["max_capital"]:
                continue
            any_capital_ok = True
            if self._account_open_count(acc["account_id"]) < max_live:
                return acc["account_id"], "OK"

        if not any_capital_ok:
            return None, "No account with sufficient capital"
        return None, f"All accounts at max live positions ({max_live} each)"

    # ── Entry ─────────────────────────────────────────────────────────────────
    def _create_exit_gtt_rules(self, angel, acc, symbol, qty, sl_price, target_price):
        """
        Create the SL-sell and target-sell GTT rules for a fresh position.
        Returns (sl_rule_id, target_rule_id) — either may be None if that
        rule's creation failed, in which case the caller should fall back
        to polling for that side.
        """
        sl_limit = round(sl_price * (1 - GTT_LIMIT_BUFFER_PCT / 100), 2)
        tgt_limit = round(target_price * (1 - GTT_LIMIT_BUFFER_PCT / 100), 2)

        if self.dry_run:
            log(f"  [DRY RUN] Would create GTT SL-sell @ trigger ₹{sl_price:.2f} (limit ₹{sl_limit:.2f})")
            log(f"  [DRY RUN] Would create GTT target-sell @ trigger ₹{target_price:.2f} (limit ₹{tgt_limit:.2f})")
            return f"DRY_GTT_SL_{symbol}", f"DRY_GTT_TGT_{symbol}"

        sl_result = angel.create_gtt_rule(symbol, "SELL", qty, sl_price, sl_limit)
        if not sl_result["status"]:
            log(f"  ✗ GTT SL rule creation failed for {symbol}: {sl_result['message']} — falling back to polling for SL")
        sl_rule_id = sl_result["rule_id"] if sl_result["status"] else None

        tgt_result = angel.create_gtt_rule(symbol, "SELL", qty, target_price, tgt_limit)
        if not tgt_result["status"]:
            log(f"  ✗ GTT target rule creation failed for {symbol}: {tgt_result['message']} — falling back to polling for target")
        tgt_rule_id = tgt_result["rule_id"] if tgt_result["status"] else None

        return sl_rule_id, tgt_rule_id

    def place_live_order(self, signal, entry_price):
        """
        Place a live BUY order sized to the live-trading risk rules
        (independent of the paper trade's own qty/allocation), then set
        up GTT-based exit protection active from day one.
        """
        symbol = signal.get("symbol")

        passes, reason = self.check_conditions(signal)
        if not passes:
            return {"placed": False, "reason": reason, "order_id": None}

        if symbol in self._all_open_symbols():
            return {"placed": False, "reason": f"{symbol} already has an open live position", "order_id": None}

        alloc = self.live_risk["alloc_per_trade"]
        if not entry_price or entry_price <= 0:
            return {"placed": False, "reason": "Invalid entry price", "order_id": None}

        qty = int(alloc // entry_price)
        if qty == 0:
            return {"placed": False, "reason": f"Entry price ₹{entry_price:.2f} > alloc ₹{alloc:,.0f}", "order_id": None}

        order_value = round(qty * entry_price, 2)
        min_order = self.risk.get("min_order_value", 1000)
        if order_value < min_order:
            return {"placed": False, "reason": f"Order value ₹{order_value:.0f} < min ₹{min_order}", "order_id": None}

        acc_id, reason = self.get_best_account(order_value)
        if not acc_id:
            return {"placed": False, "reason": reason, "order_id": None}

        acc = self.mgr.get_account(acc_id)
        if not acc:
            return {"placed": False, "reason": "Account not found", "order_id": None}

        sl_pct = self.live_risk["stop_loss_pct"]
        tgt_pct = self.live_risk["target_pct"]
        sl_price = round(entry_price * (1 - sl_pct / 100), 2)
        target_price = round(entry_price * (1 + tgt_pct / 100), 2)

        try:
            angel = None
            if self.dry_run:
                log(f"  [DRY RUN] Would BUY {qty} {symbol} @ ₹{entry_price:.2f} "
                    f"(SL ₹{sl_price:.2f} / T ₹{target_price:.2f}) on account {acc['name']}")
                order_id = f"DRY_{symbol}_{datetime.now().timestamp()}"
            else:
                angel = self._angel_for(acc)

                result = angel.place_market_order(symbol, "BUY", qty, price=entry_price)
                if not result["status"]:
                    log(f"  ✗ Live order failed for {symbol}: {result['message']}")
                    return {"placed": False, "reason": result["message"], "order_id": None}

                order_id = result["order_id"]
                log(f"  ✓ Live BUY: {symbol} {qty}sh @ ₹{entry_price:.2f}  "
                    f"SL ₹{sl_price:.2f}  T ₹{target_price:.2f}  [Order ID: {order_id}]")

            sl_rule_id, tgt_rule_id = self._create_exit_gtt_rules(
                angel, acc, symbol, qty, sl_price, target_price
            )

            self.live_positions.setdefault(str(acc_id), {})[symbol] = {
                "order_id": order_id,
                "qty": qty,
                "entry_price": entry_price,
                "stop_loss": sl_price,
                "target": target_price,
                "entry_time": datetime.now().isoformat(),
                "model_b": signal.get("model_b"),
                "vol_ratio": signal.get("vol_ratio"),
                "sl_rule_id": sl_rule_id,
                "target_rule_id": tgt_rule_id,
            }
            self._save_live_positions()

            self.mgr.log_live_order({
                "action": "BUY",
                "symbol": symbol,
                "qty": qty,
                "price": entry_price,
                "stop_loss": sl_price,
                "target": target_price,
                "sl_rule_id": sl_rule_id,
                "target_rule_id": tgt_rule_id,
                "order_id": order_id,
                "account": acc["name"],
                "model_b": signal.get("model_b"),
                "vol_ratio": signal.get("vol_ratio"),
            })

            return {"placed": True, "reason": "OK", "order_id": order_id}

        except Exception as e:
            log(f"  ✗ Exception placing live order: {e}")
            return {"placed": False, "reason": str(e), "order_id": None}

    # ── Exit (manual/fallback path) ─────────────────────────────────────────
    def place_sell_order(self, symbol, qty, exit_price, reason):
        """
        Place a manual live SELL order and clear the tracked position.
        Used as the fallback path when a position's GTT rules failed to
        create, and as the cleanup step after a GTT-triggered exit.
        """
        for acc_id, positions in list(self.live_positions.items()):
            if symbol not in positions:
                continue
            acc = self.mgr.get_account(int(acc_id))
            if not acc:
                continue

            try:
                if self.dry_run:
                    log(f"  [DRY RUN] Would SELL {qty} {symbol} @ ₹{exit_price:.2f}  [{reason}]")
                    order_id = f"DRY_SELL_{symbol}_{datetime.now().timestamp()}"
                else:
                    angel = self._angel_for(acc)
                    result = angel.place_market_order(symbol, "SELL", qty, price=exit_price)
                    if not result["status"]:
                        log(f"  ✗ Live sell order failed for {symbol}: {result['message']}")
                        return False
                    order_id = result["order_id"]
                    log(f"  ✓ Live SELL: {symbol} {qty}sh @ ₹{exit_price:.2f}  [{reason}]")

                del self.live_positions[acc_id][symbol]
                if not self.live_positions[acc_id]:
                    del self.live_positions[acc_id]
                self._save_live_positions()

                self.mgr.log_live_order({
                    "action": "SELL",
                    "symbol": symbol,
                    "qty": qty,
                    "price": exit_price,
                    "order_id": order_id,
                    "account": acc["name"],
                    "reason": reason,
                })
                return True
            except Exception as e:
                log(f"  ✗ Exception placing sell order: {e}")
                return False

        return False  # not an open live position

    def _clear_position_after_gtt(self, acc_id, symbol, reason):
        pos = self.live_positions.get(acc_id, {}).pop(symbol, None)
        if not self.live_positions.get(acc_id):
            self.live_positions.pop(acc_id, None)
        self._save_live_positions()
        if pos:
            self.mgr.log_live_order({
                "action": "SELL",
                "symbol": symbol,
                "qty": pos["qty"],
                "price": pos["stop_loss"] if reason == "SL HIT (GTT)" else pos["target"],
                "note": "Price is the GTT trigger level, not the confirmed fill price",
                "reason": reason,
            })

    def reconcile_gtt_positions(self):
        """
        Lightweight periodic check (rule status only, no price polling):
        for each position with GTT rules, see if either fired. If so,
        cancel the untriggered sibling and clear our local tracking —
        the actual sell already happened on Angel's side.
        """
        if self.dry_run or not self.live_positions:
            return

        for acc_id, positions in list(self.live_positions.items()):
            acc = self.mgr.get_account(int(acc_id))
            if not acc:
                continue
            for symbol, pos in list(positions.items()):
                sl_rule_id = pos.get("sl_rule_id")
                tgt_rule_id = pos.get("target_rule_id")
                if not sl_rule_id and not tgt_rule_id:
                    continue  # no GTT rules for this position — handled by polling fallback

                try:
                    angel = self._angel_for(acc)

                    sl_status = angel.get_gtt_rule_details(sl_rule_id)["status"] if sl_rule_id else None
                    tgt_status = angel.get_gtt_rule_details(tgt_rule_id)["status"] if tgt_rule_id else None

                    if sl_status in GTT_TRIGGERED:
                        if tgt_rule_id:
                            angel.cancel_gtt_rule(tgt_rule_id, symbol)
                        log(f"  ✓ GTT SL triggered for {symbol} — sibling target rule cancelled")
                        self._clear_position_after_gtt(acc_id, symbol, "SL HIT (GTT)")
                    elif tgt_status in GTT_TRIGGERED:
                        if sl_rule_id:
                            angel.cancel_gtt_rule(sl_rule_id, symbol)
                        log(f"  ✓ GTT target triggered for {symbol} — sibling SL rule cancelled")
                        self._clear_position_after_gtt(acc_id, symbol, "TARGET HIT (GTT)")
                    elif (sl_status and sl_status not in GTT_STILL_ACTIVE and sl_status not in GTT_TRIGGERED) or \
                         (tgt_status and tgt_status not in GTT_STILL_ACTIVE and tgt_status not in GTT_TRIGGERED):
                        log(f"  ⚠ {symbol}: unrecognized GTT status (sl={sl_status}, target={tgt_status}) "
                            f"— left as-is for manual review, not auto-cleared")
                except Exception as e:
                    log(f"  ⚠ GTT reconciliation error for {symbol}: {e}")

    def check_and_exit_positions(self, curr_prices):
        """
        Fallback exit path — only for positions where GTT rule creation
        failed at entry (no sl_rule_id/target_rule_id). Compares curr
        prices against our own stored SL/target and places a manual SELL.
        Positions with working GTT rules are handled by
        reconcile_gtt_positions() instead, not here.
        """
        if not self.live_positions:
            return

        to_close = []
        for acc_id, positions in self.live_positions.items():
            for symbol, pos in positions.items():
                if pos.get("sl_rule_id") or pos.get("target_rule_id"):
                    continue  # protected by GTT — reconciled separately

                curr = curr_prices.get(symbol)
                if curr is None:
                    continue
                if curr <= pos["stop_loss"]:
                    to_close.append((symbol, pos["qty"], curr, "SL HIT (fallback)"))
                elif curr >= pos["target"]:
                    to_close.append((symbol, pos["qty"], curr, "TARGET HIT (fallback)"))

        for symbol, qty, curr, reason in to_close:
            self.place_sell_order(symbol, qty, curr, reason)


def get_live_trader():
    """Singleton live trader instance."""
    if not hasattr(get_live_trader, "_instance"):
        get_live_trader._instance = LiveTrader()
    return get_live_trader._instance
