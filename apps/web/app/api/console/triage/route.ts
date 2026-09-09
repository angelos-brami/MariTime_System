import { authenticateConsoleRequest, consoleAuthFailure } from "../../../../lib/console-auth";
import { forwardDeskRequest } from "../../../../lib/console-api";
import { demoTriageItems } from "../../../../lib/console-demo";

export async function GET(request: Request) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  if (process.env.EASTMED_CONSOLE_DEMO === "true") {
    const params = new URL(request.url).searchParams;
    const tier = params.get("tier");
    const corridor = params.get("corridor");
    const eventType = params.get("event_type");
    return Response.json(
      demoTriageItems.filter(
        (item) =>
          (!tier || item.source_tier === tier) &&
          (!corridor || item.detected_corridors.includes(corridor as never)) &&
          (!eventType || item.suggested_event_types.includes(eventType as never)),
      ),
    );
  }
  const query = new URL(request.url).searchParams.toString();
  return forwardDeskRequest(`/api/v1/triage${query ? `?${query}` : ""}`);
}
