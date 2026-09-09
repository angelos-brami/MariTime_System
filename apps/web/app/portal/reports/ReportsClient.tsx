"use client";

import { useState } from "react";

import type { PortalBoardEvent } from "@/lib/portal-types";

type ReportReceipt = {
  id: string;
  event_version_id: string;
  requested_timestamp: string;
  version_content_hash: string;
  content_hash: string;
};

function localInput(): string {
  const date = new Date();
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

async function detail(response: Response): Promise<string> {
  const body = (await response.json().catch(() => ({}))) as { detail?: string };
  return body.detail ?? `Report request failed (${response.status})`;
}

export default function ReportsClient({ events }: { events: PortalBoardEvent[] }) {
  const [eventId, setEventId] = useState(events[0]?.event_id ?? "");
  const [timestamp, setTimestamp] = useState(localInput());
  const [report, setReport] = useState<ReportReceipt | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const generate = async () => {
    const event = events.find((item) => item.event_id === eventId);
    if (!event) return;
    setBusy(true); setError(null); setReport(null);
    try {
      const response = await fetch("/api/portal/reports/state-of-knowledge", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          event_id: event.event_id,
          requested_timestamp: new Date(timestamp).toISOString(),
          requested_by: "portal-subscriber",
        }),
      });
      if (!response.ok) throw new Error(await detail(response));
      setReport((await response.json()) as ReportReceipt);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Report generation failed");
    } finally { setBusy(false); }
  };

  return (
    <main className="portalMain reportQueryPage">
      <header className="portalPageHeading"><p className="eyebrow">Desk Pro timestamp query</p><h1>State of knowledge</h1><p>Select an event and timestamp to reproduce the exact published version then available, including version hashes and rights-safe evidence citations.</p></header>
      <section className="reportQueryForm">
        <label><span>Published event</span><select value={eventId} onChange={(event) => setEventId(event.target.value)}>{events.map((event) => <option key={event.event_id} value={event.event_id}>{event.title}</option>)}</select></label>
        <label><span>State at</span><input type="datetime-local" value={timestamp} onChange={(event) => setTimestamp(event.target.value)} /></label>
        <button type="button" disabled={busy || !eventId || !timestamp} onClick={() => void generate()}>{busy ? "Generating…" : "Generate immutable PDF"}</button>
        {error ? <p className="consoleBanner consoleBanner--error" role="alert">{error}</p> : null}
        {report ? <article className="reportReceipt"><strong>Report ready</strong><span>Version {report.event_version_id}</span><code>{report.version_content_hash}</code><a href={`/api/portal/reports/state-of-knowledge/${encodeURIComponent(report.id)}`}>Download PDF</a></article> : null}
      </section>
    </main>
  );
}
