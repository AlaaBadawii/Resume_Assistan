'''Deterministic, in-memory matching with boundary-aware textual evidence.

No semantic entailment, synonyms, extraction runtime, filesystem access, or
provider calls are used. See docs/matching.md for the exact phrase rules.
'''

import hashlib
import json
import re
from dataclasses import dataclass

from resume_assistant.job_models import JOB_ANALYSIS_SCHEMA_VERSION, JobDescriptionAnalysis
from resume_assistant.matching_models import (
    MATCHING_METHOD,
    MATCHING_SCHEMA_VERSION,
    CandidateJobMatch,
    ComponentMatch,
    CoverageStatus,
    CoverageWarning,
    MatchCellSource,
    MatchedEvidenceReference,
    MatchSourceLocation,
    RequirementCategory,
    RequirementMatch,
)
from resume_assistant.models import SCHEMA_VERSION, CandidateEvidenceCatalog, EvidenceRecord, EvidenceType

NON_QUALIFICATION_EVIDENCE_TYPES = frozenset({EvidenceType.IDENTITY, EvidenceType.CONTACT})
_CONNECTORS = frozenset({'and', 'or', 'and/or', '&', '/', '|'})
_SKILL_CLASSIFICATIONS = {
    'required_skills': 'required', 'preferred_skills': 'preferred', 'uncertain_skills': 'uncertain',
}


class CandidateJobMatchError(ValueError):
    '''Base class for controlled, content-free matcher errors.'''


class CandidateJobVersionError(CandidateJobMatchError):
    '''An input artifact does not have the supported schema version.'''


class CandidateJobIntegrityError(CandidateJobMatchError):
    '''Evidence identity or source-reference integrity could not be established.'''


def _normalize(text: str) -> str:
    return ' '.join(text.split()).casefold()


def _normalize_phrase(text: str) -> str:
    return _normalize(text).rstrip('.,;').rstrip()


def _meaningful_component(text: str) -> bool:
    return any(character.isalnum() for character in text) and _normalize_phrase(text) not in _CONNECTORS


def _token_character(text: str, index: int) -> bool:
    if index < 0 or index >= len(text):
        return False
    character = text[index]
    if character.isalnum() or character in '_+#/-&':
        return True
    if character == '.' and index + 1 < len(text):
        following = text[index + 1]
        return following.isalnum() or following in '_+#'
    return False


def _phrase_occurs(phrase: str, evidence_text: str) -> bool:
    '''Literal normalized phrase with technical-token boundaries on both ends.'''
    needle, haystack = _normalize_phrase(phrase), _normalize(evidence_text)
    if not needle or not _meaningful_component(needle):
        return False
    start = haystack.find(needle)
    while start != -1:
        end = start + len(needle)
        if not _token_character(haystack, start - 1) and not _token_character(haystack, end):
            return True
        start = haystack.find(needle, start + 1)
    return False


def _split_components(requirement_text: str) -> list[str]:
    '''Split only standalone and, &, /, and |; ambiguous compounds stay whole.'''
    original = requirement_text.strip()
    parts = [part.strip() for part in re.split(r'(?<=\s)(?:and|&|/|\|)(?=\s)', original, flags=re.IGNORECASE)]
    if any(not _meaningful_component(part) for part in parts):
        return [original]
    components, seen = [], set()
    for part in parts:
        identity = _normalize_phrase(part)
        if identity not in seen:
            seen.add(identity)
            components.append(part)
    return components


@dataclass(frozen=True)
class _Requirement:
    category: RequirementCategory
    source_index: int
    text: str
    quotation: str
    classification: str | None
    classification_status: str
    classification_context: str | None
    language_level: str | None
    identifier: str


def _requirements(analysis: JobDescriptionAnalysis) -> list[_Requirement]:
    requirements = []
    for category, text_attribute in (
        ('required_skills', 'name'), ('preferred_skills', 'name'), ('uncertain_skills', 'name'),
        ('responsibilities', 'text'), ('education_requirements', 'text'),
        ('experience_requirements', 'text'), ('certifications', 'name'), ('languages', 'language'),
    ):
        for index, item in enumerate(getattr(analysis, category)):
            identity = json.dumps(
                {'category': category, 'source_item': item.model_dump(mode='json', warnings='error')},
                sort_keys=True, ensure_ascii=False, separators=(',', ':'),
            )
            requirements.append(_Requirement(
                category=category, source_index=index, text=getattr(item, text_attribute),
                quotation=item.quotation, classification=_SKILL_CLASSIFICATIONS.get(category),
                classification_status=getattr(item, 'classification_status', analysis.interpretation_status),
                classification_context=getattr(item, 'classification_context', None),
                language_level=getattr(item, 'level', None),
                identifier=hashlib.sha256(identity.encode('utf-8')).hexdigest(),
            ))
    return requirements


def _evidence_reference(record: EvidenceRecord) -> MatchedEvidenceReference:
    return MatchedEvidenceReference(
        evidence_id=record.evidence_id, evidence_type=record.evidence_type,
        source_document=record.source_document, source_sha256=record.source_sha256,
        source_location=(
            MatchSourceLocation.model_validate(record.source_location.model_dump(warnings='error'))
            if record.source_location is not None else None
        ),
        section_reference=record.section_reference,
        cell_sources=[
            MatchCellSource.model_validate(cell.model_dump(warnings='error'))
            for cell in record.table_context.cell_sources
        ] if record.table_context is not None else [],
    )


class CandidateJobMatcher:
    '''Match validated schema-2.0.0 job analysis to schema-3.0.0 evidence.'''

    def __init__(self, catalog: CandidateEvidenceCatalog) -> None:
        if not isinstance(catalog, CandidateEvidenceCatalog):
            raise CandidateJobMatchError('Candidate input must be a validated evidence catalog.')
        if catalog.schema_version != SCHEMA_VERSION:
            raise CandidateJobVersionError('Candidate evidence artifact must use schema 3.0.0.')
        self._catalog = CandidateEvidenceCatalog.model_validate_json(
            catalog.model_dump_json(warnings='error'), strict=True,
        )
        self._records_by_id: dict[str, EvidenceRecord] = {}
        for record in self._catalog.evidence_records:
            if not record.evidence_id.strip() or not any(character.isalnum() for character in record.evidence_id):
                raise CandidateJobIntegrityError('Candidate evidence identifiers must be meaningful and nonblank.')
            previous = self._records_by_id.get(record.evidence_id)
            if previous is not None and previous.model_dump() != record.model_dump():
                raise CandidateJobIntegrityError('Candidate evidence contains conflicting records for one identifier.')
            self._records_by_id.setdefault(record.evidence_id, record)
        self._qualification_evidence = [
            record for record in self._records_by_id.values()
            if record.evidence_type not in NON_QUALIFICATION_EVIDENCE_TYPES
        ]

    @property
    def catalog(self) -> CandidateEvidenceCatalog:
        return self._catalog.model_copy(deep=True)

    @staticmethod
    def _validated_analysis(analysis: JobDescriptionAnalysis) -> JobDescriptionAnalysis:
        if not isinstance(analysis, JobDescriptionAnalysis):
            raise CandidateJobMatchError('Job input must be a validated job analysis artifact.')
        if analysis.schema_version != JOB_ANALYSIS_SCHEMA_VERSION:
            raise CandidateJobVersionError('Job analysis artifact must use schema 2.0.0.')
        return JobDescriptionAnalysis.model_validate_json(analysis.model_dump_json(warnings='error'))

    def assess(self, analysis: JobDescriptionAnalysis) -> CandidateJobMatch:
        source = self._validated_analysis(analysis)
        result = self._build_match(source)
        return self.validate_result(result, source)

    def validate_result(self, result: CandidateJobMatch, analysis: JobDescriptionAnalysis) -> CandidateJobMatch:
        '''Check structural integrity and all cross-references against both sources.'''
        source = self._validated_analysis(analysis)
        restored = CandidateJobMatch.model_validate_json(result.model_dump_json(warnings='error'))
        for requirement in restored.requirements:
            for reference in requirement.matched_evidence:
                record = self._records_by_id.get(reference.evidence_id)
                if record is None:
                    raise CandidateJobIntegrityError('Match references an unknown candidate evidence identifier.')
                if reference != _evidence_reference(record):
                    raise CandidateJobIntegrityError('Matched evidence provenance does not agree with its source record.')
        if restored != self._build_match(source):
            raise CandidateJobIntegrityError('Match requirements, components, or metadata do not agree with the source artifacts.')
        return restored

    def _coverage_status(self) -> tuple[CoverageStatus, list[CoverageWarning]]:
        completeness = self._catalog.extraction_completeness
        warnings: list[CoverageWarning] = []
        if not self._catalog.evidence_records:
            warnings.append('empty_evidence_catalog')
        if not completeness.coverage_assessed:
            warnings.append('coverage_not_assessed')
        if not completeness.source_preservation_complete:
            warnings.append('source_preservation_incomplete')
        if completeness.unsupported_content_count:
            warnings.append('unsupported_content_present')
        if completeness.unresolved_extraction_details:
            warnings.append('unresolved_extraction_details_present')
        if not completeness.section_classification_complete:
            warnings.append('section_classification_incomplete')
        if completeness.unclassified_block_indexes:
            warnings.append('unclassified_blocks_present')
        if completeness.warnings:
            warnings.append('extraction_warnings_present')
        if completeness.evidence_records != len(self._catalog.evidence_records):
            warnings.append('evidence_record_count_mismatch')
        if any(not any(character.isalnum() for character in record.original_content)
               for record in self._catalog.evidence_records):
            warnings.append('evidence_text_missing')
        if not self._catalog.evidence_records or not completeness.coverage_assessed or completeness.unresolved_extraction_details:
            return 'not_assessable', warnings
        return ('incomplete' if warnings else 'assessed'), warnings

    def _find_evidence(self, component: str) -> EvidenceRecord | None:
        return next((
            record for record in self._qualification_evidence if _phrase_occurs(component, record.original_content)
        ), None)

    def _components(self, requirement: _Requirement, negative_status: str) -> list[ComponentMatch]:
        def component(text, record, kind='phrase'):
            return ComponentMatch(
                text=text, kind=kind, status='supported' if record is not None else negative_status,
                matched_evidence_ids=[record.evidence_id] if record is not None else [],
                matching_method=MATCHING_METHOD,
            )

        if requirement.category == 'languages':
            language_records = [
                record for record in self._qualification_evidence
                if _phrase_occurs(requirement.text, record.original_content)
            ]
            full_support = next((
                record for record in language_records
                if requirement.language_level is None or _phrase_occurs(requirement.language_level, record.original_content)
            ), None)
            language_support = full_support or next(iter(language_records), None)
            components = [component(requirement.text, language_support, 'language')]
            if requirement.language_level is not None:
                components.append(component(requirement.language_level, full_support, 'language_level'))
            return components
        phrases = _split_components(requirement.text) if requirement.category in _SKILL_CLASSIFICATIONS else [requirement.text]
        return [component(phrase, self._find_evidence(phrase)) for phrase in phrases]

    def _assess_requirement(self, requirement: _Requirement, coverage_status: CoverageStatus) -> RequirementMatch:
        negative_status = 'no_evidence_found' if coverage_status == 'assessed' else 'not_assessable'
        components = self._components(requirement, negative_status)
        evidence_ids = list(dict.fromkeys(
            evidence_id for item in components for evidence_id in item.matched_evidence_ids
        ))
        missing = [item.text for item in components if item.status != 'supported']
        status = 'supported' if not missing else 'partially_supported' if evidence_ids else negative_status
        return RequirementMatch(
            requirement_id=requirement.identifier, category=requirement.category, source_index=requirement.source_index,
            requirement_text=requirement.text, source_quotation=requirement.quotation,
            classification=requirement.classification, classification_status=requirement.classification_status,
            classification_context=requirement.classification_context, language_level=requirement.language_level,
            status=status, matched_evidence_ids=evidence_ids,
            matched_evidence=[_evidence_reference(self._records_by_id[evidence_id]) for evidence_id in evidence_ids],
            components=components, unmatched_components=missing,
            matching_method=MATCHING_METHOD,
        )

    def _build_match(self, analysis: JobDescriptionAnalysis) -> CandidateJobMatch:
        coverage_status, warnings = self._coverage_status()
        return CandidateJobMatch(
            schema_version=MATCHING_SCHEMA_VERSION,
            source_job_schema_version=analysis.schema_version, source_job_prompt_version=analysis.prompt_version,
            source_job_grounding_method=analysis.grounding_method,
            source_job_interpretation_status=analysis.interpretation_status,
            candidate_evidence_schema_version=self._catalog.schema_version,
            candidate_source_sha256=self._catalog.source_document.sha256,
            candidate_evidence_record_count=len(self._catalog.evidence_records),
            coverage_status=coverage_status, coverage_warnings=warnings,
            requirements=[self._assess_requirement(requirement, coverage_status) for requirement in _requirements(analysis)],
            limitations=[
                'Phrase occurrence is textual evidence, not semantic entailment, qualification possession, or extraction recall.',
                'Negated, hypothetical, and interest-only mentions can satisfy lexical rules and require human review.',
                'No lexical match establishes only a search result, never that the candidate lacks a qualification.',
                'Missing source locations or per-record hashes remain null; provenance is never invented.',
            ],
        )
