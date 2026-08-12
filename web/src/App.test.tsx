import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

function apiResponse(input: RequestInfo | URL): Response {
  const url = String(input);
  if (url.includes("/api/config")) {
    return new Response(JSON.stringify({ cache_mode: "cached", live_available: false }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }
  if (url.includes("/api/seeds?lang=ar")) {
    return new Response(JSON.stringify({
      questions: [{
        id: "seed-ar-neighbour-light",
        language: "ar",
        question: "هل يحق لمالك عقار أن يطلب إزالة بناء جاره؟",
        fabrication_rate: 1,
        is_primary: true,
      }],
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  }
  return new Response(JSON.stringify({ seeds: [] }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("bilingual application shell", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.history.replaceState({}, "", "/");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => apiResponse(input)));
  });

  it("keeps the independence statement on the demo route and mirrors to Arabic", async () => {
    render(<App />);
    expect(screen.getByText("Independent demo built by Minhal Abdul Sami. Not affiliated with HAQQ.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Cached/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Cached replay")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Switch interface to Arabic" }));
    await waitFor(() => expect(document.documentElement).toHaveAttribute("dir", "rtl"));
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("اسأل مرة");
    const arabicSeed = await screen.findByRole("button", { name: /هل يحق لمالك عقار/ });
    expect(arabicSeed).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(arabicSeed);
    expect(arabicSeed).toHaveAttribute("aria-pressed", "true");
  });

  it("keeps the independence footer and measured metrics on the results route", async () => {
    window.history.replaceState({}, "", "/results");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("/api/results")) {
        return new Response(JSON.stringify({
          generated_at: "2026-08-12T00:00:00Z",
          corpus_article_count: 1422,
          metrics: {
            citation_precision: 0.98,
            citation_recall: 0.94,
            fabrication_rate_ungrounded: 1,
            verifier_false_positive_rate: 0,
            counts: { questions: 50 },
          },
          questions: [],
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return apiResponse(input);
    }));
    render(<App />);

    expect(screen.getByText("Independent demo built by Minhal Abdul Sami. Not affiliated with HAQQ.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("98%")).toBeInTheDocument());
    expect(screen.getByText("0%")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Live audit" })).toBeInTheDocument();
  });
});
