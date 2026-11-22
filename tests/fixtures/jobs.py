'''Synthetic-only JD fixtures; literal payloads independently specify schema 2.0.0.'''

from resume_assistant.models import (
    CandidateEvidenceCatalog,
    EvidenceRecord,
    EvidenceType,
    ExtractionCompleteness,
    SourceDocumentInfo,
)


SYNTHETIC_JOB_TEXT = (
    'Senior Backend Engineer at Acme Corp\n'
    'Employment: Full-time.\n'
    'Work arrangement: Remote.\n'
    'Responsibilities: Design and maintain Python services.\n'
    'Required skills: Python and SQL.\n'
    'Preferred skills: Kubernetes.\n'
    'Education: Bachelor degree in Computer Science.\n'
    'Experience: 5 years of experience with Python.\n'
    'Languages: English (fluent).\n'
    'Certifications: AWS Certified Developer.\n'
    'Hours may vary.\n'
)

SCALAR_FIELDS = (
    'role_title', 'company_name', 'seniority_level', 'employment_type', 'work_arrangement',
)


def empty_job_payload():
    return {
        'role_title': None,
        'company_name': None,
        'seniority_level': None,
        'employment_type': None,
        'work_arrangement': None,
        'responsibilities': [],
        'required_skills': [],
        'preferred_skills': [],
        'uncertain_skills': [],
        'education_requirements': [],
        'experience_requirements': [],
        'languages': [],
        'certifications': [],
        'ambiguities': [],
        'missing_information': list(SCALAR_FIELDS),
    }


def grounded_job_payload():
    title_context = 'Senior Backend Engineer at Acme Corp'
    return {
        'role_title': {'value': 'Senior Backend Engineer', 'quotation': title_context},
        'company_name': {'value': 'Acme Corp', 'quotation': title_context},
        'seniority_level': {'value': 'Senior', 'quotation': title_context},
        'employment_type': {'value': 'Full-time', 'quotation': 'Employment: Full-time.'},
        'work_arrangement': {'value': 'Remote', 'quotation': 'Work arrangement: Remote.'},
        'responsibilities': [{
            'text': 'Design and maintain Python services.',
            'quotation': 'Responsibilities: Design and maintain Python services.',
        }],
        'required_skills': [{
            'name': 'Python',
            'quotation': 'Required skills: Python and SQL.',
            'classification_context': 'Required skills: Python and SQL.',
        }],
        'preferred_skills': [{
            'name': 'Kubernetes',
            'quotation': 'Kubernetes',
            'classification_context': 'Preferred skills: Kubernetes.',
        }],
        'uncertain_skills': [],
        'education_requirements': [{
            'text': 'Bachelor degree in Computer Science.',
            'quotation': 'Education: Bachelor degree in Computer Science.',
        }],
        'experience_requirements': [{
            'text': '5 years of experience with Python.',
            'quotation': 'Experience: 5 years of experience with Python.',
        }],
        'languages': [{
            'language': 'English', 'level': 'fluent',
            'quotation': 'Languages: English (fluent).',
        }],
        'certifications': [{
            'name': 'AWS Certified Developer',
            'quotation': 'Certifications: AWS Certified Developer.',
        }],
        'ambiguities': [{'text': 'Hours may vary.', 'quotation': 'Hours may vary.'}],
        'missing_information': [],
    }


def synthetic_evidence(evidence_id: str, evidence_type: EvidenceType, original_content: str) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        evidence_type=evidence_type,
        original_content=original_content,
        normalized_content=None,
        section_reference=None,
        source_document='synthetic-master-resume.docx',
        source_sha256='a' * 64,
        source_location=None,
        formatting=None,
        hyperlinks=[],
        table_context=None,
    )


def synthetic_candidate_catalog(
    evidence_records: list, *, complete: bool = True, source_sha256: str = 'a' * 64,
    coverage_assessed: bool | None = None, unsupported_content_count: int = 0,
    unresolved_extraction_details: list[str] | None = None,
    unclassified_block_indexes: list[int] | None = None, sections_detected: int | None = None,
    warnings: list[str] | None = None,
) -> CandidateEvidenceCatalog:
    '''Build valid, independently configurable synthetic coverage components.'''
    records = list(evidence_records)
    assessed = complete if coverage_assessed is None else coverage_assessed
    unresolved = list(unresolved_extraction_details) if unresolved_extraction_details is not None else (
        [] if complete else ['synthetic unresolved detail']
    )
    unclassified = list(unclassified_block_indexes or [])
    detected = (1 if records else 0) if sections_detected is None else sections_detected
    preserved = assessed and unsupported_content_count == 0
    classified = detected > 0 and not unclassified
    return CandidateEvidenceCatalog(
        schema_version='3.0.0',
        source_document=SourceDocumentInfo(
            filename='synthetic-master-resume.docx',
            sha256=source_sha256,
            statistics=None,
        ),
        evidence_records=records,
        section_references=[],
        extraction_completeness=ExtractionCompleteness(
            complete=preserved and classified and not unresolved,
            coverage_assessed=assessed,
            source_preservation_complete=preserved,
            section_classification_complete=classified,
            sections_detected=detected,
            evidence_records=len(records),
            unclassified_block_indexes=unclassified,
            unsupported_content_count=unsupported_content_count,
            unresolved_extraction_details=unresolved,
            warnings=list(warnings or []),
        ),
    )
