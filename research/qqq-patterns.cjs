// Exploratory adjusted-price research; independent of the Turtle execution engine.
// Run: node research/qqq-patterns.cjs [bars.csv]
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

function loadDays(csv) {
  const days = [];
  let previous = '';
  for (const row of csv.trim().split(/\r?\n/).slice(1)) {
    const [symbol, timestamp, ...fields] = row.split(',');
    if (symbol !== 'QQQ' || timestamp <= previous) throw Error('Invalid symbol/order');
    previous = timestamp;
    const [open, high, low, close, volume] = fields.map(Number);
    if (![open, high, low, close, volume].every(Number.isFinite) || low <= 0 ||
        low > Math.min(open, close) || high < Math.max(open, close)) {
      throw Error('Invalid OHLCV');
    }
    // Regular US sessions never cross a UTC date boundary.
    const date = timestamp.slice(0, 10);
    let day = days.at(-1);
    if (!day || day.date !== date) {
      day = { date, open, high, low, close, lastOpen: open, bars: 0 };
      days.push(day);
    }
    day.high = Math.max(day.high, high);
    day.low = Math.min(day.low, low);
    day.close = close;
    day.lastOpen = open;
    day.bars++;
  }
  if (days.length < 250) throw Error('Insufficient history');
  return days;
}

const specs = [
  { name: 'Buy and hold', kind: 'hold' },
  ...[50, 100, 200].map(n => ({ name: `Close above SMA${n}`, kind: 'sma', n })),
  ...[63, 126].map(n => ({ name: `Positive ${n}-day momentum`, kind: 'momentum', n })),
  ...[1, 2].map(n => ({ name: `Buy after ${n}% down day, hold 5 sessions`, kind: 'dip', n })),
  ...[1, 2].map(n => ({ name: `SMA200 or ${n}% dip held 5 sessions`, kind: 'trend_dip', n })),
  { name: 'RSI2 below 10, exit above 50', kind: 'rsi' },
  { name: 'RSI2 below 10 above SMA200, exit above 50', kind: 'rsi_trend' },
  ...[20, 55].map(n => ({ name: `Close breakout ${n}/${n === 20 ? 10 : 20}`, kind: 'breakout', n })),
  { name: 'Overnight (last bar open to next open)', kind: 'overnight' },
  { name: 'Daytime (open to last bar open)', kind: 'daytime' },
  { name: 'Daytime after 1% down day', kind: 'daydip' },
];

function targets(days, spec) {
  let held = 0, expires = -1, gain = 0, loss = 0;
  const result = [];
  for (let i = 0; i < days.length; i++) {
    // Only completed previous sessions inform today's opening order.
    const j = i - 1;
    if (j > 0) {
      const change = days[j].close - days[j - 1].close;
      gain = (gain + Math.max(0, change)) / 2;
      loss = (loss + Math.max(0, -change)) / 2;
    }
    const rsi = gain + loss === 0 ? 50 : 100 * gain / (gain + loss);
    const sma = n => days.slice(i - n, i).reduce((s, d) => s + d.close, 0) / n;
    let overnight = 0, daytime = 0;
    if (i >= 200) {
      if (spec.kind === 'hold') held = 1;
      if (spec.kind === 'sma') held = +(days[j].close > sma(spec.n));
      if (spec.kind === 'momentum') held = +(days[j].close > days[j - spec.n].close);
      if (spec.kind === 'dip' || spec.kind === 'trend_dip') {
        if (i >= expires) held = 0;
        if (!held && days[j].close / days[j - 1].close - 1 < -spec.n / 100) {
          held = 1;
          expires = i + 5;
        }
      }
      if (spec.kind.startsWith('rsi')) {
        if (held && rsi > 50) held = 0;
        else if (!held && rsi < 10 &&
                 (spec.kind === 'rsi' || days[j].close > sma(200))) held = 1;
      }
      if (spec.kind === 'breakout') {
        const entry = Math.max(...days.slice(j - spec.n, j).map(d => d.high));
        const exitN = spec.n === 20 ? 10 : 20;
        const exit = Math.min(...days.slice(j - exitN, j).map(d => d.low));
        if (held && days[j].close < exit) held = 0;
        else if (!held && days[j].close > entry) held = 1;
      }
      daytime = overnight = held;
      if (spec.kind === 'trend_dip' && days[j].close > sma(200)) {
        daytime = overnight = 1;
      }
      if (spec.kind === 'overnight') { daytime = 0; overnight = 1; }
      if (spec.kind === 'daytime') { daytime = 1; overnight = 0; }
      if (spec.kind === 'daydip') {
        daytime = +(days[j].close / days[j - 1].close - 1 < -0.01);
        overnight = 0;
      }
    }
    result.push({ daytime, overnight });
  }
  return result;
}

function metrics(returns, orders, exposures) {
  let equity = 1, peak = 1, dd = 0;
  for (const r of returns) {
    equity *= 1 + r;
    peak = Math.max(peak, equity);
    dd = Math.min(dd, equity / peak - 1);
  }
  const n = returns.length;
  const mean = returns.reduce((a, b) => a + b, 0) / n;
  const variance = returns.reduce((a, b) => a + (b - mean) ** 2, 0) / (n - 1);
  return { totalPct: 100 * (equity - 1), cagrPct: 100 * (equity ** (252 / n) - 1),
    drawdownPct: 100 * dd, sharpeZeroCash: variance ? mean / Math.sqrt(variance) * Math.sqrt(252) : 0,
    orders, segmentOccupancyPct: 100 * exposures / (2 * n), sessions: n };
}

function simulate(days, positions, start, end, costBps) {
  let equity = 1, shares = 0, cash = 1, orders = 0, exposures = 0;
  const returns = [], yearly = {};
  const cost = costBps / 10000;
  function trade(target, price) {
    if (target && shares === 0) {
      shares = cash / (price * (1 + cost)); cash = 0; orders++;
    } else if (!target && shares > 0) {
      cash = shares * price * (1 - cost); shares = 0; orders++;
    }
  }
  for (let i = start; i < end; i++) {
    const before = equity, day = days[i], t = positions[i];
    trade(t.daytime, day.open);
    trade(i === end - 1 ? 0 : t.overnight, day.lastOpen);
    // Value at next session's first open. Last interval ends at last bar open,
    // avoiding a hypothetical fill at a close known only after the session.
    const mark = i + 1 < end ? days[i + 1].open : day.lastOpen;
    if (i === end - 1) trade(0, mark);
    equity = cash + shares * mark;
    const r = equity / before - 1;
    returns.push(r);
    const year = day.date.slice(0, 4);
    yearly[year] = (yearly[year] ?? 1) * (1 + r);
    exposures += t.daytime + (i + 1 < end ? t.overnight : 0);
  }
  return { ...metrics(returns, orders, exposures),
    yearlyPct: Object.fromEntries(Object.entries(yearly).map(([y, v]) => [y, 100 * (v - 1)])) };
}

function researchDays(days, dataSha256) {
  if (days.length < 250) throw Error('Insufficient history');
  const start = 200;
  const split = days.findIndex(d => d.date >= '2025-01-01');
  if (split <= start || split >= days.length - 1) throw Error('Invalid holdout');
  const windows = { discovery: [start, split], holdout: [split, days.length], all: [start, days.length] };
  const rows = specs.map(spec => {
    const positions = targets(days, spec);
    const results = {};
    for (const cost of [0, 5, 10]) {
      results[cost] = Object.fromEntries(Object.entries(windows).map(([name, [a, b]]) =>
        [name, simulate(days, positions, a, b, cost)]));
    }
    return { ...spec, results };
  });
  const ranked = rows.slice(1).sort((a, b) => b.results[5].discovery.cagrPct - a.results[5].discovery.cagrPct);
  const daily = days.slice(1).map((d, i) => ({
    date: d.date, overnight: d.open / days[i].close - 1,
    intraday: d.close / d.open - 1, previous: i ? days[i].close / days[i - 1].close - 1 : null,
  }));
  const patterns = {};
  for (const [label, predicate] of Object.entries({
    all: () => true, afterDown1: d => d.previous < -0.01,
    afterDown2: d => d.previous < -0.02, afterUp1: d => d.previous > 0.01,
  })) {
    patterns[label] = {};
    for (const [window, [a, b]] of Object.entries(windows)) {
      const selected = daily.filter(d => d.date >= days[a].date && d.date <= days[b - 1].date && predicate(d));
      patterns[label][window] = { count: selected.length,
        meanIntradayBps: selected.reduce((s, d) => s + d.intraday, 0) / selected.length * 10000,
        meanOvernightBps: selected.reduce((s, d) => s + d.overnight, 0) / selected.length * 10000 };
    }
  }
  return { dataSha256,
    dataStart: days[0].date, dataEnd: days.at(-1).date, sessions: days.length,
    windows: Object.fromEntries(Object.entries(windows).map(([k, [a, b]]) =>
      [k, { start: days[a].date, end: days[b - 1].date, sessions: b - a }])),
    selectionRule: 'Highest discovery CAGR at 5bps per side, excluding benchmark',
    discoverySelected: ranked[0].name, strategyCount: specs.length - 1, patterns, rows };
}

function research(csv) {
  return researchDays(loadDays(csv), crypto.createHash('sha256').update(csv).digest('hex'));
}

module.exports = { loadDays, targets, simulate, research, researchDays, specs };
if (require.main === module) {
  const file = process.argv[2] || path.join(__dirname,
    '../runs/turtle-trader-equity-5y-20261026/qqq/bars.csv');
  console.log(JSON.stringify(research(fs.readFileSync(file, 'utf8')), null, 2));
}
