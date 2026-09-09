"use client";

import Link from "next/link";
import { type FormEvent, useCallback, useEffect, useState } from "react";

import type {
  ClaimExtractionEvaluation,
  ClaimExtractionProposal,
  ClaimExtractionReview,
  ClaimExtractionWeeklyReport,
  ClaimState,
  OpenEvent,
} from "../types";

type Decision = "accept" | "edit" | "reject";

const reasonOptions = [
  "wrong_attribution",
  "not_explicit",
  "combined_claims",
  "quote_mismatch",
  "wrong_time_or_place",
  "not_material",
  "other",
];

const claimStates: { value: ClaimState; label: string }[] = [
  { value: "confirmed", label: "Confirmed" },
  { value: "reported", label: "Reported" },
  { value: "unverified", label: "Unverified" },
  { value: "corroborated_2_independent", label: "Corroborated 2×" },
  { value: "single_source_official", label: "Single official source" },
  { value: "disputed", label: "Disputed" },
];

async function detail(response: Response): Promise<string> {
  try {
    return ((await response.json()) as { detail?: string }).detail ?? `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

function label(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export default function ExtractionConsole() {
  const [proposals, setProposals] = useState<ClaimExtractionProposal[]>([]);
  const [events, setEvents] = useState<OpenEvent[]>([]);
  const [report, setReport] = useState<ClaimExtractionWeeklyReport | null>(null);
  const [evaluation, setEvaluation] = useState<ClaimExtractionEvaluation | null>(null);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reviewer, setReviewer] = useState("desk-analyst");
  const [decision, setDecision] = useState<Decision>("accept");
  const [eventId, setEventId] = useState("");
  const [claimState, setClaimState] = useState<ClaimState>("reported");
  const [editedText, setEditedText] = useState("");
  const [editedClaimant, setEditedClaimant] = useState("");
  const [reasons, setReasons] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [baselineSeconds, setBaselineSeconds] = useState(120);
  const [reviewStartedAt, setReviewStartedAt] = useState(0);
  const [pendingQa, setPendingQa] = useState<ClaimExtractionReview | null>(null);
  const [qaEvaluator, setQaEvaluator] = useState("qa-editor");
  const [qaError, setQaError] = useState(false);
  const [qaCodes, setQaCodes] = useState("");

  const selected = proposals[selectedIndex] ?? null;

  const fetchDeskData = useCallback(async () => {
    const [proposalResponse, eventResponse, reportResponse] = await Promise.all([
        fetch("/api/console/claim-extraction/proposals?status=pending", { cache: "no-store" }),
        fetch("/api/console/events", { cache: "no-store" }),
        fetch("/api/console/claim-extraction/weekly-report", { cache: "no-store" }),
    ]);
    if (!proposalResponse.ok) throw new Error(await detail(proposalResponse));
    if (!eventResponse.ok) throw new Error(await detail(eventResponse));
    if (!reportResponse.ok) throw new Error(await detail(reportResponse));
    return {
      proposals: (await proposalResponse.json()) as ClaimExtractionProposal[],
      events: (await eventResponse.json()) as OpenEvent[],
      report: (await reportResponse.json()) as ClaimExtractionWeeklyReport,
    };
  }, []);

  const resetReviewForm = useCallback((proposal: ClaimExtractionProposal | null) => {
    setEditedText(proposal?.text ?? "");
    setEditedClaimant(proposal?.claimant ?? "");
    setDecision("accept");
    setEventId("");
    setClaimState("reported");
    setReasons([]);
    setNote("");
    setReviewStartedAt(Date.now());
  }, []);

  const applyDeskData = useCallback(
    (data: {
      proposals: ClaimExtractionProposal[];
      events: OpenEvent[];
      report: ClaimExtractionWeeklyReport;
    }) => {
      setProposals(data.proposals);
      setEvents(data.events);
      setReport(data.report);
      setSelectedIndex(0);
      resetReviewForm(data.proposals[0] ?? null);
    },
    [resetReviewForm],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      applyDeskData(await fetchDeskData());
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Extraction queue could not load");
    } finally {
      setLoading(false);
    }
  }, [applyDeskData, fetchDeskData]);

  useEffect(() => {
    let cancelled = false;
    void fetchDeskData()
      .then((data) => {
        if (!cancelled) applyDeskData(data);
      })
      .catch((loadError: unknown) => {
        if (!cancelled) {
          setError(
            loadError instanceof Error ? loadError.message : "Extraction queue could not load",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [applyDeskData, fetchDeskData]);

  function toggleReason(reason: string) {
    setReasons((current) =>
      current.includes(reason) ? current.filter((item) => item !== reason) : [...current, reason],
    );
  }

  async function submitReview(event: FormEvent) {
    event.preventDefault();
    if (!selected) return;
    setSubmitting(true);
    setError(null);
    setNotice(null);
    try {
      const response = await fetch(
        `/api/console/claim-extraction/proposals/${encodeURIComponent(selected.id)}/review`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            decision,
            reviewer,
            event_id: decision === "reject" ? null : eventId || null,
            claim_state: decision === "reject" ? null : claimState,
            edited_claim:
              decision === "edit"
                ? {
                    text: editedText,
                    claimant: editedClaimant || null,
                    occurred_time: selected.occurred_time,
                    location: selected.location?.name ? selected.location : null,
                    quantities: selected.quantities,
                    hedging_language: selected.hedging_language,
                    source_sentence_quote: selected.source_sentence_quote,
                  }
                : null,
            reason_codes: reasons,
            note: note || null,
            baseline_seconds: baselineSeconds,
            review_seconds: Math.max(1, Math.round((Date.now() - reviewStartedAt) / 1000)),
          }),
        },
      );
      if (!response.ok) throw new Error(await detail(response));
      const review = (await response.json()) as ClaimExtractionReview;
      setPendingQa(review);
      const remaining = proposals.filter((proposal) => proposal.id !== selected.id);
      setProposals(remaining);
      setSelectedIndex(0);
      resetReviewForm(remaining[0] ?? null);
      setNotice(`${label(decision)} recorded in ${review.mode} mode. No claim was auto-published.`);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Review could not be recorded");
    } finally {
      setSubmitting(false);
    }
  }

  async function submitQa(event: FormEvent) {
    event.preventDefault();
    if (!pendingQa) return;
    const errorCodes = qaCodes.split(",").map((code) => code.trim()).filter(Boolean);
    setSubmitting(true);
    setError(null);
    try {
      const response = await fetch(
        `/api/console/claim-extraction/reviews/${encodeURIComponent(pendingQa.id)}/qa`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            evaluator: qaEvaluator,
            error_found: qaError,
            error_codes: qaError ? errorCodes : [],
            note: null,
          }),
        },
      );
      if (!response.ok) throw new Error(await detail(response));
      setPendingQa(null);
      setQaError(false);
      setQaCodes("");
      setNotice("Independent QA recorded. The audit row is immutable.");
      await load();
    } catch (qaSubmitError) {
      setError(qaSubmitError instanceof Error ? qaSubmitError.message : "QA could not be recorded");
    } finally {
      setSubmitting(false);
    }
  }

  async function runEvaluation() {
    const language = selected?.source_language ?? Object.keys(report?.languages ?? {})[0] ?? "en";
    setSubmitting(true);
    setError(null);
    try {
      const response = await fetch(
        `/api/console/claim-extraction/evaluations/${encodeURIComponent(language)}`,
        { method: "POST" },
      );
      if (!response.ok) throw new Error(await detail(response));
      setEvaluation((await response.json()) as ClaimExtractionEvaluation);
    } catch (evaluationError) {
      setError(evaluationError instanceof Error ? evaluationError.message : "Evaluation failed");
    } finally {
      setSubmitting(false);
    }
  }

  const needsReason = decision === "edit" || decision === "reject";
  const canSubmit = Boolean(
    selected &&
      reviewer.trim().length >= 2 &&
      baselineSeconds > 0 &&
      (!needsReason || reasons.length) &&
      (decision === "reject" || eventId) &&
      (decision !== "edit" || editedText.trim().length >= 3),
  );

  return (
    <main className="extractionShell">
      <header className="eventWorkspaceHeader">
        <div className="eventWorkspaceBrand">
          <span className="wordmarkMark">EM</span>
          <div><p className="eyebrow">S8 · analyst assist</p><h1>Claim extraction shadow desk</h1></div>
        </div>
        <nav className="workspaceNav" aria-label="Console navigation">
          <Link href="/console">Triage</Link><Link href="/console/events">Events</Link>
          <Link href="/console/extraction" aria-current="page">Extraction</Link>
          <Link href="/console/alerts">Alerts</Link><Link href="/console/briefs">Brief</Link><Link href="/console/quality">Quality</Link>
        </nav>
        <div className="shadowBadge">Shadow only · human decision required</div>
      </header>

      {error ? <p className="consoleBanner consoleBanner--error" role="alert">{error}</p> : null}
      {notice ? <p className="consoleBanner" role="status">{notice}</p> : null}

      <section className="extractionScorecard" aria-label="Shadow programme scorecard">
        <article><span>Pending proposals</span><strong>{proposals.length}</strong></article>
        <article><span>Reviewed this prompt</span><strong>{report?.reviewed ?? "—"}</strong></article>
        <article><span>Prompt version</span><strong>{report?.prompt_version ?? "—"}</strong></article>
        <article>
          <span>Graduation</span>
          <strong>{evaluation?.graduated ? "Eligible" : "Blocked"}</strong>
          <button onClick={() => void runEvaluation()} disabled={submitting}>Evaluate</button>
        </article>
      </section>

      <section className="extractionGrid">
        <aside className="extractionQueue" aria-label="Pending model proposals">
          <div className="panelHeading"><div><p className="eyebrow">Untrusted output</p><h2>Queue</h2></div><button onClick={() => void load()}>Refresh</button></div>
          {loading ? <p className="emptyState">Loading proposals…</p> : null}
          {!loading && !proposals.length ? <p className="emptyState">No pending proposals.</p> : null}
          {proposals.map((proposal, index) => (
            <button key={proposal.id} className={index === selectedIndex ? "extractionQueueCard extractionQueueCard--active" : "extractionQueueCard"} onClick={() => { setSelectedIndex(index); resetReviewForm(proposal); }}>
              <small>{proposal.source_language} · {proposal.source_name}</small>
              <strong>{proposal.text}</strong>
              <span>{proposal.hedging_language ? "Attributed / hedged" : "Direct assertion"}</span>
            </button>
          ))}
        </aside>

        <article className="extractionSource">
          {selected ? <>
            <div className="recordMeta"><span>{selected.source_name}</span><span>{selected.source_language}</span><span>{selected.prompt_version}</span></div>
            <h2>{selected.source_title ?? "Untitled source record"}</h2>
            <a className="sourceLink" href={selected.source_url} target="_blank" rel="noreferrer">Open captured source ↗</a>
            <blockquote>{selected.source_sentence_quote}</blockquote>
            <div className="recordText">{selected.source_text}</div>
          </> : <div className="recordEmpty"><p className="eyebrow">Queue clear</p><h2>No proposal selected</h2><p>The model never publishes or assigns truth labels.</p></div>}
        </article>

        <aside className="extractionReview">
          {pendingQa ? (
            <form onSubmit={submitQa} className="extractionForm">
              <p className="eyebrow">Independent quality check</p><h2>QA this review</h2>
              <p className="formNotice">Decision: {label(pendingQa.decision)} · reviewer: {pendingQa.reviewer}</p>
              <label><span>QA evaluator</span><input value={qaEvaluator} onChange={(event) => setQaEvaluator(event.target.value)} /></label>
              <label className="checkRow"><input type="checkbox" checked={qaError} onChange={(event) => setQaError(event.target.checked)} /><span>Error found</span></label>
              {qaError ? <label><span>Error codes, comma separated</span><input value={qaCodes} onChange={(event) => setQaCodes(event.target.value)} placeholder="wrong_attribution" /></label> : null}
              <button className="commitButton" disabled={submitting || (qaError && !qaCodes.trim())}>Record immutable QA</button>
            </form>
          ) : selected ? (
            <form onSubmit={submitReview} className="extractionForm">
              <p className="eyebrow">Human judgment gate</p><h2>Review proposal</h2>
              <p className="formNotice">Shadow mode stores your decision and timing. It creates no claim and publishes nothing.</p>
              <label><span>Reviewer</span><input value={reviewer} onChange={(event) => setReviewer(event.target.value)} maxLength={255} /></label>
              <fieldset><legend>Decision</legend><div className="decisionTabs">{(["accept", "edit", "reject"] as Decision[]).map((value) => <button type="button" key={value} aria-pressed={decision === value} onClick={() => setDecision(value)}>{label(value)}</button>)}</div></fieldset>
              {decision !== "reject" ? <>
                <label><span>Analyst-selected event</span><select value={eventId} onChange={(event) => setEventId(event.target.value)}><option value="">Select event…</option>{events.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
                <label><span>Analyst-selected claim state</span><select value={claimState} onChange={(event) => setClaimState(event.target.value as ClaimState)}>{claimStates.map((state) => <option key={state.value} value={state.value}>{state.label}</option>)}</select></label>
              </> : null}
              {decision === "edit" ? <>
                <label><span>Final claim text</span><textarea value={editedText} onChange={(event) => setEditedText(event.target.value)} /></label>
                <label><span>Final claimant</span><input value={editedClaimant} onChange={(event) => setEditedClaimant(event.target.value)} /></label>
              </> : null}
              {needsReason ? <fieldset><legend>Reason codes</legend><div className="reasonGrid">{reasonOptions.map((reason) => <label key={reason}><input type="checkbox" checked={reasons.includes(reason)} onChange={() => toggleReason(reason)} /><span>{label(reason)}</span></label>)}</div></fieldset> : null}
              <label><span>Optional note</span><textarea value={note} onChange={(event) => setNote(event.target.value)} /></label>
              <div className="timingFields"><label><span>Manual baseline seconds</span><input type="number" min={1} value={baselineSeconds} onChange={(event) => setBaselineSeconds(Number(event.target.value))} /></label><div><span>Review timer</span><strong>Active</strong></div></div>
              <button className="commitButton" disabled={submitting || !canSubmit}>{submitting ? "Recording…" : `Record ${decision}`}</button>
            </form>
          ) : <p className="emptyState">Select a proposal to review it.</p>}
        </aside>
      </section>

      <section className="extractionReport">
        <div><p className="eyebrow">Weekly prompt iteration</p><h2>Observed correction reasons</h2></div>
        <div>{Object.entries(report?.reason_codes ?? {}).map(([reason, count]) => <span key={reason}><strong>{count}</strong>{label(reason)}</span>)}{!Object.keys(report?.reason_codes ?? {}).length ? <p className="emptyState">No review reasons recorded yet.</p> : null}</div>
        {evaluation ? <p>{evaluation.reviewed} reviewed · {evaluation.qa_reviewed} QA · {evaluation.shadow_days.toFixed(1)} shadow days · {evaluation.time_reduction_percent.toFixed(1)}% time reduction · baseline error {evaluation.baseline_error_rate === null ? "not configured" : `${(evaluation.baseline_error_rate * 100).toFixed(1)}%`}</p> : null}
      </section>
    </main>
  );
}
