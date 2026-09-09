import { notFound } from "next/navigation";

import { forwardPortalRequest } from "@/lib/portal-api";
import type { PortalEvent } from "@/lib/portal-types";

function lines(value: string): string[] {
  return value.split("\n").map((line) => line.trim()).filter(Boolean);
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Europe/Athens",
  }).format(new Date(value));
}

export default async function EventPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const response = await forwardPortalRequest(`/api/v1/portal/events/${encodeURIComponent(slug)}`);
  if (response.status === 404) notFound();
  if (!response.ok) throw new Error("The published event record is unavailable.");
  const event = (await response.json()) as PortalEvent;
  const sections = [
    ["Confirmed", event.summary_confirmed, "confirmed"],
    ["Reported", event.summary_reported, "reported"],
    ["Unknown", event.summary_unknown, "unknown"],
    ["What changed", event.whats_changed, "changed"],
  ] as const;
  return (
    <main className="portalMain eventRecord">
      <header className="eventRecordHeader">
        <div><p className="eyebrow">Published event · version {event.version_no}</p><h1>{event.title}</h1></div>
        <dl><div><dt>Status</dt><dd>{event.status}</dd></div><div><dt>Severity</dt><dd>S{event.severity}</dd></div><div><dt>Corridor</dt><dd>{event.corridor.replaceAll("_", " ")}</dd></div><div><dt>Published</dt><dd>{formatTime(event.published_at)}</dd></div></dl>
      </header>
      <section className="knowledgeGrid" aria-label="State of knowledge">
        {sections.map(([title, content, kind]) => (
          <article className={`knowledgeBlock knowledgeBlock--${kind}`} key={kind}>
            <h2>{title}</h2>
            {lines(content).length ? lines(content).map((line) => <p key={line}>{line}</p>) : <p>None published in this version.</p>}
          </article>
        ))}
      </section>
      <section className="eventRecordColumns">
        <div><p className="eyebrow">Version timeline</p><ol className="versionTimeline">{event.timeline.map((version) => <li key={version.content_hash}><div><strong>Version {version.version_no}</strong><time>{formatTime(version.published_at)}</time></div><p>{version.whats_changed || "Initial published state."}</p><code>{version.content_hash.slice(0, 12)}</code></li>)}</ol></div>
        <div><p className="eyebrow">Sources & lineage</p><div className="sourceLedger">{event.sources.map((source) => <article key={source.source_record_id}><div><span>Tier {source.source_tier}</span><span>{source.directness}</span></div><a href={source.url} rel="noreferrer" target="_blank">{source.source_name}</a>{source.excerpt ? <blockquote>{source.excerpt}</blockquote> : <p>Used for verification; no excerpt republished under the recorded rights basis.</p>}<code>Lineage {source.lineage_root_id.slice(0, 8)}</code></article>)}</div></div>
      </section>
      {event.corrections.length ? (
        <section className="correctionLedger" aria-label="Corrections">
          <p className="eyebrow">Correction record</p>
          {event.corrections.map((correction) => (
            <article key={correction.id}>
              <div><strong>{correction.correction_type.replaceAll("_", " ")}</strong><time>{formatTime(correction.issued_at)}</time></div>
              <p>{correction.note}</p>
              <small>Version {correction.version_from} corrected by version {correction.version_to}</small>
              <code>{correction.affected_version_hash.slice(0, 12)} to {correction.corrected_version_hash.slice(0, 12)}</code>
            </article>
          ))}
        </section>
      ) : null}
      <div className="reliabilityReceiptLink">
        <div><p className="eyebrow">Verification artifact</p><strong>Machine-verifiable reliability receipt</strong><small>Exact claims, evidence, policy, model versions, people, and approval for this release.</small></div>
        <a href={`/api/portal/events/${encodeURIComponent(event.slug)}/reliability-receipt`} download>Download JSON receipt</a>
      </div>
      <p className="recordHash">Published content hash: <code>{event.content_hash}</code></p>
    </main>
  );
}
