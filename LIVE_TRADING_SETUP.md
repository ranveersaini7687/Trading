# Live Trading Setup Guide

## Overview

This guide explains how to configure and enable live trading on Angel One. The bot will automatically place live orders when:
- **Model B score >= 90**
- **Volume ratio >= 1.5x** (today's volume / 20-day average)

Existing paper trading continues unchanged — live orders are **additive**, not replacing the paper portfolio.

---

## Configuration

### 1. Enable Live Trading

Edit `live_trading_config.json`:

```json
{
  "enabled": true,
  "live_trading_conditions": {
    "model_b_min": 90,
    "vol_ratio_min": 1.5
  },
  "accounts": [
    {
      "name": "Primary",
      "account_id": 1,
      "enabled": true,  // ← Set to true
      ...
    }
  ]
}
```

### 2. Set Environment Variables

For **Primary Account** (already exists):
```bash
export ANGEL_API_KEY="your_api_key"
export ANGEL_CLIENT_ID="your_client_id"
export ANGEL_PASSWORD="your_password"
export ANGEL_TOTP_SECRET="your_totp_secret"
```

For **Secondary Account** (optional, for multi-account):
```bash
export ANGEL_API_KEY_2="second_api_key"
export ANGEL_CLIENT_ID_2="second_client_id"
export ANGEL_PASSWORD_2="second_password"
export ANGEL_TOTP_SECRET_2="second_totp_secret"
```

Add to `.env` file in the project root.

---

## Risk Controls

The following risk controls apply to live orders:

```json
"risk_controls": {
  "daily_loss_limit_pct": 5.0,         // Max daily loss %
  "max_sector_per_account": 1,         // Max 1 per sector
  "min_order_value": 10000,            // Min order size
  "position_sizing": "fixed_alloc"     // ₹1,00,000 per trade
}
```

Violations log a warning but **don't block paper trades**.

---

## Monitoring

### Live Trading Log

All live orders are logged to `live_trading.log`:

```json
{
  "timestamp": "2026-07-29T15:30:45.123456",
  "action": "BUY",
  "symbol": "INFY",
  "qty": 10,
  "price": 2850.50,
  "order_id": "20260729ABC123",
  "account": "Primary",
  "model_b": 92.5,
  "vol_ratio": 1.65
}
```

### Live Positions

Active live positions tracked in `live_positions.json`:

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

---

## Testing

### Dry-Run Mode

Test orders without actually submitting to Angel One:

```json
{
  "dry_run": true
}
```

Output:
```
[15:30:45] [DRY RUN] Would BUY 10 INFY @ ₹2850.50 on account Primary
```

### Manual Testing

```python
from live_trader import get_live_trader

trader = get_live_trader()
signal = {
    "symbol": "INFY",
    "model_b": 92.5,
    "vol_ratio": 1.65,
    "spot_price": 2850.00
}

result = trader.place_live_order(signal, qty=10, entry_price=2850.50)
print(result)  # {"placed": True, "reason": "OK", "order_id": "..."}
```

---

## Workflow

### At 3:25 PM (EOD Scan)

1. Scanner produces signals with model scores
2. Paper trader opens positions (existing flow)
3. For each paper entry:
   - Check: Model B >= 90 AND vol_ratio >= 1.5
   - If yes → Place live BUY order on Angel One
   - If no → Paper-only position (marked ⚪)

### During Intraday (Every 5 mins)

1. Check SL/target hits on paper portfolio
2. If position needs closing:
   - Place live SELL order (if had live order)
   - Close paper position

### Exit Reasons

| Paper | Live | Status |
|-------|------|--------|
| SL hit | Sell | Position closed |
| Target hit | Sell | Position closed |
| Both fail | Hold | Continue holding |

---

## Troubleshooting

### "Live trading disabled"
- Check `"enabled": true` in config
- Check account `"enabled": true`

### "No account with sufficient capital"
- Check max_capital in config
- Verify account capital allocation

### "Order value ₹X < min ₹10000"
- Minimum order value not met
- Check qty × price >= min_order_value

### Angel One API errors
- Verify credentials in .env
- Check Angel One API status
- Check internet connectivity

---

## Safety Features

1. **Fail-safe**: Live order failure doesn't block paper trade
2. **Dry-run mode**: Test without submitting orders
3. **Error logging**: All failures logged to live_trading.log
4. **Graceful degradation**: Paper trading continues if live fails
5. **Manual kill-switch**: Set `"enabled": false` to pause

---

## Phased Rollout

**Phase 1** (First week):
- Dry-run mode only
- Monitor logs for order quality
- Verify model conditions are working

**Phase 2** (Second week):
- Enable on Secondary account (₹50k capital)
- Monitor execution vs paper P&L
- Check fees and slippage

**Phase 3** (After validation):
- Enable on Primary account (₹500k capital)
- Continue monitoring dual P&L
- Adjust risk controls if needed

---

## Emergency Disable

To quickly disable live trading:

```bash
# Edit live_trading_config.json
"enabled": false
```

This will:
- Stop new live orders
- Continue monitoring existing orders
- Keep paper trading intact

---

## Next Steps

1. ✅ Configure `live_trading_config.json`
2. ✅ Set environment variables in `.env`
3. ✅ Run with `"dry_run": true` first
4. ✅ Review logs for 1-2 days
5. ✅ Enable on Secondary account if satisfied
6. ✅ Gradually expand to Primary account

---

## Support

For issues, check:
- `live_trading.log` — all order events
- `live_positions.json` — current positions
- Bot logs (`activity_log.jsonl`) — trade flow
- Paper portfolio (`paper_portfolio.json`) — baseline trades
