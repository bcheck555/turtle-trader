'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { prepare, advance, makeOrder, tryEntry, exitPrice, simulate, RULES } = require('./qqq-liquidity-sweep.cjs');
const M = 60000, F = 5 * M, T = Date.parse('2024-01-03T14:30:00Z');
const candle = (i, o, h, l, c) => ({ t: T + i * F, end: T + (i + 1) * F, o, h, l, c, atr: 2, pivot: 103 });
function fixture() {
  const fives = [candle(0, 105, 106, 104, 105), candle(1, 105, 105, 103, 104),
    candle(2, 104, 104, 101, 102), candle(3, 101, 102, 99, 101),
    candle(4, 101, 105, 100, 104), candle(5, 104, 106, 103, 105),
    candle(6, 103, 104, 102, 103), candle(7, 103, 121, 103, 120),
    candle(8, 120, 121, 119, 120)];
  const bars = fives.flatMap(b => Array.from({ length: 5 }, (_, i) => i === 0 ?
    { t: b.t, o: b.o, h: b.h, l: b.l, c: b.c } :
    { t: b.t + i * M, o: b.c, h: b.c, l: b.c, c: b.c }));
  return { date: '2024-01-03', previousLow: 100, previousHigh: 120, bars, fives,
    byEnd: new Map(fives.map((b, i) => [b.end, i])), incompleteEnds: new Set(),
    openTime: T, closeTime: T + fives.length * F };
}
function gapOrder() {
  const d = fixture();
  let state = advance(null, d, 3, 'full');
  assert(state.setup); assert.equal(state.order, null);
  state = advance(state.setup, d, 4, 'full');
  assert.equal(state.setup.confirmed, 4); assert.equal(state.order, null);
  return advance(state.setup, d, 5, 'full').order;
}
const near = (a, b) => assert(Math.abs(a - b) < 1e-8, `${a} != ${b}`);

test('full setup activates midpoint limit only after third candle closes', () => {
  const o = gapOrder();
  near(o.limit, 102.5); near(o.stop, 98.99); assert.equal(o.target, 120);
  assert.equal(o.activeAt, T + 6 * F);
  assert.equal(tryEntry(o, { t: o.activeAt - M, o: 103, l: 102 }, 25000, 0).position, undefined);
});

test('strict sweep/reclaim, body/ATR, break and contiguous three-bar gap required', () => {
  const d = fixture(); d.fives[3].l = 100;
  assert.equal(advance(null, d, 3, 'full').setup, null);
  d.fives[3].l = 99;
  const s = advance(null, d, 3, 'full').setup;
  d.fives[4].c = 102;
  assert.equal(advance(structuredClone(s), d, 4, 'full').setup.confirmed, undefined);
  d.fives[4].c = 104;
  const confirmed = advance(s, d, 4, 'full').setup;
  d.fives[5].l = 102;
  assert.equal(advance(structuredClone(confirmed), d, 5, 'full').order, null);
  d.fives[5].l = 103; d.fives[5].t += M;
  assert.equal(advance(confirmed, d, 5, 'full').order, null);
});

test('setup expiry uses wall clock and allows third candle after last eligible confirmation', () => {
  const d = fixture();
  const s = advance(null, d, 3, 'full').setup;
  assert.equal(advance({ ...s, expires: d.fives[4].end - 1 }, d, 4, 'full').setup, null);
  const confirmed = advance({ ...s, expires: d.fives[4].end }, d, 4, 'full').setup;
  assert(advance(confirmed, d, 5, 'full').order);
});

test('cash debit respects buy limit; size caps risk and capital including costs', () => {
  const o = gapOrder(), b = { t: o.activeAt, o: 103, h: 104, l: 102, c: 103 };
  const { position: p } = tryEntry(o, b, 25000, 0.0005);
  assert.equal(p.entry, o.limit); assert(p.debit <= 25000);
  assert(p.plannedRisk <= 25000 * RULES.risk_fraction);
  near(p.debit, p.shares * 102.5 * 1.0005);
  assert(Number.isInteger(p.shares));
  const improved = tryEntry(o, { ...b, o: 102.25 }, 25000, 0.0005).position;
  assert.equal(improved.entry, 102.25); assert(improved.atOpen);
});

test('unfilled orders persist until elapsed expiry; stop/target opening gaps cancel', () => {
  const o = gapOrder(), b = { t: o.activeAt, o: 104, l: 103 };
  assert.equal(tryEntry(o, b, 25000, 0).order, o);
  assert.equal(tryEntry(o, { ...b, t: o.expires }, 25000, 0).order, null);
  assert.equal(tryEntry(o, { ...b, o: 98 }, 25000, 0).position, undefined);
  assert.equal(tryEntry(o, { ...b, o: 98.995 }, 25000, 0).position, undefined);
  assert.equal(tryEntry(o, { ...b, o: 121 }, 25000, 0).position, undefined);
  assert.equal(tryEntry(o, { ...b, executionTime: o.expires }, 25000, 0).order, null);
});

test('stop wins ambiguity and gaps; intraminute entry cannot claim earlier target', () => {
  const p = { stop: 99, target: 110, atOpen: false };
  assert.deepEqual(exitPrice(p, { o: 104, h: 111, l: 98 }, true),
    { price: 99, reason: 'stop', ambiguous: true });
  assert.equal(exitPrice(p, { o: 97, h: 105, l: 96 }).price, 97);
  assert.equal(exitPrice(p, { o: 105, h: 111, l: 102 }, true), null);
  assert.equal(exitPrice({ ...p, atOpen: true }, { o: 104, h: 111, l: 102 }, true).price, 110);
});

test('reward/risk rejects poor targets and is rechecked at actual market open', () => {
  assert.equal(makeOrder({ low: 99, target: 103 }, 102, 'market', 0, M, RULES), null);
  const o = gapOrder(); o.type = 'market';
  assert.equal(tryEntry(o, { t: o.activeAt, o: 110, l: 109 }, 25000, 0).position, undefined);
});

test('full replay ledger reconciles cash, timing, costs and one-trade daily limit', () => {
  const d = fixture(), r = simulate([d], { start: d.date, keepTrades: true });
  assert.equal(r.trades, 1);
  const t = r.ledger[0];
  assert.equal(t.entry_time, new Date(T + 6 * F).toISOString());
  assert.equal(t.reason, 'target');
  near(t.pnl, t.shares * (t.exit * 0.9995 - t.entry * 1.0005));
  near(r.ending_equity, 25000 + t.pnl);
  assert(r.mean_exposure_fraction > 0 && r.mean_exposure_fraction < 1);
});

test('later price mutations cannot change prior signal or entry', () => {
  const d = fixture(), changed = fixture();
  changed.bars.filter(b => b.t >= T + 7 * F).forEach(b => { b.o = 103; b.h = 104; b.l = 100; b.c = 102; });
  const opts = { start: d.date, keepTrades: true };
  const a = simulate([d], opts).ledger[0], b = simulate([changed], opts).ledger[0];
  for (const key of ['entry_time', 'entry', 'shares', 'stop', 'target']) assert.equal(a[key], b[key]);
  assert.notEqual(a.exit, b.exit);
});

test('flat at scheduled final-minute open; benchmark terminal costs applied', () => {
  const d = fixture();
  d.previousHigh = 140;
  const r = simulate([d], { start: d.date, keepTrades: true });
  assert.equal(r.ledger[0].reason, 'session_close');
  assert.equal(r.ledger[0].exit_time, new Date(d.closeTime - M).toISOString());
  const b = simulate([d], { start: d.date, buyHold: true, keepTrades: true });
  assert.equal(b.trades, 1); assert.equal(b.ledger[0].reason, 'terminal');
  near(b.ending_equity, 25000 + b.ledger[0].pnl);
});

test('fresh validation account is independent of earlier account performance', () => {
  const a = fixture(), b = fixture();
  b.date = '2025-01-03';
  const r = simulate([a, b], { start: '2025-01-01', keepTrades: true });
  const alone = simulate([b], { start: '2025-01-01', keepTrades: true });
  assert.deepEqual(r, alone);
});

test('minute gaps cancel pending orders before reopening prices can fill', () => {
  const d = fixture();
  d.bars = d.bars.filter(b => b.t !== T + 6 * F);
  const r = simulate([d], { start: d.date, keepTrades: true });
  assert.equal(r.trades, 0);
});

function rawDay(date, offset, highs) {
  const open = T + offset * 86400000;
  const bars = highs.flatMap((h, j) => Array.from({ length: 5 }, (_, i) =>
    ({ t: open + (j * 5 + i) * M, o: 100, h, l: 99, c: 100 })));
  return { date, bars };
}
const coverageFor = days => ({ sessions: days.map(d => ({ day: d.date,
  expected_count: d.bars.length, halted_count: 0, missing_count: 0, observed_count: d.bars.length })) });

test('preparation uses only prior ATR and fully confirmed pre-sweep pivots', () => {
  const d = rawDay('2024-01-03', 0, [101, 102, 110, 103, 102, 104, 105]);
  prepare([d], coverageFor([d]), [], { ...RULES, atr_length: 2 });
  assert.equal(d.fives[4].pivot, undefined);
  assert.equal(d.fives[5].pivot, 110);
  near(d.fives[2].atr, (2 + 3) / 2);
  const changed = rawDay('2024-01-03', 0, [101, 102, 110, 103, 102, 150, 160]);
  prepare([changed], coverageFor([changed]), [], { ...RULES, atr_length: 2 });
  for (let i = 0; i < 5; i++) assert.deepEqual(d.fives[i], changed.fives[i]);
});

test('previous-day levels, early close and unexplained holes validated', () => {
  const a = rawDay('2024-01-03', 0, [105, 110]), b = rawDay('2024-01-04', 1, [102, 103]);
  prepare([a, b], coverageFor([a, b]));
  assert.equal(b.previousHigh, 110); assert.equal(b.previousLow, 99);
  assert.equal(b.closeTime - b.openTime, 10 * M);
  const broken = rawDay('2024-01-03', 0, [105, 110]);
  const cov = coverageFor([broken]); broken.bars.splice(3, 1); cov.sessions[0].observed_count--;
  assert.throws(() => prepare([broken], cov), /Unexplained minute gap/);
});

test('halt buckets omitted and reopening timestamp respects second-level resume', () => {
  const d = rawDay('2024-01-03', 0, [101, 102, 110, 103, 102, 104, 105, 106]);
  const cov = coverageFor([d]);
  d.bars = d.bars.filter(b => b.t !== T + 27 * M);
  cov.sessions[0].expected_count--; cov.sessions[0].halted_count++;
  cov.sessions[0].observed_count--;
  const halts = [{ start: new Date(T + 26 * M + 1000).toISOString(), end: new Date(T + 28 * M + 13000).toISOString() }];
  prepare([d], cov, halts);
  assert.equal(d.fives.length, 7);
  assert.equal(d.fives.find(b => b.t === T + 30 * M).pivot, undefined);
  assert.equal(d.bars.find(b => b.t === T + 28 * M).executionTime, T + 28 * M + 13000);
});

test('raw minute preparation through execution produces a causally timed trade', () => {
  const previous = rawDay('2024-01-02', -1, Array(20).fill(121));
  previous.bars.forEach(b => { b.o = 120; b.c = 120; b.l = 119; });
  previous.bars[0].h = 140; previous.bars[0].l = 100;
  const values = [[104,105,103,104], [104,106,103,105], [105,110,104,106],
    [106,107,103,104], [104,105,101,102], [101,102,99,101],
    [101,113,100,112], [112,114,111,113], [112,113,105,111],
    [111,141,110,140], [140,141,139,140]];
  const current = { date: '2024-01-03', bars: values.flatMap(([o,h,l,c], j) =>
    Array.from({ length: 5 }, (_, i) => ({ t: T + (j * 5 + i) * M,
      o: i ? c : o, h: i ? c : h, l: i ? c : l, c }))) };
  const days = [previous, current];
  prepare(days, coverageFor(days));
  assert.equal(current.fives[5].pivot, 110);
  const r = simulate(days, { start: current.date, keepTrades: true });
  assert.equal(r.trades, 1); assert.equal(r.ledger[0].reason, 'target');
  assert.equal(r.ledger[0].entry_time, new Date(T + 8 * F).toISOString());
  near(r.ledger[0].entry, 106.5);
});
