#!/usr/bin/env python3
"""
Auto Trader Dashboard — VM ops console + trading view.

Run:  streamlit run dashboard.py --server.address 0.0.0.0 --server.port 8501
"""

import json
import os
from datetime import datetime, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import bot_status

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="auto-trader",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Vibe coding theme ─────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Inter:wght@400;500;600&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}
.stApp {
    background: radial-gradient(ellipse 120% 80% at 50% -20%, #1a1033 0%, #08080c 45%, #050508 100%);
    color: #e4e4e7;
}
#MainMenu, footer, header { visibility: hidden; }

.vibe-header {
    font-family: 'JetBrains Mono', monospace;
    font-size: 1.75rem;
    font-weight: 700;
    background: linear-gradient(135deg, #c4b5fd 0%, #22d3ee 50%, #a78bfa 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    letter-spacing: -0.03em;
    margin-bottom: 0.15rem;
}
.vibe-sub {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.72rem;
    color: #71717a;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    margin-bottom: 1.5rem;
}

.glass {
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(167,139,250,0.15);
    border-radius: 12px;
    padding: 1rem 1.15rem;
    backdrop-filter: blur(12px);
    margin-bottom: 0.75rem;
}
.glass-glow {
    box-shadow: 0 0 24px rgba(167,139,250,0.08);
}

.metric-label {
    font-size: 0.65rem;
    color: #71717a;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    font-family: 'JetBrains Mono', monospace;
}
.metric-value {
    font-size: 1.5rem;
    font-weight: 600;
    font-family: 'JetBrains Mono', monospace;
    color: #fafafa;
}
.metric-value.pos { color: #4ade80; }
.metric-value.neg { color: #f87171; }
.metric-value.accent { color: #22d3ee; }

.status-dot {
    display: inline-block;
    width: 8px; height: 8px;
    border-radius: 50%;
    margin-right: 6px;
    animation: pulse 2s infinite;
}
.status-dot.ok { background: #4ade80; box-shadow: 0 0 8px #4ade80; }
.status-dot.warn { background: #fbbf24; box-shadow: 0 0 8px #fbbf24; }
.status-dot.err { background: #f87171; box-shadow: 0 0 8px #f87171; }
.status-dot.off { background: #52525b; animation: none; }

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
}

.timeline-step {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.78rem;
    padding: 0.5rem 0.75rem;
    border-left: 2px solid #3f3f46;
    margin-left: 0.5rem;
    color: #a1a1aa;
}
.timeline-step.done { border-color: #4ade80; color: #e4e4e7; }
.timeline-step.pending { border-color: #a78bfa; color: #c4b5fd; }
.timeline-step.fail { border-color: #f87171; color: #fca5a5; }

.event-row {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.72rem;
    padding: 0.35rem 0;
    border-bottom: 1px solid rgba(255,255,255,0.04);
    color: #a1a1aa;
}
.event-row .ts { color: #52525b; margin-right: 0.5rem; }
.event-row .type-trade { color: #22d3ee; }
.event-row .type-eod { color: #a78bfa; }
.event-row .type-error { color: #f87171; }

div[data-testid="stDataFrame"] {
    border: 1px solid rgba(167,139,250,0.12);
    border-radius: 10px;
    overflow: hidden;
}

.stTabs [data-baseweb="tab-list"] {
    gap: 8px;
    background: transparent;
}
.stTabs [data-baseweb="tab"] {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.8rem;
    background: rgba(255,255,255,0.03);
    border-radius: 8px;
    border: 1px solid rgba(255,255,255,0.06);
    color: #71717a;
    padding: 0.5rem 1.2rem;
}
.stTabs [aria-selected="true"] {
    background: rgba(167,139,250,0.12) !important;
    border-color: rgba(167,139,250,0.35) !important;
    color: #e4e4e7 !important;
}
</style>
""", unsafe_allow_html=True)


PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="JetBrains Mono, monospace", color="#a1a1aa", size=11),
    margin=dict(l=40, r=20, t=30, b=40),
    xaxis=dict(gridcolor="rgba(255,255,255,0.06)", zerolinecolor="rgba(255,255,255,0.1)"),
    yaxis=dict(gridcolor="rgba(255,255,255,0.06)", zerolinecolor="rgba(255,255,255,0.1)"),
)


def _load(path, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    with open(path) as f:
        return json.load(f)


@st.cache_data(ttl=30)
def load_data():
    return {
        "portfolio": _load("paper_portfolio.json", {}),
        "confirm": _load("paper_portfolio_confirm.json", {}),
        "scan": _load("scan_results.json", {}),
        "shortlist": _load("eod_shortlist.json", {}),
        "confirm_log": _load("eod_confirm_log.json", {"entries": []}),
        "status": bot_status.load_status(),
        "activity": bot_status.read_activity(35),
    }


def _fmt_inr(val, signed=False):
    if val is None:
        return "—"
    sign = "+" if val >= 0 and signed else ""
    return f"₹{sign}{val:,.0f}"


def _heartbeat(status):
    updated = status.get("updated_at")
    if not updated:
        return "off", "No heartbeat", None
    try:
        dt = datetime.fromisoformat(updated)
        mins = (datetime.now() - dt).total_seconds() / 60
    except ValueError:
        return "warn", "Invalid timestamp", None
    if mins < 8:
        return "ok", f"alive · {mins:.0f}m ago", mins
    if mins < 30:
        return "warn", f"stale · {mins:.0f}m ago", mins
    return "err", f"dead? · {mins:.0f}m ago", mins


def _metric_card(label, value, css_class=""):
    return f"""<div class="glass glass-glow">
        <div class="metric-label">{label}</div>
        <div class="metric-value {css_class}">{value}</div>
    </div>"""


def _timeline_html(today):
    data = load_data()
    scan = data["scan"]
    shortlist = data["shortlist"]
    confirm_log = data["confirm_log"]
    status = data["status"]

    scan_ok = scan.get("scan_time", "")[:10] == today
    shortlist_ok = shortlist.get("date") == today
    confirm_entries = [e for e in confirm_log.get("entries", []) if e.get("date") == today]
    confirm_ok = len(confirm_entries) > 0 or status.get("eod_confirm_done_today") == today

    steps = [
        ("3:25 Scanner", scan_ok, f"{scan.get('summary', {}).get('matched', 0)} matched" if scan_ok else "pending"),
        ("Shortlist saved", shortlist_ok, f"{len(shortlist.get('signals', []))} signals" if shortlist_ok else "pending"),
        ("3:30 Confirm", confirm_ok, f"{sum(1 for e in confirm_entries if e.get('confirm_pass'))} passed" if confirm_entries else ("done" if confirm_ok else "pending")),
        ("Excel export", bot_status.file_age_minutes("trade_log.xlsx") is not None and bot_status.file_age_minutes("trade_log.xlsx") < 1440,
         f"{bot_status.file_age_minutes('trade_log.xlsx') or '—'}m ago"),
    ]
    html = ""
    for name, done, detail in steps:
        cls = "done" if done else "pending"
        html += f'<div class="timeline-step {cls}">{"✓" if done else "○"} {name} — <span style="color:#71717a">{detail}</span></div>'
    return html


def _funnel_chart(summary):
    if not summary:
        return None
    labels = ["OI Spurt", "Long Buildup", "Volume", "EMA", "Matched"]
    keys = ["oi_spurt_stocks", "long_buildup", "vol_passed", "ema_passed", "matched"]
    values = [summary.get(k, 0) for k in keys]
    fig = go.Figure(go.Funnel(
        y=labels,
        x=values,
        textinfo="value+percent initial",
        marker=dict(color=["#3f3f46", "#52525b", "#71717a", "#a78bfa", "#22d3ee"]),
    ))
    fig.update_layout(**PLOTLY_LAYOUT, height=280, title=dict(text="scanner funnel", font=dict(size=12, color="#71717a")))
    return fig


def _equity_curve(closed_trades):
    if not closed_trades:
        return None
    df = pd.DataFrame(closed_trades)
    df["exit_date"] = pd.to_datetime(df["exit_date"])
    daily = df.groupby("exit_date")["pnl_abs"].sum().reset_index().sort_values("exit_date")
    daily["cumul"] = daily["pnl_abs"].cumsum()
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=daily["exit_date"], y=daily["cumul"],
        mode="lines+markers",
        line=dict(color="#a78bfa", width=2),
        marker=dict(size=5, color="#22d3ee"),
        fill="tozeroy",
        fillcolor="rgba(167,139,250,0.08)",
    ))
    fig.update_layout(**PLOTLY_LAYOUT, height=260, title=dict(text="cumulative realised p&l", font=dict(size=12, color="#71717a")))
    return fig


def _open_positions_df(portfolio, prices):
    rows = []
    for sym, pos in portfolio.get("positions", {}).items():
        entry = pos["entry_price"]
        curr = prices.get(sym) or entry
        sl, tgt = pos["stop_loss"], pos["target"]
        pnl = round((curr - entry) * pos["quantity"], 2)
        pnl_pct = round((curr - entry) / entry * 100, 2)
        sl_dist = round((curr - sl) / entry * 100, 2)
        tgt_dist = round((tgt - curr) / entry * 100, 2)
        rows.append({
            "Symbol": sym,
            "Entry": entry,
            "CMP": curr,
            "Qty": pos["quantity"],
            "P&L ₹": pnl,
            "P&L %": pnl_pct,
            "→ SL %": sl_dist,
            "→ Tgt %": tgt_dist,
            "Sector": pos.get("sector", "-"),
            "Macro": pos.get("macro_entry", "?"),
        })
    return pd.DataFrame(rows)


@st.cache_data(ttl=60)
def _fetch_prices(symbols):
    """Optional live prices — never block the UI if Angel One is slow."""
    if not symbols:
        return {}
    try:
        from paper_trader import fetch_prices
        return fetch_prices(list(symbols))
    except Exception:
        return {}


def _style_pnl_df(df):
    """Color P&L columns; compatible with pandas 1.x and 2.x."""
    styled = df.style.format({
        "Entry": "{:.2f}", "CMP": "{:.2f}", "P&L ₹": "{:,.0f}",
        "P&L %": "{:.2f}", "→ SL %": "{:.2f}", "→ Tgt %": "{:.2f}",
    })
    fn = lambda v: (
        "color: #4ade80" if isinstance(v, (int, float)) and v > 0
        else ("color: #f87171" if isinstance(v, (int, float)) and v < 0 else "")
    )
    if hasattr(styled, "map"):
        return styled.map(fn, subset=["P&L ₹", "P&L %"])
    return styled.applymap(fn, subset=["P&L ₹", "P&L %"])


def main():
    # ── Header ────────────────────────────────────────────────────────────────
    col_h, col_r = st.columns([5, 1])
    with col_h:
        st.markdown('<div class="vibe-header">auto-trader</div>', unsafe_allow_html=True)
        st.markdown('<div class="vibe-sub">paper trading · nse long buildup · live ops</div>', unsafe_allow_html=True)
    with col_r:
        if st.button("↻ refresh", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    today = datetime.now().strftime("%Y-%m-%d")
    data = load_data()
    status = data["status"]
    portfolio = data["portfolio"]
    confirm_pf = data["confirm"]
    scan = data["scan"]
    macro = scan.get("macro", {})
    summary = scan.get("summary", {})

    dot_cls, dot_msg, _ = _heartbeat(status)
    phase = status.get("phase", "unknown")
    market_open = status.get("market_open", False)

    # ── Top status bar ────────────────────────────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.markdown(_metric_card("bot", f'<span class="status-dot {dot_cls}"></span>{dot_msg}', "accent"), unsafe_allow_html=True)
    with c2:
        st.markdown(_metric_card("phase", phase.replace("_", " "), ""), unsafe_allow_html=True)
    with c3:
        mkt = "MARKET OPEN" if market_open else "CLOSED"
        st.markdown(_metric_card("session", mkt, "accent" if market_open else ""), unsafe_allow_html=True)
    with c4:
        sent = macro.get("sentiment", "—")
        sc = "pos" if sent == "BULLISH" else ("neg" if sent == "BEARISH" else "")
        fii = macro.get("fii_net_cr", 0)
        st.markdown(_metric_card("macro", f"{sent}<br><span style='font-size:0.75rem;color:#71717a'>FII ₹{fii:+,.0f} Cr</span>", sc), unsafe_allow_html=True)
    with c5:
        err = status.get("last_error")
        err_disp = (err[:40] + "…") if err and len(err) > 40 else (err or "none")
        st.markdown(_metric_card("last error", err_disp, "neg" if err else ""), unsafe_allow_html=True)

    # ── Tabs ────────────────────────────────────────────────────────────────
    tab_ops, tab_trade, tab_strat = st.tabs(["◈ operations", "◈ trading", "◈ strategy"])

    # ── OPERATIONS ────────────────────────────────────────────────────────────
    with tab_ops:
        o1, o2 = st.columns([1, 1])
        with o1:
            st.markdown("#### today's pipeline")
            st.markdown(_timeline_html(today), unsafe_allow_html=True)

            st.markdown("#### activity feed")
            for ev in data["activity"]:
                ts = ev.get("ts", "")[11:19]
                etype = ev.get("type", "")
                cls = f"type-{etype}" if etype in ("trade", "eod", "error") else ""
                st.markdown(
                    f'<div class="event-row"><span class="ts">{ts}</span>'
                    f'<span class="{cls}">[{etype}]</span> {ev.get("msg", "")}</div>',
                    unsafe_allow_html=True,
                )
            if not data["activity"]:
                st.caption("no events yet — starts when run_bot.py runs on VM")

        with o2:
            st.markdown("#### scanner funnel")
            if summary:
                st.plotly_chart(_funnel_chart(summary), use_container_width=True)
                st.caption(f"last scan: {scan.get('scan_time', '—')[:19].replace('T', ' ')}")
            else:
                st.info("no scan data — waiting for 3:25 PM run")

            st.markdown("#### file freshness")
            files = [
                ("scan_results.json", "Scanner"),
                ("paper_portfolio.json", "Portfolio"),
                ("trade_log.xlsx", "Excel"),
                ("eod_shortlist.json", "Shortlist"),
                ("eod_confirm_log.json", "Confirm log"),
            ]
            fres = [{"File": label, "Age (min)": bot_status.file_age_minutes(p) or "—"} for p, label in files]
            st.dataframe(pd.DataFrame(fres), hide_index=True, use_container_width=True)

            if status.get("next_wake_at"):
                st.caption(f"next wake ~{status.get('next_wake_at')} · phase {phase}")

    # ── TRADING ───────────────────────────────────────────────────────────────
    with tab_trade:
        closed = portfolio.get("closed_trades", [])
        positions = portfolio.get("positions", {})
        cash = portfolio.get("cash", 0)
        capital = portfolio.get("total_capital", 1_000_000)
        syms = list(positions.keys())

        live_prices = st.checkbox("Fetch live CMP (Angel One)", value=False)
        prices = _fetch_prices(tuple(syms)) if live_prices and syms else {}

        realised = sum(t.get("pnl_abs", 0) for t in closed)
        unrealised = sum(
            ((prices.get(s) or p["entry_price"]) - p["entry_price"]) * p["quantity"]
            for s, p in positions.items()
        )
        invested = sum(p["invested"] for p in positions.values())
        port_val = cash + invested + unrealised
        total_pnl = port_val - capital
        wins = sum(1 for t in closed if t.get("pnl_abs", 0) > 0)
        wr = round(wins / len(closed) * 100) if closed else 0

        t1, t2, t3, t4, t5, t6 = st.columns(6)
        t1.markdown(_metric_card("portfolio", _fmt_inr(port_val), ""), unsafe_allow_html=True)
        t2.markdown(_metric_card("total p&l", _fmt_inr(total_pnl, signed=True),
                                 "pos" if total_pnl >= 0 else "neg"), unsafe_allow_html=True)
        t3.markdown(_metric_card("realised", _fmt_inr(realised, signed=True),
                                 "pos" if realised >= 0 else "neg"), unsafe_allow_html=True)
        t4.markdown(_metric_card("unrealised", _fmt_inr(unrealised, signed=True),
                                 "pos" if unrealised >= 0 else "neg"), unsafe_allow_html=True)
        t5.markdown(_metric_card("open slots", f"{len(positions)} / 10", "accent"), unsafe_allow_html=True)
        t6.markdown(_metric_card("win rate", f"{wr}%", ""), unsafe_allow_html=True)

        tr1, tr2 = st.columns([1.2, 1])
        with tr1:
            st.markdown("#### open positions")
            odf = _open_positions_df(portfolio, prices)
            if not odf.empty:
                st.dataframe(
                    _style_pnl_df(odf),
                    hide_index=True,
                    use_container_width=True,
                    height=min(42 * len(odf) + 38, 400),
                )
            else:
                st.caption("no open positions")

            st.markdown("#### closed trades")
            if closed:
                cdf = pd.DataFrame(closed).sort_values("exit_date", ascending=False)
                show_cols = ["symbol", "entry_date", "exit_date", "entry_price", "exit_price",
                             "pnl_abs", "pnl_pct", "reason", "hold_days", "oi_chg", "price_chg",
                             "quality_ratio", "composite_abc", "model_f", "model_f_pass",
                             "suggested_action", "ce_oi", "total_oi", "sector"]
                show_cols = [c for c in show_cols if c in cdf.columns]
                st.dataframe(cdf[show_cols].head(30), hide_index=True, use_container_width=True)
            else:
                st.caption("no closed trades yet")

        with tr2:
            st.markdown("#### equity curve")
            fig = _equity_curve(closed)
            if fig:
                st.plotly_chart(fig, use_container_width=True)

            closed_today = [t for t in closed if t.get("exit_date") == today]
            if closed_today:
                st.markdown("#### today")
                for t in closed_today:
                    sign = "+" if t["pnl_abs"] >= 0 else ""
                    col = "#4ade80" if t["pnl_abs"] >= 0 else "#f87171"
                    st.markdown(
                        f"<span style='font-family:JetBrains Mono;font-size:0.75rem;color:{col}'>"
                        f"{t['symbol']} {sign}₹{t['pnl_abs']:,.0f} [{t['reason']}]</span>",
                        unsafe_allow_html=True,
                    )

    # ── STRATEGY ────────────────────────────────────────────────────────────
    with tab_strat:
        s1, s2 = st.columns(2)

        with s1:
            st.markdown("#### baseline vs confirm")
            b_open = len(portfolio.get("positions", {}))
            c_open = len(confirm_pf.get("positions", {}))
            b_closed = len(portfolio.get("closed_trades", []))
            c_closed = len(confirm_pf.get("closed_trades", []))
            b_pnl = sum(t.get("pnl_abs", 0) for t in portfolio.get("closed_trades", []))
            c_pnl = sum(t.get("pnl_abs", 0) for t in confirm_pf.get("closed_trades", []))

            cmp_df = pd.DataFrame([
                {"Book": "Baseline (3:25)", "Open": b_open, "Closed": b_closed, "Realised P&L": b_pnl},
                {"Book": "Confirm (3:30)", "Open": c_open, "Closed": c_closed, "Realised P&L": c_pnl},
            ])
            st.dataframe(
                cmp_df.style.format({"Realised P&L": "₹{:+,.0f}"}),
                hide_index=True,
                use_container_width=True,
            )

            signals = scan.get("results", [])
            nifty_chg = scan.get("macro", {}).get("nifty_chg_pct") or scan.get("nifty", {}).get("nifty_chg_pct")
            if nifty_chg is not None:
                st.caption(f"Nifty 50 day: {nifty_chg:+.2f}%")
            if signals:
                st.markdown("#### today's scanner matches")
                sdf = pd.DataFrame(signals)
                cols = [c for c in [
                    "symbol", "price_chg", "oi_chg", "vol_ratio", "pcr",
                    "ce_oi", "pe_oi", "total_oi", "is_liquid",
                    "quality_ratio", "model_a", "model_b", "model_c", "model_d",
                    "model_e", "model_f", "composite_abc", "model_f_pass",
                    "suggested_action", "nifty_chg_pct", "spot_price",
                ] if c in sdf.columns]
                st.dataframe(sdf[cols], hide_index=True, use_container_width=True)
            else:
                st.caption("no scanner matches today")

        with s2:
            st.markdown("#### eod confirm log")
            entries = data["confirm_log"].get("entries", [])
            today_entries = [e for e in entries if e.get("date") == today]
            if today_entries:
                edf = pd.DataFrame(today_entries)
                show = [c for c in [
                    "symbol", "confirm_pass", "vol_ratio_scan", "vol_ratio_confirm",
                    "composite_abc", "model_f", "model_f_pass", "suggested_action",
                    "ce_oi", "total_oi", "quality_ratio",
                    "close_gt_open", "near_day_high", "baseline_entered", "confirm_entered", "fail_reason",
                ] if c in edf.columns]
                st.dataframe(edf[show], hide_index=True, use_container_width=True)
            elif entries:
                st.dataframe(pd.DataFrame(entries).tail(10), hide_index=True, use_container_width=True)
                st.caption("no confirm entries for today")
            else:
                st.caption("confirm log empty — runs at 3:30 PM")

            if summary:
                st.markdown("#### entry signal snapshot")
                st.json({
                    "criteria": scan.get("criteria", {}),
                    "macro": macro,
                    "funnel": summary,
                })

    st.caption(f"◈ {datetime.now().strftime('%d %b %Y %H:%M:%S IST')} · cache 30s · live prices opt-in")


try:
    main()
except Exception as exc:
    st.error("Dashboard error — page will stay up; details below:")
    st.exception(exc)
