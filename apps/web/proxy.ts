import { clerkMiddleware } from "@clerk/nextjs/server";
import type { NextFetchEvent } from "next/server";
import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

import {
  authenticateConsoleRequest,
  consoleAuthFailure,
} from "./lib/console-auth";

const clerkProxy = clerkMiddleware(async (auth, request) => {
  const protectedDeskRoute =
    request.nextUrl.pathname.startsWith("/console") ||
    request.nextUrl.pathname.startsWith("/api/console");
  if (protectedDeskRoute && process.env.EASTMED_DESK_AUTH_MODE === "oidc") {
    await auth.protect();
  }
});

export default function proxy(request: NextRequest, event: NextFetchEvent) {
  const deskRoute =
    request.nextUrl.pathname.startsWith("/console") ||
    request.nextUrl.pathname.startsWith("/api/console");
  const clerkConfigured = Boolean(
    process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY &&
      process.env.CLERK_SECRET_KEY,
  );
  if (deskRoute && process.env.EASTMED_DESK_AUTH_MODE === "oidc") {
    if (!clerkConfigured) {
      return new Response("Production desk identity is not configured", {
        status: 503,
        headers: { "content-type": "text/plain" },
      });
    }
    return clerkProxy(request, event);
  }
  if (deskRoute && !authenticateConsoleRequest(request)) {
    return consoleAuthFailure(request.nextUrl.pathname.startsWith("/api/"));
  }
  if (clerkConfigured) return clerkProxy(request, event);
  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
    "/__clerk/(.*)",
  ],
};
