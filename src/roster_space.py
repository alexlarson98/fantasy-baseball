"""
Team space: how many players you can actually add right now.

Yahoo keeps two separate pools. Team space is the active slots plus the bench
(23 in this league). IL slots sit outside it. That distinction is the whole
point of this module:

  - An add needs an open **team space** slot.
  - Dropping an IL player frees an **IL** slot, which buys you nothing.

So an IL player is never a valid "cost" for an add, and when team space already
has room, an add costs you nobody at all.
"""
import json
import os

# Yahoo's out-of-lineup slots -- players parked here don't consume team space.
IL_SLOTS = {'IL', 'IL+', 'IL60', 'NA', 'IR'}


def load_settings(data_dir):
    with open(os.path.join(data_dir, 'yahoo', 'league_settings.json'), encoding='utf-8') as f:
        return json.load(f)


def is_il(selected_position):
    return str(selected_position).strip() in IL_SLOTS


def team_space(roster, settings):
    """
    Report team-space usage for a roster DataFrame.

    Returns capacity / occupied / open_spots, plus the roster split into the
    players who occupy team space and the players stashed on IL.
    """
    capacity = sum(
        int(p['count']) for p in settings['roster_positions']
        if p['position'] not in IL_SLOTS
    )
    il_capacity = sum(
        int(p['count']) for p in settings['roster_positions']
        if p['position'] in IL_SLOTS
    )

    on_il = [r for _, r in roster.iterrows() if is_il(r.get('selected_position'))]
    active = [r for _, r in roster.iterrows() if not is_il(r.get('selected_position'))]

    return {
        'capacity': capacity,
        'occupied': len(active),
        'open_spots': capacity - len(active),
        'il_capacity': il_capacity,
        'il_occupied': len(on_il),
        'il_keys': {r['player_key'] for r in on_il},
        'active_keys': {r['player_key'] for r in active},
    }


def droppable_keys(roster, settings):
    """
    Player keys whose drop would actually free a team-space slot.

    IL players are excluded: cutting one frees an IL slot, not the slot an add
    needs. Yahoo would reject the resulting transaction with a full roster.
    """
    return team_space(roster, settings)['active_keys']
