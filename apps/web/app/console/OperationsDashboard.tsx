"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import type { DeskPrincipal, OperationsDashboard as DashboardData } from "./types";

async function detail(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: string };
    return payload.detail ?? `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

function label(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatTime(value: string | null): string {
  if (!value) return "No expiry";
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(new Date(value));
}

export default function OperationsDashboard() {
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [principal, setPrincipal] = useState<DeskPrincipal | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchDashboard = useCallback(async () => {
    const [dashboardResponse, meResponse] = await Promise.all([
      fetch("/api/console/operations/dashboard", { cache: "no-store" }),
      fetch("/api/console/desk/me", { cache: "no-store" }),
    ]);
    if (!dashboardResponse.ok) throw new Error(await detail(dashboardResponse));
    if (!meResponse.ok) throw new Error(await detail(meResponse));
    return {
      dashboard: (await dashboardResponse.json()) as DashboardData,
      principal: (await meResponse.json()) as DeskPrincipal,
    };
  }, []);

  const refresh = useCallback(async () => {
    const next = await fetchDashboard();
    setDashboard(next.dashboard);
    setPrincipal(next.principal);
  }, [fetchDashboard]);

  useEffect(() => {
    let cancelled = false;
    void fetchDashboard()
      .then((next) => {
        if (cancelled) return;
        setDashboard(next.dashboard);
        setPrincipal(next.principal);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Dashboard unavailable");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [fetchDashboard]);

  const checks = useMemo(
    () => ({
      pass: dashboard?.readiness.filter((check) => check.state === "pass").length ?? 0,
      warning: dashboard?.readiness.filter((check) => check.state === "warning").length ?? 0,
      fail: dashboard?.readiness.filter((check) => check.state === "fail").length ?? 0,
    }),
    [dashboard],
  );

  if (loading) return <main className="opsLoading">Building the operational picture…</main>;
  if (!dashboard) {
    return <main className="opsLoading consoleBanner--error">{error ?? "Dashboard unavailable"}</main>;
  }

  const queueCards = [
    { label: "Signals awaiting triage", value: dashboard.pending_triage, href: "/console/triage", tone: "neutral" },
    { label: "Open intelligence events", value: dashboard.open_events, href: "/console/events", tone: "neutral" },
    { label: "High-severity events", value: dashboard.high_severity_events, href: "/console/events", tone: "critical" },
    { label: "Authenticated approvals", value: dashboard.pending_approval_requests, href: "/console/approvals", tone: "elevated" },
    { label: "Extraction proposals", value: dashboard.pending_extraction_proposals, href: "/console/extraction", tone: "neutral" },
    { label: "Unresolved AI incidents", value: dashboard.open_ai_incidents, href: "/console/operations", tone: "critical" },
  ];

  return (
    <main className="opsShell">
      <header className="opsHeader">
        <div className="consoleIdentity">
          <span className="wordmarkMark">EM</span>
          <div><p className="eyebrow">East Med command</p><h1>Operations dashboard</h1></div>
        </div>
        <nav className="opsNav" aria-label="Console navigation">
          <Link href="/console/triage">Triage</Link>
          <Link href="/console/events">Events</Link>
          <Link href="/console/approvals">Approvals</Link>
          <Link href="/console/extraction">AI review</Link>
          <Link href="/console/alerts">Delivery</Link>
          <Link href="/console/quality">Quality</Link>
        </nav>
        <div className="opsIdentity">
          <strong>{principal?.display_name ?? "Desk identity"}</strong>
          <span>{principal ? label(principal.role) : "Unavailable"}</span>
          <span>{principal ? label(principal.auth_assurance) : "Unknown assurance"}</span>
        </div>
      </header>

      {error ? <p className="consoleBanner consoleBanner--error" role="alert">{error}</p> : null}

      <section className={`opsStatus opsStatus--${dashboard.overall_status}`}>
        <div>
          <p className="eyebrow">Launch control</p>
          <h2>{label(dashboard.overall_status)}</h2>
          <p>
            {checks.pass} controls pass · {checks.warning} warnings · {checks.fail} blockers
          </p>
        </div>
        <div className="opsStatusFlags">
          <span className={dashboard.outbound_enabled ? "flag flag--on" : "flag flag--off"}>
            Outbound {dashboard.outbound_enabled ? "enabled" : "stopped"}
          </span>
          <span className={dashboard.desk_auth_mode === "oidc" ? "flag flag--on" : "flag flag--warn"}>
            Identity {dashboard.desk_auth_mode.toUpperCase()}
          </span>
          <span className="flag">{dashboard.environment}</span>
        </div>
        <div className="opsRefresh">
          <span>Snapshot {formatTime(dashboard.generated_at)}</span>
          <button type="button" onClick={() => void refresh()}>Refresh snapshot</button>
        </div>
      </section>

      <section className="opsQueueGrid" aria-label="Operational queues">
        {queueCards.map((card) => (
          <Link className={`opsQueueCard opsQueueCard--${card.tone}`} href={card.href} key={card.label}>
            <strong>{card.value}</strong>
            <span>{card.label}</span>
            <small>Open queue →</small>
          </Link>
        ))}
      </section>

      <div className="opsMainGrid">
        <section className="opsPanel opsReadiness">
          <div className="opsPanelHeading">
            <div><p className="eyebrow">Release readiness</p><h2>Launch gates</h2></div>
            <span>{dashboard.readiness.length} controls</span>
          </div>
          <div className="opsCheckList">
            {dashboard.readiness.map((check) => {
              const content = (
                <>
                  <span className={`opsCheckIcon opsCheckIcon--${check.state}`}>
                    {check.state === "pass" ? "✓" : check.state === "warning" ? "!" : "×"}
                  </span>
                  <div><strong>{check.label}</strong><p>{check.detail}</p></div>
                  {check.href ? <small>Open →</small> : null}
                </>
              );
              return check.href ? (
                <Link className="opsCheck" href={check.href} key={check.key}>{content}</Link>
              ) : <article className="opsCheck" key={check.key}>{content}</article>;
            })}
          </div>
        </section>

        <section className="opsPanel">
          <div className="opsPanelHeading">
            <div><p className="eyebrow">Evidence acquisition</p><h2>Source network</h2></div>
            <strong>{dashboard.active_sources}<small> / {dashboard.total_sources} active</small></strong>
          </div>
          <div className="opsHealthBars">
            {Object.entries(dashboard.source_health).sort(([, left], [, right]) => right - left).map(([state, count]) => (
              <div key={state}>
                <span>{label(state)}</span>
                <div><i style={{ width: `${dashboard.total_sources ? Math.max(4, count / dashboard.total_sources * 100) : 0}%` }} /></div>
                <strong>{count}</strong>
              </div>
            ))}
          </div>
          <Link className="opsPanelAction" href="/console/triage">Open monitoring desk</Link>
        </section>

        <section className="opsPanel">
          <div className="opsPanelHeading">
            <div><p className="eyebrow">Fail-closed automation</p><h2>AI capability controls</h2></div>
            <span>{dashboard.ai_controls.length} scopes</span>
          </div>
          <div className="opsControlList">
            {dashboard.ai_controls.length ? dashboard.ai_controls.map((control) => (
              <article key={control.id}>
                <div><strong>{label(control.scope)}</strong><span className={`controlMode controlMode--${control.mode}`}>{control.mode}</span></div>
                <p>{control.reason}</p>
                <small>Risk T{control.risk_tier} · revision {control.revision} · expires {formatTime(control.expires_at)}</small>
              </article>
            )) : <p className="opsEmpty">No active control history. All model calls fail closed.</p>}
          </div>
          <Link className="opsPanelAction" href="/console/operations">Manage AI controls</Link>
        </section>

        <section className="opsPanel">
          <div className="opsPanelHeading">
            <div><p className="eyebrow">Customer delivery</p><h2>Outbound ledger</h2></div>
            <span>{dashboard.approved_releases_expiring} approvals expiring</span>
          </div>
          <div className="opsDeliveryGrid">
            {Object.entries(dashboard.delivery_status).map(([state, count]) => (
              <article key={state}><strong>{count}</strong><span>{label(state)}</span></article>
            ))}
          </div>
          <Link className="opsPanelAction" href="/console/alerts">Open delivery operations</Link>
        </section>
      </div>
    </main>
  );
}
