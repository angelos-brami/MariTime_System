import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";
import { readJsonRequest } from "@/lib/json-request";

export async function POST(
  request: Request,
  context: { params: Promise<{ eventId: string; claimId: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const parsed = await readJsonRequest<{
    source_record_id: string;
    directness: "primary" | "secondary";
    excerpt?: string;
  }>(request, 32_768, "Evidence payload");
  if (!parsed.ok) return parsed.response;
  const { body, payload } = parsed;
  const { eventId, claimId } = await context.params;
  if (process.env.EASTMED_CONSOLE_DEMO === "true") {
    return Response.json(
      {
        id: crypto.randomUUID(),
        claim_id: claimId,
        source_record_id: payload.source_record_id,
        lineage_root_id: crypto.randomUUID(),
        directness: payload.directness,
        excerpt: payload.excerpt ?? null,
      },
      { status: 201 },
    );
  }
  return forwardDeskRequest(
    `/api/v1/events/${encodeURIComponent(eventId)}/claims/${encodeURIComponent(claimId)}/evidence`,
    { method: "POST", body },
  );
}
