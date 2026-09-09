import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";
import { demoWorkspaceFor } from "@/lib/console-demo";
import { readJsonRequest } from "@/lib/json-request";

import type {
  ClaimState,
  PublicationGateCheck,
  PublicationSection,
  PublicationSentence,
} from "@/app/console/types";

type Draft = {
  title: string;
  sentences: PublicationSentence[];
  evidence_ids: string[];
  published_by: string;
  signed_off_by: string | null;
  policy_version: string;
  model_versions: Record<string, string>;
};

const confirmedStates = new Set<ClaimState>([
  "confirmed",
  "corroborated_2_independent",
  "single_source_official",
]);

async function digest(value: string): Promise<string> {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(bytes)].map((part) => part.toString(16).padStart(2, "0")).join("");
}

export async function POST(
  request: Request,
  context: { params: Promise<{ eventId: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const parsed = await readJsonRequest<Draft>(request, 131_072, "Version preview");
  if (!parsed.ok) return parsed.response;
  const { body, payload: draft } = parsed;
  const { eventId } = await context.params;
  if (process.env.EASTMED_CONSOLE_DEMO !== "true") {
    return forwardDeskRequest(
      `/api/v1/events/${encodeURIComponent(eventId)}/versions/preview`,
      { method: "POST", body },
    );
  }

  const workspace = demoWorkspaceFor(eventId);
  if (!workspace) return Response.json({ detail: "Event not found" }, { status: 404 });
  const claimById = new Map(workspace.claims.map((claim) => [claim.id, claim]));
  const selectedEvidence = new Set(draft.evidence_ids);
  const mapped = draft.sentences.every(
    (sentence) => sentence.claim_ids.length > 0 && sentence.claim_ids.every((id) => claimById.has(id)),
  );
  const allowed: Record<PublicationSection, Set<ClaimState> | null> = {
    confirmed: confirmedStates,
    reported: new Set(["reported", "disputed"]),
    unknown: new Set(["unverified", "disputed"]),
    changed: null,
  };
  const labelsMatch = draft.sentences.every((sentence) =>
    sentence.claim_ids.every((id) => {
      const claim = claimById.get(id);
      return Boolean(claim && (!allowed[sentence.section] || allowed[sentence.section]?.has(claim.claim_state)));
    }),
  );
  const confirmedHaveEvidence = draft.sentences.every((sentence) =>
    sentence.claim_ids.every((id) => {
      const claim = claimById.get(id);
      if (!claim || !confirmedStates.has(claim.claim_state)) return true;
      return claim.evidence.some((evidence) => selectedEvidence.has(evidence.id));
    }),
  );
  const humanReview = draft.sentences.every((sentence) =>
    sentence.claim_ids.every((id) => {
      const claim = claimById.get(id);
      return Boolean(
        claim &&
          (!claim.proposed_by.startsWith("model:") || claim.reviewed_by) &&
          (!claim.sensitivity_flags.length || claim.second_reviewed_by),
      );
    }),
  );
  const severitySigned =
    !draft.published_by.startsWith("model:") &&
    (workspace.severity < 3 || Boolean(draft.signed_off_by && !draft.signed_off_by.startsWith("model:")));
  const checks: PublicationGateCheck[] = [
    {
      key: "sentence-mapping",
      label: "Every material sentence maps to a claim",
      passed: mapped,
      detail: mapped ? `${draft.sentences.length} material sentence(s) carry explicit claim IDs.` : "A sentence has no valid claim mapping.",
    },
    {
      key: "public-labels",
      label: "Claim states match public labels",
      passed: labelsMatch,
      detail: labelsMatch ? "Confirmed, Reported, and Unknown labels match claim states." : "A claim is placed under an incompatible public label.",
    },
    {
      key: "evidence",
      label: "Confirmed claims have selected evidence",
      passed: confirmedHaveEvidence,
      detail: confirmedHaveEvidence ? "Confirmed claims carry evidence in this version." : "Select evidence for every confirmed claim.",
    },
    {
      key: "human-review",
      label: "Model and sensitive claims have human review",
      passed: humanReview,
      detail: humanReview ? "Human review policy is satisfied." : "A model or sensitive claim still needs review.",
    },
    {
      key: "severity-signoff",
      label: "Severity release has human sign-off",
      passed: severitySigned,
      detail: severitySigned ? "Named human sign-off is present." : "Severity 3-4 requires a named human sign-off.",
    },
  ];
  const latest = workspace.versions[0] ?? null;
  const sectionText = (section: PublicationSection, sentences: PublicationSentence[]) =>
    sentences.filter((sentence) => sentence.section === section).map((sentence) => sentence.text).join("\n");
  const fields: ("title" | PublicationSection)[] = [
    "title",
    "confirmed",
    "reported",
    "unknown",
    "changed",
  ];
  const diff = Object.fromEntries(
    fields.map((field) => {
      const before = field === "title" ? latest?.title ?? "" : sectionText(field, latest?.sentences ?? []);
      const after = field === "title" ? draft.title : sectionText(field, draft.sentences);
      return [field, { before, after, changed: before !== after }];
    }),
  );
  const previewHash = await digest(
    JSON.stringify({ eventId, previous: latest?.content_hash ?? null, draft }),
  );
  return Response.json({
    previous_version_no: latest?.version_no ?? null,
    next_version_no: (latest?.version_no ?? 0) + 1,
    preview_hash: previewHash,
    ready: checks.every((check) => check.passed),
    checks,
    diff,
  });
}
