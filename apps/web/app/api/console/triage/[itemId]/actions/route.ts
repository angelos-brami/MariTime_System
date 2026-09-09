import { authenticateConsoleRequest, consoleAuthFailure } from "../../../../../../lib/console-auth";
import { forwardDeskRequest } from "../../../../../../lib/console-api";
import { readJsonRequest } from "../../../../../../lib/json-request";

export async function POST(
  request: Request,
  context: { params: Promise<{ itemId: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const parsed = await readJsonRequest<{ action?: string }>(request, 32_768, "Action payload");
  if (!parsed.ok) return parsed.response;
  const { body, payload } = parsed;
  const { itemId } = await context.params;
  if (process.env.EASTMED_CONSOLE_DEMO === "true") {
    const action = payload.action;
    return Response.json({
      triage_item_id: itemId,
      decision_id: crypto.randomUUID(),
      status:
        action === "dismiss"
          ? "dismissed"
          : action === "new_event"
            ? "new_event"
            : "attached",
      event_id: null,
      reviewed_at: new Date().toISOString(),
    });
  }
  return forwardDeskRequest(`/api/v1/triage/${encodeURIComponent(itemId)}/actions`, {
    method: "POST",
    body,
  });
}
