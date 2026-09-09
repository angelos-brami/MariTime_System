import { authenticateConsoleRequest, consoleAuthFailure } from "../../../../../lib/console-auth";
import { forwardDeskRequest } from "../../../../../lib/console-api";
import { demoClaimExtractionReport } from "../../../../../lib/console-demo";

export async function GET(request: Request) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const query = new URL(request.url).searchParams.toString();
  if (process.env.EASTMED_CONSOLE_DEMO === "true") {
    return Response.json(demoClaimExtractionReport);
  }
  return forwardDeskRequest(
    `/api/v1/claim-extraction/weekly-report${query ? `?${query}` : ""}`,
  );
}
