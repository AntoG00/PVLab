/* Exercise worker queue, concurrency limit parsing, FIFO order, and prefetch behavior in tests/test_solar_ui.js */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const sourcePath = path.join(__dirname, 'test_solar_ui.js');
const source = fs.readFileSync(sourcePath, 'utf8');

const targetIdx = source.indexOf('function pythonEvaluate');
assert(targetIdx > 0, 'function pythonEvaluate marker found in test_solar_ui.js');
const prefixCode = source.slice(0, targetIdx);

const exportSnippet = `
; exports.getWorkerLimit = getWorkerLimit;
exports.runWorkerTask = runWorkerTask;
exports.prefetch = prefetch;
exports.getMaxWorkers = () => maxWorkers;
exports.getActiveWorkers = () => activeWorkers;
exports.getWorkerQueue = () => workerQueue;
`;

const fullCode = prefixCode + '\n' + exportSnippet;

const flushMicrotasks = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

function createDeferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function createWorkerScope(envOverrides = {}) {
  const exportsObj = {};
  const env = { ...process.env, ...envOverrides };
  for (const [k, v] of Object.entries(envOverrides)) {
    if (v === undefined) {
      delete env[k];
    }
  }
  const mockProcess = {
    env,
    cwd: () => path.join(__dirname, '..')
  };
  const context = vm.createContext({
    require,
    __dirname: path.dirname(sourcePath),
    process: mockProcess,
    exports: exportsObj,
    module: { exports: exportsObj },
    console,
    Promise,
    Number,
    JSON,
    Error,
    setImmediate,
    clearImmediate,
    setTimeout,
    clearTimeout
  });
  vm.runInContext(fullCode, context);
  return exportsObj;
}

test('workerlimit1unset: worker limit defaults to 1 when PV_TEST_WORKERS is unset', () => {
  const scope = createWorkerScope({ PV_TEST_WORKERS: undefined });
  assert.strictEqual(scope.getWorkerLimit(), 1);
  assert.strictEqual(scope.getMaxWorkers(), 1);
});

test('positive14: worker limit accepts positive integer 14 and parses correctly', () => {
  const scope14 = createWorkerScope({ PV_TEST_WORKERS: '14' });
  assert.strictEqual(scope14.getWorkerLimit(), 14);
  assert.strictEqual(scope14.getMaxWorkers(), 14);

  const scope14Trimmed = createWorkerScope({ PV_TEST_WORKERS: '  14  ' });
  assert.strictEqual(scope14Trimmed.getWorkerLimit(), 14);
  assert.strictEqual(scope14Trimmed.getMaxWorkers(), 14);

  const scope4 = createWorkerScope({ PV_TEST_WORKERS: '4' });
  assert.strictEqual(scope4.getWorkerLimit(), 4);
  assert.strictEqual(scope4.getMaxWorkers(), 4);

  const scope1 = createWorkerScope({ PV_TEST_WORKERS: '1' });
  assert.strictEqual(scope1.getWorkerLimit(), 1);
  assert.strictEqual(scope1.getMaxWorkers(), 1);
});

test('malformed falls1 currentcontract: non-positive, floats, whitespace and malformed inputs fall back to 1', () => {
  const malformedInputs = [
    '0',
    '-1',
    '-14',
    'abc',
    '14abc',
    'abc14',
    '1.4',
    '14.0',
    '',
    '   ',
    '014',
    'Infinity',
    '-Infinity',
    'NaN',
    String(Number.MAX_SAFE_INTEGER + 1000)
  ];

  for (const input of malformedInputs) {
    const scope = createWorkerScope({ PV_TEST_WORKERS: input });
    assert.strictEqual(scope.getWorkerLimit(), 1, `Expected 1 for malformed input: ${JSON.stringify(input)}`);
    assert.strictEqual(scope.getMaxWorkers(), 1, `Expected maxWorkers 1 for malformed input: ${JSON.stringify(input)}`);
  }
});

test('queue cap2 prove controlled deferred promises and 3rd starts afterfirst completion', async () => {
  const scope = createWorkerScope({ PV_TEST_WORKERS: '2' });
  assert.strictEqual(scope.getMaxWorkers(), 2);
  assert.strictEqual(scope.getActiveWorkers(), 0);

  const d1 = createDeferred();
  const d2 = createDeferred();
  const d3 = createDeferred();

  let task1Started = false;
  let task2Started = false;
  let task3Started = false;

  let task1Finished = false;
  let task2Finished = false;
  let task3Finished = false;

  const p1 = scope.runWorkerTask(() => {
    task1Started = true;
    return d1.promise.then(val => { task1Finished = true; return val; });
  });

  const p2 = scope.runWorkerTask(() => {
    task2Started = true;
    return d2.promise.then(val => { task2Finished = true; return val; });
  });

  const p3 = scope.runWorkerTask(() => {
    task3Started = true;
    return d3.promise.then(val => { task3Finished = true; return val; });
  });

  // Since cap is 2, task 1 and task 2 start synchronously; task 3 remains queued
  assert.strictEqual(task1Started, true, 'Task 1 must have started immediately');
  assert.strictEqual(task2Started, true, 'Task 2 must have started immediately');
  assert.strictEqual(task3Started, false, 'Task 3 must not start while 2 workers are active');
  assert.strictEqual(scope.getActiveWorkers(), 2, 'Active workers should equal capacity 2');
  assert.strictEqual(scope.getWorkerQueue().length, 1, 'Queue should hold the 3rd pending task');

  // Complete task 1
  d1.resolve('result-1');
  const res1 = await p1;
  await flushMicrotasks();
  assert.strictEqual(res1, 'result-1');
  assert.strictEqual(task1Finished, true);

  // After task 1 completes, task 3 must have been popped from queue and started
  assert.strictEqual(task3Started, true, 'Task 3 must start after first completion');
  assert.strictEqual(task2Finished, false, 'Task 2 is still running');
  assert.strictEqual(scope.getActiveWorkers(), 2, 'Active workers remain 2 (task 2 and task 3)');
  assert.strictEqual(scope.getWorkerQueue().length, 0, 'Queue is now drained');

  // Complete task 2
  d2.resolve('result-2');
  const res2 = await p2;
  await flushMicrotasks();
  assert.strictEqual(res2, 'result-2');
  assert.strictEqual(task2Finished, true);
  assert.strictEqual(scope.getActiveWorkers(), 1, 'Only task 3 remains active');

  // Complete task 3
  d3.resolve('result-3');
  const res3 = await p3;
  await flushMicrotasks();
  assert.strictEqual(res3, 'result-3');
  assert.strictEqual(task3Finished, true);
  assert.strictEqual(scope.getActiveWorkers(), 0, 'All worker slots released');
});

test('FIFO: queued tasks execute in first-in-first-out order', async () => {
  const scope = createWorkerScope({ PV_TEST_WORKERS: '1' });
  const startOrder = [];
  const finishOrder = [];

  const d1 = createDeferred();
  const d2 = createDeferred();
  const d3 = createDeferred();
  const d4 = createDeferred();

  const p1 = scope.runWorkerTask(() => {
    startOrder.push(1);
    return d1.promise.then(v => { finishOrder.push(1); return v; });
  });
  const p2 = scope.runWorkerTask(() => {
    startOrder.push(2);
    return d2.promise.then(v => { finishOrder.push(2); return v; });
  });
  const p3 = scope.runWorkerTask(() => {
    startOrder.push(3);
    return d3.promise.then(v => { finishOrder.push(3); return v; });
  });
  const p4 = scope.runWorkerTask(() => {
    startOrder.push(4);
    return d4.promise.then(v => { finishOrder.push(4); return v; });
  });

  // Only task 1 should have started under worker cap 1
  assert.deepStrictEqual(startOrder, [1]);
  assert.strictEqual(scope.getWorkerQueue().length, 3);

  // Complete task 1 -> task 2 must start
  d1.resolve('r1');
  await p1;
  await flushMicrotasks();
  assert.deepStrictEqual(startOrder, [1, 2]);
  assert.deepStrictEqual(finishOrder, [1]);

  // Complete task 2 -> task 3 must start
  d2.resolve('r2');
  await p2;
  await flushMicrotasks();
  assert.deepStrictEqual(startOrder, [1, 2, 3]);
  assert.deepStrictEqual(finishOrder, [1, 2]);

  // Complete task 3 -> task 4 must start
  d3.resolve('r3');
  await p3;
  await flushMicrotasks();
  assert.deepStrictEqual(startOrder, [1, 2, 3, 4]);
  assert.deepStrictEqual(finishOrder, [1, 2, 3]);

  // Complete task 4
  d4.resolve('r4');
  await p4;
  await flushMicrotasks();
  assert.deepStrictEqual(finishOrder, [1, 2, 3, 4]);
  assert.strictEqual(scope.getActiveWorkers(), 0);
  assert.strictEqual(scope.getWorkerQueue().length, 0);
});

test('rejection propagates and slot released: async rejection rejects returned promise and releases worker slot', async () => {
  const scope = createWorkerScope({ PV_TEST_WORKERS: '1' });
  const d1 = createDeferred();
  const d2 = createDeferred();

  const err1 = new Error('async failure in worker task 1');
  const p1 = scope.runWorkerTask(() => d1.promise);
  const p2 = scope.runWorkerTask(() => d2.promise);

  assert.strictEqual(scope.getActiveWorkers(), 1);
  assert.strictEqual(scope.getWorkerQueue().length, 1);

  // Reject task 1
  d1.reject(err1);

  // Rejection must propagate to p1
  await assert.rejects(p1, err => {
    assert.strictEqual(err, err1);
    return true;
  });
  await flushMicrotasks();

  // Worker slot must be released and task 2 initiated
  assert.strictEqual(scope.getActiveWorkers(), 1);
  assert.strictEqual(scope.getWorkerQueue().length, 0);

  // Resolve task 2 to verify healthy continuation
  d2.resolve('task-2-ok');
  const res2 = await p2;
  await flushMicrotasks();
  assert.strictEqual(res2, 'task-2-ok');
  assert.strictEqual(scope.getActiveWorkers(), 0);
});

test('synchronous task throw slotreleased: synchronous exception propagates and releases worker slot', async () => {
  const scope = createWorkerScope({ PV_TEST_WORKERS: '1' });
  const syncErr = new Error('synchronous worker exception');

  const dQueued = createDeferred();

  // Task 1 throws synchronously
  const p1 = scope.runWorkerTask(() => {
    throw syncErr;
  });

  // Task 2 queued behind task 1
  const p2 = scope.runWorkerTask(() => dQueued.promise);

  await assert.rejects(p1, err => {
    assert.strictEqual(err, syncErr);
    return true;
  });
  await flushMicrotasks();

  // Slot released: queued task 2 runs
  assert.strictEqual(scope.getActiveWorkers(), 1);
  assert.strictEqual(scope.getWorkerQueue().length, 0);

  dQueued.resolve('queued-ok');
  const res2 = await p2;
  await flushMicrotasks();
  assert.strictEqual(res2, 'queued-ok');
  assert.strictEqual(scope.getActiveWorkers(), 0);

  // Subsequent synchronous task also releases slot
  await assert.rejects(
    scope.runWorkerTask(() => { throw new Error('another sync throw'); }),
    /another sync throw/
  );
  await flushMicrotasks();
  assert.strictEqual(scope.getActiveWorkers(), 0);
});

test('prefetched rejection remains rejected at await: background rejection catch handler prevents unhandled rejection while preserving await error', async () => {
  const scope = createWorkerScope({ PV_TEST_WORKERS: '2' });

  // 1. Direct deferred promise with prefetch
  const d = createDeferred();
  const rawPromise = d.promise;
  const prefetched = scope.prefetch(rawPromise);
  assert.strictEqual(prefetched, rawPromise, 'prefetch must return original promise reference');

  const expectedErr = new Error('prefetched unhandled error test');
  d.reject(expectedErr);

  // Microtask tick to ensure .catch handler is registered and invoked in background
  await Promise.resolve();

  // Awaiting prefetched promise still yields the original rejection
  await assert.rejects(prefetched, err => {
    assert.strictEqual(err, expectedErr);
    return true;
  });

  // 2. Worker task wrapped with prefetch
  const workerErr = new Error('worker task prefetch failure');
  const taskPromise = scope.runWorkerTask(() => Promise.reject(workerErr));
  const prefetchedTask = scope.prefetch(taskPromise);
  assert.strictEqual(prefetchedTask, taskPromise, 'prefetch returns original task promise');

  await Promise.resolve();

  await assert.rejects(prefetchedTask, err => {
    assert.strictEqual(err, workerErr);
    return true;
  });

  assert.strictEqual(scope.getActiveWorkers(), 0, 'Active workers 0 after prefetch rejection');
});
