"""Debug: see all stat IDs returned for a player."""
import os
import xml.etree.ElementTree as ET
from dotenv import load_dotenv
from src.yahoo_client import YahooClient

load_dotenv()

client = YahooClient()
league_id = os.getenv('YAHOO_LEAGUE_ID')
base = "https://fantasysports.yahooapis.com/fantasy/v2"

# Hitter: Freddie Freeman
print("=== Hitter: Freddie Freeman (469.p.8658) ===")
url = f"{base}/league/{league_id}/players;player_keys=469.p.8658/stats;type=season;season=2026"
resp = client.oauth.session.get(url)
root = ET.fromstring(resp.content)
ns = {'y': 'http://fantasysports.yahooapis.com/fantasy/v2/base.rng'}
for stat in root.findall('.//y:stat', ns):
    sid = stat.findtext('y:stat_id', '', ns)
    val = stat.findtext('y:value', '', ns)
    print(f"  stat_id={sid}  value={val}")

# Pitcher: Paul Skenes
print("\n=== Pitcher: Paul Skenes (469.p.62972) ===")
url = f"{base}/league/{league_id}/players;player_keys=469.p.62972/stats;type=season;season=2026"
resp = client.oauth.session.get(url)
root = ET.fromstring(resp.content)
for stat in root.findall('.//y:stat', ns):
    sid = stat.findtext('y:stat_id', '', ns)
    val = stat.findtext('y:value', '', ns)
    print(f"  stat_id={sid}  value={val}")

# Also print league stat categories for reference
print("\n=== League stat categories ===")
url = f"{base}/league/{league_id}/settings"
resp = client.oauth.session.get(url)
root = ET.fromstring(resp.content)
for stat in root.findall('.//y:stat', ns):
    sid = stat.findtext('y:stat_id', '', ns)
    name = stat.findtext('y:display_name', '', ns)
    enabled = stat.findtext('y:enabled', '', ns)
    pos = stat.findtext('y:position_type', '', ns)
    if enabled == '1':
        print(f"  stat_id={sid}  name={name}  position_type={pos}")
