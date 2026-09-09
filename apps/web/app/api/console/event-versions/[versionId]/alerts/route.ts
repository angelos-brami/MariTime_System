import type { DeliveryChannel } from "@/app/console/types";
import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";
import { readJsonRequest } from "@/lib/json-request";

export async function POST(
  request: Request,
  context: { params: Promise<{ versionId: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const parsed = await readJsonRequest<{
    channels: DeliveryChannel[];
    preview_hash?: string;
  }>(request, 32_768, "Alert release");
  if (!parsed.ok) return parsed.response;
  const { body, payload } = parsed;
  const { versionId } = await context.params;
  if (process.env.EASTMED_CONSOLE_DEMO === "true") {
    if (!payload.preview_hash || !/^[0-9a-f]{64}$/.test(payload.preview_hash)) {
      return Response.json({ detail: "A valid alert preview is required" }, { status: 422 });
    }
    const deliveryCount = payload.channels.includes("telegram") ? 5 : 3;
    return Response.json(
      {
        alert_id: crypto.randomUUID(),
        event_version_id: versionId,
        delivery_count: deliveryCount,
        queued_at: new Date().toISOString(),
        idempotent_replay: false,
      },
      { status: 201 },
    );
  }
  return forwardDeskRequest(
    `/api/v1/event-versions/${encodeURIComponent(versionId)}/alerts`,
    { method: "POST", body },
  );
}
