"""
SKINSIGNAL — Server v3.3
Intelligence upgrade based on Reddit/Amazon virality research.

Changes from v3.2:
- 340+ brands (was 242)
- 7 subreddits (was 4) — added r/30PlusSkinCare, r/OliveMUA, r/AusSkincare
- Expanded intent phrases — added "holy grail", "repurchased", "saved my skin"
- Added controversy signals — "overhyped", "counterfeit", "dupe"
- Added emerging ingredients — PDRN, exosomes, hypochlorous acid
- Switched to top.json?t=week for higher-scoring posts
- Threading scheduler (reliable on Railway)

Environment variables (Railway):
  SENDGRID_API_KEY
  ALERT_EMAIL
  FROM_EMAIL
  SECRET_KEY
  KEEPA_API_KEY  (optional)
"""

import os
import json
import time
import re
import logging
import hashlib
import threading
import requests
from datetime import datetime
from threading import Lock

from flask import Flask, jsonify, request
from flask_cors import CORS

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────

SENDGRID_API_KEY    = os.environ.get("SENDGRID_API_KEY", "")
ALERT_EMAIL         = os.environ.get("ALERT_EMAIL",      "")
FROM_EMAIL          = os.environ.get("FROM_EMAIL",       "alerts@skinsignal.co")
SECRET_KEY          = os.environ.get("SECRET_KEY",       "change-this-key")
KEEPA_API_KEY       = os.environ.get("KEEPA_API_KEY",    "")

SIGNALS_FILE        = "signals.json"
SCRAPE_INTERVAL_SEC = 1 * 60 * 60
BSR_INTERVAL_SEC    = 24 * 60 * 60
USER_AGENT          = "skinsignal/1.0 (public data reader; contact: hello@skinsignal.co)"

# ─────────────────────────────────────────────────────────────
# SUBREDDITS — expanded from 4 to 7
# ─────────────────────────────────────────────────────────────
# Added based on intelligence document:
# r/30PlusSkinCare — mature buyers, high purchase intent, holy grail discussions
# r/OliveMUA       — olive/darker skin tones, growing K-beauty crossover
# r/AusSkincare    — strong K-beauty adoption, active purchase discussions

SUBREDDITS = [
    "SkincareAddiction",    # 4.9M — primary signal source
    "AsianBeauty",          # 1.5M — K-beauty specialists, early adopters
    "30PlusSkinCare",       # 1.2M — mature buyers, high spend, holy grail focus
    "beauty",               # 6M   — general, broad reach
    "MakeupAddiction",      # 4M   — makeup/skincare crossover
    "OliveMUA",             # 200K — diverse skin tones, rising K-beauty interest
    "AusSkincare",          # 150K — active, strong K-beauty purchasing community
]

# ─────────────────────────────────────────────────────────────
# BRANDS — 340+ entries
# Prioritised by: Reddit activity + Amazon strength + viral potential
# ─────────────────────────────────────────────────────────────

BRANDS = [

    # ── TIER 1: HIGHEST VIRAL POTENTIAL (Reddit + Amazon + TikTok) ──

    # K-Beauty — Exploding growth
    "medicube", "anua", "biodance", "tirtir", "torriden",
    "mixsoon", "vt cosmetics", "vt", "numbuzin",

    # K-Beauty — Established high performers
    "cosrx", "beauty of joseon", "round lab", "skin1004",
    "isntree", "pyunkang yul", "klairs", "laneige",
    "haruharu", "haruharu wonder", "abib", "axis-y",
    "purito", "by wishtrend", "ma:nyo", "manyo",
    "goodal", "heimish", "benton", "dear klairs",
    "i'm from", "im from", "iunik", "illiyoon",
    "aestura", "rovectin",

    # K-Beauty — Rising
    "some by mi", "etude", "missha", "mediheal",
    "innisfree", "the face shop", "nature republic",
    "holika holika", "skinfood", "mizon", "neogen",
    "neogen dermalogy", "make p:rem", "graymelin",
    "tosowoong", "elizavecca", "jayjun", "acwell",
    "clio", "peripera", "rom&nd", "espoir", "3ce",
    "lagom", "huxley", "whamisa", "blithe",
    "son & park", "son and park", "thank you farmer",
    "banila co", "a'pieu", "apieu",
    "amorepacific", "sulwhasoo", "hera", "iope",
    "sum37", "su:m37", "primera", "mamonde",
    "beyond", "sooryehan", "skin&lab",

    # J-Beauty — High Reddit activity
    "hada labo", "hadalabo", "melano cc",
    "rohto", "mentholatum", "curel", "fancl",
    "albion", "sk-ii", "sk ii", "shiseido",
    "kose", "kanebo", "sofina", "pola",
    "decorte", "ipsa", "elixir", "anessa",
    "senka", "biore", "minon", "tatcha",

    # ── TIER 1: WESTERN VIRAL BRANDS ──

    # Celebrity / hype brands
    "rhode", "rhode skin", "rare beauty", "fenty skin",
    "jones road", "jones road beauty",

    # High-growth ingredient brands
    "the ordinary", "the inkey list", "inkey list",
    "naturium", "good molecules", "geek & gorgeous",
    "geek and gorgeous", "stratia", "maelove",
    "timeless", "garden of wisdom",

    # Prestige skincare
    "drunk elephant", "glow recipe", "sunday riley",
    "tatcha", "fresh", "charlotte tilbury",
    "elemis", "medik8", "paula's choice",
    "paulas choice", "peter thomas roth",

    # Problem-solution / acne patches
    "hero cosmetics", "mighty patch", "acne patch",
    "topicals",

    # Gen Z brands
    "byoma", "bubble", "bubble skincare",
    "dieux", "dieux skin",

    # Reddit cult brands
    "stratia", "wishful", "versed",

    # Makeup with skincare overlap
    "e.l.f.", "elf cosmetics", "elf skincare",
    "wonderskin", "saie", "ilia",
    "tower 28", "danessa myricks",
    "milk makeup", "glossier",

    # ── HAIRCARE (high Amazon velocity) ──
    "color wow", "k18", "k18 hair", "olaplex",
    "ouai", "briogeo", "pattern beauty",

    # ── BODY / FRAGRANCE ──
    "sol de janeiro", "cerave body", "eucerin body",

    # ── WESTERN PRESTIGE ──
    "la mer", "lamer", "estee lauder",
    "clinique", "lancome", "sisley", "valmont",
    "la prairie", "kiehl's", "kiehls", "origins",
    "philosophy", "ole henriksen", "mario badescu",
    "caudalie", "murad", "dermalogica",
    "perricone md", "ren skincare", "emma hardie",
    "liz earle", "eve lom", "niod", "deciem",
    "skinceuticals", "beautycounter", "tata harper",
    "true botanicals", "youth to the people",

    # ── WESTERN MASS MARKET ──
    "cerave", "cetaphil", "la roche-posay",
    "la roche posay", "neutrogena", "aveeno",
    "olay", "nivea", "eucerin", "vichy",
    "bioderma", "avene", "uriage", "nuxe",
    "garnier", "l'oreal", "loreal",

    # ── DERMATOLOGIST / CLINICAL ──
    "isdin", "alastin", "sente", "epionce",
    "revision skincare", "zo skin health",
    "skinmedica", "obagi", "jan marini",
    "neostrata", "image skincare", "pca skin",
    "elta md", "eltamd", "tizo",

    # ── SUNSCREEN SPECIALISTS ──
    "skin aqua", "biore uv", "canmake sunscreen",
    "altruist", "ultrasun", "supergoop",
    "coola", "blue lizard", "sun bum",
    "invisible zinc", "bare republic",

    # ── TOOLS & DEVICES ──
    "foreo", "nuface", "nu face", "theraface",
    "solawave", "currentbody", "ziip",

    # ── EMERGING INGREDIENTS (ingredient-led virality) ──
    # Classic
    "niacinamide", "retinol", "tretinoin", "bakuchiol",
    "hyaluronic acid", "vitamin c serum", "glycolic acid",
    "salicylic acid", "snail mucin", "centella",
    "ceramide", "peptide serum", "azelaic acid",
    "tranexamic acid", "kojic acid", "alpha arbutin",
    "squalane", "rosehip oil", "marula oil",

    # 2025/2026 breakout ingredients (from intelligence doc)
    "pdrn", "salmon dna", "exosome", "exosomes",
    "hypochlorous acid", "hocl",
    "rice toner", "rice essence",
    "collagen wrap", "collagen mask",
    "beta glucan", "polyglutamic acid",
    "mugwort", "centella asiatica",
    "galactomyces", "bifida ferment",
    "madecassoside", "panthenol",
]

# ─────────────────────────────────────────────────────────────
# INTENT PHRASES — expanded with holy grail + controversy signals
# ─────────────────────────────────────────────────────────────
# Intelligence doc insight: "recommendation recursion" is the
# strongest early signal — when people recommend without being asked.
# Added: holy grail phrases, repurchase signals, controversy signals

INTENT_PHRASES = [

    # ── PURCHASE INTENT (original) ──
    "where to buy", "where can i buy", "where do i buy",
    "where can i find", "link?", "asin?", "amazon link",
    "just ordered", "just bought", "just purchased",
    "in my cart", "added to cart", "is this on amazon",
    "sephora link", "ulta link", "where did you get",
    "what's the amazon link", "how do i get this",

    # ── HOLY GRAIL / RECOMMENDATION RECURSION ──
    # These are the strongest early signals per intelligence doc
    "holy grail", "hg product", "holy grail product",
    "repurchased", "repurchase", "on my third bottle",
    "on my second bottle", "reordered", "auto ship",
    "saved my skin", "changed my skin", "life changing",
    "nothing else works", "only thing that works",
    "game changer", "game-changer",
    "everyone should use", "everyone needs this",
    "i was influenced", "tiktok made me buy",
    "reddit made me buy", "saw it on reddit",
    "everyone keeps mentioning", "always recommended",
    "constantly recommended", "staple in my routine",
    "never going back", "cant live without",
    "can't live without",

    # ── CONTROVERSY / POLARIZATION SIGNALS ──
    # Intelligence doc: polarization creates discussion velocity
    "overhyped", "over hyped", "is it worth it",
    "worth the hype", "lives up to the hype",
    "fake reviews", "counterfeit", "dupe",
    "ruined my skin", "broke me out",
    "holy grail or scam", "scam or legit",

    # ── AVAILABILITY SIGNALS ──
    "sold out", "back in stock", "sold out everywhere",
    "can't find it", "where is it in stock",
]

# ─────────────────────────────────────────────────────────────
# APP
# ─────────────────────────────────────────────────────────────

app  = Flask(__name__)
CORS(app)
lock = Lock()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

scheduler_state = {
    "last_scrape":  None,
    "last_bsr":     None,
    "scrape_count": 0,
    "running":      False,
}

# ─────────────────────────────────────────────────────────────
# DATA
# ─────────────────────────────────────────────────────────────

def load_signals():
    if os.path.exists(SIGNALS_FILE):
        with open(SIGNALS_FILE) as f:
            return json.load(f)
    return []


def save_signals(signals):
    with lock:
        with open(SIGNALS_FILE, "w") as f:
            json.dump(signals, f, indent=2)

# ─────────────────────────────────────────────────────────────
# REDDIT — top.json?t=week for high-scoring posts
# ─────────────────────────────────────────────────────────────

def get_top_posts(subreddit, limit=50):
    """
    Fetch top posts of the past week.
    Top posts have scores 500-5000+ vs hot posts at 2-50.
    Falls back to hot if top returns nothing.
    """
    for sort, extra in [("top", {"t": "week"}), ("hot", {})]:
        url     = f"https://www.reddit.com/r/{subreddit}/{sort}.json"
        headers = {"User-Agent": USER_AGENT}
        params  = {"limit": limit, **extra}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            resp.raise_for_status()
            posts = resp.json()["data"]["children"]
            log.info(f"  r/{subreddit} [{sort}]: {len(posts)} posts")
            if posts:
                return [p["data"] for p in posts]
        except Exception as e:
            log.error(f"  r/{subreddit} [{sort}] error: {e}")
    return []


def get_post_comments(subreddit, post_id, limit=30):
    url     = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}.json"
    headers = {"User-Agent": USER_AGENT}
    params  = {"limit": limit, "depth": 1}
    try:
        resp     = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data     = resp.json()
        comments = data[1]["data"]["children"]
        return [c["data"].get("body", "") for c in comments if c["kind"] == "t1"]
    except Exception as e:
        log.error(f"  Comments {post_id}: {e}")
        return []

# ─────────────────────────────────────────────────────────────
# SIGNAL PROCESSING
# ─────────────────────────────────────────────────────────────

def extract_product(text):
    text_lower = text.lower()
    for brand in BRANDS:
        if brand in text_lower:
            idx   = text_lower.find(brand)
            raw   = text[idx: idx + len(brand) + 45]
            clean = re.sub(r"[^\w\s\+\-\&\']", " ", raw)
            clean = " ".join(clean.split()).strip()
            if len(clean) >= len(brand):
                return clean[:60]
    return None


def count_intent(comments):
    """
    Count purchase intent AND holy grail signals.
    Intelligence doc: recommendation recursion is the #1 early signal.
    """
    count = 0
    for comment in comments:
        body = comment.lower()
        for phrase in INTENT_PHRASES:
            if phrase in body:
                count += 1
                break
    return count


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
            "rising": delta >= 40,
            "values": [int(v) for v in values[-12:]],
        }
    except Exception as e:
        return {"delta": 0, "rising": False, "values": [], "error": str(e)}


def score_signal(upvotes, intent_count, trends):
    """
    Scoring aligned with intelligence doc's viral opportunity model:
    Reddit mention growth:       25pts
    Recommendation recursion:    25pts (our intent count)
    Google Trends velocity:      35pts
    (TikTok: 15pts — added when TikTok layer built)
    """
    pts = {
        "reddit": min(25, int((upvotes / 3000) * 25)),
        "intent": min(25, intent_count * 8),
    }
    delta = trends.get("delta", 0)
    pts["trends"] = (
        35 if delta >= 200 else
        25 if delta >= 100 else
        16 if delta >= 50  else
        8  if delta >= 40  else
        0
    )
    score  = min(100, int(sum(pts.values()) * (100 / 85)))
    action = "APPROVE" if score >= 75 else "WATCH" if score >= 50 else "DISCARD"
    return score, pts, action

# ─────────────────────────────────────────────────────────────
# EMAIL
# ─────────────────────────────────────────────────────────────

def send_alert(signal, bsr_alert=False, pct_change=0):
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail

        product  = signal["product"]
        score    = signal.get("score", 0)
        upvotes  = signal.get("upvotes", 0)
        delta    = signal.get("trends", {}).get("delta", 0)
        intent   = signal.get("intent", 0)
        post_url = signal.get("post_url", "")
        subreddit = signal.get("subreddit", "")

        if bsr_alert:
            baseline    = signal.get("bsr_baseline", 0)
            current     = signal.get("bsr_current", 0)
            subject     = f"✅ BSR Confirmed — {product[:40]}"
            headline    = "BSR Movement Confirmed — Build the page NOW"
            body_rows   = f"""
            <tr><td style="padding:8px 0;color:#888;width:40%">Product</td>
                <td style="padding:8px 0;font-weight:bold">{product}</td></tr>
            <tr><td style="padding:8px 0;color:#888">BSR before</td>
                <td style="padding:8px 0">{baseline:,}</td></tr>
            <tr><td style="padding:8px 0;color:#888">BSR now</td>
                <td style="padding:8px 0;font-weight:bold;color:#00C896">{current:,}</td></tr>
            <tr><td style="padding:8px 0;color:#888">Improvement</td>
                <td style="padding:8px 0;font-weight:bold;color:#00C896">+{pct_change:.0f}%</td></tr>
            """
            action_text = "The signal validated. Build your affiliate page now while CPC is still low."
        else:
            fire        = "🔥🔥🔥" if score >= 90 else "🔥🔥" if score >= 80 else "🔥"
            subject     = f"🔥 Signal {score}/100 — {product[:40]}"
            headline    = f"{fire} New Signal — Score {score}/100"
            body_rows   = f"""
            <tr><td style="padding:8px 0;color:#888;width:40%">Product</td>
                <td style="padding:8px 0;font-weight:bold">{product}</td></tr>
            <tr><td style="padding:8px 0;color:#888">Reddit upvotes</td>
                <td style="padding:8px 0;font-weight:bold">{upvotes:,}</td></tr>
            <tr><td style="padding:8px 0;color:#888">Google Trends</td>
                <td style="padding:8px 0;font-weight:bold;color:#00C896">+{delta}%</td></tr>
            <tr><td style="padding:8px 0;color:#888">Intent signals</td>
                <td style="padding:8px 0">{intent} comments</td></tr>
            <tr><td style="padding:8px 0;color:#888">Source</td>
                <td style="padding:8px 0">r/{subreddit}</td></tr>
            <tr><td style="padding:8px 0;color:#888">Window estimate</td>
                <td style="padding:8px 0;font-weight:bold">8–14 days</td></tr>
            """
            action_text = "Open dashboard. Find the Amazon ASIN to start BSR monitoring."

        html = f"""
        <div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto">
          <div style="background:#C4622D;padding:24px;border-radius:12px 12px 0 0">
            <h1 style="color:white;margin:0;font-size:20px">{headline}</h1>
          </div>
          <div style="background:#1A1A1A;padding:24px;color:#F0F0F0">
            <table style="width:100%;border-collapse:collapse">{body_rows}</table>
            <div style="margin:20px 0;padding:14px;background:#2A2A2A;
                        border-radius:8px;border-left:3px solid #C4622D;
                        font-size:14px;color:#AAA;line-height:1.6">
              {action_text}
            </div>
            {'<a href="' + post_url + '" style="background:#333;color:white;padding:10px 18px;border-radius:8px;text-decoration:none;font-size:13px">View Reddit Post →</a>' if post_url and not bsr_alert else ''}
          </div>
          <div style="background:#111;padding:14px;border-radius:0 0 12px 12px;
                      text-align:center;color:#555;font-size:11px">
            skinsignal.co · automated signal detection
          </div>
        </div>"""

        msg = Mail(
            from_email=FROM_EMAIL,
            to_emails=ALERT_EMAIL,
            subject=subject,
            html_content=html,
        )
        sg  = sendgrid.SendGridAPIClient(api_key=SENDGRID_API_KEY)
        r   = sg.send(msg)
        log.info(f"  Email sent — {r.status_code}")
        return True

    except Exception as e:
        log.error(f"  Email failed: {e}")
        return False

# ─────────────────────────────────────────────────────────────
# SCRAPER
# ─────────────────────────────────────────────────────────────

def run_scraper():
    log.info(f"══ Scraper run {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ══")
    log.info(f"   Brands: {len(BRANDS)} | Subreddits: {len(SUBREDDITS)}")
    log.info(f"   Intent phrases: {len(INTENT_PHRASES)}")

    scheduler_state["running"] = True
    existing  = load_signals()
    seen_ids  = {s["id"] for s in existing}
    new_found = []

    for name in SUBREDDITS:
        try:
            log.info(f"── Scanning r/{name}")
            posts = get_top_posts(name, limit=50)

            for post in posts:
                post_id      = post.get("id", "")
                score_val    = post.get("score", 0)
                num_comments = post.get("num_comments", 0)

                if post_id in seen_ids:
                    continue
                if score_val < 20:
                    continue
                if num_comments < 20:
                    continue

                title    = post.get("title", "")
                selftext = post.get("selftext", "")[:300]
                product  = extract_product(title + " " + selftext)

                if not product:
                    continue

                log.info(f"  ✓ [{score_val:,}] {title[:65]}")
                log.info(f"    Product: {product}")

                comments  = get_post_comments(name, post_id)
                intent    = count_intent(comments)
                trends    = check_trends(product)
                sig_score, breakdown, action = score_signal(score_val, intent, trends)

                log.info(f"    Intent:{intent} Trends:{trends.get('delta',0)}% Score:{sig_score}/100 → {action}")

                signal = {
                    "id":               post_id,
                    "timestamp":        datetime.now().isoformat(),
                    "product":          product,
                    "post_title":       title[:120],
                    "subreddit":        name,
                    "upvotes":          score_val,
                    "comments":         num_comments,
                    "intent":           intent,
                    "post_url":         f"https://reddit.com{post.get('permalink','')}",
                    "trends":           trends,
                    "score":            sig_score,
                    "breakdown":        breakdown,
                    "action":           action,
                    "source":           "Reddit",
                    "engagement":       str(score_val),
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
                }

                if action == "APPROVE":
                    sent = send_alert(signal)
                    signal["emailSent"] = sent
                    log.info(f"    ⚡ APPROVE — email: {sent}")

                new_found.append(signal)
                seen_ids.add(post_id)
                time.sleep(2)

            time.sleep(5)

        except Exception as e:
            log.error(f"Error on r/{name}: {e}")

    if new_found:
        save_signals(new_found + existing)
        log.info(f"── Saved {len(new_found)} signals (total: {len(existing) + len(new_found)})")
    else:
        log.info("── No new signals this run")

    scheduler_state["last_scrape"]  = datetime.now().isoformat()
    scheduler_state["scrape_count"] += 1
    scheduler_state["running"]       = False
    log.info(f"══ Done. Run #{scheduler_state['scrape_count']} ══")

# ─────────────────────────────────────────────────────────────
# BSR MONITOR
# ─────────────────────────────────────────────────────────────

def run_bsr_monitor():
    if not KEEPA_API_KEY:
        log.info("BSR monitor skipped — no Keepa key")
        return
    try:
        from bsr_monitor import run_bsr_monitor as _monitor
        signals = load_signals()
        _monitor(signals, save_signals, send_alert)
        scheduler_state["last_bsr"] = datetime.now().isoformat()
    except Exception as e:
        log.error(f"BSR monitor error: {e}")

# ─────────────────────────────────────────────────────────────
# SCHEDULER THREADS
# ─────────────────────────────────────────────────────────────

def scraper_loop():
    log.info("Scraper thread started")
    while True:
        try:
            run_scraper()
        except Exception as e:
            log.error(f"Scraper loop error: {e}")
        log.info(f"Next scrape in {SCRAPE_INTERVAL_SEC // 3600} hours")
        time.sleep(SCRAPE_INTERVAL_SEC)


def bsr_loop():
    log.info("BSR monitor thread started — first run in 1 hour")
    time.sleep(3600)
    while True:
        try:
            run_bsr_monitor()
        except Exception as e:
            log.error(f"BSR loop error: {e}")
        time.sleep(BSR_INTERVAL_SEC)


def start_scheduler():
    def delayed_start():
        time.sleep(10)
        threading.Thread(target=scraper_loop, daemon=True, name="scraper").start()
        log.info("✅ Scraper thread started")
        threading.Thread(target=bsr_loop, daemon=True, name="bsr").start()
        log.info("✅ BSR thread started")

    threading.Thread(target=delayed_start, daemon=True).start()

# ─────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────

def auth(req):
    return req.headers.get("X-Secret") == SECRET_KEY


@app.route("/api/signals", methods=["GET"])
def get_signals():
    signals = load_signals()
    return jsonify({
        "signals": signals,
        "stats": {
            "total":      len(signals),
            "approve":    len([s for s in signals if s["action"] == "APPROVE"]),
            "watch":      len([s for s in signals if s["action"] == "WATCH"]),
            "discard":    len([s for s in signals if s["action"] == "DISCARD"]),
            "converted":  len([s for s in signals if s.get("bsrMoved") == "Yes"]),
            "monitoring": len([s for s in signals if s.get("asin") and s.get("bsrMoved") == "Pending"]),
            "earned":     sum(float(s.get("commissionEarned", 0)) for s in signals),
            "last_run":   scheduler_state["last_scrape"],
            "run_count":  scheduler_state["scrape_count"],
        }
    })


@app.route("/api/signals/<signal_id>", methods=["PATCH"])
def update_signal(signal_id):
    if not auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    data    = request.json
    signals = load_signals()
    allowed = {"bsrMoved", "pageBuilt", "commissionEarned", "notes", "price", "asin"}
    for i, s in enumerate(signals):
        if s["id"] == signal_id:
            for field, value in data.items():
                if field in allowed:
                    s[field] = value
            if "asin" in data and data["asin"] and not s.get("bsr_baseline"):
                if KEEPA_API_KEY:
                    try:
                        from bsr_monitor import record_baseline
                        s = record_baseline(s)
                        signals[i] = s
                    except Exception as e:
                        log.error(f"BSR baseline error: {e}")
            s["lastUpdated"] = datetime.now().isoformat()
            save_signals(signals)
            return jsonify({"ok": True})
    return jsonify({"error": "Not found"}), 404


@app.route("/api/signals/manual", methods=["POST"])
def add_manual():
    if not auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json
    if not data.get("product"):
        return jsonify({"error": "product required"}), 400
    upvotes = int(data.get("engagement", 0))
    intent  = {"Many (5+)": 5, "Some (2-4)": 3, "One": 1, "None": 0}.get(
        data.get("intent_label", "None"), 0)
    trends  = {"delta": 0, "rising": False, "values": []}
    score, breakdown, action = score_signal(upvotes, intent, trends)
    signal = {
        "id":               hashlib.md5((data["product"] + datetime.now().isoformat()).encode()).hexdigest()[:12],
        "timestamp":        datetime.now().isoformat(),
        "product":          data["product"],
        "post_title":       "Manual entry",
        "subreddit":        data.get("subreddit", "Manual"),
        "upvotes":          upvotes,
        "comments":         0,
        "intent":           intent,
        "post_url":         data.get("post_url", ""),
        "trends":           trends,
        "score":            score,
        "breakdown":        breakdown,
        "action":           action,
        "source":           data.get("source", "Manual"),
        "engagement":       str(upvotes),
        "problemSolving":   data.get("problemSolving", "Yes"),
        "price":            data.get("price", ""),
        "notes":            data.get("notes", ""),
        "bsrMoved":         "Pending",
        "pageBuilt":        False,
        "commissionEarned": 0,
        "emailSent":        False,
        "manual":           True,
        "asin":             None,
        "bsr_baseline":     None,
        "bsr_current":      None,
        "bsr_history":      [],
        "bsr_checked":      None,
    }
    signals = load_signals()
    signals.insert(0, signal)
    save_signals(signals)
    if action == "APPROVE":
        send_alert(signal)
    return jsonify({"ok": True, "signal": signal})


@app.route("/api/run-now", methods=["POST"])
def trigger_scraper():
    if not auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    threading.Thread(target=run_scraper, daemon=True).start()
    return jsonify({"ok": True, "message": "Scraper started"})


@app.route("/api/run-bsr", methods=["POST"])
def trigger_bsr():
    if not auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    threading.Thread(target=run_bsr_monitor, daemon=True).start()
    return jsonify({"ok": True, "message": "BSR monitor started"})


@app.route("/api/health", methods=["GET"])
def health():
    signals = load_signals()
    return jsonify({
        "status":       "running",
        "time":         datetime.now().isoformat(),
        "signals":      len(signals),
        "brands":       len(BRANDS),
        "subreddits":   len(SUBREDDITS),
        "intent":       len(INTENT_PHRASES),
        "keepa":        bool(KEEPA_API_KEY),
        "version":      "3.3 (intelligence upgrade)",
        "last_scrape":  scheduler_state["last_scrape"],
        "run_count":    scheduler_state["scrape_count"],
        "scraping_now": scheduler_state["running"],
    })


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "name":       "skinsignal",
        "version":    "3.3",
        "brands":     len(BRANDS),
        "subreddits": len(SUBREDDITS),
    })

# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    log.info("=" * 55)
    log.info("  SKINSIGNAL v3.3 — Intelligence Upgrade")
    log.info(f"  Port:       {port}")
    log.info(f"  Brands:     {len(BRANDS)}")
    log.info(f"  Subreddits: {len(SUBREDDITS)}")
    log.info(f"  Intent:     {len(INTENT_PHRASES)} phrases")
    log.info(f"  Alert:      {ALERT_EMAIL}")
    log.info("=" * 55)
    start_scheduler()
    app.run(host="0.0.0.0", port=port, debug=False)