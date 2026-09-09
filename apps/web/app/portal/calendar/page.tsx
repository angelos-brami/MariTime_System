import { readPortalJson } from "@/lib/portal-api";
import type { MaritimeCalendar } from "@/lib/portal-types";

function formatTime(value: string | null): string {
  if (!value) return "Open-ended";
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Europe/Athens",
  }).format(new Date(value));
}

export default async function CalendarPage() {
  const calendar = await readPortalJson<MaritimeCalendar>("/api/v1/portal/calendar");
  return (
    <main className="portalMain calendarPage">
      <header className="portalPageHeading"><p className="eyebrow">Dated operational watch</p><h1>Port &amp; strike calendar</h1><p>Analyst-published strike windows, closures, exercises, weather windows, and regulatory deadlines. Each change creates a new immutable version.</p></header>
      <section className="calendarLedger">
        {calendar.results.map((item) => (
          <article key={item.id}>
            <div className="calendarDate"><strong>{formatTime(item.starts_at)}</strong><span>to {formatTime(item.ends_at)}</span></div>
            <div><p className="eyebrow">{item.event_type.replaceAll("_", " ")} · {item.status} · v{item.version_no}</p><h2>{item.title}</h2><p>{item.public_note}</p><small>{item.ports.map((port) => `${port.name} (${port.unlocode})`).join(" · ") || item.corridor.replaceAll("_", " ")}</small></div>
            <code>{item.content_hash.slice(0, 16)}</code>
          </article>
        ))}
        {!calendar.results.length ? <p className="portalEmpty">No published calendar items in this window.</p> : null}
      </section>
    </main>
  );
}
