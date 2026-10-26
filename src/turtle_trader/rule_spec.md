# Turtle rules specification

Specification version: 2.0. Source: Curtis Faith, *Original Turtle Trading Rules*,
firsthand account of the rules taught by Richard Dennis and William Eckhardt:
<https://www.pipsafe.com/Forex-E-Books/turtlerules.pdf>.

This is an implementation specification, not a reproduction of the source text.

## Source mechanics

N is true range averaged over 20 daily sessions, seeded with a simple average
and thereafter updated as `(19 * previous_N + TR) / 20`. True range includes gaps
from the previous close. System 2 enters one tick beyond the preceding 55-session
high/low and exits beyond the opposite 20-session channel. System 1 uses 20-session
entries, a previous-breakout winner filter, a 55-session failsafe and 10-session
exits. Entries and exits trigger intraday; gaps execute at the opening price.

One unit represents a 1% sizing-capital change for a 1N price movement, rounded
down to whole shares. Each unit starts with a 2N stop. Add every half N measured
from the preceding unit's actual fill, up to four units per symbol. Each addition
moves earlier stops half N toward profit; each lot retains its own stop. Limits
are six same-direction units in closely correlated markets, ten in loosely
correlated markets and twelve overall. Pending units consume capacity.

System 1's prior breakout is hypothetical regardless of whether it was traded,
skipped or short. Track its original 2N stop and 10-session exit independently of
actual account outcomes and alternate exits. A profitable channel exit makes it
a winner; an adverse 2N movement makes it a loser. The failsafe remains available
after skipped entries. Breakeven exits count as nonwinning here.

## Deterministic implementation conventions

- Default to System 2, one system per run, long only. Short permission never
  disables System 1's hypothetical short tracking.
- Use completed regular exchange sessions. Publish N and unit sizes before the
  week's first session, from preceding completed history. Freeze N and intended
  shares for each campaign at initial entry. Unknown prior breakout history
  permits the first eligible System 1 signal.
- Reduce sizing capital 20% at successive losses of 10% of the current sizing
  anchor; process all crossed thresholds. Restore baseline on recovery. Reset
  annual baseline to opening marked equity before the year's first new fill.
  Annual reset and campaign freezing resolve discretion/ambiguity in the account.
  At the geometric threshold limit, sizing capital becomes zero; do not iterate
  indefinitely. Drawdown reductions immediately cap new campaigns' weekly shares
  without resizing already active campaigns.
- Long units must fit available cash; reject rather than reduce their size.
  Default all symbols into shared close/loose correlation groups unless complete
  groups are provided. These are explicit equity adaptations.
- Existing stops and prospective levels may execute during the next candle.
  Newly completed observations only affect later execution. Hold overnight;
  there is no scheduled daily liquidation or reentry.
- Evaluate synchronized high-first and low-first paths across each candle batch
  from identical state, using a shared normalized segment clock. At each shared
  timestamp process all reductions before increases, then symbol order for ties.
  Commit lower aggregate closing equity; choose high first on a tie. Disclose ambiguity.
  Candle-volume participation is a liquidity assumption, not an observed queue.
- Optional shorts require a symbol allowlist and an explicit annual borrow rate.
  Lock proceeds plus 50% additional entry collateral; require 130% of current
  liability. Accrue borrowing over calendar days and force covers after observed
  maintenance breaches. Borrowing availability and margin checks are simulated.
- Chandelier is an alternate exit, independent of mandatory unit stops. Its
  defaults are completed-session high/low window 22, ATR(22), multiple 3 and
  ratcheting. Long and short formulas mirror each other.
- Preserve adjusted-price research labeling. No future corporate-action cash or
  actual stock-loan availability is inferred. Final positions remain marked.

All resolved parameters, deviations, data identity, implementation version and
this specification's hash accompany results. Parameter experiments do not alter
the source specification; changed periods or mechanics identify research variants.
