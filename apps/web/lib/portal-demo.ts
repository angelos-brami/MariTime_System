import type {
  AISFeed,
  DailyBrief,
  MaritimeCalendar,
  PortalArchive,
  PortalBoard,
  PortalEvent,
  QualityScoreboard,
} from "@/lib/portal-types";

const publishedAt = "2026-07-19T05:42:00Z";
const eventVersionId = "74000000-0000-4000-8000-000000000001";

export const demoPortalBoard: PortalBoard = {
  generated_at: "2026-07-19T06:00:00Z",
  corridors: [
    {
      corridor: "hormuz_gulf",
      label: "Hormuz & Gulf",
      operational_state: "watch",
      event_count: 1,
      updated_at: publishedAt,
      events: [
        {
          event_id: "73000000-0000-4000-8000-000000000001",
          slug: "hormuz-authority-transit-procedure",
          title: "Authority issues revised Hormuz transit procedure",
          event_type: "navigation_warning",
          corridor: "hormuz_gulf",
          status: "monitoring",
          severity: 2,
          ports: [],
          version_no: 2,
          published_at: publishedAt,
          whats_changed: "The authority clarified the reporting window for eastbound traffic.",
        },
      ],
    },
    {
      corridor: "red_sea_bem_suez",
      label: "Red Sea · Bab el-Mandeb · Suez",
      operational_state: "disrupted",
      event_count: 1,
      updated_at: "2026-07-19T04:18:00Z",
      events: [
        {
          event_id: "73000000-0000-4000-8000-000000000002",
          slug: "suez-northbound-delay",
          title: "Northbound Suez convoy reporting extended delays",
          event_type: "port_disruption",
          corridor: "red_sea_bem_suez",
          status: "developing",
          severity: 3,
          ports: [{ name: "Suez", unlocode: "EGSUZ" }],
          version_no: 3,
          published_at: "2026-07-19T04:18:00Z",
          whats_changed: "Two independent operational sources now report delays above six hours.",
        },
      ],
    },
    {
      corridor: "east_med",
      label: "East Mediterranean",
      operational_state: "clear",
      event_count: 0,
      updated_at: null,
      events: [],
    },
    {
      corridor: "port_specific",
      label: "Port-specific watch",
      operational_state: "clear",
      event_count: 0,
      updated_at: null,
      events: [],
    },
  ],
};

export const demoPortalEvent: PortalEvent = {
  event_id: "73000000-0000-4000-8000-000000000001",
  slug: "hormuz-authority-transit-procedure",
  title: "Authority issues revised Hormuz transit procedure",
  event_type: "navigation_warning",
  corridor: "hormuz_gulf",
  status: "monitoring",
  severity: 2,
  ports: [],
  occurred_start: "2026-07-19T03:30:00Z",
  occurred_end: null,
  version_no: 2,
  latest_version_id: eventVersionId,
  published_at: publishedAt,
  content_hash: "b".repeat(64),
  summary_confirmed:
    "The maritime authority published a revised reporting window for eastbound transits.",
  summary_reported:
    "Two agents report that bridge teams are receiving the procedure during pre-arrival exchange.",
  summary_unknown:
    "No official confirmation has been published on whether westbound timing will also change.",
  whats_changed: "The authority clarified the reporting window for eastbound traffic.",
  timeline: [
    {
      version_no: 2,
      published_at: publishedAt,
      whats_changed: "The authority clarified the reporting window for eastbound traffic.",
      content_hash: "b".repeat(64),
    },
    {
      version_no: 1,
      published_at: "2026-07-19T04:12:00Z",
      whats_changed: "Initial holding line published from the authority notice.",
      content_hash: "a".repeat(64),
    },
  ],
  sources: [
    {
      source_record_id: "72000000-0000-4000-8000-000000000001",
      source_name: "Regional maritime authority",
      source_tier: "A",
      url: "https://example.com/advisory",
      directness: "primary",
      lineage_root_id: "71000000-0000-4000-8000-000000000001",
      excerpt: "Eastbound vessels should submit the revised transit report before arrival.",
      rights_basis: "public-advisory",
    },
  ],
  corrections: [],
};

export const demoSuezEvent: PortalEvent = {
  ...demoPortalEvent,
  event_id: "73000000-0000-4000-8000-000000000002",
  slug: "suez-northbound-delay",
  title: "Northbound Suez convoy reporting extended delays",
  event_type: "port_disruption",
  corridor: "red_sea_bem_suez",
  status: "developing",
  severity: 3,
  ports: [{ name: "Suez", unlocode: "EGSUZ" }],
  occurred_start: "2026-07-19T01:50:00Z",
  version_no: 3,
  latest_version_id: "74000000-0000-4000-8000-000000000003",
  published_at: "2026-07-19T04:18:00Z",
  content_hash: "4".repeat(64),
  summary_confirmed:
    "Two independent operational sources confirm northbound convoy delays above six hours.",
  summary_reported:
    "Agents report congestion building at the southern waiting area.",
  summary_unknown:
    "The canal authority has not published a full recovery estimate.",
  whats_changed: "Two independent operational sources now report delays above six hours.",
  timeline: [
    {
      version_no: 3,
      published_at: "2026-07-19T04:18:00Z",
      whats_changed: "Independent corroboration raised confidence in the delay estimate.",
      content_hash: "4".repeat(64),
    },
    {
      version_no: 2,
      published_at: "2026-07-19T03:12:00Z",
      whats_changed: "A second agent report was added as reported information.",
      content_hash: "3".repeat(64),
    },
  ],
  sources: [
    {
      source_record_id: "72000000-0000-4000-8000-000000000002",
      source_name: "Canal operations bulletin",
      source_tier: "A",
      url: "https://example.com/suez-bulletin",
      directness: "primary",
      lineage_root_id: "71000000-0000-4000-8000-000000000002",
      excerpt: "Northbound convoy delays are expected to exceed six hours.",
      rights_basis: "public-advisory",
    },
  ],
};

export const demoDailyBrief: DailyBrief = {
  id: "75000000-0000-4000-8000-000000000001",
  brief_date: "2026-07-19",
  title: "East Med Corridor Watch — 19 July 2026",
  introduction: "Overnight attention remains concentrated on Suez delays and Hormuz procedure changes.",
  forward_watch: "Watch for an authority update on westbound reporting and convoy recovery timing.",
  status: "finalized",
  items: [
    {
      event_id: demoPortalEvent.event_id,
      event_slug: demoPortalEvent.slug,
      event_version_id: eventVersionId,
      version_no: demoPortalEvent.version_no,
      title: demoPortalEvent.title,
      event_type: demoPortalEvent.event_type,
      corridor: demoPortalEvent.corridor,
      status: demoPortalEvent.status,
      severity: demoPortalEvent.severity,
      published_at: demoPortalEvent.published_at,
      summary_confirmed: demoPortalEvent.summary_confirmed,
      summary_reported: demoPortalEvent.summary_reported,
      summary_unknown: demoPortalEvent.summary_unknown,
      whats_changed: demoPortalEvent.whats_changed,
      content_hash: demoPortalEvent.content_hash,
    },
  ],
  source_version_ids: [eventVersionId],
  corrections: [],
  compiled_at: "2026-07-19T05:50:00Z",
  compiled_by: "scheduler",
  finalized_at: "2026-07-19T06:08:00Z",
  finalized_by: "senior-analyst",
  content_hash: "c".repeat(64),
};

export const demoQualityScoreboard: QualityScoreboard = {
  generated_at: "2026-07-19T06:15:00Z",
  published_version_count: 34,
  correction_count: 1,
  correction_rate_percent: 2.94,
  corrections_within_60_minutes_percent: 100,
  ttv_coverage_count: 18,
  holding_line_median_minutes: 11.5,
  verified_update_median_minutes: 37,
  holding_line_within_15_minutes_percent: 77.8,
  verified_update_within_45_minutes_percent: 72.2,
  corrections: [],
};

export const demoMaritimeCalendar: MaritimeCalendar = {
  generated_at: "2026-07-19T06:20:00Z",
  results: [
    {
      id: "78000000-0000-4000-8000-000000000001",
      calendar_event_id: "79000000-0000-4000-8000-000000000001",
      slug: "piraeus-tug-strike-ballot",
      version_no: 1,
      event_type: "strike",
      title: "Piraeus tug crews announce a 24-hour strike window",
      corridor: "port_specific",
      ports: [{ name: "Piraeus", unlocode: "GRPIR" }],
      starts_at: "2026-07-23T03:00:00Z",
      ends_at: "2026-07-24T03:00:00Z",
      status: "announced",
      public_note: "Tug availability may be constrained. Monitor the union and port authority notices for confirmation or cancellation.",
      source_record_ids: ["72000000-0000-4000-8000-000000000001"],
      published_at: "2026-07-19T06:10:00Z",
      published_by: "desk-analyst",
      content_hash: "d".repeat(64),
    },
  ],
};

export const demoAISFeed: AISFeed = {
  generated_at: "2026-07-19T06:22:00Z",
  cache_status: "live",
  caveat: "AIS-derived; subject to interference/spoofing in conflict areas.",
  freshness_minutes: 30,
  results: [
    {
      id: "7a000000-0000-4000-8000-000000000001",
      mmsi: "241000001",
      imo: "9300001",
      vessel_name: "AEGEAN DEMO",
      latitude: 37.72,
      longitude: 23.54,
      course: 185.2,
      speed: 11.4,
      navigation_status: "0",
      corridor: "east_med",
      message_at: "2026-07-19T06:14:00Z",
      received_at: "2026-07-19T06:14:02Z",
      age_minutes: 8,
      stale: false,
      source: "aisstream.io",
      payload_hash: "e".repeat(64),
    },
    {
      id: "7a000000-0000-4000-8000-000000000002",
      mmsi: "636000002",
      imo: null,
      vessel_name: "CORRIDOR SAMPLE",
      latitude: 26.35,
      longitude: 56.11,
      course: 271,
      speed: 8.7,
      navigation_status: "0",
      corridor: "hormuz_gulf",
      message_at: "2026-07-19T05:42:00Z",
      received_at: "2026-07-19T05:42:01Z",
      age_minutes: 40,
      stale: true,
      source: "aisstream.io",
      payload_hash: "f".repeat(64),
    },
  ],
};

export function demoArchive(url: URL): PortalArchive {
  const query = url.searchParams.get("q")?.trim().toLowerCase() || null;
  const corridor = url.searchParams.get("corridor");
  const results = demoPortalBoard.corridors
    .flatMap((item) => item.events)
    .filter((item) => !query || `${item.title} ${item.whats_changed}`.toLowerCase().includes(query))
    .filter((item) => !corridor || item.corridor === corridor)
    .map((item) => ({
      ...item,
      summary_confirmed:
        item.slug === demoPortalEvent.slug
          ? demoPortalEvent.summary_confirmed
          : "Operational reports confirm an active delay affecting the corridor.",
      rank: query ? 0.75 : null,
    }));
  return { query, total: results.length, limit: 20, offset: 0, results };
}

function demoReportPdf(): Response {
  return new Response(
    new TextEncoder().encode(
      "%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n",
    ),
    {
      headers: {
        "content-disposition": "attachment; filename=\"eastmed-portal-report-demo.pdf\"",
        "content-type": "application/pdf",
      },
    },
  );
}

export function portalDemoResponse(path: string, init: RequestInit = {}): Response {
  const url = new URL(path, "http://portal.demo");
  const method = (init.method ?? "GET").toUpperCase();
  if (url.pathname === "/api/v1/portal/me") {
    return Response.json({
      user_id: "76000000-0000-4000-8000-000000000001",
      account_id: "77000000-0000-4000-8000-000000000001",
      email: "ops@example.test",
      company: "Aegean Shipping Demo",
      role: "subscriber",
    });
  }
  if (url.pathname === "/api/v1/portal/board") return Response.json(demoPortalBoard);
  if (url.pathname === "/api/v1/portal/archive") return Response.json(demoArchive(url));
  if (url.pathname === "/api/v1/portal/briefs/latest") return Response.json(demoDailyBrief);
  if (url.pathname === "/api/v1/public/scoreboard") return Response.json(demoQualityScoreboard);
  if (url.pathname === "/api/v1/portal/calendar") return Response.json(demoMaritimeCalendar);
  if (url.pathname === "/api/v1/portal/ais") return Response.json(demoAISFeed);
  if (url.pathname === "/api/v1/portal/reports/state-of-knowledge" && method === "POST") {
    let payload: Record<string, unknown> = {};
    if (typeof init.body === "string") {
      try { payload = JSON.parse(init.body) as Record<string, unknown>; } catch { payload = {}; }
    }
    const event = payload.event_id === demoSuezEvent.event_id ? demoSuezEvent : demoPortalEvent;
    return Response.json({
      id: "7b000000-0000-4000-8000-000000000001",
      event_id: event.event_id,
      event_version_id: event.event_id === demoSuezEvent.event_id
        ? "74000000-0000-4000-8000-000000000003"
        : eventVersionId,
      account_id: "77000000-0000-4000-8000-000000000001",
      requested_timestamp: payload.requested_timestamp,
      requested_by: "portal:demo-subscriber",
      generated_at: new Date().toISOString(),
      version_content_hash: event.content_hash,
      content_hash: "5".repeat(64),
    }, { status: 201 });
  }
  if (/^\/api\/v1\/portal\/reports\/state-of-knowledge\/[^/]+\.pdf$/.test(url.pathname)) {
    return demoReportPdf();
  }
  const receiptMatch = url.pathname.match(
    /^\/api\/v1\/portal\/events\/([^/]+)\/reliability-receipt$/,
  );
  if (receiptMatch) {
    const slug = decodeURIComponent(receiptMatch[1]);
    const event = slug === demoSuezEvent.slug ? demoSuezEvent : demoPortalEvent;
    if (slug !== event.slug) {
      return Response.json({ detail: "Published event not found" }, { status: 404 });
    }
    return Response.json({
      receipt_schema_version: "1.0",
      receipt_hash: "e".repeat(64),
      event_version_id: event.latest_version_id,
      event_id: event.event_id,
      version_no: event.version_no,
      published_at: event.published_at,
      published_by: "desk:demo-publisher",
      signed_off_by: event.severity >= 3 ? "desk:demo-approver" : null,
      publication_approval_id:
        event.severity >= 3 ? "a5000000-0000-4000-8000-000000000001" : null,
      policy_version: "publication-policy-v1",
      model_versions: {},
      content_hash: event.content_hash,
      sentence_claim_map: { "confirmed:0": ["claim-demo-1"] },
      event_snapshot: { id: event.event_id, slug: event.slug, severity: event.severity },
      claim_snapshots: [],
      evidence_snapshots: [],
    });
  }
  if (url.pathname.startsWith("/api/v1/portal/events/")) {
    const slug = decodeURIComponent(url.pathname.split("/").at(-1) ?? "");
    if (slug === demoPortalEvent.slug) return Response.json(demoPortalEvent);
    if (slug === demoSuezEvent.slug) return Response.json(demoSuezEvent);
    return Response.json({ detail: "Published event not found" }, { status: 404 });
  }
  return Response.json({ detail: "Demo portal route not found" }, { status: 404 });
}
