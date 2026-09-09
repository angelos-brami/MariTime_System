import Link from "next/link";

import { demoPortalBoard } from "@/lib/portal-demo";
import { portalDemoEnabled } from "@/lib/portal-api";
import type { PortalBoard } from "@/lib/portal-types";

export const dynamic = "force-dynamic";

async function publicBoard(): Promise<PortalBoard | null> {
  if (portalDemoEnabled()) return demoPortalBoard;
  const baseUrl = process.env.EASTMED_API_BASE_URL;
  if (!baseUrl) return null;
  try {
    const response = await fetch(new URL("/api/v1/public/board", baseUrl), {
      cache: "no-store",
      signal: AbortSignal.timeout(8_000),
    });
    return response.ok ? ((await response.json()) as PortalBoard) : null;
  } catch {
    return null;
  }
}

function formatTimestamp(value: string | null) {
  if (!value) return "No active update";
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Europe/Athens",
  }).format(new Date(value));
}

export default async function CorridorBoardPage() {
  const board = await publicBoard();
  const corridors = board?.corridors.filter((item) => item.corridor !== "port_specific") ?? [];
  return (
    <main>
      <header className="siteHeader">
        <a className="wordmark" href="#top" aria-label="East Med Intelligence home">
          <span className="wordmarkMark">EM</span><span>East Med Intelligence</span>
        </a>
        <Link className="headerAction" href="/portal">Subscriber login</Link>
      </header>
      <section className="hero" id="top">
        <p className="eyebrow">Public corridor board</p>
        <h1>The operational picture, with its limits stated.</h1>
        <p className="lede">A concise live status view for the Eastern Mediterranean, Red Sea–Suez, and Hormuz–Gulf corridor. Subscriber records carry the claims, evidence, lineage, and version history behind every update.</p>
        <div className="verificationStrip"><span>Coverage window</span><strong>06:00–22:00 EET</strong><span>Board state</span><strong>{board ? "Live published data" : "Temporarily unavailable"}</strong></div>
      </section>
      <section className="board" aria-labelledby="board-title">
        <div className="sectionHeading"><div><p className="eyebrow">Status now</p><h2 id="board-title">Three corridors. One verification standard.</h2></div><p className="legend">Status reflects operational impact, not headline volume.</p></div>
        <div className="corridorGrid">
          {corridors.map((corridor, index) => <article className="corridorCard" key={corridor.corridor}><div className="cardTopline"><span className="corridorNumber">0{index + 1}</span><span className={`status status--${corridor.operational_state}`}><span aria-hidden="true" className="statusDot" />{corridor.operational_state}</span></div><h3>{corridor.label}</h3><p>{corridor.event_count ? `${corridor.event_count} active analyst-published event${corridor.event_count === 1 ? "" : "s"}.` : "No active analyst-published disruption."}</p><dl><div><dt>Last verified</dt><dd>{formatTimestamp(corridor.updated_at)}</dd></div></dl></article>)}
          {!corridors.length ? <article className="corridorCard corridorCard--unavailable"><h3>Live board unavailable</h3><p>The public board is fail-closed while the publication service is unavailable.</p></article> : null}
        </div>
      </section>
      <section className="method"><p className="eyebrow">Method over theatre</p><div><h2>Confirmed, reported, and unknown remain separate.</h2><p>Multiple articles do not become multiple sources when they share one lineage. Material updates are timestamped, corrections remain visible, and AIS is treated cautiously in interference zones.</p></div><Link href="/portal">Open subscriber record →</Link></section>
      <footer><span>East Med Intelligence</span><span>Information support—not navigational or safety advice.</span></footer>
    </main>
  );
}
