const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { ShortLedger, auditStrategy } = require('./turtle-short-audit.cjs');
const { fillMark } = require('./turtle-minute-audit.cjs');

function fill({ direction = -1, intent = 'open', quantity = 10, price = 100,
  time = '2016-05-13T13:30:00Z', unit = 'unit-1', fee = 0 } = {}) {
  return { symbol: 'QQQ', direction, intent, quantity, price, fee, unit_id: unit,
    campaign_id: unit, side: (direction === 1) === (intent === 'open') ? 'buy' : 'sell',
    time, time_precision: 'bar_open' };
}
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9,
  `${actual} != ${expected}`);

test('profitable short locks proceeds, releases collateral and records direction PnL', () => {
  const ledger = new ShortLedger({ startingCash: 1000 });
  ledger.apply(fill());
  assert.deepEqual(ledger.mark(90), { cash: 2000, equity: 1100, exposure: 900,
    net_exposure: -900, short_liability: 900, collateral: 1500, borrow_costs: 0, shares: -10 });
  ledger.apply(fill({ intent: 'reduce', price: 90 }));
  assert.equal(ledger.cash, 1100); assert.equal(ledger.values(90).collateral, 0);
  assert.deepEqual(ledger.closed.short, { count: 1, wins: 1, realized_pnl_before_fees: 100 });
});

test('losing short can legitimately leave negative cash after a gap cover', () => {
  const ledger = new ShortLedger({ startingCash: 1000 });
  ledger.apply(fill()); ledger.apply(fill({ intent: 'reduce', price: 250 }));
  assert.equal(ledger.cash, -500); assert.equal(ledger.realized.short, -1500);
  assert.equal(ledger.closed.short.wins, 0); assert.equal(ledger.lots.size, 0);
});

test('weekend borrow charges three calendar days at previous close before cover', () => {
  const ledger = new ShortLedger({ startingCash: 1000, borrowRate: 0.03 });
  ledger.beforeDay('2016-05-13'); ledger.apply(fill()); ledger.mark(110);
  ledger.beforeDay('2016-05-16');
  const fee = 1100 * 0.03 * 3 / 365;
  near(ledger.borrowCosts, fee); near(ledger.cash, 2000 - fee);
  ledger.beforeDay('2016-05-16'); near(ledger.borrowCosts, fee);
  ledger.apply(fill({ intent: 'reduce', price: 90 }));
  near(ledger.cash, 1100 - fee); assert.equal(ledger.borrowCharges, 1);
});

test('partial covers release original entry collateral proportionally', () => {
  const ledger = new ShortLedger({ startingCash: 1000 });
  ledger.apply(fill()); ledger.apply(fill({ intent: 'reduce', quantity: 4, price: 80 }));
  assert.equal(ledger.cash, 1680); assert.equal(ledger.values(80).collateral, 900);
  assert.equal(ledger.values(80).shares, -6); assert.equal(ledger.closed.short.count, 0);
  ledger.apply(fill({ intent: 'reduce', quantity: 6, price: 120 }));
  assert.equal(ledger.cash, 960); assert.equal(ledger.closed.short.count, 1);
  assert.equal(ledger.closed.short.realized_pnl_before_fees, -40);
});

test('entries enforce unencumbered cash and no opposing directions', () => {
  assert.throws(() => new ShortLedger({ startingCash: 499 }).apply(fill()), /collateral/);
  const ledger = new ShortLedger({ startingCash: 1000 }); ledger.apply(fill());
  assert.throws(() => ledger.apply(fill({ direction: 1, unit: 'long' })), /Opposing/);
  assert.throws(() => ledger.apply(fill({ quantity: 11, unit: 'short-2' })), /collateral/);
  assert.throws(() => ledger.apply(fill({ intent: 'reduce', quantity: 11 })), /Reduction/);
  assert.throws(() => new ShortLedger({ allowShort: false }).apply(fill()), /long-only/);
  assert.throws(() => new ShortLedger().apply({ ...fill(), side: 'buy' }), /side/);
});

test('halt bounds prohibit fills and map restart fills to observed minute close', () => {
  const halts = [{ start: '2020-03-09T13:34:13Z', end: '2020-03-09T13:49:13Z' }];
  assert.throws(() => new ShortLedger().apply(fill({ time: '2020-03-09T13:49:12Z' }), halts), /halt/);
  const restart = fill({ time: '2020-03-09T13:49:13Z' });
  new ShortLedger().apply(restart, halts);
  assert.equal(fillMark(restart), Date.parse('2020-03-09T13:50:00Z'));
});

test('minute audit reconciles source prices, signed report fields and ordered fill ledger', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'turtle-short-audit-'));
  try {
    const config = { strategy: { allow_short: true, exit_strategy: 'turtle_channel' },
      risk: { starting_cash: '1000' }, execution: { borrow_rate: '0.03', initial_short_collateral: '1.5' } };
    const fills = [fill({ time: '2016-05-11T13:30:00Z' }),
      fill({ intent: 'reduce', price: 80, time: '2016-05-12T13:30:00Z' })];
    const fee = 900 * 0.03 / 365, end = 1200 - fee;
    const rows = [
      ['equity', '2016-05-11T13:31:00Z', 2000, 1100, 900, -900, 900, 1500, 0],
      ['equity', '2016-05-12T13:31:00Z', end, end, 0, 0, 0, 0, fee],
    ];
    const header = ['record_type', 'time', 'cash', 'equity', 'exposure', 'net_exposure',
      'short_liability', 'collateral', 'borrow_costs', 'side', 'symbol', 'intent', 'unit_id',
      'campaign_id', 'time_precision', 'reason', 'direction', 'quantity', 'price', 'fee'];
    const records = [header.join(',')];
    for (let i = 0; i < rows.length; i++) {
      records.push([...rows[i], ...Array(header.length - rows[i].length).fill('')].join(','));
      const f = fills[i];
      records.push(header.map(key => key === 'record_type' ? 'fill' : f[key] ?? '').join(','));
    }
    fs.writeFileSync(path.join(dir, 'report.csv'), records.join('\n') + '\n');
    const source = path.join(dir, 'source.csv');
    fs.writeFileSync(source, 'symbol,timestamp,open,close\nQQQ,2016-05-11T13:30:00Z,100,90\nQQQ,2016-05-12T13:30:00Z,80,80\n');
    const run = { status: 'complete', counts: { equity: 2, fills: 2 }, summary: {
      price_basis: 'adjusted_research', exit_strategy: 'turtle_channel', closed_trade_count: 1,
      ending_equity: end, total_return: end / 1000 - 1, max_drawdown: 0,
      gross_turnover_dollars: 1800, fees: 0, borrow_costs: fee,
      average_exposure_fraction: (900 / 1100) / 2, max_exposure_fraction: 900 / 1100 } };
    fs.writeFileSync(path.join(dir, 'run.json'), JSON.stringify(run));
    fs.writeFileSync(path.join(dir, 'fills.json'), JSON.stringify(fills));
    fs.writeFileSync(path.join(dir, 'audit.json'), JSON.stringify({ metrics: {
      ending_cash: end, borrow_costs: fee, realized_pnl_before_fees: 200 },
    orders: [{ reason: 'forced_cover' }, { reason: 'strategy_exit' }],
    cancellations: [{ reason: 'margin_breach' }] }));
    const bh = { equity_points: 2, first_mark: '2016-05-11T13:31:00.000Z',
      last_mark: '2016-05-12T13:31:00.000Z' };
    const observed = await auditStrategy(dir, source, bh, [], config);
    near(observed.ending_equity, end); assert.equal(observed.short_open_fills, 1);
    assert.equal(observed.short_cover_fills, 1); assert.equal(observed.audit, 'passed');
    assert.equal(observed.borrow_charge_sessions, 1);
    assert.deepEqual(observed.margin_records, { forced_cover_fills: 0,
      forced_cover_orders: 1, margin_breach_cancellations: 1 });
    fs.writeFileSync(path.join(dir, 'report.csv'), records.join('\n').replace('2000,1100,900,-900', '2000,1100,900,900') + '\n');
    await assert.rejects(auditStrategy(dir, source, bh, [], config), /net_exposure/);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});
