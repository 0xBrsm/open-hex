# OpenHex

OpenHex is an open standard for hex-based trading and building games, with a reference engine in Python and a hosted ladder where bots play each other.

**Live site: <https://open-hex.web.app>** has the leaderboards, recent games and the bot guide.

## What's here

| Path | Contents |
|------|----------|
| [`SPEC.md`](SPEC.md) | The specification: JSON formats for game configuration, state, actions and events, hidden information, the 1v1 variant and the game log format |
| [`firebase/functions/openhex/`](firebase/functions/openhex/) | Reference engine (standard library only) and [`schema.json`](firebase/functions/openhex/schema.json), the JSON Schema for the spec |
| [`firebase/`](firebase/) | Ladder server: Cloud Functions for the bot REST API and matchmaking, Firestore rules, and the website |
| [`bots/`](bots/) | Example clients: `local_runner.py` plays games on your machine, `daemon.py` plays on the ladder |
| [`examples/`](examples/) | A sample game state and a sample 1v1 game log |

## Play on the hosted ladder

The ladder runs at <https://open-hex.web.app>. Its Getting Started guide covers every endpoint, the coordinate formats and the full game loop.

Bots can trade resources with each other, most usefully in 3- and 4-player games, following colonist.io's trading rules (offers, counter-offers, revisable answers); see Trading in the guide and [`SPEC.md`](SPEC.md) section 7.

Register a bot once and keep the API key it returns:

```bash
curl -X POST https://open-hex.web.app/api/bots \
  -H "Content-Type: application/json" \
  -d '{"name": "my_bot", "mode": "1v1"}'
```

To rename the bot or change its description later, keeping its rating and record, send a `PATCH` to `/api/bots/me` with the key (`-X PATCH -H "Authorization: Bearer <your-api-key>" -d '{"name": "my_bot_v2"}'`). A bot can be renamed once every 24 hours.

The daemon expects the default `"coordinateFormat": "coordinates"`, so leave that field out or set it to `"coordinates"`.

Then run the example daemon, which joins the 1v1 queue and plays every game it is matched into:

```bash
cd bots
python3 -m venv .venv
.venv/bin/pip install requests
.venv/bin/python daemon.py --api-key <your-api-key> --name my_bot
```

The daemon plays with `RandomBot` from `local_runner.py`. To play with your own bot, pass `BotClient` any object with a `name` and a `pick_action(state, my_color) -> dict` method. The daemon waits 20 minutes between games; change the pause in `daemon_loop`.

## Run games locally

`local_runner.py` plays games directly through the reference engine, with no network calls:

```bash
cd bots
python3 local_runner.py --red random --blue random --games 5
```

`--red` and `--blue` take `random` or `canopy:URL`, which talks to the OpenHex server in [canopy](https://github.com/cullback/canopy).

## How bots are ranked

The leaderboards use Bradley-Terry ratings, refitted every 15 minutes by a
scheduled function (`firebase/functions/services/rankings.py`). Since
15 November 2026 they replace the per-game Elo the ladder used before, which
moved a fixed 32 points per game and so tracked a bot's last few dozen games
and the order they came in. The site explains it for bot authors at
[How Ratings Work](https://open-hex.web.app/#/ratings).

- **The model.** A bot's rating is the one at which its expected wins equal
  its actual wins against the bots it has played, each game weighted the
  same, on Elo's scale: in 1v1, A beats B with probability
  1 / (1 + 10^((R_B - R_A) / 400)). In 3p and 4p only the winner counts: a
  seat wins the table in proportion to 10^(R / 400) (Luce's choice model; with
  two seats it is exactly Bradley-Terry).
- **Which games count.** Only bots that played in the last 30 days are rated,
  from all their games against each other. A 1v1 game against an idle bot is
  left out; a 3p or 4p game an active bot won stays in, as a game among the
  table's active bots. Games aborted for timeouts count, won by the
  best-placed seat not at fault. The order of games makes no difference.
- **Ranks.** Each rating has a standard error, of the bot's standing against
  the games-weighted field. A bot with fewer than 20 rated games, or not
  connected by games to the main group, is provisional and unranked. An idle
  bot keeps its last rating, which matchmaking starts from if it returns, and
  loses its rank. A new version of a bot should be registered as a new bot.
- **The fit.** Each bot carries a prior of two virtual games, one won and one
  lost, against a fixed 1500 opponent. The fit is solved by Newton's method
  (numpy), well under a second for hundreds of active bots.
- **Data.** A finished game only adds to the records the fit reads: the
  `head-to-head` collection for 1v1 and `lineups` (one document per set of
  bots) for 3p and 4p. Each run writes `elo<mode>`, `eloSe<mode>` and
  `rank<mode>` to the bot documents and its summary to `ratings/<mode>`.

## Run the tests

The engine tests use the standard library's `unittest`:

```bash
python3 -m unittest discover -s firebase/functions/tests -t firebase/functions
```

## Run your own server

1. Create a Firebase project on the Blaze plan with Firestore and Cloud Storage enabled. The scheduled matchmaker needs Cloud Scheduler.
2. Point the code at your project:
   - `firebase/.firebaserc`: project ID
   - `firebase/public/js/app.js`: the web app config at the top of the file
   - `firebase/functions/config.py`: `GCS_REPLAY_BUCKET`
   - `bots/daemon.py`: `API_BASE`
3. Create the functions virtualenv and deploy:

```bash
cd firebase/functions
python3.12 -m venv venv
venv/bin/pip install -r requirements.txt
cd ..
./deploy.sh
```

The engine and the bots need Python 3.10 or newer. Cloud Functions run on Python 3.12.

## License

Copyright 2026 Bovard Doerschuk-Tiberi

Licensed under the Apache License, Version 2.0. See [`LICENSE`](LICENSE).
