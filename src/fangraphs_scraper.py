"""
Fetch projections from FanGraphs API.

Supports both rest-of-season (Depth Charts) and daily (THE BAT X) projections.
Uses the FanGraphs internal API directly — no browser needed.
"""
import requests
import pandas as pd

FANGRAPHS_API = "https://www.fangraphs.com/api/projections"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json',
    'Referer': 'https://www.fangraphs.com/projections',
}


def _fetch_projections(proj_type, stats_type):
    """
    Fetch projections from FanGraphs API.

    Args:
        proj_type: projection system (e.g., 'rfangraphsdc', 'thebatx')
        stats_type: 'bat' for hitters, 'pit' for pitchers

    Returns:
        list of dicts (raw JSON response)
    """
    params = {
        'type': proj_type,
        'stats': stats_type,
        'pos': 'all',
        'team': '0',
        'players': '0',
        'lg': 'all',
    }

    resp = requests.get(FANGRAPHS_API, params=params, headers=HEADERS)
    resp.raise_for_status()
    return resp.json()


def fetch_hitter_projections(proj_type='rfangraphsdc'):
    """Fetch and format hitter projections."""
    data = _fetch_projections(proj_type, 'bat')
    df = pd.DataFrame(data)

    # Compute TB = H + 2B + 2*3B + 3*HR
    if all(c in df.columns for c in ['H', '2B', '3B', 'HR']):
        df['TB'] = df['H'] + df['2B'] + 2 * df['3B'] + 3 * df['HR']

    # Select relevant columns for your league categories
    keep = ['PlayerName', 'Team', 'G', 'PA', 'AB', 'H', '2B', '3B',
            'HR', 'R', 'RBI', 'BB', 'SO', 'SB', 'CS', 'AVG', 'OBP',
            'SLG', 'OPS', 'TB', 'wOBA', 'wRC+', 'WAR', 'ADP']
    keep = [c for c in keep if c in df.columns]
    df = df[keep].copy()

    # Rename for consistency
    df = df.rename(columns={'PlayerName': 'Name', 'SO': 'K'})

    return df


def fetch_pitcher_projections(proj_type='rfangraphsdc'):
    """Fetch and format pitcher projections."""
    data = _fetch_projections(proj_type, 'pit')
    df = pd.DataFrame(data)

    # Compute SV+H
    if 'SV' in df.columns and 'HLD' in df.columns:
        df['SV+H'] = df['SV'] + df['HLD']

    # Select relevant columns
    keep = ['PlayerName', 'Team', 'GS', 'G', 'IP', 'W', 'L', 'SV', 'HLD',
            'SV+H', 'QS', 'SO', 'BB', 'HR', 'ERA', 'WHIP', 'K/9', 'BB/9',
            'FIP', 'WAR', 'ADP']
    keep = [c for c in keep if c in df.columns]
    df = df[keep].copy()

    # Rename for consistency
    df = df.rename(columns={'PlayerName': 'Name', 'SO': 'K'})

    return df


def scrape_fangraphs_projections():
    """
    Fetch FanGraphs Depth Charts ROS projections.

    Returns:
        tuple: (hitters_df, pitchers_df)
    """
    print("  Fetching ROS hitter projections (Depth Charts)...")
    hitters_df = fetch_hitter_projections('rfangraphsdc')
    print(f"  Got {len(hitters_df)} hitters")

    print("  Fetching ROS pitcher projections (Depth Charts)...")
    pitchers_df = fetch_pitcher_projections('rfangraphsdc')
    print(f"  Got {len(pitchers_df)} pitchers")

    return hitters_df, pitchers_df


if __name__ == "__main__":
    print("=== ROS Projections (Depth Charts) ===")
    hitters, pitchers = scrape_fangraphs_projections()
    cols = [c for c in ['Name', 'Team', 'R', 'HR', 'RBI', 'SB', 'TB', 'OBP'] if c in hitters.columns]
    print(hitters[cols].head(10).to_string(index=False))

    cols = [c for c in ['Name', 'Team', 'W', 'K', 'ERA', 'WHIP', 'QS', 'SV+H'] if c in pitchers.columns]
    print(pitchers[cols].head(10).to_string(index=False))
