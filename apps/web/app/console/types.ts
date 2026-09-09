export type Corridor =
  | "hormuz_gulf"
  | "red_sea_bem_suez"
  | "east_med"
  | "port_specific";

export type EventType =
  | "security_incident"
  | "port_disruption"
  | "labor_action"
  | "regulatory_sanctions"
  | "weather_hazard"
  | "infrastructure"
  | "insurance_market"
  | "navigation_warning";

export type TriageItem = {
  id: string;
  status: "pending" | "attached" | "new_event" | "dismissed";
  source_record_id: string;
  source_id: string;
  source_name: string;
  source_tier: "A" | "B" | "C" | "D" | "E";
  source_language: string;
  rights_basis: string;
  url: string;
  title: string | null;
  text: string;
  published_at: string | null;
  fetched_at: string;
  security_scan: Record<string, unknown>;
  detected_corridors: Corridor[];
  detected_ports: {
    name: string;
    unlocode: string;
    corridor: Corridor;
    matched_alias: string;
  }[];
  suggested_event_types: EventType[];
  assigned_event_id: string | null;
  created_at: string;
};

export type OpenEvent = {
  id: string;
  slug: string;
  title: string;
  event_type: EventType;
  corridor: Corridor;
  severity: number;
  status: string;
};

export type DeskPrincipal = {
  id: string;
  auth_issuer: string;
  auth_subject: string;
  email: string;
  display_name: string;
  role: "analyst" | "senior_analyst" | "administrator" | "security" | "compliance" | "service";
  auth_assurance: "password" | "mfa" | "phishing_resistant" | "service";
};

export type ApprovalRequest = {
  id: string;
  request_type: "event_publication" | "operational_correction";
  target_id: string;
  request_hash: string;
  request_payload: {
    draft?: Record<string, unknown>;
    preapproval_preview?: Record<string, unknown>;
  };
  primary_user_id: string;
  primary_display_name: string;
  status: "pending" | "approved" | "rejected" | "cancelled" | "expired";
  request_reason: string;
  requested_at: string;
  expires_at: string;
  decided_at: string | null;
  decided_by_user_id: string | null;
  decided_by_display_name: string | null;
  decision_reason: string | null;
  approval_id: string | null;
  release_hash: string | null;
  release_expires_at: string | null;
  approved_payload: Record<string, unknown> | null;
};

export type AICapabilityControl = {
  id: string;
  scope: string;
  revision: number;
  mode: "disabled" | "shadow" | "assisted" | "automated";
  risk_tier: number;
  system_version_id: string | null;
  reason: string;
  approval_refs: Record<string, string>;
  changed_by_user_id: string;
  auth_assurance: DeskPrincipal["auth_assurance"];
  previous_control_id: string | null;
  expires_at: string | null;
  changed_at: string;
};

export type AISystemVersion = {
  id: string;
  name: string;
  purpose: string;
  manifest_schema_version: string;
  manifest: Record<string, unknown>;
  fingerprint: string;
  created_by_user_id: string;
  created_at: string;
};

export type AIIncidentEvent = {
  id: string;
  incident_id: string;
  sequence: number;
  status: "open" | "contained" | "investigating" | "resolved";
  severity: "low" | "medium" | "high" | "critical";
  summary: string;
  containment_action: string | null;
  evidence_refs: string[];
  changed_by_user_id: string;
  auth_assurance: DeskPrincipal["auth_assurance"];
  at: string;
};

export type AIIncident = {
  id: string;
  title: string;
  capability_scope: string;
  system_version_id: string | null;
  detected_at: string;
  reported_by_user_id: string;
  created_at: string;
  status: AIIncidentEvent["status"];
  severity: AIIncidentEvent["severity"];
  events: AIIncidentEvent[];
};

export type OperationsDashboard = {
  generated_at: string;
  environment: string;
  overall_status: "ready" | "degraded" | "action_required";
  outbound_enabled: boolean;
  desk_auth_mode: "trusted_proxy" | "oidc";
  pending_triage: number;
  open_events: number;
  high_severity_events: number;
  pending_extraction_proposals: number;
  open_desk_alerts: number;
  pending_approval_requests: number;
  approved_releases_expiring: number;
  open_ai_incidents: number;
  active_sources: number;
  total_sources: number;
  source_health: Record<string, number>;
  delivery_status: Record<string, number>;
  ai_controls: AICapabilityControl[];
  readiness: {
    key: string;
    label: string;
    state: "pass" | "warning" | "fail";
    detail: string;
    href: string | null;
  }[];
};

export type ClaimState =
  | "confirmed"
  | "reported"
  | "unverified"
  | "corroborated_2_independent"
  | "single_source_official"
  | "disputed";

export type ClaimExtractionProposal = {
  id: string;
  run_id: string;
  source_record_id: string;
  source_name: string;
  source_language: string;
  source_title: string | null;
  source_text: string;
  source_url: string;
  model_version: string;
  prompt_version: string;
  mode: "shadow" | "production";
  proposal_index: number;
  text: string;
  claimant: string | null;
  occurred_time: string | null;
  location: { name?: string; unlocode?: string | null } | null;
  quantities: { value: string; unit: string; what: string }[];
  hedging_language: boolean;
  source_sentence_quote: string;
  status: "pending" | "accepted" | "edited" | "rejected";
};

export type ClaimExtractionReview = {
  id: string;
  proposal_id: string;
  decision: "accept" | "edit" | "reject";
  event_id: string | null;
  reviewer: string;
  claim_state: ClaimState | null;
  final_claim_json: Record<string, unknown> | null;
  reason_codes: string[];
  note: string | null;
  baseline_seconds: number;
  review_seconds: number;
  mode: string;
  resulting_claim_id: string | null;
  reviewed_at: string;
};

export type ClaimExtractionWeeklyReport = {
  prompt_version: string;
  reviewed: number;
  reason_codes: Record<string, number>;
  decisions: Record<string, number>;
  languages: Record<string, number>;
};

export type ClaimExtractionEvaluation = {
  language: string;
  prompt_version: string;
  model_version: string;
  reviewed: number;
  qa_reviewed: number;
  shadow_days: number;
  time_reduction_percent: number;
  error_rate: number;
  baseline_error_rate: number | null;
  graduated: boolean;
};

export type WorkspaceEvidence = {
  id: string;
  source_record_id: string;
  source_name: string;
  source_tier: "A" | "B" | "C" | "D" | "E";
  url: string;
  directness: "primary" | "secondary";
  lineage_root_id: string;
  excerpt: string | null;
  rights_basis: string;
};

export type WorkspaceClaim = {
  id: string;
  text: string;
  claimant: string | null;
  claim_state: ClaimState;
  occurred_at: string | null;
  proposed_by: string;
  reviewed_by: string | null;
  second_reviewed_by: string | null;
  reviewed_at: string | null;
  sensitivity_flags: string[];
  first_seen_at: string;
  evidence: WorkspaceEvidence[];
};

export type WorkspaceSource = {
  id: string;
  source_name: string;
  source_tier: "A" | "B" | "C" | "D" | "E";
  rights_basis: string;
  url: string;
  title: string | null;
  text: string;
  published_at: string | null;
  fetched_at: string;
  lineage_root_id: string | null;
  linked_claim_ids: string[];
};

export type PublicationSection = "confirmed" | "reported" | "unknown" | "changed";

export type PublicationSentence = {
  section: PublicationSection;
  text: string;
  claim_ids: string[];
};

export type WorkspaceVersion = {
  id: string;
  version_no: number;
  title: string;
  sentences: PublicationSentence[];
  published_at: string;
  published_by: string;
  signed_off_by: string | null;
  policy_version: string;
  model_versions: Record<string, string>;
  content_hash: string;
};

export type EventWorkspaceData = {
  id: string;
  slug: string;
  event_type: EventType;
  corridor: Corridor;
  status: string;
  severity: number;
  occurred_start: string | null;
  occurred_end: string | null;
  created_at: string;
  title: string;
  claims: WorkspaceClaim[];
  sources: WorkspaceSource[];
  versions: WorkspaceVersion[];
};

export type PublicationGateCheck = {
  key: string;
  label: string;
  passed: boolean;
  detail: string;
};

export type PublicationPreview = {
  previous_version_no: number | null;
  next_version_no: number;
  preview_hash: string;
  ready: boolean;
  checks: PublicationGateCheck[];
  diff: Record<
    "title" | PublicationSection,
    { before: string; after: string; changed: boolean }
  >;
};

export type AccountTier = "watch" | "desk" | "desk_pro" | "data";

export type AccountUser = {
  id: string;
  email: string;
  phone: string | null;
  channels: Record<string, unknown>;
  role: string;
  auth_subject: string | null;
  active: boolean;
  portal_enabled: boolean;
};

export type Account = {
  id: string;
  company: string;
  tier: AccountTier;
  contract_start: string;
  contract_end: string;
  users: AccountUser[];
};

export type ApiScope = "events:read" | "versions:read" | "claims:read" | "calendar:read" | "ais:read";

export type AccountApiKey = {
  id: string;
  account_id: string;
  name: string;
  key_prefix: string;
  scopes_json: ApiScope[];
  created_at: string;
  created_by: string;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  revoked_by: string | null;
};

export type IssuedAccountApiKey = AccountApiKey & { api_key: string };

export type WatchProfile = {
  id: string;
  account_id: string;
  account_company: string;
  account_tier: AccountTier;
  name: string;
  corridors: Corridor[];
  event_types: EventType[];
  min_severity: number;
  ports: string[];
  custom_geojson: Record<string, unknown> | null;
  active: boolean;
  configured_by: string | null;
  configured_with_customer_at: string | null;
  created_at: string;
  updated_at: string;
};

export type DeliveryChannel = "email" | "telegram" | "whatsapp";

export type AlertAudienceAccount = {
  account_id: string;
  company: string;
  tier: AccountTier;
  matched_profile_ids: string[];
  user_count: number;
  delivery_count: number;
  alerts_today: number;
  daily_cap: number;
  throttled: boolean;
  reason: string | null;
};

export type AlertPreview = {
  event_version_id: string;
  event_id: string;
  title: string;
  severity: number;
  rules_version: string;
  matched_profiles: number;
  account_count: number;
  user_count: number;
  planned_deliveries: Record<string, number>;
  accounts: AlertAudienceAccount[];
  blockers: string[];
  ready: boolean;
  preview_hash: string;
};

export type DeliveryRecent = {
  id: string;
  alert_id: string;
  event_version_id: string;
  event_title: string;
  account_company: string;
  recipient: string;
  channel: DeliveryChannel;
  status: "queued" | "sent" | "delivered" | "failed";
  queued_at: string;
  sent_at: string | null;
  delivered_at: string | null;
  attempt_count: number;
  last_error: string | null;
};

export type DeliveryDashboard = {
  generated_at: string;
  total: number;
  status_counts: Record<string, number>;
  channel_counts: Record<string, number>;
  publish_to_delivery_p95_seconds: number | null;
  within_60_seconds_percent: number | null;
  recent: DeliveryRecent[];
};

export type CorrectionType = "update" | "clarification" | "correction";
export type CorrectionImpact = "non_operational" | "operationally_relevant";

export type CorrectionPreview = {
  preview_hash: string;
  event_id: string;
  event_slug: string;
  version_from_id: string;
  version_to_id: string;
  version_from: number;
  version_to: number;
  affected_version_hash: string;
  corrected_version_hash: string;
  channels: DeliveryChannel[];
  recipient_count: number;
  message: Record<string, unknown>;
};

export type PublicCorrection = {
  id: string;
  event_id: string;
  event_slug: string;
  correction_type: CorrectionType;
  note: string;
  affected_version_hash: string;
  corrected_version_hash: string;
  issued_at: string;
  issued_within_60_minutes: boolean;
};

export type QualityScoreboard = {
  generated_at: string;
  published_version_count: number;
  correction_count: number;
  correction_rate_percent: number;
  corrections_within_60_minutes_percent: number | null;
  ttv_coverage_count: number;
  holding_line_median_minutes: number | null;
  verified_update_median_minutes: number | null;
  holding_line_within_15_minutes_percent: number | null;
  verified_update_within_45_minutes_percent: number | null;
  corrections: PublicCorrection[];
};

export type StateKnowledgeReport = {
  id: string;
  event_id: string;
  event_version_id: string;
  account_id: string | null;
  requested_timestamp: string;
  requested_by: string;
  generated_at: string;
  version_content_hash: string;
  content_hash: string;
};
