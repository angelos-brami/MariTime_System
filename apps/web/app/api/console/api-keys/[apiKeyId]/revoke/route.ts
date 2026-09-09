import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";
import { readJsonRequest } from "@/lib/json-request";

export async function POST(
  request: Request,
  { params }: { params: Promise<{ apiKeyId: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const parsed = await readJsonRequest<Record<string, unknown>>(request, 32_768, "API key");
  if (!parsed.ok) return parsed.response;
  const { apiKeyId } = await params;
  return forwardDeskRequest(`/api/v1/api-keys/${encodeURIComponent(apiKeyId)}/revoke`, {
    method: "POST",
    body: parsed.body,
  });
}
