import { forwardPortalRequest } from "@/lib/portal-api";

export async function GET(
  _request: Request,
  context: { params: Promise<{ slug: string }> },
) {
  const { slug } = await context.params;
  return forwardPortalRequest(`/api/v1/portal/events/${encodeURIComponent(slug)}`);
}
