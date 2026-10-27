const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { parseCsvLine, exactEntry, benchmark, equityStats, fillMark, auditStrategy } = require('./turtle-minute-audit.cjs');
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} != ${b}`);
const row = (timestamp, open, close = open) => ({ symbol: 'QQQ', timestamp, open: String(open), close: String(close) });

test('CSV parser handles metadata commas, escaped quotes and empty fields', () => {
  assert.deepEqual(parseCsvLine('equity,"[""a,b"",""c""]",,12'), ['equity', '["a,b","c"]', '', '12']);
  assert.deepEqual(parseCsvLine('"",,'), ['', '', '']);
  assert.throws(() => parseCsvLine('"unfinished'), /Unclosed/);
  assert.throws(() => parseCsvLine('"closed"extra'), /Unexpected/);
});

test('entry uses exact affordable whole shares and preserves residual cash', () => {
  const result = exactEntry(1000, '100', 5);
  assert.equal(result.shares, 9); near(result.fillPrice, 100.05); near(result.cash, 99.55);
  assert.deepEqual(exactEntry('0.30', '0.10', 0), { shares: 3, fillPrice: 0.1, cash: 0 });
});

test('buy and hold pays entry slippage, marks final close, and does not charge an exit', async () => {
  const r = await benchmark([
    row('2020-03-09T13:30:00Z', 100, 110), row('2020-03-09T13:49:00Z', 90, 80),
  ], { tradeStart: '2020-03-09T13:30:00Z', startingCash: 1000, slippageBps: 5, warmupSessions: 0 });
  assert.equal(r.shares, 9); assert.equal(r.orders, 1); assert.equal(r.equity_points, 2);
  near(r.ending_equity, 819.55); near(r.total_return, -0.18045);
  near(r.max_drawdown, 1 - 819.55 / 1089.55);
  assert.equal(r.last_mark, '2020-03-09T13:50:00.000Z');
});

test('missing exact entry and wrong warmup cannot silently shorten benchmark', async () => {
  const rows = [row('2020-03-09T13:31:00Z', 100)];
  await assert.rejects(benchmark(rows, { tradeStart: '2020-03-09T13:30:00Z', warmupSessions: 0 }), /exact/);
  await assert.rejects(benchmark([row('2020-03-09T13:30:00Z', 100)],
    { tradeStart: '2020-03-09T13:30:00Z', warmupSessions: 1 }), /warmup/);
});

test('equity metrics begin from actual starting cash and compound annual attribution', () => {
  const stats = equityStats(100);
  stats.mark('2020-12-31T21:00:00Z', 90); stats.mark('2021-01-04T21:00:00Z', 108);
  const r = stats.result();
  near(r.max_drawdown, 0.1); near(r.total_return, 0.08);
  near(r.yearly_returns['2020'], -0.1); near(r.yearly_returns['2021'], 0.2);
  assert.throws(() => stats.mark('2021-01-04T21:00:00Z', 108), /chronological/);
});

test('streamed strategy audit checks exact mark alignment and rejects fills during a halt', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'turtle-audit-'));
  try {
    const source = path.join(directory, 'source.csv');
    fs.writeFileSync(source, 'symbol,timestamp,open,close\n' +
      'QQQ,2016-05-11T13:30:00Z,100,100\nQQQ,2016-05-11T13:31:00Z,110,110\n');
    fs.writeFileSync(path.join(directory, 'config.json'), JSON.stringify({
      risk: { starting_cash: '1000' }, strategy: { exit_strategy: 'turtle_channel' },
    }));
    fs.writeFileSync(path.join(directory, 'run.json'), JSON.stringify({ summary: {
      price_basis: 'adjusted_research', exit_strategy: 'turtle_channel',
      closed_trade_count: 1, ending_equity: 1050, total_return: 0.05,
      max_drawdown: 0, gross_turnover_dollars: 1050, fees: 0,
    } }));
    fs.writeFileSync(path.join(directory, 'fills.json'), JSON.stringify([
      { time: '2016-05-11T13:30:00Z', side: 'buy', quantity: 5, price: '100', fee: '0', time_precision: 'bar_open' },
      { time: '2016-05-11T13:31:00Z', side: 'sell', quantity: 5, price: '110', fee: '0', time_precision: 'bar_open' },
    ]));
    const csv = 'record_type,time,equity,quantity,price,fee,side\n' +
      'equity,2016-05-11T13:31:00Z,1000,,,,\n' +
      'equity,2016-05-11T13:32:00Z,1050,,,,\n' +
      'fill,2016-05-11T13:30:00Z,,5,100,0,buy\n' +
      'fill,2016-05-11T13:31:00Z,,5,110,0,sell\n';
    fs.writeFileSync(path.join(directory, 'report.csv'), csv);
    const bh = { equity_points: 2, first_mark: '2016-05-11T13:31:00.000Z',
      last_mark: '2016-05-11T13:32:00.000Z' };
    const result = await auditStrategy(directory, source, bh, []);
    assert.equal(result.audit, 'passed'); assert.equal(result.fills, 2);
    assert.equal(result.final_shares, 0); near(result.total_return, 0.05);
    near(result.final_cash, 1050); near(result.ledger_max_equity_error_dollars, 0);
    await assert.rejects(auditStrategy(directory, source, bh,
      [{ start: '2016-05-11T13:30:00Z', end: '2016-05-11T13:30:30Z' }]), /Fill during halt/);
    fs.writeFileSync(path.join(directory, 'report.csv'), csv.replace('13:32:00Z,1050', '13:33:00Z,1050'));
    await assert.rejects(auditStrategy(directory, source, bh, []), /timestamp mismatch/);
    fs.writeFileSync(path.join(directory, 'report.csv'), csv.replace('13:32:00Z,1050', '13:32:00Z,1040'));
    await assert.rejects(auditStrategy(directory, source, bh, []), /cash\/share ledger equity/);
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});

test('restart-second opening and intrabar-unknown fills map to the observed minute close', () => {
  const end = Date.parse('2020-03-09T13:50:00Z');
  assert.equal(fillMark({ time: '2020-03-09T13:49:13Z', time_precision: 'bar_open' }), end);
  assert.equal(fillMark({ time: '2020-03-09T13:49:13Z', time_precision: 'intrabar_unknown',
    bar_end: '2020-03-09T13:50:00Z' }), end);
  assert.throws(() => fillMark({ time: '2020-03-09T13:49:13Z', time_precision: 'intrabar_unknown',
    bar_end: '2020-03-09T13:49:00Z' }), /Invalid intrabar/);
});
