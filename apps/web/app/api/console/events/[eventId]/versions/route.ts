import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";
import { demoWorkspaceFor } from "@/lib/console-demo";
import { readJsonRequest } from "@/lib/json-request";

export async function POST(
  request: Request,
  context: { params: Promise<{ eventId: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const parsed = await readJsonRequest<{ preview_hash?: string }>(
    request,
    131_072,
    "Version payload",
  );
  if (!parsed.ok) return parsed.response;
  const { body, payload } = parsed;
  const { eventId } = await context.params;
  if (process.env.EASTMED_CONSOLE_DEMO === "true") {
    const workspace = demoWorkspaceFor(eventId);
    if (!workspace) return Response.json({ detail: "Event not found" }, { status: 404 });
    if (!payload.preview_hash?.match(/^[0-9a-f]{64}$/)) {
      return Response.json({ detail: "Preview is stale; regenerate the version diff before publishing" }, { status: 422 });
    }
    return Response.json(
      {
        id: crypto.randomUUID(),
        event_id: eventId,
        version_no: (workspace.versions[0]?.version_no ?? 0) + 1,
        content_hash: payload.preview_hash,
        published_at: new Date().toISOString(),
      },
      { status: 201 },
    );
  }
  return forwardDeskRequest(`/api/v1/events/${encodeURIComponent(eventId)}/versions`, {
    method: "POST",
    body,
  });
}
