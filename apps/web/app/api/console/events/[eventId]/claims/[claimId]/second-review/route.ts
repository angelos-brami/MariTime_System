import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";
import { readJsonRequest } from "@/lib/json-request";

export async function POST(
  request: Request,
  context: { params: Promise<{ eventId: string; claimId: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const parsed = await readJsonRequest<{ reason: string }>(
    request,
    8_192,
    "Claim second review",
  );
  if (!parsed.ok) return parsed.response;
  const { eventId, claimId } = await context.params;
  return forwardDeskRequest(
    `/api/v1/events/${encodeURIComponent(eventId)}/claims/${encodeURIComponent(claimId)}/second-review`,
    { method: "POST", body: parsed.body },
  );
}
