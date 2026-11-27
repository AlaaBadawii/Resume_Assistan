'''Offline tests for evidence-bound cover-letter generation and UI wiring.'''

from __future__ import annotations

import asyncio
import json
import re
import shutil
import stat
from pathlib import Path
from unittest.mock import patch

import pytest
from docx import Document

from resume_assistant import cli as cli_module
from resume_assistant import docx_renderer, pipeline as pipeline_module
from resume_assistant import ui as ui_module
from resume_assistant.cover_letter_generator import (
    CoverLetterGenerationError,
    CoverLetterGenerator,
    validate_cover_letter,
)
from resume_assistant.cover_letter_models import CoverLetterParagraph, CoverLetterPayload
from resume_assistant.filenames import build_cover_letter_stem
from resume_assistant.llm_client import AgentRouterClient, LLMConfig
from resume_assistant.prompts import (
    COVER_LETTER_GENERATION_INSTRUCTIONS,
    RESUME_GENERATION_INSTRUCTIONS,
)
from resume_assistant.resume_generator import hydrate_plan_evidence
from tests.fixtures.jobs import SYNTHETIC_JOB_TEXT as JOB_TEXT, grounded_job_payload
from tests.test_pipeline_and_ui import (
    _fake_pdf_writer,
    _grounded_generation_json_from_input,
)
from tests.test_resume_generation import (
    CONTACT_EMAIL,
    EXCLUDED_MARKER,
    NAME,
    OfflineResponsesTransport,
    _candidate_document,
    _loaded,
)

_WORD_PATTERN = re.compile(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*")


def _words(text: str) -> int:
    return len(_WORD_PATTERN.findall(text))


def _cover_letter_json_from_input(llm_input_text: str) -> str:
    bundle = json.loads(llm_input_text.split('\n\nAPPLICATION_REPAIR_FEEDBACK', 1)[0])
    job = bundle['target_job_context']
    company = job.get('company_name')
    role = job.get('role_title') or 'the role'
    salutation = (
        f'Dear {company} Hiring Team,' if company else 'Dear Hiring Team,'
    )

    evidence: list[tuple[str, str]] = []
    seen: set[str] = set()
    for section in bundle['ordered_sections']:
        candidates = list(section.get('evidence', []))
        for entry in section.get('entries', []):
            candidates.extend(entry.get('evidence', []))
        for item in candidates:
            identifier = item['evidence_id']
            if identifier not in seen:
                seen.add(identifier)
                evidence.append((identifier, item['original_content']))
    if not evidence:
        raise AssertionError('Synthetic bundle did not contain selected evidence.')

    first_id, first_text = evidence[0]
    second_id, second_text = evidence[min(1, len(evidence) - 1)]
    third_id, third_text = evidence[min(2, len(evidence) - 1)]
    body_ids = list(dict.fromkeys([second_id, third_id]))

    opening = (
        f'I am writing to apply for the {role} role at '
        f'{company or "your organization"}. My background includes {first_text}'
    )
    body = f'{second_text} I also bring {third_text}'
    closing = (
        "I would welcome the opportunity to discuss how my experience can "
        "support the team's goals."
    )
    filler = [
        'I am confident that my collaborative approach and delivery focus would be valuable to the team.',
        'I would welcome the chance to discuss how my background aligns with the role.',
        'Thank you for considering my application.',
    ]
    index = 0
    while _words(opening) + _words(body) + _words(closing) < 230:
        body += ' ' + filler[index % len(filler)]
        index += 1

    return json.dumps({
        'salutation': salutation,
        'opening_paragraph': {
            'text': opening,
            'source_evidence_ids': [first_id],
        },
        'body_paragraphs': [{
            'text': body,
            'source_evidence_ids': body_ids,
        }],
        'closing_paragraph': {
            'text': closing,
            'source_evidence_ids': [first_id],
        },
        'sign_off': 'Sincerely,',
    })


class CoverLetterTransport:
    '''Serve JD analysis, resume, and cover-letter responses from one wire.'''

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, url, headers, payload, timeout):
        self.calls.append({'url': url, 'payload': payload, 'timeout': timeout})
        instructions = payload.get('instructions', '')
        if instructions == COVER_LETTER_GENERATION_INSTRUCTIONS:
            body_text = _cover_letter_json_from_input(payload['input'])
        elif instructions == RESUME_GENERATION_INSTRUCTIONS:
            body_text = _grounded_generation_json_from_input(payload['input'])
        else:
            body_text = json.dumps(grounded_job_payload())
        envelope = {
            'status': 'completed',
            'error': None,
            'incomplete_details': None,
            'output': [{
                'type': 'message',
                'role': 'assistant',
                'status': 'completed',
                'content': [{'type': 'output_text', 'text': body_text}],
            }],
        }
        return 200, json.dumps(envelope).encode('utf-8')


@pytest.fixture
def offline_project(tmp_path, monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'responses')
    monkeypatch.setenv('AGENTROUTER_API_KEY', 'sk-synthetic-offline')
    monkeypatch.setenv('AGENTROUTER_BASE_URL', 'https://provider.example.invalid/v1')
    monkeypatch.setenv('AGENTROUTER_MODEL', 'synthetic-generation-model')
    master = tmp_path / 'data' / 'private' / 'Master_Resume.docx'
    master.parent.mkdir(parents=True, exist_ok=True)
    _candidate_document().save(master)
    transport = CoverLetterTransport()
    real_client = AgentRouterClient
    monkeypatch.setattr(
        cli_module,
        'AgentRouterClient',
        lambda config: real_client(config, http_post=transport),
    )
    return transport


@pytest.fixture
def prepared_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'responses')
    monkeypatch.setenv('AGENTROUTER_API_KEY', 'sk-synthetic-offline')
    monkeypatch.setenv('AGENTROUTER_BASE_URL', 'https://provider.example.invalid/v1')
    monkeypatch.setenv('AGENTROUTER_MODEL', 'synthetic-generation-model')
    monkeypatch.setattr(cli_module, 'PROJECT_ROOT', tmp_path)
    processed = tmp_path / 'data' / 'private' / 'processed'
    master = tmp_path / 'data' / 'private' / 'Master_Resume.docx'
    master.parent.mkdir(parents=True, exist_ok=True)
    document = _candidate_document()
    document.tables[0].add_row().cells[0].text = 'Location: Copper Valley'
    document.add_paragraph('Completed the assessment in Copper Valley.')
    document.save(master)

    cli_module.run_extract_master(
        master_path=master,
        evidence_output_path=processed / 'candidate_evidence.json',
        snapshot_output_path=processed / 'document_snapshot.json',
    )
    jd_path = tmp_path / 'job.txt'
    jd_path.write_text(JOB_TEXT, encoding='utf-8')
    transport = OfflineResponsesTransport()
    real_client = AgentRouterClient
    monkeypatch.setattr(
        cli_module,
        'AgentRouterClient',
        lambda config: real_client(config, http_post=transport),
    )
    cli_module.run_analyze_job(
        input_path=jd_path, output_path=processed / 'job_analysis.json'
    )
    cli_module.run_match_job(
        analysis_path=processed / 'job_analysis.json',
        candidate_evidence_path=processed / 'candidate_evidence.json',
        output_path=processed / 'job_match.json',
    )
    cli_module.run_plan_sections(
        analysis_path=processed / 'job_analysis.json',
        match_path=processed / 'job_match.json',
        candidate_evidence_path=processed / 'candidate_evidence.json',
        output_path=processed / 'section_plan.json',
    )
    return _loaded(
        processed / 'section_plan.json', processed / 'candidate_evidence.json'
    )


def test_cover_letter_reuses_resume_prepared_run_and_exports_private_artifacts(
    tmp_path, monkeypatch, offline_project
):
    monkeypatch.setenv('GRADIO_ANALYTICS_ENABLED', 'False')
    with patch.object(docx_renderer, 'render_resume_pdf', side_effect=_fake_pdf_writer(1)), \
            patch.object(docx_renderer, 'render_cover_letter_pdf', side_effect=_fake_pdf_writer(1)):
        resume = pipeline_module.run_resume_pipeline(
            JOB_TEXT,
            preset='compact-1page',
            page_size='A4',
            sleep_fn=lambda _seconds: None,
            project_root=tmp_path,
        )
        calls_after_resume = len(offline_project.calls)
        cover = pipeline_module.run_cover_letter_pipeline(
            JOB_TEXT,
            preset='compact-1page',
            page_size='A4',
            sleep_fn=lambda _seconds: None,
            project_root=tmp_path,
        )

    assert calls_after_resume == 2
    assert len(offline_project.calls) == 3
    assert cover.prepared_cache_reused is True
    assert cover.cache_hit is True
    assert cover.run_dir == resume.run_dir
    assert cover.traceability_verified is True
    assert cover.pdf_page_count == 1
    assert 220 <= cover.word_count <= 320
    assert cover.cover_letter_docx_path.name.endswith('_Cover_Letter.docx')
    assert cover.cover_letter_pdf_path.name.endswith('_Cover_Letter.pdf')
    assert cover.cover_letter_pdf_path.read_bytes().startswith(b'%PDF')
    assert len(Document(cover.cover_letter_docx_path).tables) == 0

    for artifact in (
        cover.cover_letter_json_path,
        cover.cover_letter_docx_path,
        cover.cover_letter_pdf_path,
    ):
        assert artifact.is_file()
        assert stat.S_IMODE(artifact.stat().st_mode) == 0o600
    assert stat.S_IMODE(cover.run_dir.stat().st_mode) == 0o700

    payload = json.dumps([call['payload'] for call in offline_project.calls])
    assert NAME not in payload
    assert CONTACT_EMAIL not in payload
    assert EXCLUDED_MARKER not in cover.cover_letter_json_path.read_text(encoding='utf-8')
    assert EXCLUDED_MARKER not in cover.cover_letter_text
    assert 'Dear ' in cover.cover_letter_text
    assert 'Sincerely,' in cover.cover_letter_text
    assert NAME in cover.cover_letter_text
    assert CONTACT_EMAIL in cover.cover_letter_text
    artifact = json.loads(cover.cover_letter_json_path.read_text())
    selected = set(json.loads(cover.section_plan_path.read_text())['selected_evidence_ids'])
    for paragraph in (artifact['content']['opening_paragraph'], *artifact['content']['body_paragraphs'],
                      artifact['content']['closing_paragraph']):
        assert set(paragraph['source_evidence_ids']) <= selected


def test_standalone_cover_letter_reuses_prepared_analysis_on_second_run(
    tmp_path, offline_project
):
    with patch.object(docx_renderer, 'render_cover_letter_pdf', side_effect=_fake_pdf_writer(1)):
        first = pipeline_module.run_cover_letter_pipeline(
            JOB_TEXT,
            preset='compact-1page',
            page_size='A4',
            sleep_fn=lambda _seconds: None,
            project_root=tmp_path,
        )
        calls_after_first = len(offline_project.calls)
        second = pipeline_module.run_cover_letter_pipeline(
            JOB_TEXT,
            preset='compact-1page',
            page_size='A4',
            sleep_fn=lambda _seconds: None,
            project_root=tmp_path,
        )

    assert calls_after_first == 2
    assert len(offline_project.calls) == 3
    assert first.prepared_cache_reused is False
    assert second.prepared_cache_reused is True
    assert second.cache_hit is True
    assert second.run_dir == first.run_dir


@pytest.mark.parametrize('change', ['force_extract', 'budget', 'mismatched_plan'])
def test_cover_letter_rebuilds_stale_preparation(tmp_path, offline_project, change):
    with patch.object(docx_renderer, 'render_cover_letter_pdf', side_effect=_fake_pdf_writer(1)):
        first = pipeline_module.run_cover_letter_pipeline(JOB_TEXT, project_root=tmp_path)
        options = {}
        if change == 'force_extract':
            options['force_extract'] = True
        elif change == 'budget':
            options['max_projects'] = 2
        else:
            cached = json.loads(first.section_plan_path.read_text())
            cached['target_job_context']['company_name'] = 'Different Company'
            first.section_plan_path.write_text(json.dumps(cached))

        second = pipeline_module.run_cover_letter_pipeline(JOB_TEXT, project_root=tmp_path, **options)

    assert not second.prepared_cache_reused and second.run_dir != first.run_dir
    assert len(offline_project.calls) == 4
    assert second.cover_letter_text.startswith(NAME) and 'Dear Acme Corp Hiring Team,' in second.cover_letter_text
    if change == 'budget':
        assert json.loads(second.section_plan_path.read_text())['budgets']['max_projects'] == 2


def test_cover_letter_validation_rejects_privacy_and_grounding_failures(
    prepared_bundle,
):
    plan, catalog, snapshot, manifest = prepared_bundle
    hydrated = hydrate_plan_evidence(plan, catalog, snapshot, manifest)
    selected = plan.selected_evidence_ids
    company = plan.target_job_context.company_name
    salutation = (
        f'Dear {company} Hiring Team,' if company else 'Dear Hiring Team,'
    )
    payload = CoverLetterPayload(
        salutation=salutation,
        opening_paragraph=CoverLetterParagraph(
            text=f'Contact {CONTACT_EMAIL} or {NAME} about 999 units.',
            source_evidence_ids=[selected[0]],
        ),
        body_paragraphs=[CoverLetterParagraph(
            text='A grounded paragraph with no unsupported numeric claim.',
            source_evidence_ids=[selected[1]],
        )],
        closing_paragraph=CoverLetterParagraph(
            text='I would welcome the opportunity to discuss the role.',
            source_evidence_ids=[selected[0]],
        ),
        sign_off='Sincerely',
    )

    report = validate_cover_letter(
        payload, plan, catalog, hydrated.header, raise_on_error=False
    )
    codes = {finding.code for finding in report.findings}
    assert {
        'contact_pii',
        'candidate_name',
        'ungrounded_metric',
        'sign_off_format',
    } <= codes
    assert report.traceability_verified is False
    assert 'evidence_not_selected' not in codes

    with pytest.raises(CoverLetterGenerationError):
        validate_cover_letter(payload, plan, catalog, hydrated.header)


def test_cover_letter_generator_repairs_once_then_succeeds(prepared_bundle):
    plan, catalog, snapshot, manifest = prepared_bundle
    hydrated = hydrate_plan_evidence(plan, catalog, snapshot, manifest)
    aliases = [
        alias
        for identifier, alias in hydrated.evidence_alias_by_id.items()
        if identifier in set(plan.selected_evidence_ids)
    ][:2]
    company = plan.target_job_context.company_name
    salutation = (
        f'Dear {company} Hiring Team,' if company else 'Dear Hiring Team,'
    )
    bad = CoverLetterPayload(
        salutation=salutation,
        opening_paragraph=CoverLetterParagraph(
            text=f'Reach me at {CONTACT_EMAIL} with 999 units.',
            source_evidence_ids=[aliases[0]],
        ),
        body_paragraphs=[CoverLetterParagraph(
            text='A grounded paragraph.',
            source_evidence_ids=[aliases[1]],
        )],
        closing_paragraph=CoverLetterParagraph(
            text='I would welcome the opportunity to discuss the role.',
            source_evidence_ids=[aliases[0]],
        ),
        sign_off='Sincerely',
    ).model_dump_json()

    class RepairTransport:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, url, headers, payload, timeout):
            self.calls += 1
            body_text = (
                bad
                if self.calls == 1
                else _cover_letter_json_from_input(payload['input'])
            )
            envelope = {
                'status': 'completed',
                'error': None,
                'incomplete_details': None,
                'output': [{
                    'type': 'message',
                    'role': 'assistant',
                    'status': 'completed',
                    'content': [{'type': 'output_text', 'text': body_text}],
                }],
            }
            return 200, json.dumps(envelope).encode('utf-8')

    transport = RepairTransport()
    client = AgentRouterClient(
        LLMConfig('offline-only'), http_post=transport
    )
    artifact = CoverLetterGenerator(client).generate(
        plan, catalog, snapshot, manifest
    )
    assert transport.calls == 2
    assert artifact.validation.traceability_verified is True
    assert artifact.content.sign_off.endswith(',')


@pytest.mark.parametrize(('kind', 'error_code'), [
    ('raw_id', 'unknown_evidence_alias'), ('unknown_alias', 'unknown_evidence_alias'),
    ('skill_alias', 'unknown_evidence_alias'), ('wrong_company', 'salutation_company_mismatch'),
    ('local_address', 'contact_pii'), ('unknown_field', 'extra_forbidden'),
])
def test_repeated_invalid_response_stops_without_echoing_rejected_values(prepared_bundle, kind, error_code):
    plan, catalog, snapshot, manifest = prepared_bundle
    hydrated = hydrate_plan_evidence(plan, catalog, snapshot, manifest)
    rejected = json.loads(_cover_letter_json_from_input(hydrated.llm_input_text))
    raw_id = plan.selected_evidence_ids[0]
    if kind == 'wrong_company':
        rejected['salutation'] = 'Dear Invented Company Hiring Team,'
    elif kind == 'unknown_field':
        rejected[CONTACT_EMAIL] = NAME
    elif kind == 'local_address':
        rejected['closing_paragraph']['text'] = 'I am based in Copper Valley.'
    else:
        rejected['opening_paragraph']['source_evidence_ids'] = [{
            'raw_id': raw_id, 'unknown_alias': 'E999999', 'skill_alias': 'SK1',
        }[kind]]
    transport = OfflineResponsesTransport()
    transport.response = rejected
    generator = CoverLetterGenerator(AgentRouterClient(LLMConfig('offline-only'), http_post=transport))
    with pytest.raises(CoverLetterGenerationError, match='one repair retry'):
        generator.generate(plan, catalog, snapshot, manifest)

    original, repair = [call['payload']['input'] for call in transport.calls]
    assert repair.startswith(original + '\n\nAPPLICATION_REPAIR_FEEDBACK') and error_code in repair
    for private in (NAME, CONTACT_EMAIL, raw_id, 'Copper Valley', 'Invented Company', 'E999999'):
        assert private not in repair


def test_letter_withholds_private_job_context_and_greets_generic_team(prepared_bundle):
    plan, catalog, snapshot, manifest = prepared_bundle
    job = plan.target_job_context.model_copy(update={'role_title': CONTACT_EMAIL, 'company_name': NAME})
    transport = CoverLetterTransport()
    letter = CoverLetterGenerator(AgentRouterClient(LLMConfig('offline-only'), http_post=transport)).generate(
        plan.model_copy(update={'target_job_context': job}), catalog, snapshot, manifest,
    )
    call, = transport.calls
    bundle = json.loads(call['payload']['input'])
    assert bundle['target_job_context'] == {'role_title': None, 'company_name': None}
    assert 'technical_skills_evidence' not in bundle
    assert letter.content.salutation == 'Dear Hiring Team,'
    assert letter.header.full_name == NAME


@pytest.mark.skipif(not (shutil.which('soffice') or shutil.which('libreoffice')), reason='LibreOffice not installed')
def test_cover_letter_cli_pdf_only_converts_synthetic_letter(tmp_path, prepared_bundle, monkeypatch, capsys):
    processed = tmp_path / 'data/private/processed'
    transport = CoverLetterTransport()
    monkeypatch.setattr(cli_module, 'AgentRouterClient', lambda cfg: AgentRouterClient(cfg, http_post=transport))
    pdf = processed / 'SyntheticCandidate_Cover_Letter.pdf'
    assert cli_module.main([
        'generate-cover-letter', '--plan', str(processed / 'section_plan.json'),
        '--candidate-evidence', str(processed / 'candidate_evidence.json'),
        '--output-json', str(processed / 'letter.json'), '--output-pdf', str(pdf), '--page-size', 'Letter',
    ]) == 0
    assert len(transport.calls) == 1
    assert pdf.read_bytes().startswith(b'%PDF-') and pipeline_module._count_pdf_pages(pdf) == 1
    assert stat.S_IMODE(pdf.stat().st_mode) == 0o600
    assert not list(processed.glob('*.docx')) and not list(processed.glob('.*-pdf-*'))
    output = capsys.readouterr().out
    assert 'traceability_verified: true' in output and 'output_pdf: written' in output
    assert all(value not in output for value in (NAME, CONTACT_EMAIL, pdf.stem, str(tmp_path)))


@pytest.mark.parametrize(('name', 'role', 'company', 'expected'), [
    (
        'Jane Doe',
        'Backend Engineer',
        'Acme Corp',
        'JaneDoe_Backend_Engineer_Acme_Corp_Cover_Letter',
    ),
    (None, None, None, 'Candidate_Cover_Letter'),
    ('   ', '../', None, 'Candidate_Cover_Letter'),
])
def test_cover_letter_filename_stem(name, role, company, expected):
    assert build_cover_letter_stem(name, role, company) == expected


def test_cover_letter_filename_is_capped_and_keeps_suffix():
    stem = build_cover_letter_stem(
        'Jane Doe', 'Senior ' + 'Cloud Platform ' * 50, 'Acme Labs'
    )
    assert len(stem) <= 80
    assert stem.endswith('_Cover_Letter')
    assert stem.startswith('JaneDoe_Senior_')


def test_cover_letter_ui_handler_and_click_preserve_text(tmp_path, monkeypatch):
    monkeypatch.setenv('GRADIO_ANALYTICS_ENABLED', 'False')
    monkeypatch.setenv('GRADIO_TEMP_DIR', str(tmp_path / 'downloads'))
    fake_pdf = tmp_path / 'cover.pdf'
    fake_docx = tmp_path / 'cover.docx'
    fake_pdf.write_bytes(b'%PDF-1.4')
    fake_docx.write_bytes(b'PK')

    def _ok_pipeline(**_kwargs):
        return pipeline_module.CoverLetterResult(
            run_dir=tmp_path / 'run',
            master_sha256='a' * 64,
            cache_hit=True,
            prepared_cache_reused=True,
            candidate_evidence_path=tmp_path / 'cand.json',
            job_description_path=tmp_path / 'jd.txt',
            job_analysis_path=tmp_path / 'ja.json',
            job_match_path=tmp_path / 'jm.json',
            section_plan_path=tmp_path / 'sp.json',
            cover_letter_json_path=tmp_path / 'cover.json',
            cover_letter_docx_path=fake_docx,
            cover_letter_pdf_path=fake_pdf,
            pdf_page_count=1,
            word_count=240,
            model='synthetic-generation-model',
            prompt_version='1.0.0',
            traceability_verified=True,
            findings=(),
            cover_letter_text='Dear Hiring Team,\n\nSincerely,\nSynthetic Candidate\n',
            overflow_warning=None,
        )

    kpi, pdf, docx, audit, text = ui_module.generate_cover_letter_handler(
        JOB_TEXT, pipeline_fn=_ok_pipeline
    )
    assert '1 / 1 (A4)' in kpi
    assert 'Verified' in kpi
    assert '240 words' in kpi
    assert '`True`' in audit
    assert text.startswith('Dear Hiring Team,')
    assert pdf == str(fake_pdf) and docx == str(fake_docx)

    app = ui_module.build_app(
        cover_letter_pipeline_fn=_ok_pipeline, project_root=tmp_path
    )
    click = next(
        fn for fn in app.fns.values()
        if fn.fn and fn.fn.__name__ == '_cover_letter_click'
    )
    data = asyncio.run(
        app.process_api(
            click, [JOB_TEXT, 'compact-1page', 'A4', 3, 6, False]
        )
    )['data']
    assert data[0] == kpi
    assert Path(data[1]['path']).name == fake_pdf.name
    assert Path(data[2]['path']).name == fake_docx.name
    assert Path(data[2]['path']).read_bytes() == fake_docx.read_bytes()
    assert all(stat.S_IMODE(Path(file['path']).stat().st_mode) == 0o600 for file in data[1:3])
    assert str(tmp_path) not in audit
    assert data[4] == text
