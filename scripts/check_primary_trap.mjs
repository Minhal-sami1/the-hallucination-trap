import { readFile } from 'node:fs/promises';

const baseUrl = (process.argv[2] ?? 'http://127.0.0.1:8000').replace(/\/$/, '');
const runCount = Number.parseInt(process.argv[3] ?? '10', 10);
const limitMs = Number.parseInt(process.argv[4] ?? '2000', 10);
const seedData = JSON.parse(await readFile(new URL('../data/seed_questions.json', import.meta.url), 'utf8'));
const primarySeed = seedData.questions.find((seed) => seed.id === 'seed-ar-neighbour-light');
if (!primarySeed) throw new Error('The committed primary Arabic seed is missing.');
const question = primarySeed.question;

if (!Number.isInteger(runCount) || runCount < 1) throw new Error('Run count must be positive.');

async function runAudit(index) {
  const started = performance.now();
  const response = await fetch(`${baseUrl}/api/audit-stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({
      question,
      language: 'ar',
      mode: 'cached',
      seed_id: primarySeed.id,
    }),
  });
  if (!response.ok || !response.body) throw new Error(`Run ${index}: HTTP ${response.status}`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let fabricatedMs = null;
  let reason = null;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary;
    while ((boundary = buffer.indexOf('\n\n')) >= 0) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const event = frame.match(/^event:\s*(.+)$/m)?.[1];
      const dataText = frame.match(/^data:\s*(.+)$/m)?.[1];
      if (event !== 'citation' || !dataText) continue;
      const citation = JSON.parse(dataText).citation;
      if (citation?.verdict === 'FABRICATED' && fabricatedMs === null) {
        fabricatedMs = Math.round(performance.now() - started);
        reason = citation.reason;
      }
    }
  }
  const passed = fabricatedMs !== null && fabricatedMs < limitMs;
  console.log(JSON.stringify({ run: index, fabricated_ms: fabricatedMs, passed }));
  return { passed, fabricatedMs, reason };
}

const runs = [];
for (let index = 1; index <= runCount; index += 1) runs.push(await runAudit(index));
const passed = runs.filter((run) => run.passed).length;
const values = runs.map((run) => run.fabricatedMs).filter(Number.isFinite);
const summary = {
  passed,
  total: runCount,
  limit_ms: limitMs,
  min_ms: Math.min(...values),
  max_ms: Math.max(...values),
  reason: runs.find((run) => run.reason)?.reason,
};
console.log(JSON.stringify(summary));
if (passed !== runCount) process.exit(1);
