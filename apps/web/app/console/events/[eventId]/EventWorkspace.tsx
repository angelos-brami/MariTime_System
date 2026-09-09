"use client";

import Link from "next/link";
import { type FormEvent, useEffect, useMemo, useState } from "react";

import type {
  ApprovalRequest,
  ClaimState,
  DeskPrincipal,
  EventWorkspaceData,
  PublicationPreview,
  PublicationSection,
  PublicationSentence,
  WorkspaceClaim,
  WorkspaceSource,
} from "../../types";

const sections: { value: PublicationSection; label: string; guidance: string }[] = [
  { value: "confirmed", label: "Confirmed", guidance: "Evidence-backed facts only" },
  { value: "reported", label: "Reported", guidance: "Attributed reports and disputed claims" },
  { value: "unknown", label: "Unknown", guidance: "Explicitly unverified state" },
  { value: "changed", label: "What changed", guidance: "Delta from the previous version" },
];

const claimStates: { value: ClaimState; label: string }[] = [
  { value: "confirmed", label: "Confirmed" },
  { value: "corroborated_2_independent", label: "Corroborated 2×" },
  { value: "single_source_official", label: "Single official source" },
  { value: "reported", label: "Reported" },
  { value: "unverified", label: "Unverified" },
  { value: "disputed", label: "Disputed" },
];

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(new Date(value));
}

function label(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

async function responseDetail(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: string };
    return payload.detail ?? `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

function isCompatible(section: PublicationSection, state: ClaimState): boolean {
  if (section === "changed") return true;
  if (section === "confirmed") {
    return ["confirmed", "corroborated_2_independent", "single_source_official"].includes(state);
  }
  if (section === "reported") return ["reported", "disputed"].includes(state);
  return ["unverified", "disputed"].includes(state);
}

function mayPublishExcerpt(rightsBasis: string): boolean {
  return ["public-advisory", "licensed", "attribute-quote-min"].includes(rightsBasis);
}

export default function EventWorkspace({ eventId }: { eventId: string }) {
  const [workspace, setWorkspace] = useState<EventWorkspaceData | null>(null);
  const [title, setTitle] = useState("");
  const [draftSentences, setDraftSentences] = useState<PublicationSentence[]>([]);
  const [selectedClaimId, setSelectedClaimId] = useState<string | null>(null);
  const [reviewer, setReviewer] = useState("desk-analyst");
  const [principal, setPrincipal] = useState<DeskPrincipal | null>(null);
  const [approvalReason, setApprovalReason] = useState("");
  const [approvalRequest, setApprovalRequest] = useState<ApprovalRequest | null>(null);
  const [preview, setPreview] = useState<PublicationPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showClaimForm, setShowClaimForm] = useState(false);
  const [claimText, setClaimText] = useState("");
  const [claimant, setClaimant] = useState("");
  const [claimState, setClaimState] = useState<ClaimState>("reported");
  const [secondReviewReason, setSecondReviewReason] = useState("");

  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      fetch(`/api/console/events/${encodeURIComponent(eventId)}/workspace`, {
        cache: "no-store",
      }),
      fetch("/api/console/desk/me", { cache: "no-store" }),
      fetch("/api/console/approval-requests?limit=250", { cache: "no-store" }),
    ])
      .then(async ([workspaceResponse, meResponse, approvalsResponse]) => {
        if (!workspaceResponse.ok) throw new Error(await responseDetail(workspaceResponse));
        if (!meResponse.ok) throw new Error(await responseDetail(meResponse));
        if (!approvalsResponse.ok) throw new Error(await responseDetail(approvalsResponse));
        return {
          workspace: (await workspaceResponse.json()) as EventWorkspaceData,
          principal: (await meResponse.json()) as DeskPrincipal,
          approvals: (await approvalsResponse.json()) as ApprovalRequest[],
        };
      })
      .then(({ workspace: next, principal: me, approvals }) => {
        if (cancelled) return;
        setWorkspace(next);
        setPrincipal(me);
        setReviewer(`desk:${me.id}`);
        setTitle(next.title);
        setDraftSentences(next.versions[0]?.sentences.map((sentence) => ({ ...sentence })) ?? []);
        setSelectedClaimId(next.claims[0]?.id ?? null);
        setApprovalRequest(
          approvals.find(
            (request) =>
              request.request_type === "event_publication" &&
              request.target_id === eventId &&
              request.primary_user_id === me.id &&
              ["pending", "approved"].includes(request.status),
          ) ?? null,
        );
        setPreview(null);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : "Event workspace could not be loaded");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [eventId]);

  const selectedClaim = useMemo(
    () => workspace?.claims.find((claim) => claim.id === selectedClaimId) ?? null,
    [selectedClaimId, workspace],
  );

  const mappedClaimIds = useMemo(
    () => new Set(draftSentences.flatMap((sentence) => sentence.claim_ids)),
    [draftSentences],
  );
  const evidenceIds = useMemo(
    () =>
      workspace?.claims
        .filter((claim) => mappedClaimIds.has(claim.id))
        .flatMap((claim) => claim.evidence.map((evidence) => evidence.id)) ?? [],
    [mappedClaimIds, workspace],
  );

  const invalidatePreview = () => {
    setPreview(null);
    setApprovalRequest(null);
    setNotice(null);
  };

  async function secondReviewSelectedClaim() {
    if (!selectedClaim) return;
    setSubmitting(true);
    setError(null);
    setNotice(null);
    try {
      const response = await fetch(
        `/api/console/events/${encodeURIComponent(eventId)}/claims/${encodeURIComponent(selectedClaim.id)}/second-review`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ reason: secondReviewReason }),
        },
      );
      if (!response.ok) throw new Error(await responseDetail(response));
      const workspaceResponse = await fetch(
        `/api/console/events/${encodeURIComponent(eventId)}/workspace`,
        { cache: "no-store" },
      );
      if (!workspaceResponse.ok) throw new Error(await responseDetail(workspaceResponse));
      setWorkspace((await workspaceResponse.json()) as EventWorkspaceData);
      setSecondReviewReason("");
      invalidatePreview();
      setNotice(
        "Sensitive claim independently reviewed and bound to its exact evidence package.",
      );
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Second review failed");
    } finally {
      setSubmitting(false);
    }
  }

  const updateSentence = (index: number, patch: Partial<PublicationSentence>) => {
    setDraftSentences((current) =>
      current.map((sentence, sentenceIndex) =>
        sentenceIndex === index ? { ...sentence, ...patch } : sentence,
      ),
    );
    invalidatePreview();
  };

  const addSentence = (section: PublicationSection) => {
    const suggested = workspace?.claims.find((claim) => isCompatible(section, claim.claim_state));
    setDraftSentences((current) => [
      ...current,
      {
        section,
        text: section === "changed" ? "" : suggested?.text ?? "",
        claim_ids: suggested ? [suggested.id] : [],
      },
    ]);
    invalidatePreview();
  };

  const removeSentence = (index: number) => {
    setDraftSentences((current) => current.filter((_, sentenceIndex) => sentenceIndex !== index));
    invalidatePreview();
  };

  const toggleClaim = (index: number, claimId: string) => {
    const sentence = draftSentences[index];
    const claimIds = sentence.claim_ids.includes(claimId)
      ? sentence.claim_ids.filter((id) => id !== claimId)
      : [...sentence.claim_ids, claimId];
    updateSentence(index, { claim_ids: claimIds });
  };

  const submitClaim = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!claimText.trim() || !workspace) return;
    setSubmitting(true);
    setError(null);
    try {
      const response = await fetch(`/api/console/events/${encodeURIComponent(eventId)}/claims`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          text: claimText.trim(),
          claimant: claimant.trim() || null,
          claim_state: claimState,
          reviewer,
          sensitivity_flags: [],
        }),
      });
      if (!response.ok) throw new Error(await responseDetail(response));
      const claim = (await response.json()) as WorkspaceClaim;
      setWorkspace({ ...workspace, claims: [...workspace.claims, claim] });
      setSelectedClaimId(claim.id);
      setClaimText("");
      setClaimant("");
      setShowClaimForm(false);
      setNotice("Claim added with a human review audit entry.");
      setPreview(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Claim could not be added");
    } finally {
      setSubmitting(false);
    }
  };

  const reviseClaimState = async (claim: WorkspaceClaim, nextState: ClaimState) => {
    if (!workspace || claim.claim_state === nextState) return;
    setSubmitting(true);
    setError(null);
    try {
      const response = await fetch(
        `/api/console/events/${encodeURIComponent(eventId)}/claims/${encodeURIComponent(claim.id)}`,
        {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ claim_state: nextState, reviewer }),
        },
      );
      if (!response.ok) throw new Error(await responseDetail(response));
      const updated = (await response.json()) as WorkspaceClaim;
      setWorkspace({
        ...workspace,
        claims: workspace.claims.map((item) =>
          item.id === claim.id ? { ...claim, ...updated, claim_state: nextState } : item,
        ),
      });
      setNotice("Claim state reviewed and audited.");
      setPreview(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Claim state could not be updated");
    } finally {
      setSubmitting(false);
    }
  };

  const linkSource = async (source: WorkspaceSource) => {
    if (!workspace || !selectedClaim) return;
    setSubmitting(true);
    setError(null);
    try {
      const response = await fetch(
        `/api/console/events/${encodeURIComponent(eventId)}/claims/${encodeURIComponent(selectedClaim.id)}/evidence`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            source_record_id: source.id,
            directness: "primary",
            excerpt: mayPublishExcerpt(source.rights_basis) ? source.text.slice(0, 500) : null,
            reviewer,
          }),
        },
      );
      if (!response.ok) throw new Error(await responseDetail(response));
      const linked = (await response.json()) as {
        id: string;
        lineage_root_id: string;
        directness: "primary" | "secondary";
        excerpt: string | null;
      };
      const evidence = {
        id: linked.id,
        source_record_id: source.id,
        source_name: source.source_name,
        source_tier: source.source_tier,
        url: source.url,
        directness: linked.directness,
        lineage_root_id: linked.lineage_root_id,
        excerpt: linked.excerpt,
        rights_basis: source.rights_basis,
      };
      setWorkspace({
        ...workspace,
        claims: workspace.claims.map((claim) =>
          claim.id === selectedClaim.id
            ? { ...claim, evidence: [...claim.evidence, evidence] }
            : claim,
        ),
        sources: workspace.sources.map((item) =>
          item.id === source.id
            ? { ...item, linked_claim_ids: [...item.linked_claim_ids, selectedClaim.id] }
            : item,
        ),
      });
      setNotice("Evidence linked with lineage and rights state captured.");
      setPreview(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Evidence could not be linked");
    } finally {
      setSubmitting(false);
    }
  };

  const contentDraft = () => ({
    title: title.trim(),
    sentences: draftSentences.map((sentence) => ({
      ...sentence,
      text: sentence.text.trim(),
    })),
    evidence_ids: evidenceIds,
    policy_version: "publication-policy-v1",
    model_versions: {},
  });

  const draftPayload = () => ({
    ...contentDraft(),
    published_by: reviewer,
    signed_off_by: null,
  });

  const requestApproval = async () => {
    if (!workspace || workspace.severity < 3) return;
    if (approvalReason.trim().length < 10) {
      setError("Record why this exact release is ready for independent approval.");
      return;
    }
    setSubmitting(true);
    setError(null);
    setNotice(null);
    try {
      const response = await fetch(
        `/api/console/events/${encodeURIComponent(eventId)}/publication-requests`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ draft: contentDraft(), reason: approvalReason.trim() }),
        },
      );
      if (!response.ok) throw new Error(await responseDetail(response));
      const request = (await response.json()) as ApprovalRequest;
      setApprovalRequest(request);
      setNotice("Exact draft submitted to the authenticated approval inbox.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Approval request could not be created");
    } finally {
      setSubmitting(false);
    }
  };

  const refreshApproval = async () => {
    if (!principal) return;
    setSubmitting(true);
    setError(null);
    try {
      const response = await fetch("/api/console/approval-requests?limit=250", {
        cache: "no-store",
      });
      if (!response.ok) throw new Error(await responseDetail(response));
      const requests = (await response.json()) as ApprovalRequest[];
      const request = requests.find(
        (item) =>
          item.request_type === "event_publication" &&
          item.target_id === eventId &&
          item.primary_user_id === principal.id &&
          ["pending", "approved"].includes(item.status),
      );
      setApprovalRequest(request ?? null);
      setNotice(request?.status === "approved" ? "Approval received. Release before it expires." : "Approval state refreshed.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Approval state could not be refreshed");
    } finally {
      setSubmitting(false);
    }
  };

  const requestPreview = async () => {
    if (!workspace) return;
    if (!title.trim() || draftSentences.some((sentence) => !sentence.text.trim() || !sentence.claim_ids.length)) {
      setError("Every draft sentence needs text and at least one claim mapping.");
      return;
    }
    setSubmitting(true);
    setError(null);
    setNotice(null);
    try {
      const response = await fetch(
        `/api/console/events/${encodeURIComponent(eventId)}/versions/preview`,
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(draftPayload()),
        },
      );
      if (!response.ok) throw new Error(await responseDetail(response));
      const nextPreview = (await response.json()) as PublicationPreview;
      setPreview(nextPreview);
      setNotice(
        nextPreview.ready
          ? `Version ${nextPreview.next_version_no} passed every publication invariant.`
          : "The server found publication blockers. Resolve them and preview again.",
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Preview could not be generated");
    } finally {
      setSubmitting(false);
    }
  };

  const publish = async () => {
    if (!workspace) return;
    const releaseHash =
      workspace.severity >= 3 ? approvalRequest?.release_hash : preview?.preview_hash;
    if (!releaseHash) return;
    setSubmitting(true);
    setError(null);
    try {
      const response = await fetch(`/api/console/events/${encodeURIComponent(eventId)}/versions`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ...draftPayload(), preview_hash: releaseHash }),
      });
      if (!response.ok) throw new Error(await responseDetail(response));
      const published = (await response.json()) as {
        version_no: number;
        content_hash: string;
        published_at: string;
      };
      setNotice(
        `Version ${published.version_no} published immutably · ${published.content_hash.slice(0, 12)}…`,
      );
      setPreview(null);
      setApprovalRequest(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Version could not be published");
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) return <main className="eventWorkspaceLoading">Loading event workspace…</main>;
  if (!workspace) {
    return <main className="eventWorkspaceLoading consoleBanner--error">{error ?? "Event not found"}</main>;
  }

  return (
    <main className="eventWorkspaceShell">
      <header className="eventWorkspaceHeader">
        <div className="eventWorkspaceBrand">
          <span className="wordmarkMark">EM</span>
          <div>
            <p className="eyebrow">Event workspace</p>
            <h1>{workspace.title}</h1>
          </div>
        </div>
        <div className="eventHeaderMeta">
          <span className={`severityBadge severityBadge--${workspace.severity}`}>S{workspace.severity}</span>
          <span>{label(workspace.status)}</span>
          <span>{label(workspace.corridor)}</span>
          <span>{label(workspace.event_type)}</span>
        </div>
        <nav className="workspaceNav" aria-label="Console navigation">
          <Link href="/console">Dashboard</Link>
          <Link href="/console/triage">Triage</Link>
          <Link href="/console/events">Events</Link>
          <Link href="/console/alerts">Alerts</Link>
          <Link href="/console/extraction">Extraction</Link>
          <Link href="/console/briefs">Brief</Link>
          <Link href="/console/quality">Quality</Link>
        </nav>
      </header>

      <section className="workspaceControlBar">
        <label>
          <span>Authenticated publisher</span>
          <input
            aria-label="Publishing analyst"
            value={principal?.display_name ?? reviewer}
            readOnly
          />
        </label>
        {workspace.severity >= 3 ? (
          <div className="workspaceApprovalState">
            <span>Independent approval</span>
            <strong>{approvalRequest ? label(approvalRequest.status) : "Not requested"}</strong>
            <Link href="/console/approvals">Open approval inbox</Link>
          </div>
        ) : null}
        <div className="workspaceStats">
          <span>{workspace.claims.length} claims</span>
          <span>{workspace.sources.length} sources</span>
          <span>{workspace.versions.length} versions</span>
        </div>
      </section>
      {error ? <p className="consoleBanner consoleBanner--error" role="alert">{error}</p> : null}
      {notice ? <p className="consoleBanner" role="status">{notice}</p> : null}

      <div className="eventWorkspaceGrid">
        <aside className="claimPanel" aria-label="Claims panel">
          <div className="panelHeading">
            <div>
              <p className="eyebrow">Structured record</p>
              <h2>Claims</h2>
            </div>
            <button type="button" onClick={() => setShowClaimForm((current) => !current)}>
              {showClaimForm ? "Close" : "+ Claim"}
            </button>
          </div>
          {showClaimForm ? (
            <form className="claimCreateForm" onSubmit={submitClaim}>
              <label>
                <span>Claim text</span>
                <textarea value={claimText} onChange={(event) => setClaimText(event.target.value)} required />
              </label>
              <label>
                <span>Claimant</span>
                <input value={claimant} onChange={(event) => setClaimant(event.target.value)} />
              </label>
              <label>
                <span>Editorial state</span>
                <select value={claimState} onChange={(event) => setClaimState(event.target.value as ClaimState)}>
                  {claimStates.map((state) => <option key={state.value} value={state.value}>{state.label}</option>)}
                </select>
              </label>
              <button className="compactAction" type="submit" disabled={submitting}>Create reviewed claim</button>
            </form>
          ) : null}
          <div className="claimList">
            {workspace.claims.map((claim, index) => (
              <article key={claim.id} className={claim.id === selectedClaimId ? "claimCard claimCard--active" : "claimCard"}>
                <button type="button" onClick={() => setSelectedClaimId(claim.id)} aria-label={`Select claim ${index + 1}`}>
                  <span className={`claimState claimState--${claim.claim_state}`}>{label(claim.claim_state)}</span>
                  <strong>{claim.text}</strong>
                  <small>{claim.evidence.length} evidence · {claim.claimant ?? "No claimant"}</small>
                  {claim.sensitivity_flags.length ? (
                    <span
                      className={
                        claim.second_reviewed_by
                          ? "sensitiveClaim sensitiveClaim--reviewed"
                          : "sensitiveClaim"
                      }
                    >
                      {claim.second_reviewed_by
                        ? "Independent review bound"
                        : "Independent review required"}
                    </span>
                  ) : null}
                </button>
                <label>
                  <span>Review state</span>
                  <select
                    aria-label={`Review state for claim ${index + 1}`}
                    value={claim.claim_state}
                    disabled={submitting}
                    onChange={(event) => void reviseClaimState(claim, event.target.value as ClaimState)}
                  >
                    {claimStates.map((state) => <option key={state.value} value={state.value}>{state.label}</option>)}
                  </select>
                </label>
              </article>
            ))}
          </div>
        </aside>

        <section className="composerPanel" aria-label="Version composer">
          <div className="panelHeading composerHeading">
            <div>
              <p className="eyebrow">Version draft</p>
              <h2>Publishable state</h2>
            </div>
            <span>Every sentence requires claim IDs</span>
          </div>
          <label className="eventTitleField">
            <span>Factual title</span>
            <input
              value={title}
              onChange={(event) => {
                setTitle(event.target.value);
                invalidatePreview();
              }}
            />
          </label>
          <div className="sectionComposerList">
            {sections.map((section) => {
              const matching = draftSentences
                .map((sentence, index) => ({ sentence, index }))
                .filter(({ sentence }) => sentence.section === section.value);
              return (
                <section key={section.value} className={`sectionComposer sectionComposer--${section.value}`}>
                  <header>
                    <div><h3>{section.label}</h3><p>{section.guidance}</p></div>
                    <button type="button" onClick={() => addSentence(section.value)}>+ Sentence</button>
                  </header>
                  {matching.length ? matching.map(({ sentence, index }) => (
                    <div className="sentenceEditor" key={`${section.value}-${index}`}>
                      <textarea
                        aria-label={`${section.label} sentence ${index + 1}`}
                        value={sentence.text}
                        onChange={(event) => updateSentence(index, { text: event.target.value })}
                      />
                      <div className="claimMapping" aria-label={`Claim mapping for ${section.label} sentence`}>
                        {workspace.claims.map((claim, claimIndex) => {
                          const compatible = isCompatible(section.value, claim.claim_state);
                          const active = sentence.claim_ids.includes(claim.id);
                          return (
                            <button
                              key={claim.id}
                              type="button"
                              disabled={!compatible && !active}
                              aria-pressed={active}
                              onClick={() => toggleClaim(index, claim.id)}
                              title={claim.text}
                            >
                              C{claimIndex + 1}
                            </button>
                          );
                        })}
                        <button type="button" className="sentenceRemove" onClick={() => removeSentence(index)} aria-label="Remove sentence">×</button>
                      </div>
                    </div>
                  )) : <p className="composerEmpty">No material sentence in this section.</p>}
                </section>
              );
            })}
          </div>
        </section>

        <aside className="evidencePanel" aria-label="Evidence and timeline">
          <section>
            <div className="panelHeading">
              <div><p className="eyebrow">One-click linking</p><h2>Evidence</h2></div>
            </div>
            {selectedClaim ? <p className="selectedClaimSummary">For: {selectedClaim.text}</p> : <p className="emptyState">Select a claim.</p>}
            {selectedClaim?.sensitivity_flags.length ? (
              <div className="sensitiveReviewPanel">
                <div>
                  <span>Sensitive claim</span>
                  <strong>{selectedClaim.sensitivity_flags.map(label).join(" · ")}</strong>
                </div>
                {selectedClaim.second_reviewed_by ? (
                  <p>
                    Exact claim and evidence package reviewed by{" "}
                    <code>{selectedClaim.second_reviewed_by}</code>.
                  </p>
                ) : (
                  <>
                    <label>
                      <span>Independent review rationale</span>
                      <textarea
                        value={secondReviewReason}
                        onChange={(event) => setSecondReviewReason(event.target.value)}
                        minLength={10}
                        maxLength={2000}
                      />
                    </label>
                    <button
                      type="button"
                      disabled={
                        submitting ||
                        secondReviewReason.trim().length < 10 ||
                        !principal ||
                        !["senior_analyst", "administrator"].includes(principal.role) ||
                        !["mfa", "phishing_resistant"].includes(
                          principal.auth_assurance,
                        ) ||
                        selectedClaim.reviewed_by === `desk:${principal.id}`
                      }
                      onClick={() => void secondReviewSelectedClaim()}
                    >
                      Bind second review
                    </button>
                    <small>
                      A distinct senior or administrator with MFA must perform this review.
                    </small>
                  </>
                )}
              </div>
            ) : null}
            <div className="sourceList">
              {workspace.sources.map((source) => {
                const linked = Boolean(selectedClaim && source.linked_claim_ids.includes(selectedClaim.id));
                return (
                  <article key={source.id} className="sourceEvidenceCard">
                    <div><span className="tierBadge">{source.source_tier}</span><small>{source.rights_basis}</small></div>
                    <strong>{source.title ?? source.source_name}</strong>
                    <p>{source.text}</p>
                    <button type="button" disabled={!selectedClaim || linked || submitting} onClick={() => void linkSource(source)}>
                      {linked ? "Linked to claim" : "Link as primary evidence"}
                    </button>
                  </article>
                );
              })}
            </div>
          </section>
          <section className="timelinePanel">
            <p className="eyebrow">Immutable timeline</p>
            <ol>
              {workspace.versions.map((version) => (
                <li key={version.id}>
                  <strong>Version {version.version_no}</strong>
                  <span>{formatTime(version.published_at)}</span>
                  <code>{version.content_hash.slice(0, 12)}…</code>
                </li>
              ))}
              <li><strong>Event created</strong><span>{formatTime(workspace.created_at)}</span></li>
            </ol>
          </section>
        </aside>
      </div>

      <section className="publishGate" aria-label="Publication gate">
        <div className="publishGateIntro">
          <p className="eyebrow">Hard release boundary</p>
          <h2>Preview, verify, publish</h2>
          <p>The server replays every invariant and hashes this exact claim, evidence, and sentence set.</p>
          {workspace.severity >= 3 ? (
            <div className="publicationApprovalFlow">
              <label>
                <span>Approval request rationale</span>
                <textarea
                  value={approvalReason}
                  onChange={(event) => setApprovalReason(event.target.value)}
                  placeholder="Explain why this exact mapped release is ready for independent review."
                  minLength={10}
                  maxLength={2000}
                />
              </label>
              <div className="publishActions">
                <button
                  type="button"
                  onClick={() => void requestApproval()}
                  disabled={submitting || approvalReason.trim().length < 10}
                >
                  Submit exact draft
                </button>
                <button type="button" onClick={() => void refreshApproval()} disabled={submitting}>
                  Refresh approval
                </button>
                <button
                  type="button"
                  className="publishButton"
                  onClick={() => void publish()}
                  disabled={submitting || approvalRequest?.status !== "approved" || !approvalRequest.release_hash}
                >
                  Publish approved version
                </button>
              </div>
              {approvalRequest ? (
                <div className={`publicationApprovalReceipt publicationApprovalReceipt--${approvalRequest.status}`}>
                  <strong>{label(approvalRequest.status)}</strong>
                  <span>Requested {formatTime(approvalRequest.requested_at)}</span>
                  {approvalRequest.release_hash ? <code>{approvalRequest.release_hash}</code> : null}
                  {approvalRequest.release_expires_at ? (
                    <small>Release approval expires {formatTime(approvalRequest.release_expires_at)}</small>
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : (
            <div className="publishActions">
              <button type="button" onClick={() => void requestPreview()} disabled={submitting}>Generate diff preview</button>
              <button type="button" className="publishButton" onClick={() => void publish()} disabled={submitting || !preview?.ready}>Publish immutable version</button>
            </div>
          )}
        </div>
        <div className="gateChecks">
          {(preview?.checks ?? []).map((check) => (
            <article key={check.key} className={check.passed ? "gateCheck gateCheck--pass" : "gateCheck gateCheck--fail"}>
              <span>{check.passed ? "✓" : "!"}</span>
              <div><strong>{check.label}</strong><p>{check.detail}</p></div>
            </article>
          ))}
          {!preview && workspace.severity < 3 ? <p className="emptyState">Generate a preview to run the publication gate.</p> : null}
          {!preview && workspace.severity >= 3 ? <p className="emptyState">The approval request runs the complete server gate before it enters the inbox.</p> : null}
        </div>
        <div className="diffPreview">
          <p className="eyebrow">Version diff</p>
          {preview ? Object.entries(preview.diff).filter(([, field]) => field.changed).map(([field, change]) => (
            <article key={field}>
              <strong>{label(field)}</strong>
              <div><span>Before</span><p>{change.before || "—"}</p></div>
              <div><span>After</span><p>{change.after || "—"}</p></div>
            </article>
          )) : <p className="emptyState">No server diff yet.</p>}
          {preview && !Object.values(preview.diff).some((field) => field.changed) ? <p className="emptyState">No textual change from the latest version.</p> : null}
        </div>
      </section>
    </main>
  );
}
