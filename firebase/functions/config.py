"""Server configuration constants."""

ACTION_TIMEOUT_SECONDS = 60
# A seat past its deadline has the pending move made for it and a strike
# recorded; this many strikes in a row abort the game (1: abort at once).
TIMEOUT_STRIKES = 3
ELO_K_FACTOR = 32.0
INITIAL_ELO = 1500.0
GCS_REPLAY_BUCKET = "openhex-replays"
