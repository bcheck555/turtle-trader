// Independently reconcile production minute snapshots, short collateral and borrow charges.
// Run: node research/turtle-short-audit.cjs > research/qqq-turtle-shorts-1m-results.json
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const { csvRows, benchmark, equityStats, fillMark } = require('./turtle-minute-audit.cjs');
const TRADE_START = '2016-05-11T13:30:00Z';
const nyDate = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York',
  year: 'numeric', month: '2-digit', day: '2-digit' });

function near(actual, expected, label) {
  assert.ok(Number.isFinite(actual) && Number.isFinite(expected), `${label}: nonfinite value`);
  assert.ok(Math.abs(actual - expected) <= 1e-8 * Math.max(1, Math.abs(expected)),
    `${label}: ${actual} differs from ${expected}`);
}

class ShortLedger {
  constructor({ startingCash = 25000, borrowRate = 0, shortCollateral = 1.5, allowShort = true } = {}) {
    this.cash = startingCash; this.borrowRate = borrowRate; this.shortCollateral = shortCollateral;
    this.allowShort = allowShort; this.lots = new Map(); this.campaigns = new Map();
    this.borrowCosts = 0; this.borrowCharges = 0; this.fees = 0; this.turnover = 0;
    this.shortEntries = 0; this.shortCovers = 0; this.day = null; this.previousClose = null;
    this.closed = { long: { count: 0, wins: 0, realized_pnl_before_fees: 0 },
      short: { count: 0, wins: 0, realized_pnl_before_fees: 0 } };
    this.realized = { long: 0, short: 0 };
  }
  beforeDay(day) {
    if (day === this.day) return;
    if (this.day !== null) {
      const days = (Date.parse(day) - Date.parse(this.day)) / 86400000;
      assert.ok(Number.isInteger(days) && days > 0, 'Nonchronological borrow day');
      const liability = this.values(this.previousClose).short_liability;
      const fee = liability * this.borrowRate * days / 365;
      this.cash -= fee; this.borrowCosts += fee;
      if (fee) this.borrowCharges++;
    }
    this.day = day;
  }
  apply(fill, halts = []) {
    const quantity = Number(fill.quantity), price = Number(fill.price), fee = Number(fill.fee);
    const direction = Number(fill.direction), intent = fill.intent, at = Date.parse(fill.time);
    assert.equal(fill.symbol, 'QQQ');
    assert.ok(Number.isSafeInteger(quantity) && quantity > 0 && price > 0 && fee >= 0);
    assert.ok(Number.isFinite(price) && Number.isFinite(fee) && Number.isFinite(at));
    assert.ok(direction === 1 || direction === -1, 'Invalid direction');
    assert.ok(intent === 'open' || intent === 'reduce', 'Invalid intent');
    assert.equal(fill.side, (direction === 1) === (intent === 'open') ? 'buy' : 'sell',
      'Fill side conflicts with direction/intent');
    assert.ok(!halts.some(h => Date.parse(h.start) <= at && at < Date.parse(h.end)), 'Fill during halt');
    const key = fill.unit_id || fill.symbol, lot = this.lots.get(key), value = price * quantity;
    if (intent === 'open') {
      assert.ok(this.allowShort || direction === 1, 'Short opening in long-only run');
      assert.ok(!lot || (lot.direction === direction && lot.symbol === fill.symbol), 'Unit mismatch');
      assert.ok([...this.lots.values()].every(p => p.direction === direction), 'Opposing campaigns overlap');
      const collateral = this.values(price).collateral;
      const required = direction === 1 ? value : value * (this.shortCollateral - 1);
      assert.ok(required + fee <= this.cash - collateral + 1e-7, 'Insufficient entry collateral/cash');
      this.cash -= direction * value + fee;
      const addedCollateral = direction === -1 ? value * this.shortCollateral : 0;
      if (lot) {
        lot.average = (lot.average * lot.quantity + value) / (lot.quantity + quantity);
        lot.quantity += quantity; lot.collateral += addedCollateral;
      } else this.lots.set(key, { symbol: fill.symbol, direction, quantity, average: price,
        collateral: addedCollateral, campaign: fill.campaign_id || key });
      const campaign = fill.campaign_id || key;
      if (!this.campaigns.has(campaign)) this.campaigns.set(campaign, { direction, pnl: 0, fees: 0 });
      this.campaigns.get(campaign).fees += fee;
      if (direction === -1) this.shortEntries++;
    } else {
      assert.ok(lot && lot.direction === direction && quantity <= lot.quantity, 'Reduction exceeds unit holding');
      this.cash += direction * value - fee;
      const pnl = direction * (price - lot.average) * quantity;
      const kind = direction === 1 ? 'long' : 'short';
      this.realized[kind] += pnl;
      const campaign = this.campaigns.get(lot.campaign);
      campaign.pnl += pnl; campaign.fees += fee;
      lot.collateral *= (lot.quantity - quantity) / lot.quantity;
      lot.quantity -= quantity;
      if (!lot.quantity) this.lots.delete(key);
      if (![...this.lots.values()].some(p => p.campaign === lot.campaign)) {
        this.closed[kind].count++;
        this.closed[kind].wins += Number(campaign.pnl - campaign.fees > 0);
        this.closed[kind].realized_pnl_before_fees += campaign.pnl;
        this.campaigns.delete(lot.campaign);
      }
      if (direction === -1) this.shortCovers++;
    }
    this.turnover += value; this.fees += fee;
  }
  values(price) {
    let shares = 0, exposure = 0, shortLiability = 0, collateral = 0;
    for (const lot of this.lots.values()) {
      shares += lot.direction * lot.quantity;
      exposure += lot.quantity * price;
      if (lot.direction === -1) shortLiability += lot.quantity * price;
      collateral += lot.collateral;
    }
    const net = shares * (price || 0);
    return { cash: this.cash, equity: this.cash + net, exposure, net_exposure: net,
      short_liability: shortLiability, collateral, borrow_costs: this.borrowCosts, shares };
  }
  mark(price) { this.previousClose = price; return this.values(price); }
}

function validateConfigs(configs) {
  const base = configs['channel-long'];
  assert.ok(base, 'Missing ATR long-only control');
  for (const [name, config] of Object.entries(configs)) {
    const normalized = structuredClone(config);
    const chandelier = name.startsWith('chandelier-'), short = name.includes('-short-');
    assert.equal(config.strategy.exit_strategy, chandelier ? 'chandelier' : 'turtle_channel');
    assert.equal(config.strategy.allow_short, short);
    assert.equal(Number(config.execution.borrow_rate), name.endsWith('3pct') ? 0.03 : 0);
    normalized.strategy.exit_strategy = 'turtle_channel'; normalized.strategy.allow_short = false;
    normalized.execution.borrow_rate = base.execution.borrow_rate;
    assert.deepEqual(normalized, base, 'Unintended configuration difference');
  }
  for (const [key, value] of Object.entries({ entry_strategy: 'turtle_s2', interval_minutes: 1,
    entry_length: 55, exit_length: 20, atr_length: 20, atr_multiple: 2,
    chandelier_timeframe: 'session', chandelier_length: 22, chandelier_atr_length: 22,
    chandelier_multiple: 3, chandelier_ratchet: true })) assert.equal(base.strategy[key], value);
  assert.equal(base.risk.position_sizing, 'atr'); assert.equal(base.risk.max_units, 1);
  assert.equal(Number(base.risk.starting_cash), 25000); assert.equal(Number(base.risk.unit_fraction), 0.01);
  assert.equal(Number(base.execution.slippage_bps), 5);
  assert.equal(Number(base.execution.fee_per_order), 0); assert.equal(Number(base.execution.fee_per_share), 0);
  assert.equal(Number(base.execution.initial_short_collateral), 1.5);
  assert.equal(Number(base.execution.maintenance_short_collateral), 1.3);
  assert.equal(base.execution.market_hours, 'regular');
  assert.deepEqual(base.execution.shortable_symbols, ['QQQ']);
}

async function auditStrategy(directory, sourceFile, bh, halts, config) {
  const read = name => JSON.parse(fs.readFileSync(path.join(directory, name), 'utf8'));
  const run = read('run.json'), fills = read('fills.json'), audit = read('audit.json');
  assert.equal(run.status, 'complete'); assert.ok(Array.isArray(fills));
  assert.equal(run.summary.price_basis, 'adjusted_research');
  assert.equal(run.summary.exit_strategy, config.strategy.exit_strategy);
  const ledger = new ShortLedger({ startingCash: Number(config.risk.starting_cash),
    borrowRate: Number(config.execution.borrow_rate), allowShort: config.strategy.allow_short,
    shortCollateral: Number(config.execution.initial_short_collateral) });
  const stats = equityStats(Number(config.risk.starting_cash));
  const source = csvRows(sourceFile)[Symbol.asyncIterator]();
  let applied = 0, csvFills = 0, lastFillTime = -Infinity, lastEquity, error = 0;
  let maxGross = 0, maxNet = -Infinity, minNet = Infinity, sumGross = 0, sumNet = 0;
  let previousUtcDay, currentNyDay;
  async function nextSource() {
    let point;
    do { point = await source.next(); }
    while (!point.done && Date.parse(point.value.timestamp) < Date.parse(TRADE_START));
    return point.done ? null : point.value;
  }
  try {
    for await (const row of csvRows(path.join(directory, 'report.csv'))) {
      if (row.record_type === 'equity') {
        const point = await nextSource(); assert.ok(point, 'Unexpected extra equity mark');
        const time = Date.parse(row.time), openTime = Date.parse(point.timestamp);
        assert.equal(time, openTime + 60000, 'Equity/source timestamp mismatch');
        const utcDay = point.timestamp.slice(0, 10);
        if (utcDay !== previousUtcDay) {
          currentNyDay = nyDate.format(new Date(openTime)); previousUtcDay = utcDay;
        }
        // Borrow charges precede the day's opening fills, using the previous close.
        ledger.beforeDay(currentNyDay);
        while (applied < fills.length && fillMark(fills[applied]) <= time) {
          assert.equal(fillMark(fills[applied]), time, 'Fill lacks an observed source candle');
          assert.ok(Date.parse(fills[applied].time) >= Date.parse(TRADE_START));
          ledger.apply(fills[applied++], halts);
        }
        const values = ledger.mark(Number(point.close));
        for (const key of ['cash', 'equity', 'exposure', 'net_exposure', 'short_liability', 'collateral', 'borrow_costs']) {
          assert.ok(row[key] !== undefined && row[key] !== '', `Missing snapshot ${key}`);
          near(values[key], Number(row[key]), `${row.time} ${key}`);
          error = Math.max(error, Math.abs(values[key] - Number(row[key])));
        }
        lastEquity = values;
        const gross = values.exposure / values.equity, net = values.net_exposure / values.equity;
        maxGross = Math.max(maxGross, gross); maxNet = Math.max(maxNet, net); minNet = Math.min(minNet, net);
        sumGross += gross; sumNet += net;
        stats.mark(row.time, values.equity);
      } else if (row.record_type === 'fill') {
        const fill = fills[csvFills++]; assert.ok(fill, 'Unexpected CSV fill');
        const at = Date.parse(row.time);
        assert.ok(at >= lastFillTime, 'Nonchronological fills'); lastFillTime = at;
        for (const key of ['side', 'symbol', 'intent', 'unit_id', 'campaign_id', 'time_precision', 'reason'])
          assert.equal(row[key], String(fill[key] ?? ''));
        assert.equal(at, Date.parse(fill.time)); assert.equal(Number(row.direction), Number(fill.direction));
        for (const key of ['quantity', 'price', 'fee']) near(Number(row[key]), Number(fill[key]), `CSV fill ${key}`);
      } else throw Error(`Unexpected record: ${row.record_type}`);
    }
    assert.equal(await nextSource(), null, 'Missing final equity marks');
  } finally { await source.return(); }
  const observed = stats.result(), closed = ledger.closed.long.count + ledger.closed.short.count;
  assert.equal(applied, fills.length); assert.equal(csvFills, fills.length);
  assert.equal(observed.equity_points, bh.equity_points);
  assert.equal(observed.first_mark, bh.first_mark); assert.equal(observed.last_mark, bh.last_mark);
  assert.equal(observed.equity_points, run.counts.equity); assert.equal(fills.length, run.counts.fills);
  assert.equal(closed, run.summary.closed_trade_count);
  for (const key of ['ending_equity', 'total_return', 'max_drawdown']) near(observed[key], run.summary[key], key);
  near(ledger.turnover, run.summary.gross_turnover_dollars, 'Turnover');
  near(ledger.fees, run.summary.fees, 'Fees'); near(ledger.borrowCosts, run.summary.borrow_costs, 'Borrow fees');
  near(sumGross / observed.equity_points, run.summary.average_exposure_fraction, 'Average gross exposure');
  near(maxGross, run.summary.max_exposure_fraction, 'Maximum gross exposure');
  for (const [key, value] of Object.entries({ ending_cash: ledger.cash, borrow_costs: ledger.borrowCosts,
    realized_pnl_before_fees: ledger.realized.long + ledger.realized.short })) near(value, Number(audit.metrics[key]), key);
  const reasonCount = (items, reason) => (items || []).filter(item => item.reason === reason).length;
  return { ...observed, closed_trade_count: closed, fills: fills.length,
    short_open_fills: ledger.shortEntries, short_cover_fills: ledger.shortCovers,
    closed_trades_by_direction: ledger.closed, realized_pnl_before_fees_by_direction: ledger.realized,
    borrow_costs: ledger.borrowCosts, borrow_charge_sessions: ledger.borrowCharges, fees: ledger.fees,
    gross_turnover_dollars: ledger.turnover, final_shares: lastEquity.shares, final_cash: ledger.cash,
    final_collateral: lastEquity.collateral, final_short_liability: lastEquity.short_liability,
    average_gross_exposure_fraction: sumGross / observed.equity_points,
    average_net_exposure_fraction: sumNet / observed.equity_points,
    max_gross_exposure_fraction: maxGross, max_net_exposure_fraction: maxNet, min_net_exposure_fraction: minNet,
    margin_records: { forced_cover_fills: reasonCount(fills, 'forced_cover'),
      forced_cover_orders: reasonCount(audit.orders, 'forced_cover'),
      margin_breach_cancellations: reasonCount(audit.cancellations, 'margin_breach') },
    ledger_max_error_dollars: error, audit: 'passed', run_id: run.run_id, fingerprint: run.fingerprint,
    cli_summary: run.summary, config };
}

async function checksum(file) {
  const hash = crypto.createHash('sha256');
  for await (const chunk of fs.createReadStream(file)) hash.update(chunk);
  return hash.digest('hex');
}

async function runAudit(root = path.join(__dirname, '..')) {
  const source = path.join(root, 'runs/qqq-1m-20160223-20261023'), sourceFile = path.join(source, 'bars.csv');
  const outputs = path.join(root, 'runs/qqq-turtle-shorts-1m');
  const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
  const coverage = read(path.join(source, 'coverage.json')), provenance = read(path.join(source, 'provenance.json'));
  assert.equal(coverage.complete, true); assert.equal(coverage.missing_count, 0); assert.equal(coverage.outside_count, 0);
  const hash = await checksum(sourceFile); assert.equal(hash, provenance.artifact_checksums['bars.csv']);
  const names = ['channel-long', 'channel-short-free', 'channel-short-3pct',
    'chandelier-long', 'chandelier-short-free', 'chandelier-short-3pct'];
  const configs = Object.fromEntries(names.map(name => [name, read(path.join(outputs, name, 'config.json'))]));
  validateConfigs(configs);
  let firstMetadata;
  for (const name of names) {
    const meta = read(path.join(outputs, name, 'metadata.json'));
    assert.equal(meta.input_csv_sha256, hash); assert.deepEqual(meta.resolved_config, configs[name]);
    assert.equal(Date.parse(meta.trade_start), Date.parse(TRADE_START)); assert.equal(meta.trade_end, null);
    assert.equal(meta.manifest.interval_minutes, 1); assert.equal(meta.manifest.price_basis, 'adjusted_research');
    if (firstMetadata) for (const key of ['data_sha256', 'manifest', 'engine_sources', 'runner_sha256'])
      assert.deepEqual(meta[key], firstMetadata[key], `Inconsistent ${key}`);
    firstMetadata ??= meta;
  }
  const bh = await benchmark(csvRows(sourceFile)), strategies = {};
  for (const name of names) strategies[name] = await auditStrategy(path.join(outputs, name), sourceFile, bh,
    provenance.market_halt_policy.intervals, configs[name]);
  return { source: { path: path.relative(root, sourceFile), sha256: hash, bars: coverage.observed_count,
    missing: coverage.missing_count, halted: coverage.halted_count }, trade_start: TRADE_START,
    warmup_sessions: 55, benchmark: bh, strategies, notes: [
      'All six experiments use ATR sizing and max_units=1: 1% of sizing capital per N, approximately 2% at the initial 2N stop before gaps and costs.',
      'Shortability is assumed; 0% and 3% annual borrow are scenarios, not historical loan observations.',
      'Borrow accrues over calendar gaps before opening fills against prior close short liability.',
      'Every minute reconciles signed shares, cash, gross/net exposure, short liability, collateral and borrow fees.',
      'Direction PnL excludes borrow fees; account returns include them. Closed-trade wins deduct execution fees.',
      'Adjusted research prices; no separate dividend cash or historical share transformations.',
      'Drawdown samples minute closes. Final holdings are marked without forced liquidation.' ] };
}

module.exports = { ShortLedger, validateConfigs, auditStrategy, runAudit };
if (require.main === module) runAudit().then(result => process.stdout.write(JSON.stringify(result, null, 2) + '\n'))
  .catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; });
