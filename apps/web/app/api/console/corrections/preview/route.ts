import { authenticateConsoleRequest, consoleAuthFailure } from "@/lib/console-auth";
import { forwardDeskRequest } from "@/lib/console-api";
import { readJsonRequest } from "@/lib/json-request";

export async function POST(request: Request) {
  if (!authenticateConsoleRequest(request)) return consoleAuthFailure(true);
  const parsed = await readJsonRequest<Record<string, unknown>>(request, 131_072, "Correction");
  if (!parsed.ok) return parsed.response;
  return forwardDeskRequest("/api/v1/corrections/preview", {
    method: "POST",
    body: parsed.body,
  });
}
