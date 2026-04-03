from src.yahoo_client import YahooClient
import pandas as pd

def fetch_steamer_projections(league_id, player_keys):
    """
    Fetch projected stats for given players from Yahoo API.

    Args:
        league_id (str): Yahoo league ID
        player_keys (list): List of player keys

    Returns:
        pd.DataFrame: DataFrame with player projections
    """
    client = YahooClient()
    projections = client.get_player_projections(league_id, player_keys)
    return projections