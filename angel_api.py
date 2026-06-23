#!/usr/bin/env python3
"""
Angel One SmartAPI wrapper.
Handles login (TOTP), scrip master token map, and bulk equity LTP quotes.

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
from datetime import date

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
        self._token_map  = None   # NSE symbol -> symbolToken string

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
        """Re-login if token missing or from a previous day."""
        if self._token_date != date.today() or not self.jwt_token:
            self.login()

    # ── Scrip master ─────────────────────────────────────────────────────────
    def _load_token_map(self):
        """
        Build NSE equity symbol → symbolToken map from Angel One scrip master.
        Cached daily — scrip master changes infrequently.
        """
        cache_file = os.path.join(CACHE_DIR, "scrip_tokens.json")
        if os.path.exists(cache_file):
            age = time.time() - os.path.getmtime(cache_file)
            if age < 86400:
                with open(cache_file) as f:
                    self._token_map = json.load(f)
                print(f"  [angel] Token map: {len(self._token_map)} NSE symbols (cache)")
                return

        print("  [angel] Downloading scrip master (~5 MB)...")
        resp = requests.get(SCRIP_MASTER_URL, timeout=60)
        resp.raise_for_status()

        token_map = {}
        for item in resp.json():
            if item.get("exch_seg") == "NSE" and item.get("instrumenttype") in ("", "EQ"):
                sym = item.get("symbol", "").replace("-EQ", "").replace("-BE", "").strip()
                if sym:
                    token_map[sym] = str(item["token"])

        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cache_file, "w") as f:
            json.dump(token_map, f)
        self._token_map = token_map
        print(f"  [angel] Token map cached: {len(token_map)} NSE symbols")

    # ── Market data ───────────────────────────────────────────────────────────
    def get_quotes(self, symbols):
        """
        Fetch LTP + previous close for a list of NSE equity symbols.
        Returns: {symbol: {"ltp": float, "prev_close": float}}
        Silently skips symbols not found in scrip master.
        """
        self.ensure_session()
        if not self._token_map:
            self._load_token_map()

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
                            "ltp":        float(item.get("ltp")   or 0),
                            "prev_close": float(item.get("close") or 0),
                        }

        return results
