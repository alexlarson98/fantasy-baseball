"""
Data layer for the browsing tabs.

Everything the pull already writes to disk, joined into views you can read on a
day with no games: PitcherList rankings and streamer tiers, Statcast percentiles,
and who is actually available in your league.

Nothing here talks to Yahoo -- it's all local CSVs.
"""
import os

import pandas as pd

from src.pickup_analyzer import _normalize_name, _safe_read
from src.roster_space import is_il

# Statcast percentiles, grouped the way a scouting report reads. The scraper
# orients every metric so HIGHER IS ALWAYS BETTER, so a bar is never inverted.
HITTER_METRIC_GROUPS = [
    ('Results', [('xwOBA', 'xwOBA'), ('xBA', 'xBA'), ('xSLG', 'xSLG')]),
    ('Contact quality', [('EV', 'Exit velo'), ('Barrel', 'Barrel %'),
                         ('HardHit', 'Hard-hit %'), ('BatSpeed', 'Bat speed'),
                         ('SquaredUp', 'Squared-up %')]),
    ('Plate discipline', [('Chase', 'Chase %'), ('Whiff', 'Whiff %'),
                          ('K', 'K %'), ('BB', 'BB %')]),
]

PITCHER_METRIC_GROUPS = [
    ('Results', [('xERA', 'xERA'), ('xBA', 'xBA')]),
    ('Stuff', [('FBVelo', 'Fastball velo'), ('Extension', 'Extension')]),
    ('Missing bats', [('K', 'K %'), ('Whiff', 'Whiff %'),
                      ('Chase', 'Chase %'), ('BB', 'BB %')]),
    ('Contact suppression', [('EV', 'Exit velo against'), ('Barrel', 'Barrel % against'),
                             ('HardHit', 'Hard-hit % against')]),
]


def metric_groups(kind):
    return HITTER_METRIC_GROUPS if kind == 'hitters' else PITCHER_METRIC_GROUPS


def flat_metrics(kind):
    """[(key, label), ...] across all groups -- the column order for a leaderboard."""
    return [pair for _, group in metric_groups(kind) for pair in group]


class DayData:
    """Every local source for one date, joined by normalized player name."""

    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.roster = _safe_read(os.path.join(data_dir, 'yahoo', 'roster.csv'))
        self.free_agents = _safe_read(os.path.join(data_dir, 'yahoo', 'free_agents.csv'))
        self.savant = {
            'hitters': _safe_read(os.path.join(data_dir, 'savant', 'hitters.csv')),
            'pitchers': _safe_read(os.path.join(data_dir, 'savant', 'pitchers.csv')),
        }
        self.top_hitters = _safe_read(os.path.join(data_dir, 'pitcherlist', 'top_hitters.csv'))
        self.top_pitchers = _safe_read(os.path.join(data_dir, 'pitcherlist', 'top_pitchers.csv'))
        self.streamers = _safe_read(os.path.join(data_dir, 'pitcherlist', 'sp_streamers.csv'))
        self.waiver_adds = _safe_read(os.path.join(data_dir, 'pitcherlist', 'waiver_adds.csv'))

        self._fa = {_normalize_name(r['name']): r for _, r in self.free_agents.iterrows()}
        self._mine = {_normalize_name(r['name']): r for _, r in self.roster.iterrows()}
        self._sc = {}
        for kind, df in self.savant.items():
            for _, r in df.iterrows():
                self._sc.setdefault(_normalize_name(r['name']), (kind, r))

    def status(self, name):
        """Where a player sits relative to your team: yours, addable, or rostered elsewhere."""
        key = _normalize_name(name)
        if key in self._mine:
            row = self._mine[key]
            return 'IL' if is_il(row.get('selected_position')) else 'MINE'
        return 'FA' if key in self._fa else 'TAKEN'

    def own(self, name):
        # `a or b` on a pandas Series raises -- resolve the lookup explicitly.
        key = _normalize_name(name)
        row = self._fa.get(key)
        if row is None:
            row = self._mine.get(key)
        if row is None or pd.isna(row.get('percent_owned')):
            return None
        return int(row['percent_owned'])

    def statcast(self, name):
        """(kind, row) of Statcast percentiles for a player, or (None, None) if unqualified."""
        return self._sc.get(_normalize_name(name), (None, None))

    def score(self, name):
        _, row = self.statcast(name)
        if row is None or pd.isna(row.get('score')):
            return None
        return int(row['score'])

    def savant_id(self, name):
        _, row = self.statcast(name)
        return None if row is None else int(row['player_id'])

    def _decorate(self, row, name):
        kind, _ = self.statcast(name)
        return {
            'status': self.status(name),
            'own': self.own(name),
            'score': self.score(name),
            'savant_id': self.savant_id(name),
            'kind': kind,
        }

    def rankings(self, kind):
        """PitcherList's weekly Top 100 SP / Top 150 hitters, annotated with your league."""
        df = self.top_pitchers if kind == 'pitchers' else self.top_hitters
        if df.empty:
            return [], None
        rows = []
        for _, r in df.iterrows():
            rows.append({
                'rank': int(r['rank']),
                'name': r['name'],
                'team': r.get('team', ''),
                'tier': r.get('tier', ''),
                'change': r.get('change', ''),
                'position': r.get('position', ''),
                'badges': r.get('badges', ''),
                **self._decorate(r, r['name']),
            })
        week = int(df['week'].iloc[0]) if 'week' in df.columns and pd.notna(df['week'].iloc[0]) else None
        return rows, week

    def streamer_board(self):
        """SP streamer tiers, grouped by the day they start."""
        if self.streamers.empty:
            return []
        days = []
        for day, chunk in self.streamers.groupby('day', sort=True):
            rows = []
            for _, r in chunk.iterrows():
                rows.append({
                    'name': r['name'],
                    'tier': r.get('tier', ''),
                    'tier_score': r.get('tier_score'),
                    'opp': r.get('opp', ''),
                    'matchup': r.get('matchup', ''),
                    'rostership': r.get('rostership'),
                    **self._decorate(r, r['name']),
                })
            rows.sort(key=lambda x: (x['tier_score'] if pd.notna(x['tier_score']) else 99))
            days.append({'day': day, 'pitchers': rows})
        return days

    def priority_adds(self):
        """PitcherList's curated waiver list, annotated with league status and Statcast."""
        if self.waiver_adds.empty:
            return []
        rows = []
        for _, r in self.waiver_adds.iterrows():
            rows.append({
                'name': r['name'],
                'team': r.get('team', ''),
                'position': r.get('position', ''),
                'rostership': r.get('rostership'),
                'note': r.get('note', ''),
                **self._decorate(r, r['name']),
            })
        return rows

    def leaderboard(self, kind, limit=200):
        """Statcast leaderboard for one player type, best composite score first."""
        df = self.savant[kind]
        if df.empty:
            return []
        df = df[df['score'].notna()].sort_values('score', ascending=False).head(limit)

        metrics = [m for _, group in metric_groups(kind) for m, _ in group]
        rows = []
        for _, r in df.iterrows():
            rows.append({
                'name': r['name'],
                'team': r.get('team', ''),
                'score': int(r['score']),
                'savant_id': int(r['player_id']),
                'kind': kind,
                'status': self.status(r['name']),
                'own': self.own(r['name']),
                'metrics': {m: (int(r[m]) if m in r and pd.notna(r[m]) else None) for m in metrics},
            })
        return rows

    def available(self, limit=150):
        """Free agents in your league, best Statcast score first (unscored players last)."""
        rows = []
        for _, r in self.free_agents.iterrows():
            kind, _sc = self.statcast(r['name'])
            rows.append({
                'name': r['name'],
                'team': r.get('team', ''),
                'position': r.get('position', ''),
                'own': self.own(r['name']),
                'delta': int(r['percent_owned_delta']) if pd.notna(r.get('percent_owned_delta')) else 0,
                'score': self.score(r['name']),
                'savant_id': self.savant_id(r['name']),
                'kind': kind,
            })
        rows.sort(key=lambda x: (x['score'] is None, -(x['score'] or 0)))
        return rows[:limit]

    def player(self, kind, savant_id):
        """One player's full percentile card, or None."""
        df = self.savant[kind]
        if df.empty:
            return None
        hit = df[df['player_id'] == savant_id]
        if hit.empty:
            return None
        r = hit.iloc[0]

        groups = []
        for title, metrics in metric_groups(kind):
            entries = [
                {'key': m, 'label': label, 'value': int(r[m])}
                for m, label in metrics
                if m in r and pd.notna(r[m])
            ]
            if entries:
                groups.append({'title': title, 'metrics': entries})

        return {
            'name': r['name'],
            'team': r.get('team', ''),
            'kind': kind,
            'savant_id': savant_id,
            'score': int(r['score']) if pd.notna(r.get('score')) else None,
            'status': self.status(r['name']),
            'own': self.own(r['name']),
            'groups': groups,
            'rank': self._pl_rank(r['name'], kind),
        }

    def _pl_rank(self, name, kind):
        df = self.top_pitchers if kind == 'pitchers' else self.top_hitters
        if df.empty:
            return None
        key = _normalize_name(name)
        for _, r in df.iterrows():
            if _normalize_name(r['name']) == key:
                return int(r['rank'])
        return None
