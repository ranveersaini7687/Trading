#!/usr/bin/env python3
"""
Live account manager — loads credentials, manages sessions, routes orders.
"""

import json
import os
from datetime import datetime


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


class AccountManager:
    def __init__(self, config_path="live_trading_config.json"):
        self.config_path = config_path
        self.config = self._load_config()
        self.enabled = self.config.get("enabled", False)
        self.accounts = {}
        self._load_accounts()

    def _load_config(self):
        if not os.path.exists(self.config_path):
            log(f"⚠ Live trading config not found at {self.config_path}")
            return {"enabled": False, "accounts": []}
        with open(self.config_path) as f:
            return json.load(f)

    def _load_accounts(self):
        """Load account credentials from env vars."""
        for acc in self.config.get("accounts", []):
            if not acc.get("enabled"):
                continue

            try:
                creds = {
                    "name": acc.get("name"),
                    "account_id": acc.get("account_id"),
                    "api_key": os.environ.get(acc.get("api_key_env", "")),
                    "client_id": os.environ.get(acc.get("client_id_env", "")),
                    "password": os.environ.get(acc.get("password_env", "")),
                    "totp_secret": os.environ.get(acc.get("totp_secret_env", "")),
                    "max_capital": acc.get("max_capital"),
                    "max_positions": acc.get("max_positions"),
                    "max_alloc_per_trade": acc.get("max_alloc_per_trade"),
                }

                # Validate credentials
                if not all([creds["api_key"], creds["client_id"], creds["password"], creds["totp_secret"]]):
                    log(f"⚠ Incomplete credentials for account '{creds['name']}' — skipping")
                    continue

                self.accounts[creds["account_id"]] = creds
                log(f"✓ Loaded account: {creds['name']} (ID: {creds['account_id']})")
            except Exception as e:
                log(f"✗ Error loading account: {e}")

    def is_enabled(self):
        return self.enabled and len(self.accounts) > 0

    def get_account(self, account_id):
        """Get account credentials by ID."""
        return self.accounts.get(account_id)

    def get_enabled_accounts(self):
        """Get all enabled accounts."""
        return list(self.accounts.values())

    def get_live_conditions(self):
        """Get Model B and vol_ratio thresholds."""
        cond = self.config.get("live_trading_conditions", {})
        return {
            "model_b_min": cond.get("model_b_min", 90),
            "vol_ratio_min": cond.get("vol_ratio_min", 1.5),
        }

    def get_live_risk(self):
        """Get live trade sizing/risk rules — independent of paper trading's."""
        risk = self.config.get("live_risk", {})
        return {
            "max_live_positions_per_account": risk.get("max_live_positions_per_account", 2),
            "alloc_per_trade": risk.get("alloc_per_trade", 10000),
            "stop_loss_pct": risk.get("stop_loss_pct", 0.65),
            "target_pct": risk.get("target_pct", 2.0),
        }

    def get_risk_controls(self):
        """Get risk management settings."""
        return self.config.get("risk_controls", {})

    def is_dry_run(self):
        """Check if dry-run mode is enabled."""
        return self.config.get("dry_run", False)

    def log_live_order(self, order_data):
        """Log live order to file."""
        log_file = self.config.get("notifications", {}).get("log_file", "live_trading.log")
        try:
            with open(log_file, "a") as f:
                f.write(json.dumps({
                    "timestamp": datetime.now().isoformat(),
                    **order_data
                }) + "\n")
        except Exception as e:
            log(f"✗ Failed to log order: {e}")


def get_account_manager():
    """Singleton-ish account manager."""
    if not hasattr(get_account_manager, "_instance"):
        get_account_manager._instance = AccountManager()
    return get_account_manager._instance
