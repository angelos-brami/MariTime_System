import { authenticateConsoleRequest, consoleAuthFailure } from "../../../../../../../lib/console-auth";
import { forwardDeskRequest } from "../../../../../../../lib/console-api";
import { readJsonRequest } from "../../../../../../../lib/json-request";

export async function POST(
  request: Request,
  context: { params: Promise<{ reviewId: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const parsed = await readJsonRequest<Record<string, unknown>>(
    request,
    32_000,
    "Extraction QA payload",
  );
  if (!parsed.ok) return parsed.response;
  const { body, payload } = parsed;
  const { reviewId } = await context.params;
  if (process.env.EASTMED_CONSOLE_DEMO === "true") {
    return Response.json({
      id: crypto.randomUUID(),
      review_id: reviewId,
      evaluator: payload.evaluator,
      error_found: payload.error_found,
      error_codes: payload.error_codes ?? [],
      note: payload.note ?? null,
      evaluated_at: new Date().toISOString(),
    });
  }
  return forwardDeskRequest(
    `/api/v1/claim-extraction/reviews/${encodeURIComponent(reviewId)}/qa`,
    { method: "POST", body },
  );
}
