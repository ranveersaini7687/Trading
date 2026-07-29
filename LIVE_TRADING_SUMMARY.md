# Live Trading Implementation Summary

## What Was Added

### New Files Created

1. **`live_trading_config.json`** — Main configuration file
   - Enable/disable live trading
   - Account credentials mapping (env vars)
   - Trading conditions: Model B >= 90, vol_ratio >= 1.5
   - Risk controls and notifications

2. **`live_trading_config.example.json`** — Reference configuration with explanations

3. **`live_accounts.py`** — Account management module
   - Load credentials from environment variables
   - Manage multiple account sessions
   - Track account limits (capital, positions)
   - Log live orders to file

4. **`live_trader.py`** — Core live trading engine
   - Check trading conditions (Model B >= 90 AND vol_ratio >= 1.5)
   - Find best account with available capital
   - Place live BUY/SELL orders via Angel One API
   - Track live positions in `live_positions.json`
   - Graceful error handling and fallback

5. **`LIVE_TRADING_SETUP.md`** — User guide and troubleshooting

6. **`LIVE_TRADING_SUMMARY.md`** (this file) — Implementation overview

### Files Modified

1. **`angel_api.py`** — Extended with order placement methods
   - `place_market_order(symbol, order_type, qty, price)` — Place BUY/SELL orders
   - `get_order_status(order_id)` — Check order execution status
   - `cancel_order(order_id)` — Cancel pending orders

2. **`paper_trader.py`** — Integrated live trading logic
   - Import `live_trader` module
   - After paper entry → Call `place_live_order()` if conditions met
   - After paper exit → Call `place_sell_order()` if had live order
   - Mark positions with live order ID for tracking

---

## How It Works

### Entry Flow

```
Scanner produces signals
    ↓
Paper Trader: Open position (baseline portfolio)
    ↓
Check live conditions:
  - Model B >= 90? ✓
  - vol_ratio >= 1.5? ✓
    ↓
YES → Place live BUY order on Angel One
      Track in live_positions.json
      Log to live_trading.log
      
NO  → Paper-only position (marked ⚪)
      Continue as before
```

### Exit Flow

```
Intraday check: SL/target hit?
    ↓
Close paper position
    ↓
Had live order? 
    ↓
YES → Place live SELL order
      Track exit in live_trading.log
      
NO  → Skip (paper-only)
```

---

## Data Structures

### live_trading_config.json
```json
{
  "enabled": boolean,
  "live_trading_conditions": {
    "model_b_min": 90,
    "vol_ratio_min": 1.5
  },
  "accounts": [
    {
      "name": "Primary",
      "account_id": 1,
      "enabled": boolean,
      "api_key_env": "ANGEL_API_KEY",
      "max_capital": 500000,
      "max_positions": 5,
      "max_alloc_per_trade": 100000
    }
  ],
  "risk_controls": {...},
  "dry_run": false
}
```

### live_positions.json (auto-created)
```json
{
  "1": {
    "INFY": {
      "order_id": "20260729ABC123",
      "qty": 10,
      "entry_price": 2850.50,
      "entry_time": "2026-07-29T15:30:45",
      "model_b": 92.5,
      "vol_ratio": 1.65
    }
  }
}
```

### live_trading.log (auto-created)
```json
{"timestamp": "2026-07-29T15:30:45", "action": "BUY", "symbol": "INFY", ...}
{"timestamp": "2026-07-29T16:00:00", "action": "SELL", "symbol": "INFY", ...}
```

---

## Key Features

✅ **Non-blocking**: Live order failure doesn't affect paper trading  
✅ **Dry-run mode**: Test orders before submitting to Angel One  
✅ **Multi-account**: Support 2+ accounts with separate allocations  
✅ **Strict gating**: Only trades meeting BOTH conditions (Model B >= 90 AND vol_ratio >= 1.5)  
✅ **Risk controls**: Daily loss limits, sector concentration, min order size  
✅ **Comprehensive logging**: All orders tracked in live_trading.log  
✅ **Graceful errors**: API failures logged but don't crash bot  
✅ **Emergency disable**: Set `"enabled": false` to pause instantly  

---

## Setup Steps

### Step 1: Prepare Configuration

```bash
cd ~/Documents/auto-trader

# Review and customize
cat live_trading_config.example.json
cp live_trading_config.json live_trading_config.json.bak
```

Edit `live_trading_config.json`:
- Set `"enabled": true`
- Set at least one account `"enabled": true`

### Step 2: Add Environment Variables

Edit `.env`:
```bash
ANGEL_API_KEY="your_primary_api_key"
ANGEL_CLIENT_ID="your_primary_client_id"
ANGEL_PASSWORD="your_primary_password"
ANGEL_TOTP_SECRET="your_primary_totp_secret"

# Optional: For secondary account
ANGEL_API_KEY_2="your_secondary_api_key"
ANGEL_CLIENT_ID_2="your_secondary_client_id"
ANGEL_PASSWORD_2="your_secondary_password"
ANGEL_TOTP_SECRET_2="your_secondary_totp_secret"
```

Load:
```bash
source .env
# or for systemd: systemctl --user set-environment $(cat .env | xargs)
```

### Step 3: Test in Dry-Run Mode

```bash
# Set in live_trading_config.json
"dry_run": true

# Run bot
python3 run_bot.py --once

# Check logs
tail -20 live_trading.log
# Should show: [DRY RUN] Would BUY ...
```

### Step 4: Enable Live Trading (Phased)

**Week 1:** Secondary account only
```json
{
  "enabled": true,
  "accounts": [
    {"name": "Primary", "enabled": false},
    {"name": "Secondary", "enabled": true}
  ],
  "dry_run": false
}
```

**Week 2:** Primary account
```json
{
  "accounts": [
    {"name": "Primary", "enabled": true},
    {"name": "Secondary", "enabled": true}
  ]
}
```

---

## Monitoring Checklist

After enabling:

- [ ] Check `live_trading.log` for order events
- [ ] Verify `live_positions.json` has active positions
- [ ] Compare live P&L vs paper P&L
- [ ] Check Angel One app for orders
- [ ] Monitor slippage (entry price vs live fill)
- [ ] Track Model B and vol_ratio of executed trades
- [ ] Review daily loss (risk control)
- [ ] Check for any error messages in logs

---

## Troubleshooting Reference

| Issue | Check |
|-------|-------|
| "Live trading disabled" | `enabled: true` in config, account `enabled: true` |
| No live orders being placed | Model B or vol_ratio not met, check logs |
| "No account with capital" | Increase `max_capital` in config |
| Angel One API errors | Verify credentials in `.env`, check API status |
| Orders not executing | Check Angel One app, verify qty×price >= min_order_value |
| Imports failing | Run `pip install -r requirements.txt` |

---

## Existing Paper Trading

**No changes to paper trading flow:**
- Baseline portfolio continues as before
- Paper entries, exits, P&L unchanged
- Excel reports generated normally
- WhatsApp notifications work as before

Live trading is purely additive.

---

## Emergency Procedures

### Pause Live Trading
```json
{"enabled": false}
```
Restarts bot immediately stops new live orders.

### Cancel Pending Order
```python
from angel_api import AngelOneAPI
angel = AngelOneAPI()
angel.ensure_session()
result = angel.cancel_order("ORDER_ID")
```

### Manual Position Closure
Edit `live_positions.json` directly, or close via Angel One app.

---

## Next Steps

1. Review `LIVE_TRADING_SETUP.md` for detailed configuration guide
2. Customize `live_trading_config.json` for your account limits
3. Set environment variables in `.env`
4. Test with `dry_run: true`
5. Monitor logs for 1-2 days before enabling live trading
6. Start with Secondary account (smaller capital) for validation
7. Gradually enable Primary account once confident

---

## Questions?

- Check logs: `live_trading.log`, `activity_log.jsonl`, bot console output
- Review positions: `live_positions.json`, `paper_portfolio.json`
- Test conditions: Manually check Model B and vol_ratio in signal JSON
- Verify setup: Ensure all env vars loaded (`echo $ANGEL_API_KEY`)
