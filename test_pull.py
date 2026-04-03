"""Quick test to verify Yahoo API connection and pull sample data."""
import os
from dotenv import load_dotenv
from src.yahoo_client import YahooClient

load_dotenv()

league_id = os.getenv('YAHOO_LEAGUE_ID')
if not league_id or league_id == 'your_league_id_here':
    print("ERROR: Set your YAHOO_LEAGUE_ID in .env first!")
    print("Find it in your Yahoo Fantasy league URL: https://baseball.fantasysports.yahoo.com/b1/XXXXX")
    exit(1)

print(f"League ID: {league_id}")
print("Initializing Yahoo client (this will open a browser for OAuth if first time)...")

client = YahooClient()

# Test 1: Fetch roster
print("\n--- Fetching your roster ---")
try:
    roster = client.get_roster(league_id)
    print(f"Found {len(roster)} players on your roster:")
    for p in roster:
        print(f"  {p['name']} ({p['position']}) - key: {p['player_key']}")
except Exception as e:
    print(f"Error fetching roster: {e}")
    roster = []

# Test 2: Fetch free agents
print("\n--- Fetching top free agents ---")
try:
    free_agents = client.get_free_agents(league_id)
    print(f"Found {len(free_agents)} free agents. Top 10:")
    for p in free_agents[:10]:
        print(f"  {p['name']} ({p['position']}) - key: {p['player_key']}")
except Exception as e:
    print(f"Error fetching free agents: {e}")
    free_agents = []

# Test 3: Fetch projections for roster players
if roster:
    print("\n--- Fetching projections for your roster ---")
    try:
        player_keys = [p['player_key'] for p in roster]
        projections = client.get_player_projections(league_id, player_keys)
        print(f"Got projections for {len(projections)} players:")
        print(projections.to_string(index=False))
    except Exception as e:
        print(f"Error fetching projections: {e}")

print("\nDone!")
