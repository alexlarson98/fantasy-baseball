"""
Daily data pull from Yahoo Fantasy, PitcherList, FantasyPros, and MLB Stats API.

Saves everything to data/{date}/ organized by source:
  yahoo/               - Roster, free agents, season stats, league settings
  pitcherlist/         - SP streamer tiers + waiver "Top Priority" adds (primary pitcher signal)
  projections_fpros/   - FantasyPros daily projections (hitter signal + pitcher cross-check)
  savant/              - Baseball Savant Statcast percentile rankings (underlying skill)
  mlb/                 - Today's games, lineups, probable pitchers
"""
import os
from datetime import date
from dotenv import load_dotenv
from src.yahoo_client import YahooClient
from src.pitcherlist_scraper import scrape_pitcherlist_daily
from src.fantasypros_scraper import scrape_daily_projections
from src.savant_scraper import scrape_savant_daily
from src.mlb_client import fetch_daily_mlb_data
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
    yahoo_dir = os.path.join(data_dir, 'yahoo')
    pitcherlist_dir = os.path.join(data_dir, 'pitcherlist')
    fpros_dir = os.path.join(data_dir, 'projections_fpros')
    savant_dir = os.path.join(data_dir, 'savant')
    mlb_dir = os.path.join(data_dir, 'mlb')
    for d in [yahoo_dir, pitcherlist_dir, fpros_dir, savant_dir, mlb_dir]:
        os.makedirs(d, exist_ok=True)

    print(f"=== Daily Pull for {today} ===")
    print(f"League: {league_id}")
    print(f"Saving to: {data_dir}/\n")

    client = YahooClient()

    # 1. League settings
    print("[1/9] Fetching league settings...")
    settings = client.get_league_settings(league_id)
    with open(os.path.join(yahoo_dir, 'league_settings.json'), 'w') as f:
        json.dump(settings, f, indent=2)
    print(f"  League: {settings['name']} ({settings['num_teams']} teams)")
    print(f"  Scoring categories: {len(settings['categories'])}")
    print(f"  Roster positions: {[p['position'] + 'x' + p['count'] for p in settings['roster_positions']]}")

    # 2. Roster
    print("\n[2/9] Fetching your roster...")
    roster = client.get_roster(league_id)
    print(f"  Found {len(roster)} players on roster")
    for p in roster:
        slot = p.get('selected_position', '?')
        print(f"    [{slot}] {p['name']} ({p['position']}) - {p['team']}")
    pd.DataFrame(roster).to_csv(os.path.join(yahoo_dir, 'roster.csv'), index=False)

    # 3. Free agents (top 500)
    print("\n[3/9] Fetching free agents (top 500)...")
    free_agents = client.get_free_agents(league_id, count=500)
    print(f"  Found {len(free_agents)} free agents")
    pd.DataFrame(free_agents).to_csv(os.path.join(yahoo_dir, 'free_agents.csv'), index=False)

    # 4. Season stats for roster
    print("\n[4/9] Fetching season stats for roster players...")
    roster_keys = [p['player_key'] for p in roster]
    roster_stats = client.get_player_stats(league_id, roster_keys, stat_type='season')
    roster_stats.to_csv(os.path.join(yahoo_dir, 'roster_stats.csv'), index=False)
    print(f"  Got stats for {len(roster_stats)} roster players")

    # 5. Season stats for free agents
    print("\n[5/9] Fetching season stats for free agents...")
    fa_keys = [p['player_key'] for p in free_agents]
    fa_stats = client.get_player_stats(league_id, fa_keys, stat_type='season')
    fa_stats.to_csv(os.path.join(yahoo_dir, 'fa_stats.csv'), index=False)
    print(f"  Got stats for {len(fa_stats)} free agents")

    # 6. PitcherList: streamer tiers, waiver "Top Priority", weekly Top 150 H / Top 100 SP
    print("\n[6/9] Fetching PitcherList streamer ranks, waiver adds, weekly rankings...")
    pl_streamers, pl_waiver, pl_top_hitters, pl_top_pitchers = scrape_pitcherlist_daily(today)
    pl_streamers.to_csv(os.path.join(pitcherlist_dir, 'sp_streamers.csv'), index=False)
    pl_waiver.to_csv(os.path.join(pitcherlist_dir, 'waiver_adds.csv'), index=False)
    pl_top_hitters.to_csv(os.path.join(pitcherlist_dir, 'top_hitters.csv'), index=False)
    pl_top_pitchers.to_csv(os.path.join(pitcherlist_dir, 'top_pitchers.csv'), index=False)

    # 7. FantasyPros daily projections (hitter signal + pitcher cross-check)
    print("\n[7/9] Fetching FantasyPros daily projections...")
    fp_hitters, fp_pitchers = scrape_daily_projections()
    fp_hitters.to_csv(os.path.join(fpros_dir, 'hitters.csv'), index=False)
    fp_pitchers.to_csv(os.path.join(fpros_dir, 'pitchers.csv'), index=False)

    # 8. Baseball Savant Statcast percentile rankings (underlying skill)
    print("\n[8/9] Fetching Baseball Savant Statcast percentiles...")
    sv_hitters, sv_pitchers = scrape_savant_daily(int(today[:4]))
    sv_hitters.to_csv(os.path.join(savant_dir, 'hitters.csv'), index=False)
    sv_pitchers.to_csv(os.path.join(savant_dir, 'pitchers.csv'), index=False)

    # 9. MLB daily games + lineups
    print("\n[9/9] Fetching today's MLB games and lineups...")
    games_df, lineups_df = fetch_daily_mlb_data(today)
    games_df.to_csv(os.path.join(mlb_dir, 'games.csv'), index=False)
    lineups_df.to_csv(os.path.join(mlb_dir, 'lineups.csv'), index=False)

    # Summary
    print(f"\n{'='*50}")
    print(f"Done! All data saved to {data_dir}/")
    print(f"{'='*50}")
    for root, dirs, files in os.walk(data_dir):
        level = root.replace(data_dir, '').count(os.sep)
        indent = '  ' * level
        folder = os.path.basename(root)
        if level > 0:
            print(f"{indent}{folder}/")
        for f in sorted(files):
            size = os.path.getsize(os.path.join(root, f))
            print(f"{indent}  {f} ({size:,} bytes)")


if __name__ == "__main__":
    pull_daily_data()
