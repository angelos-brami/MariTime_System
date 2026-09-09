import { authenticateConsoleRequest, consoleAuthFailure } from "../../../../../../lib/console-auth";
import { forwardDeskRequest } from "../../../../../../lib/console-api";

export async function POST(
  request: Request,
  context: { params: Promise<{ language: string }> },
) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const { language } = await context.params;
  if (process.env.EASTMED_CONSOLE_DEMO === "true") {
    return Response.json({
      evaluation: {
        id: crypto.randomUUID(),
        component: "claim_extraction",
        component_version: "claim_extraction_v2",
        metric_name: "time_reduction_ratio",
        metric_value: 0.56,
        sample_size: 18,
        evaluated_at: new Date().toISOString(),
        graduated: false,
      },
      language,
      prompt_version: "claim_extraction_v2",
      model_version: "claude-sonnet-shadow",
      reviewed: 18,
      qa_reviewed: 12,
      shadow_days: 14,
      time_reduction_percent: 56,
      error_rate: 0,
      baseline_error_rate: null,
      graduated: false,
    });
  }
  return forwardDeskRequest(
    `/api/v1/claim-extraction/evaluations/${encodeURIComponent(language)}`,
    { method: "POST" },
  );
}
