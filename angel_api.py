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
import json
import time
import requests
from datetime import date, datetime, timedelta

ANGEL_BASE       = "https://apiconnect.angelone.in"
SCRIP_MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
CACHE_DIR        = "cache"


class AngelOneAPI:
    def __init__(self):
        self.api_key     = os.environ.get("ANGEL_API_KEY", "")
        self.client_id   = os.environ.get("ANGEL_CLIENT_ID", "")
        self.password    = os.environ.get("ANGEL_PASSWORD", "")
        self.totp_secret = os.environ.get("ANGEL_TOTP_SECRET", "")
        self.jwt_token   = None
        self._token_date = None
        self._token_map  = None   # NSE symbol -> symbolToken
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
        """
        Load scrip master once daily.
        Builds:
          _token_map : NSE equity symbol -> token
          _nfo_map   : stock name -> list of option instrument dicts
        """
        cache_file = os.path.join(CACHE_DIR, "scrip_tokens.json")
        nfo_cache  = os.path.join(CACHE_DIR, "scrip_nfo.json")

        if (os.path.exists(cache_file) and os.path.exists(nfo_cache) and
                time.time() - os.path.getmtime(cache_file) < 86400):
            with open(cache_file) as f:
                self._token_map = json.load(f)
            with open(nfo_cache) as f:
                self._nfo_map = json.load(f)
            print(f"  [angel] Scrip master: {len(self._token_map)} NSE  |  "
                  f"{len(self._nfo_map)} NFO names (cache)")
            return

        print("  [angel] Downloading scrip master (~5 MB)...")
        resp = requests.get(SCRIP_MASTER_URL, timeout=60)
        resp.raise_for_status()
        master = resp.json()

        token_map = {}
        nfo_map   = {}

        for item in master:
            seg  = item.get("exch_seg", "")
            itype = item.get("instrumenttype", "")

            # NSE equities
            if seg == "NSE" and itype in ("", "EQ"):
                sym = item.get("symbol", "").replace("-EQ", "").replace("-BE", "").strip()
                if sym:
                    token_map[sym] = str(item["token"])

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

        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cache_file, "w") as f:
            json.dump(token_map, f)
        with open(nfo_cache, "w") as f:
            json.dump(nfo_map, f)

        self._token_map = token_map
        self._nfo_map   = nfo_map
        print(f"  [angel] Scrip master cached: {len(token_map)} NSE  |  {len(nfo_map)} NFO names")

    def _load_token_map(self):
        self._load_scrip_master()

    # ── Equity LTP quotes ─────────────────────────────────────────────────────
    def get_quotes(self, symbols):
        """
        Fetch LTP + previous close for a list of NSE equity symbols.
        Returns: {symbol: {"ltp": float, "prev_close": float}}
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
                            "prev_close": float(item.get("close")      or 0),
                            "volume":     int(item.get("tradeVolume")  or 0),
                        }
        return results

    # ── 20-day average volume ─────────────────────────────────────────────────
    def get_avg_volumes(self, symbols):
        """
        Fetch 20-day average daily volume for a list of NSE equity symbols.
        Returns: {symbol: avg_volume_int}
        """
        self.ensure_session()
        if not self._token_map:
            self._load_scrip_master()

        from_date = (datetime.now() - timedelta(days=35)).strftime("%Y-%m-%d %H:%M")
        to_date   = datetime.now().strftime("%Y-%m-%d %H:%M")
        result    = {}

        for sym in symbols:
            token = self._token_map.get(sym)
            if not token:
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
            except Exception:
                result[sym] = 0

        return result

    # ── NFO option chain PCR ──────────────────────────────────────────────────
    def get_pcr(self, symbol, spot_price):
        """
        Calculate PCR and liquidity from Angel One NFO option chain data.
        Uses near-month expiry + ATM ±15% strikes.
        Returns (pcr, is_liquid).
        """
        self.ensure_session()
        if not self._nfo_map:
            self._load_scrip_master()

        instruments = self._nfo_map.get(symbol, [])
        if not instruments:
            return None, False

        # Find nearest expiry
        def parse_expiry(e):
            try:
                return datetime.strptime(e, "%d%b%Y")
            except Exception:
                return datetime.max

        expiries    = sorted({i["expiry"] for i in instruments}, key=parse_expiry)
        near_expiry = expiries[0] if expiries else None
        if not near_expiry:
            return None, False

        lower, upper = spot_price * 0.85, spot_price * 1.15
        near_opts = [
            i for i in instruments
            if i["expiry"] == near_expiry and lower <= i["strike"] <= upper
        ]

        if not near_opts:
            return None, False

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
        is_liquid = (ce_oi + pe_oi) >= 500
        return pcr, is_liquid
