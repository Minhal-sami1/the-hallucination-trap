import { afterEach, describe, expect, it, vi } from "vitest";
import { getConfig, getResults, getSeeds, streamAudit, type StreamEvent } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("typed API client", () => {
  it("normalizes snake-case configuration from the API", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      cache_mode: "cached",
      live_available: true,
      fabricated_citations_caught: 14,
      corpus: {
        article_count: 1422,
        law_name_en: "Federal Decree by Law No. 25 of 2025 Promulgating Civil Transactions Law",
        law_name_ar: "مرسوم بقانون اتحادي رقم 25 لسنة 2025",
        law_number: "25",
        law_year: 2025,
        source_url: "https://uaelegislation.gov.ae/en/legislations/4011",
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    await expect(getConfig()).resolves.toMatchObject({
      cacheMode: "cached",
      liveAvailable: true,
      caughtCount: 14,
      corpus: { articleCount: 1422, lawNumber: "25", lawYear: 2025 },
    });
  });

  it("parses chunk-safe SSE answer and citation events", async () => {
    const encoder = new TextEncoder();
    const responseBody = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('event: answer_token\ndata: {"side":"ungrounded","token":"Article "}\n'));
        controller.enqueue(encoder.encode('\nevent: citation\ndata: {"side":"ungrounded","verdict":"FABRICATED","raw":"Article 2610","reason":"Article 2610 does not exist."}\n\n'));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(responseBody, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    })));
    const events: StreamEvent[] = [];
    await streamAudit(
      { question: "What law applies?", language: "en", mode: "cached" },
      (event) => events.push(event),
      new AbortController().signal,
    );

    expect(events).toHaveLength(2);
    expect(events[0]).toEqual({ kind: "answer_token", side: "ungrounded", token: "Article " });
    expect(events[1]).toMatchObject({
      kind: "citation",
      citation: { verdict: "FABRICATED", raw: "Article 2610", side: "ungrounded" },
    });
  });

  it("loads the primary Arabic seed from the questions contract", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) => new Response(JSON.stringify({
      questions: [{
        id: "seed-ar-neighbour-light",
        language: "ar",
        question: "هل يحق للمالك طلب إزالة بناء جاره؟",
        fabrication_rate: 1,
        is_primary: true,
      }],
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getSeeds("ar")).resolves.toEqual([
      expect.objectContaining({ id: "seed-ar-neighbour-light", language: "ar", primary: true }),
    ]);
    expect(String(fetchMock.mock.calls[0][0])).toContain("/api/seeds?lang=ar");
  });

  it("normalizes delta aliases, refusal side, and done events", async () => {
    const frames = [
      'event: delta\ndata: {"channel":"grounded","content":"Supported "}\n\n',
      'event: refusal\ndata: {"side":"grounded","message":"Insufficient evidence."}\n\n',
      'event: done\ndata: {"status":"complete"}\n\n',
    ].join("");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(frames, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    })));
    const events: StreamEvent[] = [];
    await streamAudit(
      { question: "Does this rule apply?", language: "en", mode: "cached" },
      (event) => events.push(event),
      new AbortController().signal,
    );

    expect(events).toEqual([
      { kind: "answer_token", side: "grounded", token: "Supported " },
      { kind: "refusal", side: "grounded", message: "Insufficient evidence." },
      { kind: "complete", side: undefined },
    ]);
  });

  it("reads the repository evaluation metric shape", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      generated_at: "2026-08-12T00:00:00Z",
      metrics: {
        citation_precision: 0.98,
        citation_recall: 0.94,
        fabrication_rate_ungrounded: 0.81,
        verifier_false_positive_rate: 0,
        counts: { questions: 50, corpus_articles: 1422 },
      },
      corpus: { article_count: 1422 },
      questions: [{
        id: "q-1",
        language: "en",
        question: "Which provision applies?",
        expected_articles: [85, 147],
        predicted_articles: [85],
      }],
    }), { status: 200, headers: { "Content-Type": "application/json" } })));

    await expect(getResults()).resolves.toMatchObject({
      groundedPrecision: 0.98,
      groundedRecall: 0.94,
      ungroundedFabricationRate: 0.81,
      verifierFalsePositiveRate: 0,
      questionCount: 50,
      corpusArticleCount: 1422,
      rows: [{ expected: ["85", "147"], grounded: ["85"] }],
    });
  });
});
