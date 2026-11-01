"""
Desi Picks - Amazon INDIA deal scan (GitHub Actions, Playwright).  Run:  python tools/scan_in.py

Same idea as the Zoe Picks (US) scan, but for amazon.in and WITHOUT guides: it only produces two public feed files.

  guides/hot.json     v3  {updated, hot[], ess[], trend[], themes[]}      -> Daily Essentials / Top Offers ticker / Trending
  guides/offers.json  v3  {updated, fresh_h, cats{<category>: [items]}}   -> Top Offers per CATEGORY (17 categories, same as Zoe)

Item shape (same as the US feed so the bot code does not change):
  {asin, name, pct, rating, t, img, link, now, was, deal, cat, label, icon, reviews, tier}     now/was = rupees as plain numbers in text

MODE (env SCAN_MODE or 1st argument):
  auto      (default) do whatever is due: refresh hot.json if older than FEED_EVERY_H, sweep categories not swept for SWEEP_EVERY_H
  feed      force-refresh hot.json (essentials + top offers + trending) now
  offers    force-sweep every category now (still limited by the time budget)
  selftest  checks every Amazon department/alias + discount filter, prints cards per scope, writes NOTHING
ENV: AMAZON_IN_TAG (affiliate tag, optional)  BUDGET_MIN (40)  PAGES (2)  MIN_PCT (25)  SWEEP_EVERY_H (11)  FEED_EVERY_H (11)
     FRESH_H (36)  SCAN_ONLY=<category>  (offers mode: only that category)
"""
import json
import os
import random
import re
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOT_PATH = os.path.join(ROOT, "guides", "hot.json")
OFFERS_PATH = os.path.join(ROOT, "guides", "offers.json")
IST = timezone(timedelta(hours=5, minutes=30))
BASE = "https://www.amazon.in"


def _int(name, default):
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


TAG = (os.environ.get("AMAZON_IN_TAG") or "").strip()
BUDGET_MIN = _int("BUDGET_MIN", 40)
PAGES = _int("PAGES", 2)
MIN_PCT = _int("MIN_PCT", 25)                 # "xx% off or more" filter used for category sweeps / top offers
SWEEP_EVERY_H = _int("SWEEP_EVERY_H", 11)
FEED_EVERY_H = _int("FEED_EVERY_H", 11)
FRESH_H = _int("FRESH_H", 36)
PAUSE = (6, 14)                               # seconds between two Amazon pages
CAPTCHA_STOP = 2

# gates (same spirit as Zoe / Naami credibility gate)
MIN_RATING = 4.0
MIN_REVIEWS = 50
MAX_PCT = 85
SUSPICIOUS_PCT, SUSPICIOUS_REVIEWS = 70, 300
MIN_PRICE_ESS, MIN_PRICE_HOT, MIN_PRICE_OFFER = 49, 1500, 299     # rupees
MAX_HOT, MAX_ESS, MAX_TREND, PER_THEME, PER_CAT_OFFERS = 30, 60, 36, 14, 40
NAME_MAX = 70

# ---- the 17 categories (same keys / labels as Zoe) -------------------------------------------------------------
CATEGORIES = [
    ("home_kitchen", "🍳", "Home & Kitchen"), ("electronics", "🎧", "Electronics"), ("laptops", "💻", "Laptops"),
    ("mobiles", "📱", "Mobiles"), ("monitors", "🖥️", "Monitors & TV"), ("accessories", "🖱️", "Accessories"),
    ("beauty_health", "🧴", "Beauty & Health"), ("fitness_outdoor", "🏃", "Fitness & Outdoor"), ("fashion", "👟", "Fashion"),
    ("toys_kids", "🧸", "Toys & Kids"), ("tools_auto", "🔧", "Tools & Auto"), ("pets", "🐶", "Pets"),
    ("office_books", "📚", "Office & Books"), ("gaming", "🎮", "Gaming"), ("smart_home", "🏠", "Smart Home"),
    ("furniture_decor", "🛋️", "Furniture & Decor"), ("hobby_music", "🎸", "Hobby & Music"),
]
CAT_LABEL = {k: (ic, lab) for k, ic, lab in CATEGORIES}

# category -> [(amazon.in search alias for &i=, keyword or None)].  Aliases are Amazon's public ones but can change:
# run mode=selftest once; a scope with 0 cards is wrong -> fix it HERE (or it falls back to FALLBACK_KW automatically).
SCOPES = {
    "home_kitchen":    [("kitchen", None), ("appliances", None)],
    "electronics":     [("electronics", None), ("electronics", "headphones")],
    "laptops":         [("computers", "laptop")],
    "mobiles":         [("electronics", "smartphone"), ("electronics", "smartwatch")],
    "monitors":        [("computers", "monitor"), ("electronics", "smart tv"), ("electronics", "projector")],
    "accessories":     [("computers", "keyboard mouse"), ("electronics", "power bank")],
    "beauty_health":   [("beauty", None), ("hpc", None)],
    "fitness_outdoor": [("sports", None)],
    "fashion":         [("apparel", None), ("shoes", None)],
    "toys_kids":       [("toys", None), ("baby", None)],
    "tools_auto":      [("home-improvement", None), ("automotive", None)],
    "pets":            [("pets", None)],
    "office_books":    [("office-products", None), ("stripbooks", None)],
    "gaming":          [("videogames", None)],
    "smart_home":      [("electronics", "smart home"), ("electronics", "security camera")],
    "furniture_decor": [("garden", "furniture"), ("kitchen", "home decor")],
    "hobby_music":     [("musical-instruments", None)],
}
FALLBACK_KW = {"home_kitchen": "kitchen appliances", "laptops": "laptop", "gaming": "gaming", "hobby_music": "guitar keyboard instrument",
               "office_books": "stationery books", "pets": "pet food", "tools_auto": "car accessories tools", "fashion": "men shoes clothing",
               "fitness_outdoor": "fitness", "toys_kids": "toys", "beauty_health": "skin care", "electronics": "electronics",
               "furniture_decor": "furniture", "mobiles": "smartphone", "monitors": "monitor", "accessories": "computer accessories",
               "smart_home": "smart home"}

# ---- Daily Essentials: (key, label, icon, search keyword, must-match regex) -------------------------------------
ESS = [
    ("atta", "Atta & flour", "🌾", "wheat flour atta", r"atta|flour|besan|maida"),
    ("rice", "Rice", "🍚", "basmati rice", r"rice"),
    ("dal", "Dal & pulses", "🫘", "toor dal pulses", r"dal|chana|rajma|moong|lentil|pulses"),
    ("oil", "Cooking oil & ghee", "🫒", "cooking oil ghee", r"oil|ghee"),
    ("tea", "Tea & coffee", "☕", "tea coffee", r"tea|coffee"),
    ("snacks", "Biscuits & snacks", "🍪", "biscuits namkeen snacks", r"biscuit|cookies?|namkeen|chips|snack|wafer"),
    ("dryfruit", "Dry fruits & nuts", "🥜", "almonds cashews dry fruits", r"almond|cashew|raisin|pista|walnut|nuts|dry fruit|dates"),
    ("masala", "Spices & masala", "🧂", "masala spices", r"masala|spice|turmeric|chilli|jeera|haldi"),
    ("laundry", "Laundry detergent", "🧺", "laundry detergent", r"detergent|laundry|washing powder|fabric"),
    ("dish", "Dishwash", "🍽️", "dishwash liquid", r"dish ?wash|dish soap|vim"),
    ("floor", "Floor & toilet cleaner", "🧼", "floor cleaner toilet cleaner", r"floor|toilet|harpic|lizol|phenyl|cleaner"),
    ("handwash", "Handwash & sanitizer", "🧴", "handwash sanitizer", r"hand ?wash|sanitizer|hand soap"),
    ("shampoo", "Shampoo & hair care", "🧴", "shampoo conditioner", r"shampoo|conditioner|hair oil"),
    ("bodywash", "Soap & body wash", "🚿", "soap body wash", r"soap|body wash|shower gel"),
    ("toothpaste", "Toothpaste", "🪥", "toothpaste", r"toothpaste|tooth paste|toothbrush"),
    ("facewash", "Face wash & skincare", "🧴", "face wash moisturizer", r"face ?wash|moisturi|sunscreen|face cream|serum"),
    ("diapers", "Diapers & baby care", "👶", "baby diapers", r"diaper|baby wipes|pants"),
    ("pads", "Sanitary pads", "🌸", "sanitary pads", r"pads?|sanitary"),
    ("batteries", "Batteries", "🔋", "AA AAA batteries", r"batter"),
    ("protein", "Protein & supplements", "💪", "whey protein", r"protein|whey|creatine|bcaa"),
    ("vitamins", "Vitamins", "💊", "multivitamin tablets", r"vitamin|omega|multivit|zinc|calcium|supplement"),
    ("petfood", "Pet food", "🐶", "dog food", r"dog|cat|pet|puppy"),
    ("garbage", "Garbage bags", "🗑️", "garbage bags", r"garbage|trash|dustbin bags?|bin bags?"),
    ("bulbs", "LED bulbs", "💡", "led bulb", r"bulb|led"),
    ("bottles", "Bottles & lunch boxes", "🥤", "water bottle lunch box", r"bottle|flask|tiffin|lunch ?box"),
    ("socks", "Socks & innerwear", "🧦", "socks innerwear", r"socks?|vest|brief|boxer|innerwear"),
]
ACCESSORY = re.compile(r"\b(holder|dispenser|rack|stand|cover|case|bowl|feeder|tray|scoop|toy|machine|maker|grinder|mug|"
                       r"charger|power bank|mounts?|brush holder|storage|organi[sz]er|tester|empty)\b", re.I)

# ---- Trending: India seasons, picked by today's IST date.  (key, label, icon, (start m,d), (end m,d), [keywords]) ------
# Festival dates move every year (Diwali, Holi, Rakhi): adjust the windows here once a year.
THEMES = [
    ("festive", "Festive sale", "🪔", (9, 15), (10, 10), ["festive offers", "navratri decoration", "home decor lights"]),
    ("diwali", "Diwali gifts", "🪔", (10, 10), (11, 12), ["diwali gift hamper", "diwali decoration lights", "dry fruits gift box", "diya candles rangoli"]),
    ("wedding", "Wedding season", "💍", (11, 10), (2, 28), ["wedding gift", "ethnic wear", "jewellery set", "gift set"]),
    ("winter", "Winter ready", "🧣", (10, 20), (2, 15), ["room heater", "geyser water heater", "blanket quilt", "woolen jacket sweater"]),
    ("republic", "Republic Day sale", "🇮🇳", (1, 1), (1, 27), ["smartphone", "laptop", "smart tv", "headphones"]),
    ("holi", "Holi", "🎨", (2, 20), (3, 10), ["holi colours", "water gun pichkari", "gulal"]),
    ("summer", "Summer cooling", "❄️", (3, 1), (6, 15), ["air cooler", "ceiling fan", "split ac", "water bottle cooler"]),
    ("monsoon", "Monsoon ready", "🌧️", (6, 15), (9, 14), ["umbrella raincoat", "monsoon shoes", "dehumidifier"]),
    ("rakhi", "Raksha Bandhan", "🧵", (7, 25), (8, 31), ["rakhi gift", "rakhi chocolates"]),
]

# ---- hot (top offers) searches: big-ticket things Indians shop on offer -----------------------------------------
HOT_QUERIES = ["smartphone", "laptop", "smart tv", "smartwatch", "wireless earbuds", "air conditioner", "refrigerator",
               "washing machine", "air fryer", "soundbar", "water purifier", "tablet", "headphones", "robot vacuum cleaner"]

_JS_CARDS = r"""
() => [...document.querySelectorAll('div[data-component-type="s-search-result"]')].map(c => {
  const asin = c.getAttribute('data-asin') || '';
  const h = c.querySelector('h2');
  const title = h ? (h.innerText || h.getAttribute('aria-label') || '').trim() : '';
  const im = c.querySelector('img.s-image');
  const pe = c.querySelector('.a-price:not(.a-text-price) .a-offscreen');
  const we = c.querySelector('.a-price.a-text-price .a-offscreen, [data-a-strike="true"] .a-offscreen');
  const re_ = c.querySelector('span.a-icon-alt');
  const rv = c.querySelector('span.s-underline-text, a[href*="customerReviews"] span, span[aria-label*="ratings"]');
  return {asin: asin, title: title, img: im ? im.src : '',
          price: pe ? pe.textContent : '', was: we ? we.textContent : '',
          rating: re_ ? re_.textContent : '', reviews: rv ? (rv.getAttribute('aria-label') || rv.textContent) : '',
          text: (c.innerText || '').slice(0, 900)};
})
"""
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_MARKS = ("Type the characters you see", "/errors/validateCaptcha", "Enter the characters you see below",
          "To discuss automated access", "Robot Check", "api-services-support@amazon.com")
_DEAL_RX = re.compile(r"(Limited time deal|Deal of the Day|Lightning Deal|Great Indian Festival|Prime Exclusive|Top Deal|Festive Deal)", re.I)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# =====================================================================================================================
# pure helpers (unit-tested in tests/test_logic.py - no browser needed)
# =====================================================================================================================
def num(v):
    s = re.sub(r"[^\d.]", "", str(v or "").replace(",", ""))
    try:
        return float(s) if s else None
    except ValueError:
        return None


def parse_reviews(s):
    s = str(s or "").strip().replace(",", "")
    m = re.search(r"([\d.]+)\s*([KkMm]?)", s)
    if not m:
        return 0
    try:
        n = float(m.group(1))
    except ValueError:
        return 0
    return int(n * {"k": 1e3, "m": 1e6}.get(m.group(2).lower(), 1))


def parse_rating(s):
    m = re.search(r"(\d(?:\.\d)?)\s*out of", str(s or ""))
    return float(m.group(1)) if m else 0.0


def full_img(url):
    return re.sub(r"\._[A-Z0-9_,.\-]+_\.", ".", str(url or ""))


def short_name(t):
    t = re.sub(r"\s+", " ", str(t or "")).strip(" -,|")
    if len(t) <= NAME_MAX:
        return t
    cut = t[:NAME_MAX].rsplit(" ", 1)[0]
    return cut.strip(" -,|(/")


def tier(price):
    return "budget" if price < 1000 else ("mid" if price < 10000 else "premium")


def card_to_item(c, now=None):
    """raw browser card -> item dict or None (sponsored / no price / no discount / bad data)."""
    asin = str(c.get("asin") or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{10}", asin):
        return None
    text = str(c.get("text") or "")
    if re.search(r"(^|\n|\s)Sponsored\b", text):
        return None
    price, was = num(c.get("price")), num(c.get("was"))
    if not price or price <= 0:
        return None
    pct = 0
    if was and was > price * 1.01:
        pct = int(round((was - price) / was * 100))
    else:
        m = re.search(r"\(?(\d{1,2})\s*%\s*off\)?", text, re.I) or re.search(r"(?<![\d.])-\s?(\d{1,2})\s?%", text)
        if m:
            pct = int(m.group(1))
            if pct and not was:
                was = round(price / (1 - pct / 100.0))
    if pct <= 0 or not was:
        return None
    img = full_img(c.get("img"))
    if not img.startswith("http"):
        return None
    m = _DEAL_RX.search(text)
    return {"asin": asin, "name": short_name(c.get("title")), "pct": pct, "rating": parse_rating(c.get("rating")),
            "t": int(now or time.time()), "img": img, "now": ("%g" % price), "was": ("%g" % was),
            "link": f"{BASE}/dp/{asin}" + (f"?tag={TAG}" if TAG else ""), "deal": (m.group(1) if m else ""),
            "reviews": parse_reviews(c.get("reviews")), "_price": price}


def credible(it, min_pct, min_price):
    """same gates as Naami: real discount, rating >= 4.0 (unknown skipped), reviews >= 50 (unknown skipped), suspicious-discount gate."""
    if not it or not it.get("name"):
        return False
    pct, price = it["pct"], it["_price"]
    if pct < min_pct or pct > MAX_PCT or price < min_price:
        return False
    if 0 < it["rating"] < MIN_RATING:
        return False
    if 0 < it["reviews"] < MIN_REVIEWS:
        return False
    if pct >= SUSPICIOUS_PCT and it["reviews"] < SUSPICIOUS_REVIEWS:
        return False
    return True


def clean(it, **extra):
    o = {k: v for k, v in it.items() if not k.startswith("_")}
    if not o.get("deal"):
        o.pop("deal", None)
    if not o.get("reviews"):
        o.pop("reviews", None)
    o["tier"] = extra.pop("tier", None) or tier(it["_price"])
    o.update(extra)
    return o


def active_themes(now=None):
    n = (now or datetime.now(timezone.utc)).astimezone(IST)
    cur = (n.month, n.day)
    out = []
    for key, label, icon, a, b, kws in THEMES:
        inside = (a <= cur <= b) if a <= b else (cur >= a or cur <= b)
        if not inside:
            continue
        end_y = n.year if b >= cur else n.year + 1
        left = (datetime(end_y, b[0], b[1]) - datetime(n.year, cur[0], cur[1])).days
        out.append((left, key, label, icon, kws))
    out.sort()
    return [(k, l, i, kws) for _, k, l, i, kws in out]


def round_robin(groups, limit):
    """groups = {key: [items best first]} -> best-discount-first interleave across keys, up to limit."""
    keys = sorted(groups, key=lambda k: -(groups[k][0]["pct"] if groups[k] else 0))
    out, i = [], 0
    while len(out) < limit and any(groups[k][i:i + 1] for k in keys):
        for k in keys:
            if len(out) >= limit:
                break
            if i < len(groups[k]):
                out.append(groups[k][i])
        i += 1
    return out


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def iso(ts=None):
    return datetime.fromtimestamp(ts or time.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def age_h(s):
    try:
        return (time.time() - datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()) / 3600
    except Exception:
        return 1e9


# =====================================================================================================================
# browser
# =====================================================================================================================
class Amazon:
    def __init__(self):
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(headless=True)
        ctx = self.browser.new_context(user_agent=_UA, locale="en-IN", timezone_id="Asia/Kolkata",
                                       viewport={"width": 1366, "height": 900},
                                       extra_http_headers={"Accept-Language": "en-IN,en;q=0.9"})
        self.page = ctx.new_page()
        self.captcha_streak = 0
        self.pages_loaded = 0
        self.t0 = time.time()

    def close(self):
        try:
            self.browser.close()
        except Exception:
            pass
        try:
            self.pw.stop()
        except Exception:
            pass

    def out_of_time(self):
        return (time.time() - self.t0) > BUDGET_MIN * 60

    def blocked(self):
        return self.captcha_streak >= CAPTCHA_STOP

    def search(self, kw=None, alias=None, page_no=1, pct=MIN_PCT, use_filter=True):
        """-> list of raw cards (may be []), sets captcha streak. One polite pause before every page."""
        if self.pages_loaded:
            time.sleep(random.uniform(*PAUSE))
        q = []
        if kw:
            q.append("k=" + urllib.parse.quote_plus(kw))
        if alias:
            q.append("i=" + urllib.parse.quote_plus(alias))
        if use_filter and pct:
            q.append("rh=" + urllib.parse.quote(f"p_8:{pct}-"))
        if page_no > 1:
            q.append(f"page={page_no}")
        url = f"{BASE}/s?" + "&".join(q)
        self.pages_loaded += 1
        try:
            self.page.goto(url, timeout=45000, wait_until="domcontentloaded")
            try:
                self.page.wait_for_selector('div[data-component-type="s-search-result"]', timeout=9000)
            except Exception:
                pass
            html = self.page.content()
        except Exception as e:
            log("  page error:", str(e)[:100])
            return []
        if any(m in html for m in _MARKS) and 's-search-result' not in html:
            self.captcha_streak += 1
            log("  CAPTCHA / block (streak %d)" % self.captcha_streak)
            return []
        self.captcha_streak = 0
        try:
            return self.page.evaluate(_JS_CARDS) or []
        except Exception as e:
            log("  extract error:", str(e)[:100])
            return []

    def items(self, kw=None, alias=None, pages=1, pct=MIN_PCT, min_price=MIN_PRICE_OFFER):
        """-> (kept credible items, raw card count). Falls back to an unfiltered page when the discount filter returns 0 cards."""
        out, seen, raw_n = [], set(), 0
        for p in range(1, pages + 1):
            cards = self.search(kw, alias, p, pct, True)
            if not cards and not self.blocked() and p == 1:
                cards = self.search(kw, alias, p, pct, False)      # filter id might be wrong -> filter locally instead
            raw_n += len(cards)
            for c in cards:
                it = card_to_item(c)
                if it and it["asin"] not in seen and credible(it, pct, min_price):
                    seen.add(it["asin"])
                    out.append(it)
            if self.blocked() or self.out_of_time() or not cards:
                break
        return out, raw_n


# =====================================================================================================================
# jobs
# =====================================================================================================================
def build_feed(am):
    """hot.json: ess + hot + trend. Each product in ONE list only (trend > ess > hot)."""
    old = read_json(HOT_PATH, {})
    used = set()

    # trend
    themes = active_themes()
    trend = []
    if themes:
        per = {}
        for key, label, icon, kws in themes:
            lst = []
            for kw in kws:
                if am.blocked() or am.out_of_time():
                    break
                items, _ = am.items(kw, None, 1, 20, MIN_PRICE_ESS)
                lst += [clean(i, theme=key, label=label, icon=icon) for i in items]
                log(f"  trend {key}/{kw}: {len(items)}")
            lst = sorted({i["asin"]: i for i in lst}.values(), key=lambda x: -x["pct"])[:PER_THEME]
            per[key] = lst
        trend = round_robin({k: v for k, v in per.items() if v}, MAX_TREND)
    used |= {i["asin"] for i in trend}

    # ess
    groups = {}
    for key, label, icon, kw, rx in ESS:
        if am.blocked() or am.out_of_time():
            break
        items, _ = am.items(kw, None, 1, 10, MIN_PRICE_ESS)
        good = [i for i in items if re.search(rx, i["name"], re.I) and not ACCESSORY.search(i["name"]) and i["asin"] not in used]
        good.sort(key=lambda x: -x["pct"])
        groups[key] = [clean(i, cat=key, label=label, icon=icon) for i in good[:4]]
        log(f"  ess {key}: {len(groups[key])}")
    ess = round_robin({k: v for k, v in groups.items() if v}, MAX_ESS)
    used |= {i["asin"] for i in ess}

    # hot (top offers): big-ticket, diversity cap per search
    hot = []
    for kw in HOT_QUERIES:
        if am.blocked() or am.out_of_time():
            break
        items, _ = am.items(kw, None, 1, MIN_PCT, MIN_PRICE_HOT)
        items = [i for i in items if i["asin"] not in used and i["rating"] >= MIN_RATING]
        items.sort(key=lambda x: -x["pct"])
        hot += [clean(i) for i in items[:4]]
        log(f"  hot {kw}: {min(len(items), 4)}")
    hot = sorted({i["asin"]: i for i in hot}.values(), key=lambda x: -x["pct"])[:MAX_HOT]

    # never overwrite a list with nothing: keep the previous list if it is still fresh
    def keep(new, key):
        if new:
            return new
        return [i for i in (old.get(key) or []) if (time.time() - float(i.get("t") or 0)) < FRESH_H * 3600]

    out = {"v": 3, "updated": iso(), "hot": keep(hot, "hot"), "ess": keep(ess, "ess"), "trend": keep(trend, "trend"),
           "themes": [{"key": k, "label": l, "icon": i} for k, l, i, _ in themes]}
    if not (out["hot"] or out["ess"] or out["trend"]):
        log("feed: nothing usable (blocked?) - hot.json left untouched")
        return False
    write_json(HOT_PATH, out)
    log(f"feed written: hot={len(out['hot'])} ess={len(out['ess'])} trend={len(out['trend'])}")
    return True


def sweep_category(am, cat):
    ic, lab = CAT_LABEL[cat]
    got = {}
    for alias, kw in SCOPES[cat]:
        if am.blocked() or am.out_of_time():
            break
        items, raw = am.items(kw, alias, PAGES, MIN_PCT, MIN_PRICE_OFFER)
        if raw == 0 and not am.blocked():                       # wrong alias -> keyword search instead
            items, raw = am.items(kw or FALLBACK_KW.get(cat), None, PAGES, MIN_PCT, MIN_PRICE_OFFER)
        log(f"  {cat} [{alias}/{kw}]: {raw} cards -> {len(items)} offers")
        for i in items:
            got[i["asin"]] = clean(i, cat=cat, label=lab, icon=ic)
    return sorted(got.values(), key=lambda x: (-x["pct"], -(x.get("reviews") or 0)))[:PER_CAT_OFFERS]


def _drop_stale(cats):
    cutoff = time.time() - FRESH_H * 3600
    return {c: [i for i in lst if float(i.get("t") or 0) >= cutoff] for c, lst in cats.items()}


def run_offers(am, force=False):
    data = read_json(OFFERS_PATH, {})
    cats = dict(data.get("cats") or {})
    swept = dict(data.get("swept") or {})
    only = (os.environ.get("SCAN_ONLY") or "").strip()
    due = [c for c, _, _ in CATEGORIES if (not only or c == only) and (force or age_h(swept.get(c)) >= SWEEP_EVERY_H)]
    due.sort(key=lambda c: age_h(swept.get(c)), reverse=True)    # oldest first
    if not due:
        log("offers: nothing due")
        return False
    log("offers due:", ", ".join(due))
    changed = False
    for cat in due:
        if am.blocked() or am.out_of_time():
            log("offers: stopping (blocked or out of time)")
            break
        items = sweep_category(am, cat)
        if items:
            cats[cat] = items
            swept[cat] = iso()
            changed = True
            data = {"v": 3, "updated": iso(), "fresh_h": FRESH_H, "cats": _drop_stale(cats), "swept": swept}
            write_json(OFFERS_PATH, data)               # write after every category: a cancelled run loses nothing
    return changed


def selftest(am):
    log("SELFTEST - nothing is written")
    bad = 0
    for cat, scopes in SCOPES.items():
        for alias, kw in scopes:
            if am.blocked():
                log("blocked - stop")
                return
            cards = am.search(kw, alias, 1, MIN_PCT, True)
            offers = sum(1 for c in cards if card_to_item(c))
            log(f"{cat:16s} i={alias:20s} kw={kw!s:18s} filtered cards={len(cards):3d} with-offer={offers:3d}")
            if not cards:
                bad += 1
    log("scopes with 0 cards:", bad, "(they auto-fall-back to FALLBACK_KW during the sweep)")


def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SCAN_MODE") or "auto").strip().lower()
    log("mode:", mode, "| tag:", TAG or "(none)", "| budget:", BUDGET_MIN, "min")
    feed_due = mode == "feed" or (mode == "auto" and age_h(read_json(HOT_PATH, {}).get("updated")) >= FEED_EVERY_H)
    offers_due = mode in ("offers", "auto")
    if mode == "auto" and not feed_due:
        d = read_json(OFFERS_PATH, {})
        if all(age_h((d.get("swept") or {}).get(c)) < SWEEP_EVERY_H for c, _, _ in CATEGORIES):
            log("nothing due - exit")
            return 0
    am = Amazon()
    try:
        if mode == "selftest":
            selftest(am)
            return 0
        if feed_due:
            build_feed(am)
        if offers_due and not am.blocked() and not am.out_of_time():
            run_offers(am, force=(mode == "offers"))
    finally:
        am.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
