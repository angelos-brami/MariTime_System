import Link from "next/link";

import { readPortalJson } from "@/lib/portal-api";
import type { QualityScoreboard } from "@/lib/portal-types";

function metric(value: number | null, suffix = ""): string {
  return value === null ? "—" : `${value.toFixed(1)}${suffix}`;
}

export default async function ScoreboardPage() {
  const scoreboard = await readPortalJson<QualityScoreboard>("/api/v1/public/scoreboard");
  return (
    <main className="portalMain scoreboardPage">
      <header className="portalPageHeading">
        <p className="eyebrow">Public accountability record</p>
        <h1>Quality scoreboard</h1>
        <p>Correction and time-to-verified measures are calculated from immutable publication, correction, and TTV records.</p>
      </header>
      <section className="scoreboardMetrics">
        <article><span>Correction rate</span><strong>{metric(scoreboard.correction_rate_percent, "%")}</strong><small>{scoreboard.correction_count} corrections / {scoreboard.published_version_count} versions</small></article>
        <article><span>Within 60 minutes</span><strong>{metric(scoreboard.corrections_within_60_minutes_percent, "%")}</strong><small>Detected to issued</small></article>
        <article><span>Holding-line median</span><strong>{metric(scoreboard.holding_line_median_minutes, "m")}</strong><small>{metric(scoreboard.holding_line_within_15_minutes_percent, "%")} within target</small></article>
        <article><span>Verified-update median</span><strong>{metric(scoreboard.verified_update_median_minutes, "m")}</strong><small>{metric(scoreboard.verified_update_within_45_minutes_percent, "%")} within target</small></article>
      </section>
      <section className="scoreboardRecord">
        <div><p className="eyebrow">Issued correction record</p><span>{scoreboard.corrections.length} entries</span></div>
        {scoreboard.corrections.length ? scoreboard.corrections.map((correction) => (
          <article key={correction.id}>
            <div><strong>{correction.correction_type}</strong><time>{new Date(correction.issued_at).toLocaleString("en-GB", { timeZone: "Europe/Athens" })}</time></div>
            <p><Link href={`/portal/events/${encodeURIComponent(correction.event_slug)}`}>{correction.note}</Link></p>
            <code>{correction.affected_version_hash.slice(0, 16)} to {correction.corrected_version_hash.slice(0, 16)}</code>
          </article>
        )) : <p className="portalEmpty">No corrections have been issued in the current record.</p>}
      </section>
    </main>
  );
}
