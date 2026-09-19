import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { summarize } from './api.mjs';

const { cases } = JSON.parse(
  readFileSync(new URL('./shared-context.json', import.meta.url), 'utf8'),
);

assert.ok(Array.isArray(cases) && cases.length > 0, 'received cases are required');

for (const [index, { input }] of cases.entries()) {
  test(`received case ${index + 1}: shippingFee=${input.shippingFee}`, () => {
    const output = summarize(input.subtotal, input.shippingFee);
    assert.deepStrictEqual(output, input);

    if (input.shippingFee === null) {
      assert.strictEqual(output.shippingFee, null);
      assert.strictEqual(output.total, null);
      assert.notStrictEqual(output.shippingFee, 0);
      assert.notStrictEqual(output.total, 0);
    } else if (input.shippingFee === 0) {
      assert.strictEqual(output.shippingFee, 0);
      assert.notStrictEqual(output.shippingFee, null);
      assert.strictEqual(output.total, input.subtotal);
      assert.notStrictEqual(output.total, null);
    }
  });
}
