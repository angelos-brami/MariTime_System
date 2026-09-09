import { forwardPortalRequest } from "@/lib/portal-api";

export async function GET() {
  return forwardPortalRequest("/api/v1/portal/board");
}
