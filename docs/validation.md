# Offline milestone validation

## Checks performed

- Complete pytest suite on Python 3.12 and Python 3.13: 121 passed.
- Coverage on Python 3.13: 91% overall; strategy, sessions, indicators and risk 100%.
- Ruff checks and strict mypy: passed.
- Source distribution and wheel build: passed.
- Isolated wheel installation verified archived original-source SHA-256.
- CLI integration exercised import, replay, backtest, report, holdout/fold plumbing,
  persistent round trips, and original-source integrity.
- Synthetic reference/swing demo reports generated locally under ignored `runs/`.

The exchange-calendar dependency emits pandas deprecation warnings. They did not
fail validation. Test coverage measures exercised code, not proof of correctness
or profitable trading.

## Independent reviews

GPT-6.1 Sol at High reasoning implemented strategy and execution components in
separate agent assignments. Independent GPT-6.1 Sol High agents reviewed strategy
against the Pine source and reviewed execution/risk accounting. GPT-6 Luna at
Medium implemented reporting/storage; coordinator integrated the application.

Strategy review found no core translation defect. Additional regressions cover
falling active exits, missed Friday closes/Sunday reopening, held unconfirmed
previews and UTC/New York equivalence across DST.

Execution review reproduced a partial-fill sizing defect: fees/slippage on filled
shares were omitted from remaining planned risk. Corrected sizing includes these
costs and post-fill equity; regression verifies changing fill prices respect the
configured risk fraction. Inferred intrabar fills now disclose their candle
bounds and uncertain execution time. No remaining blocking review finding.

## Boundaries

Source-derived unit fixtures are not externally exported TradingView ground
truth. Platform parity remains unverified. The source is an indicator, without
portfolio returns or executable fills. Synthetic reports are software smoke tests.

Direct backtest API callers must separately validate data coverage. Historical
volume caps model a scenario; actual queue priority/liquidity is unknown. Partial
protective fills and missing deadline quotes can leave exposure. Open positions
and orders remain explicit at run end. No broker-side acceptance is claimed.

Persistent paper execution/recovery, scanner/services, provider downloads and live
trading remain outside this milestone. No brokerage connection or real order was
made during implementation or validation.
