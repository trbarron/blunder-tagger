#!/usr/bin/env python3
"""
load_games.py

Reads curated_games.json (output of blunder_tagger.py) and loads each game
into Redis under the key: blunderWatch:game:{date}

Games must have their 'date' field set to their intended publication date
(YYYY-MM-DD) before running this script. Edit curated_games.json or pass
--assign-dates to auto-assign sequential dates starting from --start-date.

Usage:
    python load_games.py                                     # reads curated_games.json
    python load_games.py --input my_games.json
    python load_games.py --assign-dates --start-date 2026-04-01
    python load_games.py --dry-run                           # preview without writing
    python load_games.py --list                              # show what's currently in Redis
    python load_games.py --delete 2026-04-01                # remove a specific date

Requirements:
    pip install redis python-dotenv
    REDIS_URL or REDIS_TLS_URL env var (or .env file)
"""

import argparse
import json
import os
import sys
from datetime import date, timedelta
from typing import Optional

try:
    import redis as redis_lib
except ImportError:
    print("ERROR: redis package not found. Run: pip install redis python-dotenv")
    sys.exit(1)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv optional

TTL_90_DAYS = 90 * 24 * 60 * 60


def get_redis_client() -> redis_lib.Redis:
    url = os.environ.get("REDIS_TLS_URL") or os.environ.get("REDIS_URL")
    if not url:
        print("ERROR: Set REDIS_URL or REDIS_TLS_URL in your environment or .env file.")
        sys.exit(1)
    kwargs: dict = {"decode_responses": True}
    if url.startswith("rediss://"):
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ctx
    return redis_lib.from_url(url, **kwargs)


def load_games_from_file(path: str) -> list:
    try:
        with open(path) as f:
            games = json.load(f)
        if not isinstance(games, list):
            print(f"ERROR: {path} must contain a JSON array.")
            sys.exit(1)
        return games
    except FileNotFoundError:
        print(f"ERROR: File not found: {path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in {path}: {e}")
        sys.exit(1)


def assign_sequential_dates(games: list, start: str) -> list:
    """Assign consecutive dates starting from start to each game."""
    current = date.fromisoformat(start)
    for game in games:
        game["date"] = current.isoformat()
        current += timedelta(days=1)
    return games


def load_to_redis(games: list, r: redis_lib.Redis, dry_run: bool = False) -> None:
    loaded = 0
    skipped = 0
    for game in games:
        game_date = game.get("date")
        if not game_date:
            print(f"  SKIP {game.get('gameId', '?')} — no date set")
            skipped += 1
            continue

        key = f"blunderWatch:game:{game_date}"

        if dry_run:
            print(f"  DRY  {game.get('gameId', '?')} ({game_date}) — {game.get('blunderCount', '?')} blunders")
            loaded += 1
            continue

        r.setex(key, TTL_90_DAYS, json.dumps(game))
        print(f"  SET  {game.get('gameId', '?')} ({game_date}) — {game.get('blunderCount', '?')} blunders  →  {key}")
        loaded += 1

    print(f"\nLoaded: {loaded}  Skipped: {skipped}")



def list_loaded_games(r: redis_lib.Redis) -> None:
    keys = r.keys("blunderWatch:game:*")
    if not keys:
        print("No games currently loaded in Redis.")
        return
    keys.sort()
    print(f"{'Date':<14} {'Game ID':<10} {'Blunders':<10} {'TTL (days)':<12} {'Game URL'}")
    print("─" * 80)
    for key in keys:
        raw = r.get(key)
        ttl_sec = r.ttl(key)
        ttl_days = round(ttl_sec / 86400, 1) if ttl_sec > 0 else "no TTL"
        if raw:
            game = json.loads(raw)
            print(f"{game.get('date', '?'):<14} {game.get('gameId', '?'):<10} {game.get('blunderCount', '?'):<10} {str(ttl_days):<12} {game.get('gameUrl', 'N/A')}")


def delete_game(r: redis_lib.Redis, game_date: str) -> None:
    key = f"blunderWatch:game:{game_date}"
    deleted = r.delete(key)
    if deleted:
        print(f"Deleted: {key}")
    else:
        print(f"Key not found: {key}")


def main():
    parser = argparse.ArgumentParser(description="Load Blunder Watch games into Redis.")
    parser.add_argument("--input", default="curated_games.json", help="Input JSON file (default: curated_games.json)")
    parser.add_argument("--assign-dates", action="store_true", help="Auto-assign sequential dates to games")
    parser.add_argument("--start-date", default=date.today().isoformat(), help="Start date for --assign-dates (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be loaded without writing to Redis")
    parser.add_argument("--list", action="store_true", help="List all games currently loaded in Redis")
    parser.add_argument("--delete", metavar="DATE", help="Delete the game for a specific date (YYYY-MM-DD)")
    args = parser.parse_args()

    r = get_redis_client()

    # Ping to verify connection
    try:
        r.ping()
        print("Redis connection: OK\n")
    except Exception as e:
        print(f"ERROR: Could not connect to Redis: {e}")
        sys.exit(1)

    if args.list:
        list_loaded_games(r)
        return

    if args.delete:
        delete_game(r, args.delete)
        return

    games = load_games_from_file(args.input)
    print(f"Loaded {len(games)} game(s) from {args.input}")

    if args.assign_dates:
        games = assign_sequential_dates(games, args.start_date)
        print(f"Assigned dates starting from {args.start_date}")

    print()
    load_to_redis(games, r, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
