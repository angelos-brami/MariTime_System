import { forwardPortalRequest } from "@/lib/portal-api";
import { readJsonRequest } from "@/lib/json-request";

export async function POST(request: Request) {
  const parsed = await readJsonRequest<Record<string, unknown>>(request, 32_768, "Report");
  if (!parsed.ok) return parsed.response;
  return forwardPortalRequest("/api/v1/portal/reports/state-of-knowledge", {
    method: "POST",
    body: parsed.body,
  });
}
