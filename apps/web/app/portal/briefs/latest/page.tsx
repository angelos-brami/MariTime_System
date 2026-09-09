import Link from "next/link";

import { readPortalJson } from "@/lib/portal-api";
import type { DailyBrief } from "@/lib/portal-types";

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Europe/Athens",
  }).format(new Date(value));
}

export default async function LatestBriefPage() {
  const brief = await readPortalJson<DailyBrief>("/api/v1/portal/briefs/latest");
  return (
    <main className="portalMain briefPage">
      <header className="briefHeader"><p className="eyebrow">Analyst-finalized daily brief</p><h1>{brief.title}</h1><p>{brief.introduction}</p><div><span>{brief.items.length} published version{brief.items.length === 1 ? "" : "s"}</span><span>Finalized by {brief.finalized_by}</span><span>{brief.brief_date}</span></div></header>
      <section className="briefItems">{brief.items.map((item, index) => <article key={item.event_version_id}><div className="briefItemNumber">{String(index + 1).padStart(2, "0")}</div><div><p className="eyebrow">S{item.severity} · {item.corridor.replaceAll("_", " ")} · v{item.version_no}</p><h2><Link href={`/portal/events/${encodeURIComponent(item.event_slug)}`}>{item.title}</Link></h2><h3>Confirmed</h3><p>{item.summary_confirmed || "No confirmed statement in this version."}</p>{item.summary_reported ? <><h3>Reported</h3><p>{item.summary_reported}</p></> : null}{item.summary_unknown ? <><h3>Unknown</h3><p>{item.summary_unknown}</p></> : null}<p className="briefChanged"><strong>What changed:</strong> {item.whats_changed || "Initial publication."}</p></div></article>)}</section>
      {brief.corrections.length ? <section className="correctionLedger"><p className="eyebrow">Corrections issued since the previous brief</p>{brief.corrections.map((correction) => <article key={correction.id}><div><strong>{correction.correction_type.replaceAll("_", " ")}</strong><time>{formatTime(correction.issued_at)}</time></div><p><Link href={`/portal/events/${encodeURIComponent(correction.event_slug)}`}>{correction.note}</Link></p><small>Version {correction.version_from} corrected by version {correction.version_to}</small><code>{correction.affected_version_hash.slice(0, 12)} to {correction.corrected_version_hash.slice(0, 12)}</code></article>)}</section> : null}
      <section className="forwardWatch"><p className="eyebrow">Forward watch</p><p>{brief.forward_watch || "No additional forward-watch note was added."}</p></section>
      <p className="recordHash">Brief content hash: <code>{brief.content_hash}</code></p>
    </main>
  );
}
