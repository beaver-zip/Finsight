import assert from "node:assert/strict";
import { test } from "node:test";
import { loadTs } from "./load-ts.mjs";

const { readChatStream } = await loadTs("../src/api/chatStream.ts");
const event = (name, data) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;
const encoder = new TextEncoder();
function stream(chunks) {
  return new ReadableStream({ start(c) { for (const chunk of chunks) c.enqueue(chunk); c.close(); } });
}
function handlers() {
  const calls = [];
  return { calls, onFinal: (x) => calls.push(["final", x]), onInterrupt: (x) => calls.push(["interrupt", x]),
    onError: (...args) => calls.push(["error", ...args]), onProgress: (x) => calls.push(["progress", x]) };
}
const signal = () => new AbortController().signal;

test("EOF without a terminal event is an error, including after partial errors", async () => {
  for (const text of ["", event("progress", { detail: "working" }), event("error", { partial: true, message: "partial" }),
    'event: final\ndata: {"answer":']) {
    await assert.rejects(readChatStream(stream([encoder.encode(text)]), handlers(), signal()), /완료 이벤트/);
  }
});

test("UTF-8 chunk splits and CRLF preserve progress, partial error, and final", async () => {
  const h = handlers();
  const text = (event("progress", { detail: "진행" }) + event("error", { message: "부분", partial: true }) +
    event("final", { answer: { markdown: "답변", citations: [] } })).replaceAll("\n", "\r\n");
  const bytes = encoder.encode(text);
  await readChatStream(stream(Array.from(bytes, (byte) => new Uint8Array([byte]))), h, signal());
  assert.deepEqual(h.calls.map((x) => x[0]), ["progress", "error", "final"]);
  assert.equal(h.calls[2][1].markdown, "답변");
});

test("interrupt and fatal errors end the stream; trailing events are ignored", async () => {
  for (const [name, data] of [["interrupt", { question: "어느 회사?", options: ["A"] }],
    ["error", { message: "fatal", partial: false }], ["final", { answer: null }]]) {
    const h = handlers();
    await readChatStream(stream([encoder.encode(event(name, data) + event("final", { answer: "unexpected" }))]), h, signal());
    assert.equal(h.calls.length, 1);
    assert.equal(h.calls[0][0], name);
  }
});

test("user cancellation is not an early-EOF error", async () => {
  const ctrl = new AbortController();
  ctrl.abort();
  const h = handlers();
  await readChatStream(stream([]), h, ctrl.signal);
  assert.deepEqual(h.calls, []);
});
