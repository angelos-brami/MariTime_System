from __future__ import annotations

import hashlib
import html
import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID

from eastmed_schema.models import (
    AuditLog,
    Event,
    EventVersion,
    Source,
    SourceRecord,
    StateKnowledgeReport,
)
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import select
from sqlalchemy.orm import Session


class ReportWorkflowError(ValueError):
    pass


def _human(value: str) -> str:
    actor = value.strip()
    if not 2 <= len(actor) <= 255 or actor.casefold().startswith("model:"):
        raise ReportWorkflowError("a named human must request the report")
    return actor


def _canonical_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _source_citations(session: Session, version: EventVersion) -> list[dict[str, Any]]:
    evidence = version.evidence_snapshot_json
    record_ids = {
        UUID(str(item["source_record_id"])) for item in evidence if item.get("source_record_id")
    }
    rows = session.execute(
        select(SourceRecord, Source)
        .join(Source, Source.id == SourceRecord.source_id)
        .where(SourceRecord.id.in_(record_ids))
    ).all()
    by_id = {record.id: (record, source) for record, source in rows}
    citations: list[dict[str, Any]] = []
    for item in evidence:
        record_id = UUID(str(item["source_record_id"]))
        row = by_id.get(record_id)
        if row is None:
            continue
        record, source = row
        rights = item.get("rights_decision") or {}
        may_quote = bool(isinstance(rights, dict) and rights.get("may_publish_excerpt"))
        citations.append(
            {
                "evidence_id": str(item["id"]),
                "claim_id": str(item["claim_id"]),
                "source_record_id": str(record.id),
                "source_name": source.name,
                "source_tier": source.tier.value,
                "url": record.url,
                "published_at": record.published_at.isoformat() if record.published_at else None,
                "fetched_at": record.fetched_at.isoformat(),
                "directness": item.get("directness"),
                "lineage_root_id": item.get("lineage_root_id"),
                "excerpt": item.get("excerpt") if may_quote else None,
                "rights_basis": rights.get("basis") if isinstance(rights, dict) else None,
            }
        )
    return sorted(citations, key=lambda item: (item["source_tier"], item["source_name"]))


def create_state_knowledge_report(
    session: Session,
    *,
    event_id: UUID,
    requested_timestamp: datetime,
    requested_by: str,
    account_id: UUID | None = None,
    now: datetime | None = None,
) -> StateKnowledgeReport:
    actor = _human(requested_by)
    if requested_timestamp.tzinfo is None or requested_timestamp.utcoffset() is None:
        raise ReportWorkflowError("requested timestamp must include a timezone")
    generated_at = now or datetime.now(UTC)
    if requested_timestamp > generated_at:
        raise ReportWorkflowError("state-of-knowledge timestamp cannot be in the future")
    event = session.get(Event, event_id)
    if event is None:
        raise LookupError("Event not found")
    version = session.scalar(
        select(EventVersion)
        .where(
            EventVersion.event_id == event_id,
            EventVersion.published_at <= requested_timestamp,
        )
        .order_by(EventVersion.published_at.desc(), EventVersion.version_no.desc())
        .limit(1)
    )
    if version is None:
        raise ReportWorkflowError("no published event version existed at that timestamp")
    snapshot: dict[str, Any] = {
        "event": version.event_snapshot_json,
        "requested_timestamp": requested_timestamp.isoformat(),
        "version": {
            "id": str(version.id),
            "number": version.version_no,
            "title": version.title,
            "published_at": version.published_at.isoformat(),
            "published_by": version.published_by,
            "signed_off_by": version.signed_off_by,
            "policy_version": version.policy_version,
            "content_hash": version.content_hash,
        },
        "sections": {
            "confirmed": version.summary_confirmed,
            "reported": version.summary_reported,
            "unknown": version.summary_unknown,
            "changed": version.whats_changed,
        },
        "claims": version.claim_snapshot_json,
        "evidence": _source_citations(session, version),
        "sentence_claim_map": version.sentence_claim_map,
    }
    report = StateKnowledgeReport(
        event_id=event_id,
        event_version_id=version.id,
        account_id=account_id,
        requested_timestamp=requested_timestamp,
        requested_by=actor,
        generated_at=generated_at,
        version_content_hash=version.content_hash,
        snapshot_json=snapshot,
        content_hash=_canonical_hash(snapshot),
    )
    session.add(report)
    session.flush()
    session.add(
        AuditLog(
            actor=actor,
            action="state_knowledge_report.generated",
            entity="state_knowledge_report",
            entity_id=report.id,
            payload_json={
                "event_id": str(event_id),
                "event_version_id": str(version.id),
                "requested_timestamp": requested_timestamp.isoformat(),
                "version_content_hash": version.content_hash,
                "report_content_hash": report.content_hash,
                "account_id": str(account_id) if account_id else None,
            },
        )
    )
    session.commit()
    session.refresh(report)
    return report


def _font_name() -> str:
    candidates = (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    )
    for path in candidates:
        if path.is_file():
            name = "EastMedUnicode"
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(path)))
            return name
    return "Helvetica"


def render_state_knowledge_pdf(report: StateKnowledgeReport) -> bytes:
    snapshot = report.snapshot_json
    version = snapshot["version"]
    event = snapshot["event"]
    sections = snapshot["sections"]
    font = _font_name()
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"State of knowledge - {version['title']}",
        author="East Med Maritime Event Intelligence",
        subject=f"Immutable event version {version['content_hash']}",
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontName=font,
        fontSize=22,
        leading=26,
        textColor=colors.HexColor("#12372d"),
        spaceAfter=10,
    )
    heading = ParagraphStyle(
        "ReportHeading",
        parent=styles["Heading2"],
        fontName=font,
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#12372d"),
        spaceBefore=12,
        spaceAfter=6,
    )
    body = ParagraphStyle(
        "ReportBody",
        parent=styles["BodyText"],
        fontName=font,
        fontSize=9.5,
        leading=14,
        textColor=colors.HexColor("#253b34"),
        spaceAfter=6,
    )
    small = ParagraphStyle(
        "ReportSmall",
        parent=body,
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor("#53675f"),
        wordWrap="CJK",
    )
    centered = ParagraphStyle("ReportFooter", parent=small, alignment=TA_CENTER)

    def escaped(value: object) -> str:
        return html.escape(str(value or "")).replace("\n", "<br/>")

    story: list[Any] = [
        Paragraph("STATE OF KNOWLEDGE", small),
        Paragraph(escaped(version["title"]), title),
        Paragraph(
            "This report reproduces the desk's published state at the requested timestamp. "
            "It is information support, not navigational advice.",
            body,
        ),
        Spacer(1, 4 * mm),
    ]
    metadata = [
        ["Requested time", snapshot["requested_timestamp"]],
        ["Selected version", f"v{version['number']} / {version['id']}"],
        ["Published", version["published_at"]],
        ["Event status", event.get("status")],
        ["Corridor", event.get("corridor")],
        ["Version SHA-256", version["content_hash"]],
        ["Report snapshot SHA-256", report.content_hash],
    ]
    table = Table(
        [
            [Paragraph(escaped(left), small), Paragraph(escaped(right), small)]
            for left, right in metadata
        ],
        colWidths=[42 * mm, 124 * mm],
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#e5eadf")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#aab7af")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c5cec8")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(table)
    for key, label in (
        ("confirmed", "Confirmed"),
        ("reported", "Reported"),
        ("unknown", "Unverified / unknown"),
        ("changed", "What had changed"),
    ):
        story.append(Paragraph(label, heading))
        story.append(Paragraph(escaped(sections.get(key) or "No published items."), body))

    story.extend([PageBreak(), Paragraph("Evidence citations", title)])
    evidence = snapshot.get("evidence") or []
    if not evidence:
        story.append(Paragraph("No selected evidence citations in this version.", body))
    for index, item in enumerate(evidence, 1):
        story.append(
            Paragraph(
                f"{index}. {escaped(item['source_name'])} (Tier {escaped(item['source_tier'])})",
                heading,
            )
        )
        story.append(Paragraph(escaped(item["url"]), small))
        story.append(
            Paragraph(
                "Evidence ID: "
                f"{escaped(item['evidence_id'])}<br/>Lineage root: "
                f"{escaped(item['lineage_root_id'])}<br/>Directness: "
                f"{escaped(item['directness'])}",
                small,
            )
        )
        if item.get("excerpt"):
            story.append(Paragraph(f"Excerpt: {escaped(item['excerpt'])}", body))
    story.extend(
        [
            Spacer(1, 8 * mm),
            Paragraph(
                f"Generated {escaped(report.generated_at.isoformat())} / Report {report.id}",
                centered,
            ),
        ]
    )

    def footer(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont(font, 7)
        canvas.setFillColor(colors.HexColor("#61736c"))
        canvas.drawString(18 * mm, 10 * mm, f"Version hash {version['content_hash'][:16]}...")
        canvas.drawRightString(192 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
