import { forwardPortalRequest } from "@/lib/portal-api";

export async function GET(request: Request) {
  const incoming = new URL(request.url);
  const query = new URLSearchParams();
  for (const key of ["q", "corridor", "event_type", "min_severity", "limit", "offset"]) {
    const value = incoming.searchParams.get(key);
    if (value) query.set(key, value);
  }
  const suffix = query.size ? `?${query.toString()}` : "";
  return forwardPortalRequest(`/api/v1/portal/archive${suffix}`);
}
