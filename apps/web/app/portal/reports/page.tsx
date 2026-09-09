import { readPortalJson } from "@/lib/portal-api";
import type { PortalBoard } from "@/lib/portal-types";

import ReportsClient from "./ReportsClient";

export default async function ReportsPage() {
  const board = await readPortalJson<PortalBoard>("/api/v1/portal/board");
  const events = board.corridors.flatMap((corridor) => corridor.events);
  return <ReportsClient events={events} />;
}
