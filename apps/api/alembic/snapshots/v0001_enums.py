from enum import StrEnum


class SourceTier(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"


class SourceType(StrEnum):
    OFFICIAL = "official"
    CLUB = "club"
    PRESS = "press"
    UNION = "union"
    AGENT = "agent"
    OSINT = "osint"
    COMPETITOR = "competitor"
    OTHER = "other"


class AccessMethod(StrEnum):
    RSS = "rss"
    WATCH = "watch"
    EMAIL = "email"
    MANUAL = "manual"
    TELEGRAM = "telegram"


class RightsBasis(StrEnum):
    PUBLIC_ADVISORY = "public-advisory"
    LICENSED = "licensed"
    ATTRIBUTE_QUOTE_MIN = "attribute-quote-min"
    VERIFY_ONLY_NO_REPUBLISH = "verify-only-no-republish"
    DISCOVERY_ONLY = "discovery-only"


class EventType(StrEnum):
    SECURITY_INCIDENT = "security_incident"
    PORT_DISRUPTION = "port_disruption"
    LABOR_ACTION = "labor_action"
    REGULATORY_SANCTIONS = "regulatory_sanctions"
    WEATHER_HAZARD = "weather_hazard"
    INFRASTRUCTURE = "infrastructure"
    INSURANCE_MARKET = "insurance_market"
    NAVIGATION_WARNING = "navigation_warning"


class Corridor(StrEnum):
    HORMUZ_GULF = "hormuz_gulf"
    RED_SEA_BEM_SUEZ = "red_sea_bem_suez"
    EAST_MED = "east_med"
    PORT_SPECIFIC = "port_specific"


class EventStatus(StrEnum):
    MONITORING = "monitoring"
    DEVELOPING = "developing"
    STABILIZED = "stabilized"
    CLOSED = "closed"


class ClaimState(StrEnum):
    CONFIRMED = "confirmed"
    REPORTED = "reported"
    UNVERIFIED = "unverified"
    CORROBORATED_2X = "corroborated_2_independent"
    SINGLE_OFFICIAL = "single_source_official"
    DISPUTED = "disputed"


class Directness(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"


class ClaimRelationType(StrEnum):
    CORROBORATES = "corroborates"
    CONTRADICTS = "contradicts"
    SUPERSEDES = "supersedes"


class CorrectionType(StrEnum):
    UPDATE = "update"
    CLARIFICATION = "clarification"
    CORRECTION = "correction"


class AccountTier(StrEnum):
    WATCH = "watch"
    DESK = "desk"
    DESK_PRO = "desk_pro"
    DATA = "data"


class DeliveryChannel(StrEnum):
    EMAIL = "email"
    TELEGRAM = "telegram"
    WHATSAPP = "whatsapp"
    PORTAL = "portal"


class DeliveryStatus(StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"


class PollerRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    NOT_MODIFIED = "not_modified"
    FAILED = "failed"
    SKIPPED = "skipped"


class DeskAlertKind(StrEnum):
    DEAD_POLLER = "dead_poller"
    INGEST_FAILURE = "ingest_failure"
    SOURCE_QUARANTINE = "source_quarantine"


class DeskAlertStatus(StrEnum):
    OPEN = "open"
    NOTIFIED = "notified"
    RESOLVED = "resolved"


class LineageProposalStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class LineageReviewDecision(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"


class TriageStatus(StrEnum):
    PENDING = "pending"
    ATTACHED = "attached"
    NEW_EVENT = "new_event"
    DISMISSED = "dismissed"


class TriageAction(StrEnum):
    ATTACH = "attach"
    NEW_EVENT = "new_event"
    DISMISS = "dismiss"
