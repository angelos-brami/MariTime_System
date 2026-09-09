import { forwardPortalRequest } from "@/lib/portal-api";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  const query = new URL(request.url).search;
  return forwardPortalRequest(`/api/v1/portal/ais${query}`);
}
