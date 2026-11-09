// Inspect frozen source as syntax, never execute it or publish an unreviewed question.
import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import * as parser from "../frontend/node_modules/prettier/plugins/typescript.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const directory = path.join(root, "question_sources", "freecodecamp");
const required = ["Question", "Answer", "Distractor1", "Distractor2", "Distractor3", "Explanation", "Link"];

export async function extract(text, filename) {
  const tree = await parser.parsers.typescript.parse(text, {});
  const arrays = tree.body.flatMap((node) =>
    node.type === "VariableDeclaration"
      ? node.declarations.map((declaration) => declaration.init).filter((value) => value?.type === "ArrayExpression")
      : [],
  );
  if (arrays.length !== 1) throw new Error(`Expected one static question array in ${filename}`);
  return arrays[0].elements.map((node, index) => {
    if (!node || node.type !== "ObjectExpression") return { filename, index, status: "unsupported" };
    const fields = {};
    for (const property of node.properties) {
      if (property.type !== "Property" || property.computed || property.value?.type !== "Literal" || typeof property.value.value !== "string")
        return { filename, index, status: "unsupported" };
      const name = property.key.name ?? property.key.value;
      if (Object.hasOwn(fields, name)) return { filename, index, status: "unsupported" };
      fields[name] = property.value.value;
    }
    if (required.some((key) => typeof fields[key] !== "string" || !fields[key].trim()))
      return { filename, index, status: "incomplete" };
    const choices = [fields.Answer, fields.Distractor1, fields.Distractor2, fields.Distractor3];
    const duplicates = new Set(choices.map((value) => value.trim().toLocaleLowerCase())).size !== choices.length;
    const canonical = JSON.stringify(required.map((key) => fields[key]));
    const fingerprint = createHash("sha256").update(canonical).digest("hex");
    return { filename, index, fingerprint, status: duplicates ? "duplicate_choices" : "unreviewed", ...fields };
  });
}

async function main() {
  const args = process.argv.slice(2);
  const topic = args[0] || "all";
  const offset = Number(args[1] || 0);
  const limit = Number(args[2] || 10);
  if (!Number.isInteger(offset) || offset < 0 || !Number.isInteger(limit) || limit < 1 || limit > 100)
    throw new Error("Usage: node scripts/review_freecodecamp_candidates.mjs [topic|all] [offset] [limit<=100]");
  const files = (await readdir(directory)).filter((name) => name.endsWith("-quiz.ts")).sort();
  const all = [];
  for (const filename of files) {
    if (topic !== "all" && filename !== `${topic}-quiz.ts`) continue;
    all.push(...await extract(await readFile(path.join(directory, filename), "utf8"), filename));
  }
  if (!all.length) throw new Error("No matching source topic");
  const counts = Object.fromEntries([...new Set(all.map((item) => item.status))].map((status) => [status, all.filter((item) => item.status === status).length]));
  process.stdout.write(JSON.stringify({ total: all.length, counts, candidates: all.slice(offset, offset + limit) }, null, 2) + "\n");
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => { process.stderr.write(`${error.message}\n`); process.exitCode = 1; });
}
