"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import type { ApprovalRequest, DeskPrincipal } from "../types";

type Filter = "all" | ApprovalRequest["status"];

async function detail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    return body.detail ?? `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

function formatTime(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(new Date(value));
}

function label(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function remaining(value: string): string {
  const milliseconds = new Date(value).getTime() - Date.now();
  if (milliseconds <= 0) return "Expired";
  const minutes = Math.ceil(milliseconds / 60_000);
  if (minutes < 60) return `${minutes} min remaining`;
  return `${Math.ceil(minutes / 60)} hr remaining`;
}

export default function ApprovalInbox() {
  const [principal, setPrincipal] = useState<DeskPrincipal | null>(null);
  const [requests, setRequests] = useState<ApprovalRequest[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [reason, setReason] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const fetchInbox = useCallback(async () => {
    const [meResponse, queueResponse] = await Promise.all([
      fetch("/api/console/desk/me", { cache: "no-store" }),
      fetch("/api/console/approval-requests?limit=250", { cache: "no-store" }),
    ]);
    if (!meResponse.ok) throw new Error(await detail(meResponse));
    if (!queueResponse.ok) throw new Error(await detail(queueResponse));
    const me = (await meResponse.json()) as DeskPrincipal;
    const queue = (await queueResponse.json()) as ApprovalRequest[];
    return { me, queue };
  }, []);

  const load = useCallback(async () => {
    const { me, queue } = await fetchInbox();
    setPrincipal(me);
    setRequests(queue);
    setSelectedId((current) =>
      current && queue.some((request) => request.id === current)
        ? current
        : (queue.find((request) => request.status === "pending")?.id ?? queue[0]?.id ?? null),
    );
  }, [fetchInbox]);

  useEffect(() => {
    let cancelled = false;
    void fetchInbox()
      .then(({ me, queue }) => {
        if (cancelled) return;
        setPrincipal(me);
        setRequests(queue);
        setSelectedId(queue.find((request) => request.status === "pending")?.id ?? queue[0]?.id ?? null);
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : "Approval inbox could not be loaded");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [fetchInbox]);

  const filtered = useMemo(
    () => requests.filter((request) => filter === "all" || request.status === filter),
    [filter, requests],
  );
  const selected = requests.find((request) => request.id === selectedId) ?? null;
  const strongAuthentication =
    principal?.auth_assurance === "mfa" || principal?.auth_assurance === "phishing_resistant";
  const approverRole = principal?.role === "senior_analyst" || principal?.role === "administrator";
  const mayDecide = Boolean(
    selected &&
      selected.status === "pending" &&
      approverRole &&
      strongAuthentication &&
      principal?.id !== selected.primary_user_id,
  );

  const decide = async (decision: "approve" | "reject") => {
    if (!selected || reason.trim().length < 10) {
      setError("Record a decision reason of at least 10 characters.");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const response = await fetch(
        `/api/console/approval-requests/${encodeURIComponent(selected.id)}/decision`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ decision, reason: reason.trim() }),
        },
      );
      if (!response.ok) throw new Error(await detail(response));
      const updated = (await response.json()) as ApprovalRequest;
      setRequests((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      setReason("");
      setNotice(
        decision === "approve"
          ? "Exact content approved. The requester has 15 minutes to release it."
          : "Request rejected with an immutable audit reason.",
      );
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Approval decision failed");
    } finally {
      setBusy(false);
    }
  };

  const copyReleaseHash = async () => {
    if (!selected?.release_hash) return;
    await navigator.clipboard.writeText(selected.release_hash);
    setNotice("Release hash copied.");
  };

  if (loading) return <main className="approvalLoading">Loading authenticated approvals…</main>;

  return (
    <main className="approvalShell">
      <header className="approvalHeader">
        <div className="consoleIdentity">
          <span className="wordmarkMark">EM</span>
          <div>
            <p className="eyebrow">Release control</p>
            <h1>Approval inbox</h1>
          </div>
        </div>
        <nav className="consoleHeaderNav" aria-label="Console navigation">
          <Link href="/console">Dashboard</Link>
          <Link href="/console/triage">Triage</Link>
          <Link href="/console/events">Events</Link>
          <Link href="/console/quality">Quality</Link>
        </nav>
        {principal ? (
          <div className="approvalIdentity">
            <strong>{principal.display_name}</strong>
            <span>{label(principal.role)}</span>
            <span className={`assurance assurance--${principal.auth_assurance}`}>
              {label(principal.auth_assurance)}
            </span>
          </div>
        ) : null}
      </header>

      {!strongAuthentication ? (
        <section className="approvalWarning" role="alert">
          <strong>Strong authentication required</strong>
          <p>This session can inspect requests but cannot approve them. Sign in with MFA.</p>
        </section>
      ) : null}
      {error ? <p className="consoleBanner consoleBanner--error" role="alert">{error}</p> : null}
      {notice ? <p className="consoleBanner" role="status">{notice}</p> : null}

      <section className="approvalSummary" aria-label="Approval queue summary">
        {(["pending", "approved", "rejected", "expired"] as const).map((status) => (
          <button key={status} type="button" onClick={() => setFilter(status)}>
            <strong>{requests.filter((request) => request.status === status).length}</strong>
            <span>{label(status)}</span>
          </button>
        ))}
        <button type="button" onClick={() => setFilter("all")}>
          <strong>{requests.length}</strong>
          <span>All requests</span>
        </button>
      </section>

      <div className="approvalGrid">
        <section className="approvalQueue" aria-label="Approval requests">
          <div className="approvalSectionHeading">
            <div>
              <p className="eyebrow">Queue</p>
              <h2>{label(filter)}</h2>
            </div>
            <button type="button" onClick={() => void load()} disabled={busy}>Refresh</button>
          </div>
          {filtered.length ? filtered.map((request) => (
            <button
              className={request.id === selectedId ? "approvalQueueItem approvalQueueItem--active" : "approvalQueueItem"}
              key={request.id}
              type="button"
              onClick={() => { setSelectedId(request.id); setReason(""); setError(null); }}
            >
              <span className={`approvalStatus approvalStatus--${request.status}`}>{label(request.status)}</span>
              <strong>{label(request.request_type)}</strong>
              <small>{request.primary_display_name}</small>
              <time>{formatTime(request.requested_at)}</time>
            </button>
          )) : <p className="approvalEmpty">No requests in this view.</p>}
        </section>

        <section className="approvalReview" aria-label="Exact approval review">
          {selected ? (
            <>
              <div className="approvalReviewHeader">
                <div>
                  <p className="eyebrow">Exact transaction</p>
                  <h2>{label(selected.request_type)}</h2>
                </div>
                <span className={`approvalStatus approvalStatus--${selected.status}`}>
                  {label(selected.status)}
                </span>
              </div>
              <dl className="approvalFacts">
                <div><dt>Requester</dt><dd>{selected.primary_display_name}</dd></div>
                <div><dt>Target</dt><dd><code>{selected.target_id}</code></dd></div>
                <div><dt>Request expires</dt><dd>{formatTime(selected.expires_at)} · {remaining(selected.expires_at)}</dd></div>
                <div><dt>Request hash</dt><dd><code>{selected.request_hash}</code></dd></div>
              </dl>
              <div className="approvalReason">
                <span>Requester rationale</span>
                <p>{selected.request_reason}</p>
              </div>
              <details className="approvalPayload" open>
                <summary>Review exact bound payload</summary>
                <pre>{JSON.stringify(selected.request_payload.draft ?? {}, null, 2)}</pre>
              </details>

              {selected.status === "pending" ? (
                <div className="approvalDecision">
                  <label>
                    <span>Independent decision reason</span>
                    <textarea
                      value={reason}
                      onChange={(event) => setReason(event.target.value)}
                      placeholder="State what evidence, mappings, impact, and changes you checked."
                      minLength={10}
                      maxLength={2000}
                    />
                  </label>
                  <div>
                    <button
                      className="approvalReject"
                      type="button"
                      disabled={!mayDecide || busy || reason.trim().length < 10}
                      onClick={() => void decide("reject")}
                    >
                      Reject exact request
                    </button>
                    <button
                      className="approvalApprove"
                      type="button"
                      disabled={!mayDecide || busy || reason.trim().length < 10}
                      onClick={() => void decide("approve")}
                    >
                      Approve with MFA
                    </button>
                  </div>
                  {!mayDecide ? (
                    <small>You need a distinct senior/admin MFA session to decide this request.</small>
                  ) : null}
                </div>
              ) : null}

              {selected.release_hash ? (
                <div className="approvalReleaseReceipt">
                  <span>Approved release hash</span>
                  <code>{selected.release_hash}</code>
                  <p>Valid until {formatTime(selected.release_expires_at)}.</p>
                  <button type="button" onClick={() => void copyReleaseHash()}>Copy release hash</button>
                  {selected.request_type === "event_publication" ? (
                    <Link href={`/console/events/${encodeURIComponent(selected.target_id)}`}>
                      Open event workspace
                    </Link>
                  ) : <Link href="/console/quality">Open correction desk</Link>}
                </div>
              ) : null}

              {selected.decision_reason ? (
                <div className="approvalDecisionRecord">
                  <span>Decision record</span>
                  <p>{selected.decision_reason}</p>
                  <small>{selected.decided_by_display_name ?? "System"} · {formatTime(selected.decided_at)}</small>
                </div>
              ) : null}
            </>
          ) : <p className="approvalEmpty">Select a request to review its exact content.</p>}
        </section>
      </div>
    </main>
  );
}
