#!/usr/bin/env python3
"""
blunder_tagger.py

Analyzes PGN files to identify blunders using Lichess's winning probability definition.
Filters games based on specific criteria:
1. Target user (default "trbarron") playing as White.
2. Game ends in checkmate.
3. Game length: 60-80 plies.
4. 3-6 blunders in total.

Writes to curated_games.json incrementally.
"""

import argparse
import json
import math
import os
import shutil
import sys
from datetime import date
from pathlib import Path
from typing import Optional, List, Dict, Any

import chess
import chess.pgn
import chess.engine

# ── Constants ────────────────────────────────────────────────────────────────

BLUNDER_THRESHOLD_WC = 0.30  # Lichess defines blunder as >= 0.30 WC loss (WC is [-1, 1])
DEFAULT_DEPTH = 20
DEFAULT_TARGET_USER = "trbarron"
MIN_PLIES = 60
MAX_PLIES = 80
MIN_BLUNDERS = 3
MAX_BLUNDERS = 6

# ── Helpers ───────────────────────────────────────────────────────────────────

def find_stockfish() -> Optional[str]:
    """Locate the Stockfish binary on common paths."""
    found = shutil.which("stockfish")
    if found:
        return found
    for candidate in (
        "/opt/homebrew/bin/stockfish",
        "/usr/local/bin/stockfish",
        "/usr/bin/stockfish",
        "/opt/homebrew/Cellar/stockfish/17/bin/stockfish", # Added for common brew path
    ):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None

def winning_chances(cp: int) -> float:
    """Lichess formula to convert centipawns to winning chances (-1.0 to 1.0)."""
    # MULTIPLIER = -0.00368208
    # 2 / (1 + exp(MULTIPLIER * cp)) - 1
    return 2 / (1 + math.exp(-0.00368208 * cp)) - 1

def get_score(engine: chess.engine.SimpleEngine, board: chess.Board, depth: int) -> chess.engine.PovScore:
    """Get the evaluation from the engine."""
    info = engine.analyse(board, chess.engine.Limit(depth=depth))
    return info["score"]

def save_game_to_json(record: Dict[str, Any], filename: str):
    """Update the JSON file with a new game record."""
    data = []
    if os.path.exists(filename):
        try:
            with open(filename, "r") as f:
                content = f.read().strip()
                if content:
                    data = json.loads(content)
                if not isinstance(data, list):
                    data = []
        except (json.JSONDecodeError, FileNotFoundError):
            data = []
    
    data.append(record)
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

# ── Core Analysis ─────────────────────────────────────────────────────────────

def process_pgn(
    pgn_path: Path,
    engine: chess.engine.SimpleEngine,
    depth: int,
    target_user: str,
    output_file: str,
    white_only: bool = False,
    black_only: bool = False,
    threshold: float = BLUNDER_THRESHOLD_WC
) -> int:
    """Process PGN file(s) and find games matching criteria."""
    pgn_files = list(pgn_path.glob("*.pgn")) if pgn_path.is_dir() else [pgn_path]
    found_count = 0
    today = date.today().isoformat()
    
    # Determine starting ID based on existing records
    current_id_num = 1
    if os.path.exists(output_file):
        try:
            with open(output_file, "r") as f:
                data = json.load(f)
                if data and isinstance(data, list):
                    last_id = data[-1].get("gameId", "bw-000")
                    if last_id.startswith("bw-"):
                        try:
                            current_id_num = int(last_id.split("-")[1]) + 1
                        except (IndexError, ValueError):
                            pass
        except:
            pass

    for p_file in pgn_files:
        print(f"Reading {p_file}...")
        with open(p_file, encoding="utf-8", errors="replace") as fh:
            while True:
                game = chess.pgn.read_game(fh)
                if game is None:
                    break
                
                # Filter 1: White is target_user
                if game.headers.get("White") != target_user:
                    continue
                
                # Filter 2: Length 60-80 plies
                moves = list(game.mainline_moves())
                ply_count = len(moves)
                if not (MIN_PLIES <= ply_count <= MAX_PLIES):
                    continue
                
                # Filter 3: Ends in checkmate
                # Check board after all moves
                board_at_end = game.end().board()
                if not board_at_end.is_checkmate():
                    continue
                
                print(f"Analyzing potential game: {game.headers.get('White')} vs {game.headers.get('Black')} ({ply_count} plies)...")
                
                # Analysis for blunders
                blunder_indices = []
                evals_cp = []
                san_moves = []
                
                analysis_board = game.board()
                curr_score = get_score(engine, analysis_board, depth)
                
                for i, move in enumerate(moves):
                    if i % 10 == 0:
                        print(f"  Analyzing ply {i+1}/{ply_count}...", flush=True)
                    
                    side_that_moved = analysis_board.turn
                    is_white_turn = (side_that_moved == chess.WHITE)
                    
                    # Current score from moving side perspective
                    prev_cp = curr_score.pov(side_that_moved).score(mate_score=10000)
                    prev_mate = curr_score.pov(side_that_moved).mate()
                    prev_wc = winning_chances(prev_cp)
                    
                    san_moves.append(analysis_board.san(move))
                    analysis_board.push(move)
                    
                    new_score = get_score(engine, analysis_board, depth)
                    # New score from moving side perspective (it should be lower if move was bad)
                    new_cp = new_score.pov(side_that_moved).score(mate_score=10000)
                    new_mate = new_score.pov(side_that_moved).mate()
                    new_wc = winning_chances(new_cp)
                    
                    evals_cp.append(new_score.pov(chess.WHITE).score(mate_score=10000))
                    
                    # Blunder logic
                    is_blunder = False
                    
                    # 1. Winning Chances Delta
                    loss = prev_wc - new_wc
                    if loss >= threshold:
                        is_blunder = True
                    
                    # 2. Mate Sequences
                    # MateCreated: Side that moved was not mated, now they are
                    if prev_mate is None or prev_mate > 0:
                        if new_mate is not None and new_mate < 0:
                            # Blunder unless it was already very bad
                            if prev_cp >= -700:
                                is_blunder = True
                    
                    # MateLost: Side that moved had a mate, now they don't (or it's for other side)
                    if prev_mate is not None and prev_mate > 0:
                        if new_mate is None or new_mate < 0:
                            # Blunder unless it's still very good (Inaccuracy/Mistake)
                            if new_cp <= 700:
                                is_blunder = True
                    
                    # Side filtering
                    is_side_of_interest = True
                    if white_only and not is_white_turn: is_side_of_interest = False
                    if black_only and is_white_turn: is_side_of_interest = False
                    
                    if is_blunder and is_side_of_interest:
                        blunder_indices.append(i)
                    
                    curr_score = new_score
                
                # Filter 4: 3-6 blunders total
                blunder_count = len(blunder_indices)
                if MIN_BLUNDERS <= blunder_count <= MAX_BLUNDERS:
                    # Get Elos safely
                    try:
                        w_elo = int(game.headers.get("WhiteElo", 0))
                    except (TypeError, ValueError):
                        w_elo = 0
                    try:
                        b_elo = int(game.headers.get("BlackElo", 0))
                    except (TypeError, ValueError):
                        b_elo = 0
                        
                    record = {
                        "gameId": f"bw-{current_id_num:03d}",
                        "date": today,
                        "gameUrl": game.headers.get("Site", ""),
                        "whiteElo": w_elo,
                        "blackElo": b_elo,
                        "moves": san_moves,
                        "blunderIndices": blunder_indices,
                        "evals": evals_cp,
                        "blunderCount": blunder_count
                    }
                    save_game_to_json(record, output_file)
                    print(f"  ✓ Found and saved: {record['gameId']} ({blunder_count} blunders)")
                    current_id_num += 1
                    found_count += 1
                else:
                    print(f"  × Skipped: {blunder_count} blunders (range: {MIN_BLUNDERS}-{MAX_BLUNDERS})")
                    
    return found_count

def main():
    parser = argparse.ArgumentParser(description="Lichess-style blunder tagger.")
    parser.add_argument("input", nargs="?", default="pgns", help="PGN file or directory (default: pgns)")
    parser.add_argument("--user", default=DEFAULT_TARGET_USER, help=f"Target White user (default: {DEFAULT_TARGET_USER})")
    parser.add_argument("--output", default="curated_games.json", help="Output JSON file")
    parser.add_argument("--depth", type=int, default=DEFAULT_DEPTH, help=f"Analysis depth (default: {DEFAULT_DEPTH})")
    parser.add_argument("--stockfish", help="Path to Stockfish binary")
    parser.add_argument("--white-only", action="store_true", help="Only count White's blunders")
    parser.add_argument("--black-only", action="store_true", help="Only count Black's blunders")
    parser.add_argument("--threshold", type=float, default=BLUNDER_THRESHOLD_WC, help=f"Blunder threshold in WC loss (default: {BLUNDER_THRESHOLD_WC})")
    args = parser.parse_args()
    
    stockfish_path = args.stockfish or find_stockfish()
    if not stockfish_path:
        print("Error: Stockfish not found. Please install it or provide path with --stockfish")
        sys.exit(1)
        
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input not found: {args.input}")
        sys.exit(1)
        
    print(f"Stockfish path: {stockfish_path}")
    print(f"Target User (White): {args.user}")
    print(f"Criteria: Plies {MIN_PLIES}-{MAX_PLIES}, Mate=Required, Blunders {MIN_BLUNDERS}-{MAX_BLUNDERS}")
    if args.white_only:
        print("Filtering: White blunders only")
    elif args.black_only:
        print("Filtering: Black blunders only")
    
    try:
        with chess.engine.SimpleEngine.popen_uci(stockfish_path) as engine:
            count = process_pgn(
                input_path, 
                engine, 
                args.depth, 
                args.user, 
                args.output,
                white_only=args.white_only,
                black_only=args.black_only,
                threshold=args.threshold
            )
    except Exception as e:
        print(f"Error during execution: {e}")
        sys.exit(1)
        
    print(f"\nDone! Found and saved {count} matching game(s).")



if __name__ == "__main__":
    main()
