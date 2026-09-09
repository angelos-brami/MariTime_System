import Link from "next/link";

import { readPortalJson } from "@/lib/portal-api";
import type { PortalBoard } from "@/lib/portal-types";

function formatTime(value: string | null): string {
  if (!value) return "No active published event";
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Europe/Athens",
  }).format(new Date(value));
}

export default async function PortalBoardPage() {
  const board = await readPortalJson<PortalBoard>("/api/v1/portal/board");
  return (
    <main className="portalMain">
      <section className="portalHero">
        <div>
          <p className="eyebrow">Subscriber operational board</p>
          <h1>Live corridor intelligence, with the evidence trail intact.</h1>
        </div>
        <p>
          Only analyst-published event versions appear here. Confirmed, reported, and unknown
          information remain separated on every event record.
        </p>
      </section>
      <section className="portalBoardGrid" aria-label="Live corridor status">
        {board.corridors.map((corridor, index) => (
          <article className="portalCorridor" key={corridor.corridor}>
            <div className="cardTopline">
              <span className="corridorNumber">0{index + 1}</span>
              <span className={`status status--${corridor.operational_state}`}>
                <span aria-hidden="true" className="statusDot" />
                {corridor.operational_state}
              </span>
            </div>
            <h2>{corridor.label}</h2>
            <p className="portalTimestamp">Last published {formatTime(corridor.updated_at)}</p>
            <div className="portalEventStack">
              {corridor.events.length ? corridor.events.map((event) => (
                <Link href={`/portal/events/${encodeURIComponent(event.slug)}`} key={event.slug}>
                  <span>S{event.severity} · v{event.version_no}</span>
                  <strong>{event.title}</strong>
                  <small>{event.whats_changed || "Latest verified version"}</small>
                </Link>
              )) : <p className="portalEmpty">No active published events.</p>}
            </div>
          </article>
        ))}
      </section>
    </main>
  );
}
