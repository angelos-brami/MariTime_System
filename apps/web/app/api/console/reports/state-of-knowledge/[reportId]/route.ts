import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";

export async function GET(
  request: Request,
  { params }: { params: Promise<{ reportId: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const { reportId } = await params;
  return forwardDeskRequest(
    `/api/v1/reports/state-of-knowledge/${encodeURIComponent(reportId)}.pdf`,
  );
}
