import { forwardPortalRequest } from "@/lib/portal-api";

export async function GET(
  _request: Request,
  context: { params: Promise<{ slug: string }> },
) {
  const { slug } = await context.params;
  const response = await forwardPortalRequest(
    `/api/v1/portal/events/${encodeURIComponent(slug)}/reliability-receipt`,
  );
  if (!response.ok) return response;
  return new Response(await response.arrayBuffer(), {
    status: response.status,
    headers: {
      "cache-control": "private, no-store",
      "content-disposition": `attachment; filename="eastmed-${slug}-reliability-receipt.json"`,
      "content-type": "application/json; charset=utf-8",
    },
  });
}
