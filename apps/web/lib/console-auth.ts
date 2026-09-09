import { timingSafeEqual } from "node:crypto";

function safeEqual(actual: string, expected: string): boolean {
  const actualBytes = Buffer.from(actual);
  const expectedBytes = Buffer.from(expected);
  return (
    actualBytes.length === expectedBytes.length &&
    timingSafeEqual(actualBytes, expectedBytes)
  );
}

export function consoleCredentialsConfigured(): boolean {
  return Boolean(
    process.env.EASTMED_CONSOLE_USERNAME && process.env.EASTMED_CONSOLE_PASSWORD,
  );
}

export function authenticateConsoleRequest(request: Request): boolean {
  if (
    process.env.EASTMED_CONSOLE_DEMO === "true" &&
    process.env.EASTMED_ENVIRONMENT?.toLowerCase() === "production"
  ) {
    return false;
  }
  const expectedUser = process.env.EASTMED_CONSOLE_USERNAME;
  const expectedPassword = process.env.EASTMED_CONSOLE_PASSWORD;
  const authorization = request.headers.get("authorization");
  if (!expectedUser || !expectedPassword || !authorization?.startsWith("Basic ")) {
    return false;
  }

  try {
    const decoded = Buffer.from(authorization.slice(6), "base64").toString("utf8");
    const separator = decoded.indexOf(":");
    if (separator < 0) return false;
    return (
      safeEqual(decoded.slice(0, separator), expectedUser) &&
      safeEqual(decoded.slice(separator + 1), expectedPassword)
    );
  } catch {
    return false;
  }
}

export function consoleAuthFailure(apiRequest: boolean): Response {
  if (!consoleCredentialsConfigured()) {
    const body = apiRequest
      ? JSON.stringify({ detail: "Console credentials are not configured" })
      : "Console credentials are not configured";
    return new Response(body, {
      status: 503,
      headers: { "content-type": apiRequest ? "application/json" : "text/plain" },
    });
  }
  const body = apiRequest
    ? JSON.stringify({ detail: "Console authentication required" })
    : "Console authentication required";
  return new Response(body, {
    status: 401,
    headers: {
      "content-type": apiRequest ? "application/json" : "text/plain",
      "www-authenticate": 'Basic realm="East Med Desk", charset="UTF-8"',
    },
  });
}
