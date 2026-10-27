const { test } = require('node:test');
const assert = require('node:assert/strict');
const { fixedClockDays } = require('./qqq-minute-comparison.cjs');
const header = 'symbol,timestamp,open,high,low,close,volume';
const row = (time, price) => `QQQ,${time},${price},${price},${price},${price},1`;

test('matching clock uses 15:30 normally and 12:30 on an early close', () => {
  const csv = [header,
    row('2024-07-02T19:30:00Z', 100), row('2024-07-02T19:59:00Z', 110),
    row('2024-07-03T16:30:00Z', 200), row('2024-07-03T16:59:00Z', 220)].join('\n');
  const days = [{ date: '2024-07-02', lastOpen: 110, close: 111 },
    { date: '2024-07-03', lastOpen: 220, close: 221 }];
  const adjusted = fixedClockDays(csv, days);
  assert.deepEqual(adjusted.map(d => d.lastOpen), [100, 200]);
  assert.deepEqual(adjusted.map(d => d.close), [111, 221]);
  assert.deepEqual(days.map(d => d.lastOpen), [110, 220], 'Original days must remain intact');
});

test('matching clock follows winter UTC offset without hardcoding UTC hours', () => {
  const csv = [header, row('2024-12-02T20:30:00Z', 100),
    row('2024-12-02T20:59:00Z', 110)].join('\n');
  assert.equal(fixedClockDays(csv, [{ date: '2024-12-02', lastOpen: 110 }])[0].lastOpen, 100);
});

test('a missing execution minute cannot silently fall back to an earlier half-hour', () => {
  const csv = [header, row('2024-07-02T19:00:00Z', 100),
    row('2024-07-02T19:59:00Z', 110)].join('\n');
  assert.throws(() => fixedClockDays(csv, [{ date: '2024-07-02', lastOpen: 110 }]),
    /Missing fixed execution clock/);
});

test('unknown symbols are rejected', () => {
  assert.throws(() => fixedClockDays(header + '\nSPY,2024-07-02T19:30:00Z,100,100,100,100,1', []),
    /Unexpected symbol/);
});
