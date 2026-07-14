"""
Desktop GUI for the fantasy baseball assistant.

Runs a small local web server and opens it in your browser. Nothing is exposed
to the network -- it binds to localhost only.

  Moves    proposed adds, each paired with the next player on your chopping
           block. Nothing reaches Yahoo until you click Approve.
  Roster   mark players for the chopping block, and set the cut order.
  Report   the same text report run_daily.py has always produced.

Launch with run_gui.bat (or: python app.py).
"""
import json
import logging
import os
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import date
from io import StringIO

import pandas as pd
from dotenv import load_dotenv
from flask import Flask, abort, jsonify, render_template, request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv()

from src import browse, chopping_block, roster_space
from src.browse import DayData
from src.pickup_analyzer import _normalize_name, analyze_pickups, print_analysis
from src.recommender import generate_recommendations, print_recommendations
from src.yahoo_client import YahooClient, YahooWriteError

# yahoo_oauth logs "TOKEN IS STILL VALID" at DEBUG on every single request, which
# buries anything worth reading in the console.
logging.getLogger('yahoo_oauth').setLevel(logging.WARNING)

app = Flask(__name__)

# Diverging ramp for Statcast percentiles: 50 is league average, so the scale has a
# real midpoint and takes two poles around a neutral gray -- blue = poor, red = elite,
# the same orientation Savant itself uses. Every step clears 3:1 on the app surface
# (validated), and the number is always printed beside the bar, so color is never the
# only channel carrying the value.
PCT_RAMP = [
    (20, '#3987e5'),   # cold blue   -- bottom of the league
    (40, '#6da7ec'),   # blue
    (60, '#898781'),   # neutral gray -- around average
    (80, '#e88b8b'),   # red
    (101, '#e66767'),  # hot red     -- elite
]


@app.template_filter('pct_color')
def pct_color(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return '#3d4552'  # unscored -- a recessive track color, not a ramp step
    for ceiling, color in PCT_RAMP:
        if value < ceiling:
            return color
    return PCT_RAMP[-1][1]

# Sections of the pickup analysis that name players you could actually add.
ADD_SECTIONS = [
    ('top_priority', 'Top priority adds'),
    ('streaming_pitchers', 'Streaming SP - today'),
    ('streaming_pitchers_tomorrow', 'Streaming SP - tomorrow'),
    ('top_pitchers_available', 'Top-ranked SP available'),
    ('top_hitters_available', 'Top-ranked hitters available'),
    ('breakout_stars', 'Breakout stars'),
    ('streaming_hitters', 'Streaming hitters'),
]

# Progress state for a pull kicked off from the GUI.
_run = {'active': False, 'log': [], 'error': None}

# The app pulls once at launch and then every hour it stays open. There is no
# external scheduler: close the window and it stops pulling.
REFRESH_INTERVAL = 60 * 60


# daily_pull creates every subfolder up front and fills them one source at a time, so
# "the folder exists" says nothing about whether it's usable yet. Only the Yahoo files
# are load-bearing: they're the roster and the league itself. FantasyPros projections
# are deliberately NOT required -- on a day with no games (the All-Star break) they're
# legitimately empty, and that must not disqualify the day.
REQUIRED_FILES = [
    os.path.join('yahoo', 'roster.csv'),
    os.path.join('yahoo', 'free_agents.csv'),
    os.path.join('yahoo', 'league_settings.json'),
]

PROJECTION_FILES = [
    os.path.join('projections_fpros', 'hitters.csv'),
    os.path.join('projections_fpros', 'pitchers.csv'),
]


def _parses(path):
    """True if the file exists and pandas can actually get columns out of it."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return False
    try:
        pd.read_csv(path, nrows=0)
        return True
    except (pd.errors.EmptyDataError, pd.errors.ParserError):
        # A 2-byte file is non-zero in size but has no header to parse.
        return False


def is_complete(data_dir):
    """True if the day has the Yahoo data the analysis can't run without."""
    return all(_parses(os.path.join(data_dir, rel)) for rel in REQUIRED_FILES)


def has_projections(data_dir):
    """False when FantasyPros published nothing -- typically because there are no games."""
    return all(_parses(os.path.join(data_dir, rel)) for rel in PROJECTION_FILES)


def latest_data_dir():
    """
    The most recent *complete* data directory.

    A pull in flight leaves today's folder half-written, so preferring it purely
    because it exists would crash the page mid-refresh. We fall back to the last
    good day and let the stale banner say so.
    """
    if not os.path.isdir('data'):
        return None
    dirs = sorted(d for d in os.listdir('data') if os.path.isdir(os.path.join('data', d)))
    for d in reversed(dirs):
        path = os.path.join('data', d)
        if is_complete(path):
            return path
    return None


def load_frames(data_dir):
    roster = pd.read_csv(os.path.join(data_dir, 'yahoo', 'roster.csv'))
    free_agents = pd.read_csv(os.path.join(data_dir, 'yahoo', 'free_agents.csv'))
    return roster, free_agents


def key_lookup(df):
    """Map normalized name -> player_key, using the analyzer's own name normalization."""
    return {_normalize_name(r['name']): r['player_key'] for _, r in df.iterrows()}


def build_moves(data_dir):
    """
    Pair each addable player with the player they'd cost you -- if anyone.

    The add side comes from the analysis. The drop side is always the top of the
    chopping block, never a player the model picked on its own -- and only when
    team space is actually full. With an open bench slot the add is free, and an
    IL player is never the answer, since cutting one frees an IL slot rather than
    the team-space slot the add needs.
    """
    roster, free_agents = load_frames(data_dir)
    fa_keys = key_lookup(free_agents)

    settings = roster_space.load_settings(data_dir)
    space = roster_space.team_space(roster, settings)

    chopping_block.prune(roster['player_key'].tolist())

    # Only a team-space player can pay for an add; IL entries on the block are skipped.
    needs_drop = space['open_spots'] <= 0
    drop = chopping_block.next_drop(space['active_keys']) if needs_drop else None

    analysis = analyze_pickups(data_dir)
    day = DayData(data_dir)

    # Sections stay separate, in the order ADD_SECTIONS declares. Flattening them into
    # one list throws away the thing that makes a suggestion worth trusting: a curated
    # top-priority add and the 25th name on a ranking are not the same recommendation.
    sections, seen = [], set()

    for section_key, label in ADD_SECTIONS:
        section = analysis.get(section_key)
        if section is None or not isinstance(section, pd.DataFrame) or section.empty:
            continue

        rows = section[section['available']] if 'available' in section.columns else section
        moves = []

        for _, row in rows.iterrows():
            name = row['name']
            player_key = fa_keys.get(_normalize_name(name))
            # No key means we couldn't match the scraped name to a Yahoo free
            # agent -- showing an Approve button we can't honor would be a lie.
            if not player_key or player_key in seen:
                continue
            seen.add(player_key)

            moves.append({
                'source': label,
                'name': name,
                'player_key': player_key,
                'team': row.get('team', ''),
                'position': row.get('position', ''),
                'note': str(row.get('note', '') or ''),
                'tier': row.get('tier', ''),
                'opp': row.get('opp', ''),
                'rostership': row.get('rostership'),
                'status': day.status(name),
                # Statcast travels with the row so a move can be judged on skill,
                # not just on whoever happened to top a scraped list.
                'score': day.score(name),
                'savant_id': day.savant_id(name),
                'kind': day.statcast(name)[0],
                'drop_name': drop['name'] if drop else None,
                'drop_key': drop['player_key'] if drop else None,
                # Free when there's a bench slot; otherwise it needs a body on the block.
                'approvable': (not needs_drop) or (drop is not None),
            })

        if moves:
            sections.append({'key': section_key, 'label': label, 'moves': moves})

    return sections, drop, roster, space


@app.route('/')
def index():
    data_dir = latest_data_dir()
    if not data_dir:
        # No complete day on disk yet -- the launch pull is probably still running.
        return render_template('index.html', empty=True, running=_run['active'],
                               load_error=_run['error'])

    try:
        move_sections, drop, roster, space = build_moves(data_dir)
    except Exception as e:
        # Never 500 the whole app over one unreadable day's data.
        return render_template('index.html', empty=True, running=_run['active'],
                               load_error=f'Could not read {data_dir} -- {type(e).__name__}: {e}')

    marked = {e['player_key'] for e in chopping_block.load()}

    # Everything below survives a day with no games -- rankings, tiers and Statcast
    # are season-to-date, so they stay worth reading when start/sit has nothing to say.
    day = DayData(data_dir)
    sp_ranks, sp_week = day.rankings('pitchers')
    hit_ranks, hit_week = day.rankings('hitters')

    roster_rows = roster.to_dict('records')
    for r in roster_rows:
        r['marked'] = r['player_key'] in marked
        r['il'] = roster_space.is_il(r.get('selected_position'))
        r['score'] = day.score(r['name'])
        r['savant_id'] = day.savant_id(r['name'])
        r['kind'] = day.statcast(r['name'])[0]

    # Flag block entries that can't pay for an add, so the roster tab explains itself.
    block_order = [
        {**e, 'il': e['player_key'] in space['il_keys']}
        for e in chopping_block.load()
    ]

    return render_template(
        'index.html',
        empty=False,
        data_dir=data_dir,
        stale=data_dir != os.path.join('data', date.today().isoformat()),
        running=_run['active'],
        no_projections=not has_projections(data_dir),
        move_sections=move_sections,
        drop=drop,
        space=space,
        roster=roster_rows,
        block_order=block_order,
        report=read_report(data_dir),
        sp_ranks=sp_ranks, sp_week=sp_week,
        hit_ranks=hit_ranks, hit_week=hit_week,
        streamer_days=day.streamer_board(),
        waiver_adds=day.priority_adds(),
        available=day.available(),
        sc_hitters=day.leaderboard('hitters'),
        sc_pitchers=day.leaderboard('pitchers'),
        hitter_cols=browse.flat_metrics('hitters'),
        pitcher_cols=browse.flat_metrics('pitchers'),
    )


@app.route('/player/<kind>/<int:savant_id>')
def player(kind, savant_id):
    """One player's Statcast card -- every percentile we have, as bars."""
    if kind not in ('hitters', 'pitchers'):
        abort(404)
    data_dir = latest_data_dir()
    if not data_dir:
        abort(404)

    card = DayData(data_dir).player(kind, savant_id)
    if card is None:
        abort(404)
    return render_template('player.html', p=card, data_dir=data_dir)


def read_report(data_dir):
    path = os.path.join(data_dir, 'report.txt')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    return '(no report yet -- click Refresh data)'


@app.route('/chopping-block/toggle', methods=['POST'])
def toggle_block():
    player_key = request.form['player_key']
    name = request.form['name']
    marked = chopping_block.toggle(player_key, name)
    return jsonify({'marked': marked})


@app.route('/chopping-block/reorder', methods=['POST'])
def reorder_block():
    chopping_block.reorder(request.json['player_keys'])
    return jsonify({'ok': True})


@app.route('/transaction', methods=['POST'])
def transaction():
    """Execute an approved add/drop. This is the only path that writes to Yahoo."""
    add_key = request.form['add_key']
    drop_key = request.form.get('drop_key') or None
    faab = request.form.get('faab_bid') or None

    league_id = os.getenv('YAHOO_LEAGUE_ID')
    try:
        result = YahooClient().execute_transaction(
            league_id,
            add_player_key=add_key,
            drop_player_key=drop_key,
            faab_bid=int(faab) if faab else None,
        )
    except YahooWriteError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'ok': False, 'error': f'{type(e).__name__}: {e}'}), 500

    if drop_key:
        chopping_block.toggle(drop_key, '')  # cut player leaves the block

    log_transaction(result, request.form.get('add_name'), request.form.get('drop_name'))
    return jsonify({'ok': True, **result})


def log_transaction(result, add_name, drop_name):
    with open('transactions.log', 'a', encoding='utf-8') as f:
        status = 'PENDING WAIVER' if result['pending'] else 'DONE'
        bid = f" (${result['faab_bid']})" if result['faab_bid'] is not None else ''
        f.write(f"{date.today().isoformat()}  {status}  "
                f"ADD {add_name}{bid}  DROP {drop_name or '-'}\n")


@app.route('/run', methods=['POST'])
def run_pull():
    """Kick off the daily pull in the background so the page stays responsive."""
    if _run['active']:
        return jsonify({'ok': False, 'error': 'already running'})

    threading.Thread(target=_do_pull, daemon=True).start()
    return jsonify({'ok': True})


def _do_pull():
    """Run the full daily pull and rewrite today's report. Never runs twice at once."""
    if _run['active']:
        return
    _run.update(active=True, log=[], error=None)

    from daily_pull import pull_daily_data

    class Tee(StringIO):
        def write(self, s):
            if s.strip():
                _run['log'].append(s.rstrip())
            return len(s)

    old_stdout = sys.stdout
    sys.stdout = Tee()
    try:
        pull_daily_data()

        data_dir = os.path.join('data', date.today().isoformat())
        recs = generate_recommendations(data_dir)
        pickups = analyze_pickups(data_dir)

        buf = StringIO()
        sys.stdout = buf
        print(f"FANTASY BASEBALL DAILY REPORT - {date.today().isoformat()}")
        print("=" * 60 + "\n")
        print_recommendations(recs)
        print()
        print_analysis(pickups)
        sys.stdout = old_stdout

        with open(os.path.join(data_dir, 'report.txt'), 'w', encoding='utf-8') as f:
            f.write(buf.getvalue())
    except Exception as e:
        _run['error'] = f'{type(e).__name__}: {e}'
    finally:
        sys.stdout = old_stdout
        _run['active'] = False


@app.route('/run/status')
def run_status():
    return jsonify({'active': _run['active'], 'log': _run['log'][-40:], 'error': _run['error']})


@app.route('/health')
def health():
    """Lets a second launch detect that this one is already serving. See __main__."""
    return jsonify({'app': 'fantasy-baseball', 'pid': os.getpid()})


@app.route('/shutdown', methods=['POST'])
def shutdown():
    """Stop the server from the UI, so quitting doesn't mean hunting for a console window."""
    def die():
        time.sleep(0.3)          # let this response reach the browser first
        os._exit(0)              # the dev server has no clean stop; daemon threads make this safe
    threading.Thread(target=die, daemon=True).start()
    return jsonify({'ok': True})


def _scheduler():
    """
    Keep the data fresh without wasting a pull.

    At launch we only pull if today's data is missing -- if you already ran one,
    the data is on disk and re-fetching it buys nothing. After that it refreshes
    hourly, and the Refresh button forces one at any time.
    """
    today = os.path.join('data', date.today().isoformat())
    if is_complete(today):
        print(f'  {today} already pulled -- skipping launch pull. '
              f'Next refresh in {REFRESH_INTERVAL // 60} min (or hit Refresh data).')
    else:
        _do_pull()

    while True:
        time.sleep(REFRESH_INTERVAL)
        _do_pull()


HOST, PORT = '127.0.0.1', 5000
URL = f'http://{HOST}:{PORT}'


def already_serving():
    """
    True if another copy of this app is already on the port.

    Werkzeug sets SO_REUSEADDR, and on Windows that lets a second process bind a
    port the first one is already listening on -- so relaunching stacks servers
    instead of failing, and requests land on whichever one Windows feels like.
    That's how you end up staring at a page served by stale code. Ask the port who
    it is before starting, rather than trusting bind() to refuse.
    """
    try:
        with urllib.request.urlopen(f'{URL}/health', timeout=1) as r:
            return json.load(r).get('app') == 'fantasy-baseball'
    except Exception:
        return False


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    if already_serving():
        print(f'  Fantasy Baseball is already running at {URL} -- opening that one.')
        print('  (Quit it from the header button, or run stop_gui.bat, then relaunch.)')
        webbrowser.open(URL)
        sys.exit(0)

    threading.Thread(target=_scheduler, daemon=True).start()
    threading.Timer(1.0, lambda: webbrowser.open(URL)).start()
    app.run(host=HOST, port=PORT, debug=False)
