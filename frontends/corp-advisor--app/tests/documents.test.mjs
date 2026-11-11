import assert from "node:assert/strict";
import { test, mock } from "node:test";
import { loadTs } from "./load-ts.mjs";

const { uploadPdfs } = await loadTs("../src/api/documents.ts");

test("renamed and accepted files use server names for registration", async (t) => {
  t.after(() => mock.restoreAll());
  const files = [new File(["pdf"], "a:b.pdf"), new File(["pdf"], "normal.pdf"), new File(["x"], "bad.txt")];
  mock.method(globalThis, "fetch", async (url, options) => {
    assert.equal(url, "/api/files/upload-pdf");
    assert.deepEqual(options.body.getAll("files").map((f) => f.name), files.map((f) => f.name));
    return Response.json({ uploaded: ["a_b.pdf", "normal.pdf"], rejected: [{ file: "bad.txt", reason: "PDF only" }] });
  });
  const result = await uploadPdfs("/api", files);
  assert.deepEqual(result.uploaded, ["a_b.pdf", "normal.pdf"]);
  assert.equal(result.rejected[0].file, "bad.txt");
});

test("all rejected uploads expose the backend HTTP error detail", async (t) => {
  t.after(() => mock.restoreAll());
  mock.method(globalThis, "fetch", async () => Response.json({ detail: {
    uploaded: [], rejected: [{ file: "bad.txt", reason: "PDF only" }],
  } }, { status: 400 }));
  const result = await uploadPdfs("/api", [new File(["x"], "bad.txt")]);
  assert.deepEqual(result.uploaded, []);
  assert.equal(result.rejected[0].reason, "PDF only");
});
