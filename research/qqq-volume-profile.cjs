'use strict';

// Research simulator, deliberately separate from the production order engine.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const BASE = Object.freeze({ rows: 400, allocation: 'uniform', valueFraction: 0.7,
  confirmations: 1, stopBuffer: 0.1 });
const COSTS = [0, 1, 5, 10];
const SPLIT = '2022-01-01';

function config(options = {}) {
  const c = { ...BASE, ...options };
  assert(Number.isInteger(c.rows) && c.rows >= 2 && c.rows <= 10000, 'Invalid rows');
  assert(['uniform', 'typical'].includes(c.allocation), 'Invalid allocation');
  assert(c.valueFraction > 0 && c.valueFraction <= 1, 'Invalid value fraction');
  assert(Number.isInteger(c.confirmations) && c.confirmations >= 1, 'Invalid confirmations');
  assert(Number.isFinite(c.stopBuffer) && c.stopBuffer > 0, 'Invalid stop buffer');
  return c;
}

function validateBar(b) {
  assert([b.o, b.h, b.l, b.c, b.v].every(Number.isFinite), 'Invalid OHLCV');
  assert(b.l > 0 && b.v >= 0 && b.l <= Math.min(b.o, b.c) &&
    b.h >= Math.max(b.o, b.c), 'Invalid OHLCV');
}

function load(csv) {
  const lines = csv.trim().split(/\r?\n/);
  assert.equal(lines.shift(), 'symbol,timestamp,open,high,low,close,volume', 'Invalid CSV header');
  const days = [];
  let previous = -Infinity;
  for (const line of lines) {
    const [symbol, stamp, ...fields] = line.split(',');
    assert.equal(symbol, 'QQQ', 'Expected QQQ');
    assert.equal(fields.length, 5, 'Invalid CSV width');
    assert(fields.every(x => x.trim() !== ''), 'Empty OHLCV field');
    const t = Date.parse(stamp);
    assert(Number.isFinite(t) && t % 60000 === 0 && t > previous, 'Invalid timestamp/order');
    const [o, h, l, c, v] = fields.map(Number);
    const bar = { t, o, h, l, c, v };
    validateBar(bar);
    previous = t;
    const date = new Date(t).toISOString().slice(0, 10);
    let day = days.at(-1);
    if (!day || day.date !== date) { day = { date, bars: [] }; days.push(day); }
    day.bars.push(bar);
  }
  assert(days.length > 0, 'Empty dataset');
  return days;
}

function readDataset(directory) {
  const provenance = JSON.parse(fs.readFileSync(path.join(directory, 'provenance.json'), 'utf8'));
  const checksums = {};
  const contents = {};
  for (const name of ['bars.csv', 'coverage.json', 'manifest.toml']) {
    const bytes = fs.readFileSync(path.join(directory, name));
    checksums[name] = crypto.createHash('sha256').update(bytes).digest('hex');
    assert.equal(checksums[name], provenance.artifact_checksums?.[name], `Checksum mismatch: ${name}`);
    contents[name] = bytes.toString('utf8');
  }
  const manifest = contents['manifest.toml'];
  for (const expression of [/^feed\s*=\s*"sip"\s*$/m, /^adjustment\s*=\s*"all"\s*$/m,
    /^interval_minutes\s*=\s*1\s*$/m,
    /^coverage\s*=\s*"regular"\s*$/m, /^symbols\s*=\s*\["QQQ"\]\s*$/m,
    /^timestamp_convention\s*=\s*"bar_open"\s*$/m,
    /^price_basis\s*=\s*"adjusted_research"\s*$/m, /^synthetic\s*=\s*false\s*$/m]) {
    assert(expression.test(manifest), 'Expected SIP QQQ one-minute regular all-adjusted research archive');
  }
  const coverage = JSON.parse(contents['coverage.json']);
  assert(coverage.complete && coverage.missing_count === 0 && coverage.outside_count === 0,
    'Incomplete coverage');
  const days = load(contents['bars.csv']);
  assert.equal(days.reduce((n, d) => n + d.bars.length, 0), coverage.observed_count,
    'Observed row count mismatch');
  assert.equal(days.length, coverage.sessions.length, 'Session count mismatch');
  for (let i = 0; i < days.length; i++) {
    const expected = coverage.sessions[i];
    assert(expected.symbol === 'QQQ' && expected.day === days[i].date &&
      expected.missing_count === 0 && expected.observed_count === days[i].bars.length &&
      expected.expected_count === expected.observed_count, 'Session coverage mismatch');
  }
  return { days, source: { checksums, dataHash: provenance.data_hash,
    start: days[0].date, end: days.at(-1).date, sessions: days.length,
    bars: coverage.observed_count, haltedBarsExcluded: coverage.halted_count,
    feed: 'sip', intervalMinutes: 1, coverage: 'regular', priceBasis: 'adjusted_research' } };
}

function buildProfile(bars, options = {}) {
  const c = config(options);
  assert(bars.length > 0, 'Empty profile');
  let low = Infinity, high = -Infinity, total = 0;
  for (const b of bars) {
    validateBar(b);
    low = Math.min(low, b.l); high = Math.max(high, b.h); total += b.v;
  }
  if (high === low || total === 0) return null;
  const width = (high - low) / c.rows;
  const volumes = Array(c.rows).fill(0);
  const bucket = price => Math.max(0, Math.min(c.rows - 1, Math.floor((price - low) / width)));
  for (const b of bars) {
    if (c.allocation === 'typical' || b.h === b.l) {
      volumes[bucket(c.allocation === 'typical' ? (b.h + b.l + b.c) / 3 : b.l)] += b.v;
    } else {
      const first = bucket(b.l), last = bucket(b.h);
      for (let i = first; i <= last; i++) {
        const overlap = Math.max(0, Math.min(b.h, low + (i + 1) * width) -
          Math.max(b.l, low + i * width));
        volumes[i] += b.v * overlap / (b.h - b.l);
      }
    }
  }
  // Equal POC volumes select the lower price. Equal adjacent volumes expand down first.
  let poc = 0;
  for (let i = 1; i < volumes.length; i++) if (volumes[i] > volumes[poc]) poc = i;
  let first = poc, last = poc, valueVolume = volumes[poc];
  while (valueVolume < total * c.valueFraction && (first > 0 || last < c.rows - 1)) {
    const down = first > 0 ? volumes[first - 1] : -1;
    const up = last < c.rows - 1 ? volumes[last + 1] : -1;
    if (down >= up) valueVolume += volumes[--first];
    else valueVolume += volumes[++last];
  }
  return { low, high, width, totalVolume: total, volumes, pocIndex: poc,
    poc: low + (poc + 0.5) * width, val: low + first * width,
    vah: low + (last + 1) * width, valueVolume, valueFraction: valueVolume / total };
}

function simulateSession(day, profile, options = {}) {
  const c = config(options);
  const bars = day.bars;
  assert(bars.length > 0, 'Empty session');
  if (!profile) return { skip: 'no_profile' };
  const side = bars[0].o < profile.val ? 1 : bars[0].o > profile.vah ? -1 : 0;
  if (!side) return { skip: 'open_inside' };
  let consecutive = 0, signalIndex = -1;
  for (let i = 0; i < bars.length; i++) {
    const inside = bars[i].c > profile.val && bars[i].c < profile.vah;
    consecutive = inside ? consecutive + 1 : 0;
    if (consecutive >= c.confirmations) { signalIndex = i; break; }
  }
  if (signalIndex < 0) return { skip: 'no_acceptance' };
  const entryIndex = signalIndex + 1;
  if (entryIndex >= bars.length - 1) return { skip: 'late_signal' };
  const entry = bars[entryIndex].o;
  const buffer = (profile.high - profile.low) * c.stopBuffer;
  const stop = side === 1 ? profile.val - buffer : profile.vah + buffer;
  const target = side === 1 ? profile.vah : profile.val;
  if (side * (entry - stop) <= 0) return { skip: 'entry_beyond_stop' };
  if (side * (target - entry) <= 0) return { skip: 'entry_beyond_target' };
  // Outcome diagnostic only: ignore the trade's stop, but retain the same tradable horizon.
  // Never use this future touch information to select or execute a trade.
  const targetTouchedByEod = bars.slice(entryIndex).some((b, offset) => {
    if (entryIndex + offset === bars.length - 1) return side * (b.o - target) >= 0;
    return side === 1 ? b.h >= target : b.l <= target;
  });
  let exit, exitIndex, reason, ambiguous = false;
  for (let i = entryIndex; i < bars.length; i++) {
    const b = bars[i];
    // Liquidation is a pre-scheduled market order at the final observed bar's open.
    if (i === bars.length - 1) { exit = b.o; exitIndex = i; reason = 'eod'; break; }
    if (side * (b.o - stop) <= 0) { exit = b.o; exitIndex = i; reason = 'stop'; break; }
    if (side * (b.o - target) >= 0) { exit = target; exitIndex = i; reason = 'target'; break; }
    const stopped = side === 1 ? b.l <= stop : b.h >= stop;
    const targeted = side === 1 ? b.h >= target : b.l <= target;
    if (stopped || targeted) {
      ambiguous = stopped && targeted;
      exit = stopped ? stop : target; exitIndex = i; reason = stopped ? 'stop' : 'target'; break;
    }
  }
  return { trade: { date: day.date, side, signalTime: bars[signalIndex].t + 60000,
    entryTime: bars[entryIndex].t, exitTime: bars[exitIndex].t, entry, exit, stop, target,
    reason, ambiguous, targetTouchedByEod, entryIndex, exitIndex, baselineExit: bars.at(-1).o,
    baselineExitTime: bars.at(-1).t, profileLow: profile.low, profileHigh: profile.high,
    val: profile.val, vah: profile.vah, poc: profile.poc } };
}

function simulate(days, options = {}) {
  const c = config(options), trades = [], skips = {};
  for (let i = 1; i < days.length; i++) {
    const profile = buildProfile(days[i - 1].bars, c);
    const result = simulateSession(days[i], profile, c);
    if (result.trade) trades.push({ ...result.trade, profileDate: days[i - 1].date });
    else skips[result.skip] = (skips[result.skip] || 0) + 1;
  }
  return { trades, skips, evaluatedSessions: Math.max(0, days.length - 1) };
}

function tradeReturn(trade, costBps = 5, baseline = false) {
  assert(Number.isFinite(costBps) && costBps >= 0 && costBps < 10000, 'Invalid cost');
  const cost = costBps / 10000;
  const entryFill = trade.entry * (1 + trade.side * cost);
  const exitFill = (baseline ? trade.baselineExit : trade.exit) * (1 - trade.side * cost);
  // Fractional quantity = starting equity / adverse entry fill, for either direction.
  const net = trade.side * (exitFill - entryFill) / entryFill;
  const initialRisk = Math.abs(entryFill - trade.stop);
  return { net, netBps: net * 10000,
    netR: initialRisk > 0 ? trade.side * (exitFill - entryFill) / initialRisk : null,
    entryFill, exitFill };
}

function stats(trades, costBps, baseline = false) {
  let equity = 1, peak = 1, maxDrawdown = 0, netSum = 0, wins = 0;
  let positive = 0, negative = 0, riskSum = 0, riskCount = 0;
  const exits = { target: 0, stop: 0, eod: 0 };
  for (const t of trades) {
    const r = tradeReturn(t, costBps, baseline);
    assert(r.net > -1, 'Unlevered account exhausted');
    netSum += r.netBps; wins += r.net > 0 ? 1 : 0;
    if (r.net > 0) positive += r.net; else negative -= r.net;
    if (r.netR !== null) { riskSum += r.netR; riskCount++; }
    equity *= 1 + r.net; peak = Math.max(peak, equity);
    maxDrawdown = Math.max(maxDrawdown, 1 - equity / peak);
    exits[baseline ? 'eod' : t.reason]++;
  }
  return { trades: trades.length, winPct: trades.length ? 100 * wins / trades.length : null,
    meanNetBps: trades.length ? netSum / trades.length : null,
    meanNetR: riskCount ? riskSum / riskCount : null,
    profitFactor: negative > 0 ? positive / negative : null,
    compoundedReturnPct: (equity - 1) * 100,
    sessionCloseSampledMaxDrawdownPct: maxDrawdown * 100, exits,
    targetBeforeStopPct: !baseline && trades.length ? 100 * exits.target / trades.length : null,
    targetTouchedByEodPct: trades.length ?
      100 * trades.filter(t => t.targetTouchedByEod).length / trades.length : null,
    ambiguousBars: baseline ? 0 : trades.filter(t => t.ambiguous).length };
}

function summarize(trades, baseline = false) {
  const summary = {};
  for (const cost of COSTS) summary[cost] = stats(trades, cost, baseline);
  return summary;
}

function study(days) {
  const variants = [
    ['base', {}], ['rows100', { rows: 100 }], ['rows200', { rows: 200 }],
    ['confirmation2', { confirmations: 2 }], ['typical_price', { allocation: 'typical' }],
    ['stop_buffer25pct', { stopBuffer: 0.25 }],
  ];
  const results = [];
  let baseTrades;
  for (const [name, options] of variants) {
    const result = simulate(days, options);
    const discovery = result.trades.filter(t => t.date < SPLIT);
    const evaluation = result.trades.filter(t => t.date >= SPLIT);
    const byYear = {};
    for (const year of [...new Set(days.slice(1).map(d => d.date.slice(0, 4)))]) {
      byYear[year] = summarize(result.trades.filter(t => t.date.startsWith(year)));
    }
    results.push({ name, config: config(options), evaluatedSessions: result.evaluatedSessions,
      skips: result.skips, all: summarize(result.trades), discovery: summarize(discovery),
      evaluation: summarize(evaluation), long: summarize(result.trades.filter(t => t.side === 1)),
      short: summarize(result.trades.filter(t => t.side === -1)), byYear,
      sameEntryHoldToEod: { all: summarize(result.trades, true),
        discovery: summarize(discovery, true), evaluation: summarize(evaluation, true) } });
    if (name === 'base') baseTrades = result.trades.map(t => ({ ...t,
      costResults: Object.fromEntries(COSTS.map(cost => [cost, tradeReturn(t, cost)])),
      baselineCostResults: Object.fromEntries(COSTS.map(cost => [cost, tradeReturn(t, cost, true)])) }));
  }
  const exampleTrade = baseTrades.find(t => t.date >= SPLIT) || baseTrades[0];
  const exampleIndex = exampleTrade ? days.findIndex(d => d.date === exampleTrade.date) : -1;
  const example = exampleIndex > 0 ? { trade: exampleTrade,
    previousSessionDate: days[exampleIndex - 1].date,
    previousProfile: buildProfile(days[exampleIndex - 1].bars),
    sessionBars: days[exampleIndex].bars } : null;
  return { schemaVersion: 1, chronologicalSplit: SPLIT, costsBpsPerSide: COSTS,
    rules: {
      hypothesis: 'Prior-session 70% volume value-area re-entry toward opposite edge',
      sourceVideo: 'https://www.youtube.com/watch?v=Nm5wdUvy0to&t=1560',
      volumeApproximation: 'Uniform volume over each minute low-high; sensitivity places volume at (H+L+C)/3',
      profile: 'Completed previous regular session only; 400 equal-price bins; contiguous 70% from POC',
      ties: 'Lowest-price POC; expand down first for equal adjacent volumes',
      acceptance: 'Open strictly outside prior VA; consecutive closes strictly inside; first acceptance only',
      entry: 'Next observed bar open; skip final-bar entries or opens at/beyond stop or target; max one trade/day',
      stop: 'Beyond entry-side VA boundary by 10% of previous-session full high-low range',
      target: 'Opposite prior-session VA boundary; limit gaps receive target, stops gap at adverse open',
      ambiguity: 'Stop first when same minute touches both; final observed bar open liquidation precedes its range',
      sizing: 'Fractional 1x starting-equity notional per trade, no leverage/overnight; short quantity uses entry proceeds basis',
      costs: 'Adverse entry/exit fills by 0/1/5/10 bps each side; no extra commission',
      netR: 'Net price P&L divided by absolute adverse-entry-fill to original stop distance',
      drawdown: 'Realized session-close sampled equity, excludes intraday unrealized drawdown',
      baseline: 'Same accepted entries and direction, exit final bar open without stop/target',
      targetTouchedByEod: 'Diagnostic ignores stops: target touched from actual entry through penultimate bar range or final bar OPEN; denominator all accepted trades',
      variants: 'Five predeclared one-at-a-time sensitivities; no winner selected',
    }, warnings: [
      'Minute OHLCV cannot recover true traded volume at each price; profiles are approximations.',
      'Stop buffer, acceptance count, binning and exits are explicit research choices, not fully specified by the video.',
      'Both chronological windows have been explored in earlier QQQ research; evaluation is not a pristine holdout.',
      'Vendor-adjusted research data are not point-in-time account reconstruction; no dividend cash/share transformations.',
      'No historical spread, order book, queue, latency, impact, fees beyond stated bps, taxes, cash yield, borrow availability/cost or financing.',
      'Fractional unlevered notional model is not the production broker simulator or a claim of executable fills.',
      'Baseline conditions on strategy entry eligibility; it is not buy-and-hold or a randomized control.',
      'Profit factor is null when no losing trades exist; empty-trade means and rates are null.',
    ], variants: results, baseTrades, example };
}

function main(args = process.argv.slice(2)) {
  let directory = path.join(ROOT, 'runs/qqq-1m-20160223-20261023'), output;
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--help') {
      console.log('node research/qqq-volume-profile.cjs [--source ARCHIVE_DIR] [--output RESULTS_JSON]');
      return;
    }
    assert(['--source', '--output'].includes(args[i]) && args[i + 1], 'Unknown/missing CLI argument');
    if (args[i] === '--source') directory = path.resolve(args[++i]);
    else output = path.resolve(args[++i]);
  }
  const { days, source } = readDataset(directory);
  const result = { source, ...study(days) };
  const json = JSON.stringify(result, null, 2) + '\n';
  if (output) { fs.writeFileSync(output, json); console.log(`Wrote ${output}`); }
  else process.stdout.write(json);
}

if (require.main === module) main();
module.exports = { BASE, COSTS, SPLIT, config, load, readDataset, buildProfile,
  simulateSession, simulate, tradeReturn, stats, summarize, study };
