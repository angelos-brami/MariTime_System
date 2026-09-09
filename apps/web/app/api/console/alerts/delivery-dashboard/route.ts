import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";
import { demoDeliveryDashboard } from "@/lib/console-demo";

export async function GET(request: Request) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  if (process.env.EASTMED_CONSOLE_DEMO === "true") {
    return Response.json(demoDeliveryDashboard);
  }
  return forwardDeskRequest("/api/v1/alerts/delivery-dashboard");
}
