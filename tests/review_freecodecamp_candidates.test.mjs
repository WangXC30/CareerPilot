import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { extract } from "../scripts/review_freecodecamp_candidates.mjs";

const good = `const bank = [{
  Question: "Question?", Answer: "A", Distractor1: "B", Distractor2: "C",
  Distractor3: "D", Explanation: "Because.", Link: "https://example.test/"
}];`;

test("extracts only static strings and produces content-sensitive fingerprints", async () => {
  const first = await extract(good, "topic-quiz.ts");
  const second = await extract(good.replace('Answer: "A"', 'Answer: "B"'), "topic-quiz.ts");
  assert.equal(first[0].status, "unreviewed");
  assert.equal(first[0].fingerprint.length, 64);
  assert.equal(second[0].status, "duplicate_choices");
  assert.notEqual(first[0].fingerprint, second[0].fingerprint);
});

test("classifies incomplete and nonliteral candidates without executing them", async () => {
  const missing = await extract(good.replace('Link: "https://example.test/"', 'Link: ""'), "test.ts");
  assert.equal(missing[0].status, "incomplete");
  const executable = await extract(good.replace('Answer: "A"', 'Answer: dangerous()'), "test.ts");
  assert.equal(executable[0].status, "unsupported");
  assert.equal(globalThis.dangerous, undefined);
});

test("rejects files without exactly one static array", async () => {
  await assert.rejects(extract("const a = []; const b = [];", "test.ts"));
});

test("reviewed JavaScript output answers agree with direct runtime checks", async () => {
  const source = await readFile("question_sources/freecodecamp/javascript-quiz.ts", "utf8");
  const items = await extract(source, "javascript-quiz.ts");
  assert.equal(items[0].Answer, "Prakhar".substring(2, 5));
  assert.equal(items[7].Answer, "Intelligent".slice(2, 5));
  assert.equal(items[32].Answer, String(null ?? 20));
});
