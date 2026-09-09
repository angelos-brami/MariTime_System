import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";
import { readJsonRequest } from "@/lib/json-request";

export async function PATCH(
  request: Request,
  context: { params: Promise<{ eventId: string; claimId: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const parsed = await readJsonRequest<Record<string, unknown>>(
    request,
    32_768,
    "Claim payload",
  );
  if (!parsed.ok) return parsed.response;
  const { body, payload } = parsed;
  const { eventId, claimId } = await context.params;
  if (process.env.EASTMED_CONSOLE_DEMO === "true") {
    return Response.json({ id: claimId, ...payload, evidence: [] });
  }
  return forwardDeskRequest(
    `/api/v1/events/${encodeURIComponent(eventId)}/claims/${encodeURIComponent(claimId)}`,
    { method: "PATCH", body },
  );
}
