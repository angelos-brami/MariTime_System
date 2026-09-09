import { readPortalJson } from "@/lib/portal-api";
import type { AISFeed } from "@/lib/portal-types";

import CorridorMap from "./CorridorMap";

export default async function AISPage() {
  const feed = await readPortalJson<AISFeed>("/api/v1/portal/ais?max_age_minutes=180&limit=1000");
  return (
    <main className="portalMain aisPage">
      <header className="portalPageHeading">
        <p className="eyebrow">Thin vessel-data context</p>
        <h1>AIS corridor picture</h1>
        <p>Latest position broadcasts cached for the three monitored corridors. This view supports analyst context; it does not confirm or deny an incident.</p>
      </header>
      <div className={`aisStatus aisStatus--${feed.cache_status}`}><strong>{feed.cache_status}</strong><span>Freshness threshold {feed.freshness_minutes} minutes · generated {new Date(feed.generated_at).toLocaleString("en-GB", { timeZone: "Europe/Athens" })}</span></div>
      <CorridorMap feed={feed} />
      <p className="aisCaveat" role="note"><strong>Mandatory caveat:</strong> {feed.caveat}</p>
      <section className="aisPositionLedger" aria-label="Recent AIS positions">
        <div><strong>Recent broadcasts</strong><span>{feed.results.length} cached vessels</span></div>
        {feed.results.slice(0, 50).map((position) => (
          <article key={position.id}>
            <div><strong>{position.vessel_name ?? `MMSI ${position.mmsi}`}</strong><span>{position.corridor.replaceAll("_", " ")}</span></div>
            <span>{position.speed === null ? "speed unknown" : `${position.speed.toFixed(1)} kn`} · {position.age_minutes.toFixed(0)} min old{position.stale ? " · stale" : ""}</span>
            <code>{position.latitude.toFixed(4)}, {position.longitude.toFixed(4)}</code>
          </article>
        ))}
      </section>
    </main>
  );
}
