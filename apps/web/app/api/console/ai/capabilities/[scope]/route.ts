import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";
import { readJsonRequest } from "@/lib/json-request";

export async function POST(
  request: Request,
  context: { params: Promise<{ scope: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const parsed = await readJsonRequest<Record<string, unknown>>(
    request,
    32_768,
    "AI capability control",
  );
  if (!parsed.ok) return parsed.response;
  const { scope } = await context.params;
  return forwardDeskRequest(
    `/api/v1/ai/governance/capabilities/${encodeURIComponent(scope)}`,
    { method: "POST", body: parsed.body },
  );
}
