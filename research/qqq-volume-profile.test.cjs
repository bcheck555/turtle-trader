'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { config, load, readDataset, buildProfile, simulateSession, simulate,
  tradeReturn, stats, study } = require('./qqq-volume-profile.cjs');

const profile = { low: 90, high: 110, val: 95, vah: 105, poc: 100 };
function bar(o, h = o, l = o, c = o, v = 100, t = 0) { return { t, o, h, l, c, v }; }
function day(prices, date = '2020-01-02') {
  const start = Date.parse(date + 'T14:30:00Z');
  return { date, bars: prices.map((p, i) => ({ ...p, t: start + i * 60000 })) };
}
function near(actual, expected, epsilon = 1e-10) {
  assert(Math.abs(actual - expected) < epsilon, `${actual} != ${expected}`);
}

test('uniform profile conserves volume, includes zero-range bars and exact boundaries', () => {
  const p = buildProfile([bar(100, 110, 90, 100, 200), bar(110, 110, 110, 110, 50)],
    { rows: 20 });
  near(p.volumes.reduce((a, b) => a + b, 0), 250);
  near(p.volumes[19], 60);
  assert.equal(p.pocIndex, 19);
  assert(p.valueFraction >= 0.7 && p.val < p.vah);
});

test('equal POC volumes and adjacent expansion tie toward lower prices', () => {
  const p = buildProfile([bar(100, 110, 90, 100, 100)], { rows: 4 });
  assert.equal(p.pocIndex, 0);
  assert.equal(p.val, 90);
  assert.equal(p.vah, 105);
  const peaked = buildProfile([bar(100, 110, 90, 100, 100), bar(102, 102, 102, 102, 20)],
    { rows: 4, valueFraction: 0.58 });
  assert.equal(peaked.pocIndex, 2);
  assert.equal(peaked.val, 95);
  assert.equal(peaked.vah, 105);
});

test('typical-price allocation conserves volume and degeneracies produce no profile', () => {
  const p = buildProfile([bar(100, 110, 90, 105, 100), bar(100, 100, 100, 100, 30)],
    { rows: 10, allocation: 'typical' });
  near(p.volumes.reduce((a, b) => a + b, 0), 130);
  assert.equal(buildProfile([bar(100)]), null);
  assert.equal(buildProfile([bar(100, 110, 90, 100, 0)]), null);
});

test('entry follows completed signal candle at next observed open and not its close', () => {
  const d = day([bar(94, 101, 94, 100), bar(101, 104, 100, 103), bar(102)]);
  const t = simulateSession(d, profile).trade;
  assert.equal(t.entry, 101);
  assert.equal(t.entryTime, d.bars[1].t);
  assert.equal(t.signalTime, d.bars[0].t + 60000);
  assert.equal(t.exit, 102);
  assert.equal(t.reason, 'eod');
});

test('two confirmations must be consecutive and strictly inside, then only first acceptance', () => {
  const d = day([bar(94, 100, 94, 100), bar(100, 100, 95, 95),
    bar(99), bar(100), bar(101), bar(102)]);
  const t = simulateSession(d, profile, { confirmations: 2 }).trade;
  assert.equal(t.entryIndex, 4);
  assert.equal(simulateSession(day([bar(95), bar(100), bar(100)]), profile).skip, 'open_inside');
});

test('same-minute ambiguous stop and target use stop first', () => {
  const t = simulateSession(day([bar(94, 100, 94, 100), bar(100, 106, 92, 101),
    bar(100)]), profile).trade;
  assert.equal(t.stop, 93);
  assert.equal(t.exit, 93);
  assert.equal(t.reason, 'stop');
  assert.equal(t.ambiguous, true);
});

test('long stop gaps execute at worse open; favorable target gaps remain at target', () => {
  const start = [bar(94, 100, 94, 100), bar(100, 101, 99, 100)];
  const stopped = simulateSession(day([...start, bar(90), bar(100)]), profile).trade;
  assert.equal(stopped.exit, 90);
  assert.equal(stopped.reason, 'stop');
  const targeted = simulateSession(day([...start, bar(108), bar(100)]), profile).trade;
  assert.equal(targeted.exit, 105);
  assert.equal(targeted.reason, 'target');
});

test('short side mirrors target, stop, adverse gap and ambiguity behavior', () => {
  const start = [bar(106, 106, 100, 100), bar(100, 101, 99, 100)];
  const gap = simulateSession(day([...start, bar(110), bar(100)]), profile).trade;
  assert.equal(gap.side, -1);
  assert.equal(gap.stop, 107);
  assert.equal(gap.exit, 110);
  const both = simulateSession(day([start[0], bar(100, 108, 94, 100), bar(100)]), profile).trade;
  assert.equal(both.reason, 'stop');
  assert.equal(both.ambiguous, true);
  assert.equal(both.exit, 107);
  const target = simulateSession(day([...start, bar(93), bar(100)]), profile).trade;
  assert.equal(target.exit, 95);
});

test('first acceptance gap at stop or target invalidates session without later retries', () => {
  for (const [next, skip] of [[93, 'entry_beyond_stop'], [105, 'entry_beyond_target']]) {
    const d = day([bar(94, 100, 94, 100), bar(next), bar(100), bar(100), bar(100)]);
    assert.equal(simulateSession(d, profile).skip, skip);
  }
});

test('last-bar open liquidation ignores final bar range and close', () => {
  const t = simulateSession(day([bar(94, 100, 94, 100), bar(100),
    bar(102, 120, 80, 90)]), profile).trade;
  assert.equal(t.exit, 102);
  assert.equal(t.reason, 'eod');
  assert.equal(t.ambiguous, false);
  assert.equal(t.targetTouchedByEod, false);
  assert.equal(simulateSession(day([bar(94), bar(94, 100, 94, 100), bar(100)]),
    profile).skip, 'late_signal');
});

test('eventual target diagnostic ignores earlier stop but never includes pre-entry or final-bar ranges', () => {
  const t = simulateSession(day([bar(94, 106, 94, 100), bar(100, 101, 99, 100),
    bar(94, 94, 92, 93), bar(100, 106, 99, 101), bar(100)]), profile).trade;
  assert.equal(t.reason, 'stop');
  assert.equal(t.exit, 93);
  assert.equal(t.targetTouchedByEod, true);
  const noLaterTouch = simulateSession(day([bar(94, 106, 94, 100), bar(100, 101, 99, 100),
    bar(94, 94, 92, 93), bar(100, 106, 99, 101)]), profile).trade;
  assert.equal(noLaterTouch.targetTouchedByEod, false);
  const finalOpenTouch = simulateSession(day([bar(94, 100, 94, 100), bar(100), bar(106)]),
    profile).trade;
  assert.equal(finalOpenTouch.reason, 'eod');
  assert.equal(finalOpenTouch.targetTouchedByEod, true);
  const summary = stats([t, noLaterTouch, finalOpenTouch], 5);
  near(summary.targetTouchedByEodPct, 200 / 3);
  assert.equal(summary.targetBeforeStopPct, 0);
  const short = simulateSession(day([bar(106, 106, 94, 100), bar(100),
    bar(106, 108, 106, 107), bar(100, 101, 94, 95), bar(100)]), profile).trade;
  assert.equal(short.reason, 'stop');
  assert.equal(short.targetTouchedByEod, true);
});

test('next observed open after missing halt slots and no overnight holdings', () => {
  const d = day([bar(94, 100, 94, 100), bar(101), bar(102)]);
  d.bars[1].t += 15 * 60000;
  d.bars[2].t += 15 * 60000;
  const t = simulateSession(d, profile).trade;
  assert.equal(t.entryTime, d.bars[1].t);
  assert.equal(t.exitTime, d.bars[2].t);
});

test('profile uses previous completed day only; future perturbation preserves earlier trades', () => {
  const previous = day([bar(100, 110, 90, 100, 0), bar(100, 104, 96, 100, 100)], '2020-01-01');
  const current = day([bar(94, 99, 94, 99), bar(100), bar(101)], '2020-01-02');
  const future = day([bar(104), bar(104, 110, 100, 105), bar(105)], '2020-01-03');
  const before = simulate([previous, current, future], { rows: 20 });
  assert.equal(before.trades[0].profileDate, previous.date);
  near(before.trades[0].val, buildProfile(previous.bars, { rows: 20 }).val);
  const changed = structuredClone(future);
  for (const b of changed.bars) for (const field of ['o', 'h', 'l', 'c']) b[field] *= 2;
  assert.deepEqual(simulate([previous, current, changed], { rows: 20 }).trades[0], before.trades[0]);
  const appended = simulate([previous, current], { rows: 20 });
  assert.deepEqual(appended.trades[0], before.trades[0]);
});

test('cost math applies adverse fills independently to both sides, including matched EOD baseline', () => {
  const t = { entry: 100, exit: 110, baselineExit: 105, stop: 95, side: 1 };
  near(tradeReturn(t, 5).net, (110 * 0.9995 - 100 * 1.0005) / (100 * 1.0005));
  near(tradeReturn(t, 5, true).net, (105 * 0.9995 - 100 * 1.0005) / (100 * 1.0005));
  const short = { ...t, exit: 90, side: -1, stop: 105 };
  near(tradeReturn(short, 5).net, (100 * 0.9995 - 90 * 1.0005) / (100 * 0.9995));
  assert(tradeReturn(t, 10).net < tradeReturn(t, 5).net);
});

test('summary compounds independent trade returns and samples realized drawdown', () => {
  const trades = [{ entry: 100, exit: 110, stop: 90, side: 1, reason: 'target' },
    { entry: 100, exit: 90, stop: 90, side: 1, reason: 'stop', ambiguous: true }];
  const s = stats(trades, 0);
  near(s.compoundedReturnPct, -1);
  near(s.sessionCloseSampledMaxDrawdownPct, 10);
  near(s.meanNetBps, 0);
  near(s.profitFactor, 1);
  assert.equal(s.targetBeforeStopPct, 50);
  assert.equal(s.ambiguousBars, 1);
  assert.equal(stats([], 0).meanNetBps, null);
});

test('loader rejects wrong symbol, empty/invalid volume, malformed OHLC, duplicate/order/time errors', () => {
  const header = 'symbol,timestamp,open,high,low,close,volume\n';
  const row = 'QQQ,2020-01-02T14:30:00Z,100,101,99,100,50';
  const parsed = load(header + row);
  assert.equal(parsed[0].bars[0].v, 50);
  for (const bad of [row.replace('QQQ', 'SPY'), row.replace(',50', ',-1'),
    row.replace(',50', ','), row.replace(',99,', ',102,'),
    row.replace('14:30:00', '14:30:01'), row + '\n' + row]) {
    assert.throws(() => load(header + bad));
  }
  assert.throws(() => config({ rows: 1 }));
  assert.throws(() => config({ allocation: 'close' }));
  assert.throws(() => config({ confirmations: 0 }));
  assert.throws(() => buildProfile([]));
});

test('dataset verifies hashes, completeness, and checksum-valid wrong feed/adjustment archives', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'qqq-profile-test-'));
  try {
    const artifacts = {
      'bars.csv': 'symbol,timestamp,open,high,low,close,volume\nQQQ,2020-01-02T14:30:00Z,100,101,99,100,50\n',
      'manifest.toml': 'feed = "sip"\nadjustment = "all"\ninterval_minutes = 1\ncoverage = "regular"\nsymbols = ["QQQ"]\n' +
        'timestamp_convention = "bar_open"\nprice_basis = "adjusted_research"\nsynthetic = false\n',
      'coverage.json': JSON.stringify({ complete: true, missing_count: 0, outside_count: 0,
        observed_count: 1, halted_count: 0, sessions: [{ symbol: 'QQQ', day: '2020-01-02',
          observed_count: 1, expected_count: 1, missing_count: 0 }] }),
    };
    const artifact_checksums = {};
    for (const [name, bytes] of Object.entries(artifacts)) {
      fs.writeFileSync(path.join(dir, name), bytes);
      artifact_checksums[name] = crypto.createHash('sha256').update(bytes).digest('hex');
    }
    fs.writeFileSync(path.join(dir, 'provenance.json'), JSON.stringify({ artifact_checksums }));
    assert.equal(readDataset(dir).source.bars, 1);
    const originalManifest = artifacts['manifest.toml'];
    for (const [before, after] of [['feed = "sip"', 'feed = "iex"'],
      ['adjustment = "all"', 'adjustment = "raw"']]) {
      const manifest = originalManifest.replace(before, after);
      fs.writeFileSync(path.join(dir, 'manifest.toml'), manifest);
      artifact_checksums['manifest.toml'] = crypto.createHash('sha256').update(manifest).digest('hex');
      fs.writeFileSync(path.join(dir, 'provenance.json'), JSON.stringify({ artifact_checksums }));
      assert.throws(() => readDataset(dir), /Expected SIP QQQ/);
    }
    fs.writeFileSync(path.join(dir, 'manifest.toml'), originalManifest);
    artifact_checksums['manifest.toml'] = crypto.createHash('sha256').update(originalManifest).digest('hex');
    fs.writeFileSync(path.join(dir, 'provenance.json'), JSON.stringify({ artifact_checksums }));
    fs.appendFileSync(path.join(dir, 'coverage.json'), ' ');
    assert.throws(() => readDataset(dir), /Checksum mismatch: coverage.json/);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('study has fixed sensitivities, deterministic output and separate chronological windows', () => {
  const days = [day([bar(100, 110, 90, 100, 0), bar(100, 104, 96, 100, 100)], '2021-12-30'),
    day([bar(94, 99, 94, 99), bar(100), bar(101)], '2021-12-31'),
    day([bar(90, 100, 90, 99), bar(100), bar(101)], '2022-01-03')];
  const a = study(days), b = study(days);
  assert.deepEqual(a, b);
  assert.equal(a.variants.length, 6);
  const base = a.variants[0];
  assert.equal(base.discovery[5].trades + base.evaluation[5].trades, base.all[5].trades);
  assert.equal(base.all[0].trades + Object.values(base.skips).reduce((x, y) => x + y, 0),
    base.evaluatedSessions);
  assert.equal(a.baseTrades[0].costResults[5].entryFill > a.baseTrades[0].entry, true);
});
