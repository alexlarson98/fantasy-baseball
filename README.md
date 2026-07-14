# Fantasy Baseball Lineup Optimizer

A daily tool that scrapes data from multiple sources to help you make short-term lineup decisions (start/sit, daily streamers, pickups) for your Yahoo Fantasy Baseball league.

## What It Does

Run `python daily_pull.py` each morning to pull fresh data, then:

- `python -m src.recommender` -- today's start/sit recommendations
- `python -m src.pickup_analyzer` -- today's best available streamers (hitters and pitchers)

Or just double-click the **Fantasy Baseball** desktop shortcut, which opens the GUI and pulls fresh data on its own (see [Desktop App](#desktop-app-apppy)).

## Data Sources

### Yahoo Fantasy API (`src/yahoo_client.py`)

**Used for:** Your league-specific data -- roster, free agents, season stats, ownership %, league settings.

- **Roster** -- your current team with player positions and roster slots
- **Free agents** -- top 500 available players in your league (with ownership % and weekly delta)
- **Season stats** -- cumulative stats for your roster and free agents in your league categories
- **League settings** -- scoring categories, roster positions, number of teams

**Auth:** Requires a Yahoo Developer app (OAuth2). Credentials stored in `oauth_token.json` (auto-refreshes after initial browser authorization).

### PitcherList (`src/pitcherlist_scraper.py`)

**Used for:** Daily pitcher and pickup calls from people who actually watch baseball -- the **primary pitcher signal**. No auth required.

- **SP Streamer Ranks** -- starting pitchers bucketed by day into tiers: `Auto-Start`, `Probably Start`, `Questionable Start`, `Do Not Start`, with opponent and rostership %. Source: [SP Streamers column](https://pitcherlist.com/category/fantasy/starting-pitchers/sp-streamers/).
- **Waiver Wire "Top Priority Players to Add"** -- a short curated pickup list (hitters and pitchers) with team, position, rostership %, and scouting notes. Source: [Waiver Wire column](https://pitcherlist.com/category/fantasy/waiver-wire/).
- **Top 150 Hitters** -- the weekly rest-of-season hitter value ranking (rank, team, position, tier). Source: [Hitter List column](https://pitcherlist.com/category/fantasy/hitters-fantasy/hitter-list/).
- **Top 100 Starting Pitchers** -- the weekly rest-of-season SP value ranking (rank, team, tier, badges). Source: [The List column](https://pitcherlist.com/category/fantasy/starting-pitchers/the-list/).

The daily post URLs are date-based and change every day, so the scraper hits the stable **category page** for each column and grabs the newest post.

**Free-tier note:** the third day of streamer ranks (and a couple of grids) are gated behind PL Pro. We parse whatever is publicly visible (usually the first ~2 days) and degrade gracefully if the layout shifts.

**Used by:** The recommender (pitcher start/sit driven by today's tier + MLB probable starters) and the pickup analyzer ("Top Priority Adds" cross-referenced against your league's free agents, plus the streamer board filtered to pitchers you can actually add).

### FantasyPros (`src/fantasypros_scraper.py`)

**Used for:** Daily stat projections with VBR (Value-Based Ranking). The **primary hitter signal** and a **secondary pitcher cross-check**.

- **Hitter projections** (~2,400 players) -- AB, R, HR, RBI, SB, AVG, OBP, H, 2B, 3B, BB, SO, plus computed TB
- **Pitcher projections** (~270 players) -- IP, K, W, SV, ERA, WHIP, ER, H, BB, HR

**Auth:** Requires a FantasyPros account (free tier only returns 10 rows). Session cookie stored in `.env` as `FANTASYPROS_COOKIE`. The scraper throws an error if the cookie expires -- refresh by logging into FantasyPros in your browser, running `document.cookie` in DevTools console, and updating the `.env` value. The `run_daily.py` script will prompt you to paste a new cookie on failure.

**Used by:** The recommender as the ranking for hitter start/sit, and shown as a secondary `FP#`/stat-line column next to the PitcherList tier for pitchers.

### Baseball Savant (`src/savant_scraper.py`)

**Used for:** Underlying Statcast skill -- the percentile rankings from a player's Savant page (higher percentile = better in Savant's orientation). No auth required.

- **Hitter percentiles** (~250 qualified) -- xwOBA, xBA, xSLG, exit velo, barrel %, hard-hit %, bat speed, squared-up %, chase %, whiff %, K %, BB %
- **Pitcher percentiles** (~300 qualified) -- xERA, xBA, fastball velo, exit velo, chase %, whiff %, K %, BB %, barrel %, hard-hit %, extension

Pulled from Savant's bulk `percentile-rankings` leaderboard (one request each for batters/pitchers), matched to your players by name. Run values and raw decimal values are intentionally omitted -- the percentiles are the comparative-strength signal.

**Composite score:** the percentiles are collapsed into a single **`SC` score (0-100, higher = better)** via a cluster-weighted average (see `HITTER_WEIGHTS` / `PITCHER_WEIGHTS` in `src/savant_scraper.py`). Weights are assigned per correlated cluster so a family of collinear metrics can't triple-count: for hitters, xwOBA anchors but is capped since it already absorbs xBA/xSLG, the contact-quality cluster (barrel/hard-hit/EV) is the most power-predictive, and plate discipline holds ~a third; for pitchers, strikeout/whiff ability (the most stable pitcher skill) is weighted highest and contact-suppression-against is kept modest. Missing metrics use available-case renormalization (never imputed), and a player needs the anchor(s) plus >=50% of the weight present or the score is left blank.

**Used by:** The pickup analyzer, which shows the `SC:NN` score for every player in the add/play/drop lists, plus a dedicated **"Breakout Statcast Stars"** section -- unrostered free agents with an elite composite score (hitters and pitchers split), flagged `*` when Yahoo ownership is rising (the honest "breaking out" proxy, since Savant doesn't cheaply expose recent-window skill).

### MLB Stats API (`src/mlb_client.py`)

**Used for:** Today's game schedule, probable pitchers, and confirmed lineups.

- **Games** -- all MLB games today with status, venue, and probable pitchers
- **Lineups** -- confirmed batting orders (usually available 1-3 hours before game time)

**Auth:** None required. Free public API.

**Used by:** The recommender to determine if a player's team is playing today (SIT if no game) and to confirm starting pitchers.

## Scripts

### `src/recommender.py` -- Start/Sit

Produces start/sit for your roster:

- **Hitters** -- FantasyPros VBR (best-ranked hitters fill your active slots) + MLB "team plays today" check. Each line shows the player's composite Statcast `SC` score and Yahoo ownership rather than raw per-game projections.
- **Pitchers** -- MLB probable starters + PitcherList tier: `Auto/Probably/Questionable Start` = START, `Do Not Start` = SIT, and rostered starters not in the streamer pool default to START when they're a probable today. Relievers (no `SP` eligibility) START whenever their team plays, since they accrue saves/holds daily. Non-probable starters SIT.

### `src/pickup_analyzer.py` -- Pickups & Streamers

Combines PitcherList + FantasyPros + Yahoo ownership to surface:

- **Top Priority Adds** -- PitcherList's curated waiver list, flagged with whether each player is actually a free agent in *your* league (+ Yahoo ownership).
- **Streaming pitchers today** -- today's PitcherList streamer board, filtered to arms available in your league and sorted by tier, with FantasyPros as a secondary column.
- **Tomorrow's streamers to stash** -- next-day streamer tiers (when publicly visible), filtered to available arms, so you can add them before rivals.
- **Best available starting pitchers (PitcherList Top 100)** -- the weekly SP ranking filtered to free agents in your league, best rank first (rank takes precedence; Statcast score is a secondary column).
- **Best available hitters (PitcherList Top 150)** -- the weekly hitter ranking filtered to free agents in your league, best rank first.
- **Top available hitters (FantasyPros)** -- best available batters by today's FantasyPros VBR.

Every player line carries a single `SC:NN` composite Baseball Savant Statcast score (0-100, higher = better) so you can weigh underlying skill alongside rank/ownership, and a **Breakout Statcast Stars** section surfaces unrostered players whose skill the field hasn't caught up to yet.
- **Roster snapshot** -- your hitters by FantasyPros VBR; your pitchers annotated with probable-today and any PitcherList tier.

## Setup

### 1. Prerequisites

- Python 3.11+
- A Yahoo Fantasy Baseball account with an active league

### 2. Install dependencies

```bash
python -m venv venv
.\venv\Scripts\Activate.ps1   # Windows PowerShell
pip install -r requirements.txt
```

### 3. Yahoo Developer App

1. Go to `developer.yahoo.com` and create a new app
2. Set **Application Type** to "Installed Application"
3. Set **OAuth Client Type** to "Confidential Client"
4. Enable **Fantasy Sports** (Read) under API Permissions
5. Set **Redirect URI** to `https://localhost`
6. Create `oauth_token.json` with your credentials:

```json
{
    "consumer_key": "your_client_id",
    "consumer_secret": "your_client_secret"
}
```

### 4. Environment variables

Copy `.env.example` to `.env` and fill in:

```
YAHOO_CLIENT_ID=your_client_id
YAHOO_CLIENT_SECRET=your_client_secret
YAHOO_LEAGUE_ID=469.l.XXXXX
FANTASYPROS_COOKIE=sessionid=...; fp_level=...; fp_userdata=...; fptoken=...
```

### 5. First run

```bash
python daily_pull.py         # Pull all data (opens browser for Yahoo OAuth on first run)
python -m src.recommender    # Get today's start/sit
python -m src.pickup_analyzer # Get today's streamers/pickups
```

Or just run `python run_daily.py` / double-click `FantasyBaseball.bat` to do all three.

## Desktop App (`app.py`)

Double-click the **Fantasy Baseball** shortcut on the desktop (or run `run_gui.bat`).
It starts a local server on `127.0.0.1:5000` -- bound to localhost, not the network --
and opens your browser.

- **Moves** -- every add the analyzer surfaced that maps to a real Yahoo free agent,
  each paired with the player it would cost you. Approve files it with Yahoo.
- **Rankings** -- PitcherList's weekly Top 100 SP and Top 150 hitters, with tier, weekly
  movement, Statcast score, and whether the player is available in *your* league.
- **Streamers** -- the SP streamer board by start date, plus PitcherList's top priority adds.
- **Statcast** -- the full percentile leaderboard for hitters and pitchers, sortable on
  every metric.
- **Available** -- free agents in your league, best Statcast score first.
- **Roster** -- tick players onto the chopping block and set the cut order.
- **Report** -- the same text report `run_daily.py` has always written.

Every table sorts by clicking a header, and every player name links to their **Statcast
card** (`/player/<hitters|pitchers>/<savant_id>`) -- a percentile bar for each metric,
grouped the way a scouting report reads. Tabs are deep-linkable: `/#statcast`.

Only **Rankings**, **Statcast** and **Available** matter on a day with no games -- they're
season-to-date, so they stay worth reading when start/sit has nothing to say.

### Reading the percentile bars

Every Statcast metric is oriented so **higher is always better** (a low chase rate shows as
a high percentile), so a bar is never inverted. The scale is diverging around the league
average: **blue = poor, gray = average, red = elite** -- the same orientation Savant uses.
The number is always printed next to the bar, so the value never depends on color alone.
Ramp steps live in `PCT_RAMP` in `app.py` and are validated to clear 3:1 contrast on the
app's surface.

### When data is pulled

On launch the app pulls **only if today's data isn't already on disk** -- if you just ran
a pull, it uses what's there rather than re-fetching. After that it refreshes **once an
hour for as long as it stays open**, and **Refresh data** forces a pull at any time.

Close the window and it stops pulling. There is no Windows scheduled task any more -- the
old 9am `FantasyBaseballDaily` trigger has been removed. Interval lives in
`REFRESH_INTERVAL` in `app.py`.

### Only one server, ever

Werkzeug sets `SO_REUSEADDR`, and **on Windows that lets a second process bind a port the
first one is already listening on** -- so relaunching used to stack servers silently rather
than failing with "port in use", and requests landed on whichever one Windows picked. That
is how you end up looking at a page rendered by stale code.

So the app asks the port who it is (`GET /health`) before starting:

- Already running? It opens the browser at the existing instance and exits.
- **Quit** in the header stops the server cleanly.
- `stop_gui.bat` kills anything orphaned on port 5000 (window closed uncleanly, machine slept).

Code changes need a restart -- the server runs with `debug=False`, so it does not reload.

### Days with no games

On the All-Star break and off-days FantasyPros publishes **no daily projections** -- the
page returns a valid table with zero rows. That is a legitimately empty result, not a
failure:

- Projection files are **not** required for a day to count as complete.
- The recommender and pickup analyzer fall back to their other signals (PitcherList tiers,
  weekly rankings, Statcast, Yahoo ownership) instead of crashing.
- The Moves tab says plainly that projections are unavailable, and why.

Don't mistake this for an expired FantasyPros cookie. A stale cookie returns **10 rows**
(the free-tier cap) and raises a loud error; no games returns **zero** rows.

### IL and team space

Yahoo keeps two separate pools, and `src/roster_space.py` models the difference:

- **Team space** -- active slots + bench (23 in this league). An add needs an open slot here.
- **IL slots** -- 4 here, sitting *outside* team space.

The consequence that drives the UI: **dropping an IL player frees an IL slot, not the slot
an add needs.** So an IL player is never offered as the cost of an add -- if the top of your
chopping block is on the IL, it's skipped for the first healthy player below them. And when
team space has an open spot, an add is proposed with **no drop at all**.

### The chopping block

`chopping_block.json` is the list of players you have pre-authorized to be dropped, in cut
order. It is the safety rail on every transaction: a drop is *only* ever proposed for
someone on this list. If a drop is needed and nobody on the list can supply one, no add can
be approved -- the buttons stay disabled. A bad scrape or a botched name match can therefore
surface a wrong *add*, but it can never cost you a player you didn't personally condemn.

Every executed transaction is appended to `transactions.log`.

### Write access

Transactions need a Read/**Write** Yahoo token; the read-only one you started with will be
rejected. Yahoo grants this at the app level, so refreshing the token is not enough:

1. https://developer.yahoo.com/apps/ -> your app -> API Permissions -> Fantasy Sports -> **Read/Write**
2. `python reauth.py` (backs up the old token, runs the browser consent flow)

Adds route themselves: a player on waivers is filed as a waiver claim (pending until it
clears overnight), anyone else as an instant free-agent add.

## Data Output Structure

```
data/{YYYY-MM-DD}/
  yahoo/
    league_settings.json    # Scoring categories, roster positions
    roster.csv              # Your team
    free_agents.csv         # Top 500 available players (with ownership %)
    roster_stats.csv        # Your players' season stats
    fa_stats.csv            # Free agent season stats
  pitcherlist/
    sp_streamers.csv        # SP streamer tiers by day (Auto/Probably/Questionable/Do Not Start)
    waiver_adds.csv         # "Top Priority Players to Add"
    top_hitters.csv         # Weekly Top 150 hitters (rank, team, position, tier)
    top_pitchers.csv        # Weekly Top 100 starting pitchers (rank, team, tier, badges)
  projections_fpros/
    hitters.csv             # FantasyPros daily hitter projections
    pitchers.csv            # FantasyPros daily pitcher projections (pitcher cross-check)
  savant/
    hitters.csv             # Statcast percentiles + composite score (hitters)
    pitchers.csv            # Statcast percentiles + composite score (pitchers)
  mlb/
    games.csv               # Today's MLB schedule + probable pitchers
    lineups.csv             # Confirmed batting orders
  report.txt                # Full start/sit + pickups combined (run_daily.py)
```

## League Scoring Categories

| Hitters | Pitchers |
|---|---|
| Runs (R) | Wins (W) |
| Home Runs (HR) | Strikeouts (K) |
| RBI | ERA |
| Stolen Bases (SB) | WHIP |
| Total Bases (TB) | Quality Starts (QS) |
| On-base Percentage (OBP) | Saves + Holds (SV+H) |
