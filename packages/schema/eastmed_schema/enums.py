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


class CorrectionImpact(StrEnum):
    NON_OPERATIONAL = "non_operational"
    OPERATIONALLY_RELEVANT = "operationally_relevant"


class DrillType(StrEnum):
    RESTORE = "restore"
    KILL_SWITCH = "kill_switch"
    INJECTION = "injection"
    LOAD = "load"


class DrillStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


class CalendarEventType(StrEnum):
    STRIKE = "strike"
    PORT_CLOSURE = "port_closure"
    NAVAL_EXERCISE = "naval_exercise"
    WEATHER_WINDOW = "weather_window"
    REGULATORY_DEADLINE = "regulatory_deadline"
    OTHER = "other"


class AccountTier(StrEnum):
    WATCH = "watch"
    DESK = "desk"
    DESK_PRO = "desk_pro"
    DATA = "data"


class BriefStatus(StrEnum):
    DRAFT = "draft"
    FINALIZED = "finalized"


class ClaimExtractionRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class ClaimExtractionProposalStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    EDITED = "edited"
    REJECTED = "rejected"


class ClaimExtractionDecision(StrEnum):
    ACCEPT = "accept"
    EDIT = "edit"
    REJECT = "reject"


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


class DeskRole(StrEnum):
    ANALYST = "analyst"
    SENIOR_ANALYST = "senior_analyst"
    ADMINISTRATOR = "administrator"
    SECURITY = "security"
    COMPLIANCE = "compliance"
    SERVICE = "service"


class AuthAssurance(StrEnum):
    PASSWORD = "password"  # noqa: S105 - authentication method, not a credential
    MFA = "mfa"
    PHISHING_RESISTANT = "phishing_resistant"
    SERVICE = "service"


class AICapabilityMode(StrEnum):
    DISABLED = "disabled"
    SHADOW = "shadow"
    ASSISTED = "assisted"
    AUTOMATED = "automated"


# --- Private fleet reconciliation (blueprint work orders 1-2) --------------------
# These back both the fleet_reconciliation_v1 data contract and the recon_* tables.
# They live here because eastmed_shared depends on eastmed_schema (not the reverse),
# so the durable models and the neutral contract can share one definition.


class RecordKind(StrEnum):
    FLEET_MEMBERSHIP = "fleet_membership"
    VOYAGE_PLAN = "voyage_plan"
    DAILY_REPORT = "daily_report"


class FuelGrade(StrEnum):
    HSFO = "HSFO"
    VLSFO = "VLSFO"
    ULSFO = "ULSFO"
    LSMGO = "LSMGO"
    MGO = "MGO"
    MDO = "MDO"
    LNG = "LNG"
    LPG = "LPG"
    METHANOL = "METHANOL"
    BIOFUEL = "BIOFUEL"


class QuantityUnit(StrEnum):
    TONNE = "tonne"
    KILOGRAM = "kilogram"
    CUBIC_METRE = "m3"
    LITRE = "litre"


class AuthorityRole(StrEnum):
    OWNER = "owner"
    OPERATOR = "operator"
    TECHNICAL_MANAGER = "technical_manager"
    CHARTERER = "charterer"


class ValueOrigin(StrEnum):
    REPORTED = "reported"
    CALCULATED = "calculated"
    ASSUMED = "assumed"


class BusinessStatus(StrEnum):
    PENDING = "pending"
    RECONCILED = "reconciled"
    VARIANCE_PRESENT = "variance_present"
    UNRESOLVED = "unresolved"
    SUPERSEDED = "superseded"


class AutomationStatus(StrEnum):
    AWAITING_ARRIVAL = "awaiting_arrival"
    RECEIVED = "received"
    VALIDATED = "validated"
    RECONCILED = "reconciled"
    CALCULATED = "calculated"
    PUBLICATION_READY = "publication_ready"
    PUBLISHED = "published"
    VERIFIED_COMPLETE = "verified_complete"
    WAITING_FOR_MACHINE_DATA = "waiting_for_machine_data"
    RECOVERING = "recovering"
    UNRESOLVED = "unresolved"
    DISABLED = "disabled"


class JobOutcome(StrEnum):
    CORRECT_ON_TIME = "correct_on_time"
    CORRECT_LATE = "correct_late"
    INCORRECT = "incorrect"
    UNRESOLVED = "unresolved"
    MISSING = "missing"
    UNSUPPORTED = "unsupported"
    PENDING_BEFORE_DEADLINE = "pending_before_deadline"


# --- Durable orchestration (blueprint work order 5, section 21) ------------------


class WorkflowStage(StrEnum):
    RECONCILE = "reconcile"
    CALCULATE = "calculate"
    VERIFY = "verify"
    EXPLAIN = "explain"
    PUBLISH = "publish"


class TaskStatus(StrEnum):
    READY = "ready"
    LEASED = "leased"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"


class AttemptStatus(StrEnum):
    RUNNING = "running"
    COMMITTED = "committed"
    FENCED = "fenced"
    FAILED = "failed"


class ReservationStatus(StrEnum):
    RESERVED = "reserved"
    SETTLED = "settled"
    RELEASED = "released"
    UNCERTAIN = "uncertain"


class AttestationDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


# --- Private publication (blueprint work order 7, section 23) --------------------
# The outbox distinguishes a proposed action, a dispatch attempt, provider acceptance
# and verified business completion. For the first release the only destination is the
# tenant's private portal, reconciled by transaction commit plus content-hash read-back;
# external response classes (DELIVERED) are a preserved later-module design.


class OutboxStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    DISPATCHED = "dispatched"
    RECONCILED = "reconciled"
    INVALIDATED = "invalidated"
    EFFECT_UNKNOWN = "effect_unknown"


class OutboxResponseClass(StrEnum):
    ACCEPTED = "accepted"
    DELIVERED = "delivered"
    EFFECT_CONFIRMED = "effect_confirmed"
