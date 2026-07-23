import assert from 'node:assert/strict';
import test from 'node:test';

import { cancellationDetail, isCancellationInProgress } from '../src/calculationUi.ts';

test('cancellation is active for a local action or backend request', () => {
  assert.equal(isCancellationInProgress('current', false), true);
  assert.equal(isCancellationInProgress(null, true), true);
  assert.equal(isCancellationInProgress(null, false), false);
});

test('cancellation copy explains whether a result will be kept', () => {
  assert.match(cancellationDetail('review', true), /možnosti/);
  assert.match(cancellationDetail('current', true), /zadnja najdena rešitev/);
  assert.match(cancellationDetail('current', false), /rezultat ne bo prikazan/);
});
