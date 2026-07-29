#!/usr/bin/env python3
"""
Angel One SmartAPI wrapper.
Handles login (TOTP), scrip master token map, equity LTP quotes,
and NFO option chain PCR calculation — all without NSE scraping.

Required env vars:
  ANGEL_API_KEY     — from smartapi.angelone.in
  ANGEL_CLIENT_ID   — your Angel One client/login ID
  ANGEL_PASSWORD    — your Angel One PIN
  ANGEL_TOTP_SECRET — base32 TOTP secret from Angel One 2FA setup
"""

import os
import time
import requests
from datetime import date, datetime, timedelta

ANGEL_BASE       = "https://apiconnect.angelone.in"
SCRIP_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"


class AngelOneAPI:
    def __init__(self):
        self.api_key     = os.environ.get("ANGEL_API_KEY", "")
        self.client_id   = os.environ.get("ANGEL_CLIENT_ID", "")
        self.password    = os.environ.get("ANGEL_PASSWORD", "")
        self.totp_secret = os.environ.get("ANGEL_TOTP_SECRET", "")
        self.jwt_token   = None
        self._token_date = None
        self._token_map  = None   # NSE symbol -> symbolToken
        self._index_map  = None   # index name -> symbolToken (e.g. NIFTY)
        self._nfo_map    = None   # {name: [{token, expiry, strike, optiontype}]}

    # ── Headers ───────────────────────────────────────────────────────────────
    def _base_headers(self):
        return {
            "Content-Type":     "application/json",
            "Accept":           "application/json",
            "X-UserType":       "USER",
            "X-SourceID":       "WEB",
            "X-ClientLocalIP":  "127.0.0.1",
            "X-ClientPublicIP": "127.0.0.1",
            "X-MACAddress":     "fe:01:02:03:04:05",
            "X-PrivateKey":     self.api_key,
        }

    def _auth_headers(self):
        return {**self._base_headers(), "Authorization": f"Bearer {self.jwt_token}"}

    # ── Auth ─────────────────────────────────────────────────────────────────
    def login(self):
        import pyotp
        if not all([self.api_key, self.client_id, self.password, self.totp_secret]):
            raise EnvironmentError(
                "Angel One credentials missing. Set ANGEL_API_KEY, ANGEL_CLIENT_ID, "
                "ANGEL_PASSWORD, ANGEL_TOTP_SECRET in .env"
            )
        totp = pyotp.TOTP(self.totp_secret).now()
        resp = requests.post(
            f"{ANGEL_BASE}/rest/auth/angelbroking/user/v1/loginByPassword",
            headers=self._base_headers(),
            json={"clientcode": self.client_id, "password": self.password, "totp": totp},
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("status") or not body.get("data"):
            raise Exception(f"Angel One login failed: {body.get('message', body)}")
        self.jwt_token   = body["data"]["jwtToken"]
        self._token_date = date.today()
        print(f"  [angel] Logged in ✓")

    def ensure_session(self):
        if self._token_date != date.today() or not self.jwt_token:
            self.login()

    # ── Scrip master ─────────────────────────────────────────────────────────
    def _load_scrip_master(self):
        """Download scrip master and build token maps."""
        print("  [angel] Downloading scrip master (~5 MB)...")
        resp = requests.get(SCRIP_MASTER_URL, timeout=60)
        resp.raise_for_status()
        master = resp.json()

        token_map = {}
        index_map = {}
        nfo_map   = {}

        for item in master:
            seg   = item.get("exch_seg", "")
            itype = item.get("instrumenttype", "")

            # NSE equities
            if seg == "NSE" and itype in ("", "EQ"):
                sym = item.get("symbol", "").replace("-EQ", "").replace("-BE", "").strip()
                if sym:
                    token_map[sym] = str(item["token"])

            # NSE indices (Nifty 50 for macro direction capture)
            elif seg == "NSE" and itype == "AMXIDX":
                name = (item.get("name") or item.get("symbol") or "").strip().upper()
                if name in ("NIFTY", "NIFTY 50", "NIFTY50"):
                    index_map["NIFTY"] = str(item["token"])

            # NFO stock options (OPTSTK)
            elif seg == "NFO" and itype == "OPTSTK":
                name   = item.get("name", "").strip()
                expiry = item.get("expiry", "").strip()
                strike = item.get("strike", "0")
                otype  = item.get("symbol", "")[-2:]   # last 2 chars: CE or PE
                token  = str(item["token"])
                if name and expiry and otype in ("CE", "PE"):
                    if name not in nfo_map:
                        nfo_map[name] = []
                    nfo_map[name].append({
                        "token":      token,
                        "expiry":     expiry,
                        "strike":     float(strike) / 100.0,  # scrip master stores paise (×100)
                        "optiontype": otype,
                    })

        self._token_map = token_map
        self._index_map = index_map
        self._nfo_map   = nfo_map
        print(f"  [angel] Scrip master loaded: {len(token_map)} NSE  |  {len(nfo_map)} NFO names"
              f"  |  {len(index_map)} indices")

    def _load_token_map(self):
        self._load_scrip_master()

    # ── Equity LTP quotes ─────────────────────────────────────────────────────
    def get_quotes(self, symbols):
        """
        Fetch LTP + OHLC + volume for a list of NSE equity symbols.
        Returns: {symbol: {"ltp", "open", "high", "low", "prev_close", "volume"}}
        """
        self.ensure_session()
        if not self._token_map:
            self._load_scrip_master()

        sym_to_tok = {s: self._token_map[s] for s in symbols if s in self._token_map}
        tok_to_sym = {v: k for k, v in sym_to_tok.items()}
        missing    = [s for s in symbols if s not in self._token_map]
        if missing:
            print(f"  [angel] No token for {len(missing)} symbol(s): {', '.join(missing[:5])}"
                  + (" ..." if len(missing) > 5 else ""))

        results = {}
        tokens  = list(sym_to_tok.values())

        for i in range(0, len(tokens), 50):
            batch = tokens[i:i+50]
            resp  = requests.post(
                f"{ANGEL_BASE}/rest/secure/angelbroking/market/v1/quote/",
                headers=self._auth_headers(),
                json={"mode": "FULL", "exchangeTokens": {"NSE": batch}},
                timeout=15,
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("status") and body.get("data"):
                for item in body["data"].get("fetched", []):
                    sym = tok_to_sym.get(str(item.get("symbolToken", "")))
                    if sym:
                        results[sym] = {
                            "ltp":        float(item.get("ltp")        or 0),
                            "open":       float(item.get("open")       or 0),
                            "high":       float(item.get("high")       or 0),
                            "low":        float(item.get("low")        or 0),
                            "prev_close": float(item.get("close")      or 0),
                            "volume":     int(item.get("tradeVolume")  or 0),
                        }
        return results

    # ── EMA stack (9 / 21 / 50) ──────────────────────────────────────────────
    @staticmethod
    def _ema(closes, period):
        if len(closes) < period:
            return None
        k   = 2 / (period + 1)
        ema = sum(closes[:period]) / period   # seed with SMA
        for p in closes[period:]:
            ema = p * k + ema * (1 - k)
        return round(ema, 2)

    def get_ema_stack(self, symbols):
        """
        Returns {symbol: {"ema9": float, "ema21": float, "ema50": float, "passes": bool}}
        'passes' = current price > ema9 > ema21 > ema50
        Fetches 90 calendar days of daily candles (gives ~63 trading days, enough for EMA50).
        """
        self.ensure_session()
        if not self._token_map:
            self._load_scrip_master()

        from_date = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d %H:%M")
        to_date   = datetime.now().strftime("%Y-%m-%d %H:%M")
        result    = {}

        for sym in symbols:
            time.sleep(0.35)   # Angel One historical API rate limit
            token = self._token_map.get(sym)
            if not token:
                print(f"  [angel] EMA: no token for {sym}")
                result[sym] = {"ema9": None, "ema21": None, "ema50": None, "passes": False}
                continue
            try:
                resp = requests.post(
                    f"{ANGEL_BASE}/rest/secure/angelbroking/historical/v1/getCandleData",
                    headers=self._auth_headers(),
                    json={
                        "exchange":    "NSE",
                        "symboltoken": token,
                        "interval":    "ONE_DAY",
                        "fromdate":    from_date,
                        "todate":      to_date,
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                candles = resp.json().get("data", [])
                closes  = [c[4] for c in candles if c[4] > 0]   # index 4 = close
                if not closes:
                    print(f"  [angel] EMA: no candle data returned for {sym} (token={token})")
                    result[sym] = {"ema9": None, "ema21": None, "ema50": None, "passes": False}
                    continue
                price = closes[-1]   # latest close (today's)
                ema9  = self._ema(closes, 9)
                ema21 = self._ema(closes, 21)
                ema50 = self._ema(closes, 50)
                passes = (
                    ema9 is not None and ema21 is not None and ema50 is not None
                    and price > ema9 > ema21 > ema50
                )
                result[sym] = {"ema9": ema9, "ema21": ema21, "ema50": ema50, "passes": passes}
            except Exception as e:
                print(f"  [angel] EMA fetch failed for {sym}: {e}")
                result[sym] = {"ema9": None, "ema21": None, "ema50": None, "passes": False}

        return result

    # ── 20-day average volume ─────────────────────────────────────────────────
    def get_avg_volumes(self, symbols):
        """
        Fetch 20-day average daily volume for a list of NSE equity symbols.
        Returns: {symbol: avg_volume_int}
        """
        self.ensure_session()
        if not self._token_map:
            self._load_scrip_master()

        from_date = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d %H:%M")
        to_date   = datetime.now().strftime("%Y-%m-%d %H:%M")
        result    = {}

        for sym in symbols:
            time.sleep(0.35)   # Angel One historical API rate limit
            token = self._token_map.get(sym)
            if not token:
                print(f"  [angel] AvgVol: no token for {sym}")
                result[sym] = 0
                continue
            try:
                resp = requests.post(
                    f"{ANGEL_BASE}/rest/secure/angelbroking/historical/v1/getCandleData",
                    headers=self._auth_headers(),
                    json={
                        "exchange":    "NSE",
                        "symboltoken": token,
                        "interval":    "ONE_DAY",
                        "fromdate":    from_date,
                        "todate":      to_date,
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                candles = resp.json().get("data", [])
                vols = [c[5] for c in candles[:-1] if c[5] > 0]  # exclude today's partial
                vols = vols[-20:]
                result[sym] = int(sum(vols) / len(vols)) if vols else 0
            except Exception as e:
                print(f"  [angel] AvgVol fetch failed for {sym}: {e}")
                result[sym] = 0

        return result

    # ── NFO option chain PCR ──────────────────────────────────────────────────
    def get_pcr(self, symbol):
        """
        Calculate PCR and liquidity from Angel One NFO option chain data.
        Uses near-month expiry + ATM ±15% strikes.
        Returns dict with pcr, ce_oi, pe_oi, total_oi, is_liquid (or None if unavailable).
        """
        self.ensure_session()
        if not self._nfo_map:
            self._load_scrip_master()

        instruments = self._nfo_map.get(symbol, [])
        if not instruments:
            return None

        # Find nearest expiry
        def parse_expiry(e):
            try:
                return datetime.strptime(e, "%d%b%Y")
            except Exception:
                return datetime.max

        expiries = sorted({i["expiry"] for i in instruments}, key=parse_expiry)
        # Use the nearest expiry that hasn't already passed
        today = datetime.now().date()
        near_expiry = None
        for exp in expiries:
            exp_date = parse_expiry(exp).date()
            if exp_date >= today:
                near_expiry = exp
                break
        if not near_expiry:
            near_expiry = expiries[0] if expiries else None
        if not near_expiry:
            return None

        near_opts = [
            i for i in instruments
            if i["expiry"] == near_expiry
        ]

        if not near_opts:
            return None

        # Fetch OI for all near-month ATM options in one batch call
        tokens    = [i["token"] for i in near_opts]
        tok_meta  = {i["token"]: i for i in near_opts}

        ce_oi = pe_oi = 0
        for i in range(0, len(tokens), 50):
            batch = tokens[i:i+50]
            resp  = requests.post(
                f"{ANGEL_BASE}/rest/secure/angelbroking/market/v1/quote/",
                headers=self._auth_headers(),
                json={"mode": "FULL", "exchangeTokens": {"NFO": batch}},
                timeout=15,
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("status") and body.get("data"):
                for item in body["data"].get("fetched", []):
                    tok = str(item.get("symbolToken", ""))
                    meta = tok_meta.get(tok)
                    oi   = int(item.get("opnInterest") or item.get("openInterest") or 0)
                    if meta:
                        if meta["optiontype"] == "CE":
                            ce_oi += oi
                        else:
                            pe_oi += oi

        pcr       = round(pe_oi / ce_oi, 2) if ce_oi > 0 else None
        total_oi  = ce_oi + pe_oi
        is_liquid = total_oi >= 500
        return {
            "pcr": pcr,
            "ce_oi": ce_oi,
            "pe_oi": pe_oi,
            "total_oi": total_oi,
            "is_liquid": is_liquid,
        }

    def get_index_quote(self, index_name="NIFTY"):
        """Fetch LTP and prev close for an NSE index."""
        self.ensure_session()
        if self._index_map is None:
            self._load_scrip_master()
        token = (self._index_map or {}).get(index_name)
        if not token:
            return None
        resp = requests.post(
            f"{ANGEL_BASE}/rest/secure/angelbroking/market/v1/quote/",
            headers=self._auth_headers(),
            json={"mode": "FULL", "exchangeTokens": {"NSE": [token]}},
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("status") or not body.get("data"):
            return None
        fetched = body["data"].get("fetched", [])
        if not fetched:
            return None
        item = fetched[0]
        ltp = float(item.get("ltp") or 0)
        prev_close = float(item.get("close") or 0)
        if not ltp or not prev_close:
            return None
        chg_pct = round((ltp - prev_close) / prev_close * 100, 2)
        return {"nifty_ltp": round(ltp, 2), "nifty_chg_pct": chg_pct}

    # ── Live order placement ──────────────────────────────────────────────────
    @staticmethod
    def _parse_order_response(resp):
        """
        Parse an Angel One order-API response, preferring Angel's own JSON
        'message' field (e.g. an RMS/insufficient-funds rejection) over the
        generic HTTP status text — Angel returns a JSON body with error
        details even on 4xx responses.
        """
        try:
            body = resp.json()
        except ValueError:
            resp.raise_for_status()
            raise Exception(f"Non-JSON response (HTTP {resp.status_code})")
        return body

    def place_market_order(self, symbol, order_type, qty, price=None):
        """
        Place a market/limit order on Angel One (NSE cash, intraday).

        Args:
            symbol (str): NSE symbol (e.g. 'INFY')
            order_type (str): 'BUY' or 'SELL'
            qty (int): quantity
            price (float, optional): limit price; if None, market order

        Returns:
            dict with keys: {"status": bool, "order_id": str, "message": str}
        """
        self.ensure_session()
        if not self._token_map:
            self._load_scrip_master()

        token = self._token_map.get(symbol)
        if not token:
            return {"status": False, "order_id": None, "message": f"Symbol {symbol} not found"}

        order_data = {
            "variety": "NORMAL",
            "tradingsymbol": f"{symbol}-EQ",
            "symboltoken": token,
            "transactiontype": order_type,
            "exchange": "NSE",
            "ordertype": "MARKET" if price is None else "LIMIT",
            "producttype": "DELIVERY",
            "duration": "DAY",
            "price": "0" if price is None else str(price),
            "squareoff": "0",
            "stoploss": "0",
            "quantity": str(qty),
        }

        try:
            resp = requests.post(
                f"{ANGEL_BASE}/rest/secure/angelbroking/order/v1/placeOrder",
                headers=self._auth_headers(),
                json=order_data,
                timeout=15,
            )
            body = self._parse_order_response(resp)

            if body.get("status"):
                order_id = body.get("data", {}).get("orderid")
                return {"status": True, "order_id": order_id, "message": "Order placed successfully"}
            else:
                msg = body.get("message", "Unknown error")
                return {"status": False, "order_id": None, "message": msg}
        except Exception as e:
            return {"status": False, "order_id": None, "message": str(e)}

    def get_order_status(self, order_id):
        """Look up an order's status from today's order book by orderid."""
        self.ensure_session()
        try:
            resp = requests.get(
                f"{ANGEL_BASE}/rest/secure/angelbroking/order/v1/getOrderBook",
                headers=self._auth_headers(),
                timeout=15,
            )
            body = self._parse_order_response(resp)

            if not body.get("status"):
                return {"order_id": order_id, "status": "UNKNOWN", "message": body.get("message", "Unknown")}

            for order in body.get("data") or []:
                if str(order.get("orderid")) == str(order_id):
                    return {
                        "order_id": order.get("orderid"),
                        "status": order.get("orderstatus"),
                        "filled_qty": int(order.get("filledshares") or 0),
                        "average_price": float(order.get("averageprice") or 0),
                        "message": "OK"
                    }
            return {"order_id": order_id, "status": "NOT_FOUND", "message": "Order not found in today's order book"}
        except Exception as e:
            return {"order_id": order_id, "status": "ERROR", "message": str(e)}

    def cancel_order(self, order_id, variety="NORMAL"):
        """Cancel a pending order."""
        self.ensure_session()
        try:
            resp = requests.post(
                f"{ANGEL_BASE}/rest/secure/angelbroking/order/v1/cancelOrder",
                headers=self._auth_headers(),
                json={"variety": variety, "orderid": order_id},
                timeout=15,
            )
            body = self._parse_order_response(resp)

            if body.get("status"):
                return {"status": True, "order_id": order_id, "message": "Order cancelled successfully"}
            else:
                return {"status": False, "order_id": order_id, "message": body.get("message", "Unknown error")}
        except Exception as e:
            return {"status": False, "order_id": order_id, "message": str(e)}

    # ── GTT (Good-Till-Triggered) rules ───────────────────────────────────────
    # Note: create/modify/cancel use the /gtt-service/... path prefix;
    # details/list use the plain /rest/secure/... prefix — this split is
    # deliberate and matches Angel One's actual route table.
    def create_gtt_rule(self, symbol, transaction_type, qty, trigger_price, limit_price,
                         producttype="DELIVERY", timeperiod=365):
        """
        Create a GTT rule — a broker-side conditional order that Angel One's
        own servers trigger when price crosses trigger_price, independent of
        whether our bot is running. Returns {"status", "rule_id", "message"}.
        """
        self.ensure_session()
        if not self._token_map:
            self._load_scrip_master()

        token = self._token_map.get(symbol)
        if not token:
            return {"status": False, "rule_id": None, "message": f"Symbol {symbol} not found"}

        rule_data = {
            "tradingsymbol": f"{symbol}-EQ",
            "symboltoken": token,
            "exchange": "NSE",
            "producttype": producttype,
            "transactiontype": transaction_type,
            "price": round(limit_price, 2),
            "qty": qty,
            "disclosedqty": qty,
            "triggerprice": round(trigger_price, 2),
            "timeperiod": timeperiod,
        }

        try:
            resp = requests.post(
                f"{ANGEL_BASE}/gtt-service/rest/secure/angelbroking/gtt/v1/createRule",
                headers=self._auth_headers(),
                json=rule_data,
                timeout=15,
            )
            body = self._parse_order_response(resp)

            if body.get("status"):
                rule_id = body.get("data", {}).get("id")
                return {"status": True, "rule_id": rule_id, "message": "GTT rule created"}
            else:
                return {"status": False, "rule_id": None, "message": body.get("message", "Unknown error")}
        except Exception as e:
            return {"status": False, "rule_id": None, "message": str(e)}

    def cancel_gtt_rule(self, rule_id, symbol):
        """Cancel a GTT rule (e.g. the untriggered sibling once one fires)."""
        self.ensure_session()
        if not self._token_map:
            self._load_scrip_master()
        token = self._token_map.get(symbol)
        if not token:
            return {"status": False, "rule_id": rule_id, "message": f"Symbol {symbol} not found"}

        try:
            resp = requests.post(
                f"{ANGEL_BASE}/gtt-service/rest/secure/angelbroking/gtt/v1/cancelRule",
                headers=self._auth_headers(),
                json={"id": rule_id, "symboltoken": token, "exchange": "NSE"},
                timeout=15,
            )
            body = self._parse_order_response(resp)

            if body.get("status"):
                return {"status": True, "rule_id": rule_id, "message": "GTT rule cancelled"}
            else:
                return {"status": False, "rule_id": rule_id, "message": body.get("message", "Unknown error")}
        except Exception as e:
            return {"status": False, "rule_id": rule_id, "message": str(e)}

    def get_gtt_rule_details(self, rule_id):
        """
        Look up a GTT rule's current status. Known values: NEW, ACTIVE
        (still watching), SENTTOEXCHANGE (triggered — order placed),
        CANCELLED. Any other/unrecognized value is returned as-is —
        callers should treat unrecognized statuses cautiously.
        """
        self.ensure_session()
        try:
            resp = requests.post(
                f"{ANGEL_BASE}/rest/secure/angelbroking/gtt/v1/ruleDetails",
                headers=self._auth_headers(),
                json={"id": rule_id},
                timeout=15,
            )
            body = self._parse_order_response(resp)

            if body.get("status") and body.get("data"):
                data = body["data"]
                return {"rule_id": rule_id, "status": data.get("status"), "message": "OK"}
            else:
                return {"rule_id": rule_id, "status": "UNKNOWN", "message": body.get("message", "Unknown")}
        except Exception as e:
            return {"rule_id": rule_id, "status": "ERROR", "message": str(e)}


# ── Shared client (one login session per process) ─────────────────────────────
_shared_client = None


def get_client():
    """Return the process-wide AngelOneAPI instance."""
    global _shared_client
    if _shared_client is None:
        _shared_client = AngelOneAPI()
    return _shared_client


def fetch_quotes(symbols):
    """Live NSE equity quotes — single entry point for all modules."""
    if not symbols:
        return {}
    return get_client().get_quotes(list(symbols))


def fetch_ltps(symbols):
    """LTP only, rounded to 2 decimals."""
    quotes = fetch_quotes(symbols)
    return {sym: round(q["ltp"], 2) for sym, q in quotes.items() if q.get("ltp")}


def fetch_nifty_day_change():
    """Nifty 50 day % change for macro direction capture (no gating in Phase 1)."""
    try:
        return get_client().get_index_quote("NIFTY")
    except Exception as e:
        print(f"  [angel] Nifty index fetch failed: {e}")
        return None
