// Offline exploratory study. Run: node research/qqq-velocity.cjs
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const ROOT = path.resolve(__dirname, '..');
const HORIZONS = [1, 2, 3, 5, 10, 20];
const SPLIT = '2025-01-01';

function load(csv) {
  const days = [];
  let day, previous = -Infinity;
  for (const line of csv.trim().split(/\r?\n/).slice(1)) {
    const [symbol, stamp, ...fields] = line.split(',');
    const t = Date.parse(stamp), [o, h, l, c, v] = fields.map(Number);
    assert(symbol === 'QQQ' && t > previous && fields.length === 5);
    assert([o, h, l, c, v].every(Number.isFinite) && l > 0 && v >= 0);
    assert(l <= Math.min(o, c) && h >= Math.max(o, c) && h >= l);
    previous = t;
    // Regular US sessions never cross a UTC date boundary.
    const date = stamp.slice(0, 10);
    if (!day || day.date !== date) { day = { date, bars: [] }; days.push(day); }
    day.bars.push({ t, o, h, l, c });
  }
  return days;
}

function aggregate(days, minutes) {
  const out = [];
  for (const day of days) {
    const start = day.bars[0].t;
    // Anchor to scheduled opening; all archive sessions include the opening slot.
    // Incomplete buckets (halts) are omitted, never concatenated across gaps.
    const buckets = new Map();
    for (const b of day.bars) {
      const slot = minutes === 390 ? 0 : Math.floor((b.t - start) / (minutes * 60000));
      if (!buckets.has(slot)) buckets.set(slot, []);
      buckets.get(slot).push(b);
    }
    for (const [slot, bs] of buckets) {
      const complete = minutes === 390 || (bs.length === minutes &&
        bs.every((b, i) => b.t === start + (slot * minutes + i) * 60000));
      if (!complete) continue;
      out.push({ date: day.date, t: bs[0].t, o: bs[0].o,
        c: bs.at(-1).c, slot, r: Math.log(bs.at(-1).c / bs[0].o) });
    }
  }
  // A scale known BEFORE the signal: RMS of previous 60 candle body returns.
  let squares = 0;
  for (let i = 0; i < out.length; i++) {
    const b = out[i];
    b.scale = i >= 60 ? Math.sqrt(Math.max(0, squares) / 60) : null;
    b.z = b.scale > 0 ? b.r / b.scale : null;
    squares += b.r * b.r;
    if (i >= 60) squares -= out[i - 60].r ** 2;
  }
  return out;
}

function eligible(bars, i, h, minutes) {
  if (i + h + 1 >= bars.length) return false;
  if (minutes === 390) return true; // Daily positions may span sessions/weekends.
  return bars[i + h + 1].date === bars[i].date &&
    bars[i + h + 1].t - bars[i].t === (h + 1) * minutes * 60000;
}

function netReturn(entry, exit, side, costBps) {
  const ratio = exit / entry, cost = costBps / 10000;
  // One unit of entry notional. Costs charged on entry AND exit notionals.
  return side * (ratio - 1) - cost * (1 + ratio);
}

function stats(values) {
  const n = values.length;
  if (!n) return { n: 0, meanBps: null, medianBps: null, winPct: null };
  const xs = values.map(x => x.r).sort((a, b) => a - b);
  return { n, meanBps: xs.reduce((a, b) => a + b, 0) / n * 10000,
    medianBps: (xs[Math.floor((n - 1) / 2)] + xs[Math.floor(n / 2)]) / 2 * 10000,
    winPct: xs.filter(x => x > 0).length / n * 100 };
}

function runs(bars, threshold, direction, minutes, period) {
  const lengths = [];
  let censored = 0, same = 0, tested = 0, baselineSame = 0, baselineTested = 0;
  for (let i = 60; i < bars.length - 1; i++) {
    const b = bars[i];
    if ((b.date < SPLIT ? 'discovery' : 'validation') !== period) continue;
    const continuous = j => j < bars.length &&
      (bars[j].date < SPLIT ? 'discovery' : 'validation') === period &&
      (minutes === 390 || (bars[j].date === b.date &&
        bars[j].t - bars[j - 1].t === minutes * 60000));
    if (continuous(i + 1)) {
      baselineTested++;
      if (bars[i + 1].r * direction > 0) baselineSame++;
    }
    if (b.z === null || b.z * direction < threshold) continue;
    if (!continuous(i + 1)) { censored++; continue; }
    tested++;
    if (bars[i + 1].r * direction > 0) same++;
    let j = i + 1, k = 0;
    while (continuous(j) && bars[j].r * direction > 0) { k++; j++; }
    if (!continuous(j)) censored++;
    else lengths.push(k);
  }
  const s = stats(lengths.map(r => ({ r: r / 10000 })));
  return { tested, nextSamePct: tested ? same / tested * 100 : null,
    unconditionalDirectionPct: baselineTested ? baselineSame / baselineTested * 100 : null,
    completeRuns: lengths.length, boundaryCensored: censored,
    medianAdditionalCandles: s.medianBps,
    atLeast2Pct: lengths.length ? lengths.filter(x => x >= 2).length / lengths.length * 100 : null,
    atLeast3Pct: lengths.length ? lengths.filter(x => x >= 3).length / lengths.length * 100 : null };
}

function trades(bars, minutes, threshold, direction, h, mode, period, cost) {
  const result = [];
  let available = 0;
  for (let i = 60; i < bars.length - h - 1; i++) {
    const b = bars[i];
    if ((b.date < SPLIT ? 'discovery' : 'validation') !== period ||
        (bars[i + h + 1].date < SPLIT ? 'discovery' : 'validation') !== period ||
        i < available || b.z === null || b.z * direction < threshold ||
        !eligible(bars, i, h, minutes)) continue;
    const side = direction * mode;
    result.push({ date: bars[i + 1].date,
      r: netReturn(bars[i + 1].o, bars[i + h + 1].o, side, cost) });
    // A new signal can complete on the exit candle, followed by next-open entry.
    available = i + h + 1;
  }
  return result;
}

function diagnostics(bars, minutes, threshold, direction, h, period) {
  const events = [], baseline = [];
  for (let i = 60; i < bars.length - h - 1; i++) {
    const b = bars[i];
    if ((b.date < SPLIT ? 'discovery' : 'validation') !== period ||
        (bars[i + h + 1].date < SPLIT ? 'discovery' : 'validation') !== period ||
        !eligible(bars, i, h, minutes)) continue;
    const r = netReturn(bars[i + 1].o, bars[i + h + 1].o, direction, 0);
    baseline.push({ r });
    if (b.z !== null && b.z * direction >= threshold) events.push({ r });
  }
  return { ...stats(events), baselineMeanBps: stats(baseline).meanBps };
}

// Circular moving-block bootstrap of 20-session sums AND counts. Includes zero-
// trade sessions. For a chosen recipe only, not a multiple-testing correction.
function bootstrap(values, dates, seed = 71623, reps = 1000) {
  if (!values.length) return null;
  const groups = new Map(dates.map(d => [d, [0, 0]]));
  for (const x of values) { const a = groups.get(x.date); a[0] += x.r; a[1]++; }
  const g = [...groups.values()], means = [];
  const random = () => { seed ^= seed << 13; seed ^= seed >>> 17; seed ^= seed << 5;
    return (seed >>> 0) / 4294967296; };
  for (let b = 0; b < reps; b++) {
    let s = 0, n = 0, count = 0;
    while (count < g.length) {
      const start = Math.floor(random() * g.length);
      for (let k = 0; k < 20 && count < g.length; k++, count++) {
        const x = g[(start + k) % g.length]; s += x[0]; n += x[1];
      }
    }
    if (n) means.push(s / n * 10000);
  }
  means.sort((a, b) => a - b);
  return [means[Math.floor(means.length * .025)], means[Math.floor(means.length * .975)]];
}

function study(days) {
  const grid = [], diagnostic = [], streaks = [];
  for (const minutes of [1, 5, 15, 30, 390]) {
    const bars = aggregate(days, minutes);
    for (const threshold of [2, 3]) for (const direction of [1, -1]) {
      for (const period of ['discovery', 'validation']) {
        streaks.push({ minutes, threshold, direction, period,
          ...runs(bars, threshold, direction, minutes, period) });
        for (const h of HORIZONS) diagnostic.push({ minutes, threshold, direction, h, period,
          ...diagnostics(bars, minutes, threshold, direction, h, period) });
      }
      for (const h of HORIZONS) for (const mode of [1, -1]) {
        const row = { minutes, threshold, direction, h, mode };
        for (const period of ['discovery', 'validation']) {
          row[period] = {};
          for (const cost of [0, 1, 5, 10]) {
            row[period][cost] = stats(trades(bars, minutes, threshold, direction, h, mode, period, cost));
          }
        }
        grid.push(row);
      }
    }
  }
  // Fixed selection criterion; return/trade is not a portfolio-performance rank.
  const ranked = grid.filter(x => x.discovery[5].n >= 100)
    .sort((a, b) => b.discovery[5].meanBps - a.discovery[5].meanBps);
  const selected = ranked[0];
  const bars = aggregate(days, selected.minutes);
  selected.intervals = {};
  selected.yearly = {};
  for (const period of ['discovery', 'validation']) {
    const ts = trades(bars, selected.minutes, selected.threshold, selected.direction,
      selected.h, selected.mode, period, 5);
    selected.intervals[period] = bootstrap(ts,
      days.filter(d => (d.date < SPLIT ? 'discovery' : 'validation') === period).map(d => d.date));
    for (const year of [...new Set(ts.map(t => t.date.slice(0, 4)))]) {
      selected.yearly[year] = stats(ts.filter(t => t.date.startsWith(year)));
    }
  }
  return { diagnostic, streaks, grid, selected, selectionEligible: ranked.length,
    sufficientBoth: grid.filter(x => x.discovery[5].n >= 100 && x.validation[5].n >= 30).length,
    positiveBothAt5: grid.filter(x => x.discovery[5].n >= 100 && x.validation[5].n >= 30 &&
      x.discovery[5].meanBps > 0 && x.validation[5].meanBps > 0).length };
}

const num = x => x === null || x === undefined ? '—' : x.toFixed(2);
const label = x => `${x.minutes === 390 ? 'daily' : x.minutes + 'm'} ${x.threshold}× ${x.direction > 0 ? 'up' : 'down'}, ${x.mode > 0 ? 'follow' : 'fade'}, hold ${x.h}`;
function report(r) {
  const s = r.selected;
  const lines = ['# QQQ velocity study — October 27, 2026', '',
    '**No dependable fixed candle-count continuation or cost-resilient trading edge established.** Large intraday candles usually stop extending in the same candle-body direction within zero or one additional candle. Only roughly 8–14% of completed validation runs reach three additional same-direction candles. A sequence of red/green bodies is not necessarily a sequence of falling/rising closes.', '',
    `Of ${r.grid.length} tested follow/fade recipes, ${r.sufficientBoth} have at least 100 discovery and 30 validation trades; **${r.positiveBothAt5}** have positive mean net returns in both periods at 5bps per side. The discovery-selected recipe returns ${num(s.discovery[5].meanBps)}bps/trade before 2025, then ${num(s.validation[5].meanBps)}bps/trade in 2025–2026; both uncertainty intervals cross zero.`, '',
    'One-minute gross follow-through is tiny relative to assumed trading costs. Large daily drops show a rebound in this validation sample, but only 19 next-candle observations exist; daily impulses and longer holds need more evidence. No strategy changes or live orders.', '',
    '## Data and definitions', '',
    `QQQ SIP fully adjusted regular-session minute archive: ${r.start}–${r.end}; ${r.bars.toLocaleString()} minute bars, ${r.sessions} sessions. SHA-256: \`${r.sha256}\`.`, '',
    'Candles: 1, 5, 15, 30 minutes and daily. Intraday candles anchored at session opening; incomplete halt buckets dropped, no invented bars. Intraday signal, entry and exit must fit in one session with no missing interval; daily trades cross sessions. Daily candle means session open-to-close, not close-to-close. Signals include green/red candle bodies; overnight gaps and multi-candle acceleration are not tested.', '',
    'Large = absolute log(open-to-close return) at least 2× or 3× RMS of the previous 60 candle body returns, excluding current candle. This is a volatility scale, not a Gaussian significance score. Scale carries across sessions and does not adjust for time-of-day seasonality. The body sign defines direction. Zero-body candles stop streaks.', '',
    'Discovery: before 2025; chronological validation: 2025–2026. Earlier warmup bars excluded. Validation already used in other research; not pristine out-of-sample evidence. All choices are exploratory and this grid increases selection bias.', '',
    '## How long does the move continue?', '',
    '2× impulse threshold. “Next same” uses next candle body sign, not a close-to-close change. Median additional run excludes right-censored session/period/archive boundary runs; boundary counts in JSON. It describes uninterrupted candle colors, not time until a price peak. Signal candle itself is excluded.', '',
    '| Candle | Impulse | Discovery next same | Validation next same | Unconditional validation direction | Validation observations | Median additional candles (complete runs) | Validation run ≥3 (complete runs) |',
    '|---|---|---:|---:|---:|---:|---:|---:|'];
  for (const v of r.streaks.filter(x => x.threshold === 2 && x.period === 'validation')) {
    const d = r.streaks.find(x => x.minutes === v.minutes && x.threshold === 2 && x.direction === v.direction && x.period === 'discovery');
    lines.push(`| ${v.minutes === 390 ? 'Daily' : v.minutes + 'm'} | ${v.direction > 0 ? 'Up' : 'Down'} | ${num(d.nextSamePct)}% | ${num(v.nextSamePct)}% | ${num(v.unconditionalDirectionPct)}% | ${v.tested} | ${num(v.medianAdditionalCandles)} | ${num(v.atLeast3Pct)}% |`);
  }
  lines.push('', '## Follow-through after executable entry', '',
    '2× threshold; signed gross mean return in bps, following impulse direction. Entry next candle open; exit open after H held candles. Negative means reversal. All qualifying events included here, so horizons overlap; these are diagnostics, not a portfolio. Table shows validation; full discovery/validation, sample counts, medians, win rates and unconditional directional baselines in JSON. Baseline is not matched for time of day or regime.', '',
    '| Candle | Impulse | 1 candle | 2 | 3 | 5 | 10 | 20 |', '|---|---|---:|---:|---:|---:|---:|---:|');
  for (const minutes of [1, 5, 15, 30, 390]) for (const direction of [1, -1]) {
    lines.push(`| ${minutes === 390 ? 'Daily' : minutes + 'm'} | ${direction > 0 ? 'Up' : 'Down'} | ${HORIZONS.map(h => num(r.diagnostic.find(x => x.minutes === minutes && x.direction === direction && x.threshold === 2 && x.h === h && x.period === 'validation').meanBps)).join(' | ')} |`);
  }
  lines.push('', '## Can it pay after costs?', '',
    `${r.grid.length} recipes: 5 candle sizes × 2 thresholds × 2 directions × 6 holds × follow/fade. Costs: 0/1/5/10bps per side. 5bps per side roughly requires 10bps gross per round trip. Trades do not overlap within each recipe; independent recipes are not combined. Selection uses highest discovery mean net bps/trade at 5bps/side with at least 100 discovery trades (${r.selectionEligible} eligible). This favors per-trade magnitude and longer holds, not portfolio return or risk-adjusted performance.`, '',
    `Selected: **${label(s)}**. Recipes positive in both periods at 5bps/side with ≥100 discovery and ≥30 validation trades: **${r.positiveBothAt5}/${r.grid.length}**. That count is exploratory, not statistical confirmation.`, '',
    '| Period | Trades | Gross mean bps | Net 1bp/side | Net 5bps/side | Net 10bps/side | Net win rate (5bps) | 95% block-bootstrap interval for mean net bps (5bps) |',
    '|---|---:|---:|---:|---:|---:|---:|---|');
  for (const p of ['discovery', 'validation']) lines.push(`| ${p} | ${s[p][5].n} | ${num(s[p][0].meanBps)} | ${num(s[p][1].meanBps)} | ${num(s[p][5].meanBps)} | ${num(s[p][10].meanBps)} | ${num(s[p][5].winPct)}% | ${s.intervals[p].map(num).join(' to ')} |`);
  lines.push('', 'Intervals use 1,000 deterministic resamples of circular 20-session blocks, retaining zero-trade sessions. They do not correct for recipe selection/multiple testing. Sparse daily impulses give low power.', '',
    '| Selected recipe year | Trades | Mean net bps at 5bps/side |', '|---|---:|---:|');
  for (const [y, v] of Object.entries(s.yearly)) lines.push(`| ${y} | ${v.n} | ${num(v.meanBps)} |`);
  lines.push('', '## Limits and reproduction', '',
    'Results describe QQQ, this history and these definitions. A positive gross mean or >50% continuation is insufficient evidence of a usable edge. Prices are vendor-adjusted, not point-in-time account reconstruction. No observed bid/ask, market impact, latency, taxes, financing, short borrow/availability, cash yield or dividend/share transformations. Short trades especially need borrow/financing analysis. Minute OHLC cannot establish obtainable next-open execution. No stop or profit target, portfolio equity, CAGR or drawdown is modeled; per-trade means do not establish outperforming buy-and-hold. Do not infer a universal X-candle trend duration.', '',
    'Run `node --test research/qqq-velocity.test.cjs`, then `node research/qqq-velocity.cjs`. Outputs: `research/qqq-velocity-results.json` and this report. Existing production strategies remain unchanged.', '');
  return lines.join('\n');
}

if (require.main === module) {
  const dir = path.join(ROOT, 'runs/qqq-1m-20160223-20261023');
  const csv = fs.readFileSync(path.join(dir, 'bars.csv'), 'utf8');
  const sha256 = crypto.createHash('sha256').update(csv).digest('hex');
  const provenance = JSON.parse(fs.readFileSync(path.join(dir, 'provenance.json'), 'utf8'));
  assert.equal(sha256, provenance.artifact_checksums['bars.csv']);
  const coverage = JSON.parse(fs.readFileSync(path.join(dir, 'coverage.json'), 'utf8'));
  assert(coverage.complete && coverage.missing_count === 0 && coverage.outside_count === 0);
  const days = load(csv);
  assert.equal(days.reduce((n, d) => n + d.bars.length, 0), coverage.observed_count);
  const result = { sha256, start: days[0].date, end: days.at(-1).date,
    bars: coverage.observed_count, sessions: days.length, ...study(days) };
  fs.writeFileSync(path.join(__dirname, 'qqq-velocity-results.json'), JSON.stringify(result, null, 2) + '\n');
  fs.writeFileSync(path.join(__dirname, 'qqq-velocity-report.md'), report(result));
  console.log(`Wrote velocity report and results; ${result.grid.length} recipes tested.`);
}
module.exports = { load, aggregate, eligible, netReturn, stats, runs, trades, diagnostics, bootstrap, study, report };
