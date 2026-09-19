# Compatibility report

Read 3 received cases from `/workspace/shared-context.json`, supplied by the coordinator from Frontend via A2A. No Frontend repository was accessed.

Provenance recorded in the shared file:

- Source: `frontend/cases.json`
- Request ID: `R1`
- Supplied SHA-256: `f6f080dbc5bd4f7793487f64c86fd7e7186443cb0943c341e2e339ce58c276e7`

The digest above is the supplied provenance value; it was not independently verified against the unavailable Frontend source file.

Each compatibility test calls `summarize(input.subtotal, input.shippingFee)` and strictly compares the entire returned object with the received `input` object.

| Received case | Subtotal | Shipping fee | Total | Outcome |
| --- | --- | --- | --- | --- |
| 1 | 100 | 10 | 110 | PASS |
| 2 | 100 | 0 | 100 | PASS |
| 3 | 100 | null | null | PASS |

Explicit strict assertions confirm that zero shipping remains zero with a numeric total, while pending shipping and total remain null.

Executed `node --test producer.test.mjs compatibility.test.mjs` successfully (exit code 0): 6 tests passed, 0 failed, 0 cancelled, 0 skipped, 0 todo. This includes all 3 received compatibility cases and all 3 producer tests.

Neither `api.mjs` nor `shared-context.json` was edited.
