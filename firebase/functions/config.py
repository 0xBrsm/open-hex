"""Server configuration constants."""

# The ladder's modes and the seats in each game. A bot registers for one mode;
# its record in mode m is the bot document's games{m} and wins{m} fields, and
# its rating elo{m}, eloSe{m} and rank{m}, refitted every 15 minutes
# (services/rankings.py). 3p plays 4p's rules with one seat fewer.
MODES = {"1v1": 2, "3p": 3, "4p": 4}
ACTION_TIMEOUT_SECONDS = 60
# A seat past its deadline has the pending move made for it and a strike
# recorded; this many strikes in a row abort the game (1: abort at once).
TIMEOUT_STRIKES = 3
RENAME_COOLDOWN_SECONDS = 24 * 60 * 60   # a bot may change its name once a day
INITIAL_ELO = 1500.0
# Only bots that played within this many days are rated and ranked (app.js
# ACTIVE_DAYS hides the others from the leaderboard)
ACTIVE_DAYS = 30
GCS_REPLAY_BUCKET = "openhex-replays"
