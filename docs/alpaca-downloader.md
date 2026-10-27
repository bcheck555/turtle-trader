# Alpaca historical acquisition

The downloader calls only Alpaca's documented historical stock-bars endpoint:
`GET https://data.alpaca.markets/v2/stocks/bars`. It never connects to a trading
endpoint. Credentials are read from environment variables only when the download
CLI command is invoked. Injected clients and clocks make tests entirely offline.

## Request and output contract

Specify up to 100 distinct symbols and inclusive completed New York dates. Default
interval is five minutes; accepted intervals match the strategy's minute divisors
of 30. Regular and extended coverage reuse `ExchangeCalendar`, including holidays,
DST and conservative early-close handling. Only the selected coverage is retained;
discarded out-of-hours rows are counted. No interval resampling is implicit.

Requests pin feed, full adjustments, symbol mapping `asof` and ascending sorting.
Symbols are split into sequential batches of 50. Each page requests 10,000 rows
and follows continuation tokens even when a page is empty or short; repeated
tokens fail acquisition. Identical duplicate rows are counted; conflicting
duplicates, malformed prices, fractional volumes or invalid timestamps fail.

Connection timeout is 10 seconds; read timeout is 30 seconds. Network errors,
429 and server errors get at most three retries with 1/2/4-second backoff and
bounded `Retry-After` handling. Authentication, entitlement and malformed-request
failures are terminal. No silent feed switch or date shortening occurs.

Completed acquisition publishes four files in a new destination directory:

- `bars.csv`: canonical columns, UTC opening timestamps, timestamp/symbol order.
- `manifest.toml`: importer-compatible dates, symbols, coverage and research basis.
- `coverage.json`: expected tradable/observed counts, absent symbols, unknown
  missing ranges and separately reported verified halt counts/ranges.
- `provenance.json`: request parameters, timestamps, page/row counts, dependency
  versions, artifact checksums, exclusions, warnings and readiness status.

Incomplete coverage keeps the files and returns exit 3. Strict backtests reject
those gaps; reference replay may use `--allow-gaps` diagnostically. Request failure
returns exit 2 and publishes only sanitized error diagnostics. Existing destination
directories are never overwritten. Interrupted processes may leave a temporary
staging directory, never a successfully published final dataset.

## Interpretation

Alpaca can omit intervals without qualifying trades. An absent candle is therefore
an unclassified gap, not proof of an outage or inactivity. The strict calendar grid
may reject legitimate sparse data, particularly extended hours or single-venue IEX.
Do not replace such bars with fabricated flat OHLCV records.

[Verified market-wide pauses](market-halts.md) remove only wholly halted slots
from the expected grid. Partially tradable boundary bars remain required. This
does not exempt unknown gaps or silently accept arbitrary absent candles.

Downloader exports deliberately use `price_basis="adjusted_research"`, full
adjustments and `action_free=false`. The latter means absence of corporate actions
is not attested. No corporate-action discovery is performed. Adjusted-price PnL
models synthetic units and fee assumptions; it is not actual-account performance.
Vendor historical adjustments can reflect events later than a simulated decision.
Archived CSV/checksums identify the exact data used despite future vendor revisions.

Historical symbol mapping is pinned to the requested end date. That mapping is not
a point-in-time universe or survivorship-bias correction. Benchmarks still require
matching timestamps and explicit total-return-adjusted input.

## Official references

- [Historical bars and pagination](https://docs.alpaca.markets/us/reference/stockbars)
- [Historical SIP access and bar construction](https://docs.alpaca.markets/us/docs/market-data-faq)

No credentials were read and no live request was made during automated validation.
See [validation evidence](downloader-validation.md) for tests, reviews and boundaries.
