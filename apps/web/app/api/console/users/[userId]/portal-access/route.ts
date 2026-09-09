import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";
import { readJsonRequest } from "@/lib/json-request";

export async function PATCH(
  request: Request,
  context: { params: Promise<{ userId: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const parsed = await readJsonRequest<{
    updated_by: string;
    auth_subject: string | null;
    portal_enabled: boolean;
  }>(request, 16_384, "Portal access update");
  if (!parsed.ok) return parsed.response;
  const { userId } = await context.params;
  if (process.env.EASTMED_CONSOLE_DEMO === "true") {
    return Response.json({
      id: userId,
      email: "ops@example.test",
      phone: null,
      channels: { email: true },
      role: "subscriber",
      auth_subject: parsed.payload.auth_subject,
      active: true,
      portal_enabled: parsed.payload.portal_enabled,
    });
  }
  return forwardDeskRequest(
    `/api/v1/users/${encodeURIComponent(userId)}/portal-access`,
    { method: "PATCH", body: parsed.body },
  );
}
