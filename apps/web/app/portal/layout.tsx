import { UserButton } from "@clerk/nextjs";
import { auth } from "@clerk/nextjs/server";
import Link from "next/link";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { clerkConfigured, portalDemoEnabled } from "@/lib/portal-api";

export const dynamic = "force-dynamic";

export default async function PortalLayout({ children }: { children: ReactNode }) {
  const demo = portalDemoEnabled();
  const configured = clerkConfigured();
  if (!demo && configured) {
    const { userId } = await auth();
    if (!userId) redirect("/sign-in?redirect_url=/portal");
  }
  if (!demo && !configured) {
    return (
      <main className="authSetup">
        <p className="eyebrow">Subscriber portal</p>
        <h1>Customer authentication needs configuration.</h1>
        <p>Set the Clerk keys and portal API token before enabling subscriber access.</p>
      </main>
    );
  }
  return (
    <div className="portalFrame">
      <header className="portalHeader">
        <Link className="wordmark" href="/portal">
          <span className="wordmarkMark">EM</span>
          <span>Corridor Watch</span>
        </Link>
        <nav aria-label="Subscriber navigation">
          <Link href="/portal">Board</Link>
          <Link href="/portal/archive">Archive</Link>
          <Link href="/portal/calendar">Port &amp; strike calendar</Link>
          <Link href="/portal/ais">AIS context</Link>
          <Link href="/portal/briefs/latest">Daily brief</Link>
          <Link href="/portal/scoreboard">Quality</Link>
          <Link href="/portal/reports">Reports</Link>
        </nav>
        {demo ? <span className="portalDemoBadge">Demo subscriber</span> : <UserButton />}
      </header>
      {children}
      <footer className="portalFooter">
        <span>East Med Intelligence</span>
        <span>Information support—not navigational or safety advice.</span>
      </footer>
    </div>
  );
}
