"""Create disposable inputs next to probe.py; refuse to overwrite a previous run."""
from pathlib import Path

root = Path(__file__).parent
for owner in ('backend', 'frontend'):
    if (root / owner).exists():
        raise SystemExit('Use a fresh temporary directory; previous workspace exists')
for owner in ('backend', 'frontend'):
    (root / owner).mkdir()
    (root / owner / 'owner-marker.txt').write_text(owner + '-only\n')
(root / 'backend' / 'api.mjs').write_text('export function summarize(subtotal, shippingFee) {\n  return {subtotal, shippingFee, total: subtotal + shippingFee};\n}\n')
(root / 'frontend' / 'view.mjs').write_text('export function render(order) {\n  return { shipping: String(order.shippingFee), total: String(order.total) };\n}\n')
(root / 'backend' / 'producer.test.mjs').write_text('''import test from 'node:test';
import assert from 'node:assert/strict';
import {summarize} from './api.mjs';
test('paid',()=>assert.deepEqual(summarize(100,10),{subtotal:100,shippingFee:10,total:110}));
test('free',()=>assert.deepEqual(summarize(100,0),{subtotal:100,shippingFee:0,total:100}));
test('pending',()=>assert.deepEqual(summarize(100,null),{subtotal:100,shippingFee:null,total:null}));
''')
