import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";
import { demoDailyBrief } from "@/lib/portal-demo";

export async function GET(
  request: Request,
  context: { params: Promise<{ briefDate: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const { briefDate } = await context.params;
  if (process.env.EASTMED_CONSOLE_DEMO === "true") {
    return Response.json({
      ...demoDailyBrief,
      brief_date: briefDate,
      title: `East Med Corridor Watch — ${briefDate}`,
      status: "draft",
      finalized_at: null,
      finalized_by: null,
    });
  }
  return forwardDeskRequest(`/api/v1/briefs/${encodeURIComponent(briefDate)}`);
}
