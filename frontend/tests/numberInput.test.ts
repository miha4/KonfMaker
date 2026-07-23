import assert from 'node:assert/strict';
import test from 'node:test';

import { preventNumberInputArrowStep } from '../src/numberInput.ts';

test('number inputs ignore vertical arrow stepping', () => {
  for (const key of ['ArrowUp', 'ArrowDown']) {
    let prevented = false;
    preventNumberInputArrowStep({
      key,
      preventDefault: () => {
        prevented = true;
      },
    });
    assert.equal(prevented, true, `${key} should be prevented`);
  }
});

test('number inputs keep horizontal and editing keys available', () => {
  for (const key of ['ArrowLeft', 'ArrowRight', 'Backspace', 'Delete']) {
    let prevented = false;
    preventNumberInputArrowStep({
      key,
      preventDefault: () => {
        prevented = true;
      },
    });
    assert.equal(prevented, false, `${key} should remain available`);
  }
});
