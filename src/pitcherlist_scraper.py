"""
Scrape daily fantasy signals from PitcherList.com (no auth required).

Two columns, driven by people who actually watch baseball:

  1. Starting Pitcher Streamer Ranks -- pitchers bucketed by day into tiers:
     Auto-Start / Probably Start / Questionable Start / Do Not Start.
     This is the PRIMARY pitcher start/sit + streaming signal.

  2. Waiver Wire -> "Top Priority Players to Add" -- a short curated
     pickup list (hitters and pitchers) with team, position, rostership %.
     The column ships under two slugs ('waiver-wire-adds-*', now mostly
     'waiver-wire-picks-*') and two markups; both are handled.

Each recurring column has a stable category page that lists the newest daily
post, so we discover today's URL rather than guessing the date-based slug.

Free-tier notes: the third day of streamer ranks and a few grids are gated
behind PL Pro. We parse whatever is publicly visible and degrade gracefully
(empty DataFrame + warning) if the layout ever shifts.
"""
import re
import requests
import pandas as pd
from datetime import date
from bs4 import BeautifulSoup

BASE = "https://pitcherlist.com"
STREAMER_CATEGORY = f"{BASE}/category/fantasy/starting-pitchers/sp-streamers/"
WAIVER_CATEGORY = f"{BASE}/category/fantasy/waiver-wire/"
HITTER_LIST_CATEGORY = f"{BASE}/category/fantasy/hitters-fantasy/hitter-list/"
SP_LIST_CATEGORY = f"{BASE}/category/fantasy/starting-pitchers/the-list/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

# Compact tier keys (letters only) -> (score, display label).
TIER_KEYS = {
    "autostart": (1, "Auto-Start"),
    "probablystart": (2, "Probably Start"),
    "questionablestart": (3, "Questionable Start"),
    "donotstart": (4, "Do Not Start"),
}

STREAMER_COLS = ["day", "tier", "tier_score", "name", "opp", "matchup", "rostership", "source_url"]
WAIVER_COLS = ["name", "team", "position", "rostership", "note", "source_url"]
TOP_HITTER_COLS = ["rank", "name", "team", "position", "tier", "change", "week", "source_url"]
TOP_PITCHER_COLS = ["rank", "name", "team", "badges", "tier", "change", "week", "source_url"]

_DAY_RE = re.compile(r"(\d{1,2})/(\d{1,2})\s+Starting Pitcher Streamer Rankings", re.I)
_OPP_RE = re.compile(r"(@|vs\.?)\s*([A-Z]{2,3})", re.I)
_RANK_TIER_RE = re.compile(r"\s+T(\d+)\s*$")  # 'T<n>' suffix marks the first name of a tier

# The waiver column has been published under both slugs; take whichever is newest.
_WAIVER_SLUG_RE = r"waiver-wire-(?:adds|picks)-\d"
_WAIVER_DATE_RE = re.compile(r"waiver-wire-(?:adds|picks)-(\d{1,2})-(\d{1,2})")

# A top-priority add's bold header line, in either of the two layouts PL uses:
#   'Caleb Durbin (BOS), INF - 41% Rostership'      (older 'adds' posts)
#   'A.J. Ewing (NYM) - 2B, OF (Yahoo - 22%)'       (current 'picks' posts)
# Section headers ('Streaming Pitchers') are bold too, but never match this.
#   'Jared Jones , RHP (PIT) - 27% Rostership'      (current waiver-wire posts)
#
# The position moved: it used to trail the team, now it leads it. Both layouts are
# accepted, and a position must start with a letter -- otherwise the trailing form
# happily matches the '27' of '27% Rostership' and files it as the player's position.
#
# A position must contain at least one LETTER -- the lookahead is what stops the
# trailing form from matching the '27' of '27% Rostership'. It can still start with a
# digit, because '3B' and '2B' do.
_POS = r"(?=[A-Z0-9/]*[A-Z])[A-Z0-9/]+"
_ADD_RE = re.compile(
    r"^(?P<name>[^(,]+?)\s*"
    rf"(?:,\s*(?P<pos_pre>{_POS}(?:\s*,\s*{_POS})*)\s*)?"
    r"\((?P<team>[A-Z]{2,3})\)"
    rf"(?:\s*[,–—-]\s*(?P<pos_post>{_POS}(?:,\s*{_POS})*))?"
)


def _get_soup(url):
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"  # site is utf-8; requests sometimes mis-guesses latin-1
    return BeautifulSoup(resp.text, "html.parser")


def _find_latest_post(category_url, slug_pattern):
    """Return the newest article URL under a category whose slug matches a regex.

    Category archives list newest-first, so the first match is today's post.
    """
    soup = _get_soup(category_url)
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if re.search(slug_pattern, href):
            return href if href.startswith("http") else BASE + href
    return None


def _tier_score(text):
    """Map a tier heading ('Auto Start', 'Do Not Starts', ...) to (score, label)."""
    key = re.sub(r"[^a-z]", "", text.lower()).replace("starts", "start")
    for k, (score, label) in TIER_KEYS.items():
        if key.startswith(k):
            return score, label
    return None, None


def _parse_opp(matchup):
    """Extract opponent abbreviation from a matchup string like '@ NYY' or 'vs. COL'."""
    m = _OPP_RE.search(matchup or "")
    return m.group(2).upper() if m else ""


def _parse_pct(text):
    """Extract a rostership percentage (float) from e.g. '98%'."""
    m = re.search(r"([\d.]+)\s*%", text or "")
    return float(m.group(1)) if m else None


# --------------------------------------------------------------------------
# Starting Pitcher Streamer Ranks
# --------------------------------------------------------------------------

def scrape_streamer_ranks(game_date=None):
    """
    Scrape today's-and-nearby SP streamer tiers.

    Returns a DataFrame with columns STREAMER_COLS. `day` is the ISO date each
    ranking applies to; consumers filter to the day they care about.
    """
    if game_date is None:
        game_date = date.today().isoformat()
    year = int(game_date[:4])

    url = _find_latest_post(STREAMER_CATEGORY, "streamer-ranks")
    if not url:
        print("  WARNING: could not find a PitcherList streamer-ranks post")
        return pd.DataFrame(columns=STREAMER_COLS)

    soup = _get_soup(url)
    article = soup.find("article") or soup

    rows = []
    current_day = None
    # Day headers and per-day tables appear interleaved in document order:
    #   DAYHEADER 7/5 -> TABLE -> DAYHEADER 7/6 -> TABLE -> ...
    # so binding each table to the most recent preceding day header is correct.
    for el in article.find_all(["strong", "table"]):
        if el.name == "strong":
            m = _DAY_RE.search(el.get_text(" ", strip=True))
            if m:
                try:
                    current_day = date(year, int(m.group(1)), int(m.group(2))).isoformat()
                except ValueError:
                    current_day = None
            continue

        # table
        tr_all = el.find_all("tr")
        if not tr_all:
            continue
        header = [c.get_text(strip=True) for c in tr_all[0].find_all(["th", "td"])]
        if header[:2] != ["Rank", "Pitcher"]:
            continue  # skip the matchup-quality grid (Top/Solid/Average/Weak/Poor)

        tier_score = tier_label = None
        for tr in tr_all[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if len(cells) < 4:
                continue
            rank, pitcher, matchup, rost = cells[0], cells[1], cells[2], cells[3]

            # tier divider row: ['', 'Auto Start', '', '']
            if not rank and not matchup:
                score, label = _tier_score(pitcher)
                if score:
                    tier_score, tier_label = score, label
                    continue
            if not rank or not pitcher:
                continue

            rows.append({
                "day": current_day,
                "tier": tier_label,
                "tier_score": tier_score,
                "name": pitcher,
                "opp": _parse_opp(matchup),
                "matchup": matchup,
                "rostership": _parse_pct(rost),
                "source_url": url,
            })

    df = pd.DataFrame(rows, columns=STREAMER_COLS)
    if df.empty:
        print(f"  WARNING: parsed 0 streamer rows from {url} (layout may have changed)")
    return df


# --------------------------------------------------------------------------
# Waiver Wire Adds -> "Top Priority Players to Add"
# --------------------------------------------------------------------------

def _bold_only_text(p):
    """The paragraph's text if the whole paragraph is bold (<strong> or <b>), else None.

    Both player headers and section headers are bold; _ADD_RE tells them apart.
    Scouting notes are plain prose, so they fall through to None.
    """
    text = p.get_text(" ", strip=True)
    if not text:
        return None
    for tag in ("strong", "b"):
        bold = p.find(tag)
        if bold and bold.get_text(" ", strip=True) == text:
            return text
    return None


def _warn_if_stale(url, game_date):
    """The column skips days; say so rather than silently reporting an old post."""
    m = _WAIVER_DATE_RE.search(url)
    if not m:
        return
    want = date.fromisoformat(game_date)
    post_md = (int(m.group(1)), int(m.group(2)))
    if post_md != (want.month, want.day):
        print(f"  NOTE: newest waiver post is {post_md[0]}/{post_md[1]}, "
              f"not {want.month}/{want.day} -- no post today yet ({url})")


def scrape_waiver_adds(game_date=None):
    """
    Scrape the "Top Priority Players to Add" section of today's waiver-wire post.

    Returns a DataFrame with columns WAIVER_COLS (hitters and pitchers mixed).
    """
    if game_date is None:
        game_date = date.today().isoformat()

    url = _find_latest_post(WAIVER_CATEGORY, _WAIVER_SLUG_RE)
    if not url:
        print("  WARNING: could not find a PitcherList waiver-wire post")
        return pd.DataFrame(columns=WAIVER_COLS)
    _warn_if_stale(url, game_date)

    soup = _get_soup(url)

    marker = next(
        (s for s in soup.find_all(["strong", "b"])
         if "Top Priority Players to Add" in s.get_text()),
        None,
    )
    if marker is None:
        print(f"  WARNING: 'Top Priority Players to Add' not found in {url}")
        return pd.DataFrame(columns=WAIVER_COLS)

    header_p = marker.find_parent("p") or marker
    rows = []
    # Section runs header -> note -> header -> note ... until the next bold
    # section title ("Yahoo and ESPN Most Added Players"), which won't match _ADD_RE.
    for p in header_p.find_all_next("p"):
        bold = _bold_only_text(p)
        if bold is not None:
            m = _ADD_RE.match(bold)
            if not m or "%" not in bold:
                break
            pos = m.group("pos_pre") or m.group("pos_post") or ""
            rows.append({
                "name": m.group("name").strip(),
                "team": m.group("team"),
                "position": re.sub(r",\s*", "/", pos),
                "rostership": _parse_pct(bold),
                "note": "",
                "source_url": url,
            })
        elif rows and not rows[-1]["note"]:
            rows[-1]["note"] = p.get_text(" ", strip=True)

    df = pd.DataFrame(rows, columns=WAIVER_COLS)
    if df.empty:
        print(f"  WARNING: parsed 0 top-priority adds from {url} (layout may have changed)")
    return df


# --------------------------------------------------------------------------
# Weekly rest-of-season value rankings (Top 150 Hitters / Top 100 Starters)
# --------------------------------------------------------------------------
# Both are the same table shape: Rank, Name, Team, <extra>, Change -- where the
# first name of each tier carries a 'T<n>' suffix. Parsed by a shared helper.

def _scrape_ranking(category, slug, name_header, extra_col, cols, label):
    """Scrape a weekly Rank/Name/Team/<extra>/Change ranking table with 'T<n>' tiers."""
    url = _find_latest_post(category, slug)
    if not url:
        print(f"  WARNING: could not find a PitcherList {slug} post")
        return pd.DataFrame(columns=cols)

    week_m = re.search(r"week-(\d+)", url)
    week = int(week_m.group(1)) if week_m else None

    soup = _get_soup(url)
    article = soup.find("article") or soup
    table = None
    for t in article.find_all("table"):
        header = [c.get_text(strip=True) for c in t.find_all("tr")[0].find_all(["th", "td"])]
        if header[:2] == ["Rank", name_header]:
            table = t
            break
    if table is None:
        print(f"  WARNING: no {label} table found at {url}")
        return pd.DataFrame(columns=cols)

    rows = []
    current_tier = None
    for tr in table.find_all("tr")[1:]:
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if len(cells) < 3:
            continue
        rank, name, team = cells[0], cells[1], cells[2]
        extra = cells[3] if len(cells) > 3 else ""
        change = cells[4] if len(cells) > 4 else ""

        m = _RANK_TIER_RE.search(name)
        if m:  # a 'T<n>' suffix marks the first name of a new tier
            current_tier = int(m.group(1))
            name = _RANK_TIER_RE.sub("", name).strip()

        if not rank.isdigit() or not name:
            continue

        rows.append({
            "rank": int(rank), "name": name, "team": team,
            extra_col: extra, "tier": current_tier,
            "change": change, "week": week, "source_url": url,
        })

    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        print(f"  WARNING: parsed 0 rows from {url} (layout may have changed)")
    return df


def scrape_top_hitters(game_date=None):
    """Scrape the latest weekly "Top 150 Hitters" ranking (DataFrame, cols TOP_HITTER_COLS)."""
    return _scrape_ranking(HITTER_LIST_CATEGORY, "top-150-hitters", "Hitter",
                           "position", TOP_HITTER_COLS, "Top 150 hitter")


def scrape_top_pitchers(game_date=None):
    """Scrape the latest weekly "Top 100 Starting Pitchers" ranking (cols TOP_PITCHER_COLS)."""
    return _scrape_ranking(SP_LIST_CATEGORY, "top-100-starting-pitchers", "Pitcher",
                           "badges", TOP_PITCHER_COLS, "Top 100 starting pitcher")


def _week_of(df):
    return int(df["week"].iloc[0]) if not df.empty and pd.notna(df["week"].iloc[0]) else "?"


def scrape_pitcherlist_daily(game_date=None):
    """Fetch all PitcherList columns. Returns (streamers, waiver, top_hitters, top_pitchers)."""
    print("  Fetching PitcherList SP streamer ranks...")
    streamers = scrape_streamer_ranks(game_date)
    days = sorted(streamers["day"].dropna().unique()) if not streamers.empty else []
    print(f"  Got {len(streamers)} streamer rows across days: {days}")

    print("  Fetching PitcherList waiver-wire top-priority adds...")
    waiver = scrape_waiver_adds(game_date)
    print(f"  Got {len(waiver)} top-priority adds")

    print("  Fetching PitcherList Top 150 Hitters...")
    top_hitters = scrape_top_hitters(game_date)
    print(f"  Got {len(top_hitters)} hitters (week {_week_of(top_hitters)})")

    print("  Fetching PitcherList Top 100 Starting Pitchers...")
    top_pitchers = scrape_top_pitchers(game_date)
    print(f"  Got {len(top_pitchers)} starting pitchers (week {_week_of(top_pitchers)})")

    return streamers, waiver, top_hitters, top_pitchers


if __name__ == "__main__":
    streamers, waiver, top_hitters, top_pitchers = scrape_pitcherlist_daily()

    print(f"\n=== SP Streamer Ranks ({len(streamers)}) ===")
    if not streamers.empty:
        for day in sorted(streamers["day"].dropna().unique()):
            print(f"\n  {day}")
            day_df = streamers[streamers["day"] == day]
            for _, r in day_df.iterrows():
                rost = f"{r['rostership']:.0f}%" if pd.notna(r["rostership"]) else "-"
                print(f"    [{r['tier']:<18}] {r['name']:<24} {r['matchup']:<10} rost {rost}")

    print(f"\n=== Top Priority Players to Add ({len(waiver)}) ===")
    for _, r in waiver.iterrows():
        rost = f"{r['rostership']:.0f}%" if pd.notna(r["rostership"]) else "-"
        print(f"    {r['name']:<22} ({r['team']}) {r['position']:<5} rost {rost}")

    print(f"\n=== Top 150 Hitters - Week {_week_of(top_hitters)} (first 15) ===")
    for _, r in top_hitters.head(15).iterrows():
        print(f"    #{r['rank']:<4} T{r['tier']:<3} {r['name']:<24} {r['team']:<4} {r['position']}")

    print(f"\n=== Top 100 Starting Pitchers - Week {_week_of(top_pitchers)} (first 15) ===")
    for _, r in top_pitchers.head(15).iterrows():
        print(f"    #{r['rank']:<4} T{r['tier']:<3} {r['name']:<24} {r['team']:<4}")
