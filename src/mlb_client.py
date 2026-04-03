"""
Fetch daily game data from the MLB Stats API.

Free, no auth required. Provides today's schedule, probable pitchers,
confirmed lineups, and game status.
"""
import requests
import pandas as pd
from datetime import date

MLB_API = "https://statsapi.mlb.com/api/v1"


def get_todays_games(game_date=None):
    """
    Fetch today's MLB schedule with probable pitchers.

    Returns:
        list of dicts with game info
    """
    if game_date is None:
        game_date = date.today().isoformat()

    resp = requests.get(
        f"{MLB_API}/schedule",
        params={
            'sportId': 1,
            'date': game_date,
            'hydrate': 'probablePitcher,venue',
        }
    )
    resp.raise_for_status()
    data = resp.json()

    games = []
    for date_entry in data.get('dates', []):
        for game in date_entry.get('games', []):
            away = game['teams']['away']
            home = game['teams']['home']

            games.append({
                'game_pk': game['gamePk'],
                'game_time': game.get('gameDate', ''),
                'status': game['status']['detailedState'],
                'away_team': away['team']['name'],
                'away_abbr': away['team'].get('abbreviation', ''),
                'home_team': home['team']['name'],
                'home_abbr': home['team'].get('abbreviation', ''),
                'away_pitcher': away.get('probablePitcher', {}).get('fullName', 'TBD'),
                'away_pitcher_id': away.get('probablePitcher', {}).get('id', ''),
                'home_pitcher': home.get('probablePitcher', {}).get('fullName', 'TBD'),
                'home_pitcher_id': home.get('probablePitcher', {}).get('id', ''),
                'venue': game.get('venue', {}).get('name', ''),
            })

    return games


def get_lineups(game_pk):
    """
    Fetch confirmed lineups for a specific game.

    Returns:
        dict with 'away' and 'home' lists of player dicts
    """
    resp = requests.get(f"{MLB_API}.1/game/{game_pk}/feed/live")
    resp.raise_for_status()
    feed = resp.json()

    boxscore = feed.get('liveData', {}).get('boxscore', {})
    result = {}

    for side in ['away', 'home']:
        team_data = boxscore.get('teams', {}).get(side, {})
        team_name = team_data.get('team', {}).get('name', '')
        batting_order = team_data.get('battingOrder', [])
        players = team_data.get('players', {})

        lineup = []
        for i, pid in enumerate(batting_order):
            p = players.get(f'ID{pid}', {})
            person = p.get('person', {})
            position = p.get('position', {})
            lineup.append({
                'batting_order': i + 1,
                'player_id': pid,
                'name': person.get('fullName', ''),
                'position': position.get('abbreviation', ''),
                'bats': person.get('batSide', {}).get('code', ''),
                'team': team_name,
            })

        result[side] = lineup

    return result


def get_all_lineups(games):
    """
    Fetch lineups for all of today's games.

    Returns:
        pd.DataFrame with all confirmed lineup entries
    """
    all_lineups = []

    for game in games:
        try:
            lineups = get_lineups(game['game_pk'])
            for side in ['away', 'home']:
                for player in lineups[side]:
                    player['game_pk'] = game['game_pk']
                    player['opponent'] = game['home_abbr'] if side == 'away' else game['away_abbr']
                    player['opp_pitcher'] = game['home_pitcher'] if side == 'away' else game['away_pitcher']
                    player['venue'] = game['venue']
                    all_lineups.append(player)
        except Exception as e:
            print(f"    Warning: Could not fetch lineup for {game['away_team']} @ {game['home_team']}: {e}")

    return pd.DataFrame(all_lineups)


def fetch_daily_mlb_data(game_date=None):
    """
    Full daily MLB data pull.

    Returns:
        tuple: (games_df, lineups_df)
    """
    print("  Fetching today's schedule...")
    games = get_todays_games(game_date)
    games_df = pd.DataFrame(games)
    print(f"  Found {len(games)} games")

    for g in games:
        print(f"    {g['away_team']} @ {g['home_team']} ({g['status']})")
        print(f"      {g['away_pitcher']} vs {g['home_pitcher']}")

    print("  Fetching confirmed lineups...")
    lineups_df = get_all_lineups(games)
    lineup_count = lineups_df['name'].nunique() if not lineups_df.empty else 0
    print(f"  Got {lineup_count} players in confirmed lineups")

    return games_df, lineups_df


if __name__ == "__main__":
    games_df, lineups_df = fetch_daily_mlb_data()

    print(f"\n=== Games ({len(games_df)}) ===")
    print(games_df[['away_team', 'home_team', 'away_pitcher', 'home_pitcher', 'status']].to_string(index=False))

    if not lineups_df.empty:
        print(f"\n=== Sample Lineups ===")
        print(lineups_df[['batting_order', 'name', 'position', 'team', 'opp_pitcher']].head(20).to_string(index=False))
