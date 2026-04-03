"""
Fetch daily projections from FantasyPros.

Requires a FantasyPros session cookie (set FANTASYPROS_COOKIE in .env).
Scrapes the daily hitter and pitcher projection tables.
"""
import os
import requests
import pandas as pd
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://www.fantasypros.com/mlb/projections"


def _get_session():
    """Create a requests session with FantasyPros auth cookie."""
    cookie = os.getenv('FANTASYPROS_COOKIE', '')
    if not cookie:
        print("  WARNING: FANTASYPROS_COOKIE not set in .env — will only get 10 rows")

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Cookie': cookie,
    })
    return session


def _scrape_table(session, url):
    """Scrape projection table from a FantasyPros page."""
    resp = session.get(url)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, 'html.parser')
    table = soup.find('table', id='data')
    if not table:
        print(f"  WARNING: No table found at {url}")
        return pd.DataFrame()

    # Headers
    headers = [th.get_text(strip=True) for th in table.find('thead').find_all('th')]

    # Rows
    rows = []
    for tr in table.find('tbody').find_all('tr'):
        cells = [td.get_text(strip=True) for td in tr.find_all('td')]
        if cells:
            rows.append(cells)

    if not rows:
        return pd.DataFrame()

    # Check if cookie is stale (free tier only gets 10 rows)
    if len(rows) <= 10:
        raise RuntimeError(
            "FantasyPros returned only 10 rows — your session cookie is likely expired.\n"
            "To fix: log into fantasypros.com in your browser, open DevTools (F12) → Console,\n"
            "run document.cookie, and update FANTASYPROS_COOKIE in your .env file."
        )

    # Build DataFrame, trimming rows to match header length
    cleaned = []
    for row in rows:
        if len(row) >= len(headers):
            cleaned.append(row[:len(headers)])

    df = pd.DataFrame(cleaned, columns=headers)

    # Parse player name and team from "Player" column
    if 'Player' in df.columns:
        df['Name'] = df['Player'].str.extract(r'^(.+?)(?:\(|$)')[0].str.strip()
        df['Team'] = df['Player'].str.extract(r'\((\w+)\s*-')[0]
        df['Position'] = df['Player'].str.extract(r'-\s*([^)]+)\)')[0]

    # Convert numeric columns
    skip = {'VBR', 'Player', 'Opp', 'Name', 'Team', 'Position', 'Rost%'}
    for col in df.columns:
        if col in skip:
            continue
        df[col] = pd.to_numeric(df[col].str.replace('%', ''), errors='coerce')

    return df


def fetch_daily_hitter_projections():
    """Fetch today's hitter projections."""
    session = _get_session()
    df = _scrape_table(session, f"{BASE_URL}/daily-hitters.php")

    # Compute TB = H + 2B + 2*3B + 3*HR
    if not df.empty and all(c in df.columns for c in ['H', '2B', '3B', 'HR']):
        df['TB'] = df['H'] + df['2B'] + 2 * df['3B'] + 3 * df['HR']

    return df


def fetch_daily_pitcher_projections():
    """Fetch today's pitcher projections."""
    session = _get_session()
    df = _scrape_table(session, f"{BASE_URL}/daily-pitchers.php")

    # Compute SV+H if both exist (FantasyPros only has SV for pitchers)
    # HLD not available in daily projections

    return df


def scrape_daily_projections():
    """
    Fetch FantasyPros daily projections for hitters and pitchers.

    Returns:
        tuple: (hitters_df, pitchers_df)
    """
    print("  Fetching daily hitter projections...")
    hitters = fetch_daily_hitter_projections()
    print(f"  Got {len(hitters)} hitters")

    print("  Fetching daily pitcher projections...")
    pitchers = fetch_daily_pitcher_projections()
    print(f"  Got {len(pitchers)} pitchers")

    return hitters, pitchers


if __name__ == "__main__":
    hitters, pitchers = scrape_daily_projections()

    print(f"\n=== Daily Hitter Projections ({len(hitters)}) ===")
    cols = [c for c in ['VBR', 'Name', 'Team', 'Opp', 'R', 'HR', 'RBI', 'SB', 'TB', 'OBP'] if c in hitters.columns]
    print(hitters[cols].head(15).to_string(index=False))

    print(f"\n=== Daily Pitcher Projections ({len(pitchers)}) ===")
    cols = [c for c in ['VBR', 'Name', 'Team', 'Opp', 'IP', 'K', 'W', 'SV', 'ERA', 'WHIP'] if c in pitchers.columns]
    print(pitchers[cols].head(15).to_string(index=False))
