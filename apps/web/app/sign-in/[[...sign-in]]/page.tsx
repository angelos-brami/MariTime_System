import { SignIn } from "@clerk/nextjs";
import { redirect } from "next/navigation";

import { clerkConfigured, portalDemoEnabled } from "@/lib/portal-api";

export default function SignInPage() {
  if (portalDemoEnabled()) redirect("/portal");
  if (!clerkConfigured()) {
    return (
      <main className="authSetup">
        <p className="eyebrow">Subscriber login</p>
        <h1>Clerk authentication is not configured.</h1>
        <p>Add the Clerk publishable and secret keys to enable customer sign-in.</p>
      </main>
    );
  }
  return <main className="authSetup"><SignIn routing="path" path="/sign-in" /></main>;
}
