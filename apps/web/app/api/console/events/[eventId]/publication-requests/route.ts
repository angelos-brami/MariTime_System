import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";
import { readJsonRequest } from "@/lib/json-request";

export async function POST(
  request: Request,
  context: { params: Promise<{ eventId: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const parsed = await readJsonRequest<Record<string, unknown>>(
    request,
    131_072,
    "Publication approval request",
  );
  if (!parsed.ok) return parsed.response;
  const { eventId } = await context.params;
  return forwardDeskRequest(
    `/api/v1/events/${encodeURIComponent(eventId)}/publication-requests`,
    { method: "POST", body: parsed.body },
  );
}
