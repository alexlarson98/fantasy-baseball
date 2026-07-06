"""
Scrape Statcast percentile rankings from Baseball Savant (no auth required).

Baseball Savant's percentile-rankings leaderboard embeds a JSON blob
(`var leaderboard_data = [...]`) with a 0-100 percentile for every skill metric,
for all qualified hitters and pitchers. Percentiles are Savant-oriented so that
**higher is always better** for that player type (e.g. a low chase% shows as a
high percentile), which makes them a clean strength signal for add/play/drop.

We pull the two bulk pages (batter + pitcher) once per day and match to our
players by normalized name. Run values and raw decimal values are intentionally
omitted -- the percentiles are the comparative-strength signal we want.
"""
import re
import json
import math
import requests
import pandas as pd
from datetime import date
from bs4 import BeautifulSoup  # noqa: F401  (kept for parity/other scrapers)

PERCENTILE_URL = "https://baseballsavant.mlb.com/leaderboard/percentile-rankings"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

# Friendly column -> Savant percentile key. Only metrics present in the bulk
# percentile endpoint (run values / LA sweet-spot / GB% are not, so they're out).
HITTER_METRICS = {
    "xwOBA": "percent_rank_xwoba",
    "xBA": "percent_rank_xba",
    "xSLG": "percent_rank_xslg",
    "EV": "percent_rank_exit_velocity_avg",
    "Barrel": "percent_rank_barrel_batted_rate",
    "HardHit": "percent_rank_hard_hit_percent",
    "BatSpeed": "percent_rank_swing_speed",
    "SquaredUp": "percent_rank_squared_up_swing",
    "Chase": "percent_rank_chase_percent",
    "Whiff": "percent_rank_whiff_percent",
    "K": "percent_rank_k_percent",
    "BB": "percent_rank_bb_percent",
}

PITCHER_METRICS = {
    "xERA": "percent_rank_xera",
    "xBA": "percent_rank_xba",
    "FBVelo": "percent_rank_fastball_velo",
    "EV": "percent_rank_exit_velocity_avg",
    "Chase": "percent_rank_chase_percent",
    "Whiff": "percent_rank_whiff_percent",
    "K": "percent_rank_k_percent",
    "BB": "percent_rank_bb_percent",
    "Barrel": "percent_rank_barrel_batted_rate",
    "HardHit": "percent_rank_hard_hit_percent",
    "Extension": "percent_rank_fastball_extension",
}


# --------------------------------------------------------------------------
# Composite Statcast score (0-100)
# --------------------------------------------------------------------------
# One "how good is the underlying profile" number = a weighted average of skill
# percentiles. Weights are assigned by CLUSTER first (so a family of collinear
# metrics can't triple-count), then split across the cluster favoring the most
# reliable member. Higher = better. Weighting per research consensus.
#
# Hitters: xwOBA anchors but is capped at 0.20 (it already absorbs xBA/xSLG and
# contact quality); the contact-quality cluster (Barrel/HardHit/EV, r~=.80-.85)
# is the most power-predictive family so Barrel carries the largest non-anchor
# weight, while new bat-tracking metrics (BatSpeed/SquaredUp) stay light; plate
# discipline (Chase/Whiff/K/BB) holds ~a third so the score isn't a pure
# "hits-it-hard" index.
HITTER_WEIGHTS = {
    "xwOBA": 0.20, "xSLG": 0.06, "xBA": 0.04,       # expected production (0.30)
    "Barrel": 0.14, "HardHit": 0.09, "EV": 0.07,    # contact quality & power (0.38)
    "SquaredUp": 0.04, "BatSpeed": 0.04,
    "Chase": 0.09, "Whiff": 0.08, "BB": 0.08, "K": 0.07,  # plate discipline (0.32)
}
# Pitchers: strikeout/whiff ability weighted highest (most stable & predictive
# pitcher skill); xERA anchors an overall summary but is held modest since it's
# partly BABIP-driven; contact-suppression-against stays light (pitchers control
# it far less reliably than hitters control their own contact); fastball velo
# earns real weight as stable stuff, and BB captures command.
PITCHER_WEIGHTS = {
    "Whiff": 0.15, "K": 0.12, "Chase": 0.10,        # bat-missing (0.37)
    "xERA": 0.14, "xBA": 0.08,                      # expected summary (0.22)
    "Barrel": 0.08, "HardHit": 0.05, "EV": 0.05,    # contact suppression (0.18)
    "FBVelo": 0.10, "Extension": 0.03,              # stuff (0.13)
    "BB": 0.10,                                      # command (0.10)
}
# Anchor metric(s) that must be present, else the profile is too thin to score.
_ANCHORS = {"hitter": ("xwOBA",), "pitcher": ("xERA", "Whiff")}


def composite_score(percentiles, weights, anchors=()):
    """
    Weighted average of present percentiles, renormalized over available metrics.

    Uses available-case renormalization (never imputes a league-average constant
    for nulls -- a null means "insufficient sample", not "average skill"). Returns
    a 0-100 int, or None if the anchor(s) are missing or under half the total
    weight is present (too thin to trust).
    """
    for a in anchors:
        v = percentiles.get(a)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
    num = wsum = 0.0
    for metric, w in weights.items():
        v = percentiles.get(metric)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        num += w * float(v)
        wsum += w
    if wsum < 0.5 * sum(weights.values()):
        return None
    return max(0, min(100, int(round(num / wsum))))


def _flip_name(savant_name):
    """Convert Savant 'Last, First' to 'First Last'."""
    if not isinstance(savant_name, str):
        return ""
    if "," in savant_name:
        last, first = savant_name.split(",", 1)
        return f"{first.strip()} {last.strip()}"
    return savant_name.strip()


def _fetch_percentiles(kind, year):
    """Fetch the bulk percentile leaderboard for 'batter' or 'pitcher'."""
    resp = requests.get(
        PERCENTILE_URL,
        params={"type": kind, "year": str(year)},
        headers=HEADERS,
        timeout=40,
    )
    resp.raise_for_status()
    resp.encoding = "utf-8"
    m = re.search(r"var leaderboard_data = (\[.*?\]);", resp.text, re.DOTALL)
    if not m:
        print(f"  WARNING: no leaderboard_data blob for {kind} {year}")
        return []
    return json.loads(m.group(1))


def _to_frame(records, metrics, weights, kind):
    rows = []
    for r in records:
        row = {
            "name": _flip_name(r.get("player_name", "")),
            "player_id": r.get("player_id", ""),
            "team": r.get("team_name", ""),
        }
        for friendly, key in metrics.items():
            val = r.get(key)
            row[friendly] = int(val) if val not in (None, "") else None
        row["score"] = composite_score(row, weights, anchors=_ANCHORS[kind])
        rows.append(row)
    cols = ["name", "player_id", "team", "score"] + list(metrics.keys())
    return pd.DataFrame(rows, columns=cols)


def scrape_savant_hitters(year=None):
    year = year or date.today().year
    return _to_frame(_fetch_percentiles("batter", year), HITTER_METRICS, HITTER_WEIGHTS, "hitter")


def scrape_savant_pitchers(year=None):
    year = year or date.today().year
    return _to_frame(_fetch_percentiles("pitcher", year), PITCHER_METRICS, PITCHER_WEIGHTS, "pitcher")


def scrape_savant_daily(year=None):
    """Fetch Statcast percentile rankings. Returns (hitters_df, pitchers_df)."""
    year = year or date.today().year
    print(f"  Fetching Baseball Savant hitter percentiles ({year})...")
    hitters = scrape_savant_hitters(year)
    print(f"  Got {len(hitters)} hitters")
    print(f"  Fetching Baseball Savant pitcher percentiles ({year})...")
    pitchers = scrape_savant_pitchers(year)
    print(f"  Got {len(pitchers)} pitchers")
    return hitters, pitchers


if __name__ == "__main__":
    hitters, pitchers = scrape_savant_daily()
    print(f"\n=== Top Savant Hitters by composite score ===")
    top = hitters.dropna(subset=["score"]).sort_values("score", ascending=False)
    print(top[["name", "team", "score", "xwOBA", "Barrel", "HardHit", "K", "BB"]].head(8).to_string(index=False))
    print(f"\n=== Top Savant Pitchers by composite score ===")
    topp = pitchers.dropna(subset=["score"]).sort_values("score", ascending=False)
    print(topp[["name", "team", "score", "xERA", "Whiff", "K", "BB", "Barrel"]].head(8).to_string(index=False))
