'''Synthetic acceptance regressions for findings M32-01 through M32-10.'''

import pytest
from pydantic import ValidationError

from resume_assistant.job_models import JobDescriptionAnalysis
from resume_assistant.matching import CandidateJobIntegrityError, CandidateJobMatcher
from resume_assistant.matching_models import CandidateJobMatch, RequirementMatch
from resume_assistant.models import EvidenceType, SourceLocation, TableCellSource, TableEvidenceContext
from tests.fixtures.jobs import empty_job_payload, grounded_job_payload, synthetic_candidate_catalog, synthetic_evidence


def _analysis(name='Python', *, category='required_skills', quotation=None, context=None):
    payload = empty_job_payload()
    quote = quotation or f'Required skill: {name}.'
    payload[category] = [{
        'name': name, 'quotation': quote, 'classification_context': context or quote,
    }]
    return JobDescriptionAnalysis.model_validate({**payload, 'prompt_version': '2.0.0'})


def _record(text='Python development.', *, identifier='e-1', evidence_type=EvidenceType.TECHNICAL_SKILL):
    return synthetic_evidence(identifier, evidence_type, text)


def _match(name='Python', text='Python development.', **coverage):
    catalog = synthetic_candidate_catalog([_record(text)], **coverage)
    return CandidateJobMatcher(catalog).assess(_analysis(name))


@pytest.mark.parametrize('requirement, content, expected', [
    ('Java', 'Java development.', 'supported'),
    ('Java', 'JavaScript development.', 'no_evidence_found'),
    ('Go', 'Go developer.', 'supported'),
    ('Go', 'Google Cloud engineer.', 'no_evidence_found'),
    ('R', 'R statistics.', 'supported'),
    ('R', 'Resume author.', 'no_evidence_found'),
    ('R', 'R&D projects.', 'no_evidence_found'),
    ('SQL', 'SQL databases.', 'supported'),
    ('SQL', 'MySQL databases.', 'no_evidence_found'),
    ('SQL', 'PostgreSQL databases.', 'no_evidence_found'),
    ('SQL', 'SQLite databases.', 'no_evidence_found'),
    ('Python', 'python', 'supported'),
    ('Python', 'Python development experience.', 'supported'),
    ('Python', 'Pythonic design.', 'no_evidence_found'),
    ('Python', 'Pythonic design followed by Python development.', 'supported'),
    ('Python', 'PyTorch development.', 'no_evidence_found'),
    ('C++', 'C++ development.', 'supported'),
    ('C++', 'C++11 development.', 'no_evidence_found'),
    ('C++', 'C development.', 'no_evidence_found'),
    ('C#', 'C# development.', 'supported'),
    ('C#', 'C## development.', 'no_evidence_found'),
    ('C#', 'C development.', 'no_evidence_found'),
    ('C', 'C++ development.', 'no_evidence_found'),
    ('C', 'C# development.', 'no_evidence_found'),
    ('.NET', '.NET applications.', 'supported'),
    ('.NET', '(.NET) applications.', 'supported'),
    ('.NET', '.NETCore applications.', 'no_evidence_found'),
    ('.NET', 'ASP.NET applications.', 'no_evidence_found'),
    ('NET', '.NET applications.', 'no_evidence_found'),
    ('Node.js', 'Node.js services.', 'supported'),
    ('Node.js', 'NodeJS services.', 'no_evidence_found'),
    ('Node', 'Node.js services.', 'no_evidence_found'),
    ('scikit-learn', 'scikit-learn projects.', 'supported'),
    ('scikit-learn', 'scikitlearn projects.', 'no_evidence_found'),
    ('CI/CD', 'CI/CD pipelines.', 'supported'),
    ('data engineering', 'data analysis projects.', 'no_evidence_found'),
    ('distributed systems', 'distributed applications and storage systems.', 'no_evidence_found'),
    ('Kubernetes', 'kube orchestration.', 'no_evidence_found'),
])
def test_boundary_aware_phrases_do_not_invent_token_fragments_or_synonyms(requirement, content, expected):
    result = _match(requirement, content)
    item = result.requirements[0]
    assert item.status == expected
    assert item.requirement_text == requirement
    assert item.matching_method == 'exact_normalized_phrase_v2'
    assert item.matched_evidence_ids == (['e-1'] if expected == 'supported' else [])


@pytest.mark.parametrize('requirement, content', [
    ('Python.', 'Python development.'),
    ('Python,', 'Python development.'),
    ('Python;', 'Python development.'),
    ('C++.', 'C++ development.'),
    ('C#,', 'C# development.'),
    ('.NET;', '.NET applications.'),
    ('Node.js.', 'Node.js services.'),
    ('Python 3.12.', 'Python 3.12 development.'),
    ('Python', 'Python, SQL; Java.'),
    ('C++', 'C++; C#; .NET.'),
    ('C#', 'C++, C#.'),
    ('.NET', 'C++; C#; .NET.'),
])
def test_terminal_sentence_punctuation_is_tolerated_without_erasing_technical_punctuation(requirement, content):
    item = _match(requirement, content).requirements[0]
    assert item.status == 'supported'
    assert item.requirement_text == requirement
    assert item.components[0].text == requirement


@pytest.mark.parametrize('requirement, content', [
    ('Python, SQL', 'Python SQL development.'),
    ('Python; SQL', 'Python SQL development.'),
    ('Python 3.12', 'Python 312 development.'),
    ('Node.js', 'Node-js development.'),
])
def test_internal_punctuation_is_not_broadly_removed(requirement, content):
    assert _match(requirement, content).requirements[0].status == 'no_evidence_found'


@pytest.mark.parametrize('content', ['Not experienced in Python.', 'Interested in Python.', 'Used Python in a project.'])
def test_lexical_mentions_always_retain_the_explicit_human_review_limitation(content):
    result = _match('Python', content)
    assert result.requirements[0].status == 'supported'
    assert result.human_review_required is True
    assert result.source_job_interpretation_status == 'ai_generated_unverified'
    assert any('Negated, hypothetical, and interest-only mentions' in limitation for limitation in result.limitations)


@pytest.mark.parametrize('name', ['Python and SQL', 'Python AND SQL', 'Python & SQL', 'Python / SQL', 'Python | SQL'])
def test_all_compound_components_supported_by_individually_traceable_evidence(name):
    catalog = synthetic_candidate_catalog([
        _record('Python development.', identifier='python'),
        _record('SQL databases.', identifier='sql'),
    ])
    item = CandidateJobMatcher(catalog).assess(_analysis(name)).requirements[0]
    assert item.status == 'supported'
    assert item.matched_evidence_ids == ['python', 'sql']
    assert [component.text for component in item.components] == ['Python', 'SQL']
    assert [component.matched_evidence_ids for component in item.components] == [['python'], ['sql']]
    assert [reference.evidence_id for reference in item.matched_evidence] == ['python', 'sql']
    assert item.unmatched_components == []


@pytest.mark.parametrize('name', ['R and/or Python', 'Python/SQL', 'Python // SQL', 'Python / / SQL', 'Python / ++'])
def test_ambiguous_or_nonstandalone_separators_are_not_parsed_into_false_components(name):
    item = _match(name, 'R, Python and SQL development in C++.').requirements[0]
    assert [component.text for component in item.components] == [name]
    assert item.status == 'no_evidence_found'
    assert item.matched_evidence_ids == []


@pytest.mark.parametrize('name', ['and', 'or', 'and/or'])
def test_connector_words_cannot_independently_supply_qualification_evidence(name):
    assert _match(name, 'Work and/or study, or research.').requirements[0].status == 'no_evidence_found'


def test_partial_components_are_raw_source_phrases_and_respect_unassessable_coverage():
    result = _match('Python / SQL', 'Python development.', unsupported_content_count=1)
    item = result.requirements[0]
    assert item.status == 'partially_supported'
    assert [(component.text, component.status) for component in item.components] == [
        ('Python', 'supported'), ('SQL', 'not_assessable'),
    ]
    assert item.unmatched_components == ['SQL']
    assert item.matched_evidence_ids == ['e-1']


def test_duplicate_explicit_skill_components_do_not_duplicate_evidence():
    item = _match('Python and Python', 'Python development.').requirements[0]
    assert item.status == 'supported'
    assert [component.text for component in item.components] == ['Python']
    assert item.matched_evidence_ids == ['e-1']


def test_all_requirement_categories_preserve_wording_quotations_and_original_classifications():
    payload = grounded_job_payload()
    analysis = JobDescriptionAnalysis.model_validate({**payload, 'prompt_version': '2.0.0'})
    catalog = synthetic_candidate_catalog([
        _record('Python and Kubernetes development.', identifier='skills'),
        _record('Design and maintain Python services for production.', identifier='responsibility', evidence_type=EvidenceType.EXPERIENCE),
        _record('5 years of experience with Python in production.', identifier='experience', evidence_type=EvidenceType.EXPERIENCE),
        _record('Bachelor degree in Computer Science with honors.', identifier='education', evidence_type=EvidenceType.EDUCATION),
        _record('AWS Certified Developer.', identifier='certification', evidence_type=EvidenceType.CERTIFICATION),
        _record('English (fluent).', identifier='language', evidence_type=EvidenceType.LANGUAGE),
    ])
    result = CandidateJobMatcher(catalog).assess(analysis)
    assert len(result.requirements) == 7
    assert all(item.status == 'supported' for item in result.requirements)
    for item in result.requirements:
        source_item = getattr(analysis, item.category)[item.source_index]
        expected_text = getattr(source_item, 'name', getattr(source_item, 'text', getattr(source_item, 'language', None)))
        assert item.requirement_text == expected_text
        assert item.source_quotation == source_item.quotation
        assert item.classification_status == 'ai_generated_unverified'
        if item.category == 'required_skills':
            assert item.classification == 'required'
        elif item.category == 'preferred_skills':
            assert item.classification == 'preferred'
        else:
            assert item.classification is None
    assert result.source_job_prompt_version == '2.0.0'
    assert result.source_job_grounding_method == 'exact_substrings_only'


def test_uncertain_skill_classification_is_not_upgraded():
    item = CandidateJobMatcher(synthetic_candidate_catalog([_record()])).assess(
        _analysis(category='uncertain_skills'),
    ).requirements[0]
    assert item.status == 'supported'
    assert item.classification == 'uncertain'
    assert item.classification_status == 'ai_generated_unverified'


@pytest.mark.parametrize('coverage, expected_coverage, warning', [
    ({}, 'assessed', None),
    ({'coverage_assessed': False}, 'not_assessable', 'coverage_not_assessed'),
    ({'unsupported_content_count': 2}, 'incomplete', 'unsupported_content_present'),
    ({'unresolved_extraction_details': ['SYNTHETIC_UNRESOLVED']}, 'not_assessable', 'unresolved_extraction_details_present'),
    ({'unclassified_block_indexes': [4]}, 'incomplete', 'unclassified_blocks_present'),
    ({'sections_detected': 0}, 'incomplete', 'section_classification_incomplete'),
    ({'warnings': ['SYNTHETIC_EXTRACTION_WARNING']}, 'incomplete', 'extraction_warnings_present'),
])
def test_every_actual_coverage_field_influences_negative_assessability(coverage, expected_coverage, warning):
    result = _match('Kubernetes', 'Python development.', **coverage)
    assert result.coverage_status == expected_coverage
    assert result.requirements[0].status == ('no_evidence_found' if expected_coverage == 'assessed' else 'not_assessable')
    assert result.requirements[0].matched_evidence_ids == []
    if warning is None:
        assert result.coverage_warnings == []
    else:
        assert warning in result.coverage_warnings
    assert 'SYNTHETIC_UNRESOLVED' not in result.model_dump_json()
    assert 'SYNTHETIC_EXTRACTION_WARNING' not in result.model_dump_json()


@pytest.mark.parametrize('coverage', [
    {'coverage_assessed': False}, {'unsupported_content_count': 2},
    {'unresolved_extraction_details': ['unresolved']}, {'unclassified_block_indexes': [4]},
    {'sections_detected': 0}, {'warnings': ['coverage warning']},
])
def test_positive_evidence_is_preserved_despite_coverage_gaps(coverage):
    result = _match(**coverage)
    assert result.coverage_status != 'assessed'
    assert result.coverage_warnings
    assert result.requirements[0].status == 'supported'
    assert result.requirements[0].matched_evidence_ids == ['e-1']


def test_coverage_warnings_are_accumulated_instead_of_hidden_by_the_first_gap():
    result = _match(
        'Kubernetes', 'Python.', coverage_assessed=False, unsupported_content_count=2,
        unresolved_extraction_details=['unresolved'], unclassified_block_indexes=[5], warnings=['warning'],
    )
    assert set(result.coverage_warnings) == {
        'coverage_not_assessed', 'source_preservation_incomplete', 'unsupported_content_present',
        'unresolved_extraction_details_present', 'section_classification_incomplete',
        'unclassified_blocks_present', 'extraction_warnings_present',
    }
    assert result.requirements[0].status == 'not_assessable'


@pytest.mark.parametrize('reported_count', [0, 17])
def test_assessed_but_empty_catalog_is_not_a_negative_candidate_claim(reported_count):
    catalog = synthetic_candidate_catalog([], sections_detected=1)
    assert catalog.extraction_completeness.complete is True
    catalog.extraction_completeness.evidence_records = reported_count
    result = CandidateJobMatcher(catalog).assess(_analysis('Kubernetes'))
    assert result.candidate_evidence_record_count == 0
    assert result.coverage_status == 'not_assessable'
    assert 'empty_evidence_catalog' in result.coverage_warnings
    assert result.requirements[0].status == 'not_assessable'


def test_incorrect_summary_count_cannot_make_extraction_coverage_reliable():
    catalog = synthetic_candidate_catalog([_record()])
    catalog.extraction_completeness.evidence_records = 999
    result = CandidateJobMatcher(catalog).assess(_analysis('Kubernetes'))
    assert result.candidate_evidence_record_count == 1
    assert 'evidence_record_count_mismatch' in result.coverage_warnings
    assert result.requirements[0].status == 'not_assessable'


def test_missing_evidence_text_is_a_coverage_warning_not_a_negative_qualification_claim():
    result = _match('Kubernetes', '')
    assert result.coverage_status == 'incomplete'
    assert 'evidence_text_missing' in result.coverage_warnings
    assert result.requirements[0].status == 'not_assessable'


@pytest.mark.parametrize('field, value', [
    ('complete', False), ('coverage_assessed', False),
    ('source_preservation_complete', False), ('section_classification_complete', False),
])
def test_mutated_contradictory_coverage_is_revalidated_before_matching(field, value):
    catalog = synthetic_candidate_catalog([_record()])
    catalog.extraction_completeness = catalog.extraction_completeness.model_copy(update={field: value})
    with pytest.raises(ValidationError):
        CandidateJobMatcher(catalog).assess(_analysis())


def _language_analysis(level='fluent'):
    payload = empty_job_payload()
    payload['languages'] = [{'language': 'English', 'level': level, 'quotation': f'English ({level}).'}]
    return JobDescriptionAnalysis.model_validate({**payload, 'prompt_version': '2.0.0'})


def test_language_and_level_in_one_record_are_supported_together():
    catalog = synthetic_candidate_catalog([_record('English (fluent).', evidence_type=EvidenceType.LANGUAGE)])
    item = CandidateJobMatcher(catalog).assess(_language_analysis()).requirements[0]
    assert item.status == 'supported'
    assert [component.matched_evidence_ids for component in item.components] == [['e-1'], ['e-1']]
    assert item.language_level == 'fluent'


@pytest.mark.parametrize('unrelated_type, unrelated_content', [
    (EvidenceType.PROFESSIONAL_SUMMARY, 'Fluent public speaker.'),
    (EvidenceType.LANGUAGE, 'French (fluent).'),
])
def test_language_proficiency_is_never_combined_across_unrelated_records(unrelated_type, unrelated_content):
    catalog = synthetic_candidate_catalog([
        _record('English (native).', identifier='english', evidence_type=EvidenceType.LANGUAGE),
        _record(unrelated_content, identifier='unrelated', evidence_type=unrelated_type),
    ])
    item = CandidateJobMatcher(catalog).assess(_language_analysis()).requirements[0]
    assert item.status == 'partially_supported'
    assert item.matched_evidence_ids == ['english']
    assert item.unmatched_components == ['fluent']
    assert [component.matched_evidence_ids for component in item.components] == [['english'], []]


def test_later_same_record_language_level_evidence_takes_precedence_over_language_only_evidence():
    catalog = synthetic_candidate_catalog([
        _record('English (native).', identifier='language-only', evidence_type=EvidenceType.LANGUAGE),
        _record('English (fluent).', identifier='full', evidence_type=EvidenceType.LANGUAGE),
    ])
    item = CandidateJobMatcher(catalog).assess(_language_analysis()).requirements[0]
    assert item.status == 'supported'
    assert item.matched_evidence_ids == ['full']
    assert [component.matched_evidence_ids for component in item.components] == [['full'], ['full']]


def test_generic_level_without_the_language_has_no_positive_support():
    catalog = synthetic_candidate_catalog([_record('Fluent public speaker.')])
    item = CandidateJobMatcher(catalog).assess(_language_analysis()).requirements[0]
    assert item.status == 'no_evidence_found'
    assert item.matched_evidence_ids == []


def test_language_without_a_requested_level_is_matched_as_the_language_only():
    catalog = synthetic_candidate_catalog([_record('English (native).', evidence_type=EvidenceType.LANGUAGE)])
    item = CandidateJobMatcher(catalog).assess(_language_analysis(None)).requirements[0]
    assert item.status == 'supported'
    assert len(item.components) == 1
    assert item.language_level is None


def test_same_name_distinct_source_quotations_and_contexts_get_unique_reproducible_ids():
    payload = empty_job_payload()
    payload['required_skills'] = [
        {'name': 'Python', 'quotation': 'Python for services.', 'classification_context': 'Required: Python for services.'},
        {'name': 'Python', 'quotation': 'Python for data.', 'classification_context': 'Required: Python for data.'},
        {'name': 'Python', 'quotation': 'Python for services.', 'classification_context': 'Also required: Python for services.'},
    ]
    analysis = JobDescriptionAnalysis.model_validate({**payload, 'prompt_version': '2.0.0'})
    matcher = CandidateJobMatcher(synthetic_candidate_catalog([_record()]))
    first, second = matcher.assess(analysis), matcher.assess(analysis)
    identifiers = [item.requirement_id for item in first.requirements]
    assert len(set(identifiers)) == 3
    assert all(len(identifier) == 64 for identifier in identifiers)
    assert [item.source_index for item in first.requirements] == [0, 1, 2]
    assert first.model_dump_json() == second.model_dump_json()
    assert matcher.validate_result(CandidateJobMatch.model_validate_json(first.model_dump_json()), analysis) == first


def test_duplicate_requirement_identifiers_are_rejected_by_final_output_validation():
    payload = empty_job_payload()
    payload['required_skills'] = [
        {'name': 'Python', 'quotation': quote, 'classification_context': quote}
        for quote in ('Python for services.', 'Python for data.')
    ]
    analysis = JobDescriptionAnalysis.model_validate({**payload, 'prompt_version': '2.0.0'})
    data = CandidateJobMatcher(synthetic_candidate_catalog([_record()])).assess(analysis).model_dump()
    data['requirements'][1]['requirement_id'] = data['requirements'][0]['requirement_id']
    with pytest.raises(ValidationError, match='must be unique'):
        CandidateJobMatch.model_validate(data)


def test_duplicate_identical_evidence_ids_are_collapsed_but_references_stay_unique():
    record = _record()
    catalog = synthetic_candidate_catalog([record, record.model_copy(deep=True)])
    result = CandidateJobMatcher(catalog).assess(_analysis('Python and Python'))
    assert result.candidate_evidence_record_count == 2
    assert result.requirements[0].matched_evidence_ids == ['e-1']
    assert len(result.requirements[0].matched_evidence) == 1


def test_repeated_mentions_with_distinct_evidence_ids_use_the_first_source_record_deterministically():
    catalog = synthetic_candidate_catalog([
        _record('Python project work.', identifier='first', evidence_type=EvidenceType.PROJECT),
        _record('Python experience.', identifier='second', evidence_type=EvidenceType.EXPERIENCE),
    ])
    matcher = CandidateJobMatcher(catalog)
    assert matcher.assess(_analysis()).requirements[0].matched_evidence_ids == ['first']
    assert matcher.assess(_analysis()).requirements[0].matched_evidence_ids == ['first']


@pytest.mark.parametrize('conflict', ['content', 'provenance'])
def test_conflicting_evidence_with_one_identifier_is_rejected_before_matching(conflict):
    first = _record()
    update = {'original_content': 'SQL databases.'} if conflict == 'content' else {'source_sha256': 'b' * 64}
    second = first.model_copy(update=update)
    catalog = synthetic_candidate_catalog([first, second])
    with pytest.raises(CandidateJobIntegrityError, match='conflicting records'):
        CandidateJobMatcher(catalog).assess(_analysis())


def test_table_provenance_and_component_references_survive_json_without_copying_candidate_content():
    location = SourceLocation(
        document_part='word/document.xml', body_block_index=7, table_index=-3,
        row_index=2, cell_index=None, nested_table_path=[7, 1, 2],
    )
    cell = TableCellSource(
        table_index=-3, row_index=2, cell_index=1,
        paragraph_positions=[0, 2], nested_table_path=[7, 1, 2],
    )
    record = _record('Python SYNTHETIC_PRIVATE_CONTENT', identifier='table-evidence')
    record.source_location = location
    record.table_context = TableEvidenceContext(
        table_index=-3, row_index=2, column_count=2, cell_sources=[cell],
        cell_texts=['SYNTHETIC_PRIVATE_CELL', 'Python'], nested_table_path=[7, 1, 2],
    )
    record.normalized_content = 'SYNTHETIC_NORMALIZED_CONTENT'
    matcher = CandidateJobMatcher(synthetic_candidate_catalog([record]))
    analysis = _analysis()
    result = matcher.assess(analysis)
    serialized = result.model_dump_json()
    restored = CandidateJobMatch.model_validate_json(serialized)
    reference = restored.requirements[0].matched_evidence[0]
    assert reference.evidence_id == 'table-evidence'
    assert reference.source_document == record.source_document
    assert reference.source_sha256 == record.source_sha256
    assert reference.source_location.model_dump() == location.model_dump()
    assert [source.model_dump() for source in reference.cell_sources] == [cell.model_dump()]
    assert restored.requirements[0].components[0].matched_evidence_ids == ['table-evidence']
    assert matcher.validate_result(restored, analysis) == result
    assert 'SYNTHETIC_PRIVATE_CONTENT' not in serialized
    assert 'SYNTHETIC_PRIVATE_CELL' not in serialized
    assert 'SYNTHETIC_NORMALIZED_CONTENT' not in serialized
    assert 'original_content' not in serialized and 'cell_texts' not in serialized


def test_missing_optional_provenance_stays_null_instead_of_being_fabricated():
    record = _record()
    record.source_sha256 = None
    result = CandidateJobMatcher(synthetic_candidate_catalog([record])).assess(_analysis())
    reference = result.requirements[0].matched_evidence[0]
    assert reference.source_location is None
    assert reference.source_sha256 is None
    assert reference.cell_sources == []


def test_unknown_evidence_ids_fail_source_validation_after_structurally_valid_round_trip():
    analysis = _analysis()
    matcher = CandidateJobMatcher(synthetic_candidate_catalog([_record()]))
    data = matcher.assess(analysis).model_dump()
    item = data['requirements'][0]
    item['matched_evidence_ids'] = ['unknown-id']
    item['matched_evidence'][0]['evidence_id'] = 'unknown-id'
    item['components'][0]['matched_evidence_ids'] = ['unknown-id']
    tampered = CandidateJobMatch.model_validate(data)
    restored = CandidateJobMatch.model_validate_json(tampered.model_dump_json())
    with pytest.raises(CandidateJobIntegrityError, match='unknown candidate evidence identifier'):
        matcher.validate_result(restored, analysis)


@pytest.mark.parametrize('tamper', ['index', 'quotation', 'omitted_requirement', 'metadata'])
def test_incorrect_job_references_or_source_metadata_are_rejected(tamper):
    analysis = _analysis()
    matcher = CandidateJobMatcher(synthetic_candidate_catalog([_record()]))
    data = matcher.assess(analysis).model_dump()
    if tamper == 'index':
        data['requirements'][0]['source_index'] = 999
    elif tamper == 'quotation':
        data['requirements'][0]['source_quotation'] = 'An invented quotation mentioning Python.'
        data['requirements'][0]['classification_context'] = 'An invented quotation mentioning Python.'
    elif tamper == 'omitted_requirement':
        data['requirements'] = []
    else:
        data['candidate_source_sha256'] = 'b' * 64
    tampered = CandidateJobMatch.model_validate(data)
    with pytest.raises(CandidateJobIntegrityError, match='source artifacts'):
        matcher.validate_result(tampered, analysis)


def test_fabricated_source_locations_are_rejected_even_when_the_evidence_id_exists():
    record = _record()
    record.source_location = SourceLocation(body_block_index=3, paragraph_index=2)
    analysis = _analysis()
    matcher = CandidateJobMatcher(synthetic_candidate_catalog([record]))
    data = matcher.assess(analysis).model_dump()
    data['requirements'][0]['matched_evidence'][0]['source_location']['body_block_index'] = 999
    tampered = CandidateJobMatch.model_validate(data)
    with pytest.raises(CandidateJobIntegrityError, match='provenance does not agree'):
        matcher.validate_result(tampered, analysis)


def test_existing_but_nonmatching_evidence_cannot_be_substituted_for_a_positive_reference():
    matcher = CandidateJobMatcher(synthetic_candidate_catalog([
        _record('Python development.', identifier='python'), _record('SQL databases.', identifier='sql'),
    ]))
    analysis = _analysis()
    data = matcher.assess(analysis).model_dump()
    sql_reference = matcher.assess(_analysis('SQL')).requirements[0].matched_evidence[0].model_dump()
    item = data['requirements'][0]
    item['matched_evidence_ids'] = ['sql']
    item['components'][0]['matched_evidence_ids'] = ['sql']
    item['matched_evidence'] = [sql_reference]
    tampered = CandidateJobMatch.model_validate(data)
    with pytest.raises(CandidateJobIntegrityError, match='source artifacts'):
        matcher.validate_result(tampered, analysis)


@pytest.mark.parametrize('status', ['supported', 'partially_supported', 'no_evidence_found', 'not_assessable'])
def test_all_declared_requirement_statuses_are_accepted_for_consistent_real_results(status):
    if status == 'partially_supported':
        result = _match('Python and SQL')
    elif status == 'no_evidence_found':
        result = _match('Kubernetes')
    elif status == 'not_assessable':
        result = _match('Kubernetes', unsupported_content_count=1)
    else:
        result = _match()
    assert CandidateJobMatch.model_validate_json(result.model_dump_json()).requirements[0].status == status


@pytest.mark.parametrize('field, value', [
    ('status', 'invented_status'), ('classification_status', 'verified'),
    ('matching_method', 'exact_normalized_substring_v1'), ('source_index', '0'),
])
def test_requirement_output_rejects_invalid_statuses_methods_and_coercible_types(field, value):
    data = _match().requirements[0].model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        RequirementMatch.model_validate(data)


@pytest.mark.parametrize('field, value', [
    ('schema_version', '1.0.0'), ('coverage_status', 'invented_status'),
    ('candidate_evidence_schema_version', '2.0.0'), ('source_job_schema_version', '1.0.0'),
    ('human_review_required', False), ('candidate_evidence_record_count', '1'),
])
def test_final_output_rejects_invalid_versions_statuses_and_false_verification_indicators(field, value):
    data = _match().model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        CandidateJobMatch.model_validate(data)


def test_nested_provenance_rejects_unexpected_fields():
    record = _record()
    record.source_location = SourceLocation(body_block_index=3)
    data = CandidateJobMatcher(synthetic_candidate_catalog([record])).assess(_analysis()).model_dump()
    data['requirements'][0]['matched_evidence'][0]['source_location']['candidate_text'] = 'SYNTHETIC_CONTENT'
    with pytest.raises(ValidationError, match='extra_forbidden'):
        CandidateJobMatch.model_validate(data)
