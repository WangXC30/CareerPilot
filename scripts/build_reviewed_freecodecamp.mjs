// Materialize only explicitly reviewed source items; no source code is executed.
import { readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { extract } from "./review_freecodecamp_candidates.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = path.join(root, "question_sources", "freecodecamp");
const manifestPath = path.join(source, "reviewed_manifest.json");
const output = path.join(root, "src", "careerpilot", "reviewed_freecodecamp.json");
const commit = "d71a60ba08113e8f999aeaa8c75c7d16aef1dbc0";

async function main() {
  const reviewed = JSON.parse(await readFile(manifestPath, "utf8"));
  if (reviewed.source_commit !== commit) throw new Error("Unexpected source revision");
  const result = [];
  const ids = new Set();
  for (const group of reviewed.groups) {
    const filename = `${group.source}-quiz.ts`;
    const candidates = await extract(await readFile(path.join(source, filename), "utf8"), filename);
    for (const entry of group.items) {
      const candidate = candidates[entry.index];
      if (!candidate || candidate.status !== "unreviewed" || candidate.fingerprint !== entry.fingerprint)
        throw new Error(`Source changed or failed structural screening: ${filename}:${entry.index}`);
      if (!entry.reference.startsWith("https://")) throw new Error("Missing primary reference");
      const id = `fcc-${group.source}-${candidate.fingerprint.slice(0, 16)}`;
      if (ids.has(id)) throw new Error(`Duplicate reviewed item ${id}`);
      ids.add(id);
      result.push({
        id, topic: group.topic, question: candidate.Question,
        choices: [candidate.Answer, candidate.Distractor1, candidate.Distractor2, candidate.Distractor3],
        correct: 0, explanation: candidate.Explanation, reference: entry.reference,
        origin: `https://github.com/freeCodeCamp/Developer_Quiz_Site/blob/${commit}/src/data/${filename}`,
        attribution: "Questions from freeCodeCamp Developer Quiz Site, BSD-3-Clause",
        fingerprint: candidate.fingerprint,
      });
    }
  }
  const payload = JSON.stringify(result, null, 2) + "\n";
  if (process.argv.includes("--check")) {
    if (await readFile(output, "utf8") !== payload) throw new Error("Reviewed snapshot is stale");
    console.log(`Verified ${result.length} source-linked questions against pinned review decisions`);
  } else {
    await writeFile(output, payload, "utf8");
    console.log(`Built ${result.length} source-linked questions from pinned review decisions`);
  }
}

main().catch((error) => { process.stderr.write(`${error.message}\n`); process.exitCode = 1; });
