'''Builds the deterministic candidate evidence catalog.'''

import hashlib
from typing import Dict, List, Optional

from resume_assistant import models
from resume_assistant.models import (
    CandidateEvidenceCatalog,
    DocumentSnapshot,
    EvidenceRecord,
    EvidenceType,
    ExtractionCompleteness,
    SourceDocumentInfo,
)
from resume_assistant.section_detector import SectionDetector

CONTACT_PREFIXES = (
    'phone',
    'mobile',
    'email',
    'e-mail',
    'location',
    'address',
    'linkedin',
    'github',
    'portfolio',
    'website',
    'personal website',
)


def _evidence_type_for_section(identifier: Optional[str]) -> EvidenceType:
    if identifier is None:
        return EvidenceType.UNCLASSIFIED
    return {
        'professional-identity': EvidenceType.PROFESSIONAL_SUMMARY,
        'technical-profile': EvidenceType.TECHNICAL_SKILL,
        'education': EvidenceType.EDUCATION,
        'professional-experience-and-engineering-training': EvidenceType.EXPERIENCE,
        'independent-software-and-ai-projects': EvidenceType.PROJECT,
        'additional-ai-and-multimodal-applications': EvidenceType.PROJECT,
        'alx-software-engineering-project-archive': EvidenceType.PROJECT,
        'technical-skills-database': EvidenceType.TECHNICAL_SKILL,
        'certifications-and-professional-development': EvidenceType.CERTIFICATION,
        'leadership-and-demonstrated-competencies': EvidenceType.LEADERSHIP,
        'achievement-and-metrics-database': EvidenceType.METRIC,
        'engineering-decisions-and-interview-evidence': EvidenceType.INTERVIEW_EVIDENCE,
        'technical-documentation-and-public-project-showcases': EvidenceType.SHOWCASE,
        'languages': EvidenceType.LANGUAGE,
    }.get(identifier, EvidenceType.UNCLASSIFIED)


def _evidence_type_for_text(text: str) -> EvidenceType:
    lowered = (text or '').strip().lower()
    for prefix in CONTACT_PREFIXES:
        if lowered.startswith(prefix):
            return EvidenceType.CONTACT
    return EvidenceType.UNCLASSIFIED


def _source_location_for_block(block) -> Optional[models.SourceLocation]:
    location = models.SourceLocation()
    location.document_part = 'word/document.xml'
    location.body_block_index = block.index
    paragraph = block.paragraph
    if paragraph is not None:
        location.paragraph_index = paragraph.index
        location.paragraph_position_in_cell = paragraph.index
        if paragraph.hyperlink_count:
            pass
    if block.table is not None:
        location.table_index = block.table.index
    return location


def build_evidence_catalog(
    snapshot: DocumentSnapshot,
    sections=None,
) -> CandidateEvidenceCatalog:
    if sections is None:
        sections = []
    detector = SectionDetector()
    detected_sections = list(sections) if sections else detector.detect(snapshot)
    unclassified = detector.find_unclassified(snapshot)
    records: List[EvidenceRecord] = []
    section_identifiers: List[str] = []
    seen_evidence_ids = set()

    for block in snapshot.blocks:
        paragraph = block.paragraph
        if paragraph is None or not (paragraph.text or '').strip():
            continue
        if paragraph.hyperlink_count and not paragraph.hyperlinks:
            paragraph.hyperlinks = []
        section_reference = None
        for section in detected_sections:
            if (
                section.start_block_index is not None
                and section.end_block_index is not None
                and section.start_block_index <= block.index <= section.end_block_index
            ):
                section_reference = section.identifier
                break
        evidence_type = _evidence_type_for_section(section_reference)
        if section_reference is None:
            evidence_type = _evidence_type_for_text(paragraph.text)
        location = _source_location_for_block(block)
        digest_input = f"{snapshot.source_sha256}|{block.index}|{paragraph.index}"
        evidence_id = hashlib.sha256(digest_input.encode('utf-8')).hexdigest()
        if evidence_id in seen_evidence_ids:
            continue
        seen_evidence_ids.add(evidence_id)
        if section_reference is not None and section_reference not in section_identifiers:
            section_identifiers.append(section_reference)
        records.append(
            EvidenceRecord(
                evidence_id=evidence_id,
                evidence_type=evidence_type,
                original_content=paragraph.text,
                normalized_content=None,
                section_reference=section_reference,
                source_document=snapshot.source_filename,
                source_sha256=snapshot.source_sha256,
                source_location=location,
                formatting={
                    'style': paragraph.style,
                    'bold': paragraph.bold,
                    'italic': paragraph.italic,
                    'font_size_pt': paragraph.font_size_pt,
                    'alignment': paragraph.alignment,
                    'bullet': paragraph.bullet,
                    'numbering_format': paragraph.numbering_format,
                    'numbering_level': paragraph.numbering_level,
                    'hyperlink_count': paragraph.hyperlink_count,
                },
                hyperlinks=list(paragraph.hyperlinks),
            )
        )

    completeness = ExtractionCompleteness(
        complete=not unclassified,
        sections_detected=len(detected_sections),
        evidence_records=len(records),
        unclassified_block_indexes=list(unclassified),
        warnings=[f'Unclassified block indexes: {list(unclassified)}'] if unclassified else [],
    )
    return CandidateEvidenceCatalog(
        schema_version=models.SCHEMA_VERSION,
        source_document=SourceDocumentInfo(
            filename=snapshot.source_filename,
            sha256=snapshot.source_sha256,
            statistics=snapshot.statistics,
        ),
        evidence_records=records,
        section_references=section_identifiers,
        extraction_completeness=completeness,
    )

