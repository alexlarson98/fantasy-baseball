"""
Daily lineup recommendation engine.

Matches your Yahoo roster to FantasyPros daily projections and MLB game data
to produce start/sit recommendations for hitters and pitchers.
"""
import os
import pandas as pd
import json
from datetime import date


def _normalize_name(name):
    """Normalize player names for fuzzy matching."""
    import unicodedata
    if not isinstance(name, str):
        return ''
    # Remove accents
    name = unicodedata.normalize('NFD', name)
    name = ''.join(c for c in name if unicodedata.category(c) != 'Mn')
    # Lowercase, strip suffixes
    name = name.lower().strip()
    for suffix in [' jr.', ' jr', ' sr.', ' sr', ' ii', ' iii', ' iv']:
        name = name.replace(suffix, '')
    return name.strip()


def _match_projection(player_name, proj_df):
    """Find a player's daily projection by name matching."""
    norm_name = _normalize_name(player_name)
    proj_df = proj_df.copy()
    proj_df['_norm'] = proj_df['Name'].apply(_normalize_name)

    match = proj_df[proj_df['_norm'] == norm_name]
    if not match.empty:
        return match.iloc[0]

    # Try last name match as fallback
    last = norm_name.split()[-1] if norm_name else ''
    if last:
        candidates = proj_df[proj_df['_norm'].str.endswith(last)]
        if len(candidates) == 1:
            return candidates.iloc[0]

    return None



# Yahoo team abbr -> MLB team name mapping
# Yahoo team abbr -> search term for matching MLB team names
TEAM_MAP = {
    'ARI': 'Arizona', 'AZ': 'Arizona',
    'ATL': 'Atlanta', 'BAL': 'Baltimore', 'BOS': 'Boston',
    'CHC': 'Cubs', 'CWS': 'White Sox', 'CHW': 'White Sox',
    'CIN': 'Cincinnati', 'CLE': 'Cleveland',
    'COL': 'Colorado', 'DET': 'Detroit', 'HOU': 'Houston',
    'KC': 'Kansas City', 'KCR': 'Kansas City',
    'LAA': 'Angels', 'LAD': 'Dodgers',
    'MIA': 'Miami', 'MIL': 'Milwaukee', 'MIN': 'Minnesota',
    'NYM': 'Mets', 'NYY': 'Yankees',
    'OAK': 'Athletics', 'ATH': 'Athletics',
    'PHI': 'Philadelphia', 'PIT': 'Pittsburgh',
    'SD': 'San Diego', 'SDP': 'San Diego',
    'SEA': 'Seattle', 'SF': 'San Francisco', 'SFG': 'San Francisco',
    'STL': 'St. Louis', 'TB': 'Tampa Bay', 'TBR': 'Tampa Bay',
    'TEX': 'Texas', 'TOR': 'Toronto',
    'WSH': 'Washington', 'WAS': 'Washington',
}


def _has_game_today(player_team, games_df):
    """Check if a player's team has a game today."""
    if games_df.empty:
        return True  # Assume yes if no game data
    team = (player_team or '').upper()
    team_name = TEAM_MAP.get(team, team).lower()

    all_teams = (
        games_df['away_team'].fillna('').str.lower().tolist() +
        games_df['home_team'].fillna('').str.lower().tolist()
    )
    return any(team_name in t for t in all_teams)


def generate_recommendations(data_dir=None):
    """
    Generate start/sit recommendations based on today's data.

    Args:
        data_dir: Path to today's data directory. Defaults to data/{today}/

    Returns:
        dict with 'hitters' and 'pitchers' recommendation DataFrames
    """
    if data_dir is None:
        data_dir = os.path.join('data', date.today().isoformat())

    # Load data
    roster = pd.read_csv(os.path.join(data_dir, 'yahoo', 'roster.csv'))
    daily_hitters = pd.read_csv(os.path.join(data_dir, 'projections_daily', 'hitters.csv'))
    daily_pitchers = pd.read_csv(os.path.join(data_dir, 'projections_daily', 'pitchers.csv'))
    games = pd.read_csv(os.path.join(data_dir, 'mlb', 'games.csv'))

    with open(os.path.join(data_dir, 'yahoo', 'league_settings.json')) as f:
        settings = json.load(f)

    # Split roster into hitters and pitchers
    pitcher_slots = {'SP', 'P'}
    bench_slot = 'BN'
    il_slot = 'IL'

    roster_hitters = roster[
        ~roster['selected_position'].isin(pitcher_slots | {bench_slot, il_slot})
    ].copy()
    roster_bench_hitters = roster[
        (roster['selected_position'] == bench_slot) &
        (~roster['position'].str.contains('SP', na=False))
    ].copy()
    roster_pitchers = roster[
        roster['selected_position'].isin(pitcher_slots)
    ].copy()
    roster_bench_pitchers = roster[
        (roster['selected_position'] == bench_slot) &
        (roster['position'].str.contains('SP', na=False))
    ].copy()

    # === HITTER RECOMMENDATIONS ===
    all_hitters = pd.concat([roster_hitters, roster_bench_hitters], ignore_index=True)
    hitter_recs = []

    for _, player in all_hitters.iterrows():
        rec = {
            'name': player['name'],
            'team': player['team'],
            'position': player['position'],
            'current_slot': player['selected_position'],
        }

        # Check if team plays today
        has_game = _has_game_today(player['team'], games)
        rec['has_game'] = has_game

        # Match to daily projection
        proj = _match_projection(player['name'], daily_hitters)
        if proj is not None:
            rec['daily_rank'] = int(proj.get('VBR', 9999)) if pd.notna(proj.get('VBR')) else 9999
            rec['opp'] = proj.get('Opp', '')
            rec['proj_R'] = proj.get('R', 0)
            rec['proj_HR'] = proj.get('HR', 0)
            rec['proj_RBI'] = proj.get('RBI', 0)
            rec['proj_SB'] = proj.get('SB', 0)
            rec['proj_TB'] = proj.get('TB', 0)
            rec['proj_OBP'] = proj.get('OBP', 0)
        else:
            rec['daily_rank'] = 9999
            rec['opp'] = ''
            rec['proj_R'] = 0
            rec['proj_HR'] = 0
            rec['proj_RBI'] = 0
            rec['proj_SB'] = 0
            rec['proj_TB'] = 0
            rec['proj_OBP'] = 0

        # Determine recommendation
        if not has_game:
            rec['action'] = 'SIT'
            rec['reason'] = 'No game today'
        elif rec['daily_rank'] == 9999:
            rec['action'] = 'SIT'
            rec['reason'] = 'Not in daily projections (likely not in lineup)'
        else:
            rec['action'] = 'TBD'  # Will rank below
            rec['reason'] = ''

        hitter_recs.append(rec)

    hitter_df = pd.DataFrame(hitter_recs)

    # Rank active hitters by daily rank
    active = hitter_df[hitter_df['action'] == 'TBD'].sort_values('daily_rank')

    # Determine how many hitter slots we have
    hitter_slots_config = settings.get('roster_positions', [])
    active_hitter_slots = sum(
        int(p['count']) for p in hitter_slots_config
        if p['position'] not in ('SP', 'P', 'BN', 'IL', 'IL+', 'DL', 'DL+', 'NA')
    )

    for i, idx in enumerate(active.index):
        if i < active_hitter_slots:
            hitter_df.loc[idx, 'action'] = 'START'
            hitter_df.loc[idx, 'reason'] = f'Ranked #{int(hitter_df.loc[idx, "daily_rank"])} today'
        else:
            hitter_df.loc[idx, 'action'] = 'SIT'
            hitter_df.loc[idx, 'reason'] = f'Ranked #{int(hitter_df.loc[idx, "daily_rank"])} (better options available)'

    hitter_df = hitter_df.sort_values(
        ['action', 'daily_rank'],
        key=lambda x: x.map({'START': 0, 'SIT': 1}) if x.name == 'action' else x,
        ascending=True
    ).reset_index(drop=True)

    # === PITCHER RECOMMENDATIONS ===
    all_pitchers = pd.concat([roster_pitchers, roster_bench_pitchers], ignore_index=True)
    pitcher_recs = []

    for _, player in all_pitchers.iterrows():
        rec = {
            'name': player['name'],
            'team': player['team'],
            'position': player['position'],
            'current_slot': player['selected_position'],
        }

        has_game = _has_game_today(player['team'], games)
        rec['has_game'] = has_game

        # Match to daily projection
        proj = _match_projection(player['name'], daily_pitchers)
        if proj is not None:
            rec['daily_rank'] = int(proj.get('VBR', 9999)) if pd.notna(proj.get('VBR')) else 9999
            rec['opp'] = proj.get('Opp', '')
            rec['proj_IP'] = proj.get('IP', 0)
            rec['proj_K'] = proj.get('K', 0)
            rec['proj_W'] = proj.get('W', 0)
            rec['proj_ERA'] = proj.get('ERA', 0)
            rec['proj_WHIP'] = proj.get('WHIP', 0)
            rec['proj_SV'] = proj.get('SV', 0)
            rec['pitching_today'] = True
        else:
            rec['daily_rank'] = 9999
            rec['opp'] = ''
            rec['proj_IP'] = 0
            rec['proj_K'] = 0
            rec['proj_W'] = 0
            rec['proj_ERA'] = 0
            rec['proj_WHIP'] = 0
            rec['proj_SV'] = 0
            rec['pitching_today'] = False

        if not has_game:
            rec['action'] = 'SIT'
            rec['reason'] = 'No game today'
        elif not rec['pitching_today']:
            rec['action'] = 'SIT'
            rec['reason'] = 'Not projected to pitch today'
        else:
            rec['action'] = 'TBD'
            rec['reason'] = ''

        pitcher_recs.append(rec)

    pitcher_df = pd.DataFrame(pitcher_recs)

    # Rank active pitchers
    active_p = pitcher_df[pitcher_df['action'] == 'TBD'].sort_values('daily_rank')

    pitcher_slots_count = sum(
        int(p['count']) for p in hitter_slots_config
        if p['position'] in ('SP', 'P')
    )

    for i, idx in enumerate(active_p.index):
        if i < pitcher_slots_count:
            pitcher_df.loc[idx, 'action'] = 'START'
            pitcher_df.loc[idx, 'reason'] = f'Ranked #{int(pitcher_df.loc[idx, "daily_rank"])} today'
        else:
            pitcher_df.loc[idx, 'action'] = 'SIT'
            pitcher_df.loc[idx, 'reason'] = f'Ranked #{int(pitcher_df.loc[idx, "daily_rank"])} (better options available)'

    pitcher_df = pitcher_df.sort_values(
        ['action', 'daily_rank'],
        key=lambda x: x.map({'START': 0, 'SIT': 1}) if x.name == 'action' else x,
        ascending=True
    ).reset_index(drop=True)

    return {'hitters': hitter_df, 'pitchers': pitcher_df}


def print_recommendations(recs):
    """Pretty-print start/sit recommendations."""
    hitters = recs['hitters']
    pitchers = recs['pitchers']

    print("\n" + "=" * 70)
    print("  DAILY LINEUP RECOMMENDATIONS")
    print("=" * 70)

    # Hitters
    print("\n  HITTERS")
    print("-" * 70)

    starters = hitters[hitters['action'] == 'START']
    sitters = hitters[hitters['action'] == 'SIT']

    print(f"\n  START ({len(starters)}):")
    for _, p in starters.iterrows():
        rank = int(p['daily_rank']) if p['daily_rank'] < 9999 else '?'
        proj = f"R:{p['proj_R']:.2f} HR:{p['proj_HR']:.2f} RBI:{p['proj_RBI']:.2f} SB:{p['proj_SB']:.2f} TB:{p['proj_TB']:.2f} OBP:{p['proj_OBP']:.3f}"
        print(f"    #{rank:<4} {p['name']:<25} {p['team']:<5} {p['opp']:<8} [{p['current_slot']:<4}] {proj}")

    print(f"\n  SIT ({len(sitters)}):")
    for _, p in sitters.iterrows():
        rank = int(p['daily_rank']) if p['daily_rank'] < 9999 else '-'
        print(f"    #{str(rank):<4} {p['name']:<25} {p['team']:<5} [{p['current_slot']:<4}] {p['reason']}")

    # Pitchers
    print(f"\n  PITCHERS")
    print("-" * 70)

    p_starters = pitchers[pitchers['action'] == 'START']
    p_sitters = pitchers[pitchers['action'] == 'SIT']

    print(f"\n  START ({len(p_starters)}):")
    for _, p in p_starters.iterrows():
        rank = int(p['daily_rank']) if p['daily_rank'] < 9999 else '?'
        proj = f"IP:{p['proj_IP']:.1f} K:{p['proj_K']:.1f} W:{p['proj_W']:.2f} ERA:{p['proj_ERA']:.2f} WHIP:{p['proj_WHIP']:.2f}"
        print(f"    #{rank:<4} {p['name']:<25} {p['team']:<5} {p['opp']:<8} [{p['current_slot']:<4}] {proj}")

    print(f"\n  SIT ({len(p_sitters)}):")
    for _, p in p_sitters.iterrows():
        rank = int(p['daily_rank']) if p['daily_rank'] < 9999 else '-'
        print(f"    #{str(rank):<4} {p['name']:<25} {p['team']:<5} [{p['current_slot']:<4}] {p['reason']}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    recs = generate_recommendations()
    print_recommendations(recs)
