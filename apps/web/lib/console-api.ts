import "server-only";

import { auth } from "@clerk/nextjs/server";

import { demoAccounts, demoWorkspace } from "@/lib/console-demo";
import { demoMaritimeCalendar, demoQualityScoreboard } from "@/lib/portal-demo";

type DemoPayload = Record<string, unknown>;

const demoDataAccount = demoAccounts.find((account) => account.tier === "data")!;
let demoApiKeys: DemoPayload[] = [
  {
    id: "da000000-0000-4000-8000-000000000001",
    account_id: demoDataAccount.id,
    name: "Read-only pilot",
    key_prefix: "em_demo_8f21",
    scopes_json: ["events:read", "versions:read", "claims:read"],
    created_at: "2026-07-19T12:00:00Z",
    created_by: "desk-admin",
    expires_at: null,
    last_used_at: "2026-07-19T17:45:00Z",
    revoked_at: null,
    revoked_by: null,
  },
];
const demoPrincipal = {
  id: "d5000000-0000-4000-8000-000000000001",
  auth_issuer: "https://identity.demo.eastmed.test",
  auth_subject: "demo-senior-analyst",
  email: "senior.analyst@eastmed.test",
  display_name: "Maya Petrou",
  role: "senior_analyst",
  auth_assurance: "phishing_resistant",
};
let demoApprovalRequests: DemoPayload[] = [
  {
    id: "a5000000-0000-4000-8000-000000000001",
    request_type: "event_publication",
    target_id: demoWorkspace.id,
    request_hash: "4".repeat(64),
    request_payload: {
      draft: {
        title: demoWorkspace.title,
        sentences: demoWorkspace.versions[0].sentences,
        evidence_ids: demoWorkspace.claims.flatMap((claim) =>
          claim.evidence.map((evidence) => evidence.id),
        ),
        policy_version: "publication-policy-v1",
        model_versions: {},
      },
      preapproval_preview: { ready: true, next_version_no: 3 },
    },
    primary_user_id: "d5000000-0000-4000-8000-000000000002",
    primary_display_name: "Nikos Andreou",
    status: "pending",
    request_reason: "Severity-three operational update requires independent release review.",
    requested_at: "2026-07-21T08:42:00Z",
    expires_at: "2026-07-22T08:42:00Z",
    decided_at: null,
    decided_by_user_id: null,
    decided_by_display_name: null,
    decision_reason: null,
    approval_id: null,
    release_hash: null,
    release_expires_at: null,
    approved_payload: null,
  },
];
const demoAISystems: DemoPayload[] = [
  {
    id: "a1000000-0000-4000-8000-000000000001",
    name: "claim-extraction-v1",
    purpose: "Evidence-bound multilingual claim extraction in non-publishing review modes.",
    manifest_schema_version: "1",
    manifest: {
      provider: "anthropic",
      model_snapshot: "claude-sonnet-5",
      prompt_hash: "1".repeat(64),
      output_schema_hash: "2".repeat(64),
    },
    fingerprint: "8f2ddbe423ac700da913f511574deb5828be3d29ce9a9bf46fb2a8f8df2440f1",
    created_by_user_id: demoPrincipal.id,
    created_at: "2026-07-20T11:20:00Z",
  },
];
let demoAIControls: DemoPayload[] = [
  {
    id: "ac000000-0000-4000-8000-000000000001",
    scope: "all_model_calls",
    revision: 8,
    mode: "shadow",
    risk_tier: 0,
    system_version_id: null,
    reason: "Time-bounded shadow evaluation during launch readiness.",
    approval_refs: {},
    changed_by_user_id: demoPrincipal.id,
    auth_assurance: "phishing_resistant",
    previous_control_id: null,
    expires_at: new Date(Date.now() + 6 * 60 * 60_000).toISOString(),
    changed_at: new Date(Date.now() - 2 * 60 * 60_000).toISOString(),
  },
  {
    id: "ac000000-0000-4000-8000-000000000002",
    scope: "claim_extraction",
    revision: 5,
    mode: "shadow",
    risk_tier: 1,
    system_version_id: demoAISystems[0].id,
    reason: "Exact-fingerprint claim extraction remains shadow only.",
    approval_refs: { change: "LAUNCH-42" },
    changed_by_user_id: demoPrincipal.id,
    auth_assurance: "phishing_resistant",
    previous_control_id: null,
    expires_at: new Date(Date.now() + 6 * 60 * 60_000).toISOString(),
    changed_at: new Date(Date.now() - 2 * 60 * 60_000).toISOString(),
  },
];
let demoAIIncidents: DemoPayload[] = [
  {
    id: "a1700000-0000-4000-8000-000000000001",
    title: "Turkish canary extraction drift",
    capability_scope: "claim_extraction",
    system_version_id: demoAISystems[0].id,
    detected_at: "2026-07-20T14:08:00Z",
    reported_by_user_id: demoPrincipal.id,
    created_at: "2026-07-20T14:09:00Z",
    status: "resolved",
    severity: "medium",
    events: [
      {
        id: "a1710000-0000-4000-8000-000000000001",
        incident_id: "a1700000-0000-4000-8000-000000000001",
        sequence: 1,
        status: "open",
        severity: "medium",
        summary: "Canary edit distance exceeded the registered Turkish-language threshold.",
        containment_action: null,
        evidence_refs: ["evaluation:tr-canary-20260720"],
        changed_by_user_id: demoPrincipal.id,
        auth_assurance: "phishing_resistant",
        at: "2026-07-20T14:09:00Z",
      },
      {
        id: "a1710000-0000-4000-8000-000000000002",
        incident_id: "a1700000-0000-4000-8000-000000000001",
        sequence: 2,
        status: "resolved",
        severity: "medium",
        summary: "Prompt revision retired; the complete Turkish canary set passed review.",
        containment_action: null,
        evidence_refs: ["evaluation:tr-canary-20260720-r2"],
        changed_by_user_id: demoPrincipal.id,
        auth_assurance: "phishing_resistant",
        at: "2026-07-20T17:21:00Z",
      },
    ],
  },
];

function demoPayload(init: RequestInit): DemoPayload {
  if (typeof init.body !== "string") return {};
  try {
    return JSON.parse(init.body) as DemoPayload;
  } catch {
    return {};
  }
}

function demoPdf(filename: string): Response {
  const bytes = new TextEncoder().encode(
    "%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n",
  );
  return new Response(bytes, {
    headers: {
      "content-disposition": `attachment; filename=\"${filename}\"`,
      "content-type": "application/pdf",
    },
  });
}

function consoleDemoResponse(path: string, init: RequestInit): Response {
  const url = new URL(path, "http://console.demo");
  const payload = demoPayload(init);
  const method = (init.method ?? "GET").toUpperCase();
  if (url.pathname === "/api/v1/public/scoreboard") {
    return Response.json(demoQualityScoreboard);
  }
  if (url.pathname === "/api/v1/desk/me" && method === "GET") {
    return Response.json(demoPrincipal);
  }
  if (url.pathname === "/api/v1/ai/governance/capabilities" && method === "GET") {
    return Response.json(demoAIControls);
  }
  const capabilityMatch = url.pathname.match(
    /^\/api\/v1\/ai\/governance\/capabilities\/([^/]+)$/,
  );
  if (capabilityMatch && method === "POST") {
    const capabilityScope = decodeURIComponent(capabilityMatch[1]);
    const previous = demoAIControls.find((control) => control.scope === capabilityScope);
    const control = {
      id: crypto.randomUUID(),
      scope: capabilityScope,
      revision: Number(previous?.revision ?? 0) + 1,
      mode: payload.mode,
      risk_tier: payload.risk_tier,
      system_version_id: payload.system_version_id ?? null,
      reason: payload.reason,
      approval_refs: payload.approval_refs ?? {},
      changed_by_user_id: demoPrincipal.id,
      auth_assurance: demoPrincipal.auth_assurance,
      previous_control_id: previous?.id ?? null,
      expires_at: payload.expires_at ?? null,
      changed_at: new Date().toISOString(),
    };
    demoAIControls = [
      control,
      ...demoAIControls.filter((existing) => existing.scope !== capabilityScope),
    ];
    return Response.json(control, { status: 201 });
  }
  if (url.pathname === "/api/v1/ai/governance/system-versions" && method === "GET") {
    return Response.json(demoAISystems);
  }
  if (url.pathname === "/api/v1/ai/governance/incidents" && method === "GET") {
    const includeResolved = url.searchParams.get("include_resolved") !== "false";
    return Response.json(
      includeResolved
        ? demoAIIncidents
        : demoAIIncidents.filter((incident) => incident.status !== "resolved"),
    );
  }
  if (url.pathname === "/api/v1/ai/governance/incidents" && method === "POST") {
    const incidentId = crypto.randomUUID();
    const severe = payload.severity === "high" || payload.severity === "critical";
    const lifecycleEvent = {
      id: crypto.randomUUID(),
      incident_id: incidentId,
      sequence: 1,
      status: severe ? "contained" : "open",
      severity: payload.severity,
      summary: payload.summary,
      containment_action: severe
        ? payload.containment_action ?? `Automatically disabled ${payload.capability_scope}.`
        : payload.containment_action ?? null,
      evidence_refs: payload.evidence_refs ?? [],
      changed_by_user_id: demoPrincipal.id,
      auth_assurance: demoPrincipal.auth_assurance,
      at: new Date().toISOString(),
    };
    const incident = {
      id: incidentId,
      title: payload.title,
      capability_scope: payload.capability_scope,
      system_version_id: payload.system_version_id ?? null,
      detected_at: payload.detected_at,
      reported_by_user_id: demoPrincipal.id,
      created_at: new Date().toISOString(),
      status: lifecycleEvent.status,
      severity: payload.severity,
      events: [lifecycleEvent],
    };
    demoAIIncidents = [incident, ...demoAIIncidents];
    if (severe) {
      const previous = demoAIControls.find(
        (control) => control.scope === payload.capability_scope,
      );
      demoAIControls = [
        {
          id: crypto.randomUUID(),
          scope: payload.capability_scope,
          revision: Number(previous?.revision ?? 0) + 1,
          mode: "disabled",
          risk_tier: payload.severity === "critical" ? 4 : 3,
          system_version_id: null,
          reason: `Automatic incident containment: ${payload.title}`,
          approval_refs: {},
          changed_by_user_id: demoPrincipal.id,
          auth_assurance: demoPrincipal.auth_assurance,
          previous_control_id: previous?.id ?? null,
          expires_at: null,
          changed_at: new Date().toISOString(),
        },
        ...demoAIControls.filter((control) => control.scope !== payload.capability_scope),
      ];
    }
    return Response.json(incident, { status: 201 });
  }
  const incidentEventMatch = url.pathname.match(
    /^\/api\/v1\/ai\/governance\/incidents\/([^/]+)\/events$/,
  );
  if (incidentEventMatch && method === "POST") {
    const incident = demoAIIncidents.find((item) => item.id === incidentEventMatch[1]);
    if (!incident) return Response.json({ detail: "AI incident not found" }, { status: 404 });
    const events = incident.events as DemoPayload[];
    const lifecycleEvent = {
      id: crypto.randomUUID(),
      incident_id: incident.id,
      sequence: events.length + 1,
      status: payload.status,
      severity: payload.severity,
      summary: payload.summary,
      containment_action: payload.containment_action ?? null,
      evidence_refs: payload.evidence_refs ?? [],
      changed_by_user_id: demoPrincipal.id,
      auth_assurance: demoPrincipal.auth_assurance,
      at: new Date().toISOString(),
    };
    events.push(lifecycleEvent);
    incident.status = payload.status;
    incident.severity = payload.severity;
    return Response.json(incident, { status: 201 });
  }
  if (url.pathname === "/api/v1/operations/dashboard" && method === "GET") {
    const openAIIncidents = demoAIIncidents.filter(
      (incident) => incident.status !== "resolved",
    ).length;
    return Response.json({
      generated_at: new Date().toISOString(),
      environment: "launch-demo",
      overall_status: openAIIncidents ? "action_required" : "ready",
      outbound_enabled: true,
      desk_auth_mode: "oidc",
      pending_triage: 4,
      open_events: 7,
      high_severity_events: 2,
      pending_extraction_proposals: 18,
      open_desk_alerts: 0,
      pending_approval_requests: demoApprovalRequests.filter(
        (request) => request.status === "pending",
      ).length,
      approved_releases_expiring: demoApprovalRequests.filter(
        (request) => request.status === "approved",
      ).length,
      open_ai_incidents: openAIIncidents,
      active_sources: 68,
      total_sources: 72,
      source_health: { healthy: 64, pending_first_poll: 4, disabled: 4 },
      delivery_status: { queued: 3, sent: 1, delivered: 146, failed: 0 },
      ai_controls: demoAIControls,
      readiness: [
        { key: "database", label: "Database connection", state: "pass", detail: "PostgreSQL schema and migration head are available.", href: null },
        { key: "identity", label: "Production desk identity", state: "pass", detail: "Cryptographically verified OIDC with phishing-resistant assurance.", href: "/console/approvals" },
        { key: "sources", label: "Approved source coverage", state: "pass", detail: "68 active sources exceed the 60-source launch target.", href: "/console/triage" },
        { key: "pollers", label: "Poller health", state: "pass", detail: "No active source is overdue or degraded.", href: null },
        { key: "delivery", label: "Delivery health", state: "pass", detail: "No failed alert or correction deliveries.", href: "/console/alerts" },
        { key: "outbound", label: "Outbound release switch", state: "pass", detail: "Outbound release is enabled.", href: null },
        { key: "ai", label: "AI execution control", state: "pass", detail: "Shadow controls are fingerprint-bound and time-limited.", href: "/console/operations" },
        {
          key: "ai-incidents",
          label: "AI incident queue",
          state: openAIIncidents ? "fail" : "pass",
          detail: openAIIncidents
            ? `${openAIIncidents} AI incident remains unresolved.`
            : "No AI incidents remain unresolved.",
          href: "/console/operations",
        },
        { key: "alerts", label: "Unresolved desk alerts", state: "pass", detail: "No unresolved source or ingestion alerts.", href: null },
      ],
    });
  }
  if (url.pathname === "/api/v1/approval-requests" && method === "GET") {
    const status = url.searchParams.get("status");
    return Response.json(
      status
        ? demoApprovalRequests.filter((request) => request.status === status)
        : demoApprovalRequests,
    );
  }
  const decisionMatch = url.pathname.match(
    /^\/api\/v1\/approval-requests\/([^/]+)\/decision$/,
  );
  if (decisionMatch && method === "POST") {
    const request = demoApprovalRequests.find((item) => item.id === decisionMatch[1]);
    if (!request) {
      return Response.json({ detail: "Approval request not found" }, { status: 404 });
    }
    const approved = payload.decision === "approve";
    const decidedAt = new Date().toISOString();
    const releaseHash = approved ? "5".repeat(64) : null;
    const draft = (request.request_payload as DemoPayload | undefined)?.draft ?? null;
    Object.assign(request, {
      status: approved ? "approved" : "rejected",
      decided_at: decidedAt,
      decided_by_user_id: demoPrincipal.id,
      decided_by_display_name: demoPrincipal.display_name,
      decision_reason: payload.reason,
      approval_id: approved ? crypto.randomUUID() : null,
      release_hash: releaseHash,
      release_expires_at: approved
        ? new Date(Date.now() + 15 * 60_000).toISOString()
        : null,
      approved_payload: approved
        ? {
            ...(draft as DemoPayload | null),
            published_by: `desk:${request.primary_user_id}`,
            signed_off_by: `desk:${demoPrincipal.id}`,
          }
        : null,
    });
    return Response.json(request);
  }
  const publicationRequestMatch = url.pathname.match(
    /^\/api\/v1\/events\/([^/]+)\/publication-requests$/,
  );
  if (publicationRequestMatch && method === "POST") {
    const now = new Date();
    const request = {
      id: crypto.randomUUID(),
      request_type: "event_publication",
      target_id: publicationRequestMatch[1],
      request_hash: crypto.randomUUID().replaceAll("-", "").padEnd(64, "0"),
      request_payload: { draft: payload.draft, preapproval_preview: { ready: true } },
      primary_user_id: demoPrincipal.id,
      primary_display_name: demoPrincipal.display_name,
      status: "pending",
      request_reason: payload.reason,
      requested_at: now.toISOString(),
      expires_at: new Date(now.getTime() + 24 * 60 * 60_000).toISOString(),
      decided_at: null,
      decided_by_user_id: null,
      decided_by_display_name: null,
      decision_reason: null,
      approval_id: null,
      release_hash: null,
      release_expires_at: null,
      approved_payload: null,
    };
    demoApprovalRequests = [request, ...demoApprovalRequests];
    return Response.json(request, { status: 201 });
  }
  const secondReviewMatch = url.pathname.match(
    /^\/api\/v1\/events\/([^/]+)\/claims\/([^/]+)\/second-review$/,
  );
  if (secondReviewMatch && method === "POST") {
    const claim = demoWorkspace.claims.find((item) => item.id === secondReviewMatch[2]);
    if (!claim) return Response.json({ detail: "Claim not found" }, { status: 404 });
    claim.second_reviewed_by = `desk:${demoPrincipal.id}`;
    return Response.json({
      id: crypto.randomUUID(),
      claim_id: claim.id,
      primary_reviewer: claim.reviewed_by,
      second_reviewer: claim.second_reviewed_by,
      auth_assurance: demoPrincipal.auth_assurance,
      reason: payload.reason,
      approved_at: new Date().toISOString(),
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    }, { status: 201 });
  }
  if (url.pathname === "/api/v1/corrections/approval-requests" && method === "POST") {
    const now = new Date();
    const draft = payload.draft as DemoPayload;
    const request = {
      id: crypto.randomUUID(),
      request_type: "operational_correction",
      target_id: draft.event_id,
      request_hash: crypto.randomUUID().replaceAll("-", "").padEnd(64, "0"),
      request_payload: { draft, preapproval_preview: { recipient_count: 4 } },
      primary_user_id: demoPrincipal.id,
      primary_display_name: demoPrincipal.display_name,
      status: "pending",
      request_reason: payload.reason,
      requested_at: now.toISOString(),
      expires_at: new Date(now.getTime() + 24 * 60 * 60_000).toISOString(),
      decided_at: null,
      decided_by_user_id: null,
      decided_by_display_name: null,
      decision_reason: null,
      approval_id: null,
      release_hash: null,
      release_expires_at: null,
      approved_payload: null,
    };
    demoApprovalRequests = [request, ...demoApprovalRequests];
    return Response.json(request, { status: 201 });
  }
  if (url.pathname === "/api/v1/openapi.json") {
    return Response.json({
      openapi: "3.1.0",
      info: { title: "East Med Maritime Event Intelligence", version: "0.1.0-demo" },
      paths: {
        "/api/v1/data/events": { get: { summary: "List published events" } },
        "/api/v1/data/events/{event_id}/versions": {
          get: { summary: "List immutable event versions" },
        },
        "/api/v1/data/events/{event_id}/claims": {
          get: { summary: "List published claim snapshots" },
        },
      },
    });
  }
  if (url.pathname === "/api/v1/api-keys" && method === "GET") {
    const accountId = url.searchParams.get("account_id");
    return Response.json(
      accountId ? demoApiKeys.filter((key) => key.account_id === accountId) : demoApiKeys,
    );
  }
  if (url.pathname === "/api/v1/api-keys" && method === "POST") {
    const id = crypto.randomUUID();
    const issued = {
      id,
      account_id: payload.account_id,
      name: payload.name,
      key_prefix: "em_demo_new1",
      scopes_json: payload.scopes,
      created_at: new Date().toISOString(),
      created_by: payload.created_by,
      expires_at: payload.expires_at ?? null,
      last_used_at: null,
      revoked_at: null,
      revoked_by: null,
      api_key: `em_demo_${crypto.randomUUID().replaceAll("-", "")}`,
    };
    demoApiKeys = [...demoApiKeys, issued];
    return Response.json(issued, { status: 201 });
  }
  const revokeMatch = url.pathname.match(/^\/api\/v1\/api-keys\/([^/]+)\/revoke$/);
  if (revokeMatch && method === "POST") {
    const revokedAt = new Date().toISOString();
    demoApiKeys = demoApiKeys.map((key) =>
      key.id === revokeMatch[1]
        ? { ...key, revoked_at: revokedAt, revoked_by: payload.revoked_by }
        : key,
    );
    const revoked = demoApiKeys.find((key) => key.id === revokeMatch[1]);
    return revoked
      ? Response.json(revoked)
      : Response.json({ detail: "API key not found" }, { status: 404 });
  }
  if (url.pathname === "/api/v1/reports/state-of-knowledge" && method === "POST") {
    return Response.json({
      id: "db000000-0000-4000-8000-000000000001",
      event_id: payload.event_id,
      event_version_id: demoWorkspace.versions[0].id,
      account_id: null,
      requested_timestamp: payload.requested_timestamp,
      requested_by: payload.requested_by,
      generated_at: new Date().toISOString(),
      version_content_hash: demoWorkspace.versions[0].content_hash,
      content_hash: "9".repeat(64),
    });
  }
  if (/^\/api\/v1\/reports\/state-of-knowledge\/[^/]+\.pdf$/.test(url.pathname)) {
    return demoPdf("eastmed-state-of-knowledge-demo.pdf");
  }
  if (url.pathname === "/api/v1/corrections/preview" && method === "POST") {
    const from = demoWorkspace.versions.find((item) => item.id === payload.version_from_id)
      ?? demoWorkspace.versions[1];
    const to = demoWorkspace.versions.find((item) => item.id === payload.version_to_id)
      ?? demoWorkspace.versions[0];
    return Response.json({
      preview_hash: "7".repeat(64),
      event_id: demoWorkspace.id,
      event_slug: demoWorkspace.slug,
      version_from_id: from.id,
      version_to_id: to.id,
      version_from: from.version_no,
      version_to: to.version_no,
      affected_version_hash: from.content_hash,
      corrected_version_hash: to.content_hash,
      channels: ["email", "telegram"],
      recipient_count: 4,
      message: { note: payload.note },
    });
  }
  if (url.pathname === "/api/v1/corrections" && method === "POST") {
    return Response.json({
      id: crypto.randomUUID(),
      ...payload,
      issued_at: new Date().toISOString(),
      issued_by: payload.signed_off_by,
      propagated_channels_json: { email: 3, telegram: 1 },
    }, { status: 201 });
  }
  if (url.pathname === "/api/v1/corrections" && method === "GET") {
    return Response.json([]);
  }
  if (url.pathname === "/api/v1/ttv" && method === "POST") {
    return Response.json({ id: crypto.randomUUID(), ...payload }, { status: 201 });
  }
  if (/^\/api\/v1\/ttv\/[^/]+$/.test(url.pathname) && method === "PATCH") {
    return Response.json({ event_id: url.pathname.split("/").at(-1), ...payload });
  }
  if (url.pathname === "/api/v1/calendar" && method === "POST") {
    return Response.json({
      ...demoMaritimeCalendar.results[0],
      id: crypto.randomUUID(),
      calendar_event_id: crypto.randomUUID(),
      ...payload,
      version_no: 1,
      published_at: new Date().toISOString(),
      content_hash: "6".repeat(64),
    }, { status: 201 });
  }
  if (/^\/api\/v1\/users\/[^/]+\/channels$/.test(url.pathname) && method === "PATCH") {
    return Response.json({
      id: url.pathname.split("/").at(-2),
      email: "ops@example.test",
      phone: null,
      channels: payload.channels ?? {},
      role: "operations",
      auth_subject: null,
      active: true,
      portal_enabled: true,
    });
  }
  return Response.json({ detail: "Demo console route not found" }, { status: 404 });
}

export function consoleDemoEnabled(): boolean {
  return (
    process.env.EASTMED_CONSOLE_DEMO === "true" &&
    process.env.EASTMED_ENVIRONMENT?.toLowerCase() !== "production"
  );
}

export async function forwardDeskRequest(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  if (consoleDemoEnabled()) return consoleDemoResponse(path, init);
  const baseUrl = process.env.EASTMED_API_BASE_URL;
  const token = process.env.EASTMED_DESK_API_TOKEN;
  const subject = process.env.EASTMED_CONSOLE_USERNAME;
  const issuer = process.env.EASTMED_CONSOLE_AUTH_ISSUER ?? "eastmed-console";
  const oidcMode = process.env.EASTMED_DESK_AUTH_MODE === "oidc";
  if (!baseUrl || !token || (!oidcMode && !subject)) {
    return Response.json(
      { detail: "Desk API connection is not configured" },
      { status: 503 },
    );
  }

  let identityHeaders: Record<string, string>;
  if (oidcMode) {
    try {
      const session = await auth();
      const template = process.env.EASTMED_DESK_OIDC_TOKEN_TEMPLATE;
      const oidcToken = await session.getToken(template ? { template } : undefined);
      if (!oidcToken) {
        return Response.json(
          { detail: "A signed desk OIDC session is required" },
          { status: 401 },
        );
      }
      identityHeaders = { authorization: `Bearer ${oidcToken}` };
    } catch {
      return Response.json(
        { detail: "Desk OIDC session validation is unavailable" },
        { status: 503 },
      );
    }
  } else {
    identityHeaders = {
      "x-desk-subject": subject!,
      "x-desk-auth-issuer": issuer,
      // Basic authentication cannot truthfully assert MFA.
      "x-desk-auth-assurance": "password",
    };
  }

  try {
    const upstream = await fetch(new URL(path, baseUrl), {
      ...init,
      cache: "no-store",
      signal: AbortSignal.timeout(10_000),
      headers: {
        accept: "application/json",
        "content-type": "application/json",
        ...init.headers,
        "x-desk-token": token,
        ...identityHeaders,
      },
    });
    return new Response(await upstream.arrayBuffer(), {
      status: upstream.status,
      headers: {
        "content-type": upstream.headers.get("content-type") ?? "application/json",
        ...(upstream.headers.get("content-disposition")
          ? { "content-disposition": upstream.headers.get("content-disposition")! }
          : {}),
        ...(upstream.headers.get("cache-control")
          ? { "cache-control": upstream.headers.get("cache-control")! }
          : {}),
      },
    });
  } catch {
    return Response.json({ detail: "Desk API is unavailable" }, { status: 502 });
  }
}
