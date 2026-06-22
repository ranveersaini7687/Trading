#!/usr/bin/env python3
"""
Single-cycle runner — called by GitHub Actions daily at 3:15 PM IST.
Also used for manual one-shot runs: python3 run_once.py
"""
from long_buildup_scanner import scan
from paper_trader import run as paper_run

print("=" * 60)
print("  AUTO TRADER — Daily Run")
print("=" * 60)
scan()
paper_run()
