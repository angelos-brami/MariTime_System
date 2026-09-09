import { forwardPortalRequest } from "@/lib/portal-api";

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ reportId: string }> },
) {
  const { reportId } = await params;
  return forwardPortalRequest(
    `/api/v1/portal/reports/state-of-knowledge/${encodeURIComponent(reportId)}.pdf`,
  );
}
