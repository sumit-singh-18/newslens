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

const SUGGESTED_TOPICS = ["15-Minute Cities", "Digital Pound", "Right to Repair"];

const COVERAGE_STATUS = {
  HIGH: "high",
  DEVELOPING: "developing",
  INSUFFICIENT: "insufficient",
};

function normalizeCoverageStatus(raw) {
  const s = raw == null ? "" : String(raw).trim().toLowerCase();
  if (s === COVERAGE_STATUS.DEVELOPING) return COVERAGE_STATUS.DEVELOPING;
  if (s === COVERAGE_STATUS.INSUFFICIENT) return COVERAGE_STATUS.INSUFFICIENT;
  if (s === COVERAGE_STATUS.HIGH) return COVERAGE_STATUS.HIGH;
  return COVERAGE_STATUS.HIGH;
}

/** Narrative timeline & coverage: room for rotated dates + bottom legend */
const CHART_MARGIN_LINE_AREA = { top: 8, right: 16, bottom: 64, left: 16 };

/** Sentiment: outlet ticks + bottom legend clearance */
const CHART_MARGIN_SENTIMENT = { top: 8, right: 16, bottom: 80, left: 16 };

const CHART_LEGEND_WRAPPER = { paddingTop: "20px", fontSize: "12px" };

const CHART_FIXED_HEIGHT = 400;
const CHART_MIN_HEIGHT = 350;

const CHART_DATE_MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

function formatChartAxisDate(value) {
  if (value == null || value === "") return "";
  const s = String(value).trim();
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return s;
  const mo = Number(m[2]);
  const d = Number(m[3]);
  if (!Number.isFinite(mo) || mo < 1 || mo > 12 || !Number.isFinite(d)) return s;
  return `${CHART_DATE_MONTHS[mo - 1]} ${String(d).padStart(2, "0")}`;
}

function chartTooltipLabelFormatter(value) {
  if (value == null || value === "") return "";
  const s = String(value).trim();
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) return formatChartAxisDate(s);
  return s;
}

/**
 * Build per-outlet series totals from chart rows; keep only outlets with totalValue > 0.
 * Mirrors `data.filter((series) => series.totalValue > 0)` for Recharts legend fidelity.
 */
function outletKeysWithPositiveTotals(rows, outletSources, mode) {
  const list = Array.isArray(rows) ? rows : [];
  const series = [...new Set(outletSources)].map((source) => {
    let totalValue = 0;
    for (const row of list) {
      if (!row || typeof row !== "object") continue;
      const raw = row[source];
      if (raw == null || !Number.isFinite(Number(raw))) continue;
      const n = Number(raw);
      totalValue += mode === "volume" ? Math.max(0, n) : Math.abs(n);
    }
    return { source, totalValue };
  });
  return series.filter((s) => s.totalValue > 0).map((s) => s.source);
}

function candidateOutletSources(outlets) {
  const list = Array.isArray(outlets) ? outlets : [];
  return list.filter((o) => (o.article_count || 0) > 0).map((o) => o.source);
}

/** Outlet columns on chart rows (excludes `date`). */
function chartOutletKeysFromRows(rows) {
  const keys = new Set();
  const list = Array.isArray(rows) ? rows : [];
  for (const row of list) {
    if (!row || typeof row !== "object") continue;
    for (const k of Object.keys(row)) {
      if (k !== "date") keys.add(k);
    }
  }
  return [...keys];
}

function outletKeysForChart(rows, outlets) {
  const preferred = candidateOutletSources(outlets);
  return preferred.length > 0 ? preferred : chartOutletKeysFromRows(rows);
}

/** True when every outlet value is 0, null, or undefined for all rows (non-finite counts as empty). */
function isCoverageVolumeDatasetEmpty(rows, outlets) {
  const list = Array.isArray(rows) ? rows : [];
  const keys = outletKeysForChart(list, outlets);
  if (!keys.length) return true;
  if (!list.length) return true;
  for (const row of list) {
    for (const key of keys) {
      const raw = row[key];
      if (raw == null) continue;
      const n = Number(raw);
      if (Number.isFinite(n) && n !== 0) return false;
    }
  }
  return true;
}

function countDaysWithCoverageVolume(rows, keys) {
  const list = Array.isArray(rows) ? rows : [];
  let count = 0;
  for (const row of list) {
    if (!row) continue;
    let dayHas = false;
    for (const key of keys) {
      const raw = row[key];
      if (raw == null) continue;
      const n = Number(raw);
      if (Number.isFinite(n) && n > 0) {
        dayHas = true;
        break;
      }
    }
    if (dayHas) count++;
  }
  return count;
}

/** True when every bias value is null or undefined for all rows (0 is valid bias). */
function isTimelineBiasDatasetEmpty(rows, outlets) {
  const list = Array.isArray(rows) ? rows : [];
  const keys = outletKeysForChart(list, outlets);
  if (!keys.length) return true;
  if (!list.length) return true;
  for (const row of list) {
    for (const key of keys) {
      const raw = row[key];
      if (raw != null && Number.isFinite(Number(raw))) return false;
    }
  }
  return true;
}

function countDaysWithBiasData(rows, keys) {
  const list = Array.isArray(rows) ? rows : [];
  let count = 0;
  for (const row of list) {
    if (!row) continue;
    let dayHas = false;
    for (const key of keys) {
      const raw = row[key];
      if (raw != null && Number.isFinite(Number(raw))) {
        dayHas = true;
        break;
      }
    }
    if (dayHas) count++;
  }
  return count;
}

/**
 * Partial-period hint: show when fewer than 7 day slots or sparse points within a full week.
 * @param {"coverage" | "timeline"} mode
 */
function getChartHistoryPartialMeta(rows, outlets, mode) {
  const list = Array.isArray(rows) ? rows : [];
  const keys = outletKeysForChart(list, outlets);
  if (!keys.length) return { show: false, x: 0 };
  const daysWithData =
    mode === "coverage"
      ? countDaysWithCoverageVolume(list, keys)
      : countDaysWithBiasData(list, keys);
  if (daysWithData === 0) return { show: false, x: 0 };
  const sparse =
    list.length < 7 || (list.length === 7 && daysWithData < 7);
  if (!sparse) return { show: false, x: daysWithData };
  const x = list.length < 7 ? list.length : daysWithData;
  return { show: true, x };
}

function ChartHistoryBuildingEmptyState() {
  return (
    <div
      className="chart-history-building-empty"
      style={{
        minHeight: CHART_MIN_HEIGHT,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: "24px 20px",
        textAlign: "center",
      }}
    >
      <div style={{ fontSize: "1.35rem", marginBottom: "10px", lineHeight: 1 }} aria-hidden>
        🕐
      </div>
      <h3
        style={{
          margin: "0 0 8px",
          fontSize: "0.98rem",
          fontWeight: 600,
          color: "#64748b",
        }}
      >
        Coverage history is building
      </h3>
      <p
        style={{
          margin: 0,
          fontSize: "0.86rem",
          color: "#94a3b8",
          maxWidth: "26rem",
          lineHeight: 1.55,
        }}
      >
        This topic was just searched for the first time. Come back tomorrow to see how coverage volume changes
        across outlets over time.
      </p>
    </div>
  );
}

function ChartHistoryPartialHint({ x }) {
  if (x == null || x <= 0 || x >= 7) return null;
  const dayLabel = x === 1 ? "day" : "days";
  return (
    <p className="micro-muted" style={{ textAlign: "center", margin: "10px 0 0" }}>
      Showing {x} {dayLabel} of data — history builds daily as more searches happen
    </p>
  );
}

const CHART_AXIS_TICK = { fill: "#888", fontSize: 11 };

/** Shown when Missing Angle is absent or backend returned quota/API noise — never raw JSON/errors. */
const MISSING_ANGLE_UNAVAILABLE_COPY =
  "Editorial analysis temporarily unavailable. Check back shortly.";
/** User-facing copy when backend hit Gemini quota / transient LLM limits (Issue 3). */
const MISSING_ANGLE_SEARCH_AGAIN_SHORTLY =
  "Analysis will be available in ~1 minute. Search again shortly.";

function reasoningLooksLikeQuotaOrTransientFailure(reasoning) {
  const r = String(reasoning ?? "").toLowerCase();
  return (
    r.includes("quota") ||
    r.includes("429") ||
    r.includes("exceeded") ||
    r.includes("unavailable")
  );
}

/** True when the topic insight is empty but the backend explained a quota/capacity issue. */
function missingAngleShouldShowQuotaWaitMessage(ma) {
  if (!ma || typeof ma !== "object") return false;
  const rawVal = ma.value;
  const valueMissing =
    rawVal == null || (typeof rawVal === "string" && rawVal.trim() === "");
  return valueMissing && reasoningLooksLikeQuotaOrTransientFailure(ma.reasoning);
}

function missingAngleIsUnavailableUserFacing(ma) {
  if (!ma || typeof ma !== "object") return true;
  if (String(ma.analysis_status ?? "").toLowerCase() === "quota_limited") return true;
  const rawVal = ma.value;
  const valueMissing =
    rawVal == null || (typeof rawVal === "string" && rawVal.trim() === "");
  const r = String(ma.reasoning ?? "").toLowerCase();
  const reasoningLooksLikeQuotaOrLimit =
    r.includes("quota") || r.includes("429") || r.includes("exceeded");
  return valueMissing || reasoningLooksLikeQuotaOrLimit;
}

function missingAnglePresentationalCopy(ma) {
  const analysisStatus = String(ma?.analysis_status ?? "").toLowerCase();
  if (analysisStatus === "quota_limited") {
    return {
      body: MISSING_ANGLE_SEARCH_AGAIN_SHORTLY,
      reasoning: MISSING_ANGLE_SEARCH_AGAIN_SHORTLY,
    };
  }
  if (missingAngleShouldShowQuotaWaitMessage(ma)) {
    return {
      body: MISSING_ANGLE_SEARCH_AGAIN_SHORTLY,
      reasoning: MISSING_ANGLE_SEARCH_AGAIN_SHORTLY,
    };
  }
  if (missingAngleIsUnavailableUserFacing(ma)) {
    return {
      body: MISSING_ANGLE_UNAVAILABLE_COPY,
      reasoning: MISSING_ANGLE_UNAVAILABLE_COPY,
    };
  }
  return {
    body:
      ma.value != null && String(ma.value).trim()
        ? ma.value
        : "Missing-angle analysis is not available for this topic yet.",
    reasoning:
      ma.reasoning != null && String(ma.reasoning).trim()
        ? ma.reasoning
        : "No additional reasoning was returned by the backend.",
  };
}

function installGlobalErrorHandlers() {
  const show = (message, extra) => {
    const line = [message, extra].filter(Boolean).join("\n");
    let el = document.getElementById("newslens-boot-error");
    if (!el) {
      el = document.createElement("div");
      el.id = "newslens-boot-error";
      el.setAttribute("role", "alert");
      el.style.cssText = [
        "position:fixed",
        "left:0",
        "right:0",
        "bottom:0",
        "z-index:2147483646",
        "max-height:45vh",
        "overflow:auto",
        "padding:12px 16px",
        "font:13px/1.4 system-ui,Segoe UI,sans-serif",
        "color:#7f1d1d",
        "background:#fef2f2",
        "border-top:1px solid #fecaca",
        "white-space:pre-wrap",
        "word-break:break-word",
      ].join(";");
      document.body.appendChild(el);
    }
    el.textContent = `NewsLens — ${line}`;
  };
  window.addEventListener("error", (ev) => {
    const loc = ev.filename ? `${ev.filename}:${ev.lineno}:${ev.colno}` : "";
    show(ev.message || "Script error", loc);
  });
  window.addEventListener("unhandledrejection", (ev) => {
    const r = ev.reason;
    const msg =
      r && typeof r === "object" && r !== null && "message" in r ? String(r.message) : String(r);
    const stack = r && typeof r === "object" && r !== null && r.stack ? String(r.stack) : "";
    show(`Unhandled promise: ${msg}`, stack);
  });
}
installGlobalErrorHandlers();

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { err: null };
  }

  static getDerivedStateFromError(err) {
    return { err };
  }

  componentDidCatch(err, info) {
    console.error("[NewsLens] React render error", err, info?.componentStack);
  }

  render() {
    if (this.state.err) {
      return (
        <div className="card" style={{ margin: "1rem", padding: "1rem", borderColor: "#fecaca" }}>
          <h2 style={{ color: "#b91c1c", marginTop: 0 }}>Something went wrong rendering results</h2>
          <p style={{ whiteSpace: "pre-wrap", fontSize: 14 }}>
            {String(this.state.err?.message || this.state.err)}
          </p>
          <button type="button" className="search-btn" onClick={() => this.setState({ err: null })}>
            Try again
          </button>
          <button
            type="button"
            className="btn-exit-compare"
            style={{ marginLeft: 8 }}
            onClick={() => window.location.reload()}
          >
            Reload page
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

function firstSentence(text) {
  const t = String(text).trim();
  if (!t) return "";
  const idx = t.search(/[.!?](\s|$)/);
  if (idx === -1) return t;
  return t.slice(0, idx + 1).trim();
}

/** Prefer stored framing; never show the empty-state copy when the outlet has articles. */
function outletFramingBody(outlet) {
  const hasArticles = (outlet.article_count || 0) > 0;
  if (outlet.framing_summary) return outlet.framing_summary;
  if (outlet.missing_angle) return outlet.missing_angle;
  if (hasArticles && outlet.headline) return `Lead: ${outlet.headline}`;
  if (hasArticles) return "Topic coverage is available; framing snippet missing for this snapshot.";
  return "No framing summary available yet for this outlet.";
}

function normalizeOutlet(o) {
  if (!o || typeof o !== "object") {
    return {
      source: "Unknown",
      article_count: 0,
      avg_sentiment_score: null,
      avg_bias_score: null,
      sentiment_labels: {},
      bias_labels: {},
      dominant_sentiment_label: null,
      dominant_bias_label: null,
      missing_angle: null,
      framing_summary: null,
      headline: null,
    };
  }
  const sl =
    o.sentiment_labels && typeof o.sentiment_labels === "object" ? { ...o.sentiment_labels } : {};
  const bl = o.bias_labels && typeof o.bias_labels === "object" ? { ...o.bias_labels } : {};
  return {
    source: typeof o.source === "string" && o.source.trim() ? o.source.trim() : "Unknown",
    article_count:
      typeof o.article_count === "number" && Number.isFinite(o.article_count) ? o.article_count : 0,
    avg_sentiment_score:
      typeof o.avg_sentiment_score === "number" && Number.isFinite(o.avg_sentiment_score)
        ? o.avg_sentiment_score
        : null,
    avg_bias_score:
      typeof o.avg_bias_score === "number" && Number.isFinite(o.avg_bias_score)
        ? o.avg_bias_score
        : null,
    sentiment_labels: sl,
    bias_labels: bl,
    dominant_sentiment_label:
      o.dominant_sentiment_label == null ? null : String(o.dominant_sentiment_label),
    dominant_bias_label: o.dominant_bias_label == null ? null : String(o.dominant_bias_label),
    missing_angle:
      o.missing_angle == null || o.missing_angle === ""
        ? null
        : typeof o.missing_angle === "string"
          ? o.missing_angle
          : String(o.missing_angle),
    headline: o.headline == null ? null : String(o.headline),
    framing_summary:
      o.framing_summary == null || o.framing_summary === ""
        ? null
        : typeof o.framing_summary === "string"
          ? o.framing_summary
          : String(o.framing_summary),
  };
}

function normalizeMissingAngleBlock(ma) {
  if (!ma || typeof ma !== "object") {
    return {
      value: null,
      reasoning: "",
      confidence: null,
      from_cache: false,
      analysis_status: null,
      error: false,
      error_message: null,
    };
  }
  return {
    value:
      ma.value == null || ma.value === ""
        ? null
        : typeof ma.value === "string"
          ? ma.value
          : String(ma.value),
    reasoning:
      ma.reasoning == null ? "" : typeof ma.reasoning === "string" ? ma.reasoning : String(ma.reasoning),
    confidence: ma.confidence ?? null,
    from_cache: Boolean(ma.from_cache),
    analysis_status: ma.analysis_status == null ? null : String(ma.analysis_status),
    error: Boolean(ma.error),
    error_message: ma.error_message == null ? null : String(ma.error_message),
  };
}

function normalizeTimeline(rows) {
  if (!Array.isArray(rows)) return [];
  return rows.map((row) => {
    if (!row || typeof row !== "object") return { date: "" };
    const copy = { ...row };
    copy.date =
      typeof copy.date === "string" ? copy.date : copy.date != null ? String(copy.date) : "";
    return copy;
  });
}

function normalizeBiasDistribution(raw) {
  if (!raw || typeof raw !== "object") return null;
  const lp = Number(raw.left_pct);
  const cp = Number(raw.center_pct);
  const rp = Number(raw.right_pct);
  if (![lp, cp, rp].every((n) => Number.isFinite(n))) return null;
  return { left_pct: lp, center_pct: cp, right_pct: rp };
}

function normalizeAnalyzePayload(raw) {
  const d = raw && typeof raw === "object" ? raw : {};
  const outlets = Array.isArray(d.outlets) ? d.outlets.map(normalizeOutlet) : [];
  const fetch = d.fetch && typeof d.fetch === "object" ? d.fetch : {};
  const maBlock = normalizeMissingAngleBlock(d.missing_angle);
  const rootAnalysisStatus =
    d.analysis_status == null || d.analysis_status === ""
      ? null
      : String(d.analysis_status);
  const missing_angle = {
    ...maBlock,
    analysis_status: maBlock.analysis_status ?? rootAnalysisStatus,
  };
  return {
    topic: typeof d.topic === "string" ? d.topic : "",
    status: normalizeCoverageStatus(d.status),
    outlets,
    timeline: normalizeTimeline(d.timeline),
    missing_angle,
    fetch,
    coverage_message:
      typeof fetch.coverage_message === "string" && fetch.coverage_message.trim()
        ? fetch.coverage_message.trim()
        : null,
    scoring: d.scoring && typeof d.scoring === "object" ? d.scoring : {},
    bias_distribution: normalizeBiasDistribution(d.bias_distribution),
    most_left_outlet: d.most_left_outlet == null ? null : String(d.most_left_outlet),
    most_right_outlet: d.most_right_outlet == null ? null : String(d.most_right_outlet),
    selected_outlets: Array.isArray(d.selected_outlets) ? d.selected_outlets.map(String) : [],
  };
}

function sentimentBucket(labels, keys) {
  const L = labels && typeof labels === "object" ? labels : {};
  for (const k of keys) {
    if (Object.prototype.hasOwnProperty.call(L, k) && L[k] != null) {
      const n = Number(L[k]);
      return Number.isFinite(n) ? n : 0;
    }
  }
  return 0;
}

const OUTLET_COLORS = {
  CNN: "#3B82F6",
  Reuters: "#9CA3AF",
  "Fox News": "#EF4444",
  "BBC News": "#0EA5E9",
  "Associated Press": "#64748B",
};

/** Align with backend bias_utils: map model labels to left / center / right buckets. */
function biasSpectrumBucket(label) {
  const s = String(label ?? "")
    .trim()
    .toLowerCase();
  if (!s) return "center";
  const leftHints = ["left", "liberal", "progressive", "socialist", "democrat"];
  const rightHints = ["right", "conservative", "republican", "nationalist", "populist"];
  if (leftHints.some((h) => s.includes(h))) return "left";
  if (rightHints.some((h) => s.includes(h))) return "right";
  return "center";
}

const biasBadgeClass = (label) => {
  const bucket = biasSpectrumBucket(label);
  if (bucket === "left") return "badge blue-bg";
  if (bucket === "right") return "badge red-bg";
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
  const next = [
    topic,
    ...readHistory().filter(
      (item) => String(item ?? "").toLowerCase() !== topic.toLowerCase()
    ),
  ].slice(0, 5);
  localStorage.setItem(HISTORY_KEY, JSON.stringify(next));
}

function spectrumSegmentWidths(biasDistribution, outlets) {
  const fromApi = normalizeBiasDistribution(biasDistribution);
  if (fromApi) {
    const sum = fromApi.left_pct + fromApi.center_pct + fromApi.right_pct;
    if (sum > 0) {
      return {
        left: fromApi.left_pct / sum,
        center: fromApi.center_pct / sum,
        right: fromApi.right_pct / sum,
      };
    }
  }
  const list = Array.isArray(outlets) ? outlets : [];
  let nl = 0;
  let nc = 0;
  let nr = 0;
  for (const o of list) {
    if ((o.article_count || 0) <= 0) continue;
    const b = biasSpectrumBucket(o.dominant_bias_label);
    if (b === "left") nl += 1;
    else if (b === "right") nr += 1;
    else nc += 1;
  }
  const t = nl + nc + nr;
  if (t === 0) return { left: 1 / 3, center: 1 / 3, right: 1 / 3 };
  return { left: nl / t, center: nc / t, right: nr / t };
}

function assignSpectrumLanes(markers, minGapPct) {
  const clamp = (n, a, b) => Math.min(b, Math.max(a, n));
  const sorted = [...markers].sort((a, b) => a.score - b.score);
  const laneLastX = [];
  const maxLane = 12;
  return sorted.map((m) => {
    // Visual multiplier so small differences don't visually collapse near center.
    const s = clamp(m.score, -1, 1);
    const visualPosition = (s * 1.5 * 50) + 50;
    const x = clamp(visualPosition, 5, 95);
    let L = 0;
    while (L < maxLane - 1 && laneLastX[L] !== undefined && x - laneLastX[L] < minGapPct) {
      L += 1;
    }
    laneLastX[L] = x;
    return { ...m, lane: L, xPct: x };
  });
}

function outletMarkerColor(outlet) {
  const bucket = biasSpectrumBucket(outlet.dominant_bias_label);
  if (bucket === "left") return "#3B82F6";
  if (bucket === "right") return "#EF4444";
  return "#6B7280";
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
  console.log("[NewsLens] /analyze raw response:", payload);
  if (!payload.success) {
    throw new Error(payload.error || "Backend returned an unsuccessful response.");
  }
  return normalizeAnalyzePayload(payload.data);
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

/** Bias mix percentages come from the API only (/analyze bias_distribution). */
function computeBiasDistribution(apiDist) {
  const fromApi = normalizeBiasDistribution(apiDist);
  if (fromApi) {
    const lp = fromApi.left_pct;
    const cp = fromApi.center_pct;
    const rp = fromApi.right_pct;
    return {
      left: lp,
      center: cp,
      right: rp,
      text: `${lp}% left, ${cp}% center, ${rp}% right`,
    };
  }
  return {
    left: 0,
    center: 0,
    right: 0,
    text: "Bias mix unavailable.",
  };
}

function extremOutlets(outlets) {
  const list = Array.isArray(outlets) ? outlets : [];
  const withBias = list.filter((o) => (o.article_count || 0) > 0 && typeof o.avg_bias_score === "number");
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

function BiasSpectrum({ outlets, biasDistribution, articlesAnalyzed, spectrumExtremes, isFetching }) {
  const list = Array.isArray(outlets) ? outlets : [];
  const widths = useMemo(
    () => spectrumSegmentWidths(biasDistribution, list),
    [biasDistribution, list]
  );
  const extremes = useMemo(() => {
    const fb = extremOutlets(list);
    const ml = spectrumExtremes?.most_left_outlet;
    const mr = spectrumExtremes?.most_right_outlet;
    return {
      left: ml != null && String(ml).trim() !== "" ? String(ml).trim() : fb.left || "—",
      right: mr != null && String(mr).trim() !== "" ? String(mr).trim() : fb.right || "—",
    };
  }, [spectrumExtremes, list]);

  const articleTotal = useMemo(() => {
    if (typeof articlesAnalyzed === "number" && Number.isFinite(articlesAnalyzed)) {
      return articlesAnalyzed;
    }
    return list.reduce((sum, o) => sum + (o.article_count || 0), 0);
  }, [articlesAnalyzed, list]);

  const placedMarkers = useMemo(() => {
    const raw = list.filter(
      (o) =>
        (o.article_count || 0) > 0 &&
        typeof o.avg_bias_score === "number" &&
        Number.isFinite(o.avg_bias_score)
    );
    const scored = raw.map((o) => ({
      outlet: o,
      score: Math.max(-1, Math.min(1, o.avg_bias_score)),
    }));
    return assignSpectrumLanes(scored, 4);
  }, [list]);

  const stripHeight = useMemo(() => {
    const maxLane = placedMarkers.reduce((m, x) => Math.max(m, x.lane), -1);
    const lanes = maxLane < 0 ? 0 : maxLane + 1;
    return 36 + lanes * 15;
  }, [placedMarkers]);

  return (
    <section id="dashboard" className="bias-hero card">
      <div className="section-head">
        <h2>Outlet marker spectrum</h2>
        <span>Each outlet positioned by average bias score for this topic</span>
      </div>
      <div className="outlet-spectrum-visual">
        <div
          className={`spectrum-gradient-bar${isFetching ? " spectrum-bar-shimmer" : ""}`}
          aria-hidden="true"
        >
          <div className="spectrum-segment spectrum-segment-left" style={{ flex: widths.left }} />
          <div
            className="spectrum-segment spectrum-segment-center"
            style={{ flex: widths.center }}
          />
          <div
            className="spectrum-segment spectrum-segment-right"
            style={{ flex: widths.right }}
          />
        </div>
        <div
          className="spectrum-marker-strip"
          style={{ minHeight: `${stripHeight}px` }}
          role="presentation"
        >
          {placedMarkers.map(({ outlet, score, lane, xPct }) => (
            <div
              key={outlet.source}
              className="spectrum-marker"
              style={{ left: `${xPct}%`, top: 4 + lane * 15 }}
              title={`${outlet.source} — bias score ${score.toFixed(3)}`}
            >
              <span
                className="spectrum-marker-dot"
                style={{ background: outletMarkerColor(outlet) }}
              />
              <span className="spectrum-marker-name">{outlet.source}</span>
            </div>
          ))}
        </div>
        <div className="spectrum-axis-labels">
          <span className="spectrum-axis-extreme spectrum-axis-left">{extremes.left}</span>
          <span className="spectrum-axis-mid">center</span>
          <span className="spectrum-axis-extreme spectrum-axis-right">{extremes.right}</span>
        </div>
        <p className="spectrum-articles-footnote">
          Positions based on {articleTotal} article{articleTotal === 1 ? "" : "s"} analyzed
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

const SENTIMENT_COLORS = {
  positive: "#10b981",
  neutral: "#94a3b8",
  negative: "#ef4444",
};

function sentimentKeyFromLabel(label) {
  const s = String(label ?? "").trim().toLowerCase();
  if (s.includes("pos")) return "positive";
  if (s.includes("neg")) return "negative";
  if (s.includes("neu")) return "neutral";
  return "neutral";
}

function sentimentBadgeClass(label) {
  const k = sentimentKeyFromLabel(label);
  if (k === "positive") return "badge sentiment-badge sentiment-positive";
  if (k === "negative") return "badge sentiment-badge sentiment-negative";
  return "badge sentiment-badge sentiment-neutral";
}

const OUTLET_METHODOLOGY = {
  Reuters: "International news organization focused on objective, fact-based reporting.",
  "Associated Press": "Global news cooperative emphasizing straight reporting and verified facts.",
  "BBC News": "Public-service broadcaster centered on impartial reporting and global context.",
  Bloomberg: "Business-first newsroom prioritizing markets, data, and financial context.",
  "The Wall Street Journal": "Business-focused reporting with emphasis on markets and economic policy.",
  "The New York Times": "Broad national and global reporting with investigative and explanatory depth.",
  "The Washington Post": "National political reporting and investigations with a focus on public accountability.",
  CNN: "Breaking-news driven coverage emphasizing speed, context, and live reporting.",
  MSNBC: "Opinion-forward cable coverage with progressive-leaning commentary and analysis.",
  "Fox News": "Opinion-forward cable coverage with conservative-leaning commentary and analysis.",
  NPR: "Public media reporting with an explanatory style and emphasis on on-the-ground sourcing.",
  "ABC News": "Broadcast newsroom focused on national headlines, breaking events, and explanatory segments.",
  "CBS News": "Broadcast newsroom focused on national headlines, breaking events, and explanatory segments.",
  "NBC News": "Broadcast newsroom focused on national headlines, breaking events, and explanatory segments.",
  "The Guardian": "International reporting with an explanatory style and strong editorial voice.",
};

function SourceProfileSection({ outletName }) {
  const [open, setOpen] = useState(false);
  const q = useQuery({
    queryKey: ["outlet-profile", outletName],
    queryFn: () => fetchOutletProfile(outletName),
    enabled: open,
    staleTime: 60_000,
  });
  const methodology = OUTLET_METHODOLOGY[outletName] || `${outletName}: Methodology summary unavailable.`;

  return (
    <div className="source-profile-wrap">
      <button
        type="button"
        className="source-profile-toggle"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <span className="source-profile-toggle-left">
          <span className="source-profile-info-icon" aria-hidden>
            i
          </span>
          <span>Source Profile</span>
        </span>
        <span className="chevron" aria-hidden>
          {open ? "▼" : "▶"}
        </span>
      </button>
      <div className={`source-profile-body${open ? " open" : ""}`}>
        <p className="source-profile-methodology">{methodology}</p>
        {open ? (
          <>
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
          </>
        ) : null}
      </div>
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
      <div className="outlet-badges">
        <p className={biasBadgeClass(outlet.dominant_bias_label)}>
          {outlet.dominant_bias_label || "No bias label"}
        </p>
        <p className={sentimentBadgeClass(outlet.dominant_sentiment_label)}>
          {outlet.dominant_sentiment_label || "Neutral"}
        </p>
      </div>
      <p className="body">{outletFramingBody(outlet)}</p>
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
  const list = Array.isArray(outlets) ? outlets : [];
  return (
    <section id="outlets" className="outlets-grid">
      {list.map((outlet) => (
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
  const list = Array.isArray(outlets) ? outlets : [];
  return (
    <section id="topics" className="card headlines">
      <div className="section-head">
        <h2>Headline Comparison</h2>
        <span>Same topic, different framing</span>
      </div>
      <div className="headline-grid">
        {list.map((outlet) => (
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
  const data = useMemo(() => {
    const rows = (outlets || [])
      .filter((outlet) => (outlet.article_count || 0) > 0)
      .map((outlet) => {
        const positive = sentimentBucket(outlet.sentiment_labels, ["Positive", "positive"]);
        const neutral = sentimentBucket(outlet.sentiment_labels, ["Neutral", "neutral"]);
        const negative = sentimentBucket(outlet.sentiment_labels, ["Negative", "negative"]);
        return {
          outlet: outlet.source || "Unknown",
          positive,
          neutral,
          negative,
          totalValue: positive + neutral + negative,
        };
      });
    return rows.filter((series) => series.totalValue > 0).map(({ totalValue, ...row }) => row);
  }, [outlets]);

  return (
    <section className="card chart-card">
      <div className="section-head">
        <h2>Sentiment Distribution</h2>
        <span>Positive / neutral / negative by outlet</span>
      </div>
      <div className="chart-wrap">
        <ResponsiveContainer width="100%" height="100%" minHeight={CHART_MIN_HEIGHT}>
          <BarChart
            data={data}
            margin={CHART_MARGIN_SENTIMENT}
            barCategoryGap="24%"
            barSize={40}
            stackOffset="sign"
          >
            <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
            <XAxis
              dataKey="outlet"
              angle={-45}
              textAnchor="end"
              height={60}
              interval="preserveStartEnd"
              minTickGap={40}
              stroke="#888"
              tick={CHART_AXIS_TICK}
            />
            <YAxis stroke="#888" tick={CHART_AXIS_TICK} allowDecimals={false} />
            <Tooltip labelFormatter={chartTooltipLabelFormatter} />
            <Legend
              iconType="circle"
              verticalAlign="bottom"
              align="center"
              layout="horizontal"
              wrapperStyle={CHART_LEGEND_WRAPPER}
            />
            <Bar
              dataKey="positive"
              stackId="sentiment"
              fill={SENTIMENT_COLORS.positive}
              radius={[4, 4, 0, 0]}
            />
            <Bar dataKey="neutral" stackId="sentiment" fill={SENTIMENT_COLORS.neutral} radius={[0, 0, 0, 0]} />
            <Bar
              dataKey="negative"
              stackId="sentiment"
              fill={SENTIMENT_COLORS.negative}
              radius={[0, 0, 4, 4]}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

function Timeline({ timeline, outlets }) {
  const rows = Array.isArray(timeline) ? timeline : [];
  const lineSources = useMemo(() => {
    const sources = (outlets || [])
      .filter((o) => (o.article_count || 0) > 0)
      .map((o) => o.source);
    return outletKeysWithPositiveTotals(rows, sources, "bias");
  }, [rows, outlets]);
  const timelineEmpty = useMemo(() => isTimelineBiasDatasetEmpty(rows, outlets), [rows, outlets]);
  const partialMeta = useMemo(() => getChartHistoryPartialMeta(rows, outlets, "timeline"), [rows, outlets]);

  if (timelineEmpty) {
    return (
      <section className="card chart-card">
        <div className="section-head">
          <h2>Narrative Timeline</h2>
          <span>Bias score trend over the last 7 days</span>
        </div>
        <div className="chart-wrap">
          <ChartHistoryBuildingEmptyState />
        </div>
      </section>
    );
  }
  return (
    <section className="card chart-card">
      <div className="section-head">
        <h2>Narrative Timeline</h2>
        <span>Bias score trend over the last 7 days</span>
      </div>
      <div className="chart-wrap">
        <ResponsiveContainer width="100%" height="100%" minHeight={CHART_MIN_HEIGHT}>
          <LineChart data={rows} margin={CHART_MARGIN_LINE_AREA}>
            <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
            <XAxis
              dataKey="date"
              angle={-45}
              textAnchor="end"
              height={60}
              interval="preserveStartEnd"
              minTickGap={40}
              stroke="#888"
              tickFormatter={formatChartAxisDate}
              tick={CHART_AXIS_TICK}
            />
            <YAxis domain={[-1, 1]} stroke="#888" tick={CHART_AXIS_TICK} />
            <Tooltip labelFormatter={chartTooltipLabelFormatter} />
            <Legend
              iconType="circle"
              verticalAlign="bottom"
              align="center"
              layout="horizontal"
              wrapperStyle={CHART_LEGEND_WRAPPER}
            />
            {lineSources.map((source) => (
              <Line
                key={source}
                type="monotone"
                dataKey={source}
                stroke={OUTLET_COLORS[source] || "#111827"}
                strokeWidth={2.2}
                dot={{ r: 3 }}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      {partialMeta.show ? <ChartHistoryPartialHint x={partialMeta.x} /> : null}
    </section>
  );
}

function TopicTrendChart({ topic, outlets }) {
  const q = useQuery({
    queryKey: ["topic-trend", topic, 7],
    queryFn: () => fetchTopicTrend(topic, 7),
    enabled: Boolean(topic),
    staleTime: 30_000,
  });

  const series = q.data?.series || [];
  const areaSources = useMemo(() => {
    const list = Array.isArray(outlets) ? outlets : [];
    const sources = list.filter((o) => (o.article_count || 0) > 0).map((o) => o.source);
    return outletKeysWithPositiveTotals(series, sources, "volume");
  }, [series, outlets]);
  const coverageEmpty = useMemo(
    () => isCoverageVolumeDatasetEmpty(series, outlets),
    [series, outlets]
  );
  const partialMeta = useMemo(
    () => getChartHistoryPartialMeta(series, outlets, "coverage"),
    [series, outlets]
  );

  return (
    <section className="card chart-card">
      <div className="section-head">
        <h2>Topic coverage by outlet</h2>
        <span>Article volume per outlet over the last 7 days</span>
      </div>
      {q.isLoading ? <p className="chart-status">Loading trend data…</p> : null}
      {q.isError ? <p className="chart-status error">Could not load trend: {q.error?.message}</p> : null}
      {q.isSuccess ? (
        coverageEmpty ? (
          <div className="chart-wrap">
            <ChartHistoryBuildingEmptyState />
          </div>
        ) : (
          <>
            <div className="chart-wrap">
              <ResponsiveContainer width="100%" height="100%" minHeight={CHART_MIN_HEIGHT}>
                <AreaChart data={series} margin={CHART_MARGIN_LINE_AREA}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
                  <XAxis
                    dataKey="date"
                    angle={-45}
                    textAnchor="end"
                    height={60}
                    interval="preserveStartEnd"
                    minTickGap={40}
                    stroke="#888"
                    tickFormatter={formatChartAxisDate}
                    tick={CHART_AXIS_TICK}
                  />
                  <YAxis allowDecimals={false} stroke="#888" tick={CHART_AXIS_TICK} />
                  <Tooltip labelFormatter={chartTooltipLabelFormatter} />
                  <Legend
                    iconType="circle"
                    verticalAlign="bottom"
                    align="center"
                    layout="horizontal"
                    wrapperStyle={CHART_LEGEND_WRAPPER}
                  />
                  {areaSources.map((source) => (
                    <Area
                      key={source}
                      type="monotone"
                      dataKey={source}
                      stackId="topic-volume"
                      stroke={OUTLET_COLORS[source] || "#111827"}
                      fill={OUTLET_COLORS[source] || "#111827"}
                      fillOpacity={0.55}
                    />
                  ))}
                </AreaChart>
              </ResponsiveContainer>
            </div>
            {partialMeta.show ? <ChartHistoryPartialHint x={partialMeta.x} /> : null}
          </>
        )
      ) : null}
    </section>
  );
}

function MissingAngleCard({ missingAngle }) {
  const { body, reasoning } = missingAnglePresentationalCopy(missingAngle);
  return (
    <section id="methodology" className="missing-angle card">
      <p className="eyebrow">Editorial insight</p>
      <h2>Missing Angle</h2>
      <p>{body}</p>
      <div className="reasoning-box">
        <h4>Reasoning</h4>
        <p>{reasoning}</p>
      </div>
    </section>
  );
}

function DevelopingStoryBanner() {
  return (
    <div className="developing-story-banner" role="status">
      <span className="developing-pulse-icon" aria-hidden>
        <span className="developing-pulse-dot" />
        <span className="developing-pulse-ring" />
      </span>
      <p className="developing-story-copy">
        <strong>Developing Story:</strong> This topic has emerging coverage. Analysis will refine as more sources
        report.
      </p>
    </div>
  );
}

function InsufficientCoverageCard({ onTryBroaderSearch }) {
  return (
    <section className="card insufficient-coverage-card" aria-labelledby="insufficient-coverage-heading">
      <p className="eyebrow">Coverage</p>
      <h2 id="insufficient-coverage-heading" className="insufficient-coverage-title">
        Not enough coverage
      </h2>
      <p className="insufficient-coverage-body">
        Few articles matched this topic in our current window. Try a shorter or broader query to surface more
        outlets.
      </p>
      <button type="button" className="btn-broader-search" onClick={onTryBroaderSearch}>
        Try a broader search
      </button>
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

function ResultsHeader({
  topic,
  outlets,
  biasDistribution,
  spectrumExtremes,
  missingAngle,
  shareCardRef,
  onShare,
  shareBusy,
  shareError,
}) {
  const dist = useMemo(() => computeBiasDistribution(biasDistribution), [biasDistribution]);
  const ex = useMemo(() => {
    const fb = extremOutlets(outlets);
    const ml = spectrumExtremes?.most_left_outlet;
    const mr = spectrumExtremes?.most_right_outlet;
    return {
      left: ml != null && String(ml).trim() !== "" ? ml : fb.left || "—",
      right: mr != null && String(mr).trim() !== "" ? mr : fb.right || "—",
    };
  }, [spectrumExtremes, outlets]);
  const teaser = useMemo(() => {
    if (String(missingAngle?.analysis_status ?? "").toLowerCase() === "quota_limited") {
      return MISSING_ANGLE_SEARCH_AGAIN_SHORTLY;
    }
    if (missingAngleShouldShowQuotaWaitMessage(missingAngle)) {
      return MISSING_ANGLE_SEARCH_AGAIN_SHORTLY;
    }
    if (missingAngleIsUnavailableUserFacing(missingAngle)) {
      return MISSING_ANGLE_UNAVAILABLE_COPY;
    }
    const v = missingAngle?.value;
    if (!v || typeof v !== "string") return "Perspective gaps may appear as more outlets publish.";
    const one = firstSentence(v) || v;
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

function AnalysisResults({
  data,
  compareSelection,
  onCompareClick,
  onExitComparison,
  shareCardRef,
  onShare,
  shareBusy,
  shareError,
  spectrumFetching,
  onTryBroaderSearch,
}) {
  const outlets = Array.isArray(data?.outlets) ? data.outlets : [];
  const timeline = Array.isArray(data?.timeline) ? data.timeline : [];
  const comparing = compareSelection.length === 2;
  const coverageShortfall =
    outlets.length === 0 && data?.coverage_message ? String(data.coverage_message) : "";
  const status = data?.status || COVERAGE_STATUS.HIGH;

  return (
    <main className="results-stack">
      {coverageShortfall ? (
        <section className="card coverage-shortfall" role="status">
          <p className="eyebrow">Coverage</p>
          <p className="coverage-shortfall-msg">{coverageShortfall}</p>
        </section>
      ) : null}
      <ResultsHeader
        topic={data.topic || ""}
        outlets={outlets}
        biasDistribution={data.bias_distribution}
        spectrumExtremes={{
          most_left_outlet: data.most_left_outlet,
          most_right_outlet: data.most_right_outlet,
        }}
        missingAngle={data.missing_angle}
        shareCardRef={shareCardRef}
        onShare={onShare}
        shareBusy={shareBusy}
        shareError={shareError}
      />
      {status === COVERAGE_STATUS.DEVELOPING ? <DevelopingStoryBanner /> : null}
      {status === COVERAGE_STATUS.INSUFFICIENT ? (
        <InsufficientCoverageCard onTryBroaderSearch={onTryBroaderSearch} />
      ) : null}
      {comparing ? (
        <ComparisonPanel pair={compareSelection} outlets={outlets} onExit={onExitComparison} />
      ) : null}
      <BiasSpectrum
        outlets={outlets}
        biasDistribution={data.bias_distribution}
        articlesAnalyzed={
          data.scoring && typeof data.scoring.article_count === "number"
            ? data.scoring.article_count
            : null
        }
        spectrumExtremes={{
          most_left_outlet: data.most_left_outlet,
          most_right_outlet: data.most_right_outlet,
        }}
        isFetching={spectrumFetching}
      />
      <OutletGrid outlets={outlets} compareSelection={compareSelection} onCompareClick={onCompareClick} />
      <HeadlineComparison outlets={outlets} />
      <div className="chart-grid">
        <SentimentDistribution outlets={outlets} />
        <div className="timeline-column">
          <Timeline timeline={timeline} outlets={outlets} />
          <TopicTrendChart topic={data.topic || ""} outlets={outlets} />
        </div>
      </div>
      <MissingAngleCard missingAngle={data.missing_angle} />
    </main>
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
    <section className="hero" id="search-anchor">
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
      <div className="suggested-topics">
        <p className="suggested-topics-label">Suggested topics</p>
        <div className="suggested-topics-row">
          {SUGGESTED_TOPICS.map((label) => (
            <button
              key={label}
              type="button"
              className="suggestion-tag"
              onClick={() => runSearch(label)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      {isError ? (
        <p className="inline-error">
          Could not load analysis: {error?.message != null ? String(error.message) : String(error)}
        </p>
      ) : null}
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

  const focusSearchArea = () => {
    document.getElementById("search-anchor")?.scrollIntoView({ behavior: "smooth", block: "center" });
    window.requestAnimationFrame(() => {
      setTimeout(() => {
        const el = searchRef.current;
        el?.focus();
        el?.select?.();
      }, 320);
    });
  };

  const handleStartAnalysis = () => {
    focusSearchArea();
  };

  const handleTryBroaderSearch = () => {
    focusSearchArea();
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
        <ErrorBoundary key={topic}>
          <AnalysisResults
            data={data}
            compareSelection={compareSelection}
            onCompareClick={handleCompareClick}
            onExitComparison={exitComparison}
            shareCardRef={shareCardRef}
            onShare={handleShare}
            shareBusy={shareBusy}
            shareError={shareError}
            spectrumFetching={query.isFetching}
            onTryBroaderSearch={handleTryBroaderSearch}
          />
        </ErrorBoundary>
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
