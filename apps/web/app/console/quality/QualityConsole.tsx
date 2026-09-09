"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import type {
  ApprovalRequest,
  CorrectionImpact,
  CorrectionPreview,
  CorrectionType,
  DeskPrincipal,
  EventWorkspaceData,
  OpenEvent,
  QualityScoreboard,
  StateKnowledgeReport,
} from "../types";

function localInput(minutesAgo = 0): string {
  const date = new Date(Date.now() - minutesAgo * 60_000);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function iso(value: string): string {
  return new Date(value).toISOString();
}

async function detail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    return body.detail ?? `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

function metric(value: number | null, suffix = ""): string {
  return value === null ? "—" : `${value.toFixed(1)}${suffix}`;
}

export default function QualityConsole() {
  const [events, setEvents] = useState<OpenEvent[]>([]);
  const [eventId, setEventId] = useState("");
  const [workspace, setWorkspace] = useState<EventWorkspaceData | null>(null);
  const [scoreboard, setScoreboard] = useState<QualityScoreboard | null>(null);
  const [actor, setActor] = useState("desk-analyst");
  const [reportTime, setReportTime] = useState(localInput());
  const [report, setReport] = useState<StateKnowledgeReport | null>(null);
  const [versionFrom, setVersionFrom] = useState("");
  const [versionTo, setVersionTo] = useState("");
  const [correctionType, setCorrectionType] = useState<CorrectionType>("correction");
  const [impact, setImpact] = useState<CorrectionImpact>("non_operational");
  const [note, setNote] = useState("");
  const [rootCause, setRootCause] = useState("");
  const [correctiveAction, setCorrectiveAction] = useState("");
  const [detectedAt, setDetectedAt] = useState(localInput());
  const [principal, setPrincipal] = useState<DeskPrincipal | null>(null);
  const [approvalReason, setApprovalReason] = useState("");
  const [correctionRequest, setCorrectionRequest] = useState<ApprovalRequest | null>(null);
  const [correctionPreview, setCorrectionPreview] = useState<CorrectionPreview | null>(null);
  const [previewPayload, setPreviewPayload] = useState<Record<string, unknown> | null>(null);
  const [sourceRecordId, setSourceRecordId] = useState("");
  const [signalAt, setSignalAt] = useState(localInput(5));
  const [holdingAt, setHoldingAt] = useState("");
  const [verifiedAt, setVerifiedAt] = useState("");
  const [ttvCreated, setTtvCreated] = useState(false);
  const [calendarSlug, setCalendarSlug] = useState("");
  const [calendarTitle, setCalendarTitle] = useState("");
  const [calendarType, setCalendarType] = useState("strike");
  const [calendarStart, setCalendarStart] = useState(localInput());
  const [calendarEnd, setCalendarEnd] = useState("");
  const [calendarNote, setCalendarNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refreshScoreboard = async () => {
    const response = await fetch("/api/console/quality/scoreboard", { cache: "no-store" });
    if (!response.ok) throw new Error(await detail(response));
    setScoreboard((await response.json()) as QualityScoreboard);
  };

  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      fetch("/api/console/events", { cache: "no-store" }),
      fetch("/api/console/quality/scoreboard", { cache: "no-store" }),
      fetch("/api/console/desk/me", { cache: "no-store" }),
      fetch("/api/console/approval-requests?limit=250", { cache: "no-store" }),
    ]).then(async ([eventResponse, scoreResponse, meResponse, approvalsResponse]) => {
      if (!eventResponse.ok) throw new Error(await detail(eventResponse));
      if (!scoreResponse.ok) throw new Error(await detail(scoreResponse));
      if (!meResponse.ok) throw new Error(await detail(meResponse));
      if (!approvalsResponse.ok) throw new Error(await detail(approvalsResponse));
      const nextEvents = (await eventResponse.json()) as OpenEvent[];
      const nextScore = (await scoreResponse.json()) as QualityScoreboard;
      const me = (await meResponse.json()) as DeskPrincipal;
      const approvals = (await approvalsResponse.json()) as ApprovalRequest[];
      if (!cancelled) {
        setEvents(nextEvents);
        setEventId(nextEvents[0]?.id ?? "");
        setScoreboard(nextScore);
        setPrincipal(me);
        setActor(`desk:${me.id}`);
        setCorrectionRequest(
          approvals.find(
            (request) =>
              request.request_type === "operational_correction" &&
              request.primary_user_id === me.id &&
              ["pending", "approved"].includes(request.status),
          ) ?? null,
        );
      }
    }).catch((reason: unknown) => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : "Quality data failed");
    });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!eventId) return;
    let cancelled = false;
    void fetch(`/api/console/events/${encodeURIComponent(eventId)}/workspace`, {
      cache: "no-store",
    }).then(async (response) => {
      if (!response.ok) throw new Error(await detail(response));
      const next = (await response.json()) as EventWorkspaceData;
      if (!cancelled) {
        setWorkspace(next);
        const ordered = [...next.versions].sort((a, b) => a.version_no - b.version_no);
        setVersionFrom(ordered.at(-2)?.id ?? ordered[0]?.id ?? "");
        setVersionTo(ordered.at(-1)?.id ?? "");
        setSourceRecordId(next.sources[0]?.id ?? "");
        setCorrectionPreview(null);
        setPreviewPayload(null);
        setCorrectionRequest((current) =>
          current?.target_id === next.id ? current : null,
        );
        setReport(null);
        setTtvCreated(false);
      }
    }).catch((reason: unknown) => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : "Event load failed");
    });
    return () => { cancelled = true; };
  }, [eventId]);

  const selectedSource = useMemo(
    () => workspace?.sources.find((source) => source.id === sourceRecordId),
    [sourceRecordId, workspace],
  );

  const invalidateCorrection = () => {
    setCorrectionPreview(null);
    setCorrectionRequest(null);
    setPreviewPayload(null);
  };

  const correctionContent = () => ({
    event_id: eventId,
    version_from_id: versionFrom,
    version_to_id: versionTo,
    correction_type: correctionType,
    impact,
    note,
    root_cause: rootCause,
    corrective_action: correctiveAction,
    detected_at: iso(detectedAt),
  });

  const correctionPayload = () => ({
    ...correctionContent(),
    drafted_by: actor,
    signed_off_by: actor,
  });

  const generateReport = async () => {
    setBusy(true); setError(null); setNotice(null);
    try {
      const response = await fetch("/api/console/reports/state-of-knowledge", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          event_id: eventId,
          requested_timestamp: iso(reportTime),
          requested_by: actor,
        }),
      });
      if (!response.ok) throw new Error(await detail(response));
      setReport((await response.json()) as StateKnowledgeReport);
      setNotice("Immutable state-of-knowledge report generated.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Report generation failed");
    } finally { setBusy(false); }
  };

  const previewCorrection = async () => {
    setBusy(true); setError(null); setNotice(null);
    try {
      if (impact === "operationally_relevant") {
        if (approvalReason.trim().length < 10) {
          throw new Error("Record why this operational correction is ready for approval.");
        }
        const approvalResponse = await fetch("/api/console/corrections/approval-requests", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            draft: correctionContent(),
            reason: approvalReason.trim(),
          }),
        });
        if (!approvalResponse.ok) throw new Error(await detail(approvalResponse));
        const request = (await approvalResponse.json()) as ApprovalRequest;
        setCorrectionRequest(request);
        setPreviewPayload(correctionPayload());
        setNotice("Operational correction submitted to the independent approval inbox.");
        return;
      }
      const payload = correctionPayload();
      const response = await fetch("/api/console/corrections/preview", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error(await detail(response));
      setCorrectionPreview((await response.json()) as CorrectionPreview);
      setPreviewPayload(payload);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Correction preview failed");
    } finally { setBusy(false); }
  };

  const issueCorrection = async () => {
    if (!previewPayload) return;
    const releaseHash =
      impact === "operationally_relevant"
        ? correctionRequest?.release_hash
        : correctionPreview?.preview_hash;
    if (!releaseHash) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      const response = await fetch("/api/console/corrections", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ...previewPayload, preview_hash: releaseHash }),
      });
      if (!response.ok) throw new Error(await detail(response));
      setNotice("Correction issued to the portal, next brief, and original channels.");
      setCorrectionPreview(null); setPreviewPayload(null);
      setCorrectionRequest(null);
      await refreshScoreboard();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Correction issue failed");
    } finally { setBusy(false); }
  };

  const refreshCorrectionApproval = async () => {
    if (!principal) return;
    setBusy(true); setError(null);
    try {
      const response = await fetch("/api/console/approval-requests?limit=250", {
        cache: "no-store",
      });
      if (!response.ok) throw new Error(await detail(response));
      const requests = (await response.json()) as ApprovalRequest[];
      const request = requests.find(
        (item) =>
          item.request_type === "operational_correction" &&
          item.target_id === eventId &&
          item.primary_user_id === principal.id &&
          ["pending", "approved"].includes(item.status),
      );
      setCorrectionRequest(request ?? null);
      setNotice(request?.status === "approved" ? "Correction approved. Issue it before expiry." : "Approval state refreshed.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Approval state could not be refreshed");
    } finally { setBusy(false); }
  };

  const recordSignal = async () => {
    setBusy(true); setError(null); setNotice(null);
    try {
      const response = await fetch("/api/console/ttv", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          event_id: eventId,
          first_credible_signal_at: iso(signalAt),
          signal_source_record_id: sourceRecordId,
          coverage_window: true,
          recorded_by: actor,
          corroborated_tier_e: selectedSource?.source_tier === "E",
        }),
      });
      if (!response.ok) throw new Error(await detail(response));
      setTtvCreated(true);
      setNotice("First credible signal clock recorded.");
      await refreshScoreboard();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "TTV clock failed");
    } finally { setBusy(false); }
  };

  const updateSignal = async () => {
    setBusy(true); setError(null); setNotice(null);
    try {
      const response = await fetch(`/api/console/ttv/${encodeURIComponent(eventId)}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          holding_line_at: holdingAt ? iso(holdingAt) : null,
          verified_update_at: verifiedAt ? iso(verifiedAt) : null,
          updated_by: actor,
        }),
      });
      if (!response.ok) throw new Error(await detail(response));
      setNotice("TTV milestones updated.");
      await refreshScoreboard();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "TTV update failed");
    } finally { setBusy(false); }
  };

  const publishCalendar = async () => {
    if (!workspace) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      const response = await fetch("/api/console/calendar", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          slug: calendarSlug,
          event_type: calendarType,
          title: calendarTitle,
          corridor: workspace.corridor,
          ports: [],
          starts_at: iso(calendarStart),
          ends_at: calendarEnd ? iso(calendarEnd) : null,
          status: "announced",
          public_note: calendarNote,
          source_record_ids: [sourceRecordId],
          published_by: actor,
        }),
      });
      if (!response.ok) throw new Error(await detail(response));
      setNotice("Immutable calendar item published to the customer portal.");
      setCalendarSlug(""); setCalendarTitle(""); setCalendarNote("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Calendar publication failed");
    } finally { setBusy(false); }
  };

  return (
    <main className="qualityShell">
      <header className="eventWorkspaceHeader">
        <div className="eventWorkspaceBrand"><span className="wordmarkMark">EM</span><div><p className="eyebrow">Accountable intelligence</p><h1>Quality & corrections</h1></div></div>
        <nav className="workspaceNav" aria-label="Console navigation"><Link href="/console">Dashboard</Link><Link href="/console/triage">Triage</Link><Link href="/console/approvals">Approvals</Link><Link href="/console/events">Events</Link><Link href="/console/alerts">Alerts</Link><Link href="/console/quality" aria-current="page">Quality</Link><Link href="/console/data">Data API</Link></nav>
      </header>
      <section className="workspaceControlBar">
        <label><span>Authenticated analyst</span><input value={principal?.display_name ?? actor} readOnly /></label>
        <label><span>Working event</span><select value={eventId} onChange={(event) => setEventId(event.target.value)}>{events.map((item) => <option key={item.id} value={item.id}>S{item.severity} · {item.title}</option>)}</select></label>
      </section>
      {error ? <p className="consoleBanner consoleBanner--error" role="alert">{error}</p> : null}
      {notice ? <p className="consoleBanner" role="status">{notice}</p> : null}

      <section className="qualityMetrics" aria-label="Public quality scoreboard">
        <article><span>Correction rate</span><strong>{metric(scoreboard?.correction_rate_percent ?? null, "%")}</strong><small>{scoreboard?.correction_count ?? 0} / {scoreboard?.published_version_count ?? 0} versions</small></article>
        <article><span>Correction SLA</span><strong>{metric(scoreboard?.corrections_within_60_minutes_percent ?? null, "%")}</strong><small>Issued within 60 minutes</small></article>
        <article><span>Holding median</span><strong>{metric(scoreboard?.holding_line_median_minutes ?? null, "m")}</strong><small>{metric(scoreboard?.holding_line_within_15_minutes_percent ?? null, "%")} within 15m</small></article>
        <article><span>Verified median</span><strong>{metric(scoreboard?.verified_update_median_minutes ?? null, "m")}</strong><small>{metric(scoreboard?.verified_update_within_45_minutes_percent ?? null, "%")} within 45m</small></article>
      </section>

      <div className="qualityGrid">
        <section className="qualityPanel">
          <div className="panelHeading"><div><p className="eyebrow">Timestamp query</p><h2>State of knowledge</h2></div></div>
          <label><span>State at</span><input type="datetime-local" value={reportTime} onChange={(event) => setReportTime(event.target.value)} /></label>
          <button type="button" disabled={busy || !eventId || !reportTime || !actor.trim()} onClick={() => void generateReport()}>Generate immutable PDF</button>
          {report ? <article className="reportReceipt"><strong>Version {report.event_version_id.slice(0, 8)}</strong><code>{report.version_content_hash}</code><a href={`/api/console/reports/state-of-knowledge/${encodeURIComponent(report.id)}`}>Download PDF</a></article> : null}
        </section>

        <section className="qualityPanel qualityPanel--wide">
          <div className="panelHeading"><div><p className="eyebrow">Two-step issue control</p><h2>Correction propagation</h2></div><span>{workspace?.versions.length ?? 0} versions</span></div>
          <div className="qualityFormGrid">
            <label><span>Affected version</span><select value={versionFrom} onChange={(event) => { setVersionFrom(event.target.value); invalidateCorrection(); }}>{workspace?.versions.map((version) => <option key={version.id} value={version.id}>v{version.version_no} · {version.content_hash.slice(0, 10)}</option>)}</select></label>
            <label><span>Corrected version</span><select value={versionTo} onChange={(event) => { setVersionTo(event.target.value); invalidateCorrection(); }}>{workspace?.versions.map((version) => <option key={version.id} value={version.id}>v{version.version_no} · {version.content_hash.slice(0, 10)}</option>)}</select></label>
            <label><span>Type</span><select value={correctionType} onChange={(event) => { setCorrectionType(event.target.value as CorrectionType); invalidateCorrection(); }}><option value="update">Update</option><option value="clarification">Clarification</option><option value="correction">Correction</option></select></label>
            <label><span>Impact</span><select value={impact} onChange={(event) => { setImpact(event.target.value as CorrectionImpact); invalidateCorrection(); }}><option value="non_operational">Non-operational</option><option value="operationally_relevant">Operationally relevant</option></select></label>
            <label><span>Detected at</span><input type="datetime-local" value={detectedAt} onChange={(event) => { setDetectedAt(event.target.value); invalidateCorrection(); }} /></label>
            <label><span>Approval state</span><input value={correctionRequest ? correctionRequest.status.replaceAll("_", " ") : impact === "operationally_relevant" ? "not requested" : "senior release"} readOnly /></label>
          </div>
          <label><span>Public correction note</span><textarea value={note} onChange={(event) => { setNote(event.target.value); invalidateCorrection(); }} /></label>
          <label><span>Internal root cause</span><textarea value={rootCause} onChange={(event) => { setRootCause(event.target.value); invalidateCorrection(); }} /></label>
          <label><span>Corrective action</span><textarea value={correctiveAction} onChange={(event) => { setCorrectiveAction(event.target.value); invalidateCorrection(); }} /></label>
          {impact === "operationally_relevant" ? <label><span>Independent approval rationale</span><textarea value={approvalReason} onChange={(event) => setApprovalReason(event.target.value)} placeholder="Explain why this exact correction is ready for independent review." /></label> : null}
          {!correctionPreview && !correctionRequest ? <button type="button" disabled={busy || !versionFrom || !versionTo || !note.trim() || !rootCause.trim() || !correctiveAction.trim() || (impact === "operationally_relevant" && approvalReason.trim().length < 10)} onClick={() => void previewCorrection()}>{impact === "operationally_relevant" ? "Submit exact correction for approval" : "Preview exact recipients"}</button> : null}
          {correctionPreview ? <div className="correctionReceipt"><strong>{correctionPreview.recipient_count} original-channel recipients</strong><span>{correctionPreview.channels.join(" · ") || "Portal and next brief only"}</span><code>{correctionPreview.preview_hash}</code><button type="button" disabled={busy} onClick={() => void issueCorrection()}>Issue signed correction</button></div> : null}
          {correctionRequest ? <div className="correctionReceipt"><strong>{correctionRequest.status.replaceAll("_", " ")}</strong><span>Independent operational approval</span>{correctionRequest.release_hash ? <code>{correctionRequest.release_hash}</code> : null}<div className="correctionApprovalActions"><button type="button" disabled={busy} onClick={() => void refreshCorrectionApproval()}>Refresh approval</button><Link href="/console/approvals">Open inbox</Link>{correctionRequest.status === "approved" && correctionRequest.release_hash ? <button type="button" disabled={busy} onClick={() => void issueCorrection()}>Issue approved correction</button> : null}</div></div> : null}
        </section>

        <section className="qualityPanel qualityPanel--wide">
          <div className="panelHeading"><div><p className="eyebrow">Source-qualified clock</p><h2>Time to verified</h2></div></div>
          <div className="qualityFormGrid">
            <label><span>Credible source record</span><select value={sourceRecordId} onChange={(event) => setSourceRecordId(event.target.value)}>{workspace?.sources.map((source) => <option key={source.id} value={source.id}>Tier {source.source_tier} · {source.source_name}</option>)}</select></label>
            <label><span>First credible signal</span><input type="datetime-local" value={signalAt} onChange={(event) => setSignalAt(event.target.value)} /></label>
            <label><span>Holding line</span><input type="datetime-local" value={holdingAt} onChange={(event) => setHoldingAt(event.target.value)} /></label>
            <label><span>Verified update</span><input type="datetime-local" value={verifiedAt} onChange={(event) => setVerifiedAt(event.target.value)} /></label>
          </div>
          {!ttvCreated ? <button type="button" disabled={busy || !sourceRecordId || !signalAt} onClick={() => void recordSignal()}>Start credible-signal clock</button> : <button type="button" disabled={busy || (!holdingAt && !verifiedAt)} onClick={() => void updateSignal()}>Save TTV milestones</button>}
        </section>

        <section className="qualityPanel qualityPanel--wide">
          <div className="panelHeading"><div><p className="eyebrow">Versioned customer utility</p><h2>Port &amp; strike calendar</h2></div></div>
          <div className="qualityFormGrid">
            <label><span>Stable slug</span><input value={calendarSlug} onChange={(event) => setCalendarSlug(event.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-"))} placeholder="piraeus-tug-strike" /></label>
            <label><span>Type</span><select value={calendarType} onChange={(event) => setCalendarType(event.target.value)}><option value="strike">Strike</option><option value="port_closure">Port closure</option><option value="naval_exercise">Naval exercise</option><option value="weather_window">Weather window</option><option value="regulatory_deadline">Regulatory deadline</option><option value="other">Other</option></select></label>
            <label><span>Starts</span><input type="datetime-local" value={calendarStart} onChange={(event) => setCalendarStart(event.target.value)} /></label>
            <label><span>Ends</span><input type="datetime-local" value={calendarEnd} onChange={(event) => setCalendarEnd(event.target.value)} /></label>
          </div>
          <label><span>Title</span><input value={calendarTitle} onChange={(event) => setCalendarTitle(event.target.value)} /></label>
          <label><span>Public operational note</span><textarea value={calendarNote} onChange={(event) => setCalendarNote(event.target.value)} /></label>
          <p className="mutedSignal">Evidence: {selectedSource ? `Tier ${selectedSource.source_tier} · ${selectedSource.source_name}` : "select a source above"}</p>
          <button type="button" disabled={busy || !calendarSlug || !calendarTitle.trim() || !calendarNote.trim() || !sourceRecordId} onClick={() => void publishCalendar()}>Publish calendar version</button>
        </section>
      </div>
      <section className="qualityCorrections"><div className="panelHeading"><div><p className="eyebrow">Public record</p><h2>Issued corrections</h2></div></div>{scoreboard?.corrections.map((correction) => <article key={correction.id}><div><strong>{correction.correction_type}</strong><time>{new Date(correction.issued_at).toLocaleString("en-GB")}</time></div><p>{correction.note}</p><code>{correction.affected_version_hash.slice(0, 12)} to {correction.corrected_version_hash.slice(0, 12)}</code></article>)}</section>
    </main>
  );
}
