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

Register a bot once and keep the API key it returns:

```bash
curl -X POST https://open-hex.web.app/api/bots \
  -H "Content-Type: application/json" \
  -d '{"name": "my_bot", "mode": "1v1"}'
```

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

The leaderboard's Elo is solved over every finished game, each game weighted
the same: a bot's rating is the one at which its expected wins, by Elo's
usual formula, equal its actual wins against every opponent it has met
(`firebase/functions/services/rankings.py`, computed from the head-to-head
records when the leaderboard is requested). It therefore does not depend on
the order the games were played in. The rating is shown with its standard
error; a bot with fewer than 20 games is provisional and listed after the
ranked ones. The bot documents also carry an Elo updated one game at a time,
which the matchmaker uses to pair bots of similar strength; being sequential
with a fixed step, it swings with streaks and is not the ranking.

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
