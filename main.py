from src.yahoo_client import YahooClient
from src.projections import fetch_steamer_projections
from src.valuation import calculate_player_values
import os
from dotenv import load_dotenv

load_dotenv()

def main():
    league_id = os.getenv('YAHOO_LEAGUE_ID')
    client = YahooClient()

    # Fetch roster and free agents
    roster = client.get_roster(league_id)
    free_agents = client.get_free_agents(league_id)

    # Collect all player keys
    all_player_keys = [p['player_key'] for p in roster + free_agents]

    # Fetch projections
    projections = fetch_steamer_projections(league_id, all_player_keys)

    # Calculate values
    valued_players = calculate_player_values(projections)

    # Logic to compare roster vs free agents
    # Identify lowest value bench player and highest value free agent
    # If optimal transaction exists, execute it

    # Stub for now
    print("Bot running...")

if __name__ == "__main__":
    main()