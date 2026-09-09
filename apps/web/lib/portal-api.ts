import "server-only";

import { auth } from "@clerk/nextjs/server";

import { portalDemoResponse } from "@/lib/portal-demo";

export function portalDemoEnabled(): boolean {
  return (
    process.env.EASTMED_PORTAL_DEMO === "true" &&
    process.env.EASTMED_ENVIRONMENT?.toLowerCase() !== "production"
  );
}

export function clerkConfigured(): boolean {
  return Boolean(
    process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY && process.env.CLERK_SECRET_KEY,
  );
}

export async function forwardPortalRequest(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  if (portalDemoEnabled()) return portalDemoResponse(path, init);
  if (!clerkConfigured()) {
    return Response.json({ detail: "Subscriber authentication is not configured" }, { status: 503 });
  }
  const { userId } = await auth();
  if (!userId) {
    return Response.json({ detail: "Subscriber sign-in required" }, { status: 401 });
  }
  const baseUrl = process.env.EASTMED_API_BASE_URL;
  const token = process.env.EASTMED_PORTAL_API_TOKEN;
  if (!baseUrl || !token) {
    return Response.json({ detail: "Portal API connection is not configured" }, { status: 503 });
  }
  try {
    const upstream = await fetch(new URL(path, baseUrl), {
      ...init,
      cache: "no-store",
      signal: AbortSignal.timeout(10_000),
      headers: {
        accept: "application/json",
        ...(init.body ? { "content-type": "application/json" } : {}),
        ...init.headers,
        // Identity/auth headers are spread last so a caller-supplied header can
        // never override the server-derived subject or the portal token.
        "x-portal-subject": userId,
        "x-portal-token": token,
      },
    });
    const disposition = upstream.headers.get("content-disposition");
    return new Response(await upstream.arrayBuffer(), {
      status: upstream.status,
      headers: {
        "cache-control": "private, no-store",
        "content-type": upstream.headers.get("content-type") ?? "application/json",
        ...(disposition ? { "content-disposition": disposition } : {}),
      },
    });
  } catch {
    return Response.json({ detail: "Portal API is unavailable" }, { status: 502 });
  }
}

export async function readPortalJson<T>(path: string): Promise<T> {
  const response = await forwardPortalRequest(path);
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as { detail?: string };
    throw new Error(payload.detail ?? `Portal request failed (${response.status})`);
  }
  return (await response.json()) as T;
}
