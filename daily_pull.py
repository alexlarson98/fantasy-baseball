"""
Daily data pull from Yahoo Fantasy Baseball API.

Fetches roster, free agents, season stats, and league settings,
then saves everything to data/{date}/ as CSV files.
"""
import os
from datetime import date
from dotenv import load_dotenv
from src.yahoo_client import YahooClient
import pandas as pd
import json

load_dotenv()


def pull_daily_data():
    league_id = os.getenv('YAHOO_LEAGUE_ID')
    if not league_id:
        print("ERROR: Set YAHOO_LEAGUE_ID in .env")
        return

    today = date.today().isoformat()
    data_dir = os.path.join('data', today)
    os.makedirs(data_dir, exist_ok=True)

    print(f"=== Daily Pull for {today} ===")
    print(f"League: {league_id}")
    print(f"Saving to: {data_dir}/\n")

    client = YahooClient()

    # 1. League settings
    print("Fetching league settings...")
    settings = client.get_league_settings(league_id)
    with open(os.path.join(data_dir, 'league_settings.json'), 'w') as f:
        json.dump(settings, f, indent=2)
    print(f"  League: {settings['name']} ({settings['num_teams']} teams)")
    print(f"  Scoring categories: {len(settings['categories'])}")
    print(f"  Roster positions: {[p['position'] + 'x' + p['count'] for p in settings['roster_positions']]}")

    # 2. Roster
    print("\nFetching your roster...")
    roster = client.get_roster(league_id)
    print(f"  Found {len(roster)} players on roster")
    for p in roster:
        slot = p.get('selected_position', '?')
        print(f"    [{slot}] {p['name']} ({p['position']}) - {p['team']}")
    pd.DataFrame(roster).to_csv(os.path.join(data_dir, 'roster.csv'), index=False)

    # 3. Free agents (top 500)
    print("\nFetching free agents (top 500)...")
    free_agents = client.get_free_agents(league_id, count=500)
    print(f"  Found {len(free_agents)} free agents")
    pd.DataFrame(free_agents).to_csv(os.path.join(data_dir, 'free_agents.csv'), index=False)

    # 4. Season stats for roster
    print("\nFetching season stats for roster players...")
    roster_keys = [p['player_key'] for p in roster]
    roster_stats = client.get_player_stats(league_id, roster_keys, stat_type='season')
    roster_stats.to_csv(os.path.join(data_dir, 'roster_stats.csv'), index=False)
    print(f"  Got stats for {len(roster_stats)} roster players")

    # 5. Season stats for free agents
    print("\nFetching season stats for free agents...")
    fa_keys = [p['player_key'] for p in free_agents]
    fa_stats = client.get_player_stats(league_id, fa_keys, stat_type='season')
    fa_stats.to_csv(os.path.join(data_dir, 'fa_stats.csv'), index=False)
    print(f"  Got stats for {len(fa_stats)} free agents")

    # 6. Last week stats for roster (useful for hot/cold streaks)
    print("\nFetching last week stats for roster players...")
    roster_lastweek = client.get_player_stats(league_id, roster_keys, stat_type='lastweek')
    roster_lastweek.to_csv(os.path.join(data_dir, 'roster_lastweek.csv'), index=False)
    print(f"  Got last week stats for {len(roster_lastweek)} roster players")

    # Summary
    print(f"\nDone! All data saved to {data_dir}/")
    print("Files:")
    for f in sorted(os.listdir(data_dir)):
        size = os.path.getsize(os.path.join(data_dir, f))
        print(f"  {f} ({size:,} bytes)")


if __name__ == "__main__":
    pull_daily_data()
