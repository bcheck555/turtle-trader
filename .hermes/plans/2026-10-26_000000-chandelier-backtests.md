# Plan: SPY/QQQ Chandelier timeframe backtests

## Goal
Run and compare SPY and QQQ backtests using existing downloaded bars for session-based Chandelier exits and intraday exits at 30-, 60-, and 240-minute intervals, writing every generated artifact only beneath `/run`.

## Current context / assumptions
- The repository root is `/var/github/turtle-trader`; it is on branch `codex/cleanup-runs` with a clean status at planning time.
- CLI usage in `README.md` shows `uv run turtle-trader backtest <bars.csv> --manifest <manifest.toml> --config <config.toml> --output-dir <dir>` and supports repeatable `--set` TOML overrides.
- The project supports `strategy.chandelier_timeframe` values `session` and `intraday`; its intraday interval configuration and accepted interval values must be confirmed from `src/turtle_trader/domain.py` and CLI config implementation before execution. The requested 30, 60, and 240 minutes may need to map to existing `interval_minutes` configuration.
- “Only write files to /run” means inputs remain untouched; configs, logs, summaries, and backtest output directories created for this task must all live below `/run`. Do not write experiment artifacts under the repository or its `runs/` directory.
- Assume the already-downloaded SPY/QQQ datasets have a shared bars CSV and manifest, and contain sufficient regular-session history for the indicators. Locate and validate exact paths before any run; if absent or incompatible, stop and report rather than downloading data.
- No implementation/code changes are planned. This is an operational experiment using existing CLI behavior.

## Architecture / proposed approach
Discover the exact existing SPY/QQQ bars and manifest, inspect the supported interval setting, and validate the input dataset without modifying it. Run five configurations (session; intraday 30, 60, and 240 minutes—clarify the count: this is four configurations total, since there is one session plus three intraday) into distinct output directories under `/run`, then collect comparable metrics and provenance into a summary also under `/run`.

## Step-by-step tasks

1. Inspect CLI requirements and locate datasets (2–5 minutes).
   - Read `src/turtle_trader/domain.py`, `src/turtle_trader/cli.py`, `src/turtle_trader/config.py` (if present; otherwise identify the actual config module), and relevant README data instructions.
   - Locate candidate existing SPY/QQQ `bars.csv` and manifest files in the user-provided/download locations and `/run`; do not scan or print credentials or `.env` contents.
   - Confirm the CLI's accepted interval field/name and validate that 30, 60, and 240 are supported. If the exact input file paths cannot be determined or interval setting is unsupported, ask the user before proceeding.
   - Read-only verification command: `git status --short --branch` (expected: branch/status shown; no changes made). Use the project's validate command with the discovered paths in a later task.

2. Prepare an isolated output root and baseline input validation (2–5 minutes).
   - Use `/run/turtle-trader-chandelier-<timestamp>/` as the experiment root; create it and any derived config files only under `/run`.
   - Run `uv run turtle-trader validate <BARS_CSV> --manifest <MANIFEST>` (expected: successful validation, with SPY and QQQ present; if validation reports missing bars/gaps or metadata problems, stop and report exact findings).
   - Preserve the existing dataset unchanged; record its paths, symbol list, interval, coverage, adjustment basis, and validation output in `/run/.../experiment-notes.txt`.

3. Establish the exact four backtest commands/configurations (2–5 minutes).
   - Use `configs/system2-chandelier.toml` as the base config (session timeframe, Chandelier, existing strategy defaults), unless inspection reveals the requested comparison needs a different already-existing baseline. Do not edit that repository config.
   - Session run: `uv run turtle-trader backtest <BARS_CSV> --manifest <MANIFEST> --config configs/system2-chandelier.toml --output-dir /run/.../session`
   - Intraday runs: pass explicit repeatable overrides for `strategy.chandelier_timeframe="intraday"` and the verified interval setting `=<30|60|240>`; output respectively under `/run/.../intraday-30m`, `/run/.../intraday-60m`, `/run/.../intraday-240m`.
   - Before executing, inspect `--print-config` behavior and run it for all three override sets. Expected: effective config explicitly reports the desired timeframe and interval for each. If 240 is unsupported, do not substitute another value; ask the user.

4. Execute all four runs (2–5 minutes each; allow longer runtime for full history).
   - Execute each backtest independently, sequentially if shared database/output behavior could conflict. Keep each `--output-dir` distinct and under the experiment root.
   - Expected: each command exits 0 and produces the project's documented backtest artifacts only within its designated `/run` directory. On any nonzero exit, stop subsequent runs only if the error invalidates shared assumptions; retain logs under `/run` and diagnose without changing source code.

5. Verify artifacts and prepare comparison summary (2–5 minutes).
   - Inspect the generated JSON/report artifacts and confirm each records symbols SPY/QQQ, Chandelier exit strategy, correct timeframe, and correct interval where applicable.
   - Compare the same available headline metrics across all four runs (for example return, drawdown, trade count, and ending equity, using actual report field names). Include data/provenance caveats and commands in `/run/.../summary.md`.
   - Verify file scope with `find /run/turtle-trader-chandelier-<timestamp> -type f -print` (expected: only experiment configs/notes/logs/reports under that experiment root; do not claim whole-system `/run` cleanliness based on this command).

## Tests / validation
- No code is changed, so TDD and commits are not applicable.
- Input validation: exact `uv run turtle-trader validate ...` command above must succeed before any backtest.
- Configuration validation: `uv run turtle-trader backtest --config configs/system2-chandelier.toml --set 'strategy.chandelier_timeframe="intraday"' --set '<VERIFIED_INTERVAL_KEY>=30' --print-config` (repeat with 60 and 240); expected output must show the requested effective values. Substitute only the key verified from source/config schema.
- Run validation: all four commands must exit 0; generated metadata must confirm the expected settings and both symbols.
- Scope validation: all newly created files for this task must be under the chosen `/run/...` experiment root.

## Risks, tradeoffs, and open questions
- Dataset paths are not stated in the request and were not established during initial repository inspection. The implementer must locate them read-only or ask for exact paths; never download/replace data.
- “Four hour” is treated as 240 minutes. Confirm project support before running; do not assume it.
- Interval field naming and whether bars are already minute-level vs aggregated must be checked in project code. If the CLI does not aggregate input bars or require interval configuration, explain the limitation and ask rather than produce misleading comparisons.
- A session vs intraday Chandelier comparison is only meaningful when the same symbols, date window, price basis, strategy/risk settings, and execution assumptions are held constant.
- Backtest metrics are historical simulations, not investment advice or guarantees; vendor adjustments, gaps, and data coverage may materially affect results.
- Request says write only under `/run`; ensure environment is writable there and that no CLI default writes databases/reports elsewhere. If the CLI persists outside the explicit output directory, identify the behavior and stop for approval before proceeding.
