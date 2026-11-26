'''Phase 4 offline tests: generation, grounding validation, DOCX, and CLI.'''

import hashlib
import json
import re
import stat
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from docx import Document
from docx.shared import Inches

from resume_assistant import cli, docx_renderer
from resume_assistant.generation_models import (
    DeterministicHeader,
    GroundingValidationReport,
    TailoredResumeArtifact,
    TailoredResumePayload,
)
from resume_assistant.llm_client import AgentRouterClient
from resume_assistant.models import (
    DocumentBlock,
    EvidenceRecord,
    EvidenceType,
    HyperlinkBlock,
    ParagraphBlock,
)
from resume_assistant.publication_reader import (
    load_candidate_evidence_input,
    load_committed_pair,
)
from resume_assistant.resume_generator import (
    ResumeGenerationError,
    _build_header,
    _build_llm_input,
    _numeric_tokens,
    _sanitize_repair_details,
    hydrate_plan_evidence,
    parse_tailored_resume_payload,
    validate_tailored_resume,
)
from resume_assistant.prompts import RESUME_GENERATION_PROMPT_VERSION
from resume_assistant.section_plan import ResumeSectionPlan, content_sha256
from tests.fixtures import builders
from tests.fixtures.jobs import SYNTHETIC_JOB_TEXT, grounded_job_payload

NAME = 'Synthetic Candidate'
CONTACT_EMAIL = 'synthetic-contact@example.invalid'
EXCLUDED_MARKER = 'SYNTHETIC_EXCLUDED_GENERATION_MARKER'
SUMMARY_MARKER = 'SYNTHETIC_GENERATION_SUMMARY_MARKER'


def _candidate_document():
    document = Document()
    # Preamble table: unclassified CONTACT and UNCLASSIFIED evidence. Both are
    # excluded from planning; the contact row feeds only the local header.
    preamble = document.add_table(rows=2, cols=1)
    preamble.cell(0, 0).text = f'Email: {CONTACT_EMAIL}'
    preamble.cell(1, 0).text = EXCLUDED_MARKER

    document.add_heading('1. Professional Identity', level=1)
    document.add_paragraph(NAME)
    document.add_paragraph(
        'Software engineer building local synthetic systems. ' + SUMMARY_MARKER
    )
    document.add_heading('2. Technical Profile', level=1)
    for skill in ('Python', 'SQL', 'Kubernetes'):
        document.add_paragraph(skill)

    document.add_heading('3. Professional Experience And Engineering Training', level=1)
    role = document.add_table(rows=2, cols=2)
    role.cell(0, 0).text = 'Original synthetic engineer at Example Organization'
    role.cell(0, 1).text = 'Original synthetic dates: 2021-2024'
    role.cell(1, 1).text = 'Design and maintain Python services.'
    role.cell(0, 0).merge(role.cell(1, 0))

    for number, heading, title, detail in (
        (4, 'Independent Software And AI Projects', 'Original synthetic service project',
         'Python service with SQL storage.'),
        (5, 'Education', 'Bachelor degree in Computer Science.',
         'Original synthetic course details.'),
        (6, 'Certifications And Professional Development', 'AWS Certified Developer',
         'Original synthetic qualification details.'),
    ):
        document.add_heading(f'{number}. {heading}', level=1)
        paragraph = document.add_paragraph(f'{number}.1. {title}')
        paragraph.runs[0].bold = True
        document.add_paragraph(detail)
    return document


class OfflineResponsesTransport:
    '''Real client/protocol parsing with deterministic synthetic wire responses.'''

    def __init__(self):
        self.calls = []
        self.response = grounded_job_payload()
        self.responses = []
        self.status = 'completed'

    def __call__(self, url, headers, payload, timeout):
        self.calls.append({'url': url, 'payload': payload, 'timeout': timeout})
        response = self.responses.pop(0) if self.responses else self.response
        response_text = response if isinstance(response, str) else json.dumps(response)
        envelope = {
            'status': self.status,
            'error': None,
            'incomplete_details': None,
            'output': [{
                'type': 'message',
                'role': 'assistant',
                'status': 'completed',
                'content': [{'type': 'output_text', 'text': response_text}],
            }],
        }
        return 200, json.dumps(envelope).encode('utf-8')


def _valid_payload(plan, catalog):
    records = {record.evidence_id: record for record in catalog.evidence_records}
    selected = set(plan.selected_evidence_ids)

    summary = []
    for statement in plan.professional_summary_blueprint.identity_statements:
        text = statement.statement_text.strip()
        if not text or text == NAME:
            continue
        summary.append({'text': text, 'evidence_ids': list(statement.evidence_ids)})
    if not summary:
        for section in plan.ordered_sections:
            if section.section_id == 'professional-summary' and section.selected_evidence_ids:
                identifier = section.selected_evidence_ids[0]
                summary.append({
                    'text': records[identifier].original_content.strip(),
                    'evidence_ids': [identifier],
                })
                break

    tier_names = {
        'required': 'Required Skills',
        'preferred': 'Preferred Skills',
        'other': 'Additional Skills',
    }
    grouped = {}
    for skill in plan.prioritized_skills:
        identifiers = [item for item in skill.matched_evidence_ids if item in selected]
        if not identifiers:
            continue
        grouped.setdefault(tier_names[skill.priority_tier], []).append({
            'skill_text': skill.skill_text,
            'evidence_ids': identifiers,
        })
    skill_groups = [
        {'group_name': group_name, 'skills': skills}
        for group_name, skills in grouped.items()
    ]

    sections = []
    for section in plan.ordered_sections:
        if not section.entries:
            continue
        entries = []
        for entry in section.entries:
            identifiers = list(entry.selected_evidence_ids)
            identity = entry.identity_evidence_id or identifiers[0]
            lines = [
                line.strip()
                for line in records[identity].original_content.splitlines()
                if line.strip()
            ]
            title = lines[0] if lines else records[identifiers[0]].original_content.strip()
            entries.append({
                'entry_id': entry.entry_id,
                'title': title,
                'meta': None,
                'bullets': [
                    {'text': records[identifier].original_content.strip(),
                     'evidence_ids': [identifier]}
                    for identifier in identifiers
                ],
            })
        sections.append({
            'section_id': section.section_id,
            'heading_text': section.heading_text,
            'entries': entries,
        })

    headline = plan.target_job_context.role_title or 'Backend Engineer'
    return {
        'headline_title': headline,
        'tagline': None,
        'summary_sentences': summary,
        'skill_groups': skill_groups,
        'sections': sections,
    }


def _alias_payload(data, plan, evidence_map=None, entry_map=None):
    evidence_map = evidence_map or {
        identifier: f'E{index}'
        for index, identifier in enumerate(plan.selected_evidence_ids, start=1)
    }
    entry_map = entry_map or {
        entry.entry_id: f'ENT{index}'
        for index, entry in enumerate(
            (entry for section in plan.ordered_sections for entry in section.entries),
            start=1,
        )
    }
    aliased = json.loads(json.dumps(data))
    for sentence in aliased['summary_sentences']:
        sentence['evidence_ids'] = [
            evidence_map.get(identifier, identifier)
            for identifier in sentence['evidence_ids']
        ]
    for group in aliased['skill_groups']:
        for item in group['skills']:
            item['evidence_ids'] = [
                evidence_map.get(identifier, identifier)
                for identifier in item['evidence_ids']
            ]
    for section in aliased['sections']:
        for entry in section['entries']:
            entry['entry_id'] = entry_map.get(entry['entry_id'], entry['entry_id'])
            for bullet in entry['bullets']:
                bullet['evidence_ids'] = [
                    evidence_map.get(identifier, identifier)
                    for identifier in bullet['evidence_ids']
                ]
    return aliased


@pytest.fixture
def pipeline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, 'PROJECT_ROOT', tmp_path)
    processed = tmp_path / 'data/private/processed'
    processed.mkdir(parents=True)
    source = builders.save(_candidate_document(), tmp_path / 'synthetic-master.docx')
    job_text = processed / 'synthetic-job.txt'
    job_text.write_text(SYNTHETIC_JOB_TEXT, encoding='utf-8')

    paths = {
        'source': source,
        'job_text': job_text,
        'candidate': processed / 'candidate.json',
        'analysis': processed / 'analysis.json',
        'match': processed / 'match.json',
        'plan': processed / 'plan.json',
        'resume_json': processed / 'tailored_resume.json',
        'resume_docx': processed / 'tailored_resume.docx',
    }
    transport = OfflineResponsesTransport()
    real_client = AgentRouterClient
    monkeypatch.setenv('AGENTROUTER_API_KEY', 'synthetic-test-key-not-real')
    monkeypatch.setenv('AGENTROUTER_BASE_URL', 'https://provider.example.invalid/v1')
    monkeypatch.setenv('AGENTROUTER_MODEL', 'synthetic-generation-model')
    monkeypatch.setenv('AGENTROUTER_TIMEOUT_SECONDS', '3')
    monkeypatch.setattr(
        cli, 'AgentRouterClient',
        lambda config: real_client(config, http_post=transport),
    )

    commands = {
        'extract': [
            'extract-master', '--master', str(source),
            '--output', str(paths['candidate']),
            '--snapshot-output', str(processed / 'snapshot-selector.json'),
        ],
        'analysis': [
            'analyze-job', '--input', str(job_text), '--output', str(paths['analysis']),
        ],
        'match': [
            'match-job', '--analysis', str(paths['analysis']),
            '--candidate-evidence', str(paths['candidate']),
            '--output', str(paths['match']),
        ],
        'plan': [
            'plan-sections', '--analysis', str(paths['analysis']),
            '--match', str(paths['match']),
            '--candidate-evidence', str(paths['candidate']),
            '--output', str(paths['plan']),
        ],
        'generate': [
            'generate-resume', '--plan', str(paths['plan']),
            '--candidate-evidence', str(paths['candidate']),
            '--output-json', str(paths['resume_json']),
            '--output-docx', str(paths['resume_docx']),
        ],
    }

    assert cli.main(commands['extract']) == 0
    assert cli.main(commands['analysis']) == 0
    assert cli.main(commands['match']) == 0
    assert cli.main(commands['plan']) == 0

    plan = ResumeSectionPlan.model_validate_json(paths['plan'].read_bytes())
    catalog, snapshot, manifest = load_candidate_evidence_input(
        cli.PROJECT_ROOT, paths['candidate']
    )
    transport.response = _valid_payload(plan, catalog)
    capsys.readouterr()
    return paths, commands, transport, plan, catalog, snapshot, manifest


def _loaded(plan_path, candidate_path):
    plan = ResumeSectionPlan.model_validate_json(plan_path.read_bytes())
    catalog, snapshot, manifest = load_candidate_evidence_input(
        cli.PROJECT_ROOT, candidate_path
    )
    return plan, catalog, snapshot, manifest


def _evidence_record(
    content,
    *,
    section='professional-identity',
    evidence_type=EvidenceType.PROFESSIONAL_SUMMARY,
    hyperlinks=None,
):
    return EvidenceRecord(
        evidence_id=hashlib.sha256(content.encode('utf-8')).hexdigest(),
        evidence_type=evidence_type,
        original_content=content,
        section_reference=section,
        hyperlinks=hyperlinks or [],
    )


def _replace_record_content(plan, catalog, identifier, content):
    records = [
        record.model_copy(update={'original_content': content})
        if record.evidence_id == identifier else record
        for record in catalog.evidence_records
    ]
    updated_catalog = catalog.model_copy(update={'evidence_records': records})
    provenance = [
        reference.model_copy(update={'content_sha256': content_sha256(content)})
        if reference.evidence_id == identifier else reference
        for reference in plan.evidence_provenance
    ]
    updated_plan = plan.model_copy(update={'evidence_provenance': provenance})
    return updated_plan, updated_catalog


def test_generate_resume_end_to_end_produces_validated_json_and_docx(pipeline):
    paths, commands, transport, plan, catalog, _snapshot, _manifest = pipeline

    assert cli.main(commands['generate']) == 0

    artifact = TailoredResumeArtifact.model_validate_json(paths['resume_json'].read_bytes())
    assert artifact.schema_version == '1.0.0'
    assert artifact.plan_schema_version == '2.0.0'
    assert artifact.model == 'synthetic-generation-model'
    assert artifact.header.full_name == NAME
    assert any(CONTACT_EMAIL in line for line in artifact.header.contact_lines)
    assert artifact.header.source_evidence_ids
    assert artifact.validation.traceability_verified is True
    assert artifact.validation.semantic_entailment_verified is False
    assert artifact.validation.human_review_required is True

    assert stat.S_IMODE(paths['resume_json'].stat().st_mode) == 0o600
    assert stat.S_IMODE(paths['resume_docx'].stat().st_mode) == 0o600

    generation_request = transport.calls[1]['payload']
    request_text = json.dumps(generation_request)
    assert CONTACT_EMAIL not in request_text
    assert EXCLUDED_MARKER not in request_text
    assert 'Email:' not in generation_request['input']
    assert 'Python' in generation_request['input']
    assert EXCLUDED_MARKER not in paths['resume_json'].read_text()

    document = Document(paths['resume_docx'])
    assert len(document.tables) == 0
    assert '<w:drawing' not in document.element.xml
    style_names = {paragraph.style.name for paragraph in document.paragraphs}
    assert {
        'Resume Name', 'Resume Title', 'Resume Contact', 'Resume Section Heading',
        'Resume Subheading', 'Resume Job Title', 'Resume Project Title',
        'Resume Body', 'List Bullet',
    } <= style_names

    texts = [paragraph.text for paragraph in document.paragraphs]
    assert NAME in texts
    assert any(CONTACT_EMAIL in text for text in texts)

    headings = [
        paragraph.text for paragraph in document.paragraphs
        if paragraph.style.name == 'Resume Section Heading'
    ]
    expected_headings = ['SUMMARY', 'TECHNICAL SKILLS'] + [
        section.heading_text for section in plan.ordered_sections if section.entries
    ]
    assert headings == expected_headings

    records = {record.evidence_id: record for record in catalog.evidence_records}
    experience = next(
        section for section in plan.ordered_sections
        if section.section_id == 'professional-experience'
    )
    expected_titles = []
    for entry in experience.entries:
        identity = entry.identity_evidence_id or entry.selected_evidence_ids[0]
        expected_titles.append(records[identity].original_content.splitlines()[0].strip())
    job_titles = [
        paragraph.text for paragraph in document.paragraphs
        if paragraph.style.name == 'Resume Job Title'
    ]
    assert job_titles == expected_titles


def test_generate_resume_rejects_plan_provenance_hash_mismatch(pipeline):
    paths, commands, transport, *_ = pipeline
    data = json.loads(paths['plan'].read_text())
    target = data['selected_evidence_ids'][0]
    for reference in data['evidence_provenance']:
        if reference['evidence_id'] == target:
            reference['content_sha256'] = hashlib.sha256(
                b'not the original evidence content'
            ).hexdigest()
            break
    paths['plan'].write_text(json.dumps(data), encoding='utf-8')

    calls = len(transport.calls)
    with patch.object(cli.PrivateOutput, 'write_json') as writer:
        assert cli.main(commands['generate']) == 2
    writer.assert_not_called()
    assert len(transport.calls) == calls
    assert not paths['resume_json'].exists()
    assert not paths['resume_docx'].exists()


def test_grounding_rejects_fabricated_evidence_id(pipeline):
    paths, *_ = pipeline
    plan, catalog, _snapshot, _manifest = _loaded(paths['plan'], paths['candidate'])
    data = _valid_payload(plan, catalog)
    data['summary_sentences'][0]['evidence_ids'] = ['f' * 64]
    payload = TailoredResumePayload.model_validate(data)

    report = validate_tailored_resume(payload, plan, catalog, raise_on_error=False)
    assert report.traceability_verified is False
    assert any(finding.code == 'evidence_not_selected' for finding in report.findings)
    with pytest.raises(ResumeGenerationError):
        validate_tailored_resume(payload, plan, catalog)


def test_grounding_rejects_entry_scope_violation(pipeline):
    paths, *_ = pipeline
    plan, catalog, _snapshot, _manifest = _loaded(paths['plan'], paths['candidate'])
    data = _valid_payload(plan, catalog)

    section = next(section for section in plan.ordered_sections if section.entries)
    entry = section.entries[0]
    outside = [
        identifier for identifier in plan.selected_evidence_ids
        if identifier not in set(entry.selected_evidence_ids)
    ]
    assert outside
    data['sections'][0]['entries'][0]['bullets'][0]['evidence_ids'] = [outside[0]]
    payload = TailoredResumePayload.model_validate(data)

    report = validate_tailored_resume(payload, plan, catalog, raise_on_error=False)
    assert report.traceability_verified is False
    assert any(finding.code == 'entry_evidence_out_of_scope' for finding in report.findings)


def test_grounding_rejects_unplanned_skill_and_skill_scope(pipeline):
    paths, *_ = pipeline
    plan, catalog, _snapshot, _manifest = _loaded(paths['plan'], paths['candidate'])

    fabricated = _valid_payload(plan, catalog)
    fabricated['skill_groups'][0]['skills'][0]['skill_text'] = 'Fabricated Skill'
    payload = TailoredResumePayload.model_validate(fabricated)
    report = validate_tailored_resume(payload, plan, catalog, raise_on_error=False)
    assert report.traceability_verified is False
    assert any(finding.code == 'skill_not_planned' for finding in report.findings)

    scoped = _valid_payload(plan, catalog)
    planned = plan.prioritized_skills[0]
    outside = [
        identifier for identifier in plan.selected_evidence_ids
        if identifier not in set(planned.matched_evidence_ids)
    ]
    assert outside
    scoped['skill_groups'][0]['skills'][0]['evidence_ids'] = [outside[0]]
    payload = TailoredResumePayload.model_validate(scoped)
    report = validate_tailored_resume(payload, plan, catalog, raise_on_error=False)
    assert report.traceability_verified is False
    assert any(finding.code == 'skill_evidence_out_of_scope' for finding in report.findings)


def test_ungrounded_metrics_warn_and_strict_mode_fails(pipeline):
    paths, *_ = pipeline
    plan, catalog, _snapshot, _manifest = _loaded(paths['plan'], paths['candidate'])
    data = _valid_payload(plan, catalog)
    data['summary_sentences'][0]['text'] = 'Delivered 42% improvements in 2020.'
    payload = TailoredResumePayload.model_validate(data)

    report = validate_tailored_resume(payload, plan, catalog, raise_on_error=False)
    assert report.traceability_verified is True
    assert report.semantic_entailment_verified is False
    assert report.human_review_required is True
    metric_findings = [f for f in report.findings if f.code == 'ungrounded_metric']
    assert metric_findings and metric_findings[0].severity == 'warning'

    with pytest.raises(ResumeGenerationError):
        validate_tailored_resume(payload, plan, catalog, strict_metrics=True)


def test_cli_strict_metrics_fails_without_artifacts(pipeline):
    paths, commands, transport, plan, catalog, _snapshot, _manifest = pipeline
    data = _valid_payload(plan, catalog)
    data['summary_sentences'][0]['text'] = 'Delivered 42% improvements in 2020.'
    transport.response = data

    assert cli.main(commands['generate'] + ['--strict-metrics']) == 2
    assert not paths['resume_json'].exists()
    assert not paths['resume_docx'].exists()

    assert cli.main(commands['generate']) == 0
    artifact = TailoredResumeArtifact.model_validate_json(paths['resume_json'].read_bytes())
    assert artifact.validation.traceability_verified is True
    assert any(
        finding.code == 'ungrounded_metric' and finding.severity == 'warning'
        for finding in artifact.validation.findings
    )


def test_cli_alias_protection_and_exit_codes(pipeline):
    paths, commands, transport, plan, catalog, _snapshot, manifest = pipeline
    base = [
        'generate-resume', '--plan', str(paths['plan']),
        '--candidate-evidence', str(paths['candidate']),
    ]
    generation_dir = cli.publication_store.generation_dir_path(
        cli.PROJECT_ROOT, manifest.bundle_key, manifest.generation_id
    )

    assert cli.main(base + [
        '--output-json', str(paths['plan']),
        '--output-docx', str(paths['resume_docx']),
    ]) == 2
    assert cli.main(base + [
        '--output-json', str(paths['resume_json']),
        '--output-docx', str(paths['resume_json']),
    ]) == 2
    assert cli.main(base + [
        '--output-json', str(paths['candidate']),
        '--output-docx', str(paths['resume_docx']),
    ]) == 2
    assert cli.main(base + [
        '--output-json', str(generation_dir / 'catalog.json'),
        '--output-docx', str(paths['resume_docx']),
    ]) == 2
    assert not paths['resume_json'].exists()
    assert not paths['resume_docx'].exists()

    with patch.object(cli.PrivateOutput, 'write_bytes', side_effect=OSError('synthetic')):
        assert cli.main(commands['generate']) == 3

    transport.status = 'incomplete'
    paths['resume_json'].unlink(missing_ok=True)
    paths['resume_docx'].unlink(missing_ok=True)
    assert cli.main(commands['generate']) == 2
    assert not paths['resume_docx'].exists()


def test_parse_payload_fence_and_ambiguous_json():
    valid = {
        'headline_title': 'Engineer',
        'tagline': None,
        'summary_sentences': [],
        'skill_groups': [],
        'sections': [],
    }
    fenced = '```json\n' + json.dumps(valid) + '\n```'
    assert parse_tailored_resume_payload(fenced).headline_title == 'Engineer'

    with pytest.raises(ResumeGenerationError):
        parse_tailored_resume_payload('{"headline_title": "A", "headline_title": "B"}')
    with pytest.raises(ResumeGenerationError):
        parse_tailored_resume_payload('{"headline_title": NaN}')
    with pytest.raises(ResumeGenerationError):
        parse_tailored_resume_payload('["not", "an", "object"]')


def test_header_uses_snapshot_headers_footers_and_hyperlinks():
    header = ParagraphBlock(
        index=0,
        text='Header Name\nEmail: header@example.invalid',
        hyperlinks=[HyperlinkBlock(
            text='Portfolio',
            url='https://candidate.example.invalid/portfolio',
        )],
    )
    footer = ParagraphBlock(index=0, text='Phone: +1 (555) 010-9999')
    snapshot = SimpleNamespace(
        headers=[DocumentBlock(type='paragraph', index=0, paragraph=header)],
        footers=[DocumentBlock(type='paragraph', index=0, paragraph=footer)],
    )
    result = _build_header(SimpleNamespace(evidence_records=[]), snapshot)
    assert result.full_name == 'Header Name'
    assert any('header@example.invalid' in line for line in result.contact_lines)
    assert any('candidate.example.invalid' in line for line in result.contact_lines)
    assert any('555' in line for line in result.contact_lines)


def test_header_extracts_professional_identity_contacts_and_hyperlinks():
    name_record = _evidence_record('Ada Lovelace')
    contact_record = _evidence_record(
        'Email: ada@example.invalid\n'
        'Phone: +1 (555) 010-9999\n'
        'LinkedIn\n'
        'https://github.com/ada\n'
        'ada.github.io',
        hyperlinks=[HyperlinkBlock(
            text='LinkedIn', url='https://www.linkedin.com/in/ada',
        )],
    )
    title_record = _evidence_record('Senior Software Engineer with Python experience')
    header = _build_header(SimpleNamespace(
        evidence_records=[title_record, contact_record, name_record],
    ))

    assert header.full_name == 'Ada Lovelace'
    assert any('ada@example.invalid' in line for line in header.contact_lines)
    assert any('555' in line for line in header.contact_lines)
    assert any('linkedin.com/in/ada' in line for line in header.contact_lines)
    assert any('github.com/ada' in line for line in header.contact_lines)
    assert any('ada.github.io' in line for line in header.contact_lines)
    assert sum(
        'linkedin.com/in/ada' in line for line in header.contact_lines
    ) == 1


def test_header_does_not_duplicate_already_visible_hyperlink_targets():
    record = _evidence_record(
        'LinkedIn: linkedin.com/in/ada\nPortfolio: ada.dev/me',
        hyperlinks=[
            HyperlinkBlock(text='LinkedIn', url='https://www.linkedin.com/in/ada/'),
            HyperlinkBlock(text='Portfolio', url='https://ada.dev/me/'),
        ],
    )
    header = _build_header(SimpleNamespace(evidence_records=[record]))

    assert header.contact_lines == [
        'LinkedIn: linkedin.com/in/ada',
        'Portfolio: ada.dev/me',
    ]


@pytest.mark.parametrize('line, expected', [
    ('José Núñez', 'José Núñez'),
    ('Jean-Luc Picard', 'Jean-Luc Picard'),
    ('Ada Lovelace, PhD', 'Ada Lovelace, PhD'),
    ('Ada Lovelace, Software Engineer', 'Ada Lovelace'),
])
def test_header_name_detection_handles_unicode_hyphens_and_credentials(line, expected):
    header = _build_header(SimpleNamespace(evidence_records=[
        _evidence_record(line, section=None, evidence_type=EvidenceType.UNCLASSIFIED),
        _evidence_record('Senior Software Engineer'),
    ]))
    assert header.full_name == expected


def test_header_allows_preamble_mononym_and_rejects_organization_lines():
    header = _build_header(SimpleNamespace(evidence_records=[
        _evidence_record(
            'Acme Corp', section=None, evidence_type=EvidenceType.UNCLASSIFIED,
        ),
        _evidence_record(
            'GitHub', section=None, evidence_type=EvidenceType.UNCLASSIFIED,
        ),
        _evidence_record(
            'Madonna', section=None, evidence_type=EvidenceType.UNCLASSIFIED,
        ),
        _evidence_record('Senior Software Engineer'),
    ]))
    assert header.full_name == 'Madonna'


def test_llm_input_scrubs_inline_pii_and_excludes_pii_only_records(pipeline):
    paths, *_ = pipeline
    plan, catalog, _snapshot, _manifest = _loaded(paths['plan'], paths['candidate'])
    records = {record.evidence_id: record for record in catalog.evidence_records}
    identifier = next(
        item for item in plan.selected_evidence_ids
        if records[item].evidence_type == EvidenceType.PROFESSIONAL_SUMMARY
    )

    records[identifier].original_content = (
        'Engineer building local systems. Email: leak@example.invalid'
    )
    text, aliases, _entry_aliases = _build_llm_input(plan, records)
    assert 'leak@example.invalid' not in text
    assert 'Engineer building local systems.' in text
    assert identifier in aliases

    records[identifier].original_content = (
        'Portfolio: https://candidate.example.invalid/private'
    )
    text, aliases, _entry_aliases = _build_llm_input(plan, records)
    assert 'candidate.example.invalid' not in text
    assert identifier not in aliases

    records[identifier].original_content = (
        'Portfolio: https://candidate.example.invalid/private\n'
        'Phone: +1 (555) 010-9999'
    )
    text, aliases, _entry_aliases = _build_llm_input(plan, records)
    assert 'candidate.example.invalid' not in text
    assert '555' not in text
    assert identifier not in aliases

    records[identifier].original_content = 'Location: Berlin'
    text, aliases, _entry_aliases = _build_llm_input(plan, records)
    assert 'Berlin' not in text
    assert identifier not in aliases

    records[identifier].original_content = (
        'Engineer building local systems. Phone: +1 (555) 010-9999'
    )
    text, aliases, _entry_aliases = _build_llm_input(plan, records)
    assert '555' not in text
    assert 'Engineer building local systems.' in text
    assert identifier in aliases


def test_technical_skill_named_github_is_not_treated_as_contact_pii(pipeline):
    paths, *_ = pipeline
    plan, catalog, _snapshot, _manifest = _loaded(paths['plan'], paths['candidate'])
    records = {record.evidence_id: record for record in catalog.evidence_records}
    identifier = next(
        item for item in plan.selected_evidence_ids
        if records[item].evidence_type == EvidenceType.TECHNICAL_SKILL
    )
    records[identifier].original_content = 'GitHub'

    text, aliases, _entry_aliases = _build_llm_input(plan, records)
    assert 'GitHub' in text
    assert identifier in aliases


def test_validation_flags_generated_contact_pii(pipeline):
    paths, *_ = pipeline
    plan, catalog, _snapshot, _manifest = _loaded(paths['plan'], paths['candidate'])
    data = _valid_payload(plan, catalog)
    data['summary_sentences'][0]['text'] = 'Reach me at leak@example.invalid.'
    payload = TailoredResumePayload.model_validate(data)

    report = validate_tailored_resume(payload, plan, catalog, raise_on_error=False)
    assert report.traceability_verified is False
    assert any(finding.code == 'contact_pii' for finding in report.findings)


def test_validation_flags_generated_pii_in_all_text_fields(pipeline):
    paths, *_ = pipeline
    plan, catalog, _snapshot, _manifest = _loaded(paths['plan'], paths['candidate'])
    data = _valid_payload(plan, catalog)
    data['headline_title'] = 'Contact ada@example.invalid'
    data['tagline'] = 'Call +1 (555) 010-9999'
    data['skill_groups'][0]['group_name'] = 'Portfolio: https://ada.example.invalid'
    section = data['sections'][0]
    section['entries'][0]['title'] = 'Phone +1 (555) 010-9999'
    section['entries'][0]['meta'] = 'https://ada.example.invalid'
    section['entries'][0]['bullets'][0]['text'] = 'Email ada@example.invalid'
    payload = TailoredResumePayload.model_validate(data)

    report = validate_tailored_resume(payload, plan, catalog, raise_on_error=False)
    pii_locations = {
        finding.location
        for finding in report.findings
        if finding.code == 'contact_pii'
    }
    assert {
        'headline_title',
        'tagline',
        'skill_groups[0].group_name',
        'sections[0].entries[0].title',
        'sections[0].entries[0].meta',
        'sections[0].entries[0].bullets[0]',
    } <= pii_locations


def test_metric_tokenizer_preserves_decimals_abbreviations_and_versions():
    assert _numeric_tokens('$1.2M') == {'$1.2m'}
    assert _numeric_tokens('$1.3M') == {'$1.3m'}
    assert _numeric_tokens('3.5K') == {'3.5k'}
    assert _numeric_tokens('3.6K') == {'3.6k'}
    assert _numeric_tokens('version 3.12.14') == {'3.12.14'}
    assert _numeric_tokens('version 3.12.15') == {'3.12.15'}
    assert _numeric_tokens('2021-2024') == {'2021', '2024'}
    assert _numeric_tokens('V2') == set()
    assert _numeric_tokens('Python 3') == {'3'}


def test_abbreviated_and_decimal_metric_mismatch_is_ungrounded(pipeline):
    paths, *_ = pipeline
    plan, catalog, _snapshot, _manifest = _loaded(paths['plan'], paths['candidate'])
    records = {record.evidence_id: record for record in catalog.evidence_records}
    identifier = next(
        item for item in plan.selected_evidence_ids
        if records[item].evidence_type == EvidenceType.PROFESSIONAL_SUMMARY
    )
    updated_plan, updated_catalog = _replace_record_content(
        plan,
        catalog,
        identifier,
        'Delivered $1.2M revenue to 3.5K users at 99.9% uptime on version 3.12.14.',
    )
    data = _valid_payload(updated_plan, updated_catalog)
    data['summary_sentences'] = [{
        'text': (
            'Delivered $1.3M revenue to 3.6K users at 99.5% uptime '
            'on version 3.12.15.'
        ),
        'evidence_ids': [identifier],
    }]
    payload = TailoredResumePayload.model_validate(data)

    report = validate_tailored_resume(
        payload, updated_plan, updated_catalog, raise_on_error=False
    )
    assert report.traceability_verified is True
    metric_findings = [
        finding for finding in report.findings
        if finding.code == 'ungrounded_metric'
    ]
    assert metric_findings and metric_findings[0].severity == 'warning'
    with pytest.raises(ResumeGenerationError):
        validate_tailored_resume(
            payload, updated_plan, updated_catalog, strict_metrics=True
        )


def test_generation_sends_aliases_and_resolves_real_ids(pipeline):
    paths, commands, transport, plan, catalog, snapshot, manifest = pipeline
    hydrated = hydrate_plan_evidence(plan, catalog, snapshot, manifest)
    transport.response = _alias_payload(
        _valid_payload(plan, catalog),
        plan,
        hydrated.evidence_alias_by_id,
        hydrated.entry_alias_by_id,
    )

    assert cli.main(commands['generate']) == 0

    generation_input = transport.calls[1]['payload']['input']
    assert 'E1' in generation_input
    assert 'ENT1' in generation_input
    assert re.search(r'[0-9a-f]{64}', generation_input) is None

    artifact = TailoredResumeArtifact.model_validate_json(paths['resume_json'].read_bytes())
    cited = [
        identifier
        for sentence in artifact.content.summary_sentences
        for identifier in sentence.evidence_ids
    ]
    assert cited
    assert all(len(identifier) == 64 for identifier in cited)


def test_generation_repairs_once_after_schema_failure(pipeline):
    paths, commands, transport, plan, catalog, _snapshot, _manifest = pipeline
    transport.responses = [
        {'headline_title': ''},
        _valid_payload(plan, catalog),
    ]

    assert cli.main(commands['generate']) == 0
    assert len(transport.calls) == 3

    repair_input = transport.calls[2]['payload']['input']
    assert 'APPLICATION_REPAIR_FEEDBACK' in repair_input
    assert CONTACT_EMAIL not in repair_input
    assert EXCLUDED_MARKER not in repair_input
    assert paths['resume_json'].exists()
    assert paths['resume_docx'].exists()


def test_generation_repair_failure_returns_controlled_error(pipeline):
    paths, commands, transport, plan, catalog, _snapshot, _manifest = pipeline
    transport.responses = [
        {'headline_title': ''},
        {'headline_title': ''},
    ]

    assert cli.main(commands['generate']) == 2
    assert len(transport.calls) == 3
    assert not paths['resume_json'].exists()
    assert not paths['resume_docx'].exists()


@pytest.mark.parametrize('failure', ['invalid_json', 'unknown_alias'])
def test_generation_repairs_once_after_parse_or_alias_failure(pipeline, failure):
    paths, commands, transport, plan, catalog, snapshot, manifest = pipeline
    hydrated = hydrate_plan_evidence(plan, catalog, snapshot, manifest)
    valid = _alias_payload(
        _valid_payload(plan, catalog),
        plan,
        hydrated.evidence_alias_by_id,
        hydrated.entry_alias_by_id,
    )
    if failure == 'invalid_json':
        first = '{"headline_title": "Broken", '
    else:
        first = json.loads(json.dumps(valid))
        if first['summary_sentences']:
            first['summary_sentences'][0]['evidence_ids'] = ['E999']
        elif first['skill_groups']:
            first['skill_groups'][0]['skills'][0]['evidence_ids'] = ['E999']
        else:
            first['sections'][0]['entries'][0]['bullets'][0]['evidence_ids'] = ['E999']

    transport.responses = [first, valid]
    assert cli.main(commands['generate']) == 0
    assert len(transport.calls) == 3

    repair_input = transport.calls[2]['payload']['input']
    assert 'APPLICATION_REPAIR_FEEDBACK' in repair_input
    assert CONTACT_EMAIL not in repair_input
    assert EXCLUDED_MARKER not in repair_input
    assert re.search(r'[0-9a-f]{64}', repair_input) is None

    artifact = TailoredResumeArtifact.model_validate_json(paths['resume_json'].read_bytes())
    cited = [
        identifier
        for sentence in artifact.content.summary_sentences
        for identifier in sentence.evidence_ids
    ]
    assert cited
    assert all(len(identifier) == 64 for identifier in cited)
    assert all(identifier != 'E999' for identifier in cited)


def test_repair_feedback_redacts_pii_and_evidence_hashes():
    payload = 'a' * 64
    sanitized = _sanitize_repair_details([{
        'code': 'invalid',
        'location': f'evidence.{payload}.original_content',
        'message': f'Contact leak@example.invalid for {payload}',
    }])

    assert payload not in json.dumps(sanitized)
    assert 'leak@example.invalid' not in json.dumps(sanitized)
    assert sanitized[0]['location'].endswith('original_content')


def test_generation_repair_failure_after_invalid_json_returns_controlled_error(pipeline):
    paths, commands, transport, plan, catalog, _snapshot, _manifest = pipeline
    transport.responses = ['not valid json', 'still not valid json']

    assert cli.main(commands['generate']) == 2
    assert len(transport.calls) == 3
    assert not paths['resume_json'].exists()
    assert not paths['resume_docx'].exists()


def test_docx_skips_non_section_outputs_and_rejects_invalid_page_size(pipeline):
    paths, commands, transport, plan, catalog, _snapshot, _manifest = pipeline
    assert cli.main(commands['generate']) == 0
    artifact = TailoredResumeArtifact.model_validate_json(paths['resume_json'].read_bytes())

    evidence_id = artifact.content.summary_sentences[0].evidence_ids[0]
    skill_evidence_id = artifact.content.skill_groups[0].skills[0].evidence_ids[0]
    data = artifact.content.model_dump(mode='python')
    data['summary_sentences'] = [
        {'text': 'Summary text', 'evidence_ids': [evidence_id]}
    ]
    data['skill_groups'] = [{
        'group_name': 'Languages',
        'skills': [{'skill_text': 'Python', 'evidence_ids': [skill_evidence_id]}],
    }]
    data['sections'] = [
        {
            'section_id': 'professional-summary',
            'heading_text': 'SUMMARY',
            'entries': [{
                'entry_id': 'summary-entry',
                'title': 'Summary entry',
                'meta': None,
                'bullets': [{'text': 'Summary bullet', 'evidence_ids': [evidence_id]}],
            }],
        },
        {
            'section_id': 'technical-skills',
            'heading_text': 'TECHNICAL SKILLS',
            'entries': [{
                'entry_id': 'skills-entry',
                'title': 'Skills entry',
                'meta': None,
                'bullets': [{'text': 'Skills bullet', 'evidence_ids': [skill_evidence_id]}],
            }],
        },
    ]
    duplicate = artifact.model_copy(
        update={'content': TailoredResumePayload.model_validate(data)}
    )
    document = docx_renderer.build_document(duplicate)
    headings = [
        paragraph.text for paragraph in document.paragraphs
        if paragraph.style.name == 'Resume Section Heading'
    ]
    assert headings == ['SUMMARY', 'TECHNICAL SKILLS']

    with pytest.raises(docx_renderer.DocxRenderError):
        docx_renderer.resolve_page_size('Tabloid')


def test_invalid_page_size_returns_controlled_exit(pipeline):
    paths, commands, transport, plan, catalog, _snapshot, _manifest = pipeline
    with patch.object(
        docx_renderer,
        'render_docx_bytes',
        side_effect=docx_renderer.DocxRenderError('unsupported page size'),
    ):
        assert cli.main(commands['generate']) == 2
    assert not paths['resume_json'].exists()
    assert not paths['resume_docx'].exists()


def test_generation_supports_letter_page_size(pipeline):
    paths, commands, transport, plan, catalog, _snapshot, _manifest = pipeline
    assert cli.main(commands['generate'] + ['--page-size', 'Letter']) == 0
    document = Document(paths['resume_docx'])
    section = document.sections[0]
    assert section.page_width == Inches(8.5)
    assert section.page_height == Inches(11)


def test_generation_hydrates_catalog_without_manifest(pipeline):
    paths, *_ = pipeline
    plan, catalog, snapshot, _manifest = _loaded(paths['plan'], paths['candidate'])
    hydrated = hydrate_plan_evidence(plan, catalog, snapshot, None)
    assert hydrated.llm_input_text
    assert hydrated.header.full_name == NAME


def test_llm_input_handles_unresolved_source_span(pipeline):
    paths, *_ = pipeline
    plan, catalog, _snapshot, _manifest = _loaded(paths['plan'], paths['candidate'])
    records = {record.evidence_id: record for record in catalog.evidence_records}
    section = next(section for section in plan.ordered_sections if section.entries)
    entry = section.entries[0]
    unresolved = entry.model_copy(update={
        'identity_evidence_id': None,
        'grouping_basis': 'unresolved_source_span',
        'entry_id': entry.selected_evidence_ids[0],
    })
    updated_section = section.model_copy(update={
        'entries': [unresolved],
        'entry_grouping_complete': False,
    })
    updated_plan = plan.model_copy(update={
        'ordered_sections': [
            updated_section if item.section_id == section.section_id else item
            for item in plan.ordered_sections
        ],
    })

    text, _aliases, entry_aliases = _build_llm_input(updated_plan, records)
    assert unresolved.entry_id in entry_aliases
    assert 'identity_evidence_id' in text


def test_unresolved_entry_with_legacy_catalog_hydrates_and_renders_letter(pipeline):
    paths, *_ = pipeline
    plan, catalog, _snapshot, _manifest = _loaded(paths['plan'], paths['candidate'])
    standalone = paths['candidate'].parent / 'standalone_candidate.json'
    standalone.write_text(catalog.model_dump_json(indent=2), encoding='utf-8')
    loaded_catalog, loaded_snapshot, loaded_manifest = load_candidate_evidence_input(
        cli.PROJECT_ROOT, standalone
    )
    assert loaded_snapshot is None
    assert loaded_manifest is None

    section = next(section for section in plan.ordered_sections if section.entries)
    entry = section.entries[0]
    unresolved = entry.model_copy(update={
        'identity_evidence_id': None,
        'grouping_basis': 'unresolved_source_span',
        'entry_id': entry.selected_evidence_ids[0],
    })
    updated_section = section.model_copy(update={
        'entries': [unresolved],
        'entry_grouping_complete': False,
    })
    updated_plan = plan.model_copy(update={
        'ordered_sections': [
            updated_section if item.section_id == section.section_id else item
            for item in plan.ordered_sections
        ],
    })

    hydrated = hydrate_plan_evidence(
        updated_plan, loaded_catalog, loaded_snapshot, loaded_manifest
    )
    assert hydrated.header.full_name == NAME
    assert unresolved.entry_id in hydrated.entry_alias_by_id

    payload = TailoredResumePayload.model_validate(
        _valid_payload(updated_plan, loaded_catalog)
    )
    report = validate_tailored_resume(payload, updated_plan, loaded_catalog)
    artifact = TailoredResumeArtifact(
        prompt_version=RESUME_GENERATION_PROMPT_VERSION,
        plan_schema_version=updated_plan.schema_version,
        model='synthetic-generation-model',
        header=hydrated.header,
        content=payload,
        validation=report,
    )
    document = docx_renderer.build_document(artifact, page_size='Letter')
    page = document.sections[0]
    assert page.page_width == Inches(8.5)
    assert page.page_height == Inches(11)
