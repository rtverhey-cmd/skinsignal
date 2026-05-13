"""
SKINSIGNAL — Reddit Public JSON Scraper
No API credentials needed. Uses Reddit's public JSON endpoints.

Reddit serves all public post data as JSON without authentication.
This is the same data Reddit's own mobile site uses.

INSTALL:
    pip install requests pytrends

RUN:
    python signal_scraper.py

OUTPUT:
    signals.json — import into dashboard
"""

import requests
import json
import time
import re
import os
import logging
from datetime import datetime

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────

SUBREDDITS = [
    "SkincareAddiction",
    "AsianBeauty",
    "beauty",
    "MakeupAddiction",
]

# Polite user agent — identifies our scraper honestly
USER_AGENT  = "skinsignal/1.0 (public data reader; contact: hello@skinsignal.co)"

POSTS_PER_SUBREDDIT = 50
MIN_UPVOTES         = 300
MIN_COMMENTS        = 20
TREND_RISE_MIN      = 40
SIGNALS_FILE        = "signals.json"
LOG_FILE            = "scraper.log"

# ─────────────────────────────────────────────────────────────
# BRANDS
# ─────────────────────────────────────────────────────────────

BRANDS = [
    "cosrx", "some by mi", "beauty of joseon", "anua", "torriden",
    "round lab", "skin1004", "isntree", "pyunkang yul", "klairs",
    "laneige", "innisfree", "etude", "missha", "mediheal",
    "goodal", "axis-y", "ma:nyo", "manyo", "tirtir", "numbuzin",
    "abib", "heimish", "farmacy", "cerave", "la roche-posay",
    "neutrogena", "aveeno", "paula's choice", "the ordinary",
    "drunk elephant", "tatcha", "sk-ii", "skinceuticals",
    "olay", "belif", "first aid beauty", "kiehl's",
]

INTENT_PHRASES = [
    "where to buy", "where can i buy", "link?", "asin?",
    "amazon link", "just ordered", "just bought", "in my cart",
    "is this on amazon", "sephora link", "where did you get",
]

# ─────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# REDDIT PUBLIC JSON
# ─────────────────────────────────────────────────────────────

def get_hot_posts(subreddit, limit=50):
    """
    Fetch hot posts from a subreddit using public JSON endpoint.
    No authentication required.
    """
    url     = f"https://www.reddit.com/r/{subreddit}/hot.json"
    headers = {"User-Agent": USER_AGENT}
    params  = {"limit": limit}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data  = resp.json()
        posts = data["data"]["children"]
        return [p["data"] for p in posts]

    except requests.exceptions.HTTPError as e:
        log.error(f"HTTP error fetching r/{subreddit}: {e}")
        return []
    except requests.exceptions.Timeout:
        log.error(f"Timeout fetching r/{subreddit}")
        return []
    except Exception as e:
        log.error(f"Error fetching r/{subreddit}: {e}")
        return []


def get_post_comments(subreddit, post_id, limit=30):
    """
    Fetch top comments for a post using public JSON endpoint.
    No authentication required.
    """
    url     = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}.json"
    headers = {"User-Agent": USER_AGENT}
    params  = {"limit": limit, "depth": 1}

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        # Comments are in the second element of the response
        if len(data) < 2:
            return []

        comments = data[1]["data"]["children"]
        return [
            c["data"].get("body", "")
            for c in comments
            if c["kind"] == "t1"
        ]

    except Exception as e:
        log.error(f"Error fetching comments for {post_id}: {e}")
        return []

# ─────────────────────────────────────────────────────────────
# PRODUCT EXTRACTION
# ─────────────────────────────────────────────────────────────

def extract_product(text):
    text_lower = text.lower()
    for brand in BRANDS:
        if brand in text_lower:
            idx   = text_lower.find(brand)
            raw   = text[idx: idx + len(brand) + 45]
            clean = re.sub(r"[^\w\s\+\-]", " ", raw)
            clean = " ".join(clean.split()).strip()
            if len(clean) >= len(brand):
                return clean[:60]
    return None


def count_intent(comments):
    count = 0
    for comment in comments:
        body = comment.lower()
        for phrase in INTENT_PHRASES:
            if phrase in body:
                count += 1
                break
    return count

# ─────────────────────────────────────────────────────────────
# GOOGLE TRENDS
# ─────────────────────────────────────────────────────────────

def check_trends(product_name):
    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
        pt.build_payload([product_name], timeframe="now 7-d", geo="US")
        df = pt.interest_over_time()

        if df.empty or product_name not in df.columns:
            return {"delta": 0, "rising": False, "values": []}

        values = df[product_name].tolist()
        if len(values) < 4:
            return {"delta": 0, "rising": False, "values": values}

        mid   = len(values) // 2
        early = sum(values[:mid])  / max(mid, 1)
        late  = sum(values[mid:])  / max(len(values) - mid, 1)
        delta = round(((late - early) / early) * 100, 1) if early > 0 \
                else (100.0 if late > 0 else 0.0)

        return {
            "delta":  delta,
            "rising": delta >= TREND_RISE_MIN,
            "values": [int(v) for v in values[-12:]],
        }
    except Exception as e:
        return {"delta": 0, "rising": False, "values": [], "error": str(e)}

# ─────────────────────────────────────────────────────────────
# SCORING
# ─────────────────────────────────────────────────────────────

def score_signal(upvotes, intent_count, trends):
    pts = {
        "reddit": min(25, int((upvotes / 3000) * 25)),
        "intent": min(25, intent_count * 8),
    }
    delta = trends.get("delta", 0)
    pts["trends"] = (
        35 if delta >= 200 else
        25 if delta >= 100 else
        16 if delta >= 50  else
        8  if delta >= TREND_RISE_MIN else
        0
    )

    score  = min(100, int(sum(pts.values()) * (100 / 85)))
    action = "APPROVE" if score >= 75 else "WATCH" if score >= 50 else "DISCARD"
    return score, pts, action

# ─────────────────────────────────────────────────────────────
# MAIN SCRAPER
# ─────────────────────────────────────────────────────────────

def scrape_subreddit(name, seen_ids):
    signals = []
    log.info(f"Scanning r/{name} ...")

    posts = get_hot_posts(name, limit=POSTS_PER_SUBREDDIT)
    log.info(f"  Got {len(posts)} posts")

    for post in posts:
        post_id = post.get("id", "")

        # Skip already seen
        if post_id in seen_ids:
            continue

        # Basic filters
        score    = post.get("score", 0)
        num_comments = post.get("num_comments", 0)

        if score < MIN_UPVOTES:
            continue
        if num_comments < MIN_COMMENTS:
            continue

        # Must mention a known product
        title   = post.get("title", "")
        selftext = post.get("selftext", "")[:300]
        product = extract_product(title + " " + selftext)

        if not product:
            continue

        log.info(f"  [{score:,} upvotes] {title[:65]}")
        log.info(f"  → Product: {product}")

        # Fetch comments via public JSON
        comments = get_post_comments(name, post_id)
        intent   = count_intent(comments)
        log.info(f"  → Intent signals: {intent}")

        # Google Trends check
        trends = check_trends(product)
        log.info(f"  → Trends delta: {trends['delta']}%")

        # Score
        sig_score, breakdown, action = score_signal(score, intent, trends)
        log.info(f"  → Score: {sig_score}/100  Action: {action}")

        signals.append({
            "id":               post_id,
            "timestamp":        datetime.now().isoformat(),
            "product":          product,
            "post_title":       title[:120],
            "subreddit":        name,
            "upvotes":          score,
            "comments":         num_comments,
            "intent":           intent,
            "post_url":         f"https://reddit.com{post.get('permalink', '')}",
            "trends":           trends,
            "score":            sig_score,
            "breakdown":        breakdown,
            "action":           action,
            "source":           "Reddit",
            "engagement":       str(score),
            "problemSolving":   "Yes",
            "price":            "",
            "notes":            "",
            "bsrMoved":         "Pending",
            "pageBuilt":        False,
            "commissionEarned": 0,
            "emailSent":        False,
            "asin":             None,
            "bsr_baseline":     None,
            "bsr_current":      None,
            "bsr_history":      [],
            "bsr_checked":      None,
        })

        # Polite delay between posts
        time.sleep(2)

    log.info(f"  Done r/{name}: {len(signals)} new signals")
    return signals


def main():
    log.info("=" * 55)
    log.info("  SKINSIGNAL — Signal Detection Run")
    log.info(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    log.info("  Mode: Public JSON (no credentials needed)")
    log.info("=" * 55)

    # Load existing signals
    if os.path.exists(SIGNALS_FILE):
        with open(SIGNALS_FILE) as f:
            existing = json.load(f)
        seen_ids = {s["id"] for s in existing}
        log.info(f"Loaded {len(existing)} existing signals")
    else:
        existing = []
        seen_ids = set()

    new_signals = []

    for name in SUBREDDITS:
        try:
            found = scrape_subreddit(name, seen_ids)
            new_signals.extend(found)
            seen_ids.update(s["id"] for s in found)
            # Polite pause between subreddits
            time.sleep(5)
        except Exception as e:
            log.error(f"Error on r/{name}: {e}")

    # Save
    combined = new_signals + existing
    with open(SIGNALS_FILE, "w") as f:
        json.dump(combined, f, indent=2)

    # Summary
    approve = [s for s in new_signals if s["action"] == "APPROVE"]
    watch   = [s for s in new_signals if s["action"] == "WATCH"]
    discard = [s for s in new_signals if s["action"] == "DISCARD"]

    log.info("")
    log.info("─" * 55)
    log.info(f"  New signals:  {len(new_signals)}")
    log.info(f"  APPROVE:      {len(approve)}")
    for s in approve:
        log.info(f"    {s['score']:3d}/100  {s['product'][:45]}")
    log.info(f"  WATCH:        {len(watch)}")
    log.info(f"  DISCARD:      {len(discard)}")
    log.info(f"  Total saved:  {len(combined)}")
    log.info("─" * 55)

    if approve:
        log.info("")
        log.info("  ⚡ APPROVE signals waiting — open dashboard")

    log.info("")


if __name__ == "__main__":
    main()
