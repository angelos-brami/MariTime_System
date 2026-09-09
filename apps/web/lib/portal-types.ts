import type { Corridor, EventType } from "@/app/console/types";

export type PortalBoardEvent = {
  event_id: string;
  slug: string;
  title: string;
  event_type: EventType;
  corridor: Corridor;
  status: string;
  severity: number;
  ports: { name: string; unlocode: string }[];
  version_no: number;
  published_at: string;
  whats_changed: string;
};

export type PortalCorridor = {
  corridor: Corridor;
  label: string;
  operational_state: "clear" | "watch" | "disrupted" | "critical";
  event_count: number;
  updated_at: string | null;
  events: PortalBoardEvent[];
};

export type PortalBoard = {
  generated_at: string;
  corridors: PortalCorridor[];
};

export type PortalArchiveResult = PortalBoardEvent & {
  summary_confirmed: string;
  rank: number | null;
};

export type PortalArchive = {
  query: string | null;
  total: number;
  limit: number;
  offset: number;
  results: PortalArchiveResult[];
};

export type PortalEvent = {
  event_id: string;
  slug: string;
  title: string;
  event_type: EventType;
  corridor: Corridor;
  status: string;
  severity: number;
  ports: { name: string; unlocode: string }[];
  occurred_start: string | null;
  occurred_end: string | null;
  version_no: number;
  latest_version_id: string;
  published_at: string;
  content_hash: string;
  summary_confirmed: string;
  summary_reported: string;
  summary_unknown: string;
  whats_changed: string;
  timeline: {
    version_no: number;
    published_at: string;
    whats_changed: string;
    content_hash: string;
  }[];
  sources: {
    source_record_id: string;
    source_name: string;
    source_tier: "A" | "B" | "C" | "D" | "E";
    url: string;
    directness: "primary" | "secondary";
    lineage_root_id: string;
    excerpt: string | null;
    rights_basis: string;
  }[];
  corrections: {
    id: string;
    correction_type: "update" | "clarification" | "correction";
    note: string;
    version_from: number;
    version_to: number;
    affected_version_hash: string;
    corrected_version_hash: string;
    issued_at: string;
  }[];
};

export type DailyBriefItem = {
  event_id: string;
  event_slug: string;
  event_version_id: string;
  version_no: number;
  title: string;
  event_type: EventType;
  corridor: Corridor;
  status: string;
  severity: number;
  published_at: string;
  summary_confirmed: string;
  summary_reported: string;
  summary_unknown: string;
  whats_changed: string;
  content_hash: string;
};

export type DailyBrief = {
  id: string;
  brief_date: string;
  title: string;
  introduction: string;
  forward_watch: string;
  status: "draft" | "finalized";
  items: DailyBriefItem[];
  source_version_ids: string[];
  corrections: {
    id: string;
    event_id: string;
    event_slug: string;
    correction_type: "update" | "clarification" | "correction";
    note: string;
    version_from: number;
    version_to: number;
    affected_version_hash: string;
    corrected_version_hash: string;
    issued_at: string;
  }[];
  compiled_at: string;
  compiled_by: string;
  finalized_at: string | null;
  finalized_by: string | null;
  content_hash: string;
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
  corrections: {
    id: string;
    event_id: string;
    event_slug: string;
    correction_type: "update" | "clarification" | "correction";
    note: string;
    affected_version_hash: string;
    corrected_version_hash: string;
    issued_at: string;
    issued_within_60_minutes: boolean;
  }[];
};

export type MaritimeCalendar = {
  generated_at: string;
  results: {
    id: string;
    calendar_event_id: string;
    slug: string;
    version_no: number;
    event_type: "strike" | "port_closure" | "naval_exercise" | "weather_window" | "regulatory_deadline" | "other";
    title: string;
    corridor: Corridor;
    ports: { name: string; unlocode: string }[];
    starts_at: string;
    ends_at: string | null;
    status: string;
    public_note: string;
    source_record_ids: string[];
    published_at: string;
    published_by: string;
    content_hash: string;
  }[];
};

export type AISFeed = {
  generated_at: string;
  cache_status: "disabled" | "empty" | "stale" | "live";
  caveat: string;
  freshness_minutes: number;
  results: {
    id: string;
    mmsi: string;
    imo: string | null;
    vessel_name: string | null;
    latitude: number;
    longitude: number;
    course: number | null;
    speed: number | null;
    navigation_status: string | null;
    corridor: Corridor;
    message_at: string;
    received_at: string;
    age_minutes: number;
    stale: boolean;
    source: string;
    payload_hash: string;
  }[];
};
