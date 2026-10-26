# Robinhood integration feasibility

Reviewed official public documentation October 26, 2026. No authentication,
MCP connection, account inspection or order request was attempted. No live adapter
exists here. Reverify changing product documentation before future integration.

## Established capabilities

Robinhood documents an official Trading MCP and onboarding into a separate MCP
account for external agents. Its documented endpoint is
`https://agent.robinhood.com/mcp/trading`; recorded here, never invoked by this app.
[Official onboarding](https://robinhood.com/us/en/support/articles/agentic-trading-overview/).

The agent guide lists equity history and advanced-order tools, including
`get_equity_historicals`, `place_advanced_order`, `review_advanced_order`, and
`cancel_advanced_order`. Availability does not settle exact schemas, historical
coverage or safe recovery semantics.
[Official agent guide](https://robinhood.com/us/en/support/articles/trading-with-your-agent/).

The consumer equity order guide lists market, limit, stop, stop-limit, trailing
stop and take-profit/stop-loss products. It says bracket and opening/closing
auction orders are unavailable. This describes consumer products; it does not
establish atomic entry-plus-protection through MCP.
[Official order types](https://robinhood.com/us/en/support/articles/order-types/).

## Capability gates

| Capability | Evidence / unresolved verification |
| --- | --- |
| Official Trading MCP exists | Confirmed public onboarding; not connected |
| US equity execution | Agent tools documented; account/instrument permissions unverified |
| Intraday historical data | Tool documented; interval, depth, feed and adjustments unresolved |
| Linked entry and protective stop | Not verified; advanced orders do not establish atomic linkage |
| Protective quantity | Acceptance, extended-hours and partial-fill semantics unresolved |
| Idempotency / order lookup | Stable client reference, retries and history visibility unresolved |
| Cancellation / replacement | Races, guarantees and oversell prevention unresolved |
| Fill-driven protection | Event subscriptions, polling latency and outage behavior unresolved |

The reviewed documentation does not verify a server-side linked entry/protective
order suitable for this application. Treat this capability as unavailable until
an authorized official schema and safe testing establish it. Do not infer it from
consumer take-profit/stop-loss features.

## Live prerequisites

Live work requires new explicit user authorization. First implement durable
submission intents, ambiguous-timeout reconciliation, broker-authoritative startup,
fill deduplication, confirmed protective coverage, cancel/replace race handling,
critical alerts and a fail-closed live authorization gate. Require independent
security/safety review and tested incident recovery against documented official
interfaces in a safe environment. No undocumented API or credential workaround.

Deterministic Python must remain the sole source of trade and risk instructions.
This offline milestone does not connect to the MCP or resolve runtime capabilities.
