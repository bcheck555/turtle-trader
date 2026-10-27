// Run: node research/qqq-minute-comparison.cjs
// All original strategy rules, warmup, validation split and costs are unchanged.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const { loadDays, research, researchDays } = require('./qqq-patterns.cjs');

function fixedClockDays(csv, days) {
  // XNYS regular sessions close on an hour boundary, including early closes.
  // The final observed xx:00/xx:30 opening is the old final 30-minute-bar clock.
  const marks = new Map();
  const lastTimes = new Map();
  for (const line of csv.trim().split(/\r?\n/).slice(1)) {
    const [symbol, timestamp, open] = line.split(',', 3);
    if (symbol !== 'QQQ') throw Error('Unexpected symbol');
    const date = timestamp.slice(0, 10);
    const at = Date.parse(timestamp);
    lastTimes.set(date, at);
    if (Number(timestamp.slice(14, 16)) % 30 === 0) {
      marks.set(date, { at, open: Number(open) });
    }
  }
  return days.map(day => {
    const mark = marks.get(day.date);
    if (!mark || !(mark.open > 0) || mark.at !== lastTimes.get(day.date) - 29 * 60000) {
      throw Error(`Missing fixed execution clock on ${day.date}`);
    }
    return { ...day, lastOpen: mark.open };
  });
}

function runComparison(root = path.join(__dirname, '..')) {
  const minuteDir = path.join(root, 'runs/qqq-1m-20160223-20261023');
  const minutePath = path.join(minuteDir, 'bars.csv');
  const baselinePath = path.join(root, 'runs/turtle-trader-equity-5y-20261026/qqq/bars.csv');
  const priorPath = path.join(root, 'research/qqq-patterns-results.json');
  const csv = fs.readFileSync(minutePath, 'utf8');
  const hash = crypto.createHash('sha256').update(csv).digest('hex');
  const provenance = JSON.parse(fs.readFileSync(path.join(minuteDir, 'provenance.json'), 'utf8'));
  const coverage = JSON.parse(fs.readFileSync(path.join(minuteDir, 'coverage.json'), 'utf8'));
  assert.equal(hash, provenance.artifact_checksums['bars.csv'], 'Minute checksum mismatch');
  assert.equal(coverage.complete, true, 'Minute coverage incomplete');
  assert.equal(coverage.missing_count, 0, 'Unknown minute gaps');
  assert.equal(coverage.outside_count, 0, 'Unexpected minute bars');
  const nativeDays = loadDays(csv);
  const fixedDays = fixedClockDays(csv, nativeDays);
  assert.equal(nativeDays.reduce((s, d) => s + d.bars, 0), coverage.observed_count);

  const prior = JSON.parse(fs.readFileSync(priorPath, 'utf8'));
  const baselineCsv = fs.readFileSync(baselinePath, 'utf8');
  const baselineDays = loadDays(baselineCsv);
  const baseline = research(baselineCsv);
  assert.equal(baseline.dataSha256, prior.dataSha256, 'Original dataset changed');
  assert.deepEqual(baseline, prior, 'Original results no longer reproduce');
  const overlapFixed = fixedDays.filter(d => d.date >= baseline.dataStart && d.date <= baseline.dataEnd);
  const overlapNative = nativeDays.filter(d => d.date >= baseline.dataStart && d.date <= baseline.dataEnd);
  assert.equal(overlapFixed.length, baselineDays.length);
  for (let i = 0; i < overlapFixed.length; i++) {
    for (const field of ['date', 'open', 'high', 'low', 'close', 'lastOpen']) {
      assert.equal(overlapFixed[i][field], baselineDays[i][field],
        `Daily price/clock mismatch: ${baselineDays[i].date} ${field}`);
    }
  }
  const overlapControl = researchDays(overlapFixed, hash);
  assert.deepEqual(overlapControl.windows, prior.windows);
  assert.deepEqual(overlapControl.rows, prior.rows, 'Resolution-only control changed');
  return {
    source: { minutePath: path.relative(root, minutePath), minuteSha256: hash,
      baselinePath: path.relative(root, baselinePath), baselineSha256: baseline.dataSha256,
      observedBars: coverage.observed_count, unknownMissing: coverage.missing_count,
      verifiedHaltSlots: coverage.halted_count },
    assumptions: { warmupSessions: 200, validationStart: '2025-01-01', costsBpsPerSide: [0, 5, 10],
      alternatives: prior.strategyCount, fixedClock: '15:30 New York; 12:30 on early closes',
      finalMinuteClock: '15:59 New York; 12:59 on early closes',
      signalSampling: 'completed daily OHLC', drawdownSampling: 'next-session open plus terminal mark',
      validationStatus: 'reused chronological validation, not unseen data' },
    resolutionControl: { sharedInputSessions: baselineDays.length,
      inputStart: baseline.dataStart, inputEnd: baseline.dataEnd,
      dailyOhlcAndExecutionPricesIdentical: true, priorResultsExactlyReproduced: true,
      oneMinuteRowsExactlyEqualPrior: true, windows: overlapControl.windows },
    scenarios: {
      extendedFixedClock: researchDays(fixedDays, hash),
      extendedFinalMinuteClock: researchDays(nativeDays, hash),
      overlapFinalMinuteClock: researchDays(overlapNative, hash),
    },
  };
}

module.exports = { fixedClockDays, runComparison };
if (require.main === module) console.log(JSON.stringify(runComparison(), null, 2));
