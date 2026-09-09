import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";
import { readJsonRequest } from "@/lib/json-request";

export async function POST(
  request: Request,
  context: { params: Promise<{ incidentId: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const parsed = await readJsonRequest<Record<string, unknown>>(
    request,
    65_536,
    "AI incident event",
  );
  if (!parsed.ok) return parsed.response;
  const { incidentId } = await context.params;
  return forwardDeskRequest(
    `/api/v1/ai/governance/incidents/${encodeURIComponent(incidentId)}/events`,
    { method: "POST", body: parsed.body },
  );
}
