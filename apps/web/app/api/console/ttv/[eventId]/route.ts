import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";
import { readJsonRequest } from "@/lib/json-request";

export async function PATCH(
  request: Request,
  { params }: { params: Promise<{ eventId: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const parsed = await readJsonRequest<Record<string, unknown>>(request, 32_768, "TTV");
  if (!parsed.ok) return parsed.response;
  const { eventId } = await params;
  return forwardDeskRequest(`/api/v1/ttv/${encodeURIComponent(eventId)}`, {
    method: "PATCH",
    body: parsed.body,
  });
}
