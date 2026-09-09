import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";
import { readJsonRequest } from "@/lib/json-request";

export async function POST(
  request: Request,
  context: { params: Promise<{ eventId: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const parsed = await readJsonRequest<{
    text: string;
    claimant?: string;
    claim_state: string;
    reviewer: string;
    sensitivity_flags?: string[];
  }>(request, 32_768, "Claim payload");
  if (!parsed.ok) return parsed.response;
  const { body, payload } = parsed;
  const { eventId } = await context.params;
  if (process.env.EASTMED_CONSOLE_DEMO === "true") {
    const now = new Date().toISOString();
    return Response.json(
      {
        id: crypto.randomUUID(),
        text: payload.text,
        claimant: payload.claimant ?? null,
        claim_state: payload.claim_state,
        occurred_at: null,
        proposed_by: payload.reviewer,
        reviewed_by: payload.reviewer,
        second_reviewed_by: null,
        reviewed_at: now,
        sensitivity_flags: payload.sensitivity_flags ?? [],
        first_seen_at: now,
        evidence: [],
      },
      { status: 201 },
    );
  }
  return forwardDeskRequest(`/api/v1/events/${encodeURIComponent(eventId)}/claims`, {
    method: "POST",
    body,
  });
}
