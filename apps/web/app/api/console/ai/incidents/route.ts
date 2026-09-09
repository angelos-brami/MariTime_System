import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";
import { readJsonRequest } from "@/lib/json-request";

export async function GET(request: Request) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const includeResolved = new URL(request.url).searchParams.get("include_resolved");
  const suffix = includeResolved === null ? "" : `?include_resolved=${includeResolved}`;
  return forwardDeskRequest(`/api/v1/ai/governance/incidents${suffix}`);
}

export async function POST(request: Request) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const parsed = await readJsonRequest<Record<string, unknown>>(request, 65_536, "AI incident");
  if (!parsed.ok) return parsed.response;
  return forwardDeskRequest("/api/v1/ai/governance/incidents", {
    method: "POST",
    body: parsed.body,
  });
}
