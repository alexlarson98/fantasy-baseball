"""
Daily pickup and streaming analyzer.

Focused on short-term lineup decisions, driven by people who watch baseball:
  - PitcherList SP streamer tiers (primary pitcher streaming signal)
  - PitcherList waiver "Top Priority Players to Add" (curated pickups)
  - FantasyPros daily hitter projections (hitter streaming signal + pitcher cross-check)
  - Yahoo free-agent list + ownership % (what's actually available in YOUR league)

Surfaces:
  - Top Priority Adds cross-referenced against your league's free agents
  - Today's SP streamer board, filtered to pitchers available in your league
  - Best available hitters from FantasyPros
"""
import os
import unicodedata
import pandas as pd
from datetime import date, datetime, timedelta

from src.recommender import _probable_today, _has_game_today


def _normalize_name(name):
    """Normalize player names for fuzzy matching."""
    if not isinstance(name, str):
        return ''
    name = unicodedata.normalize('NFD', name)
    name = ''.join(c for c in name if unicodedata.category(c) != 'Mn')
    name = name.lower().strip()
    for suffix in [' jr.', ' jr', ' sr.', ' sr', ' ii', ' iii', ' iv']:
        name = name.replace(suffix, '')
    return name.strip()


def _match(name, df, name_col='name'):
    """Match a player name to a row in df (exact match only after normalization)."""
    if df is None or df.empty or name_col not in df.columns:
        return None
    norm = _normalize_name(name)
    local = df.copy()
    local['_norm'] = local[name_col].apply(_normalize_name)
    hit = local[local['_norm'] == norm]
    if not hit.empty:
        return hit.iloc[0]
    return None


def _is_hitter(position):
    """True if position indicates a hitter (not strictly pitcher)."""
    if not isinstance(position, str):
        return True
    pitcher_only = {'SP', 'RP', 'P'}
    positions = set(p.strip() for p in position.split(','))
    return not positions.issubset(pitcher_only)


def _fa_lookup(free_agents):
    """Map normalized player name -> free-agent row (who's available in your league)."""
    m = {}
    for _, p in free_agents.iterrows():
        m[_normalize_name(p['name'])] = p
    return m


def _name_map(df):
    """Map normalized name -> row for any DataFrame with a 'name' column."""
    if df is None or df.empty or 'name' not in df.columns:
        return {}
    return {_normalize_name(r['name']): r for _, r in df.iterrows()}


def _sv_score(name, sv_map):
    """Composite Statcast score (0-100, higher = better) for a player, or None if unqualified."""
    r = sv_map.get(_normalize_name(name))
    if r is None:
        return None
    v = r.get('score')
    return int(v) if pd.notna(v) else None


def _sv_fmt(score):
    """Render the composite score as a compact 'SC:72' token (blank if missing)."""
    return f"SC:{int(score)}" if score is not None and pd.notna(score) else ''


def _available_in_league(name, fa_map):
    """Return the free-agent row for `name` if it's available in your league, else None."""
    return fa_map.get(_normalize_name(name))


def _fmt_own(row):
    if row is None or pd.isna(row.get('percent_owned')):
        return ''
    delta = row.get('percent_owned_delta', 0)
    delta_str = f"+{int(delta)}" if delta and delta > 0 else (f"{int(delta)}" if delta else '')
    return f"Own:{int(row['percent_owned'])}%{delta_str}"


def analyze_pickups(data_dir=None):
    """Analyze roster vs free agents using today's PitcherList + FantasyPros data."""
    if data_dir is None:
        data_dir = os.path.join('data', date.today().isoformat())
    game_date = os.path.basename(os.path.normpath(data_dir))

    roster = pd.read_csv(os.path.join(data_dir, 'yahoo', 'roster.csv'))
    free_agents = pd.read_csv(os.path.join(data_dir, 'yahoo', 'free_agents.csv'))
    # FantasyPros publishes no daily projections on days with no games (the All-Star
    # break, for one), so an empty projection set is normal, not a failure. _match
    # returns None against an empty frame and the sections degrade on their own.
    fp_hitters = _safe_read(os.path.join(data_dir, 'projections_fpros', 'hitters.csv'))
    fp_pitchers = _safe_read(os.path.join(data_dir, 'projections_fpros', 'pitchers.csv'))
    games = _safe_read(os.path.join(data_dir, 'mlb', 'games.csv'))
    streamers = _safe_read(os.path.join(data_dir, 'pitcherlist', 'sp_streamers.csv'))
    waiver = _safe_read(os.path.join(data_dir, 'pitcherlist', 'waiver_adds.csv'))
    top_hitters = _safe_read(os.path.join(data_dir, 'pitcherlist', 'top_hitters.csv'))
    top_pitchers = _safe_read(os.path.join(data_dir, 'pitcherlist', 'top_pitchers.csv'))
    sv_hitters = _name_map(_safe_read(os.path.join(data_dir, 'savant', 'hitters.csv')))
    sv_pitchers = _name_map(_safe_read(os.path.join(data_dir, 'savant', 'pitchers.csv')))

    def day_slice(d):
        if streamers.empty or 'day' not in streamers.columns:
            return streamers
        return streamers[streamers['day'] == d]

    today_streamers = day_slice(game_date)
    tomorrow = _next_day(game_date)
    tomorrow_streamers = day_slice(tomorrow)

    fa_map = _fa_lookup(free_agents)

    hitter_week = _week_of(top_hitters)
    pitcher_week = _week_of(top_pitchers)
    sp_rank_map = ({_normalize_name(r['name']): int(r['rank']) for _, r in top_pitchers.iterrows()}
                   if not top_pitchers.empty else {})

    return {
        'tomorrow': tomorrow,
        'hitter_week': hitter_week,
        'pitcher_week': pitcher_week,
        'top_priority': _analyze_top_priority(waiver, fa_map),
        'breakout_stars': _analyze_breakout_stars(free_agents, sv_hitters, sv_pitchers),
        'streaming_pitchers': _analyze_streaming_pitchers(today_streamers, fa_map, fp_pitchers, sv_pitchers),
        # Pitchers starting TOMORROW that are still available -- grab them before rivals do.
        'streaming_pitchers_tomorrow': _analyze_streaming_pitchers(tomorrow_streamers, fa_map, fp_pitchers, sv_pitchers),
        'top_pitchers_available': _analyze_top_ranked_available(top_pitchers, fa_map, sv_pitchers),
        'top_hitters_available': _analyze_top_ranked_available(top_hitters, fa_map, sv_hitters),
        'streaming_hitters': _analyze_streaming_hitters(free_agents, fp_hitters, sv_hitters),
        'roster_hitters': _analyze_roster_hitters(roster, fp_hitters, sv_hitters),
        'roster_pitchers': _analyze_roster_pitchers(roster, today_streamers, games, sv_pitchers, sp_rank_map),
    }


def _week_of(df):
    """Extract the ranking week number from a weekly-ranking DataFrame, or None."""
    if df is None or df.empty or 'week' not in df.columns or pd.isna(df['week'].iloc[0]):
        return None
    return int(df['week'].iloc[0])


def _analyze_top_ranked_available(ranking, fa_map, sv_map=None, limit=25):
    """
    PitcherList weekly ranking (Top 150 hitters / Top 100 SP) filtered to free agents
    in your league, best rank first. Rank takes precedence; the Statcast score rides
    along as a secondary column.
    """
    sv_map = sv_map or {}
    if ranking is None or ranking.empty:
        return pd.DataFrame()
    rows = []
    for _, r in ranking.iterrows():
        fa = _available_in_league(r['name'], fa_map)
        if fa is None:
            continue
        position = r['position'] if ('position' in r and pd.notna(r.get('position'))) else ''
        rows.append({
            'rank': r['rank'],
            'name': r['name'],
            'team': r.get('team', ''),
            'position': position,
            'tier': r.get('tier'),
            'own': _fmt_own(fa),
            'sc': _sv_score(r['name'], sv_map),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values('rank').head(limit).reset_index(drop=True)


def _next_day(game_date):
    """ISO date string one day after game_date (best-effort)."""
    try:
        return (datetime.strptime(game_date, '%Y-%m-%d') + timedelta(days=1)).date().isoformat()
    except ValueError:
        return ''


def _safe_read(path):
    if os.path.exists(path):
        try:
            return pd.read_csv(path)
        except pd.errors.EmptyDataError:
            # A scraper that found nothing writes a header-less empty file.
            return pd.DataFrame()
    return pd.DataFrame()


def _fp_vbr(row):
    """FantasyPros VBR rank as an int (9999 if missing)."""
    if row is None:
        return 9999
    return int(row['VBR']) if pd.notna(row.get('VBR')) else 9999


def _analyze_top_priority(waiver, fa_map):
    """PitcherList 'Top Priority Players to Add', flagged with league availability."""
    if waiver.empty:
        return pd.DataFrame()
    rows = []
    for _, w in waiver.iterrows():
        fa = _available_in_league(w['name'], fa_map)
        rows.append({
            'name': w['name'],
            'team': w.get('team', ''),
            'position': w.get('position', ''),
            'rostership': w.get('rostership'),
            'note': w.get('note', ''),
            'available': fa is not None,
            'own': _fmt_own(fa),
        })
    df = pd.DataFrame(rows)
    # Available players first, then by lowest rostership (most gettable).
    return df.sort_values(['available', 'rostership'], ascending=[False, True]).reset_index(drop=True)


def _analyze_streaming_pitchers(streamers, fa_map, fp_pitchers, sv_pitchers=None):
    """Today's SP streamer board, filtered to arms available in your league, tier-sorted."""
    sv_pitchers = sv_pitchers or {}
    if streamers.empty:
        return pd.DataFrame()
    rows = []
    for _, s in streamers.iterrows():
        fa = _available_in_league(s['name'], fa_map)
        if fa is None:
            continue  # only surface pitchers you can actually add
        fp = _match(s['name'], fp_pitchers, name_col='Name')
        rows.append({
            'name': s['name'],
            'tier': s.get('tier', ''),
            'tier_score': s.get('tier_score', 99),
            'opp': s.get('opp', ''),
            'matchup': s.get('matchup', ''),
            'rostership': s.get('rostership'),
            'own': _fmt_own(fa),
            'fp_rank': _fp_vbr(fp),
            'fp_K': fp.get('K', 0) if fp is not None else 0,
            'fp_ERA': fp.get('ERA', 0) if fp is not None else 0,
            'sc': _sv_score(s['name'], sv_pitchers),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(['tier_score', 'rostership'], ascending=[True, True]).reset_index(drop=True)


def _analyze_streaming_hitters(free_agents, fp_hitters, sv_hitters=None):
    """Best available hitters by FantasyPros VBR (with Yahoo ownership)."""
    sv_hitters = sv_hitters or {}
    fa_hitters = free_agents[free_agents['position'].apply(_is_hitter)]
    rows = []
    for _, p in fa_hitters.iterrows():
        fp = _match(p['name'], fp_hitters, name_col='Name')
        if fp is None:
            continue  # not in today's projections -> likely not in a lineup
        rows.append({
            'name': p['name'],
            'team': p['team'],
            'position': p['position'],
            'fp_rank': _fp_vbr(fp),
            'opp': fp.get('Opp', ''),
            'R': fp.get('R', 0), 'HR': fp.get('HR', 0), 'RBI': fp.get('RBI', 0),
            'SB': fp.get('SB', 0), 'TB': fp.get('TB', 0), 'OBP': fp.get('OBP', 0),
            'own': _fmt_own(p),
            'sc': _sv_score(p['name'], sv_hitters),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values('fp_rank').head(15).reset_index(drop=True)


def _analyze_roster_hitters(roster, fp_hitters, sv_hitters=None):
    """Your hitters ranked by today's FantasyPros VBR."""
    sv_hitters = sv_hitters or {}
    roster_hitters = roster[roster['position'].apply(_is_hitter)]
    rows = []
    for _, p in roster_hitters.iterrows():
        fp = _match(p['name'], fp_hitters, name_col='Name')
        rows.append({
            'name': p['name'],
            'team': p['team'],
            'position': p['position'],
            'slot': p['selected_position'],
            'fp_rank': _fp_vbr(fp),
            'opp': fp.get('Opp', '') if fp is not None else '',
            'playing': fp is not None,
            'sc': _sv_score(p['name'], sv_hitters),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(['playing', 'fp_rank'], ascending=[False, True]).reset_index(drop=True)


def _analyze_roster_pitchers(roster, streamers, games, sv_pitchers=None, sp_rank=None):
    """Your pitchers annotated with probable-today, PitcherList Top-100 rank, and any tier."""
    sv_pitchers = sv_pitchers or {}
    sp_rank = sp_rank or {}
    roster_pitchers = roster[~roster['position'].apply(_is_hitter)]
    tier_map = {}
    if not streamers.empty:
        for _, s in streamers.iterrows():
            tier_map[_normalize_name(s['name'])] = s
    rows = []
    for _, p in roster_pitchers.iterrows():
        position = p['position'] or ''
        is_reliever_only = 'SP' not in position  # generic 'P' relievers included
        s = tier_map.get(_normalize_name(p['name']))
        if is_reliever_only:
            probable, opp = False, ''
            status = 'RP' if _has_game_today(p['team'], games) else 'no game'
        else:
            probable, opp = _probable_today(p['name'], games)
            status = 'starting' if probable else '-'
        rows.append({
            'name': p['name'],
            'team': p['team'],
            'position': position,
            'slot': p['selected_position'],
            'status': status,
            'opp': opp or (s['opp'] if s is not None else ''),
            'tier': s['tier'] if s is not None else '',
            'pl_rank': sp_rank.get(_normalize_name(p['name'])),
            'sc': _sv_score(p['name'], sv_pitchers),
        })
    return pd.DataFrame(rows)


def _analyze_breakout_stars(free_agents, sv_hitters, sv_pitchers, min_score=60):
    """
    Unrostered Statcast standouts: free agents in your league with an elite composite
    Statcast score, split into hitters and pitchers. Sorted by score; players whose
    Yahoo ownership is rising (delta > 0) are flagged as trending -- the honest
    'breaking out' signal, since Savant doesn't cheaply expose recent-window skill to
    detect an in-season surge directly.
    """
    sv_hitters = sv_hitters or {}
    sv_pitchers = sv_pitchers or {}
    rows = []
    for _, p in free_agents.iterrows():
        is_h = _is_hitter(p['position'])
        sc = _sv_score(p['name'], sv_hitters if is_h else sv_pitchers)
        if sc is None or sc < min_score:
            continue
        delta = p.get('percent_owned_delta', 0)
        rows.append({
            'kind': 'H' if is_h else 'P',
            'name': p['name'],
            'team': p['team'],
            'position': p['position'],
            'sc': sc,
            'delta': delta if pd.notna(delta) else 0,
            'own': _fmt_own(p),
            'trending': bool(pd.notna(delta) and delta and delta > 0),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values(['sc', 'delta'], ascending=[False, False]).reset_index(drop=True)


def print_analysis(results):
    """Pretty-print the pickup/streaming analysis."""
    tp = results['top_priority']
    bo = results.get('breakout_stars')
    s_p = results['streaming_pitchers']
    s_p_tmrw = results.get('streaming_pitchers_tomorrow')
    tp_sp = results.get('top_pitchers_available')
    th = results.get('top_hitters_available')
    s_h = results['streaming_hitters']
    r_h = results['roster_hitters']
    r_p = results['roster_pitchers']

    print("\n" + "=" * 100)
    print("  DAILY PICKUP & STREAMING ANALYSIS")
    print("  (PitcherList streamer tiers + waiver adds, FantasyPros, Yahoo ownership)")
    print("  SC = composite Baseball Savant Statcast score (0-100, higher = better underlying skill)")
    print("=" * 100)

    # --- Top Priority Adds (PitcherList) ---
    print("\n  TOP PRIORITY ADDS (PitcherList waiver wire)")
    print("-" * 100)
    if tp is not None and not tp.empty:
        for _, p in tp.iterrows():
            rost = f"{p['rostership']:.0f}% rost" if pd.notna(p['rostership']) else ''
            flag = 'AVAILABLE' if p['available'] else 'rostered in your league'
            print(f"  {p['name']:<22} ({p['team']}) {str(p['position']):<5} {rost:<10} "
                  f"{p['own']:<10} <-- {flag}")
    else:
        print("  No waiver 'Top Priority' data today.")

    # --- Breakout Statcast stars, unrostered ---
    print("\n  BREAKOUT STATCAST STARS (unrostered, elite underlying skill; * = ownership rising)")
    print("-" * 100)
    if bo is not None and not bo.empty:
        for kind, label in (('H', 'Hitters'), ('P', 'Pitchers')):
            sub = bo[bo['kind'] == kind].head(12)
            if sub.empty:
                continue
            print(f"   {label}:")
            for _, p in sub.iterrows():
                star = '*' if p['trending'] else ' '
                print(f"   {star}SC:{int(p['sc']):<3} {p['name']:<22} {p['team']:<4} "
                      f"{str(p['position']):<14} {p['own']}")
    else:
        print("  No unrostered players clear the Statcast score threshold today.")

    # --- Streaming Pitchers (available in your league) ---
    print("\n  STREAMING PITCHERS TODAY (PitcherList tiers, available in your league)")
    print("-" * 100)
    if s_p is not None and not s_p.empty:
        for _, p in s_p.iterrows():
            rost = f"{p['rostership']:.0f}%" if pd.notna(p['rostership']) else '-'
            fp = f"FP#{int(p['fp_rank'])}" if p['fp_rank'] < 9999 else ''
            print(f"  [{p['tier']:<18}] {p['name']:<22} {p['matchup']:<10} "
                  f"rost {rost:<5} {fp:<7} {p['own']:<10} {_sv_fmt(p.get('sc'))}")
    else:
        print("  No PitcherList streamers available in your league today.")

    # --- Streaming Pitchers TOMORROW (get ahead of rivals) ---
    if s_p_tmrw is not None and not s_p_tmrw.empty:
        tmrw = results.get('tomorrow', '')
        print(f"\n  TOMORROW'S STREAMERS TO STASH ({tmrw}, available in your league)")
        print("-" * 100)
        for _, p in s_p_tmrw.iterrows():
            rost = f"{p['rostership']:.0f}%" if pd.notna(p['rostership']) else '-'
            print(f"  [{p['tier']:<18}] {p['name']:<22} {p['matchup']:<10} "
                  f"rost {rost:<5} {p['own']:<10} {_sv_fmt(p.get('sc'))}")

    # --- Top-100 Starting Pitchers available in your league (PitcherList weekly) ---
    p_week = results.get('pitcher_week')
    p_wk_label = f"Week {p_week}" if p_week else "latest week"
    print(f"\n  BEST AVAILABLE STARTING PITCHERS (PitcherList Top 100, {p_wk_label}) -- rank first")
    print("-" * 100)
    if tp_sp is not None and not tp_sp.empty:
        for _, p in tp_sp.iterrows():
            tier = f"T{int(p['tier'])}" if pd.notna(p['tier']) else ''
            print(f"  #{int(p['rank']):<4} {tier:<4} {p['name']:<24} {p['team']:<4} "
                  f"{p['own']:<11} {_sv_fmt(p.get('sc'))}")
    else:
        print("  No PitcherList Top-100 starting pitchers available in your league.")

    # --- Top-150 Hitters available in your league (PitcherList weekly) ---
    week = results.get('hitter_week')
    wk_label = f"Week {week}" if week else "latest week"
    print(f"\n  BEST AVAILABLE HITTERS (PitcherList Top 150, {wk_label}) -- rank first")
    print("-" * 100)
    if th is not None and not th.empty:
        for _, p in th.iterrows():
            tier = f"T{int(p['tier'])}" if pd.notna(p['tier']) else ''
            print(f"  #{int(p['rank']):<4} {tier:<4} {p['name']:<24} {p['team']:<4} "
                  f"{str(p['position']):<12} {p['own']:<10} {_sv_fmt(p.get('sc'))}")
    else:
        print("  No PitcherList Top-150 hitters available in your league.")

    # --- Streaming Hitters ---
    print("\n  TOP AVAILABLE HITTERS TODAY (FantasyPros)")
    print("-" * 100)
    if s_h is not None and not s_h.empty:
        for _, p in s_h.iterrows():
            print(f"  FP#{int(p['fp_rank']):<4} {p['name']:<22} {p['team']:<4} vs {str(p['opp']):<4} "
                  f"{p['position']:<10} {p['own']:<10} {_sv_fmt(p.get('sc'))}")
    else:
        print("  No available hitters in today's projections.")

    # --- Roster snapshot ---
    print("\n  YOUR HITTERS (FantasyPros VBR today)")
    print("-" * 100)
    if r_h is not None and not r_h.empty:
        for _, p in r_h.iterrows():
            if p['playing']:
                print(f"  FP#{int(p['fp_rank']):<4} {p['name']:<22} {p['team']:<4} vs {str(p['opp']):<4} "
                      f"[{p['slot']:<4}] {_sv_fmt(p.get('sc'))}")
            else:
                print(f"  {'-- not in projections --':>26}  {p['name']:<22} {p['team']:<4} "
                      f"[{p['slot']:<4}] {_sv_fmt(p.get('sc'))}")

    print("\n  YOUR PITCHERS (probable starters + PitcherList Top-100 rank + tier)")
    print("-" * 100)
    if r_p is not None and not r_p.empty:
        for _, p in r_p.iterrows():
            opp = f"vs {p['opp']}" if p['opp'] else ''
            tier = f"[{p['tier']}]" if p['tier'] else ''
            plr = f"PL#{int(p['pl_rank'])}" if pd.notna(p.get('pl_rank')) else ''
            print(f"  {p['status']:<10} {p['name']:<22} {p['team']:<4} {opp:<8} [{p['slot']:<4}] "
                  f"{plr:<7} {tier:<20} {_sv_fmt(p.get('sc'))}")

    print("\n" + "=" * 74)


if __name__ == "__main__":
    results = analyze_pickups()
    print_analysis(results)
