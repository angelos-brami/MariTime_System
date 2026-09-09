import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";
import { demoWatchProfiles } from "@/lib/console-demo";
import { readJsonRequest } from "@/lib/json-request";

export async function PATCH(
  request: Request,
  context: { params: Promise<{ profileId: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const parsed = await readJsonRequest<Record<string, unknown>>(
    request,
    131_072,
    "Watch profile",
  );
  if (!parsed.ok) return parsed.response;
  const { body, payload } = parsed;
  const { profileId } = await context.params;
  if (process.env.EASTMED_CONSOLE_DEMO === "true") {
    const profile = demoWatchProfiles.find((candidate) => candidate.id === profileId);
    if (!profile) return Response.json({ detail: "Watch profile not found" }, { status: 404 });
    return Response.json({ ...profile, ...payload, updated_at: new Date().toISOString() });
  }
  return forwardDeskRequest(`/api/v1/watch-profiles/${encodeURIComponent(profileId)}`, {
    method: "PATCH",
    body,
  });
}

export async function DELETE(
  request: Request,
  context: { params: Promise<{ profileId: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const { profileId } = await context.params;
  const reviewer = new URL(request.url).searchParams.get("reviewer") ?? "";
  if (process.env.EASTMED_CONSOLE_DEMO === "true") {
    const profile = demoWatchProfiles.find((candidate) => candidate.id === profileId);
    if (!profile) return Response.json({ detail: "Watch profile not found" }, { status: 404 });
    return Response.json({ ...profile, active: false, updated_at: new Date().toISOString() });
  }
  return forwardDeskRequest(
    `/api/v1/watch-profiles/${encodeURIComponent(profileId)}?reviewer=${encodeURIComponent(reviewer)}`,
    { method: "DELETE" },
  );
}
