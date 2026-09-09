import { authenticateConsoleRequest, consoleAuthFailure } from "../../../../../../../lib/console-auth";
import { forwardDeskRequest } from "../../../../../../../lib/console-api";
import { readJsonRequest } from "../../../../../../../lib/json-request";

export async function POST(
  request: Request,
  context: { params: Promise<{ proposalId: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const parsed = await readJsonRequest<Record<string, unknown>>(
    request,
    64_000,
    "Claim review payload",
  );
  if (!parsed.ok) return parsed.response;
  const { body, payload } = parsed;
  const { proposalId } = await context.params;
  if (process.env.EASTMED_CONSOLE_DEMO === "true") {
    return Response.json({
      id: crypto.randomUUID(),
      proposal_id: proposalId,
      decision: payload.decision,
      event_id: payload.event_id ?? null,
      reviewer: payload.reviewer,
      claim_state: payload.claim_state ?? null,
      final_claim_json: payload.edited_claim ?? null,
      reason_codes: payload.reason_codes ?? [],
      note: payload.note ?? null,
      baseline_seconds: payload.baseline_seconds,
      review_seconds: payload.review_seconds,
      mode: "shadow",
      resulting_claim_id: null,
      reviewed_at: new Date().toISOString(),
    });
  }
  return forwardDeskRequest(
    `/api/v1/claim-extraction/proposals/${encodeURIComponent(proposalId)}/review`,
    { method: "POST", body },
  );
}
