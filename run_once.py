#!/usr/bin/env python3
"""
Single-cycle runner — called by GitHub Actions daily at 3:15 PM IST.
Also used for manual one-shot runs: python3 run_once.py
"""
import os
import json
import requests as req
from requests.auth import HTTPBasicAuth
from datetime import datetime

from long_buildup_scanner import scan
from paper_trader import run as paper_run


def send_whatsapp(message):
    sid   = os.environ.get("TWILIO_SID")
    token = os.environ.get("TWILIO_TOKEN")
    to    = os.environ.get("WHATSAPP_TO")
    if not sid or not token or not to:
        print("  [notify] Twilio secrets not set — skipping WhatsApp")
        return
    resp = req.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        auth=HTTPBasicAuth(sid, token),
        data={
            "From": "whatsapp:+14155238886",
            "To":   f"whatsapp:{to}",
            "Body": message,
        },
        timeout=15,
    )
    if resp.status_code == 201:
        print("  [notify] WhatsApp sent ✓")
    else:
        print(f"  [notify] WhatsApp failed: {resp.status_code} — {resp.text[:200]}")


def build_summary():
    today = datetime.now().strftime("%d %b %Y")
    lines = [f"🤖 *Auto Trader — {today}*"]

    # FII macro + scanner results
    if os.path.exists("scan_results.json"):
        with open("scan_results.json") as f:
            scan_data = json.load(f)
        macro     = scan_data.get("macro", {})
        sentiment = macro.get("sentiment", "UNKNOWN")
        fii_net   = macro.get("fii_5d_net_cr", 0)
        results   = scan_data.get("results", [])
        summary   = scan_data.get("summary", {})

        icon = {"BULLISH": "🟢", "NEUTRAL": "🔵", "CAUTIOUS": "🟡", "BEARISH": "🔴"}.get(sentiment, "⚪")
        lines.append(f"\n{icon} *FII Macro:* {sentiment}  (5D Net ₹{fii_net:+,.0f} Cr)")
        lines.append(f"📊 *Scanner:* {summary.get('oi_spurt_stocks', 0)} scanned → {summary.get('long_buildup', 0)} buildup → {len(results)} matched")

        if results:
            lines.append("\n✅ *Matched Stocks:*")
            for r in results:
                lines.append(f"  • {r['symbol']:<14} {r['price_chg']:+.2f}%  OI {r['oi_chg']:+.2f}%  PCR {r['pcr']:.2f}")

    # Portfolio
    if os.path.exists("paper_portfolio.json"):
        with open("paper_portfolio.json") as f:
            port = json.load(f)

        cash         = port.get("cash", 0)
        total_cap    = port.get("total_capital", 1_000_000)
        positions    = port.get("positions", {})
        closed       = port.get("closed_trades", [])
        today_str    = datetime.now().strftime("%Y-%m-%d")
        closed_today = [t for t in closed if t.get("exit_date") == today_str]
        wins         = [t for t in closed if t["pnl_abs"] > 0]
        losses       = [t for t in closed if t["pnl_abs"] <= 0]

        invested = sum(p["invested"] for p in positions.values())
        realised = sum(t["pnl_abs"] for t in closed)
        port_val = cash + invested   # approximate (no live prices at notify time)
        total_pnl = port_val - total_cap

        lines.append(f"\n💼 *Portfolio:* ₹{port_val:,.0f}  ({'+' if total_pnl >= 0 else ''}₹{total_pnl:,.0f})")
        lines.append(f"  Open: {len(positions)} positions  |  Cash: ₹{cash:,.0f}")
        lines.append(f"  Realised P&L: ₹{realised:+,.0f}  ({len(wins)}W / {len(losses)}L)")

        if positions:
            lines.append("\n📂 *Open Positions:*")
            for sym, p in positions.items():
                lines.append(f"  • {sym:<14} Entry ₹{p['entry_price']:.2f}  SL ₹{p['stop_loss']:.2f}  T ₹{p['target']:.2f}")

        if closed_today:
            lines.append("\n🔒 *Closed Today:*")
            for t in closed_today:
                emoji = "✅" if t["pnl_abs"] > 0 else "❌"
                lines.append(f"  {emoji} {t['symbol']:<14} ₹{t['pnl_abs']:+,.0f} ({t['pnl_pct']:+.2f}%)  [{t['reason']}]")

    lines.append(f"\n⏱ {datetime.now().strftime('%H:%M IST')}")
    return "\n".join(lines)


print("=" * 60)
print("  AUTO TRADER — Daily Run")
print("=" * 60)
scan()
paper_run()

print("\n  Sending WhatsApp summary...")
send_whatsapp(build_summary())
