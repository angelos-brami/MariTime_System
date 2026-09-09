import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";
import { demoWorkspaceFor } from "@/lib/console-demo";

export async function GET(
  request: Request,
  context: { params: Promise<{ eventId: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const { eventId } = await context.params;
  if (process.env.EASTMED_CONSOLE_DEMO === "true") {
    const workspace = demoWorkspaceFor(eventId);
    return workspace
      ? Response.json(workspace)
      : Response.json({ detail: "Event not found" }, { status: 404 });
  }
  return forwardDeskRequest(`/api/v1/events/${encodeURIComponent(eventId)}/workspace`);
}
