import Link from "next/link";

import { readPortalJson } from "@/lib/portal-api";
import type { PortalArchive } from "@/lib/portal-types";

type ArchiveSearch = {
  q?: string;
  corridor?: string;
  event_type?: string;
  min_severity?: string;
};

export default async function ArchivePage({
  searchParams,
}: {
  searchParams: Promise<ArchiveSearch>;
}) {
  const values = await searchParams;
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value) query.set(key, value);
  }
  const archive = await readPortalJson<PortalArchive>(
    `/api/v1/portal/archive${query.size ? `?${query.toString()}` : ""}`,
  );
  return (
    <main className="portalMain">
      <header className="portalPageHeading">
        <p className="eyebrow">Published event archive</p>
        <h1>Search the verified record.</h1>
        <p>Postgres full-text search runs only across the latest published version of each event.</p>
      </header>
      <form className="archiveFilters" action="/portal/archive" method="get">
        <label><span>Search</span><input name="q" defaultValue={values.q} maxLength={200} placeholder="Port, authority, incident, vessel…" /></label>
        <label><span>Corridor</span><select name="corridor" defaultValue={values.corridor ?? ""}><option value="">All corridors</option><option value="hormuz_gulf">Hormuz & Gulf</option><option value="red_sea_bem_suez">Red Sea · Suez</option><option value="east_med">East Med</option><option value="port_specific">Port-specific</option></select></label>
        <label><span>Minimum severity</span><select name="min_severity" defaultValue={values.min_severity ?? ""}><option value="">Any</option><option value="2">S2</option><option value="3">S3</option><option value="4">S4</option></select></label>
        <button type="submit">Search archive</button>
      </form>
      <p className="archiveCount">{archive.total} published event{archive.total === 1 ? "" : "s"}</p>
      <section className="archiveResults" aria-label="Archive results">
        {archive.results.map((event) => (
          <Link href={`/portal/events/${encodeURIComponent(event.slug)}`} key={event.slug}>
            <div><span>S{event.severity}</span><span>{event.corridor.replaceAll("_", " ")}</span><span>v{event.version_no}</span></div>
            <h2>{event.title}</h2>
            <p>{event.summary_confirmed || event.whats_changed || "Published event record"}</p>
            <time dateTime={event.published_at}>{new Date(event.published_at).toLocaleString("en-GB", { timeZone: "Europe/Athens" })}</time>
          </Link>
        ))}
        {!archive.results.length ? <p className="portalEmpty">No published events match these filters.</p> : null}
      </section>
    </main>
  );
}
