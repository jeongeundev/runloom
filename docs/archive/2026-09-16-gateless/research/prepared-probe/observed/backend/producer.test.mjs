import test from 'node:test';
import assert from 'node:assert/strict';
import {summarize} from './api.mjs';
test('paid',()=>assert.deepEqual(summarize(100,10),{subtotal:100,shippingFee:10,total:110}));
test('free',()=>assert.deepEqual(summarize(100,0),{subtotal:100,shippingFee:0,total:100}));
test('pending',()=>assert.deepEqual(summarize(100,null),{subtotal:100,shippingFee:null,total:null}));
