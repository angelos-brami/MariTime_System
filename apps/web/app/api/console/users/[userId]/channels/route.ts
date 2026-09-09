import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";
import { readJsonRequest } from "@/lib/json-request";

export async function PATCH(
  request: Request,
  { params }: { params: Promise<{ userId: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const parsed = await readJsonRequest<Record<string, unknown>>(request, 32_768, "Channels");
  if (!parsed.ok) return parsed.response;
  const { userId } = await params;
  return forwardDeskRequest(`/api/v1/users/${encodeURIComponent(userId)}/channels`, {
    method: "PATCH",
    body: parsed.body,
  });
}
