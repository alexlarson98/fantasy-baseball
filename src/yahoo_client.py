import os
from yahoo_oauth import OAuth2
from dotenv import load_dotenv
import xml.etree.ElementTree as ET
import pandas as pd

load_dotenv()

NS = {'y': 'http://fantasysports.yahooapis.com/fantasy/v2/base.rng'}
BASE_URL = "https://fantasysports.yahooapis.com/fantasy/v2"


class YahooWriteError(Exception):
    """A transaction was rejected by Yahoo (bad roster slot, failed waiver claim, read-only token...)."""


def _extract_yahoo_error(body):
    """Pull the human-readable message out of a Yahoo error response."""
    try:
        root = ET.fromstring(body)
        desc = root.findtext('.//{http://yahoo.com}description')
        if desc:
            return desc
    except ET.ParseError:
        pass
    return (body or '').strip()[:500]


class YahooClient:
    def __init__(self):
        self.oauth = OAuth2(None, None, from_file='oauth_token.json')

    def _ensure_token(self):
        """Refresh the access token if it has expired."""
        if not self.oauth.token_is_valid():
            self.oauth.refresh_access_token()

    def _get(self, url):
        """Make authenticated GET request and return parsed XML root."""
        self._ensure_token()
        response = self.oauth.session.get(url)
        if response.status_code != 200:
            raise Exception(f"Yahoo API error {response.status_code}: {url}\n{response.text[:500]}")
        return ET.fromstring(response.content)

    def _post(self, url, xml_body):
        """Make an authenticated POST of an XML payload. Returns the parsed response root."""
        self._ensure_token()
        response = self.oauth.session.post(
            url,
            data=xml_body.encode('utf-8'),
            headers={'Content-Type': 'application/xml'},
        )
        # Yahoo answers 201 Created on an accepted transaction.
        if response.status_code not in (200, 201):
            raise YahooWriteError(
                f"Yahoo rejected the transaction (HTTP {response.status_code}).\n"
                f"{_extract_yahoo_error(response.text)}"
            )
        return ET.fromstring(response.content) if response.content else None

    def _parse_player(self, player_elem):
        """Parse a single player XML element into a dict."""
        info = {}
        info['player_key'] = player_elem.findtext('y:player_key', '', NS)
        info['player_id'] = player_elem.findtext('y:player_id', '', NS)

        name_elem = player_elem.find('y:name', NS)
        if name_elem is not None:
            info['name'] = name_elem.findtext('y:full', '', NS)

        info['position'] = player_elem.findtext('y:display_position', '', NS)
        info['team'] = player_elem.findtext('y:editorial_team_abbr', '', NS)

        # Get selected position (roster slot) if available
        sel_pos = player_elem.find('.//y:selected_position', NS)
        if sel_pos is not None:
            info['selected_position'] = sel_pos.findtext('y:position', '', NS)

        # Get percent owned if available
        pct = player_elem.find('.//y:percent_owned', NS)
        if pct is not None:
            info['percent_owned'] = int(pct.findtext('y:value', '0', NS) or 0)
            info['percent_owned_delta'] = int(pct.findtext('y:delta', '0', NS) or 0)

        return info

    def _parse_player_stats(self, player_elem, stat_map):
        """Parse a player element that includes stats."""
        info = self._parse_player(player_elem)

        for stat in player_elem.findall('.//y:stat', NS):
            stat_id = stat.findtext('y:stat_id', '', NS)
            value = stat.findtext('y:value', '', NS)
            if stat_id in stat_map:
                try:
                    info[stat_map[stat_id]] = float(value) if value and value != '-' else 0
                except ValueError:
                    info[stat_map[stat_id]] = 0

        return info

    def get_team_key(self, league_id):
        """Find the logged-in user's team key for a given league."""
        game_key = league_id.split('.')[0]
        root = self._get(f"{BASE_URL}/users;use_login=1/games;game_keys={game_key}/teams")
        for team in root.findall('.//y:team', NS):
            team_key = team.findtext('y:team_key', '', NS)
            if league_id in team_key:
                return team_key
        raise Exception(f"Could not find your team in league {league_id}")

    def get_roster(self, league_id, team_key=None):
        """Fetch current roster for the user's team."""
        if not team_key:
            team_key = self.get_team_key(league_id)

        # Hydrate percent_owned so roster players carry ownership like free agents do.
        root = self._get(f"{BASE_URL}/team/{team_key}/roster/players/percent_owned")
        players = []
        for player in root.findall('.//y:player', NS):
            players.append(self._parse_player(player))
        return players

    def get_free_agents(self, league_id, count=100):
        """Fetch top available free agents sorted by add rank, with ownership %."""
        players = []
        start = 0
        while start < count:
            batch = min(25, count - start)
            root = self._get(
                f"{BASE_URL}/league/{league_id}/players;status=FA;sort=AR;start={start};count={batch}/percent_owned"
            )
            batch_players = root.findall('.//y:player', NS)
            if not batch_players:
                break
            for player in batch_players:
                players.append(self._parse_player(player))
            start += batch
        return players

    def get_player_stats(self, league_id, player_keys, stat_type='season', season=2026):
        """
        Fetch stats for given players.

        Args:
            league_id: Yahoo league key (e.g., '469.l.68424')
            player_keys: List of player keys
            stat_type: 'season', 'date', 'lastweek'
            season: Season year (used with 'season' type)
            date: Specific date string (used with 'date' type)

        Returns:
            pd.DataFrame with player stats
        """
        if not player_keys:
            return pd.DataFrame()

        # Yahoo stat ID mapping (league-specific IDs from settings)
        # Batters: R, HR, RBI, SB, TB, OBP
        # Pitchers: W, K, ERA, WHIP, QS, SV+H
        stat_map = {
            # Batter stats
            '60': 'H/AB', '7': 'R', '12': 'HR', '13': 'RBI',
            '16': 'SB', '23': 'TB', '4': 'OBP',
            # Pitcher stats
            '50': 'IP', '28': 'W', '42': 'K',
            '26': 'ERA', '27': 'WHIP', '83': 'QS', '89': 'SV+H',
        }

        all_players = []
        # Yahoo API limits player keys per request, batch in groups of 25
        for i in range(0, len(player_keys), 25):
            batch_keys = player_keys[i:i + 25]
            keys_str = ','.join(batch_keys)

            url = f"{BASE_URL}/league/{league_id}/players;player_keys={keys_str}/stats;type={stat_type};season={season}"

            root = self._get(url)
            for player in root.findall('.//y:player', NS):
                all_players.append(self._parse_player_stats(player, stat_map))

        return pd.DataFrame(all_players)

    def get_league_settings(self, league_id):
        """Fetch league settings including scoring categories."""
        root = self._get(f"{BASE_URL}/league/{league_id}/settings")

        settings = {}
        settings['name'] = root.findtext('.//y:name', '', NS)
        settings['num_teams'] = root.findtext('.//y:num_teams', '', NS)
        settings['scoring_type'] = root.findtext('.//y:scoring_type', '', NS)

        # Get stat categories
        categories = []
        for stat in root.findall('.//y:stat', NS):
            cat = {
                'stat_id': stat.findtext('y:stat_id', '', NS),
                'name': stat.findtext('y:name', '', NS),
                'display_name': stat.findtext('y:display_name', '', NS),
                'position_type': stat.findtext('y:position_type', '', NS),
            }
            enabled = stat.findtext('y:enabled', '', NS)
            if enabled == '1':
                categories.append(cat)
        settings['categories'] = categories

        # Get roster positions
        positions = []
        for pos in root.findall('.//y:roster_position', NS):
            positions.append({
                'position': pos.findtext('y:position', '', NS),
                'count': pos.findtext('y:count', '', NS),
            })
        settings['roster_positions'] = positions

        return settings

    def get_ownership(self, league_id, player_keys):
        """
        Map player_key -> ownership type ('freeagents', 'waivers', 'team').

        An add for a player on waivers must be filed as a waiver claim, not a
        straight add, so callers need this before building a transaction.
        """
        if not player_keys:
            return {}

        ownership = {}
        for i in range(0, len(player_keys), 25):
            keys_str = ','.join(player_keys[i:i + 25])
            root = self._get(f"{BASE_URL}/league/{league_id}/players;player_keys={keys_str}/ownership")
            for player in root.findall('.//y:player', NS):
                key = player.findtext('y:player_key', '', NS)
                own = player.find('.//y:ownership', NS)
                if key and own is not None:
                    ownership[key] = own.findtext('y:ownership_type', '', NS)
        return ownership

    def execute_transaction(self, league_id, add_player_key, drop_player_key=None,
                            team_key=None, faab_bid=None):
        """
        Execute an add, a drop, or a paired add/drop against Yahoo.

        Adds are routed automatically: a player sitting on waivers is filed as a
        waiver claim (with `faab_bid` if the league uses FAAB), anyone else as a
        straight free-agent add.

        Returns a dict describing what was filed. Raises YahooWriteError if Yahoo
        refuses it -- most commonly because the app is registered read-only.
        """
        if not team_key:
            team_key = self.get_team_key(league_id)

        on_waivers = False
        if add_player_key:
            on_waivers = self.get_ownership(league_id, [add_player_key]).get(add_player_key) == 'waivers'

        if add_player_key and drop_player_key:
            txn_type = 'waiver' if on_waivers else 'add/drop'
        elif add_player_key:
            txn_type = 'waiver' if on_waivers else 'add'
        elif drop_player_key:
            txn_type = 'drop'
        else:
            raise ValueError("execute_transaction needs an add, a drop, or both")

        players = []
        if add_player_key:
            players.append(
                f"<player>"
                f"<player_key>{add_player_key}</player_key>"
                f"<transaction_data>"
                f"<type>add</type>"
                f"<destination_team_key>{team_key}</destination_team_key>"
                f"</transaction_data>"
                f"</player>"
            )
        if drop_player_key:
            players.append(
                f"<player>"
                f"<player_key>{drop_player_key}</player_key>"
                f"<transaction_data>"
                f"<type>drop</type>"
                f"<source_team_key>{team_key}</source_team_key>"
                f"</transaction_data>"
                f"</player>"
            )

        # A single-player transaction wraps the player directly; a pair wraps them in <players>.
        players_xml = f"<players>{''.join(players)}</players>" if len(players) > 1 else players[0]
        bid_xml = f"<faab_bid>{int(faab_bid)}</faab_bid>" if (txn_type == 'waiver' and faab_bid is not None) else ''

        body = (
            '<?xml version="1.0"?>'
            '<fantasy_content>'
            '<transaction>'
            f'<type>{txn_type}</type>'
            f'{bid_xml}'
            f'{players_xml}'
            '</transaction>'
            '</fantasy_content>'
        )

        self._post(f"{BASE_URL}/league/{league_id}/transactions", body)

        return {
            'type': txn_type,
            'add': add_player_key,
            'drop': drop_player_key,
            'faab_bid': faab_bid if txn_type == 'waiver' else None,
            'pending': txn_type == 'waiver',  # waiver claims clear overnight, they aren't instant
        }
