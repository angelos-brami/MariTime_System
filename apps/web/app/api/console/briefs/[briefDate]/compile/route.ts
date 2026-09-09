import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";
import { demoDailyBrief } from "@/lib/portal-demo";
import { readJsonRequest } from "@/lib/json-request";

export async function POST(
  request: Request,
  context: { params: Promise<{ briefDate: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const parsed = await readJsonRequest<{ compiled_by: string }>(
    request,
    16_384,
    "Brief compilation",
  );
  if (!parsed.ok) return parsed.response;
  const { briefDate } = await context.params;
  if (process.env.EASTMED_CONSOLE_DEMO === "true") {
    return Response.json({
      ...demoDailyBrief,
      brief_date: briefDate,
      title: `East Med Corridor Watch — ${briefDate}`,
      status: "draft",
      compiled_at: new Date().toISOString(),
      compiled_by: parsed.payload.compiled_by,
      finalized_at: null,
      finalized_by: null,
    });
  }
  return forwardDeskRequest(`/api/v1/briefs/${encodeURIComponent(briefDate)}/compile`, {
    method: "POST",
    body: parsed.body,
  });
}
