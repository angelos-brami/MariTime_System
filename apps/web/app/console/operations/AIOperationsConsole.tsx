"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import type {
  AICapabilityControl,
  AIIncident,
  AISystemVersion,
  DeskPrincipal,
} from "../types";

type IncidentSeverity = AIIncident["severity"];
type IncidentStatus = AIIncident["status"];

async function responseDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    return body.detail ?? `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

function label(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function time(value: string | null): string {
  if (!value) return "No expiry";
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(new Date(value));
}

export default function AIOperationsConsole() {
  const [principal, setPrincipal] = useState<DeskPrincipal | null>(null);
  const [controls, setControls] = useState<AICapabilityControl[]>([]);
  const [systems, setSystems] = useState<AISystemVersion[]>([]);
  const [incidents, setIncidents] = useState<AIIncident[]>([]);
  const [selectedIncidentId, setSelectedIncidentId] = useState<string | null>(null);
  const [scope, setScope] = useState("claim_extraction");
  const [controlMode, setControlMode] = useState<"disabled" | "shadow" | "assisted">("disabled");
  const [systemVersionId, setSystemVersionId] = useState("");
  const [controlReason, setControlReason] = useState("");
  const [incidentTitle, setIncidentTitle] = useState("");
  const [incidentSeverity, setIncidentSeverity] = useState<IncidentSeverity>("medium");
  const [incidentSummary, setIncidentSummary] = useState("");
  const [eventStatus, setEventStatus] = useState<IncidentStatus>("investigating");
  const [eventSummary, setEventSummary] = useState("");
  const [containmentAction, setContainmentAction] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const selectedIncident = useMemo(
    () => incidents.find((incident) => incident.id === selectedIncidentId) ?? incidents[0] ?? null,
    [incidents, selectedIncidentId],
  );

  const load = useCallback(async () => {
    const responses = await Promise.all([
      fetch("/api/console/desk/me", { cache: "no-store" }),
      fetch("/api/console/ai/capabilities", { cache: "no-store" }),
      fetch("/api/console/ai/system-versions", { cache: "no-store" }),
      fetch("/api/console/ai/incidents", { cache: "no-store" }),
    ]);
    for (const response of responses) {
      if (!response.ok) throw new Error(await responseDetail(response));
    }
    setPrincipal((await responses[0].json()) as DeskPrincipal);
    setControls((await responses[1].json()) as AICapabilityControl[]);
    setSystems((await responses[2].json()) as AISystemVersion[]);
    const nextIncidents = (await responses[3].json()) as AIIncident[];
    setIncidents(nextIncidents);
    setSelectedIncidentId((current) => current ?? nextIncidents[0]?.id ?? null);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void Promise.resolve()
      .then(() => (cancelled ? undefined : load()))
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : "AI operations are unavailable");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [load]);

  async function changeControl() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const response = await fetch(`/api/console/ai/capabilities/${encodeURIComponent(scope)}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          mode: controlMode,
          risk_tier: controlMode === "disabled" ? 4 : controlMode === "assisted" ? 2 : 1,
          system_version_id:
            controlMode !== "disabled" && scope !== "all_model_calls" ? systemVersionId || null : null,
          reason: controlReason,
          expires_at:
            controlMode === "disabled" ? null : new Date(Date.now() + 8 * 60 * 60_000).toISOString(),
          approval_refs: {},
        }),
      });
      if (!response.ok) throw new Error(await responseDetail(response));
      setNotice(
        controlMode === "disabled"
          ? `${scope} stopped. The append-only kill-switch receipt is recorded.`
          : `${scope} authorized for eight hours in ${controlMode} mode.`,
      );
      setControlReason("");
      await load();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Control change failed");
    } finally {
      setBusy(false);
    }
  }

  async function reportIncident() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const response = await fetch("/api/console/ai/incidents", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          title: incidentTitle,
          capability_scope: scope,
          system_version_id: systemVersionId || null,
          detected_at: new Date().toISOString(),
          severity: incidentSeverity,
          summary: incidentSummary,
          containment_action: containmentAction || null,
          evidence_refs: [],
        }),
      });
      if (!response.ok) throw new Error(await responseDetail(response));
      const incident = (await response.json()) as AIIncident;
      setSelectedIncidentId(incident.id);
      setIncidentTitle("");
      setIncidentSummary("");
      setContainmentAction("");
      setNotice(
        incidentSeverity === "high" || incidentSeverity === "critical"
          ? "Incident recorded and the affected capability was stopped automatically."
          : "Incident recorded in the immutable lifecycle ledger.",
      );
      await load();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Incident report failed");
    } finally {
      setBusy(false);
    }
  }

  async function updateIncident() {
    if (!selectedIncident) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const response = await fetch(`/api/console/ai/incidents/${selectedIncident.id}/events`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          status: eventStatus,
          severity: selectedIncident.severity,
          summary: eventSummary,
          containment_action: containmentAction || null,
          evidence_refs: [],
        }),
      });
      if (!response.ok) throw new Error(await responseDetail(response));
      setEventSummary("");
      setContainmentAction("");
      setNotice(`Incident moved to ${label(eventStatus)}.`);
      await load();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Incident update failed");
    } finally {
      setBusy(false);
    }
  }

  const canEnable = Boolean(
    principal &&
      ["senior_analyst", "administrator", "security"].includes(principal.role) &&
      ["mfa", "phishing_resistant"].includes(principal.auth_assurance),
  );

  return (
    <main className="aiOpsShell">
      <header className="aiOpsHeader">
        <div><p className="eyebrow">Fail-closed control plane</p><h1>AI operations</h1></div>
        <nav className="opsNav" aria-label="Console navigation">
          <Link href="/console">Dashboard</Link><Link href="/console/approvals">Approvals</Link>
          <Link href="/console/extraction">AI review</Link><Link href="/console/quality">Quality</Link>
        </nav>
        <div className="opsIdentity">
          <strong>{principal?.display_name ?? "Loading identity"}</strong>
          <span>{principal ? label(principal.role) : ""}</span>
          <span>{principal ? label(principal.auth_assurance) : ""}</span>
        </div>
      </header>

      {error ? <p className="consoleBanner consoleBanner--error" role="alert">{error}</p> : null}
      {notice ? <p className="consoleBanner consoleBanner--success" role="status">{notice}</p> : null}

      <section className="aiOpsHero">
        <div><p className="eyebrow">Human authority boundary</p><h2>Models propose. People release.</h2></div>
        <p>
          Missing, expired, or mismatched controls deny execution. Any authenticated analyst can stop
          a capability; enabling it requires an MFA control owner and a bounded authorization.
        </p>
      </section>

      <div className="aiOpsGrid">
        <section className="aiOpsPanel">
          <div className="opsPanelHeading"><div><p className="eyebrow">Live policy</p><h2>Capability ledger</h2></div><span>{controls.length} scopes</span></div>
          <div className="opsControlList">
            {controls.map((control) => (
              <article key={control.id}>
                <div><strong>{label(control.scope)}</strong><span className={`controlMode controlMode--${control.mode}`}>{control.mode}</span></div>
                <p>{control.reason}</p>
                <small>Revision {control.revision} · T{control.risk_tier} · expires {time(control.expires_at)}</small>
              </article>
            ))}
          </div>
          <div className="aiOpsForm">
            <label><span>Capability scope</span><input value={scope} onChange={(event) => setScope(event.target.value)} /></label>
            <label><span>Decision</span><select value={controlMode} onChange={(event) => setControlMode(event.target.value as typeof controlMode)}><option value="disabled">Emergency stop</option><option value="shadow">Shadow</option><option value="assisted">Assisted</option></select></label>
            {controlMode !== "disabled" && scope !== "all_model_calls" ? (
              <label className="aiOpsWide"><span>Registered system fingerprint</span><select value={systemVersionId} onChange={(event) => setSystemVersionId(event.target.value)}><option value="">Select exact system version</option>{systems.map((system) => <option value={system.id} key={system.id}>{system.name} — {system.fingerprint.slice(0, 12)}</option>)}</select></label>
            ) : null}
            <label className="aiOpsWide"><span>Reason</span><textarea value={controlReason} onChange={(event) => setControlReason(event.target.value)} placeholder="Operational evidence and reason for this control revision" /></label>
            <button type="button" disabled={busy || controlReason.length < 10 || (controlMode !== "disabled" && !canEnable)} onClick={() => void changeControl()}>{controlMode === "disabled" ? "Stop capability now" : "Authorize for 8 hours"}</button>
            {controlMode !== "disabled" && !canEnable ? <small>Enabling requires a senior, administrator, or security role with MFA.</small> : null}
          </div>
        </section>

        <section className="aiOpsPanel">
          <div className="opsPanelHeading"><div><p className="eyebrow">Safety response</p><h2>Report incident</h2></div><span>High severity auto-stops</span></div>
          <div className="aiOpsForm">
            <label className="aiOpsWide"><span>Incident title</span><input value={incidentTitle} onChange={(event) => setIncidentTitle(event.target.value)} /></label>
            <label><span>Severity</span><select value={incidentSeverity} onChange={(event) => setIncidentSeverity(event.target.value as IncidentSeverity)}>{["low", "medium", "high", "critical"].map((value) => <option key={value}>{value}</option>)}</select></label>
            <label><span>Affected scope</span><input value={scope} onChange={(event) => setScope(event.target.value)} /></label>
            <label className="aiOpsWide"><span>Observed behavior</span><textarea value={incidentSummary} onChange={(event) => setIncidentSummary(event.target.value)} /></label>
            <label className="aiOpsWide"><span>Containment action (optional)</span><textarea value={containmentAction} onChange={(event) => setContainmentAction(event.target.value)} /></label>
            <button type="button" disabled={busy || incidentTitle.length < 5 || incidentSummary.length < 10} onClick={() => void reportIncident()}>Record incident</button>
          </div>
        </section>

        <section className="aiOpsPanel aiOpsIncidents">
          <div className="opsPanelHeading"><div><p className="eyebrow">Immutable chronology</p><h2>Incident lifecycle</h2></div><span>{incidents.filter((incident) => incident.status !== "resolved").length} open</span></div>
          <div className="aiIncidentLayout">
            <div className="aiIncidentQueue">
              {incidents.map((incident) => <button type="button" className={selectedIncident?.id === incident.id ? "active" : ""} onClick={() => setSelectedIncidentId(incident.id)} key={incident.id}><span className={`incidentSeverity incidentSeverity--${incident.severity}`}>{incident.severity}</span><strong>{incident.title}</strong><small>{label(incident.status)} · {time(incident.detected_at)}</small></button>)}
              {!incidents.length ? <p className="opsEmpty">No AI incidents recorded.</p> : null}
            </div>
            {selectedIncident ? <div className="aiIncidentDetail">
              <div><p className="eyebrow">{label(selectedIncident.capability_scope)}</p><h3>{selectedIncident.title}</h3></div>
              <ol>{selectedIncident.events.map((event) => <li key={event.id}><div><strong>{label(event.status)}</strong><span>{label(event.severity)} · {time(event.at)}</span></div><p>{event.summary}</p>{event.containment_action ? <small>Containment: {event.containment_action}</small> : null}</li>)}</ol>
              {selectedIncident.status !== "resolved" ? <div className="aiOpsForm">
                <label><span>Next state</span><select value={eventStatus} onChange={(event) => setEventStatus(event.target.value as IncidentStatus)}><option value="investigating">Investigating</option><option value="contained">Contained</option><option value="resolved">Resolved</option></select></label>
                <label className="aiOpsWide"><span>Decision record</span><textarea value={eventSummary} onChange={(event) => setEventSummary(event.target.value)} /></label>
                {eventStatus === "contained" ? <label className="aiOpsWide"><span>Containment action</span><textarea value={containmentAction} onChange={(event) => setContainmentAction(event.target.value)} /></label> : null}
                <button type="button" disabled={busy || eventSummary.length < 10 || (eventStatus === "contained" && containmentAction.length < 10)} onClick={() => void updateIncident()}>Append lifecycle event</button>
              </div> : null}
            </div> : null}
          </div>
        </section>

        <section className="aiOpsPanel">
          <div className="opsPanelHeading"><div><p className="eyebrow">Reproducibility</p><h2>Registered systems</h2></div><span>{systems.length} fingerprints</span></div>
          <div className="aiSystemList">{systems.map((system) => <article key={system.id}><strong>{system.name}</strong><p>{system.purpose}</p><code>{system.fingerprint}</code><small>Registered {time(system.created_at)}</small></article>)}</div>
        </section>
      </div>
    </main>
  );
}
