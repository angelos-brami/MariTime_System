"use client";

import Link from "next/link";
import { useState } from "react";

import type { DailyBrief } from "@/lib/portal-types";

async function detail(response: Response): Promise<string> {
  try {
    return ((await response.json()) as { detail?: string }).detail ?? `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

export default function BriefEditor() {
  const [briefDate, setBriefDate] = useState(new Date().toISOString().slice(0, 10));
  const [reviewer, setReviewer] = useState("senior-analyst");
  const [brief, setBrief] = useState<DailyBrief | null>(null);
  const [title, setTitle] = useState("");
  const [introduction, setIntroduction] = useState("");
  const [forwardWatch, setForwardWatch] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const installBrief = (next: DailyBrief) => {
    setBrief(next);
    setTitle(next.title);
    setIntroduction(next.introduction);
    setForwardWatch(next.forward_watch);
    setSelectedIds(next.items.map((item) => item.event_version_id));
  };

  const compile = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const response = await fetch(`/api/console/briefs/${encodeURIComponent(briefDate)}/compile`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ compiled_by: reviewer }),
      });
      if (!response.ok) throw new Error(await detail(response));
      const next = (await response.json()) as DailyBrief;
      installBrief(next);
      setNotice(`${next.items.length} published version${next.items.length === 1 ? "" : "s"} compiled into a draft.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Brief draft could not be compiled");
    } finally {
      setBusy(false);
    }
  };

  const finalize = async () => {
    if (!brief || brief.status === "finalized") return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const response = await fetch(`/api/console/briefs/${encodeURIComponent(brief.id)}/finalize`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          finalized_by: reviewer,
          title,
          introduction,
          forward_watch: forwardWatch,
          item_version_ids: selectedIds,
        }),
      });
      if (!response.ok) throw new Error(await detail(response));
      const next = (await response.json()) as DailyBrief;
      installBrief(next);
      setNotice(`Brief finalized with immutable hash ${next.content_hash.slice(0, 12)}.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Brief could not be finalized");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="briefEditorShell">
      <header className="eventWorkspaceHeader">
        <div className="eventWorkspaceBrand"><span className="wordmarkMark">EM</span><div><p className="eyebrow">Publication desk</p><h1>Daily brief</h1></div></div>
        <div className="eventHeaderMeta"><span>Auto-compiled</span><span>Human-finalized</span></div>
        <nav className="workspaceNav" aria-label="Console navigation"><Link href="/console">Triage</Link><Link href="/console/events">Events</Link><Link href="/console/extraction">Extraction</Link><Link href="/console/alerts">Alerts</Link><Link href="/console/briefs" aria-current="page">Brief</Link><Link href="/console/quality">Quality</Link></nav>
      </header>
      <section className="briefControlBar">
        <label><span>Brief date</span><input type="date" value={briefDate} onChange={(event) => { setBriefDate(event.target.value); setBrief(null); }} /></label>
        <label><span>Analyst</span><input value={reviewer} onChange={(event) => setReviewer(event.target.value)} /></label>
        <button type="button" onClick={compile} disabled={busy || !reviewer.trim()}>{busy ? "Working…" : "Compile / refresh draft"}</button>
      </section>
      {error ? <p className="consoleBanner consoleBanner--error" role="alert">{error}</p> : null}
      {notice ? <p className="consoleBanner" role="status">{notice}</p> : null}
      {!brief ? <section className="briefEditorEmpty"><p className="eyebrow">No draft loaded</p><h2>Compile the selected day’s analyst-published versions.</h2><p>The scheduler creates a draft after 06:00 Athens time; this control also refreshes a still-unfinalized draft.</p></section> : (
        <section className="briefEditorGrid">
          <div className="briefEditorForm">
            <div className="panelHeading"><div><p className="eyebrow">Editorial frame</p><h2>{brief.status === "draft" ? "Finalize the customer brief" : "Finalized brief"}</h2></div><span className={`briefState briefState--${brief.status}`}>{brief.status}</span></div>
            <label><span>Title</span><input value={title} onChange={(event) => setTitle(event.target.value)} disabled={brief.status === "finalized"} maxLength={500} /></label>
            <label><span>Introduction</span><textarea value={introduction} onChange={(event) => setIntroduction(event.target.value)} disabled={brief.status === "finalized"} maxLength={10000} /></label>
            <label><span>Forward watch</span><textarea value={forwardWatch} onChange={(event) => setForwardWatch(event.target.value)} disabled={brief.status === "finalized"} maxLength={10000} /></label>
            <button className="briefFinalizeButton" type="button" onClick={finalize} disabled={busy || brief.status === "finalized" || title.trim().length < 3}>{brief.status === "finalized" ? "Brief finalized" : "Finalize immutable brief"}</button>
            <small>Finalization pins the selected event-version IDs and prevents later edits.</small>
          </div>
          <div className="briefVersionPicker">
            <div className="panelHeading"><div><p className="eyebrow">Compiled source set</p><h2>{brief.items.length} published versions</h2></div><code>{brief.content_hash.slice(0, 12)}</code></div>
            {brief.items.map((item) => <label key={item.event_version_id} className="briefVersionChoice"><input type="checkbox" checked={selectedIds.includes(item.event_version_id)} disabled={brief.status === "finalized"} onChange={() => setSelectedIds((current) => current.includes(item.event_version_id) ? current.filter((id) => id !== item.event_version_id) : [...current, item.event_version_id])} /><span>S{item.severity} · {item.corridor.replaceAll("_", " ")} · v{item.version_no}</span><strong>{item.title}</strong><p>{item.summary_confirmed || item.whats_changed || "Published version"}</p><code>{item.content_hash.slice(0, 12)}</code></label>)}
            {!brief.items.length ? <p className="portalEmpty">No event versions were published during this Athens calendar day.</p> : null}
          </div>
        </section>
      )}
    </main>
  );
}
