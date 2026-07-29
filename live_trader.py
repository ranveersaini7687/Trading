#!/usr/bin/env python3
"""
Live Trader — Places actual orders on Angel One when conditions are met.

Entry condition : Model B >= 90 AND vol_ratio >= 1.5
Live sizing     : max 2 concurrent live trades per account, ₹10,000 alloc each
Live SL/Target  : -0.65% / +2%  (independent of paper trading's -1% / +2%)

Exits are enforced by two REAL resting orders placed right after entry
fills, using the same proven placeOrder endpoint as the BUY itself
(NOT Angel's GTT feature, which is broken — see angel_api.py's GTT
methods, kept dormant for when Angel fixes it):

  - SL side: a STOPLOSS_LIMIT SELL order (variety=STOPLOSS). This stays
    dormant on the exchange until price actually drops to the trigger,
    then fires — unlike a plain LIMIT sell below market price, which
    would execute immediately.
  - Target side: a plain LIMIT SELL order at the target price. Rests in
    the order book and fires automatically once price rises to it — no
    special order type needed since it's not marketable yet at entry.

Both orders sit on Angel's exchange and can fire without our bot
running. We still run a lightweight periodic reconciliation: check
both orders' status, and once either fills, cancel the untriggered
sibling (using the correct variety for each) and clear local tracking.
If either order's creation itself fails, that side falls back to our
own price polling + manual MARKET SELL (check_and_exit_positions).

Paper trading (paper_trader.py) is untouched — live orders are additive.
"""

import json
import os
import time
from datetime import datetime
from live_accounts import get_account_manager
from angel_api import AngelOneAPI

# How long to wait for a MARKET order to actually fill before falling
# back to the pre-trade estimated price for SL/target calculation.
FILL_CHECK_ATTEMPTS = 5
FILL_CHECK_DELAY_SEC = 1.5

# Buffer below the SL trigger for the STOPLOSS_LIMIT order's own limit
# price, so it's still marketable (fills) once triggered. Exchanges cap
# how far apart trigger and limit price may be — keep this small.
SL_LIMIT_BUFFER_PCT = 0.3


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

    def _wait_for_fill(self, angel, order_id, estimated_price):
        """
        Poll a just-placed MARKET order a few times for its actual fill
        price. Market orders can execute at a different price than our
        pre-trade estimate, especially in a fast-moving stock — SL/target
        should be computed off the real fill, not the stale estimate.
        Falls back to estimated_price if no fill is confirmed in time.
        """
        for _ in range(FILL_CHECK_ATTEMPTS):
            status = angel.get_order_status(order_id)
            if status.get("status") == "complete" and status.get("average_price"):
                return round(status["average_price"], 2), True
            time.sleep(FILL_CHECK_DELAY_SEC)
        log(f"  ⚠ Order {order_id} not confirmed filled after {FILL_CHECK_ATTEMPTS} checks — "
            f"using estimated price ₹{estimated_price:.2f} for SL/target")
        return estimated_price, False

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
    def _create_exit_orders(self, angel, symbol, qty, sl_price, target_price):
        """
        Place the two real resting exit orders for a fresh position:
        a STOPLOSS_LIMIT SELL for SL, a plain LIMIT SELL for target.
        Returns (sl_order_id, target_order_id) — either may be None if
        that order's placement failed, in which case the caller falls
        back to polling for that side.
        """
        sl_limit = round(sl_price * (1 - SL_LIMIT_BUFFER_PCT / 100), 2)

        if self.dry_run:
            log(f"  [DRY RUN] Would place STOPLOSS_LIMIT SELL @ trigger ₹{sl_price:.2f} (limit ₹{sl_limit:.2f})")
            log(f"  [DRY RUN] Would place LIMIT SELL (target) @ ₹{target_price:.2f}")
            return f"DRY_SL_{symbol}", f"DRY_TGT_{symbol}"

        sl_result = angel.place_stoploss_order(symbol, "SELL", qty, sl_price, sl_limit)
        if not sl_result["status"]:
            log(f"  ✗ SL order placement failed for {symbol}: {sl_result['message']} — falling back to polling for SL")
        sl_order_id = sl_result["order_id"] if sl_result["status"] else None

        tgt_result = angel.place_market_order(symbol, "SELL", qty, price=target_price)
        if not tgt_result["status"]:
            log(f"  ✗ Target order placement failed for {symbol}: {tgt_result['message']} — falling back to polling for target")
        tgt_order_id = tgt_result["order_id"] if tgt_result["status"] else None

        return sl_order_id, tgt_order_id

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

        try:
            angel = None
            fill_price = entry_price  # pre-trade estimate; refined below once filled
            filled_confirmed = False

            if self.dry_run:
                sl_price = round(entry_price * (1 - sl_pct / 100), 2)
                target_price = round(entry_price * (1 + tgt_pct / 100), 2)
                log(f"  [DRY RUN] Would BUY {qty} {symbol} at MARKET (est. ₹{entry_price:.2f}) "
                    f"(SL ₹{sl_price:.2f} / T ₹{target_price:.2f}) on account {acc['name']}")
                order_id = f"DRY_{symbol}_{datetime.now().timestamp()}"
            else:
                angel = self._angel_for(acc)

                # MARKET order — qty only, no price. A LIMIT order at our
                # pre-trade estimate can go unfilled if price has moved by
                # the time it reaches the exchange; MARKET always executes.
                result = angel.place_market_order(symbol, "BUY", qty)
                if not result["status"]:
                    log(f"  ✗ Live order failed for {symbol}: {result['message']}")
                    return {"placed": False, "reason": result["message"], "order_id": None}

                order_id = result["order_id"]
                log(f"  ✓ Live BUY (MARKET): {symbol} {qty}sh  [Order ID: {order_id}]  checking fill price...")

                fill_price, filled_confirmed = self._wait_for_fill(angel, order_id, entry_price)
                sl_price = round(fill_price * (1 - sl_pct / 100), 2)
                target_price = round(fill_price * (1 + tgt_pct / 100), 2)
                log(f"  ✓ {symbol} entry ₹{fill_price:.2f} "
                    f"({'confirmed fill' if filled_confirmed else 'estimated — unconfirmed'})  "
                    f"SL ₹{sl_price:.2f}  T ₹{target_price:.2f}")

            sl_order_id, tgt_order_id = None, None
            if self.dry_run:
                sl_order_id, tgt_order_id = self._create_exit_orders(angel, symbol, qty, sl_price, target_price)
            elif filled_confirmed:
                sl_order_id, tgt_order_id = self._create_exit_orders(angel, symbol, qty, sl_price, target_price)
            else:
                log(f"  ⚪ BUY fill unconfirmed for {symbol} — skipping exit-order placement, "
                    f"protected via polling instead")

            self.live_positions.setdefault(str(acc_id), {})[symbol] = {
                "order_id": order_id,
                "qty": qty,
                "entry_price": fill_price,
                "entry_price_confirmed": filled_confirmed,
                "stop_loss": sl_price,
                "target": target_price,
                "entry_time": datetime.now().isoformat(),
                "model_b": signal.get("model_b"),
                "vol_ratio": signal.get("vol_ratio"),
                "sl_order_id": sl_order_id,
                "target_order_id": tgt_order_id,
            }
            self._save_live_positions()

            self.mgr.log_live_order({
                "action": "BUY",
                "symbol": symbol,
                "qty": qty,
                "price": fill_price,
                "price_confirmed": filled_confirmed,
                "stop_loss": sl_price,
                "target": target_price,
                "sl_order_id": sl_order_id,
                "target_order_id": tgt_order_id,
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
        Used as the fallback path when a position's SL/target order
        failed to place (or died without filling). Also cancels any
        still-resting sibling order first — otherwise it would sit on
        the exchange trying to sell shares we no longer hold.
        """
        for acc_id, positions in list(self.live_positions.items()):
            if symbol not in positions:
                continue
            acc = self.mgr.get_account(int(acc_id))
            if not acc:
                continue

            pos = positions[symbol]

            try:
                fill_price = exit_price  # trigger-price estimate; refined below once filled
                filled_confirmed = False

                if self.dry_run:
                    log(f"  [DRY RUN] Would SELL {qty} {symbol} at MARKET (est. ₹{exit_price:.2f})  [{reason}]")
                    order_id = f"DRY_SELL_{symbol}_{datetime.now().timestamp()}"
                else:
                    angel = self._angel_for(acc)

                    # Cancel whichever sibling order is still resting, so it
                    # doesn't try to sell shares we're about to no longer hold.
                    if "SL HIT" in reason and pos.get("target_order_id"):
                        angel.cancel_order(pos["target_order_id"], variety="NORMAL")
                    elif "TARGET HIT" in reason and pos.get("sl_order_id"):
                        angel.cancel_order(pos["sl_order_id"], variety="STOPLOSS")

                    # MARKET order — guarantees execution even if price has
                    # moved past the SL/target trigger by the time it's sent.
                    result = angel.place_market_order(symbol, "SELL", qty)
                    if not result["status"]:
                        log(f"  ✗ Live sell order failed for {symbol}: {result['message']}")
                        return False
                    order_id = result["order_id"]
                    log(f"  ✓ Live SELL (MARKET): {symbol} {qty}sh  [{reason}]  [Order ID: {order_id}]  checking fill price...")

                    fill_price, filled_confirmed = self._wait_for_fill(angel, order_id, exit_price)
                    log(f"  ✓ {symbol} exit ₹{fill_price:.2f} "
                        f"({'confirmed fill' if filled_confirmed else 'estimated — unconfirmed'})")

                del self.live_positions[acc_id][symbol]
                if not self.live_positions[acc_id]:
                    del self.live_positions[acc_id]
                self._save_live_positions()

                self.mgr.log_live_order({
                    "action": "SELL",
                    "symbol": symbol,
                    "qty": qty,
                    "price": fill_price,
                    "price_confirmed": filled_confirmed,
                    "order_id": order_id,
                    "account": acc["name"],
                    "reason": reason,
                })
                return True
            except Exception as e:
                log(f"  ✗ Exception placing sell order: {e}")
                return False

        return False  # not an open live position

    def _clear_position_after_exit(self, acc_id, symbol, reason, exit_price):
        pos = self.live_positions.get(acc_id, {}).pop(symbol, None)
        if not self.live_positions.get(acc_id):
            self.live_positions.pop(acc_id, None)
        self._save_live_positions()
        if pos:
            self.mgr.log_live_order({
                "action": "SELL",
                "symbol": symbol,
                "qty": pos["qty"],
                "price": round(exit_price, 2),
                "reason": reason,
            })

    def reconcile_exit_orders(self):
        """
        Lightweight periodic check: for each position with a resting SL
        and/or target order, check their status. If one filled, cancel
        the untriggered sibling (correct variety — STOPLOSS for the SL
        order, NORMAL for the target order) and clear local tracking —
        the actual sell already happened on Angel's side. If an order
        died without filling (rejected/cancelled, e.g. exchange-side
        issue), null out its ID so check_and_exit_positions()'s polling
        fallback picks up protection for that specific side.
        """
        if self.dry_run or not self.live_positions:
            return

        for acc_id, positions in list(self.live_positions.items()):
            acc = self.mgr.get_account(int(acc_id))
            if not acc:
                continue
            for symbol, pos in list(positions.items()):
                sl_order_id = pos.get("sl_order_id")
                tgt_order_id = pos.get("target_order_id")
                if not sl_order_id and not tgt_order_id:
                    continue  # both sides already on polling fallback

                try:
                    angel = self._angel_for(acc)
                    sl_status = angel.get_order_status(sl_order_id) if sl_order_id else None
                    tgt_status = angel.get_order_status(tgt_order_id) if tgt_order_id else None

                    if sl_status and sl_status.get("status") == "complete":
                        if tgt_order_id:
                            angel.cancel_order(tgt_order_id, variety="NORMAL")
                        exit_price = sl_status.get("average_price") or pos["stop_loss"]
                        log(f"  ✓ SL order filled for {symbol} @ ₹{exit_price:.2f} — sibling target order cancelled")
                        self._clear_position_after_exit(acc_id, symbol, "SL HIT", exit_price)
                        continue

                    if tgt_status and tgt_status.get("status") == "complete":
                        if sl_order_id:
                            angel.cancel_order(sl_order_id, variety="STOPLOSS")
                        exit_price = tgt_status.get("average_price") or pos["target"]
                        log(f"  ✓ Target order filled for {symbol} @ ₹{exit_price:.2f} — sibling SL order cancelled")
                        self._clear_position_after_exit(acc_id, symbol, "TARGET HIT", exit_price)
                        continue

                    if sl_order_id and sl_status and sl_status.get("status") in ("rejected", "cancelled"):
                        log(f"  ⚠ SL order for {symbol} is {sl_status.get('status')} without filling — falling back to polling for SL")
                        pos["sl_order_id"] = None
                        self._save_live_positions()

                    if tgt_order_id and tgt_status and tgt_status.get("status") in ("rejected", "cancelled"):
                        log(f"  ⚠ Target order for {symbol} is {tgt_status.get('status')} without filling — falling back to polling for target")
                        pos["target_order_id"] = None
                        self._save_live_positions()

                except Exception as e:
                    log(f"  ⚠ Exit-order reconciliation error for {symbol}: {e}")

    def check_and_exit_positions(self, curr_prices):
        """
        Fallback exit path — per-side, not per-position: covers whichever
        of SL/target doesn't currently have a working resting order
        (placement failed at entry, or reconcile_exit_orders() detected
        it died without filling). Compares curr prices against our
        stored SL/target and places a manual MARKET SELL for that side.
        """
        if not self.live_positions:
            return

        to_close = []
        for acc_id, positions in self.live_positions.items():
            for symbol, pos in positions.items():
                curr = curr_prices.get(symbol)
                if curr is None:
                    continue
                if not pos.get("sl_order_id") and curr <= pos["stop_loss"]:
                    to_close.append((symbol, pos["qty"], curr, "SL HIT (fallback)"))
                elif not pos.get("target_order_id") and curr >= pos["target"]:
                    to_close.append((symbol, pos["qty"], curr, "TARGET HIT (fallback)"))

        for symbol, qty, curr, reason in to_close:
            self.place_sell_order(symbol, qty, curr, reason)


def get_live_trader():
    """Singleton live trader instance."""
    if not hasattr(get_live_trader, "_instance"):
        get_live_trader._instance = LiveTrader()
    return get_live_trader._instance
