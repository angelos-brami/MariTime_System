import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";
import { readJsonRequest } from "@/lib/json-request";

export async function POST(
  request: Request,
  context: { params: Promise<{ requestId: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const parsed = await readJsonRequest<Record<string, unknown>>(
    request,
    16_384,
    "Approval decision",
  );
  if (!parsed.ok) return parsed.response;
  const { requestId } = await context.params;
  return forwardDeskRequest(
    `/api/v1/approval-requests/${encodeURIComponent(requestId)}/decision`,
    { method: "POST", body: parsed.body },
  );
}
