import { ClerkProvider } from "@clerk/nextjs";
import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "East Med Corridor Watch",
  description: "Verified maritime event intelligence for the East Med, Red Sea, and Gulf.",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  const content = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY ? (
    <ClerkProvider dynamic>{children}</ClerkProvider>
  ) : (
    children
  );
  return (
    <html lang="en">
      <body>{content}</body>
    </html>
  );
}
