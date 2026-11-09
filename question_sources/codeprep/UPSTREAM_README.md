# CodePrep Open Interview Questions

A free, curated set of software-engineering interview questions — each with the answer and an explanation — published as structured JSON under CC-BY-4.0. Use it to build study tools, seed a quiz app, fine-tune or evaluate a model, or just practise.

The current release holds **110 questions across 11 technologies**, balanced across five difficulty levels. It is a sample of the larger [CodePrep](https://codeprep.dev) bank, regenerated from the same source so the format stays consistent.

## What's inside

| Field | Description |
| --- | --- |
| Technologies | JavaScript, TypeScript, React, Web Platform, CSS, Node.js, HTTP & APIs, Databases & SQL, System Design, AI Engineering, Python |
| Difficulty | `1`–`5`, labelled `intro` / `easy` / `medium` / `hard` / `expert` |
| Types | `MCQ_SINGLE`, `MCQ_MULTI`, `TRUE_FALSE`, `CODE_OUTPUT`, `FIND_THE_BUG`, `SHORT_ANSWER`, `FLASHCARD` |

Every question carries its answer and a written explanation. The data is in [`data/questions.json`](data/questions.json); its structure is described by [`schema.json`](schema.json) (JSON Schema draft-07).

## Format

The file is a single object: metadata, per-technology and per-type counts, then a `questions` array. Each question:

```json
{
  "id": "js-bug-loop-off-by-one",
  "technology": "javascript",
  "technologyName": "JavaScript",
  "difficulty": 2,
  "difficultyLabel": "easy",
  "type": "FIND_THE_BUG",
  "tags": ["array-methods"],
  "prompt": "This function should sum an array but returns `NaN`. Which line has the bug?",
  "answer": { "code": "function sum(arr) { ... }", "language": "javascript", "buggyLines": [3] },
  "explanation": "`i <= arr.length` reads one index past the end. ..."
}
```

The `answer` shape depends on `type`:

| Type | `answer` fields |
| --- | --- |
| `MCQ_SINGLE` | `options[]`, `correctOptionId` |
| `MCQ_MULTI` | `options[]`, `correctOptionIds[]` |
| `TRUE_FALSE` | `value` (boolean) |
| `CODE_OUTPUT` | `code`, `language`, then `options[]` + `correctOptionId` or `expectedOutput` |
| `FIND_THE_BUG` | `code`, `language`, then `options[]` + `correctOptionId` or `buggyLines[]` |
| `SHORT_ANSWER` | `sampleAnswer` |
| `FLASHCARD` | `back` |

## Use it

Fetch the raw file directly:

```bash
curl -sL https://raw.githubusercontent.com/codeprepdev/codeprep-questions/main/data/questions.json -o questions.json
```

Filter with `jq` — for example, every hard React question:

```bash
jq '.questions[] | select(.technology=="react" and .difficulty>=4) | .prompt' questions.json
```

Load it in Node:

```js
import data from "./questions.json" with { type: "json" };
const flashcards = data.questions.filter((q) => q.type === "FLASHCARD");
```

Validate against the schema with any JSON Schema validator (for example `ajv`):

```bash
npx ajv-cli validate -s schema.json -d data/questions.json
```

## License and attribution

The questions are licensed under [CC-BY-4.0](LICENSE). You may use, adapt, and redistribute them — including commercially — as long as you credit the source. Attribution line:

> Questions from CodePrep (https://codeprep.dev), CC-BY-4.0.

If you ship this in an app or model, a link back to https://codeprep.dev satisfies the requirement.

## Contributing

Found a wrong answer or an unclear prompt? Open an issue with the question `id`. The dataset is generated from the upstream bank, so fixes land there and flow back on the next release.
