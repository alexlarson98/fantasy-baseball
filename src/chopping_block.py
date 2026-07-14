"""
The chopping block: players you've pre-authorized to be dropped.

This is the safety rail for transactions. A drop is only ever proposed for a
player on this list, so a bad scrape or a name-match miss can surface a wrong
*add*, but it can never cost you a player you didn't personally condemn.

Order is priority order: the first player on the list is the first one cut.
"""
import json
import os

BLOCK_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'chopping_block.json')


def load():
    """Return the chopping block as a list of {player_key, name} dicts, in cut order."""
    if not os.path.exists(BLOCK_PATH):
        return []
    with open(BLOCK_PATH, 'r', encoding='utf-8') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def save(entries):
    with open(BLOCK_PATH, 'w', encoding='utf-8') as f:
        json.dump(entries, f, indent=2)


def is_marked(player_key):
    return any(e['player_key'] == player_key for e in load())


def toggle(player_key, name):
    """Add a player to the block, or remove them if already on it. Returns the new state."""
    entries = load()
    remaining = [e for e in entries if e['player_key'] != player_key]

    if len(remaining) < len(entries):
        save(remaining)
        return False

    entries.append({'player_key': player_key, 'name': name})
    save(entries)
    return True


def reorder(player_keys):
    """Rewrite the block in the given priority order, dropping any keys no longer present."""
    entries = {e['player_key']: e for e in load()}
    save([entries[k] for k in player_keys if k in entries])


def next_drop(eligible_keys):
    """
    The player to cut next: the highest-priority block entry that is eligible.

    `eligible_keys` must be players whose drop actually frees a team-space slot --
    see roster_space.droppable_keys. IL players are deliberately excluded there,
    so a block entry sitting on IL is skipped rather than paired with an add
    Yahoo would reject.

    Returns None if nobody on the block is eligible -- exactly the signal that no
    transaction should be offered.
    """
    eligible_keys = set(eligible_keys)
    for entry in load():
        if entry['player_key'] in eligible_keys:
            return entry
    return None


def prune(roster_keys):
    """Forget block entries for players who are no longer on the roster."""
    roster_keys = set(roster_keys)
    entries = load()
    kept = [e for e in entries if e['player_key'] in roster_keys]
    if len(kept) != len(entries):
        save(kept)
    return kept
