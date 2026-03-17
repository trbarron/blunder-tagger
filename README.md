# blunder-tagger

Analyzes Lichess PGN exports to curate chess games for the **Blunder Watch** daily puzzle feed. Uses Stockfish and the Lichess winning-chances formula to identify games with an interesting blunder profile, then loads them into Redis keyed by publication date.

## How it works

1. **`blunder_tagger.py`** — scans PGN files and selects games where:
   - Target user is playing White
   - Game ends in checkmate
   - Game length is 60–80 plies
   - Total blunders (≥0.30 winning-chances loss) is between 3 and 6

   Matching games are written to `curated_games.json`.

2. **`load_games.py`** — reads `curated_games.json` and loads each game into Redis under `blunderWatch:game:{YYYY-MM-DD}` with a 90-day TTL.

## Requirements

- Python 3.9+
- [Stockfish](https://stockfishchess.org/download/) installed (e.g. `brew install stockfish`)
- A Redis instance

```
pip install chess redis python-dotenv
```

Copy `.env.example` to `.env` and fill in your Redis URL:

```
cp .env.example .env
```

## Usage

**Step 1: Tag blunders**

Place PGN files in a `pgns/` directory (or point to a single file), then:

```bash
python blunder_tagger.py
python blunder_tagger.py pgns/ --user yourname --depth 20
python blunder_tagger.py game.pgn --stockfish /path/to/stockfish
```

Options:
- `--user` — Lichess username to filter for (White player, default: `trbarron`)
- `--depth` — Stockfish analysis depth (default: 20)
- `--output` — output JSON file (default: `curated_games.json`)
- `--threshold` — winning-chances blunder threshold (default: 0.30)
- `--white-only` / `--black-only` — count blunders for one side only

**Step 2: Assign dates and load to Redis**

```bash
# Preview what would be loaded
python load_games.py --dry-run

# Auto-assign dates starting tomorrow, then load
python load_games.py --assign-dates --start-date 2026-04-01

# List what's in Redis
python load_games.py --list

# Remove a specific date
python load_games.py --delete 2026-04-01
```

## Output format

Each entry in `curated_games.json`:

```json
{
  "gameId": "bw-001",
  "date": "2026-04-01",
  "gameUrl": "https://lichess.org/abc123",
  "whiteElo": 1850,
  "blackElo": 1820,
  "moves": ["e4", "e5", "Nf3", "..."],
  "blunderIndices": [12, 24, 37],
  "evals": [45, 38, -120, "..."],
  "blunderCount": 3
}
```
