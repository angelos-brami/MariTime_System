import EventWorkspace from "./EventWorkspace";

export default async function EventWorkspacePage({
  params,
}: {
  params: Promise<{ eventId: string }>;
}) {
  const { eventId } = await params;
  return <EventWorkspace eventId={eventId} />;
}
