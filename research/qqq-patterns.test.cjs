const { test } = require('node:test');
const assert = require('node:assert/strict');
const { targets, simulate, specs, loadDays } = require('./qqq-patterns.cjs');
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-8,
  `${actual} differs from ${expected}`);
const day = (date, open, lastOpen = open) => ({ date, open, lastOpen, close: lastOpen,
  high: Math.max(open, lastOpen), low: Math.min(open, lastOpen), bars: 13 });

test('buy and hold preserves shares and pays entry/exit costs exactly once', () => {
  const days = [day('2024-01-02', 100, 110), day('2024-01-03', 120, 130)];
  const r = simulate(days, days.map(() => ({ daytime: 1, overnight: 1 })), 0, 2, 5);
  near(r.totalPct, (130 / 100 * 0.9995 / 1.0005 - 1) * 100);
  assert.equal(r.orders, 2);
});

test('overnight buys last-bar open, sells next open; no terminal reentry', () => {
  const days = [day('2024-01-02', 100, 110), day('2024-01-03', 121, 200)];
  const r = simulate(days, days.map(() => ({ daytime: 0, overnight: 1 })), 0, 2, 5);
  near(r.totalPct, (121 / 110 * 0.9995 / 1.0005 - 1) * 100);
  assert.equal(r.orders, 2);
});

test('last-session overnight signal alone executes no trade', () => {
  const r = simulate([day('2024-01-02', 100, 200)],
    [{ daytime: 0, overnight: 1 }], 0, 1, 10);
  near(r.totalPct, 0);
  assert.equal(r.orders, 0);
});

test('daytime excludes overnight price moves', () => {
  const days = [day('2024-01-02', 100, 110), day('2024-01-03', 220, 198)];
  const r = simulate(days, days.map(() => ({ daytime: 1, overnight: 0 })), 0, 2, 0);
  near(r.totalPct, -1);
  assert.equal(r.orders, 4);
});

test('all signals depend only on completed previous sessions', () => {
  const days = Array.from({ length: 280 }, (_, i) =>
    day(String(i).padStart(4, '0'), 100 + Math.sin(i) * 10 + i / 10));
  for (const spec of specs) {
    const original = targets(days, spec);
    const changed = days.map((d, i) => i < 230 ? d :
      { ...d, open: 1e6, lastOpen: 1e6, close: 1e6, high: 1e6, low: 1e6 });
    assert.deepEqual(targets(changed, spec).slice(0, 231), original.slice(0, 231), spec.name);
    assert.deepEqual(targets(days.slice(0, 231), spec), original.slice(0, 231), spec.name);
    assert.ok(original.slice(0, 200).every(t => t.daytime === 0 && t.overnight === 0));
  }
});

test('dip campaign holds exactly five opening-to-opening sessions', () => {
  const days = Array.from({ length: 210 }, (_, i) => day(String(i), i < 199 ? 100 : 98));
  const t = targets(days, { kind: 'dip', n: 1 });
  assert.deepEqual(t.slice(200, 207).map(x => x.daytime), [1, 1, 1, 1, 1, 0, 0]);
});

test('holdout account excludes previous interval gap and previous trades', () => {
  const days = [day('2024-12-31', 10, 20), day('2025-01-02', 100, 110)];
  const r = simulate(days, days.map(() => ({ daytime: 1, overnight: 1 })), 1, 2, 0);
  near(r.totalPct, 10);
  assert.equal(r.orders, 2);
});

test('CSV loader rejects non-QQQ input', () => {
  assert.throws(() => loadDays('symbol,timestamp,open,high,low,close,volume\nSPY,2024-01-02T14:30:00Z,1,1,1,1,1'),
    /Invalid symbol/);
});
