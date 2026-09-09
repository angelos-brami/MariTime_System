import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";

export async function GET(request: Request) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const url = new URL(request.url);
  const query = new URLSearchParams();
  const status = url.searchParams.get("status");
  const limit = url.searchParams.get("limit");
  if (status) query.set("status", status);
  if (limit) query.set("limit", limit);
  const suffix = query.size ? `?${query}` : "";
  return forwardDeskRequest(`/api/v1/approval-requests${suffix}`);
}
