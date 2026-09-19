import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { render } from './view.mjs';

const cases = JSON.parse(await readFile(new URL('./cases.json', import.meta.url), 'utf8'));
const names = ['paid shipping', 'free shipping', 'pending shipping'];

for (const [index, { input, expected_display }] of cases.entries()) {
  test(names[index], () => {
    assert.deepStrictEqual(render(input), expected_display);
  });
}
