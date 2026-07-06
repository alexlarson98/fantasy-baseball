"""
Daily pickup and streaming analyzer.

Focused on short-term lineup decisions. Uses:
  - FanDuel DFS daily projections (primary ranking -- fantasy points per game)
  - FantasyPros daily projections (secondary -- VBR rank + league categories)
  - Yahoo free agent ownership % (who's trending)

Surfaces:
  - Roster value based on today's projections
  - Highly-ranked available players (batters and pitchers) = streamers to pick up
"""
import os
import unicodedata
import pandas as pd
from datetime import date


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
    if df is None or df.empty:
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


def _score_hitter(name, fd_batters, fp_hitters):
    """Get daily projection data for a hitter."""
    fd = _match(name, fd_batters)
    fp = _match(name, fp_hitters, name_col='Name')

    if fd is None and fp is None:
        return None

    row = {}
    if fd is not None:
        row['fd_rank'] = int(fd.get('rank', 9999))
        row['fd_fantasy'] = fd.get('fantasy', 0)
        row['fd_opp'] = fd.get('opp', '')
        row['fd_salary'] = fd.get('salary', '')
        row['R'] = fd.get('R', 0)
        row['HR'] = fd.get('HR', 0)
        row['RBI'] = fd.get('RBI', 0)
        row['SB'] = fd.get('SB', 0)
        row['OBP'] = fd.get('OBP', 0)
    else:
        row['fd_rank'] = 9999
        row['fd_fantasy'] = 0

    if fp is not None:
        row['fp_rank'] = int(fp['VBR']) if pd.notna(fp.get('VBR')) else 9999
    else:
        row['fp_rank'] = 9999

    row['playing'] = (fd is not None) or (fp is not None)
    return row


def _score_pitcher(name, fd_pitchers, fp_pitchers):
    """Get daily projection data for a pitcher."""
    fd = _match(name, fd_pitchers)
    fp = _match(name, fp_pitchers, name_col='Name')

    if fd is None and fp is None:
        return None

    row = {}
    if fd is not None:
        row['fd_rank'] = int(fd.get('rank', 9999))
        row['fd_fantasy'] = fd.get('fantasy', 0)
        row['fd_opp'] = fd.get('opp', '')
        row['fd_salary'] = fd.get('salary', '')
        row['IP'] = fd.get('IP', 0)
        row['K'] = fd.get('K', 0)
        row['W'] = fd.get('W', 0)
        row['ERA'] = fd.get('ERA', 0)
        row['WHIP'] = fd.get('WHIP', 0)
    else:
        row['fd_rank'] = 9999
        row['fd_fantasy'] = 0

    if fp is not None:
        row['fp_rank'] = int(fp['VBR']) if pd.notna(fp.get('VBR')) else 9999
    else:
        row['fp_rank'] = 9999

    row['pitching'] = (fd is not None) or (fp is not None)
    return row


def _fmt_own(row):
    if pd.isna(row.get('owned')):
        return ''
    delta = row.get('owned_delta', 0)
    delta_str = f"+{int(delta)}" if delta and delta > 0 else (f"{int(delta)}" if delta else '')
    return f"Own:{int(row['owned'])}%{delta_str}"


def analyze_pickups(data_dir=None):
    """Analyze roster vs free agents using today's projections."""
    if data_dir is None:
        data_dir = os.path.join('data', date.today().isoformat())

    roster = pd.read_csv(os.path.join(data_dir, 'yahoo', 'roster.csv'))
    free_agents = pd.read_csv(os.path.join(data_dir, 'yahoo', 'free_agents.csv'))
    fd_batters = pd.read_csv(os.path.join(data_dir, 'projections_fanduel', 'batters.csv'))
    fd_pitchers = pd.read_csv(os.path.join(data_dir, 'projections_fanduel', 'pitchers.csv'))
    fp_hitters = pd.read_csv(os.path.join(data_dir, 'projections_fpros', 'hitters.csv'))
    fp_pitchers = pd.read_csv(os.path.join(data_dir, 'projections_fpros', 'pitchers.csv'))

    # Score roster hitters
    roster_hitters = roster[roster['position'].apply(_is_hitter)]
    r_h_rows = []
    for _, p in roster_hitters.iterrows():
        s = _score_hitter(p['name'], fd_batters, fp_hitters)
        if s is None:
            s = {'fd_rank': 9999, 'fd_fantasy': 0, 'fp_rank': 9999, 'playing': False}
        s.update({
            'name': p['name'],
            'team': p['team'],
            'position': p['position'],
            'slot': p['selected_position'],
        })
        r_h_rows.append(s)
    roster_h_df = pd.DataFrame(r_h_rows)
    if not roster_h_df.empty:
        roster_h_df = roster_h_df.sort_values(
            ['playing', 'fd_fantasy'], ascending=[False, False]
        ).reset_index(drop=True)

    # Score FA hitters
    fa_hitters = free_agents[free_agents['position'].apply(_is_hitter)]
    fa_h_rows = []
    for _, p in fa_hitters.iterrows():
        s = _score_hitter(p['name'], fd_batters, fp_hitters)
        if s is None or not s.get('playing'):
            continue
        s.update({
            'name': p['name'],
            'team': p['team'],
            'position': p['position'],
            'owned': p.get('percent_owned'),
            'owned_delta': p.get('percent_owned_delta'),
        })
        fa_h_rows.append(s)
    fa_h_df = pd.DataFrame(fa_h_rows)
    if not fa_h_df.empty:
        fa_h_df = fa_h_df.sort_values('fd_fantasy', ascending=False).reset_index(drop=True)

    # Score roster pitchers
    roster_pitchers = roster[~roster['position'].apply(_is_hitter)]
    r_p_rows = []
    for _, p in roster_pitchers.iterrows():
        s = _score_pitcher(p['name'], fd_pitchers, fp_pitchers)
        if s is None:
            s = {'fd_rank': 9999, 'fd_fantasy': 0, 'fp_rank': 9999, 'pitching': False}
        s.update({
            'name': p['name'],
            'team': p['team'],
            'position': p['position'],
            'slot': p['selected_position'],
        })
        r_p_rows.append(s)
    roster_p_df = pd.DataFrame(r_p_rows)
    if not roster_p_df.empty:
        roster_p_df = roster_p_df.sort_values(
            ['pitching', 'fd_fantasy'], ascending=[False, False]
        ).reset_index(drop=True)

    # Score FA pitchers
    fa_pitchers = free_agents[~free_agents['position'].apply(_is_hitter)]
    fa_p_rows = []
    for _, p in fa_pitchers.iterrows():
        s = _score_pitcher(p['name'], fd_pitchers, fp_pitchers)
        if s is None or not s.get('pitching'):
            continue
        s.update({
            'name': p['name'],
            'team': p['team'],
            'position': p['position'],
            'owned': p.get('percent_owned'),
            'owned_delta': p.get('percent_owned_delta'),
        })
        fa_p_rows.append(s)
    fa_p_df = pd.DataFrame(fa_p_rows)
    if not fa_p_df.empty:
        fa_p_df = fa_p_df.sort_values('fd_fantasy', ascending=False).reset_index(drop=True)

    return {
        'roster_hitters': roster_h_df,
        'roster_pitchers': roster_p_df,
        'streaming_hitters': fa_h_df.head(15) if not fa_h_df.empty else fa_h_df,
        'streaming_pitchers': fa_p_df.head(15) if not fa_p_df.empty else fa_p_df,
    }


def print_analysis(results):
    """Pretty-print the pickup/streaming analysis."""
    r_h = results['roster_hitters']
    r_p = results['roster_pitchers']
    s_h = results['streaming_hitters']
    s_p = results['streaming_pitchers']

    print("\n" + "=" * 70)
    print("  DAILY PICKUP & STREAMING ANALYSIS")
    print("  (Based on today's FanDuel DFS + FantasyPros projections)")
    print("=" * 70)

    # --- Roster Hitters ---
    print("\n  YOUR HITTERS (sorted by today's FanDuel fantasy points)")
    print("-" * 70)
    if not r_h.empty:
        for _, p in r_h.iterrows():
            slot = p.get('slot', '?')
            if p.get('playing'):
                fd = int(p['fd_rank']) if p['fd_rank'] < 9999 else '-'
                fp = int(p['fp_rank']) if p['fp_rank'] < 9999 else '-'
                print(
                    f"  FD:{p['fd_fantasy']:>5.1f}pts  FD# {fd:<4} FP# {fp:<4} "
                    f"{p['name']:<25} {p['team']:<4} vs {p.get('fd_opp', '?'):<4} [{slot:<4}]"
                )
            else:
                print(f"  {'-- NOT PLAYING --':>22}  {p['name']:<25} {p['team']:<4} [{slot:<4}]")

    # --- Streaming Hitters ---
    print(f"\n  TOP AVAILABLE HITTERS TODAY (streamer pickups)")
    print("-" * 70)
    if not s_h.empty:
        # Reference: your worst playing roster hitter
        roster_min_playing = r_h[r_h['playing']]['fd_fantasy'].min() if not r_h.empty and r_h['playing'].any() else 0
        for _, p in s_h.iterrows():
            fd = int(p['fd_rank']) if p['fd_rank'] < 9999 else '-'
            own = _fmt_own(p)
            upgrade = ' <-- upgrade' if p['fd_fantasy'] > roster_min_playing else ''
            print(
                f"  FD:{p['fd_fantasy']:>5.1f}pts  FD# {fd:<4} "
                f"{p['name']:<25} {p['team']:<4} vs {p.get('fd_opp', '?'):<4} {p['position']:<12} {own}{upgrade}"
            )
    else:
        print("  No available hitters in today's slate.")

    # --- Roster Pitchers ---
    print(f"\n  YOUR PITCHERS (sorted by today's FanDuel fantasy points)")
    print("-" * 70)
    if not r_p.empty:
        for _, p in r_p.iterrows():
            slot = p.get('slot', '?')
            if p.get('pitching'):
                fd = int(p['fd_rank']) if p['fd_rank'] < 9999 else '-'
                fp = int(p['fp_rank']) if p['fp_rank'] < 9999 else '-'
                print(
                    f"  FD:{p['fd_fantasy']:>5.1f}pts  FD# {fd:<4} FP# {fp:<4} "
                    f"{p['name']:<25} {p['team']:<4} vs {p.get('fd_opp', '?'):<4} [{slot:<4}]  "
                    f"IP:{p.get('IP', 0):.1f} K:{p.get('K', 0):.1f} ERA:{p.get('ERA', 0):.2f}"
                )
            else:
                print(f"  {'-- NOT PITCHING --':>22}  {p['name']:<25} {p['team']:<4} [{slot:<4}]")

    # --- Streaming Pitchers ---
    print(f"\n  TOP AVAILABLE PITCHERS TODAY (streamer pickups)")
    print("-" * 70)
    if not s_p.empty:
        roster_min_pitching = r_p[r_p['pitching']]['fd_fantasy'].min() if not r_p.empty and r_p['pitching'].any() else 0
        for _, p in s_p.iterrows():
            fd = int(p['fd_rank']) if p['fd_rank'] < 9999 else '-'
            own = _fmt_own(p)
            upgrade = ' <-- upgrade' if p['fd_fantasy'] > roster_min_pitching else ''
            print(
                f"  FD:{p['fd_fantasy']:>5.1f}pts  FD# {fd:<4} "
                f"{p['name']:<25} {p['team']:<4} vs {p.get('fd_opp', '?'):<4}  "
                f"IP:{p.get('IP', 0):.1f} K:{p.get('K', 0):.1f} ERA:{p.get('ERA', 0):.2f} {own}{upgrade}"
            )
    else:
        print("  No available pitchers in today's slate.")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    results = analyze_pickups()
    print_analysis(results)
