import React, { useEffect, useMemo, useRef, useState } from "https://esm.sh/react@18.3.1";
import { createRoot } from "https://esm.sh/react-dom@18.3.1/client";
import {
  QueryClient,
  QueryClientProvider,
  keepPreviousData,
  useQuery,
} from "https://esm.sh/@tanstack/react-query@5.100.7";
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
} from "https://esm.sh/recharts@2.12.7";

const API_BASE_URL = window.NEWSLENS_API_BASE_URL || "http://127.0.0.1:8000";
const HISTORY_KEY = "newslens-search-history";

const OUTLET_COLORS = {
  "CNN": "#3B82F6",
  "Reuters": "#9CA3AF",
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
        <p><span className="dot blue" />Left-leaning</p>
        <p><span className="dot gray" />Center / neutral</p>
        <p><span className="dot red" />Right-leaning</p>
      </div>
    </section>
  );
}

function OutletGrid({ outlets }) {
  return (
    <section id="outlets" className="outlets-grid">
      {outlets.map((outlet) => (
        <article key={outlet.source} className="card outlet-card">
          <h3>{outlet.source}</h3>
          <p className={biasBadgeClass(outlet.dominant_bias_label)}>{outlet.dominant_bias_label || "No bias label"}</p>
          <p className="body">{outlet.missing_angle || "No framing summary available yet for this outlet."}</p>
          <div className="metric-row">
            <span>Sentiment score</span>
            <strong>{typeof outlet.avg_sentiment_score === "number" ? outlet.avg_sentiment_score.toFixed(3) : "N/A"}</strong>
          </div>
          <div className="metric-row">
            <span>Emotional intensity (0-10)</span>
            <strong>{emotionalIntensity(outlet.avg_sentiment_score)}</strong>
          </div>
        </article>
      ))}
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
        <a href="#dashboard" className="active">Dashboard</a>
        <a href="#topics">Topics</a>
        <a href="#outlets">Outlets</a>
        <a href="#methodology">Methodology</a>
      </nav>
      <button className="cta" onClick={onStartAnalysis}>Start Analysis</button>
    </header>
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
        NewsLens maps truth signals, bias direction, and narrative framing so you can compare how
        outlets shape the same topic.
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
        <button className="search-btn" type="submit">Analyze</button>
      </form>
      {isError ? <p className="inline-error">Could not load analysis: {error.message}</p> : null}
      <div className="history-row">
        {history.map((item) => (
          <button key={item} className="history-chip" onClick={() => runSearch(item)}>{item}</button>
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

  const runSearch = (nextTopic) => {
    const normalized = nextTopic.trim();
    if (!normalized) return;
    setTopic(normalized);
    setSearchInput(normalized);
    updateHistory(normalized);
    setHistory(readHistory());
  };

  const onSubmit = (event) => {
    event.preventDefault();
    runSearch(searchInput);
  };

  const handleStartAnalysis = () => {
    searchRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => searchRef.current?.focus(), 300);
  };

  const data = query.data;
  const outlets = data?.outlets || [];
  const timeline = data?.timeline || [];

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

      {!topic ? <p className="empty-note">Start with a topic to generate a full outlet comparison dashboard.</p> : null}
      {query.isFetching ? <LoadingSkeleton /> : null}

      {data ? (
        <main className="results-stack">
          <BiasSpectrum outlets={outlets}></BiasSpectrum>
          <OutletGrid outlets={outlets}></OutletGrid>
          <HeadlineComparison outlets={outlets}></HeadlineComparison>
          <div className="chart-grid">
            <SentimentDistribution outlets={outlets}></SentimentDistribution>
            <Timeline timeline={timeline} outlets={outlets}></Timeline>
          </div>
          <MissingAngleCard missingAngle={data.missing_angle}></MissingAngleCard>
        </main>
      ) : null}
    </div>
  );
}

const queryClient = new QueryClient();
createRoot(document.getElementById("root")).render(
  <QueryClientProvider client={queryClient}>
    <App />
  </QueryClientProvider>
);
