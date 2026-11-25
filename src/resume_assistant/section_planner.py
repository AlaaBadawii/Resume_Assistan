'''Deterministic resume section planning and evidence strategy.

Consumes the candidate evidence catalog (schema 3.0.0), the job
description analysis (schema 2.0.0), and the candidate job match
(schema 2.0.0) to produce a versioned resume section plan
(schema 2.0.0). The planner is fully deterministic and makes no LLM
calls. It reuses existing matcher decisions and evidence provenance
rather than recreating the matching engine.

The output is the structured input consumed by the future Phase 4
resume-generation stage.
'''

import re
from collections.abc import Callable
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

from resume_assistant.evidence import CONTACT_PREFIXES, _is_contact_text
from resume_assistant.job_models import JOB_ANALYSIS_SCHEMA_VERSION, JobDescriptionAnalysis
from resume_assistant.legacy_section_plan import LegacyResumeSectionPlan
from resume_assistant.matching import CandidateJobMatchError, CandidateJobMatcher
from resume_assistant.matching_models import (
    MATCHING_SCHEMA_VERSION,
    CandidateJobMatch,
)
from resume_assistant.models import (
    SCHEMA_VERSION,
    CandidateEvidenceCatalog,
    DocumentSnapshot,
    EvidenceRecord,
    EvidenceType,
    ParagraphBlock,
    SourceDocumentInfo,
)
from resume_assistant.section_detector import TOP_LEVEL_NUMBER_PATTERN, _identifier_for
from resume_assistant.section_plan import (
    PLAN_SCHEMA_VERSION, PLANNER_VERSION, OUTPUT_SECTION_ORDER, SECTION_HEADING_TEXT,
    SOURCE_SECTION_TO_OUTPUT_SECTION, BACKGROUND_SECTION_REFERENCES, SECTION_EVIDENCE_TYPES,
    DEFAULT_BUDGETS, ENTRY_GROUPED_SECTIONS, SUPPORTED_STATUSES, SKILL_TIER_ORDER,
    PlanningMetadata, TargetJobContext, SkillRequirementAssociation, PlannedSkill,
    PlannedRequirementCoverage, ProfessionalSummaryBlueprint, PlanBudgets,
    ExcludedEvidence, PlannedResumeEntry, PlannedSection, ResumeSectionPlan,
    EvidenceProvenance, PlanSourceLocation, PlanCellSource, IdentityStatement,
    ExperienceCategoryBinding, ExtractionGenerationIdentity, content_sha256,
    skill_identity as _skill_identity, source_order_key as _source_order_key,
)

_CONTACT_LABEL_QUALIFIERS = frozenset({
    'address',
    'contact',
    'handle',
    'id',
    'link',
    'mobile',
    'number',
    'page',
    'phone',
    'profile',
    'site',
    'url',
})
_CONTACT_PREFIXES_BY_LENGTH = tuple(sorted(CONTACT_PREFIXES, key=len, reverse=True))
_EMAIL_VALUE = re.compile(r'[^@\s]+@[^@\s]+\.[^@\s]+')
_LINKEDIN_PROFILE_VALUE = re.compile(
    r'(?:https?://)?(?:www\.)?linkedin\.com(?:/.*)?', re.IGNORECASE,
)
_GITHUB_PROFILE_VALUE = re.compile(
    r'(?:https?://)?(?:www\.)?github\.com/.+', re.IGNORECASE,
)
_ENTRY_HEADING_STYLE = re.compile(r'heading\s*([1-9])', re.IGNORECASE)
_NUMBERED_ENTRY_HEADING = re.compile(r'^\s*(\d+)\.(\d+)\.?\s+\S')


class SectionPlannerError(ValueError):
    '''Controlled, content-free planner error.'''


def _ensure_sufficient_provenance(catalog: CandidateEvidenceCatalog) -> None:
    '''Reject catalogs whose records cannot be traced to the catalog source.

    Every record must carry the catalog source filename and digest, and a
    source location with a body-block index. This is a content-free gate; it
    does not re-derive provenance or compare against a snapshot.
    '''
    source = catalog.source_document
    if (
        not isinstance(source, SourceDocumentInfo)
        or not isinstance(source.filename, str)
        or not source.filename.strip()
        or not isinstance(source.sha256, str)
        or len(source.sha256) != 64
        or any(character not in '0123456789abcdefABCDEF' for character in source.sha256)
    ):
        raise SectionPlannerError(
            'Candidate evidence provenance is insufficient for section planning.'
        )
    for record in catalog.evidence_records:
        if (
            record.source_sha256 != source.sha256
            or record.source_document != source.filename
            or record.source_location is None
            or record.source_location.body_block_index is None
        ):
            raise SectionPlannerError(
                'Candidate evidence provenance is insufficient for section planning.'
            )


def _contact_prefix_for_label(label: str) -> str | None:
    identity = _skill_identity(label)
    for prefix in _CONTACT_PREFIXES_BY_LENGTH:
        if identity == prefix:
            return prefix
        if identity.startswith(prefix + ' '):
            remainder = identity[len(prefix) + 1:]
            if remainder and all(
                token in _CONTACT_LABEL_QUALIFIERS for token in remainder.split()
            ):
                return prefix
    return None


def _is_contact_label(label: str, *, bare: bool) -> bool:
    prefix = _contact_prefix_for_label(label)
    if prefix is None:
        return False
    if prefix == 'github' and bare and _skill_identity(label) == 'github':
        return False
    return True


def _is_contact_profile_value(value: str) -> bool:
    normalized = value.strip().casefold().rstrip('.,;')
    if not normalized:
        return False
    return bool(
        _EMAIL_VALUE.fullmatch(normalized)
        or _LINKEDIN_PROFILE_VALUE.fullmatch(normalized)
        or _GITHUB_PROFILE_VALUE.fullmatch(normalized)
    )


def _line_has_contact_label_and_value(line: str) -> bool:
    identity = _skill_identity(line)
    for prefix in _CONTACT_PREFIXES_BY_LENGTH:
        if prefix == 'github':
            continue
        if not identity.startswith(prefix + ' '):
            continue
        remainder = identity[len(prefix) + 1:]
        if _is_contact_profile_value(remainder) or _is_contact_label(remainder, bare=False):
            return True
    return False


def _is_contact_metadata_record(content: str) -> bool:
    if not content:
        return False
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if ':' in line:
            label, _, _value = line.partition(':')
            if _is_contact_label(label, bare=False) and _is_contact_text(line):
                return True
            continue
        if _is_contact_label(line, bare=True) and _is_contact_text(line):
            return True
        if _is_contact_profile_value(line):
            return True
        if _line_has_contact_label_and_value(line):
            return True
    return False


def _section_boundaries_unavailable(
    snapshot: DocumentSnapshot | None, section_reference: str | None,
) -> bool:
    if section_reference is None:
        return False
    if snapshot is None:
        return True
    matching = [
        section for section in snapshot.sections
        if section.identifier == section_reference
    ]
    if not matching:
        return True
    return all(section.start_block_index is None for section in matching)


def _deduplicate_skills(skills: list[PlannedSkill]) -> list[PlannedSkill]:
    '''Merge priority-ordered candidates without discarding evidence or parents.'''
    merged: dict[str, PlannedSkill] = {}
    for skill in skills:
        identity = _skill_identity(skill.skill_text)
        previous = merged.get(identity)
        if previous is None:
            merged[identity] = skill
            continue
        associations = {item.requirement_id: item for item in previous.requirement_associations}
        for association in skill.requirement_associations:
            existing = associations.get(association.requirement_id)
            if existing is None:
                associations[association.requirement_id] = association
            else:
                associations[association.requirement_id] = existing.model_copy(update={
                    'matched_evidence_ids': list(dict.fromkeys([
                        *existing.matched_evidence_ids, *association.matched_evidence_ids,
                    ])),
                })
        # The first candidate has the strongest tier and deterministic source
        # tie-break, so its display text, primary parent, and section remain.
        merged[identity] = previous.model_copy(update={
            'matched_evidence_ids': list(dict.fromkeys([
                *previous.matched_evidence_ids, *skill.matched_evidence_ids,
            ])),
            'requirement_associations': list(associations.values()),
        })
    return list(merged.values())


def _heading_evidence_ids(catalog: CandidateEvidenceCatalog, snapshot: DocumentSnapshot | None) -> set[str]:
    '''Use section source boundaries and recorded heading metadata, not skill inference.'''
    sections_by_start = {
        section.start_block_index: section
        for section in snapshot.sections
        if section.start_block_index is not None
    } if snapshot is not None else {}
    headings = set()
    for record in catalog.evidence_records:
        numbered = TOP_LEVEL_NUMBER_PATTERN.match(record.original_content)
        text = numbered.group(2) if numbered else record.original_content
        section = sections_by_start.get(record.source_location.body_block_index)
        if section is not None and _skill_identity(text) == _skill_identity(section.heading_text):
            headings.add(record.evidence_id)
            continue
        style = (record.formatting or {}).get('style')
        # Technical subgroup headings are labels, while employment/project
        # titles can carry factual content and must not be discarded by style.
        if (
            record.evidence_type == EvidenceType.TECHNICAL_SKILL
            and isinstance(style, str) and style.casefold().startswith('heading')
        ):
            headings.add(record.evidence_id)
        elif (
            _section_boundaries_unavailable(snapshot, record.section_reference)
            and _identifier_for(text) == record.section_reference
        ):
            headings.add(record.evidence_id)
    return headings


def _eligible_section_evidence(record: EvidenceRecord, headings: set[str]) -> bool:
    destination = SOURCE_SECTION_TO_OUTPUT_SECTION.get(record.section_reference)
    if destination is None or record.evidence_type != SECTION_EVIDENCE_TYPES[destination]:
        return False
    if record.evidence_id in headings or not record.original_content.strip():
        return False
    # Reuse the extraction contact classifier for contact-label metadata. A
    # plain technology name such as GitHub is not itself a contact field.
    return not _is_contact_metadata_record(record.original_content)


def _standalone_background_skill(record: EvidenceRecord) -> bool:
    '''A coarse technical record can be content without being an atomic skill.'''
    text = record.original_content.strip()
    return (
        record.evidence_type == EvidenceType.TECHNICAL_SKILL
        and SOURCE_SECTION_TO_OUTPUT_SECTION.get(record.section_reference) == 'technical-skills'
        and bool(re.fullmatch(r'[\w.+#/-]+', text))
        and any(character.isalnum() for character in text)
    )


def _entry_heading_level(
    record: EvidenceRecord,
    parent_number: str | None,
    paragraphs: dict[int, ParagraphBlock],
) -> int | None:
    '''Use heading styles or fully bold, source-numbered child headings.'''
    location = record.source_location
    if location.table_index is not None:
        return None
    paragraph = paragraphs.get(location.body_block_index)
    if paragraph is not None and (
        paragraph.text != record.original_content
        or (location.paragraph_index is not None and paragraph.index != location.paragraph_index)
    ):
        return None
    formatting = record.formatting or {}
    style = paragraph.style if paragraph is not None else formatting.get('style')
    style_heading = _ENTRY_HEADING_STYLE.fullmatch(style) if isinstance(style, str) else None
    if style_heading is not None:
        return int(style_heading.group(1))
    numbered = _NUMBERED_ENTRY_HEADING.match(record.original_content)
    if (
        paragraph is None or numbered is None or numbered.group(1) != parent_number
        or paragraph.bullet is True or paragraph.numbering_format == 'bullet'
    ):
        return None
    visible_runs = [run for run in paragraph.runs if run.text.strip()]
    if (
        not visible_runs
        or ''.join(run.text for run in paragraph.runs) != paragraph.text
        or not all(run.bold is True for run in visible_runs)
    ):
        return None
    return 2


def _table_entry_key(record: EvidenceRecord) -> tuple[int, int, int] | None:
    location = record.source_location
    if location.table_index is None or location.row_index is None:
        return None
    path = location.nested_table_path or []
    if len(path) % 3:
        return None
    table, row = (path[0], path[1]) if path else (location.table_index, location.row_index)
    return (location.body_block_index, table, row)


def _vertical_merge_roots(
    snapshot: DocumentSnapshot | None,
) -> dict[tuple[int, int, int], tuple[tuple[int, int, int], bool]]:
    '''Bind source-connected outer rows; nested evidence already shares its outer row key.'''
    roots = {}
    if snapshot is None:
        return roots
    for block in snapshot.blocks:
        table = block.table
        if table is None or not any(cell.v_merge is not None for row in table.rows for cell in row):
            continue
        parents = list(range(len(table.rows)))
        uncertain_rows: set[int] = set()
        restart_rows: set[int] = set()

        def find(row: int) -> int:
            while parents[row] != row:
                parents[row] = parents[parents[row]]
                row = parents[row]
            return row

        def connect(first: int, second: int) -> None:
            first, second = find(first), find(second)
            parents[max(first, second)] = min(first, second)

        # Active cells retain logical column ranges, origin row and preceding row.
        active: list[tuple[int, int, int | None, int]] = []
        previous_width = 0
        previous_valid = True
        for row_index, row in enumerate(table.rows):
            column = 0
            valid_layout = True
            cells = []
            for cell in row:
                width = 1 if cell.grid_span is None else cell.grid_span
                if width < 1:
                    width, valid_layout = 1, False
                cells.append((cell, column, column + width))
                column += width
            next_active = []
            for cell, start, end in cells:
                if cell.v_merge == 'restart':
                    restart_rows.add(row_index)
                    origin = row_index if valid_layout else None
                    if not valid_layout:
                        uncertain_rows.add(row_index)
                    next_active.append((start, end, origin, row_index))
                elif cell.v_merge == 'continue':
                    overlapping = [parent for parent in active if parent[0] < end and start < parent[1]]
                    aligned = valid_layout and previous_valid and column == previous_width
                    possible_parents = overlapping if aligned else active
                    for parent in possible_parents:
                        connect(row_index, parent[3])
                    reliable = (
                        aligned and len(overlapping) == 1
                        and overlapping[0][:2] == (start, end)
                        and overlapping[0][2] is not None
                    )
                    origin = overlapping[0][2] if reliable else None
                    if not reliable:
                        uncertain_rows.add(row_index)
                    next_active.append((start, end, origin, row_index))
            active = next_active
            previous_width, previous_valid = column, valid_layout

        components: dict[int, list[int]] = {}
        for row_index in range(len(table.rows)):
            components.setdefault(find(row_index), []).append(row_index)
        for root, rows in components.items():
            origins = restart_rows.intersection(rows)
            complete = not uncertain_rows.intersection(rows) and len(origins) <= 1
            anchor = next(iter(origins)) if complete and origins else root
            key = (block.index, table.index, anchor)
            for row_index in rows:
                roots[(block.index, table.index, row_index)] = (key, complete)
    return roots


def _enclosing_source_section_index(
    record: EvidenceRecord, snapshot: DocumentSnapshot | None,
) -> int | None:
    '''Resolve one recorded section instance; an identifier alone is not an instance.'''
    if snapshot is None:
        return None
    block_index = record.source_location.body_block_index
    matches = [
        index for index, section in enumerate(snapshot.sections)
        if section.identifier == record.section_reference
        and section.start_block_index is not None and section.end_block_index is not None
        and section.start_block_index <= block_index <= section.end_block_index
    ]
    return matches[0] if len(matches) == 1 else None


def _group_source_entries(
    records: list[EvidenceRecord],
    eligible_ids: set[str],
    headings: set[str],
    snapshot: DocumentSnapshot | None,
) -> list[PlannedResumeEntry]:
    '''Bind explicit heading spans and row ancestry; never split unmarked spans.'''
    paragraphs = {
        block.index: block.paragraph
        for block in snapshot.blocks if block.paragraph is not None
    } if snapshot is not None else {}
    merge_roots = _vertical_merge_roots(snapshot)
    runs: list[list[EvidenceRecord]] = []
    instances: list[int | None] = []
    for record in records:
        instance = _enclosing_source_section_index(record, snapshot)
        if (
            not runs or runs[-1][-1].section_reference != record.section_reference
            or record.evidence_id in headings
            or instance != instances[-1]
        ):
            runs.append([])
            instances.append(instance)
        runs[-1].append(record)

    entries: list[PlannedResumeEntry] = []

    def append_entry(group: list[EvidenceRecord], identity: str | None, basis: str) -> None:
        ids = [record.evidence_id for record in group if record.evidence_id in eligible_ids]
        if not ids:
            return
        if identity not in ids:
            identity, basis = None, 'unresolved_source_span'
        entries.append(PlannedResumeEntry(
            entry_id=identity or ids[0],
            source_section_reference=group[0].section_reference,
            identity_evidence_id=identity,
            grouping_basis=basis,
            selected_evidence_ids=ids,
        ))

    def append_unheaded_span(span: list[EvidenceRecord]) -> None:
        if not span:
            return
        if any(_table_entry_key(record) is None for record in span):
            append_entry(span, None, 'unresolved_source_span')
            return
        rows: dict[tuple[int, int, int], list[EvidenceRecord]] = {}
        complete_groups: dict[tuple[int, int, int], bool] = {}
        for record in span:
            row_key = _table_entry_key(record)
            root_key, complete = merge_roots.get(row_key, (row_key, True))
            rows.setdefault(root_key, []).append(record)
            complete_groups[root_key] = complete_groups.get(root_key, True) and complete
        for root_key, group in rows.items():
            roots = [
                record for record in group
                if not record.source_location.nested_table_path
                and _table_entry_key(record) == root_key
                and complete_groups[root_key]
            ]
            append_entry(group, roots[0].evidence_id if roots else None, 'table_row')

    for run, instance in zip(runs, instances):
        parent_numbers = set()
        section = snapshot.sections[instance] if snapshot is not None and instance is not None else None
        if section is not None:
            number = re.fullmatch(r'(\d+)\.', section.numbering) if section.numbering is not None else None
            if number is not None:
                parent_numbers.add(number.group(1))
            heading = paragraphs.get(section.start_block_index)
            numbered = TOP_LEVEL_NUMBER_PATTERN.match(heading.text) if heading is not None else None
            if numbered is not None and _skill_identity(numbered.group(2)) == _skill_identity(section.heading_text):
                parent_numbers.add(numbered.group(1))
        for record in run:
            if record.evidence_id in headings and (
                section is None or record.source_location.body_block_index == section.start_block_index
            ):
                numbered = TOP_LEVEL_NUMBER_PATTERN.match(record.original_content)
                if numbered is not None:
                    parent_numbers.add(numbered.group(1))
        parent_number = next(iter(parent_numbers)) if len(parent_numbers) == 1 else None
        content = [record for record in run if record.evidence_id not in headings]
        levels = {
            record.evidence_id: _entry_heading_level(record, parent_number, paragraphs)
            for record in content
        }
        ambiguous_numbering = False
        for record in content:
            numbered = _NUMBERED_ENTRY_HEADING.match(record.original_content)
            paragraph = paragraphs.get(record.source_location.body_block_index)
            formatting = record.formatting or {}
            bold = paragraph.bold if paragraph is not None else formatting.get('bold')
            bullet = paragraph.bullet if paragraph is not None else formatting.get('bullet')
            if (
                numbered is not None and numbered.group(1) == parent_number
                and bold is True and bullet is not True
                and levels[record.evidence_id] is None
            ):
                ambiguous_numbering = True
                break
        if ambiguous_numbering:
            append_entry(content, None, 'unresolved_source_span')
            continue
        root_level = min((level for level in levels.values() if level is not None), default=None)
        span: list[EvidenceRecord] = []
        identity = None
        for record in content:
            if root_level is not None and levels[record.evidence_id] == root_level:
                if identity is not None:
                    append_entry(span, identity, 'source_heading')
                else:
                    append_unheaded_span(span)
                span, identity = [], record.evidence_id
            span.append(record)
        if identity is not None:
            append_entry(span, identity, 'source_heading')
        else:
            append_unheaded_span(span)
    return entries


def _effective_section_budgets(budgets: PlanBudgets) -> dict[str, int]:
    '''Per-section budgets; a one-page target halves content budgets.

    Section-count and page-guarantee settings are structural and unchanged;
    a positive budget never tightens below one atomic source group or record.
    '''
    return budgets.section_caps()


def _select_section_evidence(
    records: list[EvidenceRecord],
    priority_for: Callable[[str], int],
    budget: int,
    *,
    entry_grouped: bool,
    eligible_ids: set[str],
    headings: set[str],
    snapshot: DocumentSnapshot | None,
) -> tuple[list[str], list[str], list[PlannedResumeEntry]]:
    '''Select records under a budget after relevance ranking.

    Records must arrive in source order. Selection ranks by relevance first
    (lower priority value wins), then source position. Source-defined entries
    and unresolved spans are indivisible; returned ids retain source order.
    '''
    eligible_records = [record for record in records if record.evidence_id in eligible_ids]
    if not eligible_records:
        return [], [], []
    if not entry_grouped:
        ranked = sorted(
            range(len(eligible_records)),
            key=lambda index: (priority_for(eligible_records[index].evidence_id), index),
        )
        chosen = set(ranked[:budget])
        selected = [
            eligible_records[index].evidence_id
            for index in range(len(eligible_records))
            if index in chosen
        ]
        excluded = [
            eligible_records[index].evidence_id
            for index in range(len(eligible_records))
            if index not in chosen
        ]
        return selected, excluded, []
    entries = _group_source_entries(records, eligible_ids, headings, snapshot)
    entry_order = sorted(
        range(len(entries)),
        key=lambda entry_index: (
            min(priority_for(evidence_id) for evidence_id in entries[entry_index].selected_evidence_ids),
            entry_index,
        ),
    )
    chosen_entries = set(entry_order[:budget])
    selected_entries = [entry for index, entry in enumerate(entries) if index in chosen_entries]
    selected_set = {
        evidence_id for entry in selected_entries for evidence_id in entry.selected_evidence_ids
    }
    selected = [record.evidence_id for record in eligible_records if record.evidence_id in selected_set]
    excluded = [record.evidence_id for record in eligible_records if record.evidence_id not in selected_set]
    return selected, excluded, selected_entries


class SectionPlanner:
    '''Deterministically plans resume sections and evidence selection.'''

    def __init__(self, budgets: PlanBudgets | None = None) -> None:
        self.budgets = budgets or PlanBudgets(**DEFAULT_BUDGETS)
        if self.budgets.page_guarantee is not False:
            raise SectionPlannerError('Content budgets cannot guarantee a page count.')

    def _prepare_inputs(
        self,
        analysis,
        match: CandidateJobMatch,
        catalog: CandidateEvidenceCatalog,
        snapshot: DocumentSnapshot | None = None,
    ):
        if analysis.schema_version != JOB_ANALYSIS_SCHEMA_VERSION:
            raise SectionPlannerError('Job analysis artifact must use schema 2.0.0.')
        if match.schema_version != MATCHING_SCHEMA_VERSION:
            raise SectionPlannerError('Job match artifact must use schema 2.0.0.')
        if catalog.schema_version != SCHEMA_VERSION:
            raise SectionPlannerError('Candidate evidence artifact must use schema 3.0.0.')

        _ensure_sufficient_provenance(catalog)
        if snapshot is not None:
            # Both models use models.SCHEMA_VERSION for the extraction contract.
            # Check support independently from source identity, not by comparing
            # arbitrary version strings from the supplied artifacts.
            if snapshot.schema_version != SCHEMA_VERSION:
                raise SectionPlannerError('Document snapshot schema version is not supported.')
            if snapshot.source_sha256 != catalog.source_document.sha256:
                raise SectionPlannerError('Snapshot and catalog source hashes disagree.')
            if snapshot.source_filename != catalog.source_document.filename:
                raise SectionPlannerError('Snapshot and catalog source filenames disagree.')

        try:
            analysis = JobDescriptionAnalysis.model_validate_json(analysis.model_dump_json(warnings='error'))
            catalog = CandidateEvidenceCatalog.model_validate_json(catalog.model_dump_json(warnings='error'), strict=True)
            if snapshot is not None:
                snapshot = DocumentSnapshot.model_validate_json(snapshot.model_dump_json(warnings='error'), strict=True)
        except (ValidationError, PydanticSerializationError):
            raise SectionPlannerError('Section planning source models are invalid.') from None

        records_by_id: dict[str, EvidenceRecord] = {}
        for record in catalog.evidence_records:
            if record.evidence_id in records_by_id:
                raise SectionPlannerError(
                    'Candidate evidence contains conflicting records for one identifier.'
                )
            records_by_id[record.evidence_id] = record

        # Validate matcher evidence references resolve to catalog records.
        for requirement in match.requirements:
            for evidence_id in requirement.matched_evidence_ids:
                if evidence_id not in records_by_id:
                    raise SectionPlannerError(
                        'Job match references an unknown candidate evidence identifier.'
                    )

        # Validate source section references when a snapshot is available;
        # fall back to the catalog's declared section references otherwise.
        if snapshot is not None:
            known_section_ids = {section.identifier for section in snapshot.sections if section.identifier}
            if any(reference not in known_section_ids for reference in catalog.section_references):
                raise SectionPlannerError('Catalog references an unknown source section.')
        else:
            known_section_ids = set(catalog.section_references)
        for record in catalog.evidence_records:
            if (
                record.section_reference is not None
                and record.section_reference not in known_section_ids
            ):
                raise SectionPlannerError(
                    'Evidence record references an unknown source section.'
                )

        # A-01: Cross-artifact consistency. The approved matcher is the
        # authoritative boundary for candidate evidence, job analysis, and the
        # candidate job match. Planner-specific checks above are retained as
        # defense-in-depth and do not replace this validation.
        try:
            match = CandidateJobMatcher(catalog).validate_result(match, analysis)
        except CandidateJobMatchError:
            raise SectionPlannerError(
                'Candidate evidence, job analysis, and job match are not mutually consistent.'
            ) from None
        return analysis, match, catalog, snapshot

    def plan(
        self,
        analysis,
        match: CandidateJobMatch,
        catalog: CandidateEvidenceCatalog,
        snapshot: DocumentSnapshot | None = None,
        *,
        manifest_available: bool = False,
        extraction_generation: ExtractionGenerationIdentity | None = None,
    ) -> ResumeSectionPlan:
        sources = self._prepare_inputs(analysis, match, catalog, snapshot)
        try:
            result = self._build_plan(
                *sources, manifest_available=manifest_available,
                extraction_generation=extraction_generation,
            )
            self._validate_source_facts(
                result, *sources, manifest_available=manifest_available,
                extraction_generation=extraction_generation,
            )
        except (ValidationError, PydanticSerializationError):
            raise SectionPlannerError('Section plan output contract is invalid.') from None
        return result

    def validate_result(
        self,
        result: ResumeSectionPlan,
        analysis,
        match: CandidateJobMatch,
        catalog: CandidateEvidenceCatalog,
        snapshot: DocumentSnapshot | None = None,
        *,
        manifest_available: bool = False,
        extraction_generation: ExtractionGenerationIdentity | None = None,
    ) -> ResumeSectionPlan:
        '''Validate completed output against source facts and approved selection policy.'''
        if not isinstance(result, ResumeSectionPlan) or result.schema_version != PLAN_SCHEMA_VERSION:
            raise SectionPlannerError('Section plan result must use the current schema 2.0.0 contract.')
        sources = self._prepare_inputs(analysis, match, catalog, snapshot)
        try:
            restored = ResumeSectionPlan.model_validate_json(result.model_dump_json(warnings='error'))
            self._validate_source_facts(
                restored, *sources, manifest_available=manifest_available,
                extraction_generation=extraction_generation,
            )
            expected = self._build_plan(
                *sources, manifest_available=manifest_available,
                extraction_generation=extraction_generation,
            )
        except (ValidationError, PydanticSerializationError):
            raise SectionPlannerError('Section plan output contract is invalid.') from None
        if restored != expected:
            raise SectionPlannerError('Section plan selections do not agree with the validated source decisions.')
        return restored

    def replan_legacy(
        self,
        legacy: LegacyResumeSectionPlan,
        analysis,
        match: CandidateJobMatch,
        catalog: CandidateEvidenceCatalog,
        snapshot: DocumentSnapshot | None = None,
        **context,
    ) -> ResumeSectionPlan:
        '''Replace historical instructions only by planning from their validated sources.'''
        if not isinstance(legacy, LegacyResumeSectionPlan):
            raise SectionPlannerError('Historical plan must use the explicit schema-1 reader.')
        sources = self._prepare_inputs(analysis, match, catalog, snapshot)
        analysis, match, catalog, snapshot = sources
        coverage = [
            {
                'requirement_id': item.requirement_id, 'category': item.category,
                'status': item.status, 'matched_evidence_ids': list(item.matched_evidence_ids),
                'unmatched_components': list(item.unmatched_components),
            }
            for item in match.requirements
        ]
        target = self._target_job_context(analysis)
        if (
            legacy.planning_metadata.source_candidate_source_sha256 != catalog.source_document.sha256
            or legacy.planning_metadata.source_candidate_evidence_record_count != len(catalog.evidence_records)
            or legacy.target_job_context.model_dump() != target.model_dump()
            or [item.model_dump() for item in legacy.requirement_coverage] != coverage
        ):
            raise SectionPlannerError('Historical plan does not belong to the supplied validated sources.')
        return self.plan(analysis, match, catalog, snapshot, **context)

    @staticmethod
    def _target_job_context(analysis) -> TargetJobContext:
        return TargetJobContext(
            role_title=analysis.role_title.value if analysis.role_title is not None else None,
            company_name=analysis.company_name.value if analysis.company_name is not None else None,
            seniority_level=analysis.seniority_level.value if analysis.seniority_level is not None else None,
            employment_type=analysis.employment_type.value if analysis.employment_type is not None else None,
            work_arrangement=analysis.work_arrangement.value if analysis.work_arrangement is not None else None,
            required_skill_count=len(analysis.required_skills), preferred_skill_count=len(analysis.preferred_skills),
            uncertain_skill_count=len(analysis.uncertain_skills), responsibility_count=len(analysis.responsibilities),
            interpretation_status=analysis.interpretation_status,
        )

    def _validate_source_facts(
        self, result, analysis, match, catalog, snapshot, *, manifest_available, extraction_generation,
    ) -> None:
        '''Explicit original-record comparisons; no matching or factual reinterpretation.'''
        metadata = result.planning_metadata
        if (
            metadata.source_candidate_source_sha256 != catalog.source_document.sha256
            or metadata.source_candidate_filename != catalog.source_document.filename
            or metadata.source_candidate_evidence_record_count != len(catalog.evidence_records)
            or metadata.snapshot_available != (snapshot is not None)
            or metadata.manifest_available != manifest_available
            or metadata.extraction_generation != extraction_generation
            or result.target_job_context != self._target_job_context(analysis)
            or result.budgets != self.budgets
        ):
            raise SectionPlannerError('Section plan context does not agree with its validated sources.')
        records = {record.evidence_id: record for record in catalog.evidence_records}
        if {reference.evidence_id for reference in result.evidence_provenance} != set(records):
            raise SectionPlannerError('Section plan provenance does not account for the original evidence catalog.')
        headings = _heading_evidence_ids(catalog, snapshot)
        for reference in result.evidence_provenance:
            record = records[reference.evidence_id]
            eligible = _eligible_section_evidence(record, headings)
            source_cells = [cell.model_dump(warnings='error') for cell in record.table_context.cell_sources] if record.table_context is not None else None
            reference_cells = [cell.model_dump(warnings='error') for cell in reference.cell_sources] if reference.cell_sources is not None else None
            if (
                reference.evidence_type != record.evidence_type
                or reference.source_document != record.source_document
                or reference.source_sha256 != record.source_sha256
                or reference.content_sha256 != content_sha256(record.original_content)
                or reference.source_location.model_dump(warnings='error') != record.source_location.model_dump(warnings='error')
                or reference_cells != source_cells
                or reference.source_section_reference != record.section_reference
                or reference.content_eligible != eligible
                or reference.atomic_skill_text != (record.original_content if eligible and _standalone_background_skill(record) else None)
            ):
                raise SectionPlannerError('Section plan evidence provenance does not agree with its original record.')
        expected_coverage = [PlannedRequirementCoverage(
            requirement_id=item.requirement_id, category=item.category, status=item.status,
            matched_evidence_ids=list(item.matched_evidence_ids), unmatched_components=list(item.unmatched_components),
            components=[component.model_copy(deep=True) for component in item.components],
        ) for item in match.requirements]
        if result.requirement_coverage != expected_coverage:
            raise SectionPlannerError('Section plan requirements do not preserve the supplied matcher decisions.')
        for statement in result.professional_summary_blueprint.identity_statements:
            record = records[statement.evidence_ids[0]]
            if record.evidence_type != EvidenceType.PROFESSIONAL_SUMMARY or statement.statement_text != record.original_content:
                raise SectionPlannerError('Summary identity statements do not preserve original professional evidence.')

    def _build_plan(
        self, analysis, match, catalog, snapshot, *, manifest_available, extraction_generation,
    ) -> ResumeSectionPlan:
        '''One approved selection implementation shared by production and result validation.'''
        records_by_id = {record.evidence_id: record for record in catalog.evidence_records}

        # --- Evidence selection driven by matcher decisions ---
        matched_ids: list[str] = []
        skill_requirements: list = []
        required_matched: set[str] = set()
        preferred_matched: set[str] = set()
        other_matched: set[str] = set()
        for requirement in match.requirements:
            if requirement.status in SUPPORTED_STATUSES:
                for evidence_id in requirement.matched_evidence_ids:
                    if evidence_id not in matched_ids:
                        matched_ids.append(evidence_id)
                requirement_evidence = set(requirement.matched_evidence_ids)
                if requirement.category == 'required_skills':
                    required_matched |= requirement_evidence
                elif requirement.category == 'preferred_skills':
                    preferred_matched |= requirement_evidence
                else:
                    other_matched |= requirement_evidence
            if requirement.category in ('required_skills', 'preferred_skills'):
                skill_requirements.append(requirement)
        preferred_matched -= required_matched
        other_matched -= required_matched | preferred_matched

        def _evidence_priority(evidence_id: str) -> int:
            if evidence_id in required_matched:
                return 0
            if evidence_id in preferred_matched:
                return 1
            if evidence_id in other_matched:
                return 2
            return 3

        # Section assignment is not enough to establish content eligibility:
        # preserve the extractor's content types and exclude structural records.
        headings = _heading_evidence_ids(catalog, snapshot)
        exclusion_reasons: dict[str, str] = {}
        section_pools: dict[str, list[EvidenceRecord]] = {
            section_id: [] for section_id in OUTPUT_SECTION_ORDER
        }
        for record in catalog.evidence_records:
            if record.section_reference is None:
                exclusion_reasons[record.evidence_id] = (
                    'unclassified_section_no_output_mapping'
                )
                continue
            destination = SOURCE_SECTION_TO_OUTPUT_SECTION.get(record.section_reference)
            if destination is None:
                exclusion_reasons[record.evidence_id] = (
                    'source_section_not_mapped_to_output_section'
                )
                continue
            section_pools[destination].append(record)
            if not _eligible_section_evidence(record, headings):
                exclusion_reasons[record.evidence_id] = (
                    'content_not_eligible_for_output_section'
                )
        for section_id in OUTPUT_SECTION_ORDER:
            section_pools[section_id].sort(key=_source_order_key)
        eligible_ids = {
            record.evidence_id
            for record in catalog.evidence_records
            if record.evidence_id not in exclusion_reasons
        }
        effective_budgets = _effective_section_budgets(self.budgets)

        # --- Prioritized skills: required -> preferred -> other ---
        prioritized_skills: list[PlannedSkill] = []
        for requirement in skill_requirements:
            tier = 'required' if requirement.category == 'required_skills' else 'preferred'
            for component in requirement.components:
                if component.status != 'supported':
                    continue
                evidence_ids = [evidence_id for evidence_id in component.matched_evidence_ids if evidence_id in eligible_ids]
                if not evidence_ids:
                    continue
                record = records_by_id[evidence_ids[0]]
                prioritized_skills.append(
                    PlannedSkill(
                        skill_text=component.text,
                        priority_tier=tier,
                        matched_evidence_ids=evidence_ids,
                        source_section_reference=record.section_reference,
                        requirement_id=requirement.requirement_id,
                        requirement_associations=[SkillRequirementAssociation(
                            requirement_id=requirement.requirement_id,
                            priority_tier=tier,
                            matched_evidence_ids=evidence_ids,
                        )],
                    )
                )
        for record in catalog.evidence_records:
            if (
                record.evidence_id in eligible_ids
                and _standalone_background_skill(record)
                and record.evidence_id not in matched_ids
            ):
                prioritized_skills.append(
                    PlannedSkill(
                        skill_text=record.original_content,
                        priority_tier='other',
                        matched_evidence_ids=[record.evidence_id],
                        source_section_reference=record.section_reference,
                        requirement_id=None,
                    )
                )

        # Stable skill ordering uses the same source coordinates as sections.
        def _skill_sort_key(skill: PlannedSkill):
            tier_rank = SKILL_TIER_ORDER.index(skill.priority_tier)
            return (
                tier_rank,
                min(_source_order_key(records_by_id[evidence_id]) for evidence_id in skill.matched_evidence_ids),
            )

        prioritized_skills.sort(key=_skill_sort_key)
        prioritized_skills = _deduplicate_skills(prioritized_skills)

        # --- Select whole source groups before applying the output-section cap ---
        ordered_sections: list[PlannedSection] = []
        for section_id in OUTPUT_SECTION_ORDER:
            pool = section_pools[section_id]
            if not pool:
                continue
            budget = effective_budgets[section_id]
            selected_ids, budget_excluded_ids, entries = _select_section_evidence(
                pool,
                _evidence_priority,
                budget,
                entry_grouped=section_id in ENTRY_GROUPED_SECTIONS,
                eligible_ids=eligible_ids,
                headings=headings,
                snapshot=snapshot,
            )
            for evidence_id in budget_excluded_ids:
                exclusion_reasons[evidence_id] = 'content_budget_exhausted'
            if not selected_ids:
                continue
            selected_records = [records_by_id[evidence_id] for evidence_id in selected_ids]
            ordered_sections.append(
                PlannedSection(
                    section_id=section_id,
                    heading_text=SECTION_HEADING_TEXT[section_id],
                    source_section_references=[
                        reference for reference in dict.fromkeys(
                            record.section_reference for record in selected_records
                        )
                        if reference is not None
                    ],
                    selected_evidence_ids=selected_ids,
                    item_count=len(selected_ids),
                    budget=budget,
                    dropped_empty=False,
                    entries=entries,
                    entry_grouping_complete=all(
                        entry.grouping_basis != 'unresolved_source_span' for entry in entries
                    ),
                )
            )

        ranked_sections = sorted(
            ordered_sections,
            key=lambda section: (
                min(_evidence_priority(evidence_id) for evidence_id in section.selected_evidence_ids),
                OUTPUT_SECTION_ORDER.index(section.section_id),
            ),
        )
        kept_sections = {section.section_id for section in ranked_sections[: self.budgets.max_output_sections]}
        for section in ordered_sections:
            if section.section_id not in kept_sections:
                for evidence_id in section.selected_evidence_ids:
                    exclusion_reasons[evidence_id] = 'output_section_budget_exhausted'
        ordered_sections = [section for section in ordered_sections if section.section_id in kept_sections]

        # Flat selection concatenates the surviving sections in output order.
        final_selected_ids: list[str] = []
        for section in ordered_sections:
            final_selected_ids.extend(section.selected_evidence_ids)
        selected_set = set(final_selected_ids)
        if len(final_selected_ids) != len(selected_set):
            raise SectionPlannerError('Section planning produced duplicate evidence selections.')
        for section in ordered_sections:
            if section.section_id in ENTRY_GROUPED_SECTIONS:
                members = [evidence_id for entry in section.entries for evidence_id in entry.selected_evidence_ids]
                if len(members) != len(set(members)) or set(members) != set(section.selected_evidence_ids):
                    raise SectionPlannerError('Section and resume entry evidence membership disagree.')

        # Every catalog record must resolve to exactly one outcome.
        for record in catalog.evidence_records:
            if record.evidence_id in selected_set:
                if record.evidence_id in exclusion_reasons:
                    raise SectionPlannerError(
                        'Section planning produced conflicting evidence outcomes.'
                    )
            elif record.evidence_id not in exclusion_reasons:
                raise SectionPlannerError(
                    'Section planning left candidate evidence without an outcome.'
                )

        # Keep only prioritized skills whose evidence survived section budgets.
        prioritized_skills = [
            skill
            for skill in prioritized_skills
            if any(evidence_id in selected_set for evidence_id in skill.matched_evidence_ids)
        ]
        prioritized_skills = prioritized_skills[: effective_budgets['technical-skills']]

        # --- Requirement coverage (copied from matcher, not recomputed) ---
        requirement_coverage: list[PlannedRequirementCoverage] = [
            PlannedRequirementCoverage(
                requirement_id=requirement.requirement_id,
                category=requirement.category,
                status=requirement.status,
                matched_evidence_ids=list(requirement.matched_evidence_ids),
                unmatched_components=list(requirement.unmatched_components),
                components=[component.model_copy(deep=True) for component in requirement.components],
            )
            for requirement in match.requirements
        ]

        # --- Professional-summary blueprint ---
        identity_ids = [
            record.evidence_id
            for record in catalog.evidence_records
            if record.evidence_type == EvidenceType.PROFESSIONAL_SUMMARY
            and record.evidence_id in selected_set
        ]
        experience_categories = [
            section.section_id
            for section in ordered_sections
            if section.section_id == 'professional-experience' and section.item_count > 0
        ]
        project_ids = [
            evidence_id
            for section in ordered_sections
            if section.section_id == 'projects'
            for evidence_id in section.selected_evidence_ids
        ]
        technical_capability_texts = [skill.skill_text for skill in prioritized_skills]

        blueprint = ProfessionalSummaryBlueprint(
            professional_identity_evidence_ids=identity_ids,
            experience_categories=experience_categories,
            technical_capability_skill_texts=technical_capability_texts,
            relevant_project_evidence_ids=project_ids,
            identity_statements=[IdentityStatement(
                statement_text=records_by_id[evidence_id].original_content,
                evidence_ids=[evidence_id],
            ) for evidence_id in identity_ids],
            technical_capabilities=[skill.model_copy(deep=True) for skill in prioritized_skills],
            experience_bindings=[ExperienceCategoryBinding(
                category='professional-experience',
                entry_ids=[entry.entry_id for entry in section.entries],
                evidence_ids=list(section.selected_evidence_ids),
            ) for section in ordered_sections if section.section_id == 'professional-experience'],
            project_highlights=[entry.model_copy(deep=True) for section in ordered_sections if section.section_id == 'projects' for entry in section.entries],
        )

        # --- Excluded evidence accounting ---
        excluded_evidence: list[ExcludedEvidence] = [
            ExcludedEvidence(
                evidence_id=record.evidence_id,
                reason=exclusion_reasons[record.evidence_id],
            )
            for record in sorted(catalog.evidence_records, key=_source_order_key)
            if record.evidence_id not in selected_set
        ]

        assignments = {
            evidence_id: (section.section_id, next((entry.entry_id for entry in section.entries if evidence_id in entry.selected_evidence_ids), None))
            for section in ordered_sections for evidence_id in section.selected_evidence_ids
        }
        provenance = [EvidenceProvenance(
            evidence_id=record.evidence_id, evidence_type=record.evidence_type,
            source_document=record.source_document, source_sha256=record.source_sha256,
            content_sha256=content_sha256(record.original_content),
            source_location=PlanSourceLocation.model_validate(record.source_location.model_dump(warnings='error')),
            cell_sources=[PlanCellSource.model_validate(cell.model_dump(warnings='error')) for cell in record.table_context.cell_sources] if record.table_context is not None else None,
            source_section_reference=record.section_reference,
            output_section_id=assignments.get(record.evidence_id, (None, None))[0],
            resume_entry_id=assignments.get(record.evidence_id, (None, None))[1],
            content_eligible=record.evidence_id in eligible_ids,
            atomic_skill_text=record.original_content if record.evidence_id in eligible_ids and _standalone_background_skill(record) else None,
        ) for record in sorted(catalog.evidence_records, key=_source_order_key)]

        return ResumeSectionPlan(
            planning_metadata=PlanningMetadata(
                source_candidate_evidence_schema_version=catalog.schema_version,
                source_job_analysis_schema_version=analysis.schema_version,
                source_job_match_schema_version=match.schema_version,
                source_candidate_source_sha256=catalog.source_document.sha256,
                source_candidate_filename=catalog.source_document.filename,
                source_candidate_evidence_record_count=len(catalog.evidence_records),
                snapshot_available=snapshot is not None,
                manifest_available=manifest_available,
                extraction_generation=extraction_generation,
            ),
            target_job_context=self._target_job_context(analysis),
            ordered_sections=ordered_sections,
            selected_evidence_ids=final_selected_ids,
            prioritized_skills=prioritized_skills,
            requirement_coverage=requirement_coverage,
            professional_summary_blueprint=blueprint,
            budgets=self.budgets,
            excluded_evidence=excluded_evidence,
            evidence_provenance=provenance,
        )
