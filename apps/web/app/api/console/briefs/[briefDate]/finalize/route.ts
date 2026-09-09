import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";
import { demoDailyBrief } from "@/lib/portal-demo";
import { readJsonRequest } from "@/lib/json-request";

export async function POST(
  request: Request,
  context: { params: Promise<{ briefDate: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const parsed = await readJsonRequest<{
    finalized_by: string;
    title: string;
    introduction: string;
    forward_watch: string;
    item_version_ids: string[];
  }>(request, 65_536, "Brief finalization");
  if (!parsed.ok) return parsed.response;
  const { briefDate: briefId } = await context.params;
  if (process.env.EASTMED_CONSOLE_DEMO === "true") {
    return Response.json({
      ...demoDailyBrief,
      id: briefId,
      ...parsed.payload,
      source_version_ids: parsed.payload.item_version_ids,
      status: "finalized",
      finalized_at: new Date().toISOString(),
      finalized_by: parsed.payload.finalized_by,
    });
  }
  return forwardDeskRequest(`/api/v1/briefs/${encodeURIComponent(briefId)}/finalize`, {
    method: "POST",
    body: parsed.body,
  });
}
