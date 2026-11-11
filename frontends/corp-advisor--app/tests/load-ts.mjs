import { readFile } from "node:fs/promises";
import { transformWithEsbuild } from "vite";

export async function loadTs(path) {
  const source = await readFile(new URL(path, import.meta.url), "utf8");
  const { code } = await transformWithEsbuild(source, path, { loader: "ts", format: "esm", target: "node20" });
  return import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
}
