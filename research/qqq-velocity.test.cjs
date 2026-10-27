'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { load, aggregate, eligible, netReturn, runs, trades, bootstrap } = require('./qqq-velocity.cjs');

test('aggregation preserves candle bodies and drops incomplete halt buckets', () => {
  const bars = Array.from({ length: 15 }, (_, i) => ({ t: i * 60000,
    o: 100 + i, c: 101 + i })).filter((_, i) => i !== 6);
  const out = aggregate([{ date: '2024-01-02', bars }], 5);
  assert.deepEqual(out.map(b => [b.t, b.o, b.c]), [[0, 100, 105], [600000, 110, 115]]);
  assert.equal(eligible(out, 0, 1, 5), false);
});

test('signal scale excludes current and future returns', () => {
  const make = shock => [{ date: '2024-01-02', bars: Array.from({ length: 64 }, (_, i) => ({
    t: i * 60000, o: 100, c: 100 * Math.exp(i === 60 ? shock : .001) })) }];
  const a = aggregate(make(.02), 1), b = aggregate(make(.04), 1);
  assert(Math.abs(a[60].scale - .001) < 1e-12);
  assert.equal(a[60].scale, b[60].scale);
  assert.equal(a[59].z, null);
  assert(Math.abs(b[60].z - 40) < 1e-9);
});

const fixture = () => Array.from({ length: 90 }, (_, i) => ({
  date: '2024-01-02', t: i * 60000, o: 100 + i, c: 100 + i,
  r: 0, z: i >= 60 ? 3 : null
}));

test('fills next open, holds H intervals, charges exit notional; skips overlap', () => {
  const bars = fixture();
  const ts = trades(bars, 1, 2, 1, 3, 1, 'discovery', 5);
  assert(Math.abs(ts[0].r - netReturn(161, 164, 1, 5)) < 1e-12);
  assert.equal(ts.length, 7); // signals 60,64,...84; final exit at index88.
  assert(Math.abs(netReturn(100, 110, -1, 5) - (-.1 - .0005 * 2.1)) < 1e-12);
});

test('no intraday overnight, halt-crossing or split-crossing fills', () => {
  const bars = fixture();
  for (let i = 62; i < bars.length; i++) bars[i].date = '2025-01-02';
  assert.equal(trades(bars.slice(0, 64), 1, 2, 1, 2, 1, 'discovery', 0).length, 0);
  const gapped = fixture();
  for (let i = 62; i < gapped.length; i++) gapped[i].t += 60000;
  assert.equal(eligible(gapped, 60, 2, 1), false);
  const overnight = fixture();
  for (let i = 62; i < overnight.length; i++) overnight[i].date = '2024-01-03';
  assert.equal(eligible(overnight, 60, 2, 1), false);
  assert.equal(eligible(overnight, 60, 2, 390), true);
});

test('streak length excludes signal; reports boundary censoring', () => {
  const bars = fixture().slice(0, 67);
  for (const b of bars) b.z = null;
  bars[60].z = 3; bars[61].r = .01; bars[62].r = .01; bars[63].r = -.01;
  const s = runs(bars, 2, 1, 1, 'discovery');
  assert.equal(s.tested, 1); assert.equal(s.nextSamePct, 100);
  assert(Math.abs(s.unconditionalDirectionPct - 100 / 3) < 1e-12);
  assert.equal(s.medianAdditionalCandles, 2); assert.equal(s.atLeast3Pct, 0);
  bars[63].r = bars[64].r = bars[65].r = bars[66].r = .01;
  const c = runs(bars, 2, 1, 1, 'discovery');
  assert.equal(c.completeRuns, 0); assert.equal(c.boundaryCensored, 1);
});

test('loader rejects malformed price/order; bootstrap reproducible', () => {
  assert.throws(() => load('symbol,timestamp,open,high,low,close,volume\nQQQ,2024-01-02T14:30:00Z,10,9,8,11,2'));
  const dates = Array.from({ length: 60 }, (_, i) => String(i));
  const values = dates.filter((_, i) => i % 2).map(date => ({ date, r: .001 }));
  const interval = bootstrap(values, dates);
  assert.deepEqual(interval, bootstrap(values, dates));
  assert(interval.every(x => Math.abs(x - 10) < 1e-10));
});
