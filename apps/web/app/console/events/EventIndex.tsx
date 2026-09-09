"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import type { OpenEvent } from "../types";

function label(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export default function EventIndex() {
  const [events, setEvents] = useState<OpenEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void fetch("/api/console/events", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error(`Events could not be loaded (${response.status})`);
        setEvents((await response.json()) as OpenEvent[]);
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : "Events could not be loaded");
      });
  }, []);

  return (
    <main className="eventIndexShell">
      <header className="eventIndexHeader">
        <div className="consoleIdentity">
          <span className="wordmarkMark">EM</span>
          <div>
            <p className="eyebrow">East Med desk</p>
            <h1>Open event workspaces</h1>
          </div>
        </div>
        <nav className="consoleHeaderNav" aria-label="Console navigation">
          <Link href="/console" className="consoleNavLink">
            Triage
          </Link>
          <Link href="/console/alerts" className="consoleNavLink">
            Alerts
          </Link>
          <Link href="/console/extraction" className="consoleNavLink">
            Extraction
          </Link>
          <Link href="/console/briefs" className="consoleNavLink">
            Brief
          </Link>
          <Link href="/console/quality" className="consoleNavLink">
            Quality
          </Link>
        </nav>
      </header>
      {error ? <p className="consoleBanner consoleBanner--error">{error}</p> : null}
      <section className="eventIndexGrid" aria-label="Open events">
        {events.map((event) => (
          <Link key={event.id} href={`/console/events/${event.id}`} className="eventIndexCard">
            <div className="eventIndexTopline">
              <span className={`severityBadge severityBadge--${event.severity}`}>S{event.severity}</span>
              <span>{event.status}</span>
            </div>
            <h2>{event.title}</h2>
            <p>
              {label(event.corridor)} · {label(event.event_type)}
            </p>
            <strong>Open workspace →</strong>
          </Link>
        ))}
        {!events.length && !error ? <p className="emptyState">Loading open events…</p> : null}
      </section>
    </main>
  );
}
