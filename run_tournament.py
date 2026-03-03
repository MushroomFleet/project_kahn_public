#!/usr/bin/env python3
"""
Tournament runner for Project Kahn — batch execution of model matchups.
Supports OpenRouter models and ZeroSystem integration.

Usage:
    # Quickest: run from entrants config (includes models, scenarios, settings)
    python run_tournament.py --entrants config/tournament_entrants.json --dry_run
    python run_tournament.py --entrants config/tournament_entrants.json

    # Override entrants config settings from CLI
    python run_tournament.py --entrants config/tournament_entrants.json --turns 10 --mode single

    # Manual: specify models directly
    python run_tournament.py --models openrouter/openai/gpt-5.2 openrouter/anthropic/claude-sonnet-4.6 \
        --scenarios v7_alliance v8_first_strike_fear --zerosystem

    # From full roster file
    python run_tournament.py --roster config/openrouter_models.json --scenario v7_alliance --zerosystem
"""

import os
import sys
import json
import itertools
import argparse
import subprocess
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_model_roster(roster_path: str) -> list:
    """Load model list from JSON roster file"""
    with open(roster_path, 'r') as f:
        data = json.load(f)
    return [m['id'] for m in data['models']]


def load_entrants_config(config_path: str) -> dict:
    """Load tournament entrants config with models, scenarios, and settings.

    Returns dict with keys: entrants, scenarios, settings
    Entries prefixed with '//' in the entrants list are treated as comments and skipped.
    """
    with open(config_path, 'r') as f:
        data = json.load(f)
    # Filter out comment-style entries
    entrants = [m for m in data.get('entrants', []) if not m.strip().startswith('//')]
    return {
        'entrants': entrants,
        'scenarios': data.get('scenarios', ['v7_alliance']),
        'settings': data.get('settings', {}),
    }


def generate_matchups(models: list, mode: str = 'round_robin') -> list:
    """Generate tournament matchups.

    Modes:
        round_robin: Every model plays every other model (both sides)
        single: Every unique pair plays once (randomly assigned sides)
    """
    if mode == 'round_robin':
        return list(itertools.permutations(models, 2))
    elif mode == 'single':
        return list(itertools.combinations(models, 2))
    else:
        raise ValueError(f"Unknown tournament mode: {mode}")


def run_single_match(model_a: str, model_b: str, scenario: str,
                     game_version: str = 'v11', aggressor: str = 'A',
                     turns: int = 50, zerosystem: bool = False,
                     results_dir: str = None) -> str:
    """Execute a single Kahn game match as a subprocess"""

    script = f"Kahn_game_{game_version}.py"
    script_path = os.path.join(BASE_DIR, script)

    cmd = [
        sys.executable, script_path,
        '--model_a', model_a,
        '--model_b', model_b,
        '--aggressor', aggressor,
        '--turns', str(turns),
        '--scenario', scenario,
    ]

    if zerosystem:
        cmd.append('--zerosystem')

    if results_dir:
        cmd.extend(['--results_dir', results_dir])

    logger.info(f"MATCH: {model_a} vs {model_b} [{scenario}] (ZeroSystem: {zerosystem})")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if result.returncode == 0:
            logger.info(f"  COMPLETE: {result.stdout.strip()}")
            return result.stdout.strip()
        else:
            logger.error(f"  FAILED: {result.stderr.strip()[:500]}")
            return f"FAILED: {result.stderr.strip()[:500]}"
    except subprocess.TimeoutExpired:
        logger.error(f"  TIMEOUT: Match exceeded 1 hour")
        return "TIMEOUT"
    except Exception as e:
        logger.error(f"  ERROR: {e}")
        return f"ERROR: {e}"


def main():
    parser = argparse.ArgumentParser(description='Project Kahn Tournament Runner')

    # Model sources (pick one)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--entrants', type=str,
                       help='Path to tournament entrants JSON config (recommended — includes models, scenarios, and settings)')
    source.add_argument('--roster', type=str, help='Path to model roster JSON file (full registry)')
    source.add_argument('--models', nargs='+', help='Model IDs to include (inline)')

    # These override entrants config when provided
    parser.add_argument('--scenario', type=str, default=None,
                       help='Scenario key (overrides entrants config)')
    parser.add_argument('--scenarios', nargs='+', default=None,
                       help='Multiple scenarios (overrides entrants config)')
    parser.add_argument('--mode', choices=['round_robin', 'single'], default=None,
                       help='Tournament bracket mode (overrides entrants config)')
    parser.add_argument('--game_version', choices=['v11', 'v12'], default=None)
    parser.add_argument('--aggressor', choices=['A', 'B'], default='A',
                       help='Which side is the aggressor for all matches')
    parser.add_argument('--turns', type=int, default=None)
    parser.add_argument('--zerosystem', action='store_true', default=None,
                       help='Enable ZEROsystem v3.0 for all matches (overrides entrants config)')
    parser.add_argument('--results_dir', type=str, default=None)
    parser.add_argument('--dry_run', action='store_true',
                       help='Print matchups without executing')

    args = parser.parse_args()

    # Load models and config defaults from entrants file, roster, or inline
    cfg_settings = {}
    cfg_scenarios = ['v7_alliance']

    if args.entrants:
        cfg = load_entrants_config(args.entrants)
        models = cfg['entrants']
        cfg_scenarios = cfg['scenarios']
        cfg_settings = cfg['settings']
        if not models:
            parser.error(f"No entrants found in {args.entrants}")
        logger.info(f"Loaded {len(models)} entrants from {args.entrants}")
    elif args.roster:
        models = load_model_roster(args.roster)
    elif args.models:
        models = args.models
    else:
        parser.error("Must specify --entrants, --roster, or --models")

    # Resolve settings: CLI flags override entrants config, which overrides hardcoded defaults
    mode = args.mode or cfg_settings.get('mode', 'round_robin')
    game_version = args.game_version or cfg_settings.get('game_version', 'v11')
    turns = args.turns if args.turns is not None else cfg_settings.get('max_turns', 50)
    zerosystem = args.zerosystem if args.zerosystem is not None else cfg_settings.get('zerosystem', False)

    # Scenarios: CLI --scenarios > CLI --scenario > entrants config > default
    if args.scenarios:
        scenarios = args.scenarios
    elif args.scenario:
        scenarios = [args.scenario]
    else:
        scenarios = cfg_scenarios

    # Generate matchups
    matchups = generate_matchups(models, mode)

    total = len(matchups) * len(scenarios)
    logger.info(f"Tournament: {len(models)} models, {len(matchups)} matchups, "
                f"{len(scenarios)} scenarios = {total} total games")
    logger.info(f"Game: {game_version} | Mode: {mode} | Turns: {turns} | "
                f"ZeroSystem: {'ENABLED' if zerosystem else 'DISABLED'}")

    if args.dry_run:
        for i, ((a, b), scen) in enumerate(itertools.product(matchups, scenarios), 1):
            print(f"  [{i}/{total}] {a} vs {b} [{scen}]")
        return

    # Setup results directory
    results_dir = args.results_dir or os.path.join(
        BASE_DIR, 'tournament_results',
        f"tournament_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    os.makedirs(results_dir, exist_ok=True)

    # Execute tournament
    results_log = []
    for i, ((model_a, model_b), scenario) in enumerate(
            itertools.product(matchups, scenarios), 1):
        logger.info(f"[{i}/{total}] Starting match...")
        result = run_single_match(
            model_a, model_b, scenario,
            game_version=game_version,
            aggressor=args.aggressor,
            turns=turns,
            zerosystem=zerosystem,
            results_dir=results_dir
        )
        results_log.append({
            'match': i,
            'model_a': model_a,
            'model_b': model_b,
            'scenario': scenario,
            'result': result
        })

    # Save tournament manifest
    manifest_path = os.path.join(results_dir, 'tournament_manifest.json')
    with open(manifest_path, 'w') as f:
        json.dump({
            'timestamp': datetime.now().isoformat(),
            'models': models,
            'scenarios': scenarios,
            'mode': mode,
            'zerosystem': zerosystem,
            'game_version': game_version,
            'max_turns': turns,
            'total_games': total,
            'results': results_log
        }, f, indent=2)

    logger.info(f"Tournament complete. {len(results_log)} games played. Manifest: {manifest_path}")


if __name__ == '__main__':
    main()
