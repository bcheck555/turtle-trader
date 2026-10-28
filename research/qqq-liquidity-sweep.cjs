'use strict';
// Isolated exploratory simulator. Production Turtle rules remain independent.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const { load } = require('./qqq-velocity.cjs');
const RULES = require('./qqq-liquidity-sweep-rules.json');
const MINUTE = 60000;
const FIVE = 5 * MINUTE;
const ROOT = path.resolve(__dirname, '..');
const hash = data => crypto.createHash('sha256').update(data).digest('hex');
const iso = t => new Date(t).toISOString();

function scheduledOpen(t) {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(t);
  const part = type => Number(parts.find(p => p.type === type).value);
  return t - (part('hour') * 60 + part('minute') - 570) * MINUTE;
}

function prepare(days, coverage, haltIntervals = [], rules = RULES) {
  const sessions = new Map(coverage.sessions.map(s => [s.day, s]));
  const halts = haltIntervals.map(h => ({ start: Date.parse(h.start), end: Date.parse(h.end) }));
  const ranges = [];
  let previousClose, previousDay;
  for (const day of days) {
    const session = sessions.get(day.date);
    assert(session && !session.missing_count, `Missing session coverage: ${day.date}`);
    day.openTime = scheduledOpen(day.bars[0].t);
    day.closeTime = day.openTime + (session.expected_count + session.halted_count) * MINUTE;
    assert.equal(day.bars.at(-1).t, day.closeTime - MINUTE, 'Missing scheduled final minute');
    assert.equal(day.bars.length, session.observed_count);
    for (let j = 0; j < day.bars.length; j++) {
      const b = day.bars[j];
      assert(b.t >= day.openTime && b.t < day.closeTime);
      if (j && b.t !== day.bars[j - 1].t + MINUTE) {
        for (let t = day.bars[j - 1].t + MINUTE; t < b.t; t += MINUTE)
          assert(halts.some(h => t >= h.start && t + MINUTE <= h.end), 'Unexplained minute gap');
      }
      b.executionTime = Math.max(b.t, ...halts.filter(h => b.t >= h.start && b.t < h.end).map(h => h.end));
    }
    day.high = Math.max(...day.bars.map(b => b.h));
    day.low = Math.min(...day.bars.map(b => b.l));
    day.previousHigh = previousDay?.high;
    day.previousLow = previousDay?.low;
    const buckets = new Map();
    for (const b of day.bars) {
      const slot = Math.floor((b.t - day.openTime) / FIVE);
      if (!buckets.has(slot)) buckets.set(slot, []);
      buckets.get(slot).push(b);
    }
    day.fives = [];
    day.byEnd = new Map();
    day.incompleteEnds = new Set();
    let pivot;
    for (let slot = 0; slot < (day.closeTime - day.openTime) / FIVE; slot++) {
      const bs = buckets.get(slot) || [], t = day.openTime + slot * FIVE;
      if (bs.length !== 5 || bs.some((b, i) => b.t !== t + i * MINUTE)) {
        day.incompleteEnds.add(t + FIVE);
        // Never confirm a pivot across a halt or incomplete signal candle.
        pivot = undefined;
        continue;
      }
      const j = day.fives.length, p = j - rules.pivot_left_right - 1;
      const n = rules.pivot_left_right;
      if (p >= n) {
        const neighbors = day.fives.slice(p - n, p + n + 1);
        if (neighbors.at(-1).end === t &&
            neighbors.every((b, i) => i === 0 || b.t === neighbors[i - 1].t + FIVE) &&
            neighbors.every(b => b === day.fives[p] || b.h < day.fives[p].h)) pivot = day.fives[p].h;
      }
      const b = { t, end: t + FIVE, o: bs[0].o, h: Math.max(...bs.map(x => x.h)),
        l: Math.min(...bs.map(x => x.l)), c: bs.at(-1).c,
        pivot, atr: ranges.length === rules.atr_length ? ranges.reduce((a, v) => a + v, 0) / ranges.length : null };
      ranges.push(previousClose === undefined ? b.h - b.l :
        Math.max(b.h - b.l, Math.abs(b.h - previousClose), Math.abs(b.l - previousClose)));
      if (ranges.length > rules.atr_length) ranges.shift();
      previousClose = b.c;
      day.fives.push(b); day.byEnd.set(b.end, j);
    }
    previousDay = day;
  }
  return days;
}

function makeOrder(setup, entry, type, activeAt, expires, rules) {
  const stop = setup.low - rules.tick_buffer, target = setup.target;
  if (!(entry > stop && target > entry && (target - entry) / (entry - stop) >= rules.minimum_reward_risk)) return null;
  return { type, limit: entry, stop, target, sweepLow: setup.low, activeAt, expires };
}

function advance(setup, day, j, variant, rules = RULES) {
  const b = day.fives[j];
  let order = null;
  if (setup && (b.l < setup.low || (setup.confirmed === undefined && b.end > setup.expires))) setup = null;
  if (setup && variant !== 'sweep') {
    if (setup.confirmed !== undefined && j === setup.confirmed + 1) {
      const first = day.fives[j - 2], middle = day.fives[j - 1];
      if (first && first.t + FIVE === middle.t && middle.t + FIVE === b.t &&
          b.l - first.h >= rules.minimum_gap) {
        const entry = first.h + rules.gap_fraction * (b.l - first.h);
        order = makeOrder(setup, entry, 'limit', b.end, b.end + rules.retest_bars * FIVE, rules);
      }
      setup = null;
    } else if (setup.confirmed === undefined && b.c > setup.pivot && b.atr > 0 &&
        b.c - b.o >= rules.body_atr_multiple * b.atr) {
      setup.confirmed = j;
      if (variant === 'confirmed') {
        order = makeOrder(setup, b.c, 'market', b.end, b.end + MINUTE, rules);
        setup = null;
      }
    }
  }
  if (!setup && !order && b.l < day.previousLow && b.c > day.previousLow &&
      (variant === 'sweep' || b.pivot !== undefined)) {
    setup = { low: b.l, target: day.previousHigh, pivot: b.pivot,
      expires: b.end + rules.confirmation_bars * FIVE };
    if (variant === 'sweep') {
      order = makeOrder(setup, b.c, 'market', b.end, b.end + MINUTE, rules);
      setup = null;
    }
  }
  return { setup, order };
}

function tryEntry(order, b, cash, cost, rules = RULES) {
  const time = b.executionTime ?? b.t;
  if (time < order.activeAt) return { order };
  if (time >= order.expires || b.o < order.sweepLow || b.o <= order.stop || b.o >= order.target) return { order: null };
  const atOpen = order.type === 'market' || b.o <= order.limit;
  if (!atOpen && b.l > order.limit) return { order };
  const price = atOpen ? b.o : order.limit;
  if ((order.target - price) / (price - order.stop) < rules.minimum_reward_risk) return { order: null };
  const perShareRisk = price - order.stop + cost * (price + order.stop);
  const shares = Math.floor(Math.min(cash * rules.risk_fraction / perShareRisk, cash / (price * (1 + cost))));
  if (!shares) return { order: null };
  return { order: null, position: { shares, entry: price, entryTime: time,
    stop: order.stop, target: order.target, debit: shares * price * (1 + cost),
    plannedRisk: shares * perShareRisk, atOpen } };
}

function exitPrice(position, b, entered = false) {
  if (b.o <= position.stop) return { price: b.o, reason: 'stop_gap', ambiguous: false };
  const stop = b.l <= position.stop, target = b.h >= position.target;
  if (stop) return { price: position.stop, reason: 'stop', ambiguous: target };
  // A target high may precede an intraminute limit entry. Never credit that order.
  if (target && (!entered || position.atOpen)) return { price: position.target, reason: 'target', ambiguous: false };
  return null;
}

function simulate(days, { variant = 'full', costBps = 5, start = RULES.trade_start,
  end = '9999', overrides = {}, keepTrades = false, buyHold = false } = {}) {
  const rules = { ...RULES, ...overrides }, cost = costBps / 10000;
  let cash = rules.starting_cash, position = null, peak = cash, drawdown = 0;
  let equity = cash, marks = 0, sessions = 0, exposure = 0, ambiguities = 0;
  let wins = 0, losses = 0, grossProfit = 0, grossLoss = 0, sumR = 0, count = 0;
  let firstTime, lastTime, year;
  const trades = [], yearly = {};
  const mark = (b, price, time) => {
    equity = cash + (position?.shares || 0) * price;
    peak = Math.max(peak, equity); drawdown = Math.max(drawdown, 1 - equity / peak);
    exposure += position ? position.shares * price / equity : 0;
    marks++; firstTime ??= time; lastTime = time; yearly[year] = equity;
  };
  const close = (b, price, reason) => {
    const credit = position.shares * price * (1 - cost), pnl = credit - position.debit;
    cash += credit; count++; sumR += pnl / position.plannedRisk;
    if (pnl > 0) { wins++; grossProfit += pnl; } else { losses++; grossLoss -= pnl; }
    if (keepTrades) trades.push({ date: iso(b.t).slice(0, 10), entry_time: iso(position.entryTime),
      exit_time: iso(reason === 'session_close' || reason === 'terminal' ? (b.executionTime ?? b.t) :
        (reason === 'stop_gap' ? (b.executionTime ?? b.t) : b.t + MINUTE)),
      entry: position.entry, exit: price, shares: position.shares, stop: position.stop,
      target: position.target, pnl, net_r: pnl / position.plannedRisk, reason });
    position = null;
  };
  const selected = days.filter(d => d.date >= start && d.date < end);
  for (const day of selected) {
    sessions++; year = day.date.slice(0, 4);
    let setup = null, order = null, filled = false, previousMinute;
    for (const b of day.bars) {
      if (previousMinute !== undefined && b.t !== previousMinute + MINUTE) { setup = null; order = null; }
      previousMinute = b.t;
      const terminal = day === selected.at(-1) && b.t === day.closeTime - MINUTE;
      const finalMinute = b.t === day.closeTime - MINUTE;
      if (finalMinute && position && (!buyHold || terminal)) close(b, b.o, buyHold ? 'terminal' : 'session_close');
      if (buyHold) {
        if (!position && marks === 0 && !terminal) {
          const shares = Math.floor(cash / (b.o * (1 + cost)));
          position = { shares, entry: b.o, entryTime: b.executionTime ?? b.t,
            debit: shares * b.o * (1 + cost), plannedRisk: cash };
          cash -= position.debit;
        }
      } else if (!finalMinute) {
        let entered = false;
        if (order && !position) {
          const entry = tryEntry(order, b, cash, cost, rules); order = entry.order;
          if (entry.position) { position = entry.position; cash -= position.debit; filled = true; entered = true; }
        }
        if (position) {
          const exit = exitPrice(position, b, entered);
          if (exit) { ambiguities += Number(exit.ambiguous); close(b, exit.price, exit.reason); }
        }
        if (setup && b.l < setup.low) setup = null;
      }
      mark(b, finalMinute && (!buyHold || terminal) ? b.o : b.c,
        finalMinute && (!buyHold || terminal) ? b.t : b.t + MINUTE);
      if (!buyHold && !filled && !order && !finalMinute) {
        const j = day.byEnd.get(b.t + MINUTE);
        if (j !== undefined) ({ setup, order } = advance(setup, day, j, variant, rules));
      }
      // Cancel signal state at a gap as soon as the next observed minute exposes it.
      if (!buyHold && day.incompleteEnds.has(b.t + MINUTE)) { setup = null; order = null; }
      if (!buyHold && order) order.expires = Math.min(order.expires, day.closeTime - MINUTE);
    }
    assert(buyHold || position === null, 'Overnight position');
  }
  assert(marks > 0);
  let prior = rules.starting_cash;
  const yearlyReturns = Object.fromEntries(Object.entries(yearly).map(([y, value]) => {
    const r = value / prior - 1; prior = value; return [y, r];
  }));
  return { total_return: equity / rules.starting_cash - 1,
    cagr: (equity / rules.starting_cash) ** (252 / sessions) - 1,
    max_drawdown: drawdown, ending_equity: equity, sessions, minute_marks: marks,
    first_mark: iso(firstTime), last_mark: iso(lastTime), trades: count,
    win_rate: count ? wins / count : null, profit_factor: grossLoss ? grossProfit / grossLoss : null,
    net_expectancy_r: count && !buyHold ? sumR / count : null,
    mean_exposure_fraction: exposure / marks, ambiguous_stop_target_minutes: ambiguities,
    yearly_returns: yearlyReturns, ...(keepTrades ? { ledger: trades } : {}) };
}

function run(root = ROOT) {
  const source = path.join(root, 'runs/qqq-1m-20160223-20261023');
  const bytes = fs.readFileSync(path.join(source, 'bars.csv'));
  const coverage = JSON.parse(fs.readFileSync(path.join(source, 'coverage.json'), 'utf8'));
  const provenance = JSON.parse(fs.readFileSync(path.join(source, 'provenance.json'), 'utf8'));
  assert(coverage.complete && coverage.missing_count === 0 && coverage.outside_count === 0);
  const sha256 = hash(bytes); assert.equal(sha256, provenance.artifact_checksums['bars.csv']);
  const days = prepare(load(bytes.toString()), coverage, provenance.market_halt_policy.intervals);
  const windows = { combined: { start: RULES.trade_start },
    discovery: { start: RULES.trade_start, end: RULES.validation_start },
    reused_validation: { start: RULES.validation_start } };
  const recipes = [...RULES.variants.map(name => ({ name, variant: name })),
    ...RULES.sensitivity.map(s => ({ name: s.name, variant: 'full', overrides: s })),
    { name: 'buy_hold', buyHold: true }];
  const results = {};
  for (const recipe of recipes) {
    results[recipe.name] = {};
    for (const costBps of RULES.cost_bps_per_side) {
      results[recipe.name][costBps] = Object.fromEntries(Object.entries(windows).map(([window, limits]) =>
        [window, simulate(days, { ...recipe, ...limits, costBps,
          keepTrades: recipe.name !== 'buy_hold' && RULES.variants.includes(recipe.name) && costBps === 5 && window === 'combined' })]));
    }
  }
  const turtle = JSON.parse(fs.readFileSync(path.join(root, 'research/qqq-turtle-exits-1m-results.json'), 'utf8'));
  assert.equal(turtle.source.sha256, sha256);
  assert.equal(turtle.trade_start.slice(0, 10), RULES.trade_start);
  return { source: { path: path.relative(root, path.join(source, 'bars.csv')), sha256,
    bars: coverage.observed_count, halted_minutes: coverage.halted_count,
    start: days[0].date, end: days.at(-1).date }, rules: RULES,
    rules_sha256: hash(fs.readFileSync(path.join(__dirname, 'qqq-liquidity-sweep-rules.json'))),
    runner_sha256: hash(fs.readFileSync(__filename)), results,
    contextual_turtle_5bps: Object.fromEntries(Object.entries(turtle.strategies).map(([name, r]) =>
      [name, { total_return: r.total_return, cagr: r.cagr, max_drawdown: r.max_drawdown,
        trades: r.closed_trade_count, final_shares: r.final_shares,
        note: 'Cached audited production result. Different sizing/holding periods; final position marked, no forced terminal sale.' }])) };
}

const pct = v => v === null ? '—' : (100 * v).toFixed(2) + '%';
function report(r) {
  const full = r.results.full[5], base = full.combined, validation = full.reused_validation;
  const lines = ['# QQQ liquidity-sweep experiment — October 28, 2026', '',
    `**This predefined version provides no evidence of an edge after costs.** The full setup returns **${pct(base.total_return)}** across **${base.trades} trades** at 5bps per side; reused validation returns **${pct(validation.total_return)}** across **${validation.trades} trades**, with ${pct(validation.win_rate)} wins. Gross combined return is ${pct(r.results.full[0].combined.total_return)}; at 10bps it is ${pct(r.results.full[10].combined.total_return)}. Costs erase the small gross gain, and the validation sample is too small for a strong conclusion.`, '',
    'Video: [TradeIQ liquidity sweep](https://www.youtube.com/watch?v=l27wF-EvDkw&t=335s).', '',
    'Rules frozen before first data run in `qqq-liquidity-sweep-rules.json`. This is one explicit interpretation of discretionary examples, implemented as an isolated research simulator.', '',
    `Source: ${r.source.path}; SHA-256 \`${r.source.sha256}\`. ${r.source.bars.toLocaleString('en-US')} SIP one-minute bars, ${r.source.start}–${r.source.end}. Adjusted research prices, regular sessions; zero unknown missing bars, ${r.source.halted_minutes} documented halted minutes.`, '',
    'Trading starts 2016-05-11, matching the cached Turtle comparison. Discovery ends 2024-12-31. Reused validation starts 2025-01-02; this period has been examined in earlier experiments and is not unseen evidence. Each window starts with $25,000 cash. No ranking, selection, or parameter tuning.', '',
    '## Frozen model', '',
    ...Object.entries(RULES.definitions).map(([k, v]) => `- **${k}:** ${v}`), '',
    'The full rule requires a same-session 2-left/2-right swing high confirmed before the sweep. Five-minute ATR uses only prior completed candles. First 14 aggregate candles warm ATR. Incomplete five-minute buckets are omitted; setup state is canceled at gaps. Halt reopening prices are available no earlier than the verified reopening time.', '',
    '## Base results at 5bps per side', '',
    '| Rule | Combined return | CAGR | Minute-sampled drawdown | Trades | Win rate | Net R/trade | Discovery return | Reused validation return |',
    '|---|---:|---:|---:|---:|---:|---:|---:|---:|'];
  for (const name of [...RULES.variants, 'buy_hold']) {
    const s = r.results[name][5], c = s.combined;
    lines.push(`| ${name} | ${pct(c.total_return)} | ${pct(c.cagr)} | ${pct(c.max_drawdown)} | ${c.trades} | ${pct(c.win_rate)} | ${c.net_expectancy_r?.toFixed(3) ?? '—'} | ${pct(s.discovery.total_return)} | ${pct(s.reused_validation.total_return)} |`);
  }
  lines.push('', '## Predefined sensitivity and cost cases', '',
    'All rows below use the full rule except the separately named ablations and benchmark. Sensitivity changes one parameter at a time; the base is body/ATR 1, gap fraction 0.5, retest expiry six five-minute bars.', '',
    '| Rule | Combined return 0bps | 5bps | 10bps | Reused validation trades at 5bps | Reused validation net R/trade |',
    '|---|---:|---:|---:|---:|---:|');
  for (const [name, costs] of Object.entries(r.results)) lines.push(`| ${name} | ${pct(costs[0].combined.total_return)} | ${pct(costs[5].combined.total_return)} | ${pct(costs[10].combined.total_return)} | ${costs[5].reused_validation.trades} | ${costs[5].reused_validation.net_expectancy_r?.toFixed(3) ?? '—'} |`);
  lines.push('', '## Contextual production Turtle comparison', '',
    'Same archive, trading start and 5bps setting. These cached audited results use available-cash Turtle sizing, overnight holdings, and final-close marks. They are not matched-risk or identical-terminal-execution comparisons.', '',
    '| Rule | Combined return | CAGR | Minute-sampled drawdown | Closed trades |', '|---|---:|---:|---:|---:|');
  for (const [name, s] of Object.entries(r.contextual_turtle_5bps)) lines.push(`| Turtle ${name} | ${pct(s.total_return)} | ${pct(s.cagr)} | ${pct(s.max_drawdown)} | ${s.trades} |`);
  lines.push('', '## Interpretation and limits', '',
    'Return and drawdown must be read alongside exposure and sample size. Sweep positions risk at most 0.5% planned capital per trade, capped by cash; buy-and-hold invests all available cash. No leverage, no shorting, no reinvested cash yield, no taxes, no volume participation model or actual bid/ask history. Cost cases debit 0/5/10bps of notional per side, combining assumed spread/slippage/fees; costs do not move a limit fill above its price. Stop gaps can exceed planned risk.', '',
    'Limit fills assume sufficient liquidity when touched. If a limit is filled during a minute, a same-minute target is ignored unless entry occurred at open; any stop touch wins. Stop/target ambiguity counts and minute-close exposure/drawdown are in JSON. Execution inside a minute remains uncertain. Daily positions liquidate at the final scheduled minute open, including early closes. Buy-and-hold liquidates at the same clock on the window’s final day.', '',
    'Prior-day levels are an objective subset of the video: no equal-level clustering, higher-timeframe trend filter, Asian-session levels or discretionary setup selection. Price patterns cannot identify institutional intent. This retrospective experiment cannot establish a durable edge or statistical significance. One independent code-review agent using inherited model/settings reviewed timing, execution and accounting. Review found an opening-below-sweep cancellation defect; it was fixed with regression coverage before the first historical run.', '',
    '## Reproduce', '', '```sh', 'node --test research/qqq-liquidity-sweep.test.cjs', 'node research/qqq-liquidity-sweep.cjs', '```', '',
    'Full window/year/cost metrics, frozen rules, source/rules/runner hashes and combined 5bps trade ledgers: `qqq-liquidity-sweep-results.json`.', '');
  return lines.join('\n');
}

if (require.main === module) {
  const result = run();
  fs.writeFileSync(path.join(__dirname, 'qqq-liquidity-sweep-results.json'), JSON.stringify(result, null, 2) + '\n');
  fs.writeFileSync(path.join(__dirname, 'qqq-liquidity-sweep-report.md'), report(result));
  console.log(JSON.stringify(Object.fromEntries(Object.entries(result.results).map(([name, costs]) =>
    [name, { combined: costs[5].combined.total_return, validation: costs[5].reused_validation.total_return,
      trades: costs[5].combined.trades }]))));
}
module.exports = { prepare, advance, makeOrder, tryEntry, exitPrice, simulate, run, report, RULES };
