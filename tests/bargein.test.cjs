// node tests/bargein.test.cjs  (also run by tests/test_bargein.py)
const assert = require("assert");
const { BargeIn } = require("../web/bargein.js");

const CHUNK_MS = 43; // 2048 samples at 48 kHz

function feed(b, rms, ms, speaking) {
  let fired = 0;
  for (let t = 0; t < ms; t += CHUNK_MS) if (b.update(rms, CHUNK_MS, speaking) === "barge") fired++;
  return fired;
}
function quietRoom() { const b = new BargeIn(); feed(b, 0.004, 2000, false); return b; }

// 1. While the agent is silent the mic is open and nothing "barges in".
let b = quietRoom();
assert.strictEqual(feed(b, 0.2, 1000, false), 0);
assert.ok(b.micOpen, "mic open when the agent is silent");

// 2. While the agent speaks the mic is guarded (the model hears silence).
b = quietRoom();
feed(b, 0.02, 700, true);
assert.ok(!b.micOpen, "mic guarded while the agent speaks");

// 3. Laptop speakers: loud echo (0.09, above the old fixed bar) never interrupts once learned.
b = quietRoom();
assert.strictEqual(feed(b, 0.09, 6000, true), 0, "agent's own echo must not cut it off");

// 4. Small sounds while the agent speaks: a click, a cough, a short word do not interrupt.
b = quietRoom();
feed(b, 0.02, 700, true);                       // warm-up on quiet echo
assert.strictEqual(feed(b, 0.4, 86, true), 0, "86 ms click");
assert.strictEqual(feed(b, 0.02, 300, true), 0);
assert.strictEqual(feed(b, 0.3, 215, true), 0, "215 ms cough / short word");
assert.strictEqual(feed(b, 0.02, 300, true), 0);

// 5. Real speech over the agent fires once, after ~320 ms, and opens the mic.
b = quietRoom();
feed(b, 0.02, 700, true);
let firedAt = -1;
for (let t = 0; t < 1500; t += CHUNK_MS) if (b.update(0.18, CHUNK_MS, true) === "barge") { firedAt = t + CHUNK_MS; break; }
assert.ok(firedAt >= 320 && firedAt <= 400, `fired at ${firedAt} ms`);
assert.ok(b.micOpen, "mic opens for the user");
assert.strictEqual(feed(b, 0.18, 1000, true), 0, "fires only once");

// 6. No stammer: a false alarm (model does not confirm) cannot re-fire on the same noise.
b = quietRoom();
feed(b, 0.02, 700, true);
assert.strictEqual(feed(b, 0.12, 500, true), 1, "a sustained noise fires once");
b.reject();
assert.ok(!b.micOpen, "rejected: mic guarded again");
assert.strictEqual(feed(b, 0.12, 4000, true), 0, "same noise level never fires again: no stop-start loop");
assert.strictEqual(feed(b, 0.5, 600, true), 1, "clearly louder real speech still can");

// 7. Speech within the first 600 ms of a reply is ignored (echo is being learned).
b = quietRoom();
assert.strictEqual(feed(b, 0.2, 550, true), 0);

// 8. Re-arms for the next reply; the learned echo level is kept.
b = quietRoom();
feed(b, 0.09, 3000, true);
feed(b, 0.004, 500, false);
assert.strictEqual(feed(b, 0.09, 3000, true), 0, "same echo on the next reply");
assert.strictEqual(feed(b, 0.4, 600, true), 1);

// 9. Noisy room (fan): background raises the bar.
b = new BargeIn();
feed(b, 0.05, 30000, false);
feed(b, 0.05, 700, true);
assert.strictEqual(feed(b, 0.15, 1000, true), 0, "0.15 is only 3x a loud background");
assert.strictEqual(feed(b, 0.6, 600, true), 1);

console.log("bargein: all checks passed");
