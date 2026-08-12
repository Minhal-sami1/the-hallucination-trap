export type Language = "en" | "ar";
export type AnswerMode = "cached" | "live";
export type AnswerSide = "ungrounded" | "grounded";
export type Verdict = "VERIFIED" | "FABRICATED" | "UNVERIFIABLE";
export type StageName = "retrieve" | "rank" | "generate" | "parse" | "verify" | "queued" | "complete";
export type StageStatus = "idle" | "running" | "complete" | "degraded" | "failed";

export interface CorpusSummary {
  lawNameEn: string;
  lawNameAr: string;
  lawNumber: string;
  lawYear: number;
  articleCount: number;
  sourceUrl: string;
}

export interface AppConfig {
  cacheMode: AnswerMode;
  liveAvailable: boolean;
  caughtCount: number | null;
  corpus: CorpusSummary | null;
}

export interface SeedQuestion {
  id: string;
  question: string;
  language: Language;
  observedFabricationRate: number | null;
  trials: number | null;
  fabricationRuns: number | null;
  primary: boolean;
}

export interface AuditCitation {
  id: string;
  side: AnswerSide;
  raw: string;
  verdict: Verdict;
  articleNumber: string | null;
  lawName: string | null;
  lawNumber: string | null;
  lawYear: number | null;
  excerpt: string | null;
  reason: string;
  sourceUrl: string | null;
}

export type StreamEvent =
  | { kind: "meta"; runId?: string; mode?: AnswerMode; cached?: boolean }
  | { kind: "answer_token"; side: AnswerSide; token: string }
  | { kind: "citation"; citation: AuditCitation }
  | { kind: "stage"; side: AnswerSide; stage: StageName; status: StageStatus; message?: string }
  | { kind: "refusal"; side: AnswerSide; message: string }
  | { kind: "complete"; side?: AnswerSide }
  | { kind: "error"; side?: AnswerSide; stage?: StageName; message: string };

export interface AuditRequest {
  question: string;
  language: Language;
  mode: AnswerMode;
  seedId?: string;
}

export interface EvaluationResult {
  generatedAt: string | null;
  questionCount: number | null;
  groundedPrecision: number | null;
  groundedRecall: number | null;
  ungroundedFabricationRate: number | null;
  verifierFalsePositiveRate: number | null;
  corpusArticleCount: number | null;
  modelName: string | null;
  rows: EvaluationRow[];
  raw: Record<string, unknown>;
}

export interface EvaluationRow {
  id: string;
  question: string;
  expected: string[];
  grounded: string[];
  ungrounded: string[] | null;
  language: Language;
}

const API_BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/$/, "");

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function asText(value: unknown): string | null {
  return typeof value === "string" && value.length ? value : null;
}

function asNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() && Number.isFinite(Number(value))) return Number(value);
  return null;
}

function asBoolean(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function first(record: Record<string, unknown>, ...keys: string[]): unknown {
  for (const key of keys) if (record[key] !== undefined && record[key] !== null) return record[key];
  return undefined;
}

async function getJson(path: string, signal?: AbortSignal): Promise<unknown> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}: ${response.statusText}`);
  return response.json();
}

export async function getConfig(signal?: AbortSignal): Promise<AppConfig> {
  const body = asRecord(await getJson("/api/config", signal));
  const corpusRaw = asRecord(first(body, "corpus", "law", "corpus_summary"));
  const articleCount = asNumber(first(corpusRaw, "articleCount", "article_count", "total_articles"));
  const lawNameEn = asString(first(corpusRaw, "lawNameEn", "law_name_en", "law_name", "name"));
  const lawNameAr = asString(first(corpusRaw, "lawNameAr", "law_name_ar"));
  const sourceUrl = asString(first(corpusRaw, "sourceUrl", "source_url"));
  const corpus = articleCount !== null && lawNameEn && sourceUrl
    ? {
        articleCount,
        lawNameEn,
        lawNameAr: lawNameAr ?? lawNameEn,
        lawNumber: asString(first(corpusRaw, "lawNumber", "law_number", "number"))
          ?? String(asNumber(first(corpusRaw, "lawNumber", "law_number", "number")) ?? ""),
        lawYear: asNumber(first(corpusRaw, "lawYear", "law_year", "year")) ?? 0,
        sourceUrl,
      }
    : null;

  const configuredMode = asString(first(body, "cacheMode", "cache_mode"));
  return {
    cacheMode: configuredMode === "live" ? "live" : "cached",
    liveAvailable: asBoolean(first(body, "liveAvailable", "live_available", "llm_available")),
    caughtCount: asNumber(first(body, "caughtCount", "caught_count", "fabricated_citations_caught")),
    corpus,
  };
}

function normalizeSeed(value: unknown, index: number): SeedQuestion | null {
  const row = asRecord(value);
  const question = asString(first(row, "question", "text", "question_text"));
  if (!question) return null;
  const languageValue = asString(first(row, "language", "lang"));
  return {
    id: asString(first(row, "id", "slug", "seed_id")) ?? `seed-${index + 1}`,
    question,
    language: languageValue === "ar" ? "ar" : "en",
    observedFabricationRate: asNumber(first(row, "observedFabricationRate", "observed_fabrication_rate", "fabrication_rate")),
    trials: asNumber(first(row, "trials", "run_count")),
    fabricationRuns: asNumber(first(row, "fabricationRuns", "fabrication_runs")),
    primary: asBoolean(first(row, "primary", "is_primary"), index === 0),
  };
}

export async function getSeeds(language: Language, signal?: AbortSignal): Promise<SeedQuestion[]> {
  const payload = await getJson(`/api/seeds?lang=${encodeURIComponent(language)}`, signal);
  const record = asRecord(payload);
  const list = Array.isArray(payload) ? payload : first(record, "seeds", "questions", "items");
  if (!Array.isArray(list)) return [];
  return list.map(normalizeSeed).filter((seed): seed is SeedQuestion => seed !== null);
}

function normalizeSide(value: unknown, eventName: string): AnswerSide | null {
  const text = asString(value)?.toLowerCase() ?? eventName.toLowerCase();
  if (text.includes("ungrounded") || text === "left") return "ungrounded";
  if (text.includes("grounded") || text === "right") return "grounded";
  return null;
}

function normalizeVerdict(value: unknown): Verdict | null {
  const text = asString(value)?.toUpperCase();
  return text === "VERIFIED" || text === "FABRICATED" || text === "UNVERIFIABLE" ? text : null;
}

function normalizeCitation(payload: Record<string, unknown>, eventName: string): AuditCitation | null {
  const source = asRecord(first(payload, "citation", "audit", "verdict"));
  const row = Object.keys(source).length ? { ...payload, ...source } : payload;
  const side = normalizeSide(first(row, "side", "panel", "answer_side", "channel", "lane", "model"), eventName);
  const verdict = normalizeVerdict(first(row, "verdict", "status", "result"));
  if (!side || !verdict) return null;
  const raw = asString(first(row, "raw", "raw_citation", "citation_text", "text")) ?? "";
  const articleNumber = asString(first(row, "articleNumber", "article_number"));
  const lawNumber = asString(first(row, "lawNumber", "law_number"));
  const lawYear = asNumber(first(row, "lawYear", "law_year"));
  const stable = [side, raw, lawNumber, lawYear, articleNumber].join(":");
  return {
    id: asString(first(row, "id", "citation_id")) ?? stable,
    side,
    raw,
    verdict,
    articleNumber,
    lawName: asString(first(row, "lawName", "law_name")),
    lawNumber,
    lawYear,
    excerpt: asString(first(row, "excerpt", "article_excerpt", "article_text")),
    reason: asString(first(row, "reason", "explanation", "message")) ?? "",
    sourceUrl: asString(first(row, "sourceUrl", "source_url", "official_source_url")),
  };
}

function normalizeEvent(eventName: string, value: unknown): StreamEvent | null {
  const payload = asRecord(value);
  const name = eventName.toLowerCase();
  if (name === "citation" || name === "audit" || name === "verdict" || name.includes("citation")) {
    const citation = normalizeCitation(payload, name);
    return citation ? { kind: "citation", citation } : null;
  }
  if (name === "answer_token" || name === "token" || name === "delta" || name.includes("token")) {
    const side = normalizeSide(first(payload, "side", "panel", "answer_side", "channel", "lane", "model"), name);
    const token = asText(first(payload, "token", "delta", "text", "content"));
    return side && token !== null ? { kind: "answer_token", side, token } : null;
  }
  if (name === "stage" || name.includes("stage")) {
    const side = normalizeSide(first(payload, "side", "panel", "answer_side", "channel", "lane", "model"), name);
    const stage = (asString(first(payload, "stage", "name")) ?? "queued") as StageName;
    const status = (asString(first(payload, "status", "state")) ?? "running") as StageStatus;
    if (!side) return null;
    return { kind: "stage", side, stage, status, message: asString(first(payload, "message", "detail")) ?? undefined };
  }
  if (name === "refusal") {
    return {
      kind: "refusal",
      side: normalizeSide(first(payload, "side", "panel", "channel", "lane", "model"), name) ?? "grounded",
      message: asString(first(payload, "message", "text", "reason")) ?? "",
    };
  }
  if (name === "complete" || name === "done" || name === "end") {
    return { kind: "complete", side: normalizeSide(first(payload, "side", "panel"), name) ?? undefined };
  }
  if (name === "error" || name.includes("error")) {
    return {
      kind: "error",
      side: normalizeSide(first(payload, "side", "panel", "channel", "lane", "model"), name) ?? undefined,
      stage: (asString(first(payload, "stage")) as StageName | null) ?? undefined,
      message: asString(first(payload, "message", "detail", "error")) ?? "The pipeline reported an unknown error.",
    };
  }
  if (name === "meta" || name === "message") {
    const nestedKind = asString(first(payload, "kind", "type", "event"));
    if (nestedKind && nestedKind !== name) return normalizeEvent(nestedKind, payload);
    return {
      kind: "meta",
      runId: asString(first(payload, "runId", "run_id")) ?? undefined,
      mode: asString(first(payload, "mode")) === "live" ? "live" : undefined,
      cached: typeof payload.cached === "boolean" ? payload.cached : undefined,
    };
  }
  return null;
}

function parseSseFrame(frame: string): StreamEvent | null {
  let eventName = "message";
  const data: string[] = [];
  for (const line of frame.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    let value = separator === -1 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") eventName = value;
    if (field === "data") data.push(value);
  }
  if (!data.length) return null;
  const raw = data.join("\n");
  try {
    return normalizeEvent(eventName, JSON.parse(raw));
  } catch {
    return normalizeEvent(eventName, { text: raw, token: raw });
  }
}

export async function streamAudit(
  request: AuditRequest,
  onEvent: (event: StreamEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE}/api/audit-stream`, {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
      "Cache-Control": "no-cache",
    },
    body: JSON.stringify({
      question: request.question,
      language: request.language,
      mode: request.mode,
      seed_id: request.seedId,
    }),
    signal,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `HTTP ${response.status}: ${response.statusText}`);
  }
  if (!response.body) throw new Error("The server opened no streaming response body.");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const event = parseSseFrame(frame);
      if (event) onEvent(event);
    }
    if (done) break;
  }
  if (buffer.trim()) {
    const event = parseSseFrame(buffer);
    if (event) onEvent(event);
  }
}

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => typeof item === "number" && Number.isFinite(item) ? String(item) : asString(item))
    .filter((item): item is string => item !== null);
}

function normalizeEvaluationRow(value: unknown, index: number): EvaluationRow | null {
  const row = asRecord(value);
  const question = asString(first(row, "question", "question_text"));
  if (!question) return null;
  const lang = asString(first(row, "language", "lang"));
  const ungrounded = first(row, "ungrounded", "ungrounded_citations", "ungrounded_articles");
  return {
    id: asString(first(row, "id", "question_id")) ?? `evaluation-${index + 1}`,
    question,
    expected: stringArray(first(row, "expected", "expected_citations", "gold_citations", "expected_articles")),
    grounded: stringArray(first(row, "grounded", "grounded_citations", "predicted_citations", "predicted_articles")),
    ungrounded: ungrounded === undefined ? null : stringArray(ungrounded),
    language: lang === "ar" ? "ar" : "en",
  };
}

export async function getResults(signal?: AbortSignal): Promise<EvaluationResult> {
  const payload = asRecord(await getJson("/api/results", signal));
  const metrics = asRecord(first(payload, "metrics", "summary"));
  const counts = asRecord(first(metrics, "counts"));
  const corpus = asRecord(first(payload, "corpus", "corpus_summary"));
  const evaluation = asRecord(first(payload, "evaluation", "eval"));
  const grounded = asRecord(first(payload, "grounded", "grounded_pipeline", "grounded_metrics"));
  const ungrounded = asRecord(first(payload, "ungrounded", "ungrounded_model", "ungrounded_metrics"));
  const verifier = asRecord(first(payload, "verifier", "verifier_metrics"));
  const rowValue = first(payload, "questions", "results", "per_question", "rows");
  const rows = Array.isArray(rowValue)
    ? rowValue.map(normalizeEvaluationRow).filter((row): row is EvaluationRow => row !== null)
    : [];
  return {
    generatedAt: asString(first(payload, "generatedAt", "generated_at", "evaluated_at"))
      ?? asString(first(evaluation, "generated_at", "generatedAt", "evaluated_at")),
    questionCount: asNumber(first(payload, "questionCount", "question_count", "total_questions", "total"))
      ?? asNumber(first(counts, "questions", "question_count", "total_questions"))
      ?? (rows.length || null),
    groundedPrecision: asNumber(first(grounded, "citationPrecision", "citation_precision", "precision"))
      ?? asNumber(first(metrics, "citation_precision", "grounded_citation_precision"))
      ?? asNumber(first(payload, "citation_precision", "grounded_citation_precision")),
    groundedRecall: asNumber(first(grounded, "citationRecall", "citation_recall", "recall"))
      ?? asNumber(first(metrics, "citation_recall", "grounded_citation_recall"))
      ?? asNumber(first(payload, "citation_recall", "grounded_citation_recall")),
    ungroundedFabricationRate: asNumber(first(ungrounded, "fabricationRate", "fabrication_rate"))
      ?? asNumber(first(metrics, "fabrication_rate", "ungrounded_fabrication_rate", "fabrication_rate_ungrounded"))
      ?? asNumber(first(payload, "fabrication_rate", "ungrounded_fabrication_rate", "fabrication_rate_ungrounded")),
    verifierFalsePositiveRate: asNumber(first(verifier, "falsePositiveRate", "false_positive_rate"))
      ?? asNumber(first(metrics, "verifier_false_positive_rate", "false_positive_rate"))
      ?? asNumber(first(payload, "verifier_false_positive_rate", "false_positive_rate")),
    corpusArticleCount: asNumber(first(payload, "corpusArticleCount", "corpus_article_count", "article_count"))
      ?? asNumber(first(counts, "corpus_articles", "articles", "article_count"))
      ?? asNumber(first(corpus, "article_count", "articleCount", "total_articles")),
    modelName: asString(first(payload, "modelName", "model_name", "ungrounded_model"))
      ?? asString(first(evaluation, "model", "model_name")),
    rows,
    raw: payload,
  };
}
