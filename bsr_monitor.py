"""
SKINSIGNAL — BSR Monitor
Tracks Amazon Best Seller Rank for approved signals using Keepa API

KEEPA SETUP (free tier, 2 minutes):
─────────────────────────────────────
1. Go to: https://keepa.com → Sign up free
2. Go to: https://keepa.com/#!api → Get API key
3. Free tier: 250 tokens/day
   Each product lookup costs ~10 tokens
   = 25 product checks per day free
   Enough for 25 active signals

Add to Railway environment variables:
   KEEPA_API_KEY = your_key_here

HOW IT WORKS:
─────────────────────────────────────
1. Signal approved + ASIN added → baseline BSR recorded
2. Daily check → compare current BSR to baseline
3. BSR drops by >50% → significant movement detected
4. Email fires → "BSR Confirmed — build the page now"
5. Signal auto-marked as converted

BSR DROP = GOOD (lower number = selling more)
  Baseline: 48,000
  Now:       3,200
  Drop:      93%  ← significant, email fires
"""

import os
import json
import logging
import requests
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

KEEPA_API_KEY   = os.environ.get("KEEPA_API_KEY", "PASTE_HERE")
KEEPA_BASE_URL  = "https://api.keepa.com"

# BSR drop threshold to trigger "converted" email
# 50% drop = BSR went from 10,000 to 5,000 or better
BSR_MOVE_THRESHOLD = 0.50


# ─────────────────────────────────────────────────────────────
# KEEPA API
# ─────────────────────────────────────────────────────────────

def get_keepa_data(asin, domain=1):
    """
    Fetch product data from Keepa.
    domain=1 is amazon.com

    Returns dict with current BSR or None on failure.
    """
    try:
        url    = f"{KEEPA_BASE_URL}/product"
        params = {
            "key":    KEEPA_API_KEY,
            "domain": domain,
            "asin":   asin,
            "stats":  1,       # Include statistics
            "history": 0,      # Don't need full price history
        }

        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        # Check tokens remaining
        tokens_left = data.get("tokensLeft", 0)
        log.info(f"  Keepa tokens remaining: {tokens_left}")

        if not data.get("products"):
            log.warning(f"  No product data for ASIN {asin}")
            return None

        product = data["products"][0]

        # Extract current BSR
        # Keepa stores BSR in csv[3] — categories BSR list
        # stats.current[3] = current BSR in root category
        stats   = product.get("stats", {})
        current = stats.get("current", [])

        # current[3] = BSR in main category
        bsr = current[3] if len(current) > 3 and current[3] > 0 else None

        # Also get category name
        categories = product.get("categories", {})
        root_cat   = list(categories.values())[0] if categories else "Unknown"

        return {
            "asin":        asin,
            "bsr":         bsr,
            "category":    root_cat,
            "title":       product.get("title", "")[:80],
            "tokens_left": tokens_left,
            "timestamp":   datetime.now().isoformat(),
        }

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 429:
            log.error(f"  Keepa rate limit hit — too many requests")
        else:
            log.error(f"  Keepa HTTP error for {asin}: {e}")
        return None

    except Exception as e:
        log.error(f"  Keepa error for {asin}: {e}")
        return None


def check_tokens():
    """Check how many Keepa tokens we have left today."""
    try:
        resp = requests.get(
            f"{KEEPA_BASE_URL}/token",
            params={"key": KEEPA_API_KEY},
            timeout=10
        )
        data = resp.json()
        return data.get("tokensLeft", 0)
    except Exception:
        return 0


# ─────────────────────────────────────────────────────────────
# BSR LOGIC
# ─────────────────────────────────────────────────────────────

def record_baseline(signal):
    """
    Record initial BSR when signal is first approved + ASIN added.
    Called once per signal.
    """
    asin = signal.get("asin")
    if not asin:
        return signal

    log.info(f"Recording BSR baseline for {signal['product']} ({asin})")
    data = get_keepa_data(asin)

    if data and data["bsr"]:
        signal["bsr_baseline"]  = data["bsr"]
        signal["bsr_current"]   = data["bsr"]
        signal["bsr_category"]  = data["category"]
        signal["bsr_checked"]   = datetime.now().isoformat()
        signal["bsr_history"]   = [{
            "date": datetime.now().isoformat(),
            "bsr":  data["bsr"],
        }]
        log.info(f"  Baseline BSR: {data['bsr']:,}")
    else:
        log.warning(f"  Could not get baseline BSR for {asin}")

    return signal


def check_bsr_movement(signal):
    """
    Check current BSR against baseline.
    Returns (signal, moved, pct_change)
    """
    asin     = signal.get("asin")
    baseline = signal.get("bsr_baseline")

    if not asin or not baseline:
        return signal, False, 0

    log.info(f"Checking BSR for {signal['product']} ({asin})")
    data = get_keepa_data(asin)

    if not data or not data["bsr"]:
        log.warning(f"  Could not fetch BSR for {asin}")
        return signal, False, 0

    current = data["bsr"]

    # BSR drop = improvement (lower is better)
    # pct_change is positive when BSR improved
    pct_change = (baseline - current) / baseline

    # Update signal
    signal["bsr_current"] = current
    signal["bsr_checked"] = datetime.now().isoformat()

    # Append to history
    if "bsr_history" not in signal:
        signal["bsr_history"] = []
    signal["bsr_history"].append({
        "date": datetime.now().isoformat(),
        "bsr":  current,
    })

    # Keep last 30 data points
    signal["bsr_history"] = signal["bsr_history"][-30:]

    moved = pct_change >= BSR_MOVE_THRESHOLD

    log.info(f"  Baseline: {baseline:,} → Current: {current:,} "
             f"({'+' if pct_change > 0 else ''}{pct_change*100:.1f}%) "
             f"{'✅ MOVED' if moved else '⏳ waiting'}")

    if moved and signal.get("bsrMoved") != "Yes":
        signal["bsrMoved"]         = "Yes"
        signal["bsr_moved_date"]   = datetime.now().isoformat()
        signal["bsr_moved_pct"]    = round(pct_change * 100, 1)

    return signal, moved, pct_change


# ─────────────────────────────────────────────────────────────
# DAILY MONITOR — called by scheduler
# ─────────────────────────────────────────────────────────────

def run_bsr_monitor(signals, save_fn, alert_fn):
    """
    Check BSR for all approved signals with an ASIN.
    Called daily by the scheduler in app.py.

    signals  — list of signal dicts
    save_fn  — function to save updated signals
    alert_fn — function to send email alert
    """
    log.info("── BSR Monitor starting ──")

    # Check token balance first
    tokens = check_tokens()
    log.info(f"Keepa tokens available: {tokens}")

    if tokens < 10:
        log.warning("Low Keepa tokens — skipping BSR check today")
        return signals

    # Find signals that need monitoring
    to_monitor = [
        s for s in signals
        if s.get("asin")                        # Has ASIN
        and s.get("action") == "APPROVE"        # Was approved
        and s.get("bsrMoved") != "Yes"          # Not already converted
        and s.get("bsrMoved") != "No"           # Not already confirmed no-move
    ]

    log.info(f"Monitoring {len(to_monitor)} signals")

    # Limit to token budget (each check = ~10 tokens)
    max_checks = min(len(to_monitor), tokens // 10)
    to_monitor = to_monitor[:max_checks]

    newly_converted = []

    for signal in to_monitor:
        # Skip if checked in last 20 hours (don't double-check)
        last_check = signal.get("bsr_checked")
        if last_check:
            hours_ago = (datetime.now() - datetime.fromisoformat(last_check)).total_seconds() / 3600
            if hours_ago < 20:
                log.info(f"  Skipping {signal['product'][:30]} — checked {hours_ago:.0f}hrs ago")
                continue

        signal, moved, pct = check_bsr_movement(signal)

        if moved:
            newly_converted.append((signal, pct))

        # Small pause between Keepa calls
        import time
        time.sleep(2)

    # Save updated signals
    save_fn(signals)

    # Fire alerts for newly converted signals
    for signal, pct in newly_converted:
        log.info(f"🎯 BSR confirmed for {signal['product']} — alerting")
        alert_fn(signal, bsr_alert=True, pct_change=pct)

    log.info(f"── BSR Monitor done. {len(newly_converted)} newly converted ──")
    return signals


# ─────────────────────────────────────────────────────────────
# HELPER — find ASIN from Amazon URL (convenience)
# ─────────────────────────────────────────────────────────────

def extract_asin_from_url(url):
    """
    Extract ASIN from an Amazon product URL.
    Handles most common URL formats.

    Example:
      https://amazon.com/dp/B07Y9YYH3B
      https://amazon.com/product-name/dp/B07Y9YYH3B/ref=...
    """
    import re
    match = re.search(r"/dp/([A-Z0-9]{10})", url)
    if match:
        return match.group(1)

    # Also try /gp/product/ format
    match = re.search(r"/gp/product/([A-Z0-9]{10})", url)
    if match:
        return match.group(1)

    return None
