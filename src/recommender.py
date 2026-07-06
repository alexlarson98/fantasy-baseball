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


# Full MLB team name -> abbreviation (the schedule endpoint often omits abbreviations).
NAME_TO_ABBR = {
    'Arizona Diamondbacks': 'ARI', 'Athletics': 'ATH', 'Oakland Athletics': 'ATH',
    'Atlanta Braves': 'ATL', 'Baltimore Orioles': 'BAL', 'Boston Red Sox': 'BOS',
    'Chicago Cubs': 'CHC', 'Chicago White Sox': 'CWS', 'Cincinnati Reds': 'CIN',
    'Cleveland Guardians': 'CLE', 'Colorado Rockies': 'COL', 'Detroit Tigers': 'DET',
    'Houston Astros': 'HOU', 'Kansas City Royals': 'KC', 'Los Angeles Angels': 'LAA',
    'Los Angeles Dodgers': 'LAD', 'Miami Marlins': 'MIA', 'Milwaukee Brewers': 'MIL',
    'Minnesota Twins': 'MIN', 'New York Mets': 'NYM', 'New York Yankees': 'NYY',
    'Philadelphia Phillies': 'PHI', 'Pittsburgh Pirates': 'PIT', 'San Diego Padres': 'SD',
    'San Francisco Giants': 'SF', 'Seattle Mariners': 'SEA', 'St. Louis Cardinals': 'STL',
    'Tampa Bay Rays': 'TB', 'Texas Rangers': 'TEX', 'Toronto Blue Jays': 'TOR',
    'Washington Nationals': 'WSH',
}


def _team_abbr(abbr, full):
    """Resolve a team abbreviation, falling back to a name lookup (schedule abbr is often blank)."""
    if isinstance(abbr, str) and abbr and abbr.lower() != 'nan':
        return abbr
    return NAME_TO_ABBR.get(str(full), str(full) if pd.notna(full) else '')


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


def _probable_today(name, games_df):
    """
    Is this pitcher a probable starter today? Uses MLB Stats API probables.

    Returns (is_probable, opponent_abbr).
    """
    if games_df is None or games_df.empty:
        return False, ''
    norm = _normalize_name(name)
    for _, g in games_df.iterrows():
        if _normalize_name(g.get('away_pitcher', '')) == norm:
            return True, _team_abbr(g.get('home_abbr'), g.get('home_team'))
        if _normalize_name(g.get('home_pitcher', '')) == norm:
            return True, _team_abbr(g.get('away_abbr'), g.get('away_team'))
    return False, ''


def _load_savant_scores(data_dir, kind):
    """Map normalized name -> composite Statcast score for hitters/pitchers."""
    path = os.path.join(data_dir, 'savant', f'{kind}.csv')
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path)
    if df.empty or 'name' not in df.columns or 'score' not in df.columns:
        return {}
    return {
        _normalize_name(r['name']): (int(r['score']) if pd.notna(r['score']) else None)
        for _, r in df.iterrows()
    }


def _fmt_own(own, delta):
    """Format ownership like 'Own:47%+1' (blank if unknown)."""
    if own is None or (isinstance(own, float) and pd.isna(own)):
        return ''
    delta = 0 if (delta is None or (isinstance(delta, float) and pd.isna(delta))) else int(delta)
    delta_str = f"+{delta}" if delta > 0 else (str(delta) if delta < 0 else '')
    return f"Own:{int(own)}%{delta_str}"


def _load_streamer_tiers(data_dir, game_date):
    """Load today's PitcherList streamer tiers as a name -> {tier, tier_score, opp, rostership} map."""
    path = os.path.join(data_dir, 'pitcherlist', 'sp_streamers.csv')
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path)
    if df.empty or 'day' not in df.columns:
        return {}
    today = df[df['day'] == game_date]
    tiers = {}
    for _, r in today.iterrows():
        tiers[_normalize_name(r['name'])] = {
            'tier': r.get('tier'),
            'tier_score': r.get('tier_score'),
            'opp': r.get('opp', ''),
            'rostership': r.get('rostership'),
        }
    return tiers


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

    game_date = os.path.basename(os.path.normpath(data_dir))

    # Load data
    roster = pd.read_csv(os.path.join(data_dir, 'yahoo', 'roster.csv'))
    daily_hitters = pd.read_csv(os.path.join(data_dir, 'projections_fpros', 'hitters.csv'))
    daily_pitchers = pd.read_csv(os.path.join(data_dir, 'projections_fpros', 'pitchers.csv'))
    games = pd.read_csv(os.path.join(data_dir, 'mlb', 'games.csv'))
    # PitcherList tiers are the primary pitcher signal; FantasyPros is a cross-check.
    streamer_tiers = _load_streamer_tiers(data_dir, game_date)
    sv_hitter_scores = _load_savant_scores(data_dir, 'hitters')
    sv_pitcher_scores = _load_savant_scores(data_dir, 'pitchers')

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
            'sc': sv_hitter_scores.get(_normalize_name(player['name'])),
            'own': _fmt_own(player.get('percent_owned'), player.get('percent_owned_delta')),
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
            hitter_df.loc[idx, 'reason'] = f'# {int(hitter_df.loc[idx, "daily_rank"])} today'
        else:
            hitter_df.loc[idx, 'action'] = 'SIT'
            hitter_df.loc[idx, 'reason'] = f'# {int(hitter_df.loc[idx, "daily_rank"])} (better options available)'

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
            'sc': sv_pitcher_scores.get(_normalize_name(player['name'])),
            'own': _fmt_own(player.get('percent_owned'), player.get('percent_owned_delta')),
        }

        position = player['position'] or ''
        # Reliever-type = no starter eligibility. Yahoo may list a reliever as
        # generic 'P' rather than 'RP', so key off the absence of 'SP'.
        is_reliever_only = 'SP' not in position

        # FantasyPros pitcher projection -- kept as a secondary cross-check only.
        proj = _match_projection(player['name'], daily_pitchers)
        rec['fp_rank'] = (int(proj.get('VBR', 9999)) if proj is not None and pd.notna(proj.get('VBR'))
                          else 9999)
        rec['proj_K'] = proj.get('K', 0) if proj is not None else 0
        rec['proj_ERA'] = proj.get('ERA', 0) if proj is not None else 0
        rec['proj_WHIP'] = proj.get('WHIP', 0) if proj is not None else 0

        # PitcherList tier (primary signal for starters).
        tier = streamer_tiers.get(_normalize_name(player['name']))
        rec['tier'] = tier['tier'] if tier else ''
        tier_score = tier['tier_score'] if tier else None
        rec['tier_score'] = tier_score

        if is_reliever_only:
            # Relievers accrue saves/holds daily -- start them whenever the team plays.
            has_game = _has_game_today(player['team'], games)
            rec['probable'] = False
            rec['opp'] = ''
            rec['sort_key'] = -1  # keep active relievers at the top of STARTs
            if has_game:
                rec['action'] = 'START'
                rec['reason'] = 'Reliever (team plays today)'
            else:
                rec['action'] = 'SIT'
                rec['reason'] = 'No game today'
        else:
            probable, opp = _probable_today(player['name'], games)
            rec['probable'] = probable
            tier_opp = tier['opp'] if (tier and isinstance(tier.get('opp'), str)) else ''
            rec['opp'] = tier_opp or opp
            # Rostered starters (not in the streamer pool) sort ahead of streamers.
            rec['sort_key'] = 0 if tier_score is None else tier_score
            if not probable:
                rec['action'] = 'SIT'
                rec['reason'] = 'Not starting today'
            elif tier_score == 4:
                rec['action'] = 'SIT'
                rec['reason'] = 'PitcherList: Do Not Start'
            elif tier:
                rec['action'] = 'START'
                rec['reason'] = f"PitcherList: {tier['tier']}"
            else:
                rec['action'] = 'START'
                rec['reason'] = 'Rostered starter'

        pitcher_recs.append(rec)

    pitcher_df = pd.DataFrame(pitcher_recs)

    pitcher_df = pitcher_df.sort_values(
        ['action', 'sort_key', 'fp_rank'],
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
        sc = f"SC:{int(p['sc'])}" if pd.notna(p.get('sc')) else ''
        print(f"    # {rank:<4} {p['name']:<25} {p['team']:<5} {p['opp']:<8} [{p['current_slot']:<4}] {sc:<6} {p['own']}")

    print(f"\n  SIT ({len(sitters)}):")
    for _, p in sitters.iterrows():
        rank = int(p['daily_rank']) if p['daily_rank'] < 9999 else '-'
        sc = f"SC:{int(p['sc'])}" if pd.notna(p.get('sc')) else ''
        print(f"    # {str(rank):<4} {p['name']:<25} {p['team']:<5} [{p['current_slot']:<4}] {sc:<6} {p['own']:<11} {p['reason']}")

    # Pitchers
    print(f"\n  PITCHERS")
    print("-" * 70)

    p_starters = pitchers[pitchers['action'] == 'START']
    p_sitters = pitchers[pitchers['action'] == 'SIT']

    print(f"\n  START ({len(p_starters)}):")
    for _, p in p_starters.iterrows():
        tier = p.get('tier') or 'roster'
        opp = f"vs {p['opp']}" if p['opp'] else ''
        sc = f"SC:{int(p['sc'])}" if pd.notna(p.get('sc')) else ''
        print(f"    [{tier:<18}] {p['name']:<24} {p['team']:<5} {opp:<8} [{p['current_slot']:<4}] {sc:<6} {p['own']}")

    print(f"\n  SIT ({len(p_sitters)}):")
    for _, p in p_sitters.iterrows():
        sc = f"SC:{int(p['sc'])}" if pd.notna(p.get('sc')) else ''
        print(f"    {p['name']:<24} {p['team']:<5} [{p['current_slot']:<4}] {sc:<6} {p['own']:<11} {p['reason']}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    recs = generate_recommendations()
    print_recommendations(recs)
