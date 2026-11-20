'''Deterministic candidate-to-job matching tests (Mission 3.2).'''

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from resume_assistant.job_models import JobDescriptionAnalysis
from resume_assistant.matching import CandidateJobMatcher, CandidateJobVersionError
from resume_assistant.matching_models import CandidateJobMatch
from resume_assistant.models import CandidateEvidenceCatalog, EvidenceType
from tests.fixtures.jobs import (
    grounded_job_payload,
    synthetic_candidate_catalog,
    synthetic_evidence,
)


def _analysis(payload=None) -> JobDescriptionAnalysis:
    document = dict(grounded_job_payload() if payload is None else payload)
    document['prompt_version'] = '2.0.0'
    return JobDescriptionAnalysis.model_validate(document)


def _catalog_with_skill(evidence_id: str = 'ev-skill-py', content: str = 'Senior engineer proficient in Python and SQL.') -> CandidateEvidenceCatalog:
    return synthetic_candidate_catalog([
        synthetic_evidence(evidence_id, EvidenceType.TECHNICAL_SKILL, content),
    ])


def test_exact_skill_match_is_supported_and_references_evidence():
    match = CandidateJobMatcher(_catalog_with_skill()).assess(_analysis())
    supported = [item for item in match.requirements if item.category == 'required_skills']
    assert len(supported) == 1
    item = supported[0]
    assert item.status == 'supported'
    assert item.matching_method == 'exact_normalized_phrase_v2'
    assert item.matched_evidence_ids == ['ev-skill-py']
    assert item.unmatched_components == []
    assert item.classification == 'required'
    assert item.classification_status == 'ai_generated_unverified'
    assert item.source_quotation is not None


def test_skill_match_is_case_insensitive_and_whitespace_normalized():
    payload = dict(grounded_job_payload())
    payload['required_skills'] = [{
        'name': 'PYTHON   development',
        'quotation': 'Required skills: PYTHON   development.',
        'classification_context': 'Required skills: PYTHON   development.',
    }]
    match = CandidateJobMatcher(_catalog_with_skill('ev-skill-py', 'Python\n\tdevelopment in a project.')).assess(_analysis(payload))
    item = [entry for entry in match.requirements if entry.category == 'required_skills'][0]
    assert item.status == 'supported'
    assert item.matching_method == 'exact_normalized_phrase_v2'


def test_technical_punctuation_is_preserved_for_lexical_matching():
    payload = dict(grounded_job_payload())
    payload['required_skills'] = [{
        'name': '.NET',
        'quotation': 'Required skills: .NET',
        'classification_context': 'Required skills: .NET',
    }]
    catalog = synthetic_candidate_catalog([
        synthetic_evidence('ev-skill-net', EvidenceType.TECHNICAL_SKILL, 'Experience with C++ and C# and .NET.'),
    ])
    match = CandidateJobMatcher(catalog).assess(_analysis(payload))
    item = [entry for entry in match.requirements if entry.category == 'required_skills'][0]
    assert item.status == 'supported'
    assert item.matched_evidence_ids == ['ev-skill-net']


def test_no_match_with_assessed_coverage_is_no_evidence_found():
    payload = dict(grounded_job_payload())
    payload['preferred_skills'] = [{
        'name': 'Kubernetes',
        'quotation': 'Preferred skills: Kubernetes.',
        'classification_context': 'Preferred skills: Kubernetes.',
    }]
    analysis = _analysis(payload)
    match = CandidateJobMatcher(_catalog_with_skill('ev-skill-py', 'Senior engineer proficient in Python.')).assess(analysis)
    item = [entry for entry in match.requirements if entry.category == 'preferred_skills'][0]
    assert item.status == 'no_evidence_found'
    assert item.matched_evidence_ids == []
    assert item.unmatched_components == ['Kubernetes']
    assert match.coverage_status == 'assessed'
    assert match.coverage_warnings == []


def test_no_match_with_incomplete_coverage_is_not_assessable():
    analysis = _analysis({
        **grounded_job_payload(),
        'required_skills': [{
            'name': 'Kubernetes',
            'quotation': 'Preferred skills: Kubernetes.',
            'classification_context': 'Preferred skills: Kubernetes.',
        }],
        'preferred_skills': [],
    })
    catalog = synthetic_candidate_catalog([], complete=False)
    match = CandidateJobMatcher(catalog).assess(analysis)
    item = [entry for entry in match.requirements if entry.category == 'required_skills'][0]
    assert item.status == 'not_assessable'
    assert match.coverage_status == 'not_assessable'
    assert match.coverage_warnings
    assert match.human_review_required is True


def test_partial_compound_skill_requirement_is_partially_supported():
    payload = dict(grounded_job_payload())
    payload['required_skills'] = [{
        'name': 'Python and SQL',
        'quotation': 'Required skills: Python and SQL.',
        'classification_context': 'Required skills: Python and SQL.',
    }]
    catalog = synthetic_candidate_catalog([
        synthetic_evidence('ev-skill-py', EvidenceType.TECHNICAL_SKILL, 'Senior engineer proficient in Python.'),
    ])
    match = CandidateJobMatcher(catalog).assess(_analysis(payload))
    item = [entry for entry in match.requirements if entry.category == 'required_skills'][0]
    assert item.status == 'partially_supported'
    assert item.matched_evidence_ids == ['ev-skill-py']
    assert item.unmatched_components == ['SQL']


def test_preferred_classification_and_ai_unverified_status_are_preserved():
    match = CandidateJobMatcher(_catalog_with_skill()).assess(_analysis())
    item = [entry for entry in match.requirements if entry.category == 'preferred_skills'][0]
    assert item.classification == 'preferred'
    assert item.classification_status == 'ai_generated_unverified'
    assert item.source_quotation is not None


def test_descriptive_metadata_is_not_treated_as_qualification():
    match = CandidateJobMatcher(_catalog_with_skill()).assess(_analysis())
    categories = {item.category for item in match.requirements}
    assert 'company_name' not in categories
    assert 'role_title' not in categories
    assert 'seniority_level' not in categories


def test_artifact_round_trip_and_strict_extra_field_rejection():
    match = CandidateJobMatcher(_catalog_with_skill()).assess(_analysis())
    restored = CandidateJobMatch.model_validate_json(match.model_dump_json())
    assert restored.model_dump() == match.model_dump()
    assert restored.source_job_schema_version == '2.0.0'
    assert restored.candidate_evidence_schema_version == '3.0.0'
    assert restored.source_job_interpretation_status == 'ai_generated_unverified'
    assert restored.schema_version == '2.0.0'
    with pytest.raises(ValidationError, match='extra_forbidden'):
        CandidateJobMatch.model_validate({**match.model_dump(), 'unexpected_field': 1})


def test_candidate_version_mismatch_is_rejected():
    catalog = _catalog_with_skill()
    catalog.schema_version = '2.0.0'
    with pytest.raises(CandidateJobVersionError):
        CandidateJobMatcher(catalog).assess(_analysis())


def test_matching_service_has_no_llm_or_provider_dependency():
    allowed = {
        'hashlib', 'json', 're', 'dataclasses', 'typing', 'pydantic',
        'resume_assistant.job_models', 'resume_assistant.models', 'resume_assistant.matching_models',
    }
    directory = Path(__file__).resolve().parents[1] / 'src' / 'resume_assistant'
    for filename in ('matching.py', 'matching_models.py'):
        tree = ast.parse((directory / filename).read_text(encoding='utf-8'))
        imported, unsafe_calls = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
            elif isinstance(node, ast.Call):
                function = node.func
                name = function.id if isinstance(function, ast.Name) else getattr(function, 'attr', '')
                if name in {'__import__', 'exec', 'eval', 'open', 'read_text', 'read_bytes', 'urlopen', 'connect'}:
                    unsafe_calls.add(name)
        assert imported <= allowed
        assert not unsafe_calls
