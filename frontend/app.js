import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  QueryClient,
  QueryClientProvider,
  keepPreviousData,
  useQuery,
} from "@tanstack/react-query";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  LineChart,
  Line,
  AreaChart,
  Area,
} from "recharts";
import html2canvas from "html2canvas";

// #region agent log
fetch("http://127.0.0.1:7528/ingest/89d055b3-625f-4e57-9ed5-0d70b4272673", {
  method: "POST",
  headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "974724" },
  body: JSON.stringify({
    sessionId: "974724",
    location: "app.js:imports",
    message: "bundle module evaluated after esm imports",
    data: {},
    timestamp: Date.now(),
    hypothesisId: "H1",
  }),
}).catch(() => {});
// #endregion

const API_BASE_URL = window.NEWSLENS_API_BASE_URL || "http://127.0.0.1:8000";
const HISTORY_KEY = "newslens-search-history";
const DEFAULT_SERIES_LABEL = "14d";

const OUTLET_COLORS = {
  CNN: "#3B82F6",
  Reuters: "#9CA3AF",
  "Fox News": "#EF4444",
  "BBC News": "#0EA5E9",
  "Al Jazeera English": "#8B5CF6",
};

const biasBadgeClass = (label = "") => {
  const normalized = label.toLowerCase();
  if (normalized.includes("left")) return "badge blue-bg";
  if (normalized.includes("right")) return "badge red-bg";
  return "badge gray-bg";
};

const readHistory = () => {
  try {
    const parsed = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
    return Array.isArray(parsed) ? parsed.slice(0, 5) : [];
  } catch {
    return [];
  }
};

function updateHistory(term) {
  const topic = term.trim();
  if (!topic) return;
  const next = [topic, ...readHistory().filter((item) => item.toLowerCase() !== topic.toLowerCase())].slice(0, 5);
  localStorage.setItem(HISTORY_KEY, JSON.stringify(next));
}

function formatBiasPosition(score) {
  if (typeof score !== "number") return 50;
  return Math.max(2, Math.min(98, ((score + 1) / 2) * 100));
}

function emotionalIntensity(sentimentScore) {
  if (typeof sentimentScore !== "number") return "N/A";
  return Math.min(10, Math.abs(sentimentScore) * 10).toFixed(1);
}

function framingPhrase(text) {
  if (!text || typeof text !== "string") return "—";
  const t = text.trim();
  if (!t) return "—";
  return t.length > 120 ? `${t.slice(0, 117)}…` : t;
}

async function fetchAnalysis(topic) {
  const response = await fetch(`${API_BASE_URL}/analyze?topic=${encodeURIComponent(topic)}`);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || "Failed to fetch analysis.");
  }
  const payload = await response.json();
  if (!payload.success) {
    throw new Error(payload.error || "Backend returned an unsuccessful response.");
  }
  return payload.data;
}

async function fetchOutletProfile(outlet) {
  const response = await fetch(`${API_BASE_URL}/outlet-profile?outlet=${encodeURIComponent(outlet)}`);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || "Failed to load outlet profile.");
  }
  const payload = await response.json();
  if (!payload.success) {
    throw new Error(payload.error || "Profile request failed.");
  }
  return payload.data;
}

async function fetchTopicTrend(topic, days = 7) {
  const response = await fetch(
    `${API_BASE_URL}/topic-trend?topic=${encodeURIComponent(topic)}&days=${days}`
  );
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || "Failed to load topic trend.");
  }
  const payload = await response.json();
  if (!payload.success) {
    throw new Error(payload.error || "Topic trend request failed.");
  }
  return payload.data;
}

function computeBiasDistribution(outlets) {
  const active = outlets.filter((o) => o.article_count > 0);
  if (!active.length) {
    return { text: "No outlets with articles for this topic yet.", left: 0, center: 0, right: 0 };
  }
  let left = 0;
  let center = 0;
  let right = 0;
  for (const o of active) {
    const label = (o.dominant_bias_label || "").toLowerCase();
    if (label.includes("left")) left += 1;
    else if (label.includes("right")) right += 1;
    else center += 1;
  }
  const t = active.length;
  const pct = (n) => Math.round((n / t) * 100);
  return {
    left: pct(left),
    center: pct(center),
    right: pct(right),
    text: `${pct(left)}% left, ${pct(center)}% center, ${pct(right)}% right`,
  };
}

function extremOutlets(outlets) {
  const withBias = outlets.filter((o) => o.article_count > 0 && typeof o.avg_bias_score === "number");
  if (!withBias.length) return { left: null, right: null };
  const sorted = [...withBias].sort((a, b) => a.avg_bias_score - b.avg_bias_score);
  return { left: sorted[0].source, right: sorted[sorted.length - 1].source };
}

function LoadingSkeleton() {
  return (
    <section className="skeleton-grid">
      <div className="card skeleton-card tall" />
      <div className="card skeleton-card" />
      <div className="card skeleton-card" />
      <div className="card skeleton-card" />
      <div className="card skeleton-card wide" />
    </section>
  );
}

function BiasSpectrum({ outlets }) {
  return (
    <section id="dashboard" className="bias-hero card">
      <div className="section-head">
        <h2>Bias Spectrum</h2>
        <span>Prominent signal for this topic</span>
      </div>
      <div className="spectrum-track">
        <div className="stop left" />
        <div className="stop center" />
        <div className="stop right" />
        {outlets
          .filter((outlet) => outlet.article_count > 0)
          .map((outlet) => (
            <div
              key={outlet.source}
              className="marker"
              style={{ left: `${formatBiasPosition(outlet.avg_bias_score)}%` }}
            >
              {outlet.source}
            </div>
          ))}
      </div>
      <div className="spectrum-legend">
        <p>
          <span className="dot blue" />
          Left-leaning
        </p>
        <p>
          <span className="dot gray" />
          Center / neutral
        </p>
        <p>
          <span className="dot red" />
          Right-leaning
        </p>
      </div>
    </section>
  );
}

function SparklineSeries({ series, color }) {
  const pts = (series || []).filter((d) => typeof d.avg_bias === "number");
  if (pts.length < 2) return null;
  const w = 120;
  const h = 32;
  const vals = pts.map((p) => p.avg_bias);
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const span = max - min || 1;
  const path = pts
    .map((p, i) => {
      const x = (i / (pts.length - 1)) * w;
      const y = h - ((p.avg_bias - min) / span) * (h - 4) - 2;
      return `${i === 0 ? "M" : "L"}${x},${y}`;
    })
    .join(" ");
  return (
    <svg width={w} height={h} className="sparkline-svg" aria-hidden>
      <path d={path} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function SourceProfileSection({ outletName }) {
  const [open, setOpen] = useState(false);
  const q = useQuery({
    queryKey: ["outlet-profile", outletName],
    queryFn: () => fetchOutletProfile(outletName),
    enabled: open,
    staleTime: 60_000,
  });

  return (
    <div className="source-profile-wrap">
      <button type="button" className="source-profile-toggle" onClick={() => setOpen(!open)}>
        <span>Source Profile</span>
        <span className="chevron" aria-hidden>
          {open ? "▼" : "▶"}
        </span>
      </button>
      {open ? (
        <div className="source-profile-body">
          {q.isLoading ? <p className="micro-muted">Loading historical stats…</p> : null}
          {q.isError ? (
            <p className="micro-error">Could not load profile: {q.error?.message || "Error"}</p>
          ) : null}
          {q.data ? (
            <div className="source-profile-inner">
              <div className="stat-pills">
                <div className="stat-pill">
                  <span>Avg bias</span>
                  <strong>{q.data.avg_bias_score != null ? q.data.avg_bias_score.toFixed(3) : "—"}</strong>
                </div>
                <div className="stat-pill">
                  <span>Avg sentiment</span>
                  <strong>
                    {q.data.avg_sentiment_score != null ? q.data.avg_sentiment_score.toFixed(3) : "—"}
                  </strong>
                </div>
                <div className="stat-pill">
                  <span>Articles (all topics)</span>
                  <strong>{q.data.article_count ?? 0}</strong>
                </div>
              </div>
              {q.data.series?.length ? (
                <div className="sparkline-row">
                  <span className="micro-muted">Bias (daily, {DEFAULT_SERIES_LABEL})</span>
                  <SparklineSeries series={q.data.series} color={OUTLET_COLORS[outletName] || "#111827"} />
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function OutletCard({ outlet, compareSelected, onCompareClick }) {
  const selectedClass = compareSelected ? "outlet-card-selected" : "";
  return (
    <article className={`card outlet-card ${selectedClass}`}>
      <div className="outlet-card-top">
        <h3>{outlet.source}</h3>
        <button type="button" className="btn-compare" onClick={() => onCompareClick(outlet.source)}>
          Compare
        </button>
      </div>
      <p className={biasBadgeClass(outlet.dominant_bias_label)}>{outlet.dominant_bias_label || "No bias label"}</p>
      <p className="body">{outlet.missing_angle || "No framing summary available yet for this outlet."}</p>
      <div className="metric-row">
        <span>Sentiment score</span>
        <strong>
          {typeof outlet.avg_sentiment_score === "number" ? outlet.avg_sentiment_score.toFixed(3) : "N/A"}
        </strong>
      </div>
      <div className="metric-row">
        <span>Emotional intensity (0-10)</span>
        <strong>{emotionalIntensity(outlet.avg_sentiment_score)}</strong>
      </div>
      <SourceProfileSection outletName={outlet.source} />
    </article>
  );
}

function OutletGrid({ outlets, compareSelection, onCompareClick }) {
  const selectedSet = new Set(compareSelection);
  return (
    <section id="outlets" className="outlets-grid">
      {outlets.map((outlet) => (
        <OutletCard
          key={outlet.source}
          outlet={outlet}
          compareSelected={selectedSet.has(outlet.source)}
          onCompareClick={onCompareClick}
        />
      ))}
    </section>
  );
}

function ComparisonPanel({ pair, outlets, onExit }) {
  const map = useMemo(() => Object.fromEntries(outlets.map((o) => [o.source, o])), [outlets]);
  const a = pair[0] ? map[pair[0]] : null;
  const b = pair[1] ? map[pair[1]] : null;

  return (
    <section className="card comparison-panel" aria-label="Outlet comparison">
      <div className="comparison-head">
        <h2>Outlet comparison</h2>
        <button type="button" className="btn-exit-compare" onClick={onExit}>
          Exit Comparison
        </button>
      </div>
      <div className="comparison-grid">
        <div className="comparison-col">
          <p className="comparison-label">{a?.source || "—"}</p>
          {a ? (
            <ul className="comparison-list">
              <li>
                <span>Bias score</span>
                <strong>{typeof a.avg_bias_score === "number" ? a.avg_bias_score.toFixed(3) : "N/A"}</strong>
              </li>
              <li>
                <span>Sentiment score</span>
                <strong>
                  {typeof a.avg_sentiment_score === "number" ? a.avg_sentiment_score.toFixed(3) : "N/A"}
                </strong>
              </li>
              <li>
                <span>Headline used</span>
                <strong className="wrap-strong">{a.headline || "—"}</strong>
              </li>
              <li>
                <span>Emotional intensity</span>
                <strong>{emotionalIntensity(a.avg_sentiment_score)}</strong>
              </li>
              <li>
                <span>Key framing phrase</span>
                <strong className="wrap-strong">{framingPhrase(a.missing_angle)}</strong>
              </li>
            </ul>
          ) : (
            <p className="micro-muted">Select a second outlet.</p>
          )}
        </div>
        <div className="comparison-divider" aria-hidden />
        <div className="comparison-col">
          <p className="comparison-label">{b?.source || "—"}</p>
          {b ? (
            <ul className="comparison-list">
              <li>
                <span>Bias score</span>
                <strong>{typeof b.avg_bias_score === "number" ? b.avg_bias_score.toFixed(3) : "N/A"}</strong>
              </li>
              <li>
                <span>Sentiment score</span>
                <strong>
                  {typeof b.avg_sentiment_score === "number" ? b.avg_sentiment_score.toFixed(3) : "N/A"}
                </strong>
              </li>
              <li>
                <span>Headline used</span>
                <strong className="wrap-strong">{b.headline || "—"}</strong>
              </li>
              <li>
                <span>Emotional intensity</span>
                <strong>{emotionalIntensity(b.avg_sentiment_score)}</strong>
              </li>
              <li>
                <span>Key framing phrase</span>
                <strong className="wrap-strong">{framingPhrase(b.missing_angle)}</strong>
              </li>
            </ul>
          ) : (
            <p className="micro-muted">Select another outlet.</p>
          )}
        </div>
      </div>
    </section>
  );
}

function HeadlineComparison({ outlets }) {
  return (
    <section id="topics" className="card headlines">
      <div className="section-head">
        <h2>Headline Comparison</h2>
        <span>Same topic, different framing</span>
      </div>
      <div className="headline-grid">
        {outlets.map((outlet) => (
          <article key={outlet.source} className="headline-item">
            <p className="headline-source">{outlet.source}</p>
            <p className="headline-text">{outlet.headline || "No headline available for this topic yet."}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

function SentimentDistribution({ outlets }) {
  const data = useMemo(
    () =>
      outlets.map((outlet) => ({
        outlet: outlet.source,
        positive: outlet.sentiment_labels?.Positive || 0,
        neutral: outlet.sentiment_labels?.Neutral || 0,
        negative: outlet.sentiment_labels?.Negative || 0,
      })),
    [outlets]
  );

  return (
    <section className="card chart-card">
      <div className="section-head">
        <h2>Sentiment Distribution</h2>
        <span>Positive / neutral / negative by outlet</span>
      </div>
      <div className="chart-wrap">
        <ResponsiveContainer width="100%" height={320}>
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
            <XAxis dataKey="outlet" />
            <YAxis allowDecimals={false} />
            <Tooltip />
            <Legend />
            <Bar dataKey="positive" fill="#10B981" radius={[6, 6, 0, 0]} />
            <Bar dataKey="neutral" fill="#9CA3AF" radius={[6, 6, 0, 0]} />
            <Bar dataKey="negative" fill="#EF4444" radius={[6, 6, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

function Timeline({ timeline, outlets }) {
  const activeOutlets = outlets.filter((outlet) => outlet.article_count > 0);
  return (
    <section className="card chart-card">
      <div className="section-head">
        <h2>Narrative Timeline</h2>
        <span>Bias score trend over the last 7 days</span>
      </div>
      <div className="chart-wrap">
        <ResponsiveContainer width="100%" height={320}>
          <LineChart data={timeline}>
            <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
            <XAxis dataKey="date" />
            <YAxis domain={[-1, 1]} />
            <Tooltip />
            <Legend />
            {activeOutlets.map((outlet) => (
              <Line
                key={outlet.source}
                type="monotone"
                dataKey={outlet.source}
                stroke={OUTLET_COLORS[outlet.source] || "#111827"}
                strokeWidth={2.2}
                dot={{ r: 3 }}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

function TopicTrendChart({ topic, outlets }) {
  const activeOutlets = outlets.filter((outlet) => outlet.article_count > 0);
  const q = useQuery({
    queryKey: ["topic-trend", topic, 7],
    queryFn: () => fetchTopicTrend(topic, 7),
    enabled: Boolean(topic),
    staleTime: 30_000,
  });

  const series = q.data?.series || [];

  return (
    <section className="card chart-card topic-trend-card">
      <div className="section-head">
        <h2>Topic coverage by outlet</h2>
        <span>Article volume per outlet over the last 7 days</span>
      </div>
      {q.isLoading ? <p className="chart-status">Loading trend data…</p> : null}
      {q.isError ? <p className="chart-status error">Could not load trend: {q.error?.message}</p> : null}
      {q.isSuccess ? (
        <div className="chart-wrap chart-wrap-tall">
          <ResponsiveContainer width="100%" height={360}>
            <AreaChart data={series}>
              <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
              <XAxis dataKey="date" />
              <YAxis allowDecimals={false} />
              <Tooltip />
              <Legend />
              {activeOutlets.map((outlet) => (
                <Area
                  key={outlet.source}
                  type="monotone"
                  dataKey={outlet.source}
                  stackId="topic-volume"
                  stroke={OUTLET_COLORS[outlet.source] || "#111827"}
                  fill={OUTLET_COLORS[outlet.source] || "#111827"}
                  fillOpacity={0.55}
                />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        </div>
      ) : null}
    </section>
  );
}

function MissingAngleCard({ missingAngle }) {
  return (
    <section id="methodology" className="missing-angle card">
      <p className="eyebrow">Editorial insight</p>
      <h2>Missing Angle</h2>
      <p>{missingAngle?.value || "Missing-angle analysis is not available for this topic yet."}</p>
      <div className="reasoning-box">
        <h4>Reasoning</h4>
        <p>{missingAngle?.reasoning || "No additional reasoning was returned by the backend."}</p>
      </div>
    </section>
  );
}

function Header({ onStartAnalysis }) {
  return (
    <header className="topbar">
      <div className="brand-lockup">
        <div className="brand">NewsLens</div>
        <p className="brand-tag">Truth in headlines. Bias in framing.</p>
      </div>
      <nav>
        <a href="#dashboard" className="active">
          Dashboard
        </a>
        <a href="#topics">Topics</a>
        <a href="#outlets">Outlets</a>
        <a href="#methodology">Methodology</a>
      </nav>
      <button className="cta" onClick={onStartAnalysis}>
        Start Analysis
      </button>
    </header>
  );
}

function ResultsHeader({ topic, outlets, missingAngle, shareCardRef, onShare, shareBusy, shareError }) {
  const dist = useMemo(() => computeBiasDistribution(outlets), [outlets]);
  const ex = useMemo(() => extremOutlets(outlets), [outlets]);
  const teaser = useMemo(() => {
    const v = missingAngle?.value;
    if (!v || typeof v !== "string") return "Perspective gaps may appear as more outlets publish.";
    const one = v.split(/(?<=[.!?])\s+/)[0] || v;
    return one.length > 140 ? `${one.slice(0, 137)}…` : one;
  }, [missingAngle]);

  return (
    <div className="results-header card">
      <div>
        <p className="eyebrow">Results</p>
        <h2 className="results-topic">{topic}</h2>
        <p className="results-meta muted">
          {dist.text} · Most left: {ex.left || "—"} · Most right: {ex.right || "—"}
        </p>
        {shareError ? <p className="share-inline-error">{shareError}</p> : null}
      </div>
      <button
        type="button"
        className="btn-share"
        disabled={shareBusy}
        onClick={() => onShare(shareCardRef)}
      >
        {shareBusy ? "Saving…" : "Share"}
      </button>
      <div ref={shareCardRef} className="share-card-capture share-card-offscreen" aria-hidden="true">
        <div className="share-card-brand">NewsLens</div>
        <p className="share-card-topic">{topic}</p>
        <p className="share-card-line">Bias mix: {dist.text}</p>
        <p className="share-card-line">
          Most left: {ex.left || "—"} · Most right: {ex.right || "—"}
        </p>
        <p className="share-card-teaser">{teaser}</p>
      </div>
    </div>
  );
}

function Hero({
  searchInput,
  setSearchInput,
  onSubmit,
  searchRef,
  isError,
  error,
  history,
  runSearch,
}) {
  return (
    <section className="hero">
      <p className="eyebrow">NewsLens editorial intelligence</p>
      <h1>Read the same story through every bias line.</h1>
      <p className="lede">
        NewsLens maps truth signals, bias direction, and narrative framing so you can compare how outlets shape the
        same topic.
      </p>
      <form className="search-form" onSubmit={onSubmit}>
        <input
          ref={searchRef}
          className="search-input"
          type="text"
          placeholder="Search a topic (e.g. trade war, AI regulation, climate policy)"
          value={searchInput}
          onChange={(event) => setSearchInput(event.target.value)}
        />
        <button className="search-btn" type="submit">
          Analyze
        </button>
      </form>
      {isError ? <p className="inline-error">Could not load analysis: {error.message}</p> : null}
      <div className="history-row">
        {history.map((item) => (
          <button key={item} className="history-chip" onClick={() => runSearch(item)}>
            {item}
          </button>
        ))}
      </div>
    </section>
  );
}

function App() {
  const [searchInput, setSearchInput] = useState("");
  const [topic, setTopic] = useState("");
  const [history, setHistory] = useState(readHistory);
  const searchRef = useRef(null);
  const shareCardRef = useRef(null);
  const [compareSelection, setCompareSelection] = useState([]);
  const [shareBusy, setShareBusy] = useState(false);
  const [shareError, setShareError] = useState(null);

  const query = useQuery({
    queryKey: ["analysis", topic],
    queryFn: () => fetchAnalysis(topic),
    enabled: Boolean(topic),
    placeholderData: keepPreviousData,
    retry: 1,
  });

  useEffect(() => {
    setHistory(readHistory());
  }, []);

  // #region agent log
  useEffect(() => {
    fetch("http://127.0.0.1:7528/ingest/89d055b3-625f-4e57-9ed5-0d70b4272673", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "974724" },
      body: JSON.stringify({
        sessionId: "974724",
        location: "app.js:App",
        message: "App component mounted (useEffect)",
        data: {},
        timestamp: Date.now(),
        hypothesisId: "H2",
      }),
    }).catch(() => {});
  }, []);
  // #endregion

  const runSearch = (nextTopic) => {
    const normalized = nextTopic.trim();
    if (!normalized) return;
    setTopic(normalized);
    setSearchInput(normalized);
    updateHistory(normalized);
    setHistory(readHistory());
    setCompareSelection([]);
  };

  const onSubmit = (event) => {
    event.preventDefault();
    runSearch(searchInput);
  };

  const handleStartAnalysis = () => {
    searchRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => searchRef.current?.focus(), 300);
  };

  const handleCompareClick = (source) => {
    setCompareSelection((prev) => {
      if (prev.includes(source)) {
        return prev.filter((s) => s !== source);
      }
      if (prev.length < 2) {
        return [...prev, source];
      }
      return [prev[0], source];
    });
  };

  const exitComparison = () => setCompareSelection([]);

  const handleShare = async (ref) => {
    const el = ref?.current;
    if (!el) return;
    setShareError(null);
    setShareBusy(true);
    try {
      const canvas = await html2canvas(el, {
        scale: 2,
        backgroundColor: "#ffffff",
        useCORS: true,
      });
      const link = document.createElement("a");
      link.download = `newslens-${topic.replace(/\s+/g, "-").slice(0, 40)}.png`;
      link.href = canvas.toDataURL("image/png");
      link.click();
    } catch (e) {
      console.error(e);
      setShareError("Could not generate image. Try again.");
    } finally {
      setShareBusy(false);
    }
  };

  const data = query.data;
  const outlets = data?.outlets || [];
  const timeline = data?.timeline || [];
  const comparing = compareSelection.length === 2;

  return (
    <div className="page">
      <Header onStartAnalysis={handleStartAnalysis} />
      <Hero
        searchInput={searchInput}
        setSearchInput={setSearchInput}
        onSubmit={onSubmit}
        searchRef={searchRef}
        isError={query.isError}
        error={query.error}
        history={history}
        runSearch={runSearch}
      />

      {!topic ? (
        <p className="empty-note">Start with a topic to generate a full outlet comparison dashboard.</p>
      ) : null}
      {query.isFetching ? <LoadingSkeleton /> : null}

      {data ? (
        <main className="results-stack">
          <ResultsHeader
            topic={data.topic}
            outlets={outlets}
            missingAngle={data.missing_angle}
            shareCardRef={shareCardRef}
            onShare={handleShare}
            shareBusy={shareBusy}
            shareError={shareError}
          />
          {comparing ? (
            <ComparisonPanel pair={compareSelection} outlets={outlets} onExit={exitComparison} />
          ) : null}
          <BiasSpectrum outlets={outlets} />
          <OutletGrid outlets={outlets} compareSelection={compareSelection} onCompareClick={handleCompareClick} />
          <HeadlineComparison outlets={outlets} />
          <div className="chart-grid">
            <SentimentDistribution outlets={outlets} />
            <div className="timeline-column">
              <Timeline timeline={timeline} outlets={outlets} />
              <TopicTrendChart topic={data.topic} outlets={outlets} />
            </div>
          </div>
          <MissingAngleCard missingAngle={data.missing_angle} />
        </main>
      ) : null}
    </div>
  );
}

const queryClient = new QueryClient();

// #region agent log
(function bootstrap() {
  const rootEl = document.getElementById("root");
  if (!rootEl) {
    fetch("http://127.0.0.1:7528/ingest/89d055b3-625f-4e57-9ed5-0d70b4272673", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "974724" },
      body: JSON.stringify({
        sessionId: "974724",
        location: "app.js:bootstrap",
        message: "no #root element",
        data: {},
        timestamp: Date.now(),
        hypothesisId: "H3",
      }),
    }).catch(() => {});
    return;
  }
  try {
    createRoot(rootEl).render(
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    );
    fetch("http://127.0.0.1:7528/ingest/89d055b3-625f-4e57-9ed5-0d70b4272673", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "974724" },
      body: JSON.stringify({
        sessionId: "974724",
        location: "app.js:bootstrap",
        message: "createRoot().render ok",
        data: {},
        timestamp: Date.now(),
        hypothesisId: "H2",
      }),
    }).catch(() => {});
  } catch (e) {
    fetch("http://127.0.0.1:7528/ingest/89d055b3-625f-4e57-9ed5-0d70b4272673", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "974724" },
      body: JSON.stringify({
        sessionId: "974724",
        location: "app.js:bootstrap",
        message: "createRoot/render threw",
        data: { err: e && (e.message || String(e)), stack: e && e.stack },
        timestamp: Date.now(),
        hypothesisId: "H2",
      }),
    }).catch(() => {});
    throw e;
  }
})();
// #endregion
