import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";
import { readJsonRequest } from "@/lib/json-request";

export async function GET(request: Request) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const query = new URL(request.url).search;
  return forwardDeskRequest(`/api/v1/api-keys${query}`);
}

export async function POST(request: Request) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const parsed = await readJsonRequest<Record<string, unknown>>(request, 32_768, "API key");
  if (!parsed.ok) return parsed.response;
  return forwardDeskRequest("/api/v1/api-keys", { method: "POST", body: parsed.body });
}
