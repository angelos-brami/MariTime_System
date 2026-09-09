"use client";

import Link from "next/link";
import {
  type FormEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

import type { Corridor, EventType, OpenEvent, TriageItem } from "./types";

type ActionMode = "attach" | "new_event" | "dismiss" | null;

const corridors: { value: Corridor; label: string }[] = [
  { value: "hormuz_gulf", label: "Hormuz / Gulf" },
  { value: "red_sea_bem_suez", label: "Red Sea / BEM / Suez" },
  { value: "east_med", label: "East Mediterranean" },
  { value: "port_specific", label: "Port specific" },
];

const eventTypes: { value: EventType; label: string }[] = [
  { value: "security_incident", label: "Security incident" },
  { value: "port_disruption", label: "Port disruption" },
  { value: "labor_action", label: "Labor action" },
  { value: "regulatory_sanctions", label: "Regulatory / sanctions" },
  { value: "weather_hazard", label: "Weather hazard" },
  { value: "infrastructure", label: "Infrastructure" },
  { value: "insurance_market", label: "Insurance market" },
  { value: "navigation_warning", label: "Navigation warning" },
];

function labelFor(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatTime(value: string | null): string {
  if (!value) return "Time unavailable";
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(new Date(value));
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function HighlightedText({ item }: { item: TriageItem }): ReactNode {
  const terms = item.detected_ports.flatMap((port) => [
    port.matched_alias,
    port.name,
    port.unlocode,
  ]);
  const uniqueTerms = [...new Set(terms.filter(Boolean))].sort(
    (left, right) => right.length - left.length,
  );
  if (!uniqueTerms.length) return item.text;
  const pattern = new RegExp(`(${uniqueTerms.map(escapeRegExp).join("|")})`, "giu");
  return item.text.split(pattern).map((part, index) =>
    uniqueTerms.some((term) => term.localeCompare(part, undefined, { sensitivity: "base" }) === 0) ? (
      <mark key={`${part}-${index}`}>{part}</mark>
    ) : (
      part
    ),
  );
}

async function responseDetail(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: string };
    return payload.detail ?? `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

export default function TriageConsole() {
  const [items, setItems] = useState<TriageItem[]>([]);
  const [events, setEvents] = useState<OpenEvent[]>([]);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [actionMode, setActionMode] = useState<ActionMode>(null);
  const [submitting, setSubmitting] = useState(false);
  const [reviewer, setReviewer] = useState("desk-analyst");
  const [tierFilter, setTierFilter] = useState("");
  const [corridorFilter, setCorridorFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");

  const selected = items[selectedIndex] ?? null;

  const fetchQueue = useCallback(async (): Promise<[TriageItem[], OpenEvent[]]> => {
    const params = new URLSearchParams();
    if (tierFilter) params.set("tier", tierFilter);
    if (corridorFilter) params.set("corridor", corridorFilter);
    if (typeFilter) params.set("event_type", typeFilter);
    const [queueResponse, eventsResponse] = await Promise.all([
      fetch(`/api/console/triage?${params}`, { cache: "no-store" }),
      fetch("/api/console/events", { cache: "no-store" }),
    ]);
    if (!queueResponse.ok) throw new Error(await responseDetail(queueResponse));
    if (!eventsResponse.ok) throw new Error(await responseDetail(eventsResponse));
    return [
      (await queueResponse.json()) as TriageItem[],
      (await eventsResponse.json()) as OpenEvent[],
    ];
  }, [corridorFilter, tierFilter, typeFilter]);

  const loadQueue = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [nextItems, nextEvents] = await fetchQueue();
      setItems(nextItems);
      setEvents(nextEvents);
      setSelectedIndex(0);
      setActionMode(null);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Queue could not be loaded");
    } finally {
      setLoading(false);
    }
  }, [fetchQueue]);

  useEffect(() => {
    let cancelled = false;
    void fetchQueue()
      .then(([nextItems, nextEvents]) => {
        if (cancelled) return;
        setItems(nextItems);
        setEvents(nextEvents);
        setSelectedIndex(0);
        setActionMode(null);
        setError(null);
      })
      .catch((loadError: unknown) => {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "Queue could not be loaded");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [fetchQueue]);

  const moveSelection = useCallback(
    (delta: number) => {
      setSelectedIndex((current) =>
        Math.min(Math.max(current + delta, 0), Math.max(items.length - 1, 0)),
      );
      setActionMode(null);
    },
    [items.length],
  );

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.matches("input, select, textarea, button")) return;
      if (event.key === "Escape") {
        setActionMode(null);
        return;
      }
      if (actionMode || !selected) return;
      const key = event.key.toLowerCase();
      if (key === "j") moveSelection(1);
      else if (key === "k") moveSelection(-1);
      else if (key === "a") setActionMode("attach");
      else if (key === "r") setActionMode("dismiss");
      else if (key === "e") setActionMode("new_event");
      else return;
      event.preventDefault();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [actionMode, moveSelection, selected]);

  const resolveItem = useCallback(
    async (payload: Record<string, unknown>, successMessage: string) => {
      if (!selected || !reviewer.trim()) return;
      setSubmitting(true);
      setError(null);
      window.localStorage.setItem("eastmed-reviewer", reviewer.trim());
      try {
        const response = await fetch(`/api/console/triage/${selected.id}/actions`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ ...payload, reviewer: reviewer.trim() }),
        });
        if (!response.ok) throw new Error(await responseDetail(response));
        setItems((current) => current.filter((item) => item.id !== selected.id));
        setSelectedIndex((current) => Math.max(0, Math.min(current, items.length - 2)));
        setActionMode(null);
        setNotice(successMessage);
      } catch (actionError) {
        setError(actionError instanceof Error ? actionError.message : "Action failed");
      } finally {
        setSubmitting(false);
      }
    },
    [items.length, reviewer, selected],
  );

  const safetyFlags = useMemo(() => {
    if (!selected) return [];
    return [
      selected.security_scan.quarantined ? "Quarantined" : null,
      selected.security_scan.injection_suspected ? "Injection pattern" : null,
    ].filter(Boolean) as string[];
  }, [selected]);

  return (
    <main className="triageShell">
      <header className="triageHeader">
        <div className="consoleIdentity">
          <span className="wordmarkMark">EM</span>
          <div>
            <p className="eyebrow">East Med desk</p>
            <h1>Triage control</h1>
          </div>
        </div>
        <nav className="consoleHeaderNav" aria-label="Console navigation">
          <Link href="/console" className="consoleNavLink">
            Dashboard
          </Link>
          <Link href="/console/events" className="consoleNavLink">
            Events
          </Link>
          <Link href="/console/approvals" className="consoleNavLink">
            Approvals
          </Link>
          <Link href="/console/extraction" className="consoleNavLink">
            Extraction
          </Link>
          <Link href="/console/alerts" className="consoleNavLink">
            Alerts
          </Link>
          <Link href="/console/briefs" className="consoleNavLink">
            Brief
          </Link>
          <Link href="/console/quality" className="consoleNavLink">
            Quality
          </Link>
        </nav>
        <div className="queueStatus" aria-live="polite">
          <strong>{items.length}</strong>
          <span>pending signals</span>
        </div>
        <label className="reviewerField">
          <span>Analyst</span>
          <input
            aria-label="Analyst name"
            value={reviewer}
            onChange={(event) => setReviewer(event.target.value)}
            maxLength={255}
          />
        </label>
      </header>

      <section className="filterBar" aria-label="Queue filters">
        <label>
          <span>Tier</span>
          <select value={tierFilter} onChange={(event) => setTierFilter(event.target.value)}>
            <option value="">All</option>
            {['A', 'B', 'C', 'D', 'E'].map((tier) => <option key={tier}>{tier}</option>)}
          </select>
        </label>
        <label>
          <span>Corridor</span>
          <select
            value={corridorFilter}
            onChange={(event) => setCorridorFilter(event.target.value)}
          >
            <option value="">All corridors</option>
            {corridors.map((corridor) => (
              <option key={corridor.value} value={corridor.value}>{corridor.label}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Suggested type</span>
          <select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}>
            <option value="">All types</option>
            {eventTypes.map((eventType) => (
              <option key={eventType.value} value={eventType.value}>{eventType.label}</option>
            ))}
          </select>
        </label>
        <button className="refreshButton" onClick={() => void loadQueue()} disabled={loading}>
          Refresh queue
        </button>
      </section>

      {error ? <div className="consoleBanner consoleBanner--error" role="alert">{error}</div> : null}
      {notice ? <div className="consoleBanner" role="status">{notice}</div> : null}

      <section className="triageWorkspace">
        <aside className="queueRail" aria-label="Pending records">
          {loading ? <p className="emptyState">Loading verified source records…</p> : null}
          {!loading && !items.length ? (
            <p className="emptyState">Queue clear for these filters.</p>
          ) : null}
          {items.map((item, index) => (
            <button
              className={`queueCard${index === selectedIndex ? " queueCard--active" : ""}`}
              key={item.id}
              onClick={() => {
                setSelectedIndex(index);
                setActionMode(null);
              }}
            >
              <span className={`tierBadge tierBadge--${item.source_tier}`}>{item.source_tier}</span>
              <span className="queueCardBody">
                <strong>{item.title || "Untitled source record"}</strong>
                <small>{item.source_name} · {formatTime(item.published_at)}</small>
              </span>
              <span className="queueIndex">{String(index + 1).padStart(2, "0")}</span>
            </button>
          ))}
        </aside>

        <article className="recordPane">
          {selected ? (
            <>
              <div className="recordMeta">
                <span className={`tierBadge tierBadge--${selected.source_tier}`}>
                  Tier {selected.source_tier}
                </span>
                <span>{selected.source_name}</span>
                <span>{selected.source_language.toUpperCase()}</span>
                <span>{selected.rights_basis}</span>
              </div>
              <h2>{selected.title || "Untitled source record"}</h2>
              <div className="recordTiming">
                <span>Published {formatTime(selected.published_at)} UTC</span>
                <span>Fetched {formatTime(selected.fetched_at)} UTC</span>
              </div>
              <p className="recordText"><HighlightedText item={selected} /></p>
              <a className="sourceLink" href={selected.url} target="_blank" rel="noreferrer">
                Open immutable source ↗
              </a>
            </>
          ) : (
            <div className="recordEmpty">
              <p className="eyebrow">No selection</p>
              <h2>The queue is clear.</h2>
              <p>Change filters or refresh to check for new source records.</p>
            </div>
          )}
        </article>

        <aside className="signalPane" aria-label="Detected signals and actions">
          {selected ? (
            <>
              <section>
                <p className="signalLabel">Gazetteer matches</p>
                <div className="chipGroup">
                  {selected.detected_ports.map((port) => (
                    <span className="chip chip--port" key={port.unlocode}>
                      {port.name} <small>{port.unlocode}</small>
                    </span>
                  ))}
                  {selected.detected_corridors.map((corridor) => (
                    <span className="chip" key={corridor}>{labelFor(corridor)}</span>
                  ))}
                  {!selected.detected_ports.length && !selected.detected_corridors.length ? (
                    <span className="mutedSignal">No deterministic location match</span>
                  ) : null}
                </div>
              </section>
              <section>
                <p className="signalLabel">Suggested event types</p>
                <div className="chipGroup">
                  {selected.suggested_event_types.map((eventType) => (
                    <span className="chip chip--type" key={eventType}>{labelFor(eventType)}</span>
                  ))}
                  {!selected.suggested_event_types.length ? (
                    <span className="mutedSignal">No keyword suggestion</span>
                  ) : null}
                </div>
              </section>
              <section>
                <p className="signalLabel">Safety scan</p>
                <div className="chipGroup">
                  {safetyFlags.length ? safetyFlags.map((flag) => (
                    <span className="chip chip--danger" key={flag}>{flag}</span>
                  )) : <span className="chip chip--safe">No flags</span>}
                </div>
              </section>

              <div className="actionStack">
                <button onClick={() => setActionMode("attach")}><kbd>A</kbd> Attach to event</button>
                <button onClick={() => setActionMode("new_event")}><kbd>E</kbd> New event</button>
                <button className="dismissButton" onClick={() => setActionMode("dismiss")}>
                  <kbd>R</kbd> Dismiss record
                </button>
              </div>

              {actionMode ? (
                <ActionPanel
                  mode={actionMode}
                  item={selected}
                  events={events}
                  submitting={submitting}
                  onCancel={() => setActionMode(null)}
                  onResolve={resolveItem}
                />
              ) : null}
            </>
          ) : null}
        </aside>
      </section>

      <footer className="shortcutBar">
        <span><kbd>J</kbd> next</span>
        <span><kbd>K</kbd> previous</span>
        <span><kbd>A</kbd> attach</span>
        <span><kbd>R</kbd> dismiss</span>
        <span><kbd>E</kbd> new event</span>
        <strong>{selected ? `${selectedIndex + 1} / ${items.length}` : "0 / 0"}</strong>
      </footer>
    </main>
  );
}

function ActionPanel({
  mode,
  item,
  events,
  submitting,
  onCancel,
  onResolve,
}: {
  mode: Exclude<ActionMode, null>;
  item: TriageItem;
  events: OpenEvent[];
  submitting: boolean;
  onCancel: () => void;
  onResolve: (payload: Record<string, unknown>, successMessage: string) => Promise<void>;
}) {
  const [eventId, setEventId] = useState(events[0]?.id ?? "");
  const [reason, setReason] = useState("");
  const [title, setTitle] = useState(item.title ?? "");
  const [eventType, setEventType] = useState<EventType>(
    item.suggested_event_types[0] ?? "security_incident",
  );
  const suggestedCorridor = item.detected_corridors.find(
    (corridor) => corridor !== "port_specific",
  );
  const [corridor, setCorridor] = useState<Corridor>(suggestedCorridor ?? "east_med");
  const [severity, setSeverity] = useState(1);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (mode === "attach") {
      void onResolve({ action: "attach", event_id: eventId, reason: reason || null }, "Record attached to event.");
    } else if (mode === "dismiss") {
      void onResolve({ action: "dismiss", reason }, "Record dismissed with an audit entry.");
    } else {
      void onResolve(
        { action: "new_event", title, event_type: eventType, corridor, severity },
        "Monitoring event created from source record.",
      );
    }
  };

  return (
    <form className="actionPanel" onSubmit={submit}>
      <div className="actionPanelHeading">
        <strong>{mode === "attach" ? "Attach to event" : mode === "dismiss" ? "Dismiss record" : "Create monitoring event"}</strong>
        <button type="button" onClick={onCancel} aria-label="Close action panel">×</button>
      </div>
      {mode === "attach" ? (
        <>
          <label>Open event
            <select value={eventId} onChange={(event) => setEventId(event.target.value)} required>
              <option value="" disabled>Select an event</option>
              {events.map((openEvent) => (
                <option key={openEvent.id} value={openEvent.id}>
                  S{openEvent.severity} · {openEvent.title}
                </option>
              ))}
            </select>
          </label>
          <label>Analyst note <span>optional</span>
            <textarea value={reason} onChange={(event) => setReason(event.target.value)} maxLength={2000} />
          </label>
        </>
      ) : null}
      {mode === "dismiss" ? (
        <label>Dismissal reason
          <textarea value={reason} onChange={(event) => setReason(event.target.value)} required maxLength={2000} autoFocus />
        </label>
      ) : null}
      {mode === "new_event" ? (
        <>
          <label>Factual title
            <input value={title} onChange={(event) => setTitle(event.target.value)} required minLength={3} maxLength={500} />
          </label>
          <label>Event type
            <select value={eventType} onChange={(event) => setEventType(event.target.value as EventType)}>
              {eventTypes.map((type) => <option key={type.value} value={type.value}>{type.label}</option>)}
            </select>
          </label>
          <label>Corridor
            <select value={corridor} onChange={(event) => setCorridor(event.target.value as Corridor)}>
              {corridors.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
          </label>
          <label>Severity
            <select value={severity} onChange={(event) => setSeverity(Number(event.target.value))}>
              {[1, 2, 3, 4].map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
        </>
      ) : null}
      <button className="commitButton" disabled={submitting || (mode === "attach" && !events.length)}>
        {submitting ? "Recording…" : "Confirm and audit"}
      </button>
    </form>
  );
}
