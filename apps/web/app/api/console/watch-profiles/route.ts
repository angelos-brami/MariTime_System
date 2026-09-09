import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";
import { demoAccounts, demoWatchProfiles } from "@/lib/console-demo";
import { readJsonRequest } from "@/lib/json-request";

export async function GET(request: Request) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  if (process.env.EASTMED_CONSOLE_DEMO === "true") return Response.json(demoWatchProfiles);
  const query = new URL(request.url).search;
  return forwardDeskRequest(`/api/v1/watch-profiles${query}`);
}

export async function POST(request: Request) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const parsed = await readJsonRequest<{
    account_id: string;
    name: string;
    corridors: string[];
    event_types: string[];
    min_severity: number;
    ports: string[];
    active: boolean;
    configured_by: string | null;
    configured_with_customer_at: string | null;
  }>(request, 131_072, "Watch profile");
  if (!parsed.ok) return parsed.response;
  const { body, payload } = parsed;
  if (process.env.EASTMED_CONSOLE_DEMO === "true") {
    const account = demoAccounts.find((candidate) => candidate.id === payload.account_id);
    if (!account) return Response.json({ detail: "Account not found" }, { status: 404 });
    const now = new Date().toISOString();
    return Response.json(
      {
        id: crypto.randomUUID(),
        ...payload,
        account_company: account.company,
        account_tier: account.tier,
        custom_geojson: null,
        created_at: now,
        updated_at: now,
      },
      { status: 201 },
    );
  }
  return forwardDeskRequest("/api/v1/watch-profiles", { method: "POST", body });
}
