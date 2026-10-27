// Audit production CLI exports without loading multi-GB JSON reports.
// Run: node research/turtle-minute-audit.cjs
const fs = require('node:fs');
const path = require('node:path');
const readline = require('node:readline');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');

const TRADE_START = '2016-05-11T13:30:00Z';

function parseCsvLine(line) {
  const fields = [];
  let value = '', quoted = false, closed = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (quoted) {
      if (c === '"') {
        if (line[i + 1] === '"') { value += '"'; i++; }
        else { quoted = false; closed = true; }
      } else value += c;
    } else if (c === ',') {
      fields.push(value); value = ''; closed = false;
    } else if (c === '"') {
      if (value || closed) throw Error('Invalid CSV quote');
      quoted = true;
    } else {
      if (closed) throw Error('Unexpected characters after CSV quote');
      value += c;
    }
  }
  if (quoted) throw Error('Unclosed CSV quote; multiline records are unsupported');
  fields.push(value);
  return fields;
}

async function* csvRows(file) {
  const input = fs.createReadStream(file, { encoding: 'utf8' });
  const lines = readline.createInterface({ input, crlfDelay: Infinity });
  let columns;
  try {
    for await (const line of lines) {
      const fields = parseCsvLine(line);
      if (!columns) {
        columns = fields;
        if (new Set(columns).size !== columns.length) throw Error('Duplicate CSV columns');
      } else {
        if (fields.length !== columns.length) throw Error('CSV field count mismatch');
        yield Object.fromEntries(columns.map((name, i) => [name, fields[i]]));
      }
    }
    if (!columns) throw Error('Empty CSV file');
  } finally { lines.close(); input.destroy(); }
}

function decimal(value) {
  const match = /^(\d+)(?:\.(\d+))?$/.exec(String(value));
  if (!match) throw Error('Expected nonnegative plain decimal');
  return [BigInt(match[1] + (match[2] || '')), 10n ** BigInt((match[2] || '').length)];
}

function exactEntry(startingCash, open, slippageBps) {
  if (!Number.isInteger(slippageBps) || slippageBps < 0) throw Error('Invalid slippage');
  const [cashN, cashD] = decimal(startingCash);
  const [priceN, priceD] = decimal(open);
  if (cashN <= 0n || priceN <= 0n) throw Error('Cash and price must be positive');
  const fillN = priceN * BigInt(10000 + slippageBps), fillD = priceD * 10000n;
  const shares = cashN * fillD / (cashD * fillN);
  if (shares > BigInt(Number.MAX_SAFE_INTEGER)) throw Error('Unsafe share count');
  const residualN = cashN * fillD - shares * fillN * cashD;
  return { shares: Number(shares), fillPrice: Number(fillN) / Number(fillD),
    cash: Number(residualN) / Number(cashD * fillD) };
}

function equityStats(startingCash) {
  let peak = startingCash, drawdown = 0, ending, first, last, count = 0;
  const yearlyEnd = new Map(), sessions = new Set();
  return {
    mark(time, equity) {
      const at = Date.parse(time);
      if (!Number.isFinite(at) || !Number.isFinite(equity) || equity <= 0 ||
          (last !== undefined && at <= last)) throw Error('Invalid/nonchronological equity mark');
      first ??= at; last = at; ending = equity; count++;
      peak = Math.max(peak, equity); drawdown = Math.max(drawdown, 1 - equity / peak);
      const date = new Date(at).toISOString().slice(0, 10);
      sessions.add(date); yearlyEnd.set(date.slice(0, 4), equity);
    },
    result() {
      if (!count) throw Error('No equity marks');
      let previous = startingCash;
      const yearlyReturns = {};
      for (const [year, value] of yearlyEnd) { yearlyReturns[year] = value / previous - 1; previous = value; }
      return { starting_equity: startingCash, ending_equity: ending,
        total_return: ending / startingCash - 1, max_drawdown: drawdown,
        cagr: (ending / startingCash) ** (252 / sessions.size) - 1,
        equity_points: count, sessions: sessions.size,
        first_mark: new Date(first).toISOString(), last_mark: new Date(last).toISOString(),
        yearly_returns: yearlyReturns };
    },
  };
}

async function benchmark(rows, { tradeStart = TRADE_START, startingCash = 25000,
  slippageBps = 5, warmupSessions = 55 } = {}) {
  const start = Date.parse(tradeStart), priorDays = new Set();
  if (!Number.isFinite(start)) throw Error('Invalid trade start');
  const stats = equityStats(startingCash);
  let entry, entryTime, last = -Infinity;
  for await (const row of rows) {
    const at = Date.parse(row.timestamp);
    if (row.symbol !== 'QQQ' || !Number.isFinite(at) || at <= last || at % 60000) {
      throw Error('Invalid source symbol/timestamp');
    }
    last = at;
    if (at < start) { priorDays.add(row.timestamp.slice(0, 10)); continue; }
    if (!entry) {
      if (at !== start) throw Error('Missing exact benchmark opening');
      assert.equal(priorDays.size, warmupSessions, 'Unexpected common warmup');
      entry = exactEntry(startingCash, row.open, slippageBps);
      entryTime = new Date(at).toISOString();
    }
    const close = Number(row.close);
    if (!(close > 0)) throw Error('Invalid benchmark closing price');
    stats.mark(new Date(at + 60000).toISOString(), entry.cash + entry.shares * close);
  }
  if (!entry) throw Error('No benchmark trading data');
  return { ...stats.result(), ...entry, entry_time: entryTime, orders: 1,
    exit_policy: 'open position marked at final close; no forced sale', slippage_bps: slippageBps };
}

function closeTo(actual, expected, label) {
  assert.ok(Number.isFinite(actual) && Number.isFinite(expected), `${label}: nonfinite value`);
  assert.ok(Math.abs(actual - expected) <= 1e-9 * Math.max(1, Math.abs(expected)),
    `${label}: ${actual} differs from ${expected}`);
}

function fillMark(fill) {
  const at = Date.parse(fill.time);
  if (!Number.isFinite(at)) throw Error('Invalid fill timestamp');
  if (fill.time_precision === 'intrabar_unknown') {
    const end = Date.parse(fill.bar_end);
    if (!Number.isFinite(end) || end <= at || end % 60000 || end - at > 60000) {
      throw Error('Invalid intrabar fill boundary');
    }
    return end;
  }
  if (fill.time_precision !== 'bar_open') throw Error('Unknown fill precision');
  return Math.floor(at / 60000) * 60000 + 60000;
}

function validateConfigs(channel, chandelier) {
  const other = structuredClone(chandelier);
  assert.equal(other.strategy.exit_strategy, 'chandelier');
  other.strategy.exit_strategy = 'turtle_channel';
  assert.deepEqual(channel, other, 'Exit comparisons must otherwise have identical settings');
  assert.equal(channel.strategy.exit_strategy, 'turtle_channel');
  for (const [key, value] of Object.entries({ entry_strategy: 'turtle_s2', interval_minutes: 1,
    entry_length: 55, exit_length: 20, atr_length: 20, atr_multiple: 2,
    allow_short: false, chandelier_timeframe: 'session', chandelier_length: 22,
    chandelier_atr_length: 22, chandelier_multiple: 3, chandelier_ratchet: true })) {
    assert.equal(channel.strategy[key], value, `Unexpected strategy.${key}`);
  }
  assert.equal(channel.risk.position_sizing, 'available_cash');
  assert.equal(channel.risk.max_units, 1);
  assert.equal(Number(channel.risk.starting_cash), 25000);
  assert.equal(Number(channel.execution.slippage_bps), 5);
  assert.equal(Number(channel.execution.fee_per_order), 0);
  assert.equal(Number(channel.execution.fee_per_share), 0);
  assert.equal(channel.execution.market_hours, 'regular');
}

async function auditStrategy(directory, sourceFile, bh, halts) {
  const config = JSON.parse(fs.readFileSync(path.join(directory, 'config.json'), 'utf8'));
  const run = JSON.parse(fs.readFileSync(path.join(directory, 'run.json'), 'utf8'));
  assert.equal(run.summary.price_basis, 'adjusted_research');
  assert.equal(run.summary.exit_strategy, config.strategy.exit_strategy);
  const ledgerFills = JSON.parse(fs.readFileSync(path.join(directory, 'fills.json'), 'utf8'));
  if (!Array.isArray(ledgerFills)) throw Error('Expected fill ledger array');
  const stats = equityStats(Number(config.risk.starting_cash));
  let fills = 0, shares = 0, closed = 0, turnover = 0, fees = 0, fillLast = -Infinity;
  let cash = Number(config.risk.starting_cash), ledgerShares = 0, ledgerIndex = 0, ledgerMaxError = 0;
  const expected = csvRows(sourceFile)[Symbol.asyncIterator]();
  let expectedPoint;
  async function nextSourceMark() {
    do { expectedPoint = await expected.next(); }
    while (!expectedPoint.done && Date.parse(expectedPoint.value.timestamp) < Date.parse(TRADE_START));
    return expectedPoint.done ? null : Date.parse(expectedPoint.value.timestamp) + 60000;
  }
  try {
    for await (const row of csvRows(path.join(directory, 'report.csv'))) {
      if (row.price_basis !== undefined) assert.equal(row.price_basis, 'adjusted_research');
      if (row.exit_strategy !== undefined) assert.equal(row.exit_strategy, config.strategy.exit_strategy);
      if (row.record_type === 'equity') {
        const time = Date.parse(row.time);
        assert.equal(time, await nextSourceMark(), 'Equity/source timestamp mismatch');
        while (ledgerIndex < ledgerFills.length && fillMark(ledgerFills[ledgerIndex]) <= time) {
          const fill = ledgerFills[ledgerIndex++];
          assert.equal(fillMark(fill), time, 'Fill has no matching observed source candle');
          const quantity = Number(fill.quantity), price = Number(fill.price), fee = Number(fill.fee);
          assert.ok(Number.isSafeInteger(quantity) && quantity > 0 && price > 0 && fee >= 0);
          assert.ok(['buy', 'sell'].includes(fill.side));
          if (fill.side === 'buy') { cash -= quantity * price + fee; ledgerShares += quantity; }
          else { cash += quantity * price - fee; ledgerShares -= quantity; }
          assert.ok(cash >= -1e-7 && ledgerShares >= 0, 'Fill ledger violates cash-funded long sizing');
        }
        const observedEquity = Number(row.equity);
        const reconstructed = cash + ledgerShares * Number(expectedPoint.value.close);
        ledgerMaxError = Math.max(ledgerMaxError, Math.abs(reconstructed - observedEquity));
        closeTo(reconstructed, observedEquity, 'Source-price cash/share ledger equity');
        if (row.cash !== undefined) closeTo(cash, Number(row.cash), 'Cash ledger');
        if (row.exposure !== undefined) closeTo(ledgerShares * Number(expectedPoint.value.close),
          Number(row.exposure), 'Source-price exposure ledger');
        stats.mark(row.time, observedEquity);
      } else if (row.record_type === 'fill') {
        const at = Date.parse(row.time), quantity = Number(row.quantity), price = Number(row.price);
        assert.ok(Number.isFinite(at) && at >= Date.parse(TRADE_START) && at >= fillLast);
        assert.ok(!halts.some(h => Date.parse(h.start) <= at && at < Date.parse(h.end)), 'Fill during halt');
        assert.ok(Number.isSafeInteger(quantity) && quantity > 0 && price > 0);
        assert.ok(['buy', 'sell'].includes(row.side));
        const ledgerFill = ledgerFills[fills];
        assert.ok(ledgerFill, 'CSV contains an unexpected fill');
        assert.equal(at, Date.parse(ledgerFill.time));
        assert.equal(row.side, ledgerFill.side); assert.equal(quantity, Number(ledgerFill.quantity));
        closeTo(price, Number(ledgerFill.price), 'CSV/fill-ledger price');
        closeTo(Number(row.fee), Number(ledgerFill.fee), 'CSV/fill-ledger fee');
        shares += (row.side === 'buy' ? 1 : -1) * quantity;
        assert.ok(shares >= 0, 'Unexpected short position');
        if (!shares && row.side === 'sell') closed++;
        turnover += quantity * price; fees += Number(row.fee); fills++; fillLast = at;
      } else throw Error(`Unexpected report record type: ${row.record_type}`);
    }
    assert.equal(await nextSourceMark(), null, 'Missing final strategy equity marks');
  } finally { await expected.return(); }
  const observed = stats.result();
  assert.equal(observed.equity_points, bh.equity_points);
  assert.equal(ledgerIndex, ledgerFills.length, 'Unapplied fill ledger records');
  assert.equal(fills, ledgerFills.length, 'CSV/fill-ledger count mismatch');
  assert.equal(shares, ledgerShares, 'Final share ledger mismatch');
  if (run.counts) {
    assert.equal(observed.equity_points, run.counts.equity, 'Engine/CSV equity count mismatch');
    assert.equal(fills, run.counts.fills, 'Engine/CSV fill count mismatch');
  }
  assert.equal(observed.first_mark, bh.first_mark); assert.equal(observed.last_mark, bh.last_mark);
  assert.equal(closed, run.summary.closed_trade_count);
  for (const key of ['ending_equity', 'total_return', 'max_drawdown']) closeTo(observed[key], run.summary[key], key);
  closeTo(turnover, run.summary.gross_turnover_dollars, 'gross turnover');
  closeTo(fees, run.summary.fees, 'fees');
  return { ...observed, fills, closed_trade_count: closed, final_shares: shares,
    gross_turnover_dollars: turnover, fees, cli_summary: run.summary, config,
    final_cash: cash, ledger_max_equity_error_dollars: ledgerMaxError,
    cash_share_ledger: 'every minute reconciled against actual source close and ordered fills',
    run_id: run.run_id, fingerprint: run.fingerprint, audit: 'passed' };
}

async function checksum(file) {
  const hash = crypto.createHash('sha256');
  for await (const chunk of fs.createReadStream(file)) hash.update(chunk);
  return hash.digest('hex');
}

async function runAudit(root = path.join(__dirname, '..')) {
  const source = path.join(root, 'runs/qqq-1m-20160223-20261023');
  const outputs = path.join(root, 'runs/qqq-turtle-exits-1m-streamed');
  const sourceFile = path.join(source, 'bars.csv');
  const coverage = JSON.parse(fs.readFileSync(path.join(source, 'coverage.json'), 'utf8'));
  const provenance = JSON.parse(fs.readFileSync(path.join(source, 'provenance.json'), 'utf8'));
  assert.equal(coverage.complete, true); assert.equal(coverage.missing_count, 0);
  assert.equal(coverage.outside_count, 0);
  const hash = await checksum(sourceFile);
  assert.equal(hash, provenance.artifact_checksums['bars.csv']);
  const channelConfig = JSON.parse(fs.readFileSync(path.join(outputs, 'channel/config.json'), 'utf8'));
  const chandelierConfig = JSON.parse(fs.readFileSync(path.join(outputs, 'chandelier/config.json'), 'utf8'));
  validateConfigs(channelConfig, chandelierConfig);
  const bh = await benchmark(csvRows(sourceFile));
  const results = {};
  for (const kind of ['channel', 'chandelier']) {
    results[kind] = await auditStrategy(path.join(outputs, kind), sourceFile, bh,
      provenance.market_halt_policy.intervals);
  }
  return { source: { path: path.relative(root, sourceFile), sha256: hash,
    bars: coverage.observed_count, missing: coverage.missing_count, halted: coverage.halted_count },
    trade_start: TRADE_START, warmup_sessions: 55, benchmark: bh, strategies: results,
    notes: ['Production run_backtest with unchanged strategy/execution logic and streamed artifact packaging.',
      'Manual benchmark includes whole shares, residual cash and entry slippage.',
      'Minute-close sampled drawdown is not the worst tick-level loss.',
      'Final positions remain marked; neither benchmark nor strategies force a closing sale.'] };
}

module.exports = { parseCsvLine, csvRows, exactEntry, benchmark, equityStats, fillMark,
  validateConfigs, auditStrategy, runAudit };
if (require.main === module) runAudit().then(r => process.stdout.write(JSON.stringify(r, null, 2) + '\n'))
  .catch(e => { process.stderr.write(e.stack + '\n'); process.exitCode = 1; });
