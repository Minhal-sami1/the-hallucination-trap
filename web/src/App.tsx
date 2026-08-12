import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  getConfig,
  getResults,
  getSeeds,
  streamAudit,
  type AnswerMode,
  type AnswerSide,
  type AppConfig,
  type AuditCitation,
  type EvaluationResult,
  type Language,
  type SeedQuestion,
  type StageName,
  type StageStatus,
  type StreamEvent,
  type Verdict,
} from "./api";
import { copy } from "./i18n";

interface LaneState {
  text: string;
  stage: StageName;
  status: StageStatus;
  detail: string | null;
  refusal: string | null;
}

const initialLane = (): LaneState => ({
  text: "",
  stage: "queued",
  status: "idle",
  detail: null,
  refusal: null,
});

function localeFor(language: Language): string {
  return language === "ar" ? "ar-AE" : "en-GB";
}

function formatInteger(value: number, language: Language): string {
  return new Intl.NumberFormat(localeFor(language), { maximumFractionDigits: 0 }).format(value);
}

function formatIndex(value: number, language: Language): string {
  return new Intl.NumberFormat(localeFor(language), { minimumIntegerDigits: 2, useGrouping: false }).format(value);
}

function formatRate(value: number | null, language: Language): string {
  if (value === null) return "—";
  const normalized = value > 1 ? value / 100 : value;
  return new Intl.NumberFormat(localeFor(language), {
    style: "percent",
    maximumFractionDigits: 1,
    minimumFractionDigits: normalized > 0 && normalized < 0.01 ? 1 : 0,
  }).format(normalized);
}

function formatDate(value: string | null, language: Language): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat(localeFor(language), { dateStyle: "medium", timeStyle: "short" }).format(date);
}

function ExternalIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" width="16" height="16">
      <path d="M7 5H5.75A1.75 1.75 0 0 0 4 6.75v7.5C4 15.22 4.78 16 5.75 16h7.5A1.75 1.75 0 0 0 15 14.25V13M10 4h6v6M16 4l-7 7" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.6" />
    </svg>
  );
}

function ScaleIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" width="22" height="22">
      <path d="M12 3v17M7 21h10M5 6h14M5 6 2 12h6L5 6Zm14 0-3 6h6l-3-6Z" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.7" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" width="18" height="18">
      <path d="m4.5 10.5 3.2 3.1 7.8-8" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" />
    </svg>
  );
}

function CrossIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" width="18" height="18">
      <path d="m5 5 10 10M15 5 5 15" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="2" />
    </svg>
  );
}

function QuestionIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" width="18" height="18">
      <path d="M7.3 7.2A2.9 2.9 0 0 1 10 5.5c1.8 0 3 1.1 3 2.6 0 2-2.5 2.2-2.8 4M10.2 15h.01" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="1.8" />
    </svg>
  );
}

function Header({ language, setLanguage, config }: { language: Language; setLanguage: (value: Language) => void; config: AppConfig | null }) {
  const t = copy[language];
  const onResults = window.location.pathname.replace(/\/$/, "") === "/results";
  return (
    <header className="site-header">
      <a className="brand-lockup" href="/" aria-label={`${t.product}: ${t.demo}`}>
        <span className="brand-mark"><ScaleIcon /></span>
        <span>
          <strong>{t.product}</strong>
          <small>{t.proofLine}</small>
        </span>
      </a>

      <div className="corpus-status" aria-live="polite">
        <span className={`status-dot ${config?.corpus ? "is-ready" : ""}`} />
        <span>
          <small>{t.corpus}</small>
          <strong>
            {config?.corpus
              ? `${formatInteger(config.corpus.articleCount, language)} · ${language === "ar" ? config.corpus.lawNameAr : config.corpus.lawNameEn}`
              : t.corpusUnavailable}
          </strong>
        </span>
      </div>

      <nav className="utility-nav" aria-label={t.primaryNav}>
        <a aria-current={!onResults ? "page" : undefined} href="/">{t.demo}</a>
        <a aria-current={onResults ? "page" : undefined} href="/results">{t.results}</a>
        <button
          className="language-toggle"
          type="button"
          onClick={() => setLanguage(language === "en" ? "ar" : "en")}
          aria-label={language === "en" ? "Switch interface to Arabic" : "تحويل الواجهة إلى الإنجليزية"}
        >
          <span aria-hidden="true">{language === "en" ? "AR" : "EN"}</span>
          <span>{t.languageName}</span>
        </button>
      </nav>
    </header>
  );
}

function Footer({ language, config }: { language: Language; config: AppConfig | null }) {
  const t = copy[language];
  return (
    <footer className="site-footer">
      <p lang="en">{t.independent}</p>
      <div className="footer-evidence">
        {config?.caughtCount !== null && config?.caughtCount !== undefined && (
          <span><strong>{formatInteger(config.caughtCount, language)}</strong> {t.caught}</span>
        )}
        {config?.corpus?.sourceUrl && (
          <a href={config.corpus.sourceUrl} target="_blank" rel="noreferrer">
            {t.corpus} <ExternalIcon />
          </a>
        )}
      </div>
    </footer>
  );
}

function Scoreboard({ language, citations }: { language: Language; citations: AuditCitation[] }) {
  const t = copy[language];
  const counts = useMemo(() => citations.reduce(
    (score, citation) => ({ ...score, [citation.verdict]: score[citation.verdict] + 1 }),
    { VERIFIED: 0, FABRICATED: 0, UNVERIFIABLE: 0 } as Record<Verdict, number>,
  ), [citations]);
  const total = citations.length;
  return (
    <div className="scoreboard" aria-label={`${t.verified}, ${t.fabricated}, ${t.defensibility}`}>
      <div className="score score-verified"><span>{t.verified}</span><strong>{formatInteger(counts.VERIFIED, language)}</strong></div>
      <div className="score score-fabricated"><span>{t.fabricated}</span><strong>{formatInteger(counts.FABRICATED, language)}</strong></div>
      <div className="score score-unverifiable"><span>{t.unverifiable}</span><strong>{formatInteger(counts.UNVERIFIABLE, language)}</strong></div>
      <div className="score score-defensibility"><span>{t.defensibility}</span><strong>{total ? formatRate(counts.VERIFIED / total, language) : "—"}</strong></div>
    </div>
  );
}

const groundedStages: StageName[] = ["retrieve", "rank", "generate", "parse", "verify"];
const ungroundedStages: StageName[] = ["generate", "parse", "verify"];

function StageRail({ language, side, lane }: { language: Language; side: AnswerSide; lane: LaneState }) {
  const t = copy[language];
  const stages = side === "grounded" ? groundedStages : ungroundedStages;
  const current = stages.indexOf(lane.stage);
  return (
    <ol className="stage-rail" aria-label={t.stage}>
      {stages.map((stage, index) => {
        const complete = (lane.stage === "complete" && lane.status === "complete")
          || current > index
          || (current === index && lane.status === "complete");
        const active = lane.status === "running" && lane.stage === stage;
        const failed = (lane.status === "failed" || lane.status === "degraded") && lane.stage === stage;
        return (
          <li className={`${complete ? "is-complete" : ""} ${active ? "is-active" : ""} ${failed ? "is-failed" : ""}`} key={stage}>
            <span className="stage-node" aria-hidden="true" />
            <span>{t[stage]}</span>
          </li>
        );
      })}
    </ol>
  );
}

function AnswerPanel({
  language,
  side,
  lane,
  citations,
  isRunning,
}: {
  language: Language;
  side: AnswerSide;
  lane: LaneState;
  citations: AuditCitation[];
  isRunning: boolean;
}) {
  const t = copy[language];
  const title = side === "ungrounded" ? t.ungrounded : t.grounded;
  const detail = side === "ungrounded" ? t.ungroundedDetail : t.groundedDetail;
  const statusText = lane.status === "idle" ? t.idle : lane.status === "running" ? t[lane.stage] : t[lane.status];
  return (
    <section className={`answer-panel panel-${side}`} aria-labelledby={`${side}-title`} aria-busy={isRunning && lane.status === "running"}>
      <header className="panel-header">
        <div>
          <span className="panel-index">{formatIndex(side === "ungrounded" ? 1 : 2, language)}</span>
          <h2 id={`${side}-title`}>{title}</h2>
          <p>{detail}</p>
        </div>
        <span className={`lane-status status-${lane.status}`} role="status" aria-live="polite">
          <span aria-hidden="true" /> {statusText}
        </span>
      </header>
      <StageRail language={language} side={side} lane={lane} />
      <Scoreboard language={language} citations={citations} />
      <div className="answer-body">
        {!lane.text && !lane.refusal && !lane.detail && (
          <p className="answer-placeholder">{t.waitingAnswer}</p>
        )}
        {lane.text && (
          <p className="streamed-answer">
            {lane.text}
            {isRunning && lane.status === "running" && <span className="stream-caret" aria-hidden="true" />}
          </p>
        )}
        {lane.refusal && (
          <div className="refusal-card" role="status">
            <span className="refusal-icon"><CheckIcon /></span>
            <div><strong>{t.correctRefusal}</strong><p>{lane.refusal || t.refusalDetail}</p></div>
          </div>
        )}
        {lane.detail && lane.status !== "running" && (
          <div className={`lane-message ${lane.status === "failed" ? "is-error" : ""}`} role="alert">
            <strong>{lane.status === "failed" ? t.failed : t.degraded}</strong>
            <p>{lane.detail}</p>
          </div>
        )}
      </div>
    </section>
  );
}

function VerdictIcon({ verdict }: { verdict: Verdict }) {
  if (verdict === "VERIFIED") return <CheckIcon />;
  if (verdict === "FABRICATED") return <CrossIcon />;
  return <QuestionIcon />;
}

function safeCitation(citation: AuditCitation, language: Language): AuditCitation {
  if (citation.verdict === "FABRICATED" && !citation.reason.trim()) {
    return { ...citation, verdict: "UNVERIFIABLE", reason: copy[language].noReason };
  }
  if (citation.verdict === "VERIFIED" && (!citation.sourceUrl || !citation.excerpt)) {
    return { ...citation, verdict: "UNVERIFIABLE", reason: copy[language].noReason };
  }
  return citation;
}

function AuditCard({ citation, language }: { citation: AuditCitation; language: Language }) {
  const t = copy[language];
  const label = citation.verdict === "VERIFIED" ? t.verified : citation.verdict === "FABRICATED" ? t.fabricated : t.unverifiable;
  const articleLabel = citation.articleNumber ? `${t.article} ${citation.articleNumber}` : citation.raw;
  return (
    <article className={`audit-card verdict-${citation.verdict.toLowerCase()}`}>
      <div className="verdict-line">
        <span className="verdict-icon"><VerdictIcon verdict={citation.verdict} /></span>
        <span className="verdict-stamp">{label}</span>
        <span className={`side-tag side-${citation.side}`}>{citation.side === "grounded" ? t.grounded : t.ungrounded}</span>
      </div>
      <h3>{articleLabel || label}</h3>
      {citation.lawName && <p className="law-identity">{citation.lawName}</p>}
      {citation.reason && <p className="verdict-reason">{citation.reason}</p>}
      {citation.excerpt && (
        <blockquote>
          <span>{t.evidenceExcerpt}</span>
          <p>{citation.excerpt.slice(0, 200)}{citation.excerpt.length > 200 ? "…" : ""}</p>
        </blockquote>
      )}
      {citation.sourceUrl && (
        <a className="source-link" href={citation.sourceUrl} target="_blank" rel="noreferrer">
          {t.officialSource} <ExternalIcon />
        </a>
      )}
    </article>
  );
}

function AuditRail({ language, citations }: { language: Language; citations: AuditCitation[] }) {
  const t = copy[language];
  return (
    <aside className="audit-rail" aria-labelledby="audit-title">
      <header className="audit-header">
        <div>
          <span className="panel-index">{formatIndex(3, language)}</span>
          <h2 id="audit-title">{t.audit}</h2>
          <p>{t.auditDetail}</p>
        </div>
        <span className="exact-key">{t.exactKey}</span>
      </header>
      <div className="audit-list">
        {!citations.length && (
          <div className="audit-empty">
            <ScaleIcon />
            <p>{t.waitingAudit}</p>
          </div>
        )}
        {citations.map((citation) => <AuditCard citation={citation} language={language} key={citation.id} />)}
      </div>
    </aside>
  );
}

function SeedChips({
  language,
  seeds,
  loading,
  error,
  selectedId,
  onSelect,
}: {
  language: Language;
  seeds: SeedQuestion[];
  loading: boolean;
  error: boolean;
  selectedId: string | null;
  onSelect: (seed: SeedQuestion) => void;
}) {
  const t = copy[language];
  return (
    <div className="seed-section">
      <div className="field-heading">
        <strong>{t.testedTraps}</strong>
        {loading && <span className="quiet-status">{t.seedLoading}</span>}
      </div>
      {error && <p className="inline-error">{t.seedLoadError}</p>}
      <div className="seed-list">
        {seeds.map((seed, index) => (
          <button
            className={`seed-chip ${selectedId === seed.id ? "is-selected" : ""}`}
            key={seed.id}
            type="button"
            onClick={() => onSelect(seed)}
            lang={seed.language}
            dir={seed.language === "ar" ? "rtl" : "ltr"}
            title={seed.question}
            aria-pressed={selectedId === seed.id}
          >
            <span className="seed-number">{formatIndex(index + 1, language)}</span>
            <span className="seed-copy">{seed.question}</span>
            {seed.primary && <span className="primary-tag">{t.primaryTrap}</span>}
            {seed.observedFabricationRate !== null && (
              <span className="seed-rate">{formatRate(seed.observedFabricationRate, language)} {t.rate}</span>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}

function DemoPage({ language, config }: { language: Language; config: AppConfig | null }) {
  const t = copy[language];
  const urlQuestion = new URLSearchParams(window.location.search).get("q") ?? "";
  const [question, setQuestion] = useState(urlQuestion);
  const [selectedSeed, setSelectedSeed] = useState<string | null>(null);
  const [mode, setMode] = useState<AnswerMode>("cached");
  const [seeds, setSeeds] = useState<SeedQuestion[]>([]);
  const [seedsLoading, setSeedsLoading] = useState(true);
  const [seedsError, setSeedsError] = useState(false);
  const [ungrounded, setUngrounded] = useState<LaneState>(initialLane);
  const [grounded, setGrounded] = useState<LaneState>(initialLane);
  const [citations, setCitations] = useState<AuditCitation[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [assertiveMessage, setAssertiveMessage] = useState("");
  const runController = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setSeedsLoading(true);
    setSeedsError(false);
    getSeeds(language, controller.signal)
      .then((items) => {
        setSeeds(items);
        setSelectedSeed((current) => {
          if (!current || items.some((seed) => seed.id === current)) return current;
          const replacement = items.find((seed) => seed.primary) ?? items[0];
          setQuestion(replacement?.question ?? "");
          return replacement?.id ?? null;
        });
      })
      .catch((error: unknown) => {
        if ((error as Error).name !== "AbortError") setSeedsError(true);
      })
      .finally(() => setSeedsLoading(false));
    return () => controller.abort();
  }, [language]);

  useEffect(() => () => runController.current?.abort(), []);

  const sideCitations = useMemo(() => ({
    ungrounded: citations.filter((citation) => citation.side === "ungrounded"),
    grounded: citations.filter((citation) => citation.side === "grounded"),
  }), [citations]);

  const applyEvent = (event: StreamEvent) => {
    const updateSide = (side: AnswerSide, mutate: (current: LaneState) => LaneState) => {
      if (side === "ungrounded") setUngrounded(mutate);
      else setGrounded(mutate);
    };
    if (event.kind === "answer_token") {
      updateSide(event.side, (lane) => lane.refusal
        ? lane
        : { ...lane, text: lane.text + event.token, status: "running", stage: "generate" });
    } else if (event.kind === "citation") {
      const citation = safeCitation(event.citation, language);
      setCitations((current) => {
        const existing = current.findIndex((item) => item.id === citation.id);
        if (existing === -1) return [...current, citation];
        const next = [...current];
        next[existing] = citation;
        return next;
      });
      if (citation.verdict === "FABRICATED") setAssertiveMessage(`${t.fabricated}: ${citation.raw}. ${citation.reason}`);
    } else if (event.kind === "stage") {
      updateSide(event.side, (lane) => ({ ...lane, stage: event.stage, status: event.status, detail: event.message ?? lane.detail }));
    } else if (event.kind === "refusal") {
      updateSide(event.side, (lane) => ({
        ...lane,
        refusal: event.message || t.refusalDetail,
        text: "",
        status: "complete",
        stage: "complete",
      }));
    } else if (event.kind === "complete") {
      if (event.side) updateSide(event.side, (lane) => ({ ...lane, status: "complete", stage: "complete" }));
      else {
        setUngrounded((lane) => ({ ...lane, status: "complete", stage: "complete" }));
        setGrounded((lane) => ({ ...lane, status: "complete", stage: "complete" }));
      }
    } else if (event.kind === "error") {
      const applyError = (lane: LaneState): LaneState => ({ ...lane, status: "failed", stage: event.stage ?? lane.stage, detail: event.message });
      if (event.side) updateSide(event.side, applyError);
      else {
        setUngrounded(applyError);
        setGrounded(applyError);
      }
    }
  };

  const runAudit = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = question.trim();
    if (!trimmed) {
      setFormError(t.emptyQuestion);
      return;
    }
    if (mode === "cached" && !selectedSeed) {
      setFormError(t.cachedNeedsSeed);
      return;
    }
    runController.current?.abort();
    const controller = new AbortController();
    runController.current = controller;
    setFormError(null);
    setAssertiveMessage("");
    setCitations([]);
    setUngrounded({ ...initialLane(), status: "running", stage: "generate" });
    setGrounded({ ...initialLane(), status: "running", stage: "retrieve" });
    setIsRunning(true);
    try {
      await streamAudit({ question: trimmed, language, mode, seedId: selectedSeed ?? undefined }, applyEvent, controller.signal);
      setUngrounded((lane) => lane.status === "running" ? { ...lane, status: "complete", stage: "complete" } : lane);
      setGrounded((lane) => lane.status === "running" ? { ...lane, status: "complete", stage: "complete" } : lane);
      const permalink = new URL(window.location.href);
      permalink.pathname = "/";
      permalink.searchParams.set("q", trimmed);
      permalink.searchParams.set("mode", mode);
      window.history.replaceState({}, "", permalink);
    } catch (error) {
      if ((error as Error).name !== "AbortError") {
        const message = (error as Error).message || t.streamFailed;
        setFormError(message);
        setUngrounded((lane) => lane.status === "running" ? { ...lane, status: "failed", detail: message } : lane);
        setGrounded((lane) => lane.status === "running" ? { ...lane, status: "failed", detail: message } : lane);
      }
    } finally {
      if (runController.current === controller) setIsRunning(false);
    }
  };

  const selectSeed = (seed: SeedQuestion) => {
    setQuestion(seed.question);
    setSelectedSeed(seed.id);
    setFormError(null);
  };

  return (
    <main id="main-content" className="demo-page">
      <section className="docket" aria-labelledby="page-title">
        <div className="docket-intro">
          <p className="eyebrow">{t.eyebrow}</p>
          <h1 id="page-title">{t.title}</h1>
          <p className="lede">{t.intro}</p>
        </div>
        <form className="question-form" onSubmit={runAudit}>
          <label htmlFor="legal-question">{t.inputLabel}</label>
          <p id="question-hint">{t.inputHint}</p>
          <textarea
            id="legal-question"
            aria-describedby="question-hint"
            placeholder={t.inputPlaceholder}
            value={question}
            onChange={(event) => {
              setQuestion(event.target.value);
              if (selectedSeed && !seeds.some((seed) => seed.id === selectedSeed && seed.question === event.target.value)) setSelectedSeed(null);
            }}
            rows={3}
          />
          <SeedChips
            language={language}
            seeds={seeds}
            loading={seedsLoading}
            error={seedsError}
            selectedId={selectedSeed}
            onSelect={selectSeed}
          />
          <div className="run-controls">
            <fieldset className="mode-switch">
              <legend>{t.mode}</legend>
              <div className="segmented-control">
                <button
                  className={mode === "cached" ? "is-active" : ""}
                  type="button"
                  onClick={() => setMode("cached")}
                  aria-pressed={mode === "cached"}
                >
                  <span className="mode-led" aria-hidden="true" />
                  <span><strong>{t.cached}</strong><small>{t.cachedHint}</small></span>
                </button>
                <button
                  className={mode === "live" ? "is-active" : ""}
                  type="button"
                  onClick={() => setMode("live")}
                  disabled={!config?.liveAvailable}
                  title={!config?.liveAvailable ? t.liveUnavailable : undefined}
                  aria-pressed={mode === "live"}
                >
                  <span className="mode-led" aria-hidden="true" />
                  <span><strong>{t.live}</strong><small>{config?.liveAvailable ? t.liveHint : t.liveUnavailable}</small></span>
                </button>
              </div>
            </fieldset>
            <button className="run-button" type="submit" disabled={isRunning} aria-busy={isRunning}>
              <span>{isRunning ? t.running : t.run}</span>
              <span className="run-arrow" aria-hidden="true">→</span>
            </button>
          </div>
          <div className="form-error-slot" aria-live="assertive">
            {formError && <p className="form-error" role="alert">{formError}</p>}
          </div>
        </form>
      </section>

      <div className="mode-ribbon" aria-live="polite">
        <span className={`mode-state mode-${mode}`}><span aria-hidden="true" />{mode === "cached" ? t.runModeCached : t.runModeLive}</span>
        <span>{t.auditDetail}</span>
      </div>

      <section className="workbench" aria-label={t.demo} aria-busy={isRunning}>
        <AnswerPanel language={language} side="ungrounded" lane={ungrounded} citations={sideCitations.ungrounded} isRunning={isRunning} />
        <AnswerPanel language={language} side="grounded" lane={grounded} citations={sideCitations.grounded} isRunning={isRunning} />
        <AuditRail language={language} citations={citations} />
      </section>
      <div className="sr-only" aria-live="assertive" aria-atomic="true">{assertiveMessage}</div>
    </main>
  );
}

function Metric({ label, value, language, kind = "rate" }: { label: string; value: number | null; language: Language; kind?: "rate" | "integer" }) {
  const t = copy[language];
  return (
    <article className="metric-card">
      <p>{label}</p>
      <strong>{value === null ? "—" : kind === "rate" ? formatRate(value, language) : formatInteger(value, language)}</strong>
      <small>{value === null ? t.notReported : t.metricSource}</small>
    </article>
  );
}

function CitationList({ citations, empty, notReported }: { citations: string[] | null; empty: string; notReported?: string }) {
  if (citations === null) return <span className="empty-value">{notReported ?? empty}</span>;
  return citations.length ? (
    <ul className="table-citations">{citations.map((citation, index) => <li key={`${citation}-${index}`}>{citation}</li>)}</ul>
  ) : <span className="empty-value">{empty}</span>;
}

function ResultsPage({ language }: { language: Language }) {
  const t = copy[language];
  const [result, setResult] = useState<EvaluationResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    getResults(controller.signal)
      .then(setResult)
      .catch((caught: unknown) => {
        if ((caught as Error).name !== "AbortError") setError(true);
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, []);
  return (
    <main id="main-content" className="results-page">
      <header className="results-intro">
        <p className="eyebrow">{t.resultsEyebrow}</p>
        <h1>{t.resultsTitle}</h1>
        <p>{t.resultsIntro}</p>
        {result?.generatedAt && <span className="evaluation-date">{t.lastRun}: <strong>{formatDate(result.generatedAt, language)}</strong></span>}
      </header>

      {loading && <div className="page-state" role="status"><span className="loading-bar" />{t.loading}…</div>}
      {error && <div className="page-state is-error" role="alert"><strong>{t.resultsLoadError}</strong><p>{t.resultsEmpty}</p></div>}
      {result && (
        <>
          <section className="metrics-grid" aria-label={t.results}>
            <Metric label={t.precision} value={result.groundedPrecision} language={language} />
            <Metric label={t.recall} value={result.groundedRecall} language={language} />
            <Metric label={t.fabricationRate} value={result.ungroundedFabricationRate} language={language} />
            <Metric label={t.falsePositiveRate} value={result.verifierFalsePositiveRate} language={language} />
            <Metric label={t.questionCount} value={result.questionCount} language={language} kind="integer" />
            <Metric label={t.articleCount} value={result.corpusArticleCount} language={language} kind="integer" />
          </section>
          <section className="evidence-table-section" aria-labelledby="evidence-table-title">
            <div className="table-heading">
              <div><p className="eyebrow">{t.evidenceTableCode}</p><h2 id="evidence-table-title">{t.evidenceTable}</h2></div>
              <span>{formatInteger(result.rows.length, language)} / {result.questionCount === null ? "—" : formatInteger(result.questionCount, language)}</span>
            </div>
            {result.rows.length ? (
              <div className="table-scroll" tabIndex={0} aria-label={t.evidenceTable}>
                <table>
                  <thead><tr><th scope="col">#</th><th scope="col">{t.question}</th><th scope="col">{t.expected}</th><th scope="col">{t.groundedFound}</th><th scope="col">{t.ungroundedFound}</th></tr></thead>
                  <tbody>
                    {result.rows.map((row, index) => (
                      <tr key={row.id}>
                        <th scope="row">{formatInteger(index + 1, language)}</th>
                        <td lang={row.language} dir={row.language === "ar" ? "rtl" : "ltr"}>{row.question}</td>
                        <td><CitationList citations={row.expected} empty={t.noCitations} /></td>
                        <td><CitationList citations={row.grounded} empty={t.noCitations} /></td>
                        <td><CitationList citations={row.ungrounded} empty={t.noCitations} notReported={t.notReported} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <div className="page-state"><p>{t.resultsEmpty}</p></div>}
          </section>
        </>
      )}
    </main>
  );
}

export function App() {
  const stored = window.localStorage.getItem("trap-language");
  const [language, setLanguageState] = useState<Language>(stored === "ar" ? "ar" : "en");
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [configFailed, setConfigFailed] = useState(false);

  const setLanguage = (value: Language) => {
    setLanguageState(value);
    window.localStorage.setItem("trap-language", value);
  };

  useEffect(() => {
    document.documentElement.lang = language;
    document.documentElement.dir = language === "ar" ? "rtl" : "ltr";
    document.title = language === "ar" ? "مصيدة الهلوسة" : "The Hallucination Trap";
  }, [language]);

  useEffect(() => {
    const controller = new AbortController();
    getConfig(controller.signal)
      .then(setConfig)
      .catch((error: unknown) => {
        if ((error as Error).name !== "AbortError") setConfigFailed(true);
      });
    return () => controller.abort();
  }, []);

  const route = window.location.pathname.replace(/\/$/, "") || "/";
  return (
    <div className="app-shell">
      <Header language={language} setLanguage={setLanguage} config={config} />
      {configFailed && (
        <div className="service-banner" role="status">
          <strong>{copy[language].apiErrorTitle}</strong>
          <span>{copy[language].apiErrorDetail}</span>
        </div>
      )}
      {route === "/results" ? <ResultsPage language={language} /> : <DemoPage language={language} config={config} />}
      <Footer language={language} config={config} />
    </div>
  );
}
