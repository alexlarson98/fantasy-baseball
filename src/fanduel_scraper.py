"""
Fetch daily DFS projections from FanDuel Research.

Uses the FanDuel Research GraphQL API (no auth required).
Returns batter and pitcher projections for today's main slate.
"""
import requests
import pandas as pd
from datetime import date

GRAPHQL_URL = "https://fdresearch-api.fanduel.com/graphql"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": "https://www.fanduel.com",
    "Referer": "https://www.fanduel.com/research/mlb/fantasy/dfs-projections/batters",
}

SLATES_QUERY = """
query($sport: ProjectionSports!, $range: Range) {
  getSlates(sport: $sport, range: $range) { id name }
}
"""

BATTER_QUERY = """
query($input: ProjectionsInput!) {
  getProjections(input: $input) {
    ... on MlbBatter {
      player { name position handedness }
      team { abbreviation }
      salary value fantasy
      plateAppearances runs hits singles doubles triples homeRuns runsBattedIn
      stolenBases walks strikeouts battingAverage onBasePercentage sluggingPercentage
      gameInfo { gameTime homeTeam { abbreviation } awayTeam { abbreviation } }
    }
  }
}
"""

PITCHER_QUERY = """
query($input: ProjectionsInput!) {
  getProjections(input: $input) {
    ... on MlbPitcher {
      player { name position handedness }
      team { abbreviation }
      salary value fantasy wins losses earnedRunsAvg gamesStarted saves
      inningsPitched hits runs earnedRuns homeRuns walks strikeouts
      walksPlusHitsPerInningsPitched gamesPlayed
      gameInfo { gameTime homeTeam { abbreviation } awayTeam { abbreviation } }
    }
  }
}
"""


def _post(query, variables):
    resp = requests.post(
        GRAPHQL_URL,
        json={"query": query, "variables": variables},
        headers=HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"FanDuel GraphQL error: {data['errors']}")
    return data["data"]


def get_main_slate_id(game_date=None):
    """Find today's 'Main' MLB slate ID."""
    if game_date is None:
        game_date = date.today().isoformat()

    data = _post(SLATES_QUERY, {"sport": "MLB", "range": {"date": game_date}})
    slates = data.get("getSlates", [])
    if not slates:
        raise RuntimeError(f"No MLB slates found for {game_date}")

    # Prefer "Main", fallback to "All Day", then first
    for pref in ["Main", "All Day"]:
        for s in slates:
            if pref in s["name"]:
                return s["id"], s["name"]
    return slates[0]["id"], slates[0]["name"]


def _flatten_batter(p):
    game = p.get("gameInfo") or {}
    home = (game.get("homeTeam") or {}).get("abbreviation", "")
    away = (game.get("awayTeam") or {}).get("abbreviation", "")
    team = (p.get("team") or {}).get("abbreviation", "")
    opp = away if team == home else home
    return {
        "name": (p.get("player") or {}).get("name"),
        "position": (p.get("player") or {}).get("position"),
        "handedness": (p.get("player") or {}).get("handedness"),
        "team": team,
        "opp": opp,
        "salary": p.get("salary"),
        "value": p.get("value"),
        "fantasy": p.get("fantasy"),
        "PA": p.get("plateAppearances"),
        "R": p.get("runs"),
        "H": p.get("hits"),
        "1B": p.get("singles"),
        "2B": p.get("doubles"),
        "3B": p.get("triples"),
        "HR": p.get("homeRuns"),
        "RBI": p.get("runsBattedIn"),
        "SB": p.get("stolenBases"),
        "BB": p.get("walks"),
        "K": p.get("strikeouts"),
        "AVG": p.get("battingAverage"),
        "OBP": p.get("onBasePercentage"),
        "SLG": p.get("sluggingPercentage"),
        "game_time": game.get("gameTime"),
    }


def _flatten_pitcher(p):
    game = p.get("gameInfo") or {}
    home = (game.get("homeTeam") or {}).get("abbreviation", "")
    away = (game.get("awayTeam") or {}).get("abbreviation", "")
    team = (p.get("team") or {}).get("abbreviation", "")
    opp = away if team == home else home
    return {
        "name": (p.get("player") or {}).get("name"),
        "position": (p.get("player") or {}).get("position"),
        "handedness": (p.get("player") or {}).get("handedness"),
        "team": team,
        "opp": opp,
        "salary": p.get("salary"),
        "value": p.get("value"),
        "fantasy": p.get("fantasy"),
        "IP": p.get("inningsPitched"),
        "W": p.get("wins"),
        "L": p.get("losses"),
        "K": p.get("strikeouts"),
        "ERA": p.get("earnedRunsAvg"),
        "WHIP": p.get("walksPlusHitsPerInningsPitched"),
        "GS": p.get("gamesStarted"),
        "SV": p.get("saves"),
        "ER": p.get("earnedRuns"),
        "H": p.get("hits"),
        "BB": p.get("walks"),
        "HR": p.get("homeRuns"),
        "game_time": game.get("gameTime"),
    }


def fetch_daily_batters(slate_id):
    data = _post(BATTER_QUERY, {
        "input": {"type": "DAILY", "sport": "MLB", "position": "MLB_BATTER", "slateId": slate_id}
    })
    players = data.get("getProjections") or []
    df = pd.DataFrame(_flatten_batter(p) for p in players)
    if not df.empty and "fantasy" in df.columns:
        df = df.sort_values("fantasy", ascending=False).reset_index(drop=True)
        df.insert(0, "rank", df.index + 1)
    return df


def fetch_daily_pitchers(slate_id):
    data = _post(PITCHER_QUERY, {
        "input": {"type": "DAILY", "sport": "MLB", "position": "MLB_PITCHER", "slateId": slate_id}
    })
    players = data.get("getProjections") or []
    df = pd.DataFrame(_flatten_pitcher(p) for p in players)
    if not df.empty and "fantasy" in df.columns:
        df = df.sort_values("fantasy", ascending=False).reset_index(drop=True)
        df.insert(0, "rank", df.index + 1)
    return df


def scrape_fanduel_daily(game_date=None):
    """
    Fetch today's FanDuel DFS projections for batters and pitchers.

    Returns:
        tuple: (batters_df, pitchers_df, slate_name)
    """
    slate_id, slate_name = get_main_slate_id(game_date)
    print(f"  Using slate: {slate_name} (id: {slate_id})")

    print("  Fetching FanDuel batter projections...")
    batters = fetch_daily_batters(slate_id)
    print(f"  Got {len(batters)} batters")

    print("  Fetching FanDuel pitcher projections...")
    pitchers = fetch_daily_pitchers(slate_id)
    print(f"  Got {len(pitchers)} pitchers")

    return batters, pitchers, slate_name


if __name__ == "__main__":
    batters, pitchers, slate = scrape_fanduel_daily()
    print(f"\n=== FanDuel Daily Batters - {slate} ===")
    cols = [c for c in ["rank", "name", "team", "opp", "fantasy", "salary", "R", "HR", "RBI", "SB", "OBP"] if c in batters.columns]
    print(batters[cols].head(15).to_string(index=False))

    print(f"\n=== FanDuel Daily Pitchers - {slate} ===")
    cols = [c for c in ["rank", "name", "team", "opp", "fantasy", "salary", "IP", "K", "W", "ERA", "WHIP"] if c in pitchers.columns]
    print(pitchers[cols].head(15).to_string(index=False))
