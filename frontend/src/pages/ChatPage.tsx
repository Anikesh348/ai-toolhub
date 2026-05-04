import { ChangeEvent, ClipboardEvent as ReactClipboardEvent, FormEvent, PointerEvent as ReactPointerEvent, WheelEvent as ReactWheelEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import {
  API_BASE_URL,
  ChatMessage,
  ChatMode,
  ChatSession,
  ChatStreamEvent,
  createChatSession,
  fetchChatModels,
  fetchChatMessages,
  fetchChatSessions,
  fetchYouTubeShortsFeed,
  formatDate,
  stopChatMessageStream,
  streamChatMessage,
  uploadChatAttachment,
  updateChatSession,
  type YouTubeShortFeedItem
} from "@/lib/api";
import ChatMarkdown from "@/components/ChatMarkdown";
import {
  loadThinkingPanelMode,
  saveThinkingPanelMode,
  ThinkingPanelMode
} from "@/lib/chat-thinking-panel";

const MODE_LABELS: Record<ChatMode, string> = {
  general: "General",
  tool_builder: "Tool Builder",
  operator: "Operator",
  pi_operator: "Operator"
};

type SelectableMode = Exclude<ChatMode, "pi_operator">;
type StreamActivity = "ready" | "thinking" | "web_search";
type MessageAttachment = {
  id: string;
  fileName: string;
  contentType: string;
  size: number;
  url: string;
};
type KnowledgeArticle = {
  title: string;
  company: string;
  feature: string;
  readTime: string;
  sourceUrl: string;
  content: string[];
};
type YouTubeShort = YouTubeShortFeedItem;
type SelectableThinkingPanelMode = Exclude<ThinkingPanelMode, "none">;
type ToolBuilderPromptExample = {
  id: string;
  title: string;
  summary: string;
  tags: string[];
  prompt: string;
};
type ToolBuilderIntakeState = {
  toolName: string;
  details: string;
  workflow: string;
  integrations: string;
  constraints: string;
  requiresFrontend: boolean;
  requiresBackend: boolean;
  requiresMongo: boolean;
  requiresCron: boolean;
};
type ToolBuilderBooleanField = "requiresFrontend" | "requiresBackend" | "requiresMongo" | "requiresCron";
type PendingImageAttachment = { file: File; previewUrl: string };

const MODE_OPTIONS: Array<{ value: SelectableMode; label: string }> = [
  { value: "general", label: "General" },
  { value: "tool_builder", label: "Tool Builder" },
  { value: "operator", label: "Operator" }
];
const TECHNICAL_READING_WPM = 120;
const MIN_KNOWLEDGE_ARTICLE_MINUTES = 10;
const CHAT_COMPOSER_MAX_WIDTH_CLASS = "max-w-5xl";
const CHAT_INPUT_MAX_HEIGHT_PX = 240;
const MAX_CHAT_IMAGE_ATTACHMENTS = 5;

const KNOWLEDGE_ARTICLES: KnowledgeArticle[] = [
  {
    title: "Dynamo-Style Storage: Always Write, Reconcile Later",
    company: "Amazon Dynamo",
    feature: "Leaderless replication, quorums, and read repair",
    readTime: "14 min read",
    sourceUrl: "https://www.allthingsdistributed.com/files/amazon-dynamo-sosp2007.pdf",
    content: [
      "Dynamo is a strong example of building for availability first: writes should usually succeed even during partial failures, and consistency can be repaired afterward.",
      "Data is partitioned by consistent hashing so ownership can move gradually as nodes are added or removed. This keeps rebalancing bounded instead of creating full-cluster reshuffles.",
      "Reads and writes use tunable quorums. Teams can select W and R values to shift behavior toward lower latency, stronger consistency, or better failure tolerance depending on the workload.",
      "Because replicas can diverge, version reconciliation is part of the contract. Vector clocks and merge strategies make conflicts explicit instead of pretending they do not exist.",
      "Hinted handoff and anti-entropy are operationally critical. Without background repair, the system quietly accumulates inconsistency debt that surfaces later as correctness bugs.",
      "The senior-level lesson is not 'eventual consistency is easy.' The hard part is designing domain-safe merge rules and observability so divergence is measurable, bounded, and recoverable.",
      "When this pattern fits: high write volume, regional instability, and product surfaces where short-lived inconsistency is acceptable if availability remains high."
    ]
  },
  {
    title: "Spanner-Style Global Transactions Without Giving Up SQL",
    company: "Google Spanner",
    feature: "Externally consistent transactions across regions",
    readTime: "15 min read",
    sourceUrl: "https://research.google/pubs/pub39966/",
    content: [
      "Spanner demonstrates how to preserve transactional semantics at global scale by making time a first-class systems primitive.",
      "The architecture combines synchronous replication with shard leadership and consensus-backed logs. This provides clear write ordering even with regional replication.",
      "TrueTime bounds clock uncertainty so commit protocols can reason about causality. The design avoids hand-wavy timestamp assumptions that often break under skew.",
      "Schema and query design still matter. Global consistency does not remove hotspot risk, lock contention, or poor index locality; it only gives stronger correctness foundations.",
      "A practical takeaway for experienced engineers: evaluate where you need strict serializable behavior and where local eventual models are enough, then isolate the boundaries carefully.",
      "Operationally, multi-region transactions require tight SLO discipline. Tail latencies, failover choreography, and backpressure policies become part of correctness, not just performance.",
      "Use this pattern for money, inventory, and entitlement domains where reconciliation after the fact is expensive or unacceptable."
    ]
  },
  {
    title: "Exactly-Once Effects in Event-Driven Systems",
    company: "Kafka + Service Architecture",
    feature: "Transactional outbox and idempotent consumers",
    readTime: "13 min read",
    sourceUrl: "https://microservices.io/patterns/data/transactional-outbox.html",
    content: [
      "The classic failure case in microservices is dual write: commit to a DB, then fail before publishing the event. This creates permanent state drift between services.",
      "Transactional outbox solves this by writing business state and event intent in one local transaction. A relay process then publishes from the outbox with retries.",
      "At-least-once delivery remains the practical baseline, so consumers must be idempotent. Store processed event keys and make handlers safe for replay.",
      "Ordering guarantees are scope-dependent. Per-aggregate ordering is usually sufficient and far cheaper than global ordering across all entities.",
      "Compensation workflows should be explicit. If a downstream side effect fails, use saga compensation steps with auditable state transitions instead of hidden rollbacks.",
      "The advanced insight is to treat 'exactly once' as an end-to-end property, not a broker feature. Correctness requires cooperating storage, transport, and handler design.",
      "This article is especially useful when moving from synchronous request chains to asynchronous domain events at scale."
    ]
  },
  {
    title: "Cache Consistency Under Write Pressure",
    company: "Meta / Large-Scale Caching",
    feature: "Invalidation ordering and anti-stampede controls",
    readTime: "12 min read",
    sourceUrl: "https://engineering.fb.com/2022/06/08/core-infra/cache-made-consistent/",
    content: [
      "Caching stops being simple once writes are frequent and correctness matters. The hard part is guaranteeing clients never observe stale data in critical windows.",
      "Invalidate-before-write is not enough when delivery is asynchronous or reordered. Sequence tokens or version checks are needed to reject older updates.",
      "Hot keys can melt a backend during cache miss storms. Single-flight requests, jittered TTLs, and stale-while-revalidate are practical protections.",
      "Negative caching and partial object caching reduce repeated expensive misses. Teams often ignore these and pay for it during incidents.",
      "Consistency strategy should follow data semantics. Product metadata may allow seconds of staleness; permission checks usually do not.",
      "Design reviews should always include cache failure modes: what happens when invalidation queues lag, cache nodes reboot, or regional links flap?",
      "This read helps experienced engineers reason about cache correctness as a distributed systems problem, not only as a latency optimization."
    ]
  },
  {
    title: "Load Shedding and Graceful Degradation by SLO Tier",
    company: "Google SRE",
    feature: "Overload control for p99 stability",
    readTime: "11 min read",
    sourceUrl: "https://sre.google/sre-book/handling-overload/",
    content: [
      "Healthy systems preserve core user journeys first during overload. Everything else should degrade in a planned order, not by accident.",
      "Admission control belongs at multiple layers: edge, service, and dependency boundaries. Rejecting early is cheaper than timing out deep in the call graph.",
      "Queue depth, concurrency limits, and timeout budgets should be aligned. Mismatched limits create retry storms that amplify outages.",
      "Brownout modes are underrated. Turning off expensive non-essential features can protect core API latency and error budgets during incidents.",
      "Backpressure must be observable. Without per-hop visibility, teams cannot see where work is accumulating until user-facing latency collapses.",
      "The mature engineering posture is to predefine overload playbooks with objective triggers, so operators are not improvising under pressure.",
      "This read is a strong bridge between architecture diagrams and real on-call resilience."
    ]
  },
  {
    title: "Event Streaming as the System Record of Change",
    company: "Confluent / Kafka",
    feature: "Transactional streams, replay, and materialized views",
    readTime: "13 min read",
    sourceUrl: "https://www.confluent.io/blog/transactional-systems-with-apache-kafka/",
    content: [
      "Event logs let teams reconstruct state transitions over time, which is powerful for auditing, debugging, and backfilling derived systems.",
      "Partitioning strategy is a business decision disguised as infrastructure. Key choice defines parallelism, ordering guarantees, and hot-spot risk.",
      "Exactly-once stream processing is achievable only when producers, brokers, and consumers cooperate with transactional semantics and idempotent writes.",
      "Materialized views reduce query latency but introduce lag windows. Consumers should surface freshness metadata so product behavior is explicit to users.",
      "Schema governance is non-negotiable at scale. Backward/forward compatibility rules prevent one team from breaking downstream consumers silently.",
      "Replay is both a feature and a risk. It enables recovery, but can trigger duplicate side effects unless handlers are carefully designed.",
      "For engineers past the basics, this topic sharpens trade-off thinking across correctness, operability, and team autonomy."
    ]
  }
].map((article) => {
  const extendedContent = [...article.content, ...buildEngineeringDeepDive(article)];
  return {
    ...article,
    content: extendedContent,
    readTime: estimateReadTime(extendedContent)
  };
});
const YOUTUBE_SHORTS: YouTubeShort[] = [
  {
    id: "y72ZjofvLLo",
    title: "Place On Earth That Doesn't Feel Real",
    channel: "relax vibe",
    category: "Nature"
  },
  {
    id: "G9NRzrx7m4U",
    title: "Switzerland 4K",
    channel: "Smart info",
    category: "Nature"
  },
  {
    id: "86N5GBzlClU",
    title: "Beautiful View",
    channel: "BeautyVibe",
    category: "Nature"
  },
  {
    id: "8g_fxFoptOk",
    title: "Train Food In India",
    channel: "Adrija Roy",
    category: "Travel"
  },
  {
    id: "6kstnFaD9OI",
    title: "Crazy Gadgets",
    channel: "Tech Master Shorts",
    category: "Tech"
  },
  {
    id: "iuLgq7bf6QY",
    title: "New Innovation",
    channel: "Your Mahbub",
    category: "Tech"
  },
  {
    id: "Tu-uNTx5-7A",
    title: "Matka Maggi Street Food",
    channel: "Indori Foodist",
    category: "Food"
  },
  {
    id: "tDEM3P7eRRg",
    title: "Would You Eat This?",
    channel: "Zach Choi",
    category: "Food"
  },
  {
    id: "FpzNVwM5N9U",
    title: "Super Cars In India",
    channel: "Karthik Gilly",
    category: "City"
  },
  {
    id: "M1nuGREjhKw",
    title: "School Shopping Mini Vlog",
    channel: "nishi tiwari",
    category: "City"
  },
  {
    id: "-TjojsxYU6U",
    title: "Funny Animals",
    channel: "javeed hashim 94",
    category: "Animals"
  },
  {
    id: "MObqFN_Jr6U",
    title: "How To Unload Lions",
    channel: "The Lion Whisperer",
    category: "Animals"
  },
  {
    id: "X6NLp_p7QWw",
    title: "DIY Window Clings",
    channel: "Mukta easy drawing",
    category: "Art"
  },
  {
    id: "9aBz4G5OfGg",
    title: "Digital Art",
    channel: "WhArt",
    category: "Art"
  },
  {
    id: "9dMFxnpEkKc",
    title: "Newton's Apple",
    channel: "Sick Science!",
    category: "Science"
  },
  {
    id: "FdoQ4zT929Q",
    title: "The Rarest Rainfalls",
    channel: "MisterUniverse",
    category: "Science"
  }
];
const COMPANION_PANEL_STORAGE_KEY = "toolhub.chat.companion.width";
const DEFAULT_COMPANION_PANEL_WIDTH = 320;
const MIN_COMPANION_PANEL_WIDTH = 280;
const MAX_COMPANION_PANEL_WIDTH = 420;
const SHORTS_START_HISTORY_STORAGE_KEY = "toolhub.chat.shorts.recent-starts";
const SHORTS_START_HISTORY_LIMIT = 12;
const SHORTS_NON_REPEAT_WINDOW = 50;
const SHORTS_FETCH_BATCH_SIZE = 24;
const SHORTS_PREFETCH_THRESHOLD = 12;
const MOBILE_BREAKPOINT_QUERY = "(max-width: 1023px)";
const DISPLAY_MODE_STANDALONE_QUERY = "(display-mode: standalone)";
const CHAT_DRAFT_STORAGE_PREFIX = "toolhub.chat.draft.v1";
const TOOL_BUILDER_PROMPT_EXAMPLES: ToolBuilderPromptExample[] = [
  {
    id: "price-tracker",
    title: "Price Tracker",
    summary: "Track products, save price history, and alert when targets are reached.",
    tags: ["Mongo", "Frontend", "Alerts"],
    prompt: `Build a production-ready price tracking tool.

Requirements:
- Build a modern dark-themed web UI for managing tracked products.
- Users should be able to add a product with name, product URL, target price, currency, category, and optional notes.
- Include a dashboard that shows current price, lowest recorded price, highest recorded price, target price, change since last check, and last checked time.
- Persist all tool data in the existing MongoDB provided by this platform using MONGO_URI and MONGO_DB_NAME.
- Create tool-specific MongoDB collections for products, price_history, and alerts.
- Do not use in-memory storage for tracked products or price history.
- Add APIs to create a tracked product, list tracked products, trigger a price check, update target price, and fetch historical price data.
- Support a manual "Check now" action from the UI.
- Store every observed price point so history survives reloads, container restarts, and crashes.
- Show a product detail view with a price history table or chart.
- Include alert logic that marks a product as triggered when the price is at or below the target.
- Add tests for product creation, MongoDB persistence, price history persistence, and the main tracking workflow.
- Keep the implementation lightweight and easy to run in Docker.
- Include GET /status returning {"status":"ok"}.`
  },
  {
    id: "movie-alerts",
    title: "Movie Alerts",
    summary: "Monitor live show listings and notify users when matches appear.",
    tags: ["Cron", "Backend", "Live Data"],
    prompt: `Build a production-ready movie alerts tool for live show availability.

Requirements:
- Build a modern dark-themed UI where users can create and manage movie alert rules.
- Users should be able to choose city, movie name, language, format, theatre preference, and date range.
- The backend should poll live movie listing sources on a schedule and compare fresh results with previous runs.
- Add cron or scheduler support so checks can run automatically at a configurable interval.
- Alerts should fire only when a newly available matching show appears compared with the previous successful check.
- Persist alert rules, polling runs, match history, and sent-alert history in MongoDB using the platform-provided MONGO_URI and MONGO_DB_NAME.
- Create separate collections for alert_rules, polling_runs, match_history, and sent_alerts.
- Include APIs to create an alert rule, list alert rules, trigger a manual run, fetch latest matches, and inspect previous run history.
- The dashboard should show rule status, last run time, newly detected matches, and previous alert activity.
- Design the workflow so duplicate alerts are avoided for the same show unless availability changes again later.
- Add tests for rule creation, scheduler/manual-run behavior, persistence of run history, and dedupe logic for alerts.
- Keep the stack lightweight and Docker-friendly.
- Include GET /status returning {"status":"ok"}.`
  },
  {
    id: "lead-dashboard",
    title: "Lead Dashboard",
    summary: "Collect leads, qualify them, and view pipeline analytics.",
    tags: ["Frontend", "Backend", "Mongo"],
    prompt: `Build a production-ready lead management dashboard.

Requirements:
- Build a modern dark-themed UI for sales or operations teams.
- Include a lead capture form with fields for name, company, email, phone, source, status, owner, notes, and priority.
- Add list and detail views for leads, plus summary cards and charts for pipeline status, source breakdown, and conversion trends.
- Persist all data in MongoDB using the existing platform database via MONGO_URI and MONGO_DB_NAME.
- Create collections for leads, lead_activities, and dashboard_snapshots or aggregated metrics if needed.
- Provide backend APIs to create, update, assign, qualify, archive, and list leads with filtering by status, owner, source, and date.
- Track a timeline of lead activity whenever a lead is created, updated, reassigned, or qualified.
- The dashboard should support quick actions like mark as contacted, qualify lead, move to closed won, and archive.
- Include search and filtering in the UI.
- Add tests for lead creation, update workflow, MongoDB persistence, and activity tracking behavior.
- Keep the implementation clean, practical, and Docker-friendly.
- Include GET /status returning {"status":"ok"}.`
  },
  {
    id: "content-planner",
    title: "Content Planner",
    summary: "Plan campaigns, store drafts, and schedule recurring work.",
    tags: ["Cron", "Mongo", "Workflow"],
    prompt: `Build a production-ready content planning and scheduling tool.

Requirements:
- Build a modern dark-themed UI for planning campaigns and managing content drafts.
- Users should be able to create campaigns, add draft content items, assign channel, target publish date, owner, status, and notes.
- Add calendar and list views for upcoming content, overdue work, draft status, and campaign progress.
- Persist all draft, campaign, and schedule data in the existing MongoDB using MONGO_URI and MONGO_DB_NAME.
- Create collections for campaigns, content_items, publishing_schedules, and activity_log.
- Support recurring schedules or cron-style planned work for repetitive publishing tasks.
- Include backend APIs to create campaigns, create drafts, update publishing status, reschedule items, and list upcoming content.
- The UI should clearly separate idea, draft, review, scheduled, published, and archived states.
- Add a lightweight workflow history so users can see when a content item changed state.
- Include tests for campaign creation, content-item persistence, schedule creation, and status transition behavior.
- Keep the stack lightweight and practical for Docker deployment.
- Include GET /status returning {"status":"ok"}.`
  }
];

const DEFAULT_TOOL_BUILDER_INTAKE: ToolBuilderIntakeState = {
  toolName: "",
  details: "",
  workflow: "",
  integrations: "",
  constraints: "",
  requiresFrontend: true,
  requiresBackend: true,
  requiresMongo: false,
  requiresCron: false
};
const TOOL_BUILDER_STACK_HINTS: Array<{ field: ToolBuilderBooleanField; label: string }> = [
  { field: "requiresFrontend", label: "Frontend" },
  { field: "requiresBackend", label: "Backend/API" },
  { field: "requiresMongo", label: "MongoDB" },
  { field: "requiresCron", label: "Cron / Scheduler" }
];

function getPhoneViewportState(): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  return window.matchMedia(MOBILE_BREAKPOINT_QUERY).matches;
}

function getStandalonePwaState(): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  const displayModeStandalone = window.matchMedia(DISPLAY_MODE_STANDALONE_QUERY).matches;
  const iosStandalone = Boolean((window.navigator as Navigator & { standalone?: boolean }).standalone);
  return displayModeStandalone || iosStandalone;
}

function estimateReadTime(content: string[]): string {
  const text = content.join(" ").trim();
  const wordCount = text ? text.split(/\s+/).length : 0;
  const minutes = Math.max(
    MIN_KNOWLEDGE_ARTICLE_MINUTES,
    Math.ceil(wordCount / TECHNICAL_READING_WPM)
  );
  return `${minutes} min read`;
}

function buildEngineeringDeepDive(article: Pick<KnowledgeArticle, "title" | "company" | "feature">): string[] {
  return [
    `${article.title} should be read as a production architecture case study rather than a list of components. For senior engineers, the most useful lens is to identify where correctness can fail, where tail latency amplifies, and where operational work is hidden behind simple diagrams. Keep asking: what are the explicit non-goals, which guarantees are contractual versus best-effort, and what workload assumptions make this design reasonable? Those answers determine whether the same pattern should be copied into your system or replaced with a different trade-off profile.`,
    `A meaningful first pass is workload characterization. Estimate peak read and write QPS, p95 and p99 latency targets, fan-out shape, object size distribution, and seasonality. For ${article.company}, this framing often changes the entire architecture choice because the same feature can be CPU-bound at one scale and storage or network-bound at another. Mature design work avoids averages and instead budgets for burst multipliers, regional failover traffic, and retry amplification so the system remains stable under stress, not only under normal traffic.`,
    `Data modeling is where long-term complexity is usually decided. Define primary entities, mutation frequency, index strategy, retention windows, and archival boundaries before selecting databases. Partition keys should align with dominant access paths and tenant fairness goals, otherwise one hot key can erase horizontal scalability. In ${article.feature}, engineers should deliberately separate immutable event history from mutable serving state where possible, because this unlocks replay, debugging, and safer migrations while reducing coupling between transactional workloads and analytical workloads.`,
    `The write path deserves line-by-line scrutiny. Document every synchronous hop, idempotency key boundary, retry policy, timeout, and dead-letter route. If a request partially succeeds, decide exactly what user-visible state is acceptable and how reconciliation happens. Teams with strong reliability posture treat duplicate delivery and out-of-order events as normal operating conditions. For ${article.title}, the resilient approach is to make each side effect replay-safe and observable so operators can re-drive failed flows without requiring ad hoc scripts or data surgery during incidents.`,
    `Read-path design should start from user-perceived latency budgets and then allocate them across cache lookup, storage fetch, ranking or aggregation, and render serialization. Caching policy must be tied to correctness class, not only hit ratio. Some reads can tolerate stale values, while entitlement, payment, and permission checks often cannot. A solid engineering review will include cache invalidation ordering, negative caching behavior, stampede protection, and how stale data is signaled to consumers. These concerns decide whether ${article.feature} remains trustworthy at scale.`,
    `Consistency semantics should be explicit in product language. Engineers should name exactly which invariants are strict and which are eventually repaired: uniqueness, monotonic counters, causal ordering, balance conservation, or timeline freshness. Once defined, map each invariant to storage and messaging behavior with testable assertions. Senior teams avoid accidental consistency by writing these constraints into API contracts and integration tests. In distributed features inspired by ${article.company}, this discipline prevents subtle bugs that only appear under partitions, retries, or concurrent regional failover.`,
    `Failure-mode analysis is mandatory for a 10-minute caliber read. Consider node loss, zone outage, network partition, stale leader election, partial dependency outage, and delayed background jobs. For each scenario, define expected system behavior, blast radius, operator intervention, and user impact. Recovery design should include replay windows, data backfill procedures, and bounded degradation modes. In practice, systems that survive real incidents are not those with perfect components, but those that expose enough control points to recover safely without violating core business invariants.`,
    `Observability should be designed as part of the feature, not added after launch. Track golden signals plus domain counters that prove correctness, such as dropped event count, duplicate suppression hits, cache staleness age, and reconciliation lag. Add high-cardinality dimensions carefully so teams can isolate tenant, region, and version-specific regressions. For ${article.title}, on-call success depends on dashboards that connect symptom to probable layer quickly, and on alerts tied to user-impacting SLOs rather than noisy infrastructure thresholds that do not correlate with experience quality.`,
    `Capacity and cost modeling are often under-specified in design docs. Estimate storage growth per month, network egress per request class, cache memory footprint, and reprocessing cost for one-hour and one-day replay scenarios. Include worst-case costs during incidents, when retries and backfills can multiply load. For ${article.feature}, architecture choices that look elegant can become unaffordable if hot partitions force over-provisioning. Skilled engineers compare at least two variants, quantify spend versus reliability gain, and keep the cheaper option available as a fallback plan.`,
    `Security and compliance concerns should be mapped directly to data flow. Identify where PII enters, where it is transformed, where it is logged, and how deletion requests propagate across caches, indexes, and derived views. Encryption at rest and in transit is baseline; the harder problem is minimizing sensitive data spread across debugging tools and analytics copies. In systems modeled after ${article.company}, access policies should be enforceable at service boundaries with auditable decision logs, so incident response and compliance reviews can reconstruct exactly what happened and why.`,
    `Rollout strategy is part of architecture quality. Favor progressive delivery with shadow traffic, dual-write verification, and canary gates tied to clear rollback criteria. During migrations, preserve backward compatibility at API and schema layers long enough for asynchronous consumers to catch up. For ${article.title}, an effective migration plan includes data parity checks, replay rehearsals, and circuit breakers that disable optional enrichment while preserving critical flows. This reduces risk when introducing new indexes, ranking models, or storage engines under active production load.`,
    `Team topology and ownership boundaries influence system reliability as much as technology choices. Define which team owns schema evolution, incident response, dependency budgets, and deprecation policy. Cross-team contracts should include versioning guarantees and explicit escalation paths. In ${article.feature}, unclear ownership usually appears first as stale runbooks and long MTTR. High-performing organizations pair architecture decisions with operational ownership from day one, ensuring every component has a clear steward who can make fast decisions during load events and customer-impacting incidents.`,
    `A useful exercise after reading is to write a one-page rebuttal: under what traffic pattern, product constraint, or regulatory requirement would this design be the wrong choice? This forces engineers to reason about alternative architectures such as stronger transactional stores, simpler monolith boundaries, or asynchronous decomposition. Treat ${article.title} as a pattern catalog entry, not a template. The best senior-level outcome is the ability to defend the chosen trade-offs in terms of correctness, latency, operability, and cost, with measurable criteria for revisiting the decision later.`
  ];
}

function toSeededIndex(seed: string, length: number): number {
  if (length <= 0) {
    return 0;
  }
  let hash = 0;
  for (let index = 0; index < seed.length; index += 1) {
    hash = (hash * 31 + seed.charCodeAt(index)) | 0;
  }
  return Math.abs(hash) % length;
}

function clampIndex(index: number, length: number): number {
  if (length <= 0) {
    return 0;
  }
  const normalized = index % length;
  return normalized < 0 ? normalized + length : normalized;
}

type ShortsFeedState = {
  history: YouTubeShort[];
  cursor: number;
  recentIds: string[];
};

type YouTubeShortEmbedOptions = {
  autoplay?: boolean;
  mute?: boolean;
  controls?: boolean;
};

function readRecentShortStartIds(): string[] {
  if (typeof window === "undefined") {
    return [];
  }

  try {
    const rawValue = window.localStorage.getItem(SHORTS_START_HISTORY_STORAGE_KEY);
    const parsedValue = rawValue ? JSON.parse(rawValue) : [];
    return Array.isArray(parsedValue)
      ? parsedValue.filter((item): item is string => typeof item === "string" && item.length > 0)
      : [];
  } catch {
    return [];
  }
}

function rememberShortSessionStart(shortId: string): void {
  if (typeof window === "undefined" || !shortId) {
    return;
  }

  try {
    const recentStarts = readRecentShortStartIds();
    const nextStarts = [shortId, ...recentStarts.filter((id) => id !== shortId)].slice(0, SHORTS_START_HISTORY_LIMIT);
    window.localStorage.setItem(SHORTS_START_HISTORY_STORAGE_KEY, JSON.stringify(nextStarts));
  } catch {
    // Ignore storage failures; random rotation still works for the current page.
  }
}

function createInitialShortFeedState(pool: YouTubeShort[], rememberStart = true): ShortsFeedState {
  if (pool.length <= 0) {
    return { history: [], cursor: 0, recentIds: [] };
  }
  const recentStarts = readRecentShortStartIds();
  const firstShort = pickNextShortFromPool(pool, recentStarts, recentStarts[0]) ?? pool[0];
  if (!firstShort) {
    return { history: [], cursor: 0, recentIds: [] };
  }
  if (rememberStart) {
    rememberShortSessionStart(firstShort.id);
  }
  return ensureShortLookahead({
    history: [firstShort],
    cursor: 0,
    recentIds: [firstShort.id]
  }, pool);
}

function pickNextShortFromPool(
  pool: YouTubeShort[],
  recentIds: string[],
  fallbackExcludeId?: string
): YouTubeShort | null {
  if (pool.length === 0) {
    return null;
  }

  const recentSet = new Set(recentIds);
  const nonRecentCandidates = pool.filter((item) => !recentSet.has(item.id) && item.id !== fallbackExcludeId);
  if (nonRecentCandidates.length > 0) {
    return nonRecentCandidates[Math.floor(Math.random() * nonRecentCandidates.length)] ?? nonRecentCandidates[0] ?? null;
  }

  const noConsecutiveCandidates = pool.filter((item) => item.id !== fallbackExcludeId);
  if (noConsecutiveCandidates.length > 0) {
    return noConsecutiveCandidates[Math.floor(Math.random() * noConsecutiveCandidates.length)] ?? noConsecutiveCandidates[0] ?? null;
  }

  return pool[Math.floor(Math.random() * pool.length)] ?? pool[0] ?? null;
}

function appendNextShort(state: ShortsFeedState, pool: YouTubeShort[]): ShortsFeedState {
  if (pool.length <= 0) {
    return state;
  }

  const fallbackExcludeId = state.history[state.history.length - 1]?.id;
  const nextShort = pickNextShortFromPool(pool, state.recentIds, fallbackExcludeId);
  if (!nextShort) {
    return state;
  }

  const nonRepeatWindow = Math.max(1, Math.min(pool.length - 1, SHORTS_NON_REPEAT_WINDOW));
  const nextRecentIds = [...state.recentIds, nextShort.id].slice(-nonRepeatWindow);
  const nextHistory = [...state.history, nextShort];
  return {
    history: nextHistory,
    cursor: nextHistory.length - 1,
    recentIds: nextRecentIds
  };
}

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function ensureShortLookahead(state: ShortsFeedState, pool: YouTubeShort[]): ShortsFeedState {
  if (pool.length <= 1 || state.history.length <= 0 || state.cursor < state.history.length - 1) {
    return state;
  }

  const activeShortId = state.history[state.cursor]?.id;
  const nextShort = pickNextShortFromPool(pool, state.recentIds, activeShortId);
  if (!nextShort) {
    return state;
  }

  const nonRepeatWindow = Math.max(1, Math.min(pool.length - 1, SHORTS_NON_REPEAT_WINDOW));
  return {
    ...state,
    history: [...state.history, nextShort],
    recentIds: [...state.recentIds, nextShort.id].slice(-nonRepeatWindow)
  };
}

function advanceShortFeedState(state: ShortsFeedState, pool: YouTubeShort[]): ShortsFeedState {
  if (state.cursor < state.history.length - 1) {
    return ensureShortLookahead({ ...state, cursor: state.cursor + 1 }, pool);
  }
  return ensureShortLookahead(appendNextShort(state, pool), pool);
}

function buildYouTubeShortEmbedUrl(videoId: string, options: YouTubeShortEmbedOptions = {}): string {
  const params = new URLSearchParams({
    autoplay: options.autoplay === false ? "0" : "1",
    loop: "1",
    playlist: videoId,
    mute: options.mute ? "1" : "0",
    playsinline: "1",
    rel: "0",
    modestbranding: "1",
    controls: options.controls === false ? "0" : "1"
  });
  return `https://www.youtube-nocookie.com/embed/${encodeURIComponent(videoId)}?${params.toString()}`;
}

function buildToolBuilderIntakePrompt(intake: ToolBuilderIntakeState): string {
  const requestedCapabilities: string[] = [];
  requestedCapabilities.push(`Frontend: ${intake.requiresFrontend ? "required" : "not required"}`);
  requestedCapabilities.push(`Backend/API: ${intake.requiresBackend ? "required" : "not required"}`);
  requestedCapabilities.push(`MongoDB persistence: ${intake.requiresMongo ? "required" : "not required"}`);
  requestedCapabilities.push(`Cron/scheduled jobs: ${intake.requiresCron ? "required" : "not required"}`);

  const sections = [
    "Build a production-ready tool using this structured intake brief.",
    "",
    "Tool intake:",
    `- Tool name: ${intake.toolName.trim() || "Choose a concise product name based on the request"}`,
    `- Core request: ${intake.details.trim() || "Not provided"}`,
    `- Primary workflow: ${intake.workflow.trim() || "Infer the main workflow and ask concise clarification questions if needed"}`,
    `- Integrations or external systems: ${intake.integrations.trim() || "None specified"}`,
    `- Constraints or special notes: ${intake.constraints.trim() || "None specified"}`,
    ...requestedCapabilities.map((item) => `- ${item}`),
    "",
    "Delivery guidance:",
    "- First clarify any missing or risky requirements.",
    "- Then refine this intake into an implementation-ready build brief with explicit assumptions and acceptance criteria before coding.",
    "- Keep the resulting product modern, polished, and dark-themed by default unless the user later asks for a different theme."
  ];

  return sections.join("\n");
}

function ToolBuilderIntakeModal({
  open,
  value,
  onChange,
  onClose,
  onApply
}: {
  open: boolean;
  value: ToolBuilderIntakeState;
  onChange: (next: ToolBuilderIntakeState) => void;
  onClose: () => void;
  onApply: () => void;
}) {
  if (!open) {
    return null;
  }

  const updateField = <K extends keyof ToolBuilderIntakeState>(field: K, nextValue: ToolBuilderIntakeState[K]): void => {
    onChange({
      ...value,
      [field]: nextValue
    });
  };

  return (
    <div className="fixed inset-0 z-[70] flex items-start justify-center overflow-y-auto bg-black/70 px-3 py-3 backdrop-blur-sm md:items-center md:px-4 md:py-6">
      <button
        type="button"
        aria-label="Close prompt intake form"
        onClick={onClose}
        className="absolute inset-0"
      />
      <div className="relative z-[71] my-auto flex w-full max-w-3xl flex-col overflow-hidden rounded-[1.75rem] border border-amber/20 bg-[#11100d] shadow-[0_28px_90px_-30px_rgba(0,0,0,0.95)] max-md:min-h-[calc(100dvh-1.5rem)] max-md:max-h-[calc(100dvh-1.5rem)] md:max-h-[calc(100dvh-3rem)]">
        <div className="shrink-0 border-b border-amber/15 bg-[radial-gradient(circle_at_top,#2a2116_0%,#16120d_52%,#0d0b09_100%)] px-4 py-4 md:px-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-[11px] uppercase tracking-[0.18em] text-amber/70">Tool Builder Intake</p>
              <h2 className="mt-1 text-xl font-semibold text-[color:var(--text-main)]">Shape the request before you send it</h2>
              <p className="mt-2 max-w-2xl text-sm text-muted">
                This form turns rough ideas into a structured build brief. Tool Builder will still clarify gaps and refine the brief before the actual build starts.
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="flex h-9 w-9 items-center justify-center rounded-full border border-white/10 bg-black/35 text-muted transition hover:border-amber/35 hover:text-[color:var(--text-main)]"
            >
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M6 6l12 12" />
                <path d="M18 6L6 18" />
              </svg>
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-4 md:px-5 md:py-5">
          <div className="grid gap-4 md:grid-cols-2">
            <label className="block">
              <span className="mb-1.5 block text-sm text-muted">Tool name</span>
              <input
                value={value.toolName}
                onChange={(event) => updateField("toolName", event.target.value)}
                className="w-full rounded-2xl border border-amber/20 bg-black/35 px-3 py-2.5 text-sm text-[color:var(--text-main)] outline-none transition focus:border-amber/45"
                placeholder="Price Watch Console"
              />
            </label>
            <div className="rounded-2xl border border-amber/15 bg-black/30 px-4 py-3">
              <p className="text-sm font-medium text-[color:var(--text-main)]">Stack hints</p>
              <p className="mt-1 text-xs text-muted">Use these to steer the generated tool without hand-writing every implementation detail.</p>
              <div className="mt-3 grid grid-cols-2 gap-2 text-sm">
                {TOOL_BUILDER_STACK_HINTS.map(({ field, label }) => {
                  const checked = value[field];
                  return (
                    <label
                      key={field}
                      className={`flex cursor-pointer items-center gap-2 rounded-xl border px-3 py-2 transition ${
                        checked
                          ? "border-amber/40 bg-amber/10 text-[color:var(--text-main)]"
                          : "border-white/10 bg-black/25 text-muted hover:border-amber/25"
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(event) => updateField(field, event.target.checked)}
                        className="h-4 w-4 rounded border-white/20 bg-transparent accent-[#f1c66a]"
                      />
                      <span>{label}</span>
                    </label>
                  );
                })}
              </div>
            </div>
          </div>

          <div className="mt-4 grid gap-4">
            <label className="block">
              <span className="mb-1.5 block text-sm text-muted">What should the tool do?</span>
              <textarea
                value={value.details}
                onChange={(event) => updateField("details", event.target.value)}
                rows={5}
                className="w-full rounded-2xl border border-amber/20 bg-black/35 px-3 py-3 text-sm text-[color:var(--text-main)] outline-none transition focus:border-amber/45"
                placeholder="Describe the main job of the tool, the key entities it manages, and the outcome the user expects."
              />
            </label>

            <label className="block">
              <span className="mb-1.5 block text-sm text-muted">Primary workflow</span>
              <textarea
                value={value.workflow}
                onChange={(event) => updateField("workflow", event.target.value)}
                rows={4}
                className="w-full rounded-2xl border border-amber/20 bg-black/35 px-3 py-3 text-sm text-[color:var(--text-main)] outline-none transition focus:border-amber/45"
                placeholder="Example: user adds a product URL, sets a target price, runs a manual check, and later sees saved price history and alerts."
              />
            </label>

            <div className="grid gap-4 md:grid-cols-2">
              <label className="block">
                <span className="mb-1.5 block text-sm text-muted">Integrations or data sources</span>
                <textarea
                  value={value.integrations}
                  onChange={(event) => updateField("integrations", event.target.value)}
                  rows={4}
                  className="w-full rounded-2xl border border-amber/20 bg-black/35 px-3 py-3 text-sm text-[color:var(--text-main)] outline-none transition focus:border-amber/45"
                  placeholder="External APIs, websites, email providers, webhooks, or third-party systems."
                />
              </label>
              <label className="block">
                <span className="mb-1.5 block text-sm text-muted">Constraints or notes</span>
                <textarea
                  value={value.constraints}
                  onChange={(event) => updateField("constraints", event.target.value)}
                  rows={4}
                  className="w-full rounded-2xl border border-amber/20 bg-black/35 px-3 py-3 text-sm text-[color:var(--text-main)] outline-none transition focus:border-amber/45"
                  placeholder="Mention limits like lightweight stack, backend-only, auth needs, admin access, or expected scale."
                />
              </label>
            </div>
          </div>
        </div>

        <div className="shrink-0 border-t border-amber/15 bg-[#100e0c]/96 px-4 py-3 backdrop-blur md:px-5">
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-amber/15 bg-black/25 px-4 py-3">
            <p className="max-w-xl text-xs text-muted">
              After you insert this draft into chat, Tool Builder will ask follow-up questions if needed and then refine it into a model-friendly implementation brief before building.
            </p>
            <div className="flex w-full flex-wrap items-center justify-end gap-2 sm:w-auto">
              <button type="button" onClick={onClose} className="btn-ghost px-3 py-2 text-sm">
                Cancel
              </button>
              <button type="button" onClick={onApply} className="btn-primary px-4 py-2 text-sm">
                Insert Into Chat
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ThinkingCompanionPanel({
  mode,
  activeShort,
  preloadedShort,
  article,
  onMinimize,
  onOpenShort,
  onNextShort,
  onPreviousShort,
  onShortRailWheel,
  onNextArticle,
  onPreviousArticle,
  onOpenArticleExpanded,
  onOpenArticleWindow,
  onModeChange,
  allowExternalOpen
}: {
  mode: SelectableThinkingPanelMode;
  activeShort: YouTubeShort;
  preloadedShort: YouTubeShort | null;
  article: KnowledgeArticle;
  onMinimize: () => void;
  onOpenShort: () => void;
  onNextShort: () => void;
  onPreviousShort: () => void;
  onShortRailWheel: (event: ReactWheelEvent<HTMLDivElement>) => void;
  onNextArticle: () => void;
  onPreviousArticle: () => void;
  onOpenArticleExpanded: () => void;
  onOpenArticleWindow: () => void;
  onModeChange: (nextMode: SelectableThinkingPanelMode) => void;
  allowExternalOpen: boolean;
}) {
  const shortEmbedUrl = buildYouTubeShortEmbedUrl(activeShort.id);
  const preloadedShortEmbedUrl = preloadedShort?.id
    ? buildYouTubeShortEmbedUrl(preloadedShort.id, { autoplay: false, mute: true, controls: false })
    : null;

  if (mode === "insta") {
    return (
      <section className="flex h-full min-h-0 w-full flex-col bg-[radial-gradient(circle_at_top,#2f2617_0%,#15110b_44%,#0a0907_100%)] text-[color:var(--text-main)]">
        <header className="shrink-0 px-3 pb-2 pt-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="inline-flex rounded-full border border-amber/25 bg-black/35 px-2.5 py-1 text-[10px] uppercase tracking-[0.18em] text-muted">
                Thinking Companion
              </p>
              <h2 className="mt-2 text-[15px] font-semibold text-[color:var(--text-main)]">
                YouTube Shorts
              </h2>
              <div className="mt-2 inline-flex items-center gap-1 rounded-full border border-amber/20 bg-black/45 p-1">
                <button
                  type="button"
                  onClick={() => onModeChange("insta")}
                  className="rounded-full bg-amber/20 px-2.5 py-1 text-[10px] font-medium uppercase tracking-[0.1em] text-[color:var(--text-main)]"
                >
                  Shorts
                </button>
                <button
                  type="button"
                  onClick={() => onModeChange("knowledge")}
                  className="rounded-full px-2.5 py-1 text-[10px] uppercase tracking-[0.1em] text-muted transition hover:bg-black/45 hover:text-[color:var(--text-main)]"
                >
                  Reads
                </button>
              </div>
            </div>
            <button
              type="button"
              onClick={onMinimize}
              className="flex h-8 w-8 items-center justify-center rounded-full border border-white/10 bg-black/45 text-muted transition hover:border-amber/35 hover:text-[color:var(--text-main)]"
              aria-label="Close thinking companion"
            >
              <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M6 6l12 12" />
                <path d="M18 6L6 18" />
              </svg>
            </button>
          </div>
        </header>

        <div className="flex min-h-0 flex-1 flex-col px-3 pb-3 pt-1">
          <div className="mb-2 flex items-center gap-2">
            <span className="rounded-full border border-white/10 bg-black/45 px-2.5 py-1 text-[10px] uppercase tracking-[0.14em] text-muted">
              {activeShort.category}
            </span>
            <button
              type="button"
              onClick={onPreviousShort}
              className="btn-ghost px-2.5 py-1.5 text-[11px]"
            >
              Prev
            </button>
            <button
              type="button"
              onClick={onNextShort}
              className="btn-ghost px-2.5 py-1.5 text-[11px]"
            >
              Next
            </button>
            {allowExternalOpen && (
              <button
                type="button"
                onClick={onOpenShort}
                className="ml-auto btn-ghost px-2.5 py-1.5 text-[11px]"
              >
                Open
              </button>
            )}
          </div>
          <div className="relative min-h-[320px] flex-1 overflow-hidden rounded-[1.45rem] border border-white/10 bg-black/70 shadow-[0_22px_60px_-34px_rgba(0,0,0,0.95)] lg:min-h-0">
            <iframe
              key={activeShort.id}
              title={`${activeShort.title} by ${activeShort.channel}`}
              src={shortEmbedUrl}
              loading="eager"
              referrerPolicy="strict-origin-when-cross-origin"
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
              allowFullScreen
              className="absolute inset-0 h-full w-full border-0"
            />
            {preloadedShortEmbedUrl && preloadedShort && (
              <iframe
                key={`preload-${preloadedShort.id}`}
                title={`Preload ${preloadedShort.title}`}
                src={preloadedShortEmbedUrl}
                loading="eager"
                referrerPolicy="strict-origin-when-cross-origin"
                allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                aria-hidden="true"
                tabIndex={-1}
                className="pointer-events-none absolute -left-px -top-px h-px w-px border-0 opacity-0"
              />
            )}
            <div className="pointer-events-none absolute inset-x-0 bottom-0 h-24 bg-gradient-to-t from-black/90 via-black/30 to-transparent" />
            <div className="absolute bottom-3 left-3 right-14 rounded-2xl border border-white/10 bg-black/60 px-3 py-2 backdrop-blur">
              <p className="text-sm font-medium leading-5 text-[color:var(--text-main)]">
                {activeShort.title}
              </p>
              <p className="mt-1 text-[11px] text-muted">{activeShort.channel}</p>
            </div>
            <div
              className="absolute inset-y-0 right-0 z-10 flex w-12 flex-col items-center justify-center gap-2 bg-gradient-to-l from-black/55 to-transparent"
              onWheel={onShortRailWheel}
            >
              <button
                type="button"
                onClick={onPreviousShort}
                className="flex h-9 w-9 items-center justify-center rounded-full border border-white/10 bg-black/72 text-[color:var(--text-main)] transition hover:border-amber/45"
                aria-label="Previous short"
              >
                ↑
              </button>
              <button
                type="button"
                onClick={onNextShort}
                className="flex h-9 w-9 items-center justify-center rounded-full border border-white/10 bg-black/72 text-[color:var(--text-main)] transition hover:border-amber/45"
                aria-label="Next short"
              >
                ↓
              </button>
            </div>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="flex h-full min-h-0 w-full flex-col bg-[radial-gradient(circle_at_top,#2f2617_0%,#15110b_44%,#0a0907_100%)] text-[#f9edcf]">
      <header className="shrink-0 px-3 pb-2 pt-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="inline-flex rounded-full border border-amber/25 bg-black/35 px-2.5 py-1 text-[10px] uppercase tracking-[0.18em] text-[#d4c398]">
              Thinking Companion
            </p>
            <h2 className="mt-2 text-[15px] font-semibold text-[#f9edcf]">
              System Design Reads
            </h2>
            <div className="mt-2 inline-flex items-center gap-1 rounded-full border border-amber/20 bg-black/45 p-1">
              <button
                type="button"
                onClick={() => onModeChange("insta")}
                className="rounded-full px-2.5 py-1 text-[10px] uppercase tracking-[0.1em] text-[#d4c398] transition hover:bg-black/45 hover:text-[#f9edcf]"
              >
                Shorts
              </button>
              <button
                type="button"
                onClick={() => onModeChange("knowledge")}
                className="rounded-full bg-amber/20 px-2.5 py-1 text-[10px] font-medium uppercase tracking-[0.1em] text-[#f9edcf]"
              >
                Reads
              </button>
            </div>
          </div>
          <button
            type="button"
            onClick={onMinimize}
            className="flex h-8 w-8 items-center justify-center rounded-full border border-white/10 bg-black/45 text-muted transition hover:border-amber/35 hover:text-[color:var(--text-main)]"
            aria-label="Close thinking companion"
          >
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M6 6l12 12" />
              <path d="M18 6L6 18" />
            </svg>
          </button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col px-3 pb-3 pt-1">
        <div className="mb-2 flex items-center gap-2">
          <span className="rounded-full border border-white/10 bg-black/45 px-2.5 py-1 text-[10px] uppercase tracking-[0.14em] text-[#d4c398]">
            {article.readTime}
          </span>
          <button
            type="button"
            onClick={onPreviousArticle}
            className="btn-ghost px-2.5 py-1.5 text-[11px]"
          >
            Prev
          </button>
          <button
            type="button"
            onClick={onNextArticle}
            className="btn-ghost px-2.5 py-1.5 text-[11px]"
          >
            Next
          </button>
          <button
            type="button"
            onClick={onOpenArticleExpanded}
            className="btn-ghost px-2.5 py-1.5 text-[11px]"
          >
            Expand
          </button>
          {allowExternalOpen && (
            <button
              type="button"
              onClick={onOpenArticleWindow}
              className="ml-auto btn-ghost px-2.5 py-1.5 text-[11px]"
            >
              Open
            </button>
          )}
        </div>

        <article
          className="min-h-0 flex-1 overflow-y-auto overscroll-y-contain rounded-[1.45rem] border border-white/10 bg-black/65 px-3 pb-4 pt-3 shadow-[0_22px_60px_-34px_rgba(0,0,0,0.95)]"
          onWheel={(event) => event.stopPropagation()}
        >
          <p className="text-[11px] uppercase tracking-[0.14em] text-[#d4c398]">
            {article.company} • {article.feature}
          </p>
          <h3 className="mt-1 text-base font-semibold text-[#f9edcf]">
            {article.title}
          </h3>
          <div className="mt-3 space-y-2">
            {article.content.map((paragraph) => (
              <p
                key={paragraph}
                className="text-sm leading-6 text-[#f9edcf]"
              >
                {paragraph}
              </p>
            ))}
          </div>
        </article>
      </div>
    </section>
  );
}

function parseMessageAttachments(metadata: Record<string, unknown>): MessageAttachment[] {
  const raw = metadata.attachments;
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    .map((item) => ({
      id: String(item.id ?? ""),
      fileName: String(item.fileName ?? "image"),
      contentType: String(item.contentType ?? ""),
      size: Number(item.size ?? 0),
      url: String(item.url ?? "")
    }))
    .filter((item) => Boolean(item.id) && Boolean(item.url));
}

function toAttachmentUrl(url: string): string {
  if (url.startsWith("http://") || url.startsWith("https://")) {
    return url;
  }
  const base = API_BASE_URL.endsWith("/") ? API_BASE_URL.slice(0, -1) : API_BASE_URL;
  const path = url.startsWith("/") ? url : `/${url}`;
  return `${base}${path}`;
}

function toSelectableMode(mode: ChatMode): SelectableMode {
  if (mode === "tool_builder") {
    return "tool_builder";
  }
  if (mode === "operator" || mode === "pi_operator") {
    return "operator";
  }
  return "general";
}

function isPinnedNewChat(chat: ChatSession): boolean {
  return chat.title.trim().toLowerCase() === "new chat";
}

function sortChatsForSidebar(list: ChatSession[]): ChatSession[] {
  return [...list].sort((left, right) => {
    const leftPinned = isPinnedNewChat(left);
    const rightPinned = isPinnedNewChat(right);

    if (leftPinned !== rightPinned) {
      return leftPinned ? -1 : 1;
    }

    return new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime();
  });
}

function hasPendingAssistantReply(messages: ChatMessage[]): boolean {
  let latestUserIndex = -1;
  let latestAssistantIndex = -1;
  for (let index = 0; index < messages.length; index += 1) {
    const role = messages[index]?.role;
    if (role === "user") {
      latestUserIndex = index;
      continue;
    }
    if (role === "assistant") {
      latestAssistantIndex = index;
    }
  }
  return latestUserIndex > latestAssistantIndex;
}

function chatDraftStorageKey(chatId: string | null): string {
  return `${CHAT_DRAFT_STORAGE_PREFIX}:${chatId ?? "new-chat"}`;
}

function loadChatDraft(chatId: string | null): string {
  try {
    return window.localStorage.getItem(chatDraftStorageKey(chatId)) ?? "";
  } catch {
    return "";
  }
}

function persistChatDraft(chatId: string | null, value: string): void {
  try {
    const key = chatDraftStorageKey(chatId);
    if (value.length === 0) {
      window.localStorage.removeItem(key);
      return;
    }
    window.localStorage.setItem(key, value);
  } catch {
    // Ignore localStorage failures for chat drafts.
  }
}

function ChatPageContent() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [chats, setChats] = useState<ChatSession[]>([]);
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [prompt, setPrompt] = useState("");
  const [composerMode, setComposerMode] = useState<SelectableMode>("general");
  const [composerModel, setComposerModel] = useState<string | null>(null);
  const [toolBuilderIntakeOpen, setToolBuilderIntakeOpen] = useState(false);
  const [toolBuilderStudioManualOverride, setToolBuilderStudioManualOverride] = useState(false);
  const [toolBuilderStudioVisible, setToolBuilderStudioVisible] = useState(true);
  const [toolBuilderIntake, setToolBuilderIntake] = useState<ToolBuilderIntakeState>(DEFAULT_TOOL_BUILDER_INTAKE);
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [defaultModel, setDefaultModel] = useState<string | null>(null);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [sending, setSending] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [creatingChat, setCreatingChat] = useState(false);
  const [updatingMode, setUpdatingMode] = useState(false);
  const [updatingModel, setUpdatingModel] = useState(false);
  const [streamActivity, setStreamActivity] = useState<StreamActivity>("ready");
  const [streamingAssistant, setStreamingAssistant] = useState("");
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingImages, setPendingImages] = useState<PendingImageAttachment[]>([]);
  const [thinkingPanelMode, setThinkingPanelMode] = useState<ThinkingPanelMode>("none");
  const [thinkingPanelCycle, setThinkingPanelCycle] = useState(0);
  const [companionOpen, setCompanionOpen] = useState(false);
  const [companionPanelWidth, setCompanionPanelWidth] = useState(DEFAULT_COMPANION_PANEL_WIDTH);
  const [expandedArticleOpen, setExpandedArticleOpen] = useState(false);
  const [activeArticleIndex, setActiveArticleIndex] = useState(0);
  const [shortPool, setShortPool] = useState<YouTubeShort[]>(() => YOUTUBE_SHORTS);
  const [shortFeedState, setShortFeedState] = useState<ShortsFeedState>(() =>
    createInitialShortFeedState(YOUTUBE_SHORTS, false)
  );
  const [shortFeedCursor, setShortFeedCursor] = useState<string | null>(null);
  const [isPhoneViewport, setIsPhoneViewport] = useState(false);
  const [isStandalonePwa, setIsStandalonePwa] = useState(false);

  const initializedRef = useRef(false);
  const handledNewTokenRef = useRef<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const streamingAssistantRef = useRef<HTMLDivElement | null>(null);
  const messageElementRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const activeChatIdRef = useRef<string | null>(null);
  const streamTokenRef = useRef(0);
  const pendingScrollToEndRef = useRef(false);
  const pendingAssistantScrollMessageIdRef = useRef<string | null>(null);
  const pendingStreamingStartScrollRef = useRef(false);
  const optimisticUserMessageRef = useRef<{ chatId: string; messageId: string; message: ChatMessage } | null>(null);
  const sendingChatIdRef = useRef<string | null>(null);
  const imageInputRef = useRef<HTMLInputElement | null>(null);
  const promptTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  const copyFeedbackTimeoutRef = useRef<number | null>(null);
  const shortWheelCooldownRef = useRef(0);
  const shortFeedRequestInFlightRef = useRef(false);
  const shortFeedExhaustedRef = useRef(false);
  const shortPoolIdsRef = useRef<Set<string>>(new Set(YOUTUBE_SHORTS.map((short) => short.id)));
  const shortFeedCursorRef = useRef<string | null>(null);
  const wasThinkingActiveRef = useRef(false);
  const companionAsideRef = useRef<HTMLElement | null>(null);
  const companionResizeRafRef = useRef<number | null>(null);
  const companionDragWidthRef = useRef<number | null>(null);

  const clampCompanionPanelWidth = useCallback((width: number): number => {
    if (typeof window === "undefined") {
      return Math.max(MIN_COMPANION_PANEL_WIDTH, Math.min(MAX_COMPANION_PANEL_WIDTH, width));
    }

    const viewportCap = Math.floor(window.innerWidth * 0.96);
    const maxAllowed = Math.max(MIN_COMPANION_PANEL_WIDTH, Math.min(MAX_COMPANION_PANEL_WIDTH, viewportCap));
    return Math.max(MIN_COMPANION_PANEL_WIDTH, Math.min(maxAllowed, width));
  }, []);

  const activeChat = useMemo(() => chats.find((chat) => chat.id === activeChatId) ?? null, [chats, activeChatId]);
  const isToolBuilderMode = composerMode === "tool_builder";
  const shouldSubmitOnEnter = !isPhoneViewport && !isStandalonePwa;

  const requestedChatId = searchParams.get("chatId") ?? searchParams.get("sessionId");
  const newChatToken = searchParams.get("new");

  function resolveModelForRequest(): string | null {
    return composerModel ?? activeChat?.model ?? defaultModel ?? availableModels[0] ?? null;
  }

  function resolveModelForNewChat(): string | null {
    return defaultModel ?? availableModels[0] ?? resolveModelForRequest();
  }

  function scrollChatTargetIntoView(element: HTMLElement | null, block: ScrollLogicalPosition): void {
    if (!element) {
      return;
    }
    window.requestAnimationFrame(() => {
      element.scrollIntoView({ behavior: "smooth", block, inline: "nearest" });
    });
  }

  function latestAssistantMessageId(items: ChatMessage[]): string | null {
    for (let index = items.length - 1; index >= 0; index -= 1) {
      const message = items[index];
      if (message?.role === "assistant") {
        return message.id;
      }
    }
    return null;
  }

  function clearCopyFeedbackTimeout(): void {
    if (copyFeedbackTimeoutRef.current === null) {
      return;
    }
    window.clearTimeout(copyFeedbackTimeoutRef.current);
    copyFeedbackTimeoutRef.current = null;
  }

  function showCopiedMessageFeedback(messageId: string): void {
    setCopiedMessageId(messageId);
    clearCopyFeedbackTimeout();
    copyFeedbackTimeoutRef.current = window.setTimeout(() => {
      setCopiedMessageId((current) => (current === messageId ? null : current));
      copyFeedbackTimeoutRef.current = null;
    }, 1800);
  }

  async function handleCopyMessage(message: ChatMessage): Promise<void> {
    const text = (message.content || "").trim();
    if (!text) {
      setError("Message is empty and could not be copied.");
      return;
    }

    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const textarea = document.createElement("textarea");
        textarea.value = text;
        textarea.setAttribute("readonly", "true");
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        textarea.style.pointerEvents = "none";
        textarea.style.left = "-9999px";
        document.body.appendChild(textarea);
        textarea.focus();
        textarea.select();
        const copied = document.execCommand("copy");
        document.body.removeChild(textarea);
        if (!copied) {
          throw new Error("Clipboard copy command failed.");
        }
      }
      setError(null);
      showCopiedMessageFeedback(message.id);
    } catch {
      setError("Unable to copy this message automatically.");
    }
  }

  const loadMoreShorts = useCallback(async (batchSize = SHORTS_FETCH_BATCH_SIZE): Promise<void> => {
    if (shortFeedRequestInFlightRef.current || shortFeedExhaustedRef.current) {
      return;
    }

    shortFeedRequestInFlightRef.current = true;
    try {
      const response = await fetchYouTubeShortsFeed(shortFeedCursorRef.current, batchSize);
      const uniqueIncoming = response.items.filter((item) => {
        if (!item.id || shortPoolIdsRef.current.has(item.id)) {
          return false;
        }
        shortPoolIdsRef.current.add(item.id);
        return true;
      });

      if (uniqueIncoming.length > 0) {
        setShortPool((current) => [...current, ...uniqueIncoming]);
      }

      shortFeedCursorRef.current = response.nextCursor;
      setShortFeedCursor(response.nextCursor);
      if (!response.nextCursor) {
        shortFeedExhaustedRef.current = true;
      }
    } catch (loadError) {
      console.warn("Unable to load YouTube shorts feed:", loadError);
    } finally {
      shortFeedRequestInFlightRef.current = false;
    }
  }, []);

  const handleNextShort = useCallback((): void => {
    setShortFeedState((current) => advanceShortFeedState(current, shortPool));
  }, [shortPool]);

  const handlePreviousShort = useCallback((): void => {
    setShortFeedState((current) => ({
      ...current,
      cursor: Math.max(0, current.cursor - 1)
    }));
  }, []);

  const handleShortRailWheel = useCallback((event: ReactWheelEvent<HTMLDivElement>): void => {
    event.preventDefault();
    const now = Date.now();
    if (now - shortWheelCooldownRef.current < 420) {
      return;
    }
    if (Math.abs(event.deltaY) < 14) {
      return;
    }

    shortWheelCooldownRef.current = now;
    if (event.deltaY > 0) {
      handleNextShort();
      return;
    }
    handlePreviousShort();
  }, [handleNextShort, handlePreviousShort]);

  const activeShort = useMemo(() => {
    const fallbackShort: YouTubeShort = {
      id: "",
      title: "No Shorts available",
      channel: "Unknown",
      category: "General"
    };
    if (shortFeedState.history.length <= 0) {
      return fallbackShort;
    }
    const historyIndex = Math.max(0, Math.min(shortFeedState.cursor, shortFeedState.history.length - 1));
    return shortFeedState.history[historyIndex] ?? shortFeedState.history[0] ?? fallbackShort;
  }, [shortFeedState]);

  const preloadedShort = useMemo(() => {
    const nextIndex = shortFeedState.cursor + 1;
    return shortFeedState.history[nextIndex] ?? null;
  }, [shortFeedState]);

  const openActiveShort = useCallback((): void => {
    if (!activeShort.id) {
      return;
    }
    const shortUrl = `https://www.youtube.com/shorts/${activeShort.id}`;
    const popup = window.open(shortUrl, "_blank", "noopener,noreferrer");
    if (!popup && (isPhoneViewport || isStandalonePwa)) {
      window.location.assign(shortUrl);
    }
  }, [activeShort, isPhoneViewport, isStandalonePwa]);

  useEffect(() => {
    if (thinkingPanelMode !== "insta") {
      return;
    }
    shortFeedExhaustedRef.current = false;
    shortFeedRequestInFlightRef.current = false;
    setShortFeedState(createInitialShortFeedState(shortPool));
  }, [activeChatId, thinkingPanelCycle, thinkingPanelMode]);

  useEffect(() => {
    if (thinkingPanelMode !== "insta") {
      return;
    }
    setShortFeedState((current) => ensureShortLookahead(current, shortPool));
  }, [thinkingPanelMode, shortPool]);

  useEffect(() => {
    shortFeedCursorRef.current = shortFeedCursor;
  }, [shortFeedCursor]);

  useEffect(() => {
    if (thinkingPanelMode !== "insta") {
      return;
    }
    void loadMoreShorts();
  }, [thinkingPanelMode, loadMoreShorts]);

  useEffect(() => {
    if (thinkingPanelMode !== "insta") {
      return;
    }
    const remainingPoolBuffer = shortPool.length - shortFeedState.history.length;
    if (remainingPoolBuffer <= SHORTS_PREFETCH_THRESHOLD) {
      void loadMoreShorts();
    }
  }, [thinkingPanelMode, shortPool.length, shortFeedState.history.length, loadMoreShorts]);

  useEffect(() => {
    if (thinkingPanelMode !== "insta") {
      return;
    }

    const handleArrowNavigation = (event: KeyboardEvent): void => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        handleNextShort();
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        handlePreviousShort();
      }
    };

    window.addEventListener("keydown", handleArrowNavigation);
    return () => {
      window.removeEventListener("keydown", handleArrowNavigation);
    };
  }, [thinkingPanelMode, handleNextShort, handlePreviousShort]);

  useEffect(() => {
    const thinkingActiveNow = streamActivity !== "ready" || Boolean(streamingAssistant);

    if (thinkingPanelMode === "none") {
      setCompanionOpen(false);
      setExpandedArticleOpen(false);
      wasThinkingActiveRef.current = thinkingActiveNow;
      return;
    }

    if (!wasThinkingActiveRef.current && thinkingActiveNow) {
      setCompanionOpen(true);
    }

    wasThinkingActiveRef.current = thinkingActiveNow;
  }, [thinkingPanelMode, streamActivity, streamingAssistant]);

  useEffect(() => {
    const seed = `${activeChatId ?? "new-chat"}-${thinkingPanelCycle}`;
    setActiveArticleIndex(toSeededIndex(`${seed}-knowledge`, KNOWLEDGE_ARTICLES.length));
  }, [activeChatId, thinkingPanelCycle]);

  useEffect(() => {
    if (!initializedRef.current || requestedChatId || !newChatToken) {
      return;
    }
    if (handledNewTokenRef.current === newChatToken) {
      return;
    }

    handledNewTokenRef.current = newChatToken;
    void handleCreateChat(composerMode);
  }, [newChatToken, requestedChatId, composerMode]);

  useEffect(() => {
    activeChatIdRef.current = activeChatId;
  }, [activeChatId]);

  useEffect(() => {
    setToolBuilderStudioManualOverride(false);
  }, [activeChatId, isToolBuilderMode]);

  useEffect(() => {
    if (!isToolBuilderMode) {
      setToolBuilderStudioVisible(false);
      return;
    }
    if (toolBuilderStudioManualOverride) {
      return;
    }
    if (loadingMessages && activeChatId) {
      return;
    }
    setToolBuilderStudioVisible(messages.length === 0);
  }, [activeChatId, isToolBuilderMode, loadingMessages, messages.length, toolBuilderStudioManualOverride]);

  useEffect(() => {
    const textarea = promptTextareaRef.current;
    if (!textarea) {
      return;
    }

    textarea.style.height = "0px";
    const nextHeight = Math.min(textarea.scrollHeight, CHAT_INPUT_MAX_HEIGHT_PX);
    textarea.style.height = `${Math.max(nextHeight, 44)}px`;
    textarea.style.overflowY = textarea.scrollHeight > CHAT_INPUT_MAX_HEIGHT_PX ? "auto" : "hidden";
  }, [prompt]);

  useEffect(() => {
    if (!activeChat) {
      return;
    }
    setComposerMode(toSelectableMode(activeChat.mode));
    const sessionModel = activeChat.model;
    if (sessionModel && availableModels.includes(sessionModel)) {
      setComposerModel(sessionModel);
      return;
    }
    setComposerModel(defaultModel ?? availableModels[0] ?? null);
  }, [activeChat, defaultModel, availableModels]);

  useEffect(() => {
    clearPendingImages();
  }, [activeChatId]);

  useEffect(() => {
    setCopiedMessageId(null);
    clearCopyFeedbackTimeout();
  }, [activeChatId]);

  useEffect(() => {
    setPrompt(loadChatDraft(activeChatId));
  }, [activeChatId]);

  useEffect(() => {
    persistChatDraft(activeChatId, prompt);
  }, [prompt]);

  useEffect(() => {
    if (!toolBuilderIntakeOpen) {
      return;
    }
    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = originalOverflow;
    };
  }, [toolBuilderIntakeOpen]);
  const handleCompanionResizeStart = useCallback(
    (event: ReactPointerEvent<HTMLButtonElement>): void => {
      event.preventDefault();
      const handleElement = event.currentTarget;
      const pointerId = event.pointerId;
      const startX = event.clientX;
      const startWidth = companionPanelWidth;
      companionDragWidthRef.current = startWidth;

      const previousUserSelect = document.body.style.userSelect;
      const previousCursor = document.body.style.cursor;
      document.body.style.userSelect = "none";
      document.body.style.cursor = "col-resize";

      if (handleElement.setPointerCapture) {
        try {
          handleElement.setPointerCapture(pointerId);
        } catch {
          // Ignore capture errors and keep fallback listeners active.
        }
      }

      const onPointerMove = (moveEvent: PointerEvent): void => {
        if (moveEvent.pointerId !== pointerId) {
          return;
        }
        const delta = moveEvent.clientX - startX;
        const nextWidth = clampCompanionPanelWidth(startWidth - delta);
        companionDragWidthRef.current = nextWidth;
        if (companionResizeRafRef.current !== null) {
          return;
        }
        companionResizeRafRef.current = window.requestAnimationFrame(() => {
          companionResizeRafRef.current = null;
          const draftWidth = companionDragWidthRef.current;
          if (typeof draftWidth !== "number" || !companionAsideRef.current) {
            return;
          }
          companionAsideRef.current.style.width = `min(${draftWidth}px, 96vw)`;
        });
      };

      const stopResize = (): void => {
        if (companionResizeRafRef.current !== null) {
          window.cancelAnimationFrame(companionResizeRafRef.current);
          companionResizeRafRef.current = null;
        }
        const finalWidth = companionDragWidthRef.current;
        if (typeof finalWidth === "number") {
          setCompanionPanelWidth(finalWidth);
        }
        document.body.style.userSelect = previousUserSelect;
        document.body.style.cursor = previousCursor;
        handleElement.removeEventListener("pointermove", onPointerMove);
        handleElement.removeEventListener("pointerup", stopResize);
        handleElement.removeEventListener("pointercancel", stopResize);
        handleElement.removeEventListener("lostpointercapture", stopResize);
        window.removeEventListener("blur", stopResize);
        if (handleElement.hasPointerCapture?.(pointerId)) {
          try {
            handleElement.releasePointerCapture(pointerId);
          } catch {
            // Ignore release errors when pointer is already released.
          }
        }
      };

      handleElement.addEventListener("pointermove", onPointerMove);
      handleElement.addEventListener("pointerup", stopResize);
      handleElement.addEventListener("pointercancel", stopResize);
      handleElement.addEventListener("lostpointercapture", stopResize);
      window.addEventListener("blur", stopResize);
    },
    [clampCompanionPanelWidth, companionPanelWidth]
  );

  useEffect(() => {
    return () => {
      if (companionResizeRafRef.current !== null) {
        window.cancelAnimationFrame(companionResizeRafRef.current);
        companionResizeRafRef.current = null;
      }
    };
  }, []);

  async function loadChats(): Promise<ChatSession[]> {
    const data = sortChatsForSidebar(await fetchChatSessions());
    setChats(data);
    setActiveChatId((current) => {
      if (current && data.some((chat) => chat.id === current)) {
        return current;
      }
      return data[0]?.id ?? null;
    });
    return data;
  }

  async function findReusableNewChat(list: ChatSession[]): Promise<ChatSession | null> {
    const candidates = list.filter((chat) => chat.title.trim().toLowerCase() === "new chat").slice(0, 5);

    for (const candidate of candidates) {
      try {
        const candidateMessages = await fetchChatMessages(candidate.id);
        if (candidateMessages.length === 0) {
          return candidate;
        }
      } catch {
        continue;
      }
    }

    return null;
  }

  async function bootstrapChat(initialChatId: string | null, preferFreshChat: boolean): Promise<void> {
    const list = sortChatsForSidebar(await fetchChatSessions());
    let selectedChatId: string | null = null;

    if (initialChatId) {
      setChats(list);
      const target = list.find((chat) => chat.id === initialChatId);
      if (target) {
        setActiveChatId(target.id);
        selectedChatId = target.id;
      }
      if (!requestedChatId && selectedChatId) {
        navigate(`/chat?chatId=${selectedChatId}`, { replace: true });
      }
      if (selectedChatId) {
        return;
      }
    }

    if (preferFreshChat) {
      const reusableNewChat = await findReusableNewChat(list);
      if (reusableNewChat) {
        setChats(list);
        setActiveChatId(reusableNewChat.id);
        selectedChatId = reusableNewChat.id;
        navigate(`/chat?chatId=${reusableNewChat.id}`, { replace: true });
        return;
      }

      const created = await createChatSession("New Chat", "general", resolveModelForNewChat());
      setChats(sortChatsForSidebar([created, ...list]));
      setActiveChatId(created.id);
      selectedChatId = created.id;
      navigate(`/chat?chatId=${created.id}`, { replace: true });
      return;
    }

    setChats(list);
    selectedChatId = list[0]?.id ?? null;
    setActiveChatId(selectedChatId);
    if (selectedChatId && !requestedChatId) {
      navigate(`/chat?chatId=${selectedChatId}`, { replace: true });
    }
  }

  useEffect(() => {
    if (initializedRef.current) {
      return;
    }

    initializedRef.current = true;
    handledNewTokenRef.current = newChatToken;
    setError(null);

    void bootstrapChat(requestedChatId, !requestedChatId).catch((loadError: unknown) => {
      setError(loadError instanceof Error ? loadError.message : "Unable to initialize chat");
    });
  }, [requestedChatId, newChatToken]);

  useEffect(() => {
    void fetchChatModels()
      .then((response) => {
        setAvailableModels(response.models);
        setDefaultModel(response.defaultModel);
        setComposerModel((current) => {
          if (current && response.models.includes(current)) {
            return current;
          }
          return response.defaultModel ?? response.models[0] ?? null;
        });
      })
      .catch((loadError: unknown) => {
        setError(loadError instanceof Error ? loadError.message : "Unable to load models");
      });
  }, []);

  useEffect(() => {
    return () => {
      for (const pendingImage of pendingImages) {
        URL.revokeObjectURL(pendingImage.previewUrl);
      }
    };
  }, [pendingImages]);

  useEffect(() => {
    return () => {
      clearCopyFeedbackTimeout();
    };
  }, []);

  useEffect(() => {
    const refreshPreference = (): void => {
      setThinkingPanelMode(loadThinkingPanelMode());
    };
    refreshPreference();
    window.addEventListener("storage", refreshPreference);
    window.addEventListener("focus", refreshPreference);
    return () => {
      window.removeEventListener("storage", refreshPreference);
      window.removeEventListener("focus", refreshPreference);
    };
  }, []);

  useEffect(() => {
    const syncViewportSignals = (): void => {
      setIsPhoneViewport(getPhoneViewportState());
      setIsStandalonePwa(getStandalonePwaState());
    };

    syncViewportSignals();
    const standaloneMedia = window.matchMedia(DISPLAY_MODE_STANDALONE_QUERY);
    window.addEventListener("resize", syncViewportSignals);
    document.addEventListener("visibilitychange", syncViewportSignals);

    if ("addEventListener" in standaloneMedia) {
      standaloneMedia.addEventListener("change", syncViewportSignals);
    } else {
      standaloneMedia.addListener(syncViewportSignals);
    }

    return () => {
      window.removeEventListener("resize", syncViewportSignals);
      document.removeEventListener("visibilitychange", syncViewportSignals);

      if ("removeEventListener" in standaloneMedia) {
        standaloneMedia.removeEventListener("change", syncViewportSignals);
      } else {
        standaloneMedia.removeListener(syncViewportSignals);
      }
    };
  }, []);

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(COMPANION_PANEL_STORAGE_KEY);
      if (!saved) {
        return;
      }
      const parsed = Number(saved);
      if (Number.isFinite(parsed)) {
        setCompanionPanelWidth(clampCompanionPanelWidth(parsed));
      }
    } catch {
      // Ignore localStorage errors for companion width.
    }
  }, [clampCompanionPanelWidth]);

  useEffect(() => {
    try {
      window.localStorage.setItem(COMPANION_PANEL_STORAGE_KEY, String(companionPanelWidth));
    } catch {
      // Ignore localStorage errors for companion width.
    }
  }, [companionPanelWidth]);

  useEffect(() => {
    const handleResize = (): void => {
      setCompanionPanelWidth((current) => clampCompanionPanelWidth(current));
    };
    window.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
    };
  }, [clampCompanionPanelWidth]);

  useEffect(() => {
    streamTokenRef.current += 1;
    setStreamActivity("ready");
    setStreamingAssistant("");
    optimisticUserMessageRef.current = null;
    pendingAssistantScrollMessageIdRef.current = null;
    pendingStreamingStartScrollRef.current = false;
    pendingScrollToEndRef.current = false;

    if (!activeChatId) {
      setMessages([]);
      return;
    }

    setLoadingMessages(true);
    setError(null);
    void fetchChatMessages(activeChatId)
      .then((data) => {
        const merged = mergeFetchedMessagesWithOptimistic(data, activeChatId);
        pendingScrollToEndRef.current = true;
        setMessages(merged);
        setStreamActivity(hasPendingAssistantReply(merged) ? "thinking" : "ready");
      })
      .catch((loadError: unknown) => {
        setError(loadError instanceof Error ? loadError.message : "Unable to load messages");
      })
      .finally(() => {
        setLoadingMessages(false);
      });
  }, [activeChatId]);

  useEffect(() => {
    if (!activeChatId || sending || streamingAssistant || !hasPendingAssistantReply(messages)) {
      return;
    }

    setStreamActivity("thinking");
    const intervalId = window.setInterval(() => {
      const targetChatId = activeChatId;
      void fetchChatMessages(targetChatId)
        .then((data) => {
          if (activeChatIdRef.current !== targetChatId) {
            return;
          }
          const merged = mergeFetchedMessagesWithOptimistic(data, targetChatId);
          if (hasPendingAssistantReply(messages) && !hasPendingAssistantReply(merged)) {
            pendingAssistantScrollMessageIdRef.current = latestAssistantMessageId(merged);
          }
          setMessages(merged);
          setStreamActivity(hasPendingAssistantReply(merged) ? "thinking" : "ready");
        })
        .catch(() => {
          // Ignore transient polling errors while waiting for completion.
        });
    }, 2500);

    return () => {
      window.clearInterval(intervalId);
    };
  }, [activeChatId, messages, sending, streamingAssistant]);

  useEffect(() => {
    if (!requestedChatId) {
      return;
    }
    if (!chats.some((chat) => chat.id === requestedChatId)) {
      void loadChats().catch((loadError: unknown) => {
        setError(loadError instanceof Error ? loadError.message : "Unable to load chats");
      });
      return;
    }
    setActiveChatId(requestedChatId);
  }, [requestedChatId, chats]);

  useEffect(() => {
    const pendingAssistantMessageId = pendingAssistantScrollMessageIdRef.current;
    if (pendingAssistantMessageId) {
      const element = messageElementRefs.current.get(pendingAssistantMessageId);
      if (element) {
        pendingAssistantScrollMessageIdRef.current = null;
        pendingStreamingStartScrollRef.current = false;
        scrollChatTargetIntoView(element, "start");
        return;
      }
    }

    if (pendingScrollToEndRef.current) {
      pendingScrollToEndRef.current = false;
      scrollChatTargetIntoView(messagesEndRef.current, "end");
      return;
    }

    if (pendingStreamingStartScrollRef.current && (streamActivity !== "ready" || streamingAssistant)) {
      pendingStreamingStartScrollRef.current = false;
      scrollChatTargetIntoView(streamingAssistantRef.current, "start");
    }
  }, [messages, streamingAssistant, streamActivity]);

  function mergeFetchedMessagesWithOptimistic(fetched: ChatMessage[], chatId: string): ChatMessage[] {
    const optimistic = optimisticUserMessageRef.current;
    if (!optimistic || optimistic.chatId !== chatId) {
      return fetched;
    }

    if (fetched.some((message) => message.id === optimistic.messageId)) {
      optimisticUserMessageRef.current = null;
      return fetched;
    }

    const normalizeForComparison = (value: string): string => value.trim().replace(/\s+/g, " ").toLowerCase();
    const optimisticNormalized = normalizeForComparison(optimistic.message.content);
    const recentUserMessages = fetched.filter((message) => message.role === "user").slice(-5);
    const optimisticAlreadyPersisted = recentUserMessages.some(
      (message) => normalizeForComparison(message.content) === optimisticNormalized
    );
    if (optimisticAlreadyPersisted) {
      optimisticUserMessageRef.current = null;
      return fetched;
    }

    return [...fetched, optimistic.message];
  }

  function upsertChat(chat: ChatSession): void {
    setChats((current) => {
      const index = current.findIndex((item) => item.id === chat.id);
      if (index === -1) {
        return sortChatsForSidebar([chat, ...current]);
      }
      const copy = [...current];
      copy[index] = chat;
      return sortChatsForSidebar(copy);
    });
  }

  function handleStreamEvent(event: ChatStreamEvent, streamToken: number, streamChatId: string): void {
    if (streamToken !== streamTokenRef.current) {
      return;
    }
    if (activeChatIdRef.current !== streamChatId) {
      return;
    }

    if (event.type === "error") {
      setError(event.error);
      setStreamActivity("ready");
      setStreamingAssistant("");
      return;
    }
    if (event.type === "user_message") {
      upsertChat(event.session);
      setMessages((current) => {
        const optimistic = optimisticUserMessageRef.current;
        if (optimistic && optimistic.chatId === streamChatId) {
          const optimisticIndex = current.findIndex((message) => message.id === optimistic.messageId);
          if (optimisticIndex >= 0) {
            const next = [...current];
            next[optimisticIndex] = event.message;
            optimisticUserMessageRef.current = null;
            return next;
          }
        }
        if (current.some((message) => message.id === event.message.id)) {
          optimisticUserMessageRef.current = null;
          return current;
        }
        optimisticUserMessageRef.current = null;
        return [...current, event.message];
      });
      return;
    }
    if (event.type === "status") {
      if (event.status === "web_search") {
        setStreamActivity("web_search");
      } else if (event.status === "ready") {
        setStreamActivity("ready");
      } else {
        setStreamActivity("thinking");
      }
      return;
    }
    if (event.type === "assistant_delta") {
      setStreamActivity("thinking");
      setStreamingAssistant((current) => `${current}${event.delta}`);
      return;
    }
    if (event.type === "assistant_message") {
      upsertChat(event.session);
      setStreamActivity("ready");
      setStreamingAssistant("");
      pendingAssistantScrollMessageIdRef.current = event.message.id;
      pendingStreamingStartScrollRef.current = false;
      setMessages((current) => {
        if (current.some((message) => message.id === event.message.id)) {
          return current;
        }
        return [...current, event.message];
      });
      return;
    }
    if (event.type === "done") {
      setStreamActivity("ready");
      setStreamingAssistant("");
    }
  }

  async function handleCreateChat(mode: SelectableMode = composerMode): Promise<void> {
    if (creatingChat) {
      return;
    }

    setCreatingChat(true);
    setError(null);
    try {
      const title = mode === "general" ? "New Chat" : `${MODE_LABELS[mode]} Chat`;
      const selectedModel = resolveModelForNewChat();
      const created = await createChatSession(title, mode, selectedModel);
      setChats((current) => sortChatsForSidebar([created, ...current]));
      setActiveChatId(created.id);
      setComposerMode(mode);
      setComposerModel(created.model ?? selectedModel);
      setMessages([]);
      setPrompt("");
      navigate(`/chat?chatId=${created.id}`);
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : "Unable to create chat");
    } finally {
      setCreatingChat(false);
    }
  }

  async function handleModeChange(nextMode: SelectableMode): Promise<void> {
    setComposerMode(nextMode);
    if (!activeChat || updatingMode) {
      return;
    }

    if (toSelectableMode(activeChat.mode) === nextMode) {
      return;
    }

    setError(null);
    setUpdatingMode(true);
    try {
      const updated = await updateChatSession(activeChat.id, { mode: nextMode });
      upsertChat(updated);
    } catch (modeError) {
      setError(modeError instanceof Error ? modeError.message : "Unable to switch chat mode");
    } finally {
      setUpdatingMode(false);
    }
  }

  function handleUseToolBuilderExample(example: ToolBuilderPromptExample): void {
    setPrompt(example.prompt);
    setToolBuilderIntake({
      ...DEFAULT_TOOL_BUILDER_INTAKE,
      details: example.prompt
    });
  }

  function handleApplyToolBuilderIntake(): void {
    const generatedPrompt = buildToolBuilderIntakePrompt(toolBuilderIntake);
    setPrompt(generatedPrompt);
    setToolBuilderIntakeOpen(false);
  }

  async function handleModelChange(nextModel: string): Promise<void> {
    setComposerModel(nextModel);
    if (!activeChat || updatingModel) {
      return;
    }

    const currentModel = activeChat.model ?? defaultModel ?? availableModels[0] ?? null;
    if (!nextModel || currentModel === nextModel) {
      return;
    }

    setError(null);
    setUpdatingModel(true);
    try {
      const updated = await updateChatSession(activeChat.id, { model: nextModel });
      upsertChat(updated);
    } catch (modelError) {
      setError(modelError instanceof Error ? modelError.message : "Unable to switch model");
    } finally {
      setUpdatingModel(false);
    }
  }

  function clearPendingImages(): void {
    setPendingImages((current) => {
      for (const item of current) {
        URL.revokeObjectURL(item.previewUrl);
      }
      return [];
    });
    if (imageInputRef.current) {
      imageInputRef.current.value = "";
    }
  }

  function removePendingImage(index: number): void {
    setPendingImages((current) => {
      if (index < 0 || index >= current.length) {
        return current;
      }
      const next = [...current];
      const [removed] = next.splice(index, 1);
      if (removed) {
        URL.revokeObjectURL(removed.previewUrl);
      }
      return next;
    });
    if (imageInputRef.current) {
      imageInputRef.current.value = "";
    }
  }

  function validatePendingImageFile(file: File): string | null {
    if (!file.type.startsWith("image/")) {
      return "Please select only image files.";
    }
    if (file.size > 10 * 1024 * 1024) {
      return "Image is too large. Maximum size is 10MB.";
    }
    return null;
  }

  function attachImageFiles(files: File[]): void {
    if (!files.length) {
      return;
    }

    const remainingSlots = MAX_CHAT_IMAGE_ATTACHMENTS - pendingImages.length;
    if (remainingSlots <= 0) {
      setError(`You can attach up to ${MAX_CHAT_IMAGE_ATTACHMENTS} images per message.`);
      if (imageInputRef.current) {
        imageInputRef.current.value = "";
      }
      return;
    }

    const toAdd: PendingImageAttachment[] = [];
    let firstValidationError: string | null = null;
    for (const file of files) {
      if (toAdd.length >= remainingSlots) {
        break;
      }
      const validationError = validatePendingImageFile(file);
      if (validationError) {
        if (!firstValidationError) {
          firstValidationError = validationError;
        }
        continue;
      }
      toAdd.push({ file, previewUrl: URL.createObjectURL(file) });
    }

    if (!toAdd.length) {
      setError(firstValidationError ?? `You can attach up to ${MAX_CHAT_IMAGE_ATTACHMENTS} images per message.`);
      if (imageInputRef.current) {
        imageInputRef.current.value = "";
      }
      return;
    }

    setPendingImages((current) => [...current, ...toAdd]);
    if (files.length > remainingSlots) {
      setError(`Only ${MAX_CHAT_IMAGE_ATTACHMENTS} images can be attached per message.`);
    } else if (firstValidationError) {
      setError(firstValidationError);
    } else {
      setError(null);
    }
  }

  function firstImageFileFromClipboardData(data: DataTransfer | null): File | null {
    if (!data) {
      return null;
    }

    const files = Array.from(data.files || []);
    const fileMatch = files.find((file) => file.type.startsWith("image/"));
    if (fileMatch) {
      return fileMatch;
    }

    const items = Array.from(data.items || []);
    for (const item of items) {
      if (!item.type.startsWith("image/")) {
        continue;
      }
      const file = item.getAsFile();
      if (file) {
        return file;
      }
    }
    return null;
  }

  async function readImageFromNavigatorClipboard(): Promise<File | null> {
    if (!("clipboard" in navigator) || typeof navigator.clipboard.read !== "function") {
      return null;
    }

    try {
      const clipboardItems = await navigator.clipboard.read();
      for (const item of clipboardItems) {
        const imageType = item.types.find((type) => type.startsWith("image/"));
        if (!imageType) {
          continue;
        }
        const blob = await item.getType(imageType);
        const extensionRaw = imageType.split("/")[1] || "png";
        const extension = extensionRaw.replace(/[^a-z0-9.+-]/gi, "") || "png";
        return new File([blob], `pasted-image-${Date.now()}.${extension}`, {
          type: imageType,
          lastModified: Date.now()
        });
      }
    } catch {
      return null;
    }

    return null;
  }

  function attachPastedImage(file: File): void {
    const hasName = file.name && file.name.trim().length > 0;
    const normalized = hasName
      ? file
      : new File([file], `pasted-image-${Date.now()}.png`, {
          type: file.type || "image/png",
          lastModified: Date.now()
        });
    attachImageFiles([normalized]);
  }

  function handleImageSelection(event: ChangeEvent<HTMLInputElement>): void {
    const files = Array.from(event.target.files || []);
    if (!files.length) {
      return;
    }
    attachImageFiles(files);
    if (imageInputRef.current) {
      imageInputRef.current.value = "";
    }
  }

  function handleComposerPaste(event: ReactClipboardEvent<HTMLTextAreaElement>): void {
    if (!activeChat || sending) {
      return;
    }

    const pastedFromEvent = firstImageFileFromClipboardData(event.clipboardData);
    if (pastedFromEvent) {
      event.preventDefault();
      attachPastedImage(pastedFromEvent);
      return;
    }

    const clipboardTypes = Array.from(event.clipboardData?.types || []).map((type) => type.toLowerCase());
    const maybeImageClipboard =
      clipboardTypes.includes("files") || clipboardTypes.some((type) => type.startsWith("image/"));
    if (!maybeImageClipboard) {
      return;
    }

    event.preventDefault();
    void (async () => {
      const fallbackImage = await readImageFromNavigatorClipboard();
      if (!fallbackImage) {
        setError("Clipboard image was detected but could not be read. Try using the Image button.");
        return;
      }
      attachPastedImage(fallbackImage);
    })();
  }

  async function submitPrompt(): Promise<void> {
    if (!activeChatId || sending || (!prompt.trim() && pendingImages.length === 0)) {
      return;
    }

    const streamChatId = activeChatId;
    const imagesToUpload = [...pendingImages];
    sendingChatIdRef.current = streamChatId;
    setError(null);
    setSending(true);
    setStopping(false);
    const streamToken = streamTokenRef.current + 1;
    const content = prompt.trim() || "Use the attached image as the primary visual reference and apply its UI style/layout.";
    const optimisticUserMessage: ChatMessage = {
      id: `optimistic-user-${streamChatId}-${streamToken}`,
      sessionId: streamChatId,
      role: "user",
      content,
      metadata: {},
      createdAt: new Date().toISOString()
    };
    optimisticUserMessageRef.current = {
      chatId: streamChatId,
      messageId: optimisticUserMessage.id,
      message: optimisticUserMessage
    };
    pendingScrollToEndRef.current = true;
    pendingStreamingStartScrollRef.current = true;
    setMessages((current) => [...current, optimisticUserMessage]);
    setThinkingPanelCycle((current) => current + 1);
    streamTokenRef.current = streamToken;
    setPrompt("");
    if (imagesToUpload.length > 0) {
      clearPendingImages();
    }

    try {
      setStreamActivity("thinking");
      setStreamingAssistant("");
      const selectedModel = resolveModelForRequest();
      const attachmentIds: string[] = [];
      for (const imageToUpload of imagesToUpload) {
        const uploaded = await uploadChatAttachment(streamChatId, imageToUpload.file);
        attachmentIds.push(uploaded.id);
      }
      await streamChatMessage(
        streamChatId,
        content,
        selectedModel,
        attachmentIds,
        (event) => handleStreamEvent(event, streamToken, streamChatId)
      );
      await loadChats();
    } catch (sendError) {
      setPrompt(content);
      streamTokenRef.current += 1;
      setMessages((current) => {
        const optimistic = optimisticUserMessageRef.current;
        if (!optimistic || optimistic.chatId !== streamChatId) {
          return current;
        }
        optimisticUserMessageRef.current = null;
        return current.filter((message) => message.id !== optimistic.messageId);
      });
      setStreamActivity("ready");
      setStreamingAssistant("");
      setError(sendError instanceof Error ? sendError.message : "Unable to send message");
    } finally {
      setSending(false);
      setStopping(false);
      sendingChatIdRef.current = null;
    }
  }

  async function handleStopStreaming(): Promise<void> {
    if (!sending || stopping) {
      return;
    }
    const targetSessionId = sendingChatIdRef.current ?? activeChatId;
    if (!targetSessionId) {
      return;
    }

    setStopping(true);
    setError(null);
    try {
      await stopChatMessageStream(targetSessionId);
    } catch (stopError) {
      setError(stopError instanceof Error ? stopError.message : "Unable to stop response");
      setStopping(false);
    }
  }

  async function handleSend(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    await submitPrompt();
  }

  function handleNextArticle(): void {
    setActiveArticleIndex((current) => clampIndex(current + 1, KNOWLEDGE_ARTICLES.length));
  }

  function handlePreviousArticle(): void {
    setActiveArticleIndex((current) => clampIndex(current - 1, KNOWLEDGE_ARTICLES.length));
  }

  function handleCompanionModeChange(nextMode: ThinkingPanelMode): void {
    setThinkingPanelMode(nextMode);
    saveThinkingPanelMode(nextMode);
    if (nextMode === "none") {
      setCompanionOpen(false);
      setExpandedArticleOpen(false);
      return;
    }

    setCompanionOpen(true);
    if (nextMode === "insta") {
      setShortFeedState(createInitialShortFeedState(shortPool));
    }
    if (nextMode !== "knowledge") {
      setExpandedArticleOpen(false);
    }
  }

  function openArticleInNewWindow(article: KnowledgeArticle): void {
    if (isPhoneViewport || isStandalonePwa) {
      setExpandedArticleOpen(true);
      return;
    }

    const newWindow = window.open("", "_blank", "noopener,noreferrer");
    if (!newWindow) {
      setError("Popup blocked. Please allow popups for this site.");
      return;
    }

    const contentHtml = article.content
      .map(
        (paragraph) => `<p style=\"line-height:1.7;margin:0 0 12px;\">${escapeHtml(paragraph)}</p>`
      )
      .join("");

    newWindow.document.write(`<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
    <title>${escapeHtml(article.title)} | ToolHub Reader</title>
    <style>
      body { margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif; background: #11100d; color: #f9edcf; }
      main { max-width: 920px; margin: 0 auto; padding: 28px 18px 48px; }
      .meta { font-size: 12px; letter-spacing: .08em; text-transform: uppercase; color: #c8b485; }
      h1 { margin: 8px 0 14px; font-size: 30px; line-height: 1.2; }
      .source { margin-top: 20px; font-size: 14px; color: #d7be87; }
      a { color: #f1c66a; }
    </style>
  </head>
  <body>
    <main>
      <p class=\"meta\">${escapeHtml(article.company)} • ${escapeHtml(article.feature)} • ${escapeHtml(article.readTime)}</p>
      <h1>${escapeHtml(article.title)}</h1>
      ${contentHtml}
      <p class=\"source\">Source: <a href=\"${escapeHtml(article.sourceUrl)}\" target=\"_blank\" rel=\"noreferrer\">${escapeHtml(article.sourceUrl)}</a></p>
    </main>
  </body>
</html>`);
    newWindow.document.close();
  }

  const showCenteredWelcome = !loadingMessages && messages.length === 0 && !streamingAssistant && streamActivity === "ready";
  const thinkingActive = streamActivity !== "ready" || Boolean(streamingAssistant);
  const companionEnabled = thinkingPanelMode !== "none";
  const thinkingPanelContentMode = thinkingPanelMode === "none" ? null : thinkingPanelMode;
  const companionPanelStyleWidth = `min(${companionPanelWidth}px, 96vw)`;
  const activeArticle = KNOWLEDGE_ARTICLES[clampIndex(activeArticleIndex, KNOWLEDGE_ARTICLES.length)];
  const allowCompanionExternalOpen = !isPhoneViewport && !isStandalonePwa;
  return (
    <main className="chat-page-root relative flex h-full min-h-0 w-full min-w-0 max-w-full flex-col overflow-hidden lg:flex-row">
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <header className="chat-page-header shrink-0 flex items-center justify-between gap-3 border-b border-amber/15 px-2 pb-3 lg:px-4">
          <div>
            <p className="text-[11px] uppercase tracking-[0.18em] text-muted">Chat</p>
            <h1 className="mt-1 text-xl font-medium text-[color:var(--text-main)]">{activeChat?.title ?? "New chat"}</h1>
          </div>
          <div className="flex items-center gap-2">
            <span className="rounded-full bg-black/45 px-3 py-1 text-xs text-muted">
              {streamActivity === "web_search" ? "Searching web" : streamActivity === "thinking" ? "Thinking" : "Ready"}
            </span>
            <button
              type="button"
              onClick={() => void handleCreateChat(composerMode)}
              disabled={creatingChat}
              className="btn-ghost px-3 py-1.5 text-xs"
            >
              {creatingChat ? "Creating..." : "New chat"}
            </button>
          </div>
        </header>

        <section className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          <div className="min-w-0 flex-1 overflow-x-hidden overflow-y-auto px-2 pb-4 pt-5 lg:px-4">
            {loadingMessages && <p className="text-sm text-muted">Loading messages...</p>}

            {showCenteredWelcome && (
              <div className="flex h-full min-h-[46vh] flex-col items-center justify-center text-center">
                <h2 className="text-4xl font-medium tracking-tight text-[color:var(--text-main)] md:text-5xl">
                  {isToolBuilderMode ? "What should we build?" : "What can I help with?"}
                </h2>
                <p className="mt-3 max-w-2xl text-sm text-muted">
                  {isToolBuilderMode
                    ? "Use the Tool Builder Studio near the composer to start from sample prompts or a structured intake form."
                    : "Start a new chat and pick the mode below to guide how ToolHub should respond."}
                </p>
              </div>
            )}

            <div className="mx-auto w-full max-w-4xl min-w-0 space-y-4 overflow-x-hidden">
              {messages.map((message) => {
                const messageAttachments = parseMessageAttachments(message.metadata ?? {});
                const isCopied = copiedMessageId === message.id;
                return (
                  <div
                    key={message.id}
                    ref={(element) => {
                      if (element) {
                        messageElementRefs.current.set(message.id, element);
                      } else {
                        messageElementRefs.current.delete(message.id);
                      }
                    }}
                    className={`flex w-full min-w-0 ${message.role === "user" ? "justify-end" : "justify-start"}`}
                  >
                    <div
                      className={`group relative min-w-0 max-w-[88%] overflow-hidden px-3 py-2.5 ${
                        message.role === "user"
                          ? "rounded-3xl bg-amber/15 text-[color:var(--text-main)]"
                          : "text-[color:var(--text-main)]"
                      }`}
                    >
                      <ChatMarkdown content={message.content} isUser={message.role === "user"} />
                      {messageAttachments.length > 0 && (
                        <div className="mt-2 grid min-w-0 gap-2 sm:grid-cols-2">
                          {messageAttachments.map((attachment) => (
                            <a
                              key={attachment.id}
                              href={toAttachmentUrl(attachment.url)}
                              target="_blank"
                              rel="noreferrer"
                              className="block overflow-hidden rounded-xl border border-amber/25 bg-black/25"
                            >
                              <img
                                src={toAttachmentUrl(attachment.url)}
                                alt={attachment.fileName}
                                className="max-h-60 w-full object-cover"
                              />
                            </a>
                          ))}
                        </div>
                      )}
                      <div className="mt-2 flex items-center justify-between gap-2">
                        <p className="text-[11px] text-muted">{formatDate(message.createdAt)}</p>
                        <button
                          type="button"
                          onClick={() => void handleCopyMessage(message)}
                          className={`btn-ghost h-7 w-7 shrink-0 p-0 transition ${
                            isCopied
                              ? "border-mint/45 bg-mint/10 text-mint opacity-100"
                              : "opacity-70 sm:opacity-0 sm:group-hover:opacity-100 focus:opacity-100"
                          }`}
                          aria-label={isCopied ? "Copied" : "Copy message"}
                          title={isCopied ? "Copied" : "Copy message"}
                        >
                          {isCopied ? (
                            <svg viewBox="0 0 20 20" fill="none" className="mx-auto h-4 w-4" aria-hidden="true">
                              <path d="M4.5 10.25L8.1 13.85L15.5 6.45" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                            </svg>
                          ) : (
                            <svg viewBox="0 0 20 20" fill="none" className="mx-auto h-4 w-4" aria-hidden="true">
                              <rect x="7" y="3.5" width="9" height="12.5" rx="1.8" stroke="currentColor" strokeWidth="1.4" />
                              <path d="M4 12.5V5.8C4 4.81 4.81 4 5.8 4H12.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
                            </svg>
                          )}
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })}

              {(streamActivity !== "ready" || streamingAssistant) && (
                <div ref={streamingAssistantRef} className="flex w-full min-w-0 justify-start">
                  <div className="min-w-0 max-w-[88%] overflow-hidden px-3 py-2.5 text-[color:var(--text-main)]">
                    {streamingAssistant ? (
                      <ChatMarkdown content={streamingAssistant} />
                    ) : (
                      <p className="whitespace-pre-wrap break-words text-[15px] leading-relaxed [overflow-wrap:anywhere]">
                        {streamActivity === "web_search" ? "Searching web..." : "Thinking..."}
                      </p>
                    )}
                  </div>
                </div>
              )}

              <div ref={messagesEndRef} />
            </div>
          </div>

          <form className="chat-page-composer shrink-0 border-t border-amber/15 bg-[#0e0d0b]/88 px-2 pb-3 pt-3 backdrop-blur lg:px-4 lg:pb-4" onSubmit={handleSend}>
            <div className={`mx-auto w-full ${CHAT_COMPOSER_MAX_WIDTH_CLASS}`}>
              <div className="chat-page-modes mb-2 flex flex-wrap items-center gap-2">
                {MODE_OPTIONS.map((option) => {
                  const active = composerMode === option.value;
                  return (
                    <button
                      key={option.value}
                      type="button"
                      onClick={() => void handleModeChange(option.value)}
                      disabled={!activeChat || updatingMode}
                      className={`rounded-full px-3 py-1 text-xs font-medium transition ${
                        active
                          ? "bg-amber/20 text-[color:var(--text-main)]"
                          : "bg-black/40 text-muted hover:bg-black/60 hover:text-[color:var(--text-main)]"
                      } disabled:cursor-not-allowed disabled:opacity-60`}
                    >
                      {option.label}
                    </button>
                  );
                })}
                {updatingMode && <span className="text-[11px] text-muted">Switching mode...</span>}
                {isToolBuilderMode && (
                  <button
                    type="button"
                    onClick={() => {
                      setToolBuilderStudioManualOverride(true);
                      setToolBuilderStudioVisible((current) => !current);
                    }}
                    disabled={!activeChat}
                    className="rounded-full border border-amber/30 bg-black/35 px-3 py-1 text-[11px] uppercase tracking-[0.14em] text-amber/80 transition hover:border-amber/45 hover:text-amber disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {toolBuilderStudioVisible ? "Hide Studio" : "Show Studio"}
                  </button>
                )}
                <div className="ml-auto flex items-center gap-2">
                  <label htmlFor="chat-companion-select" className="text-[11px] uppercase tracking-[0.14em] text-muted">
                    Companion
                  </label>
                  <select
                    id="chat-companion-select"
                    value={thinkingPanelMode}
                    onChange={(event) => handleCompanionModeChange(event.target.value as ThinkingPanelMode)}
                    className="rounded-full border border-amber/30 bg-black/40 px-3 py-1 text-xs text-[color:var(--text-main)] outline-none"
                  >
                    <option value="none">None</option>
                    <option value="insta">Shorts</option>
                    <option value="knowledge">Reads</option>
                  </select>
                  <label htmlFor="chat-model-select" className="text-[11px] uppercase tracking-[0.14em] text-muted">
                    Model
                  </label>
                  <select
                    id="chat-model-select"
                    value={composerModel ?? ""}
                    onChange={(event) => void handleModelChange(event.target.value)}
                    disabled={!activeChat || sending || availableModels.length === 0 || updatingModel}
                    className="rounded-full border border-amber/30 bg-black/40 px-3 py-1 text-xs text-[color:var(--text-main)] outline-none disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {availableModels.length === 0 ? (
                      <option value="">Default</option>
                    ) : (
                      availableModels.map((modelOption) => (
                        <option key={modelOption} value={modelOption}>
                          {modelOption}
                        </option>
                      ))
                    )}
                  </select>
                  {updatingModel && <span className="text-[11px] text-muted">Saving model...</span>}
                </div>
              </div>

              {isToolBuilderMode && toolBuilderStudioVisible && (
                <div className="mb-3 rounded-[1.6rem] border border-amber/18 bg-[radial-gradient(circle_at_top,#21180f_0%,#14110d_56%,#0d0b09_100%)] p-3 shadow-[0_24px_70px_-36px_rgba(0,0,0,0.92)]">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="max-w-2xl">
                      <p className="text-[11px] uppercase tracking-[0.18em] text-amber/70">Tool Builder Studio</p>
                      <p className="mt-1 text-sm text-[color:var(--text-main)]">
                        Make the prompt easier to write with examples or a structured intake form.
                      </p>
                      <p className="mt-1 text-xs text-muted">
                        After you send it, Tool Builder still asks clarifying questions if needed and refines the brief into a model-friendly build request before the build starts.
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => setToolBuilderIntakeOpen(true)}
                      disabled={!activeChat || sending}
                      className="btn-primary px-4 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      Intake Form
                    </button>
                  </div>
                  <div className="mt-3 flex gap-2 overflow-x-auto pb-1">
                    {TOOL_BUILDER_PROMPT_EXAMPLES.map((example) => (
                      <button
                        key={example.id}
                        type="button"
                        onClick={() => handleUseToolBuilderExample(example)}
                        disabled={!activeChat || sending}
                        className="min-w-[220px] rounded-[1.2rem] border border-amber/15 bg-black/25 p-3 text-left transition hover:border-amber/35 hover:bg-black/35 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        <div className="flex flex-wrap gap-1.5">
                          {example.tags.map((tag) => (
                            <span
                              key={`${example.id}-${tag}`}
                              className="rounded-full border border-amber/15 bg-amber/10 px-2 py-0.5 text-[10px] uppercase tracking-[0.14em] text-amber/80"
                            >
                              {tag}
                            </span>
                          ))}
                        </div>
                        <p className="mt-2 text-sm font-medium text-[color:var(--text-main)]">{example.title}</p>
                        <p className="mt-1 text-xs leading-5 text-muted">{example.summary}</p>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {pendingImages.length > 0 && (
                <div className="mb-2 rounded-2xl border border-amber/25 bg-black/35 px-3 py-2">
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <p className="text-xs text-muted">
                      {pendingImages.length} / {MAX_CHAT_IMAGE_ATTACHMENTS} images selected
                    </p>
                    <button type="button" onClick={clearPendingImages} className="btn-ghost px-2 py-1 text-xs">
                      Clear all
                    </button>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {pendingImages.map((pendingImage, index) => (
                      <div key={`${pendingImage.file.name}-${pendingImage.file.size}-${index}`} className="flex min-w-[220px] items-center gap-3 rounded-xl border border-amber/20 bg-black/35 px-2 py-2">
                        <img src={pendingImage.previewUrl} alt={pendingImage.file.name} className="h-14 w-14 rounded-lg object-cover" />
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-xs text-[color:var(--text-main)]">{pendingImage.file.name}</p>
                          <p className="text-[11px] text-muted">{Math.max(1, Math.round(pendingImage.file.size / 1024))} KB</p>
                        </div>
                        <button type="button" onClick={() => removePendingImage(index)} className="btn-ghost px-2 py-1 text-xs">
                          Remove
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <input ref={imageInputRef} type="file" accept="image/*" multiple className="hidden" onChange={handleImageSelection} />

              <div className="flex items-end gap-2 rounded-3xl border border-amber/22 bg-[#1a1813]/90 px-3 py-2">
                <textarea
                  ref={promptTextareaRef}
                  value={prompt}
                  onChange={(event) => setPrompt(event.target.value)}
                  onPaste={handleComposerPaste}
                  onKeyDown={(event) => {
                    if (event.nativeEvent.isComposing) {
                      return;
                    }
                    if (shouldSubmitOnEnter && event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      void submitPrompt();
                    }
                  }}
                  rows={1}
                  className="min-h-[44px] max-h-[240px] w-full resize-none overflow-y-hidden bg-transparent py-2 text-sm leading-6 text-[color:var(--text-main)] outline-none placeholder:text-muted/90"
                  placeholder="Ask anything"
                  disabled={!activeChat || sending}
                />
                <button
                  type="button"
                  onClick={() => imageInputRef.current?.click()}
                  disabled={!activeChat || sending || pendingImages.length >= MAX_CHAT_IMAGE_ATTACHMENTS}
                  className="btn-ghost rounded-full px-3 py-2 text-xs disabled:cursor-not-allowed disabled:opacity-60"
                >
                  Image
                </button>
                {sending ? (
                  <button
                    type="button"
                    onClick={() => void handleStopStreaming()}
                    disabled={stopping}
                    className="rounded-full border border-coral/40 bg-coral/15 px-4 py-2 text-sm text-coral transition hover:border-coral/70 hover:bg-coral/25 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {stopping ? "Stopping..." : "Stop"}
                  </button>
                ) : (
                  <button
                    type="submit"
                    disabled={!activeChat || (!prompt.trim() && pendingImages.length === 0)}
                    className="btn-primary rounded-full px-4 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    Send
                  </button>
                )}
              </div>

              <p className="mt-2 text-xs text-muted">
                {shouldSubmitOnEnter ? "Enter to send, Shift + Enter for a new line." : "Enter for a new line. Tap Send to submit."}
                {" "}
                Paste an image from clipboard to attach (up to {MAX_CHAT_IMAGE_ATTACHMENTS} images).
              </p>
            </div>
            {error && (
              <div className={`mx-auto mt-2 w-full ${CHAT_COMPOSER_MAX_WIDTH_CLASS} border border-coral/35 bg-coral/10 px-3 py-2 text-sm text-coral`}>
                {error}
              </div>
            )}
          </form>
        </section>
      </div>

      <ToolBuilderIntakeModal
        open={toolBuilderIntakeOpen}
        value={toolBuilderIntake}
        onChange={setToolBuilderIntake}
        onClose={() => setToolBuilderIntakeOpen(false)}
        onApply={handleApplyToolBuilderIntake}
      />

      {companionEnabled && companionOpen && thinkingPanelContentMode && (
        <>
          <button
            type="button"
            aria-label="Close companion panel backdrop"
            onClick={() => setCompanionOpen(false)}
            className="fixed inset-0 z-[46] bg-black/60 lg:hidden"
          />
          <aside
            ref={companionAsideRef}
            className="chat-companion-aside fixed inset-y-0 right-0 z-50 flex min-h-0 overflow-hidden border-l border-amber/20 bg-black/45 text-[color:var(--text-main)] shadow-[0_18px_48px_-24px_rgba(0,0,0,0.9)] backdrop-blur-xl lg:relative lg:z-auto lg:h-full lg:shrink-0 lg:shadow-none"
            style={{ width: companionPanelStyleWidth }}
          >
            <button
              type="button"
              onPointerDown={handleCompanionResizeStart}
              className="absolute left-0 top-0 hidden h-full w-4 -translate-x-1/2 cursor-col-resize touch-none lg:block"
              aria-label="Resize thinking companion panel"
            >
              <span className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-amber/25" />
              <span className="absolute left-1/2 top-1/2 flex h-16 w-6 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border border-amber/20 bg-[#16130d]/90 shadow-[0_10px_24px_-18px_rgba(0,0,0,0.9)]">
                <span className="h-8 w-1 rounded-full bg-amber/55" />
              </span>
            </button>
            <ThinkingCompanionPanel
              mode={thinkingPanelContentMode}
              activeShort={activeShort}
              preloadedShort={preloadedShort}
              article={activeArticle}
              onMinimize={() => setCompanionOpen(false)}
              onOpenShort={openActiveShort}
              onNextShort={handleNextShort}
              onPreviousShort={handlePreviousShort}
              onShortRailWheel={handleShortRailWheel}
              onNextArticle={handleNextArticle}
              onPreviousArticle={handlePreviousArticle}
              onOpenArticleExpanded={() => setExpandedArticleOpen(true)}
              onOpenArticleWindow={() => openArticleInNewWindow(activeArticle)}
              onModeChange={handleCompanionModeChange}
              allowExternalOpen={allowCompanionExternalOpen}
            />
          </aside>
        </>
      )}

      {companionEnabled && !companionOpen && (
        <button
          type="button"
          onClick={() => setCompanionOpen(true)}
          className="fixed bottom-3 right-3 z-30 flex items-center gap-2 rounded-full border border-amber/30 bg-black/75 px-3 py-2 text-xs text-[color:var(--text-main)] shadow-[0_14px_30px_-18px_rgba(0,0,0,0.85)] transition hover:border-amber/45 lg:absolute lg:bottom-auto lg:right-2 lg:top-1/2 lg:-translate-y-1/2 lg:rounded-l-xl lg:rounded-r-none"
          aria-label="Open thinking companion"
        >
          <svg
            viewBox="0 0 24 24"
            width="14"
            height="14"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.9"
            aria-hidden="true"
          >
            <path d="M5 12h14" />
            <path d="M12 5l7 7-7 7" />
          </svg>
          <span>{thinkingActive ? "Companion" : "Open companion"}</span>
        </button>
      )}

      {expandedArticleOpen && thinkingPanelContentMode === "knowledge" && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 px-4 py-6">
          <div className="flex h-full max-h-[92vh] w-full max-w-4xl flex-col rounded-2xl border border-amber/30 bg-[#0d0c0a]">
            <div className="flex items-center justify-between gap-2 border-b border-amber/18 px-4 py-3">
              <div>
                <p className="text-[11px] uppercase tracking-[0.16em] text-muted">
                  Expanded Reader
                </p>
                <h3 className="mt-1 text-lg font-semibold text-[color:var(--text-main)]">
                  {activeArticle.title}
                </h3>
              </div>
              <div className="flex items-center gap-2">
                {allowCompanionExternalOpen && (
                  <button
                    type="button"
                    onClick={() => openArticleInNewWindow(activeArticle)}
                    className="btn-ghost px-3 py-1.5 text-xs"
                  >
                    Open in new window
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => setExpandedArticleOpen(false)}
                  className="btn-ghost px-3 py-1.5 text-xs"
                >
                  Close
                </button>
              </div>
            </div>

            <div
              className="min-h-0 flex-1 overflow-y-auto overscroll-y-contain px-5 py-4"
              onWheel={(event) => event.stopPropagation()}
            >
              <p className="text-[11px] uppercase tracking-[0.14em] text-[#d4c398]">
                {activeArticle.company} • {activeArticle.feature} • {activeArticle.readTime}
              </p>
              <div className="mt-3 space-y-3">
                {activeArticle.content.map((paragraph) => (
                  <p
                    key={paragraph}
                    className="text-[15px] leading-7 text-[color:var(--text-main)]"
                  >
                    {paragraph}
                  </p>
                ))}
              </div>
              <a
                href={activeArticle.sourceUrl}
                target="_blank"
                rel="noreferrer"
                className="mt-5 inline-flex text-sm text-amber underline decoration-amber/60 underline-offset-4"
              >
                Open original source
              </a>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}

export default function ChatPage() {
  return <ChatPageContent />;
}
