'''Offline tests for Stage 5 user_instructions, pipeline.py, ui.py, and main.py.'''

from __future__ import annotations

import json
import stat
import warnings
from pathlib import Path
from unittest.mock import patch

import gradio as gr
import pytest

import main as app_main
from resume_assistant import cli as cli_module
from resume_assistant import docx_renderer, pipeline as pipeline_module
from resume_assistant import ui as ui_module
from resume_assistant.llm_client import AgentRouterClient, LLMConfigurationError, LLMProviderError
from resume_assistant.prompts import (
    RESUME_GENERATION_INSTRUCTIONS,
    RESUME_GENERATION_PROMPT_VERSION,
)
from resume_assistant.resume_generator import (
    MAX_USER_INSTRUCTIONS_CHARS,
    ResumeGenerationError,
    ResumeGenerator,
    hydrate_plan_evidence,
    sanitize_user_instructions,
)
from tests.fixtures.jobs import SYNTHETIC_JOB_TEXT as JOB_TEXT, grounded_job_payload
from tests.test_resume_generation import (
    CONTACT_EMAIL,
    EXCLUDED_MARKER,
    OfflineResponsesTransport,
    _candidate_document,
    _loaded,
)


def _grounded_generation_json_from_input(llm_input_text: str) -> str:
    bundle = json.loads(llm_input_text.split('\n\nAPPLICATION_REPAIR_FEEDBACK', 1)[0])
    blueprint = bundle['professional_summary_blueprint']
    summary_sentences = []
    if blueprint['identity_statements']:
        stmt = blueprint['identity_statements'][0]
        summary_sentences.append({
            'text': 'Software engineer building reliable Python backend systems.',
            'evidence_ids': [stmt['evidence_ids'][0]],
        })

    skills = [
        {'skill_text': skill['skill_text'], 'evidence_ids': skill['matched_evidence_ids']}
        for skill in bundle['prioritized_skills']
    ]
    skill_groups = (
        [{'group_name': 'Languages & Backend', 'skills': skills}]
        if skills else []
    )

    sections = []
    for section in bundle['ordered_sections']:
        if section['section_id'] in ('professional-summary', 'technical-skills'):
            continue
        entries = []
        for entry in section['entries']:
            ev_list = entry['evidence']
            first_ev = ev_list[0]
            if section['section_id'] in ('education', 'certifications', 'languages'):
                entries.append({
                    'entry_id': entry['entry_id'],
                    'title': first_ev['original_content'][:80],
                    'organization': None,
                    'dates': None,
                    'location': None,
                    'tech_stack': None,
                    'meta': None,
                    'bullets': [],
                })
            else:
                bullet_ev = ev_list[-1]
                entries.append({
                    'entry_id': entry['entry_id'],
                    'title': first_ev['original_content'][:80],
                    'organization': None,
                    'dates': None,
                    'location': None,
                    'tech_stack': None,
                    'meta': None,
                    'bullets': [{
                        'text': bullet_ev['original_content'],
                        'evidence_ids': [bullet_ev['evidence_id']],
                    }],
                })
        sections.append({
            'section_id': section['section_id'],
            'heading_text': section['heading_text'],
            'entries': entries,
        })

    return json.dumps({
        'headline_title': 'Software Engineer',
        'tagline': None,
        'summary_sentences': summary_sentences,
        'skill_groups': skill_groups,
        'sections': sections,
    })


class EndToEndOfflineTransport:
    '''Serve valid JD analysis JSON for Stage 2 and grounded resume JSON for Stage 5.'''

    def __init__(self, *, fail_503_count: int = 0) -> None:
        self.calls: list[dict] = []
        self.fail_503_count = fail_503_count

    def __call__(self, url, headers, payload, timeout):
        self.calls.append({'url': url, 'payload': payload, 'timeout': timeout})
        if self.fail_503_count > 0:
            self.fail_503_count -= 1
            return 503, b'{"error": "transient overload"}'

        instructions = payload.get('instructions', '')
        if instructions == RESUME_GENERATION_INSTRUCTIONS:
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


def _fake_pdf_writer(page_count: int = 1):
    def _writer(resume, output_path: Path, *, page_size: str = 'LETTER', docx_path=None, converter_bin=None):
        page_objs = b'\n'.join(
            f'{i + 1} 0 obj << /Type /Page >> endobj'.encode('ascii')
            for i in range(page_count)
        )
        pdf_payload = b'%PDF-1.4\n' + page_objs + b'\n%%EOF\n'
        docx_renderer._write_atomic_private_bytes(Path(output_path), pdf_payload)
        return Path(output_path)

    return _writer


def test_user_instructions_sanitization_prompt_wiring_and_grounding_subordination(
    tmp_path, monkeypatch
):
    assert 'user_instructions' in RESUME_GENERATION_INSTRUCTIONS
    assert 'STRICTLY SUBORDINATE' in RESUME_GENERATION_INSTRUCTIONS

    assert sanitize_user_instructions(None) is None
    assert sanitize_user_instructions('   \n\t  ') is None
    long_text = 'Emphasize FastAPI concurrency. ' * 40
    trimmed = sanitize_user_instructions(long_text)
    assert trimmed is not None
    assert len(trimmed) <= MAX_USER_INSTRUCTIONS_CHARS

    with pytest.raises(ResumeGenerationError):
        sanitize_user_instructions(f'Reach me at {CONTACT_EMAIL}')

    # Build a real Stage 1-4 synthetic bundle.
    monkeypatch.setenv('LLM_PROVIDER', 'responses')
    monkeypatch.setenv('AGENTROUTER_API_KEY', 'sk-synthetic-offline')
    monkeypatch.setenv('AGENTROUTER_BASE_URL', 'https://provider.example.invalid/v1')
    monkeypatch.setenv('AGENTROUTER_MODEL', 'synthetic-generation-model')
    monkeypatch.setattr(cli_module, 'PROJECT_ROOT', tmp_path)
    processed = tmp_path / 'data' / 'private' / 'processed'
    master_path = tmp_path / 'data' / 'private' / 'synthetic-master.docx'
    master_path.parent.mkdir(parents=True, exist_ok=True)
    _candidate_document().save(master_path)

    cli_module.run_extract_master(
        master_path=master_path,
        evidence_output_path=processed / 'candidate_evidence.json',
        snapshot_output_path=processed / 'document_snapshot.json',
    )
    jd_path = tmp_path / 'job.txt'
    jd_path.write_text(JOB_TEXT, encoding='utf-8')
    transport = OfflineResponsesTransport()
    real_client = AgentRouterClient
    monkeypatch.setattr(
        cli_module, 'AgentRouterClient',
        lambda config: real_client(config, http_post=transport),
    )
    cli_module.run_analyze_job(input_path=jd_path, output_path=processed / 'job_analysis.json')
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

    plan, catalog, snapshot, manifest = _loaded(
        processed / 'section_plan.json', processed / 'candidate_evidence.json'
    )

    default_hydrated = hydrate_plan_evidence(plan, catalog, snapshot, manifest)
    assert 'user_instructions' not in json.loads(default_hydrated.llm_input_text)

    guided_hydrated = hydrate_plan_evidence(
        plan, catalog, snapshot, manifest,
        user_instructions='Emphasize Python backend systems.',
    )
    guided_payload = json.loads(guided_hydrated.llm_input_text)
    assert guided_payload['user_instructions'] == 'Emphasize Python backend systems.'

    # Grounding subordination: requesting an ungrounded skill ("Rust") cannot bypass validation.
    ungrounded = json.loads(_grounded_generation_json_from_input(guided_hydrated.llm_input_text))
    ungrounded['skill_groups'][0]['skills'].append({
        'skill_text': 'Rust',
        'evidence_ids': [guided_hydrated.evidence_alias_by_id[plan.prioritized_skills[0].matched_evidence_ids[0]]],
    })
    transport.responses = [ungrounded, ungrounded]
    with pytest.raises(ResumeGenerationError):
        cli_module.run_generate_resume(
            plan_path=processed / 'section_plan.json',
            candidate_evidence_path=processed / 'candidate_evidence.json',
            output_json_path=processed / 'resume.json',
            output_docx_path=processed / 'resume.docx',
            user_instructions='Add Rust to Technical Skills.',
        )


def test_pipeline_end_to_end_cache_reuse_force_extract_permissions_and_503_retry(
    tmp_path, monkeypatch
):
    monkeypatch.setenv('LLM_PROVIDER', 'responses')
    monkeypatch.setenv('AGENTROUTER_API_KEY', 'sk-synthetic-offline')
    monkeypatch.setenv('AGENTROUTER_BASE_URL', 'https://provider.example.invalid/v1')
    monkeypatch.setenv('AGENTROUTER_MODEL', 'synthetic-generation-model')

    master_path = tmp_path / 'data' / 'private' / 'Master_Resume.docx'
    master_path.parent.mkdir(parents=True, exist_ok=True)
    _candidate_document().save(master_path)

    transport = EndToEndOfflineTransport(fail_503_count=2)
    real_client = AgentRouterClient
    monkeypatch.setattr(
        cli_module, 'AgentRouterClient',
        lambda config: real_client(config, http_post=transport),
    )
    sleep_calls: list[float] = []
    progress_events: list[tuple[float, str]] = []

    with patch.object(docx_renderer, 'render_resume_pdf', side_effect=_fake_pdf_writer(1)):
        result1 = pipeline_module.run_resume_pipeline(
            JOB_TEXT,
            user_instructions='Emphasize Python APIs.',
            preset='compact-1page',
            page_size='A4',
            progress_callback=lambda f, msg: progress_events.append((f, msg)),
            sleep_fn=sleep_calls.append,
            project_root=tmp_path,
        )

    assert result1.cache_hit is False
    assert result1.traceability_verified is True
    assert result1.pdf_page_count == 1
    assert result1.overflow_warning is None
    assert result1.prompt_version == RESUME_GENERATION_PROMPT_VERSION
    assert sleep_calls == [10.0, 10.0]
    assert progress_events[0][0] == 0.05
    assert progress_events[-1][0] == 1.0

    for artifact in (
        result1.candidate_evidence_path,
        result1.job_description_path,
        result1.job_analysis_path,
        result1.job_match_path,
        result1.section_plan_path,
        result1.resume_json_path,
        result1.resume_docx_path,
        result1.resume_pdf_path,
    ):
        assert artifact.is_file()
        assert stat.S_IMODE(artifact.stat().st_mode) == 0o600
    assert stat.S_IMODE(result1.run_dir.stat().st_mode) == 0o700
    assert EXCLUDED_MARKER not in result1.resume_json_path.read_text(encoding='utf-8')
    assert CONTACT_EMAIL not in json.dumps(result1.findings)

    # Second run tests auto-fit compact_tier=1 stopping on 1 page without extra LLM call.
    tier_transport = EndToEndOfflineTransport()
    monkeypatch.setattr(
        cli_module, 'AgentRouterClient',
        lambda config: real_client(config, http_post=tier_transport),
    )
    rendered_pages = [2, 1]

    def _adaptive_pdf(resume, output_path: Path, *, page_size: str = 'LETTER', docx_path=None, converter_bin=None, compact_tier: int = 0):
        count = rendered_pages.pop(0) if rendered_pages else 1
        return _fake_pdf_writer(count)(resume, output_path, page_size=page_size, docx_path=docx_path)

    with patch.object(docx_renderer, 'render_resume_pdf', side_effect=_adaptive_pdf), \
            patch.object(cli_module, 'run_extract_master', side_effect=AssertionError('should use cache')):
        result2 = pipeline_module.run_resume_pipeline(
            JOB_TEXT,
            preset='compact-1page',
            page_size='A4',
            sleep_fn=lambda _s: None,
            project_root=tmp_path,
        )
    assert result2.cache_hit is True
    assert result2.compact_tier == 1
    assert result2.pdf_page_count == 1
    assert result2.overflow_warning is None
    assert len(tier_transport.calls) == 2

    # Persistent >1 pages across compact_tier 0, 1, 2 emits overflow_warning at compact_tier=2.
    monkeypatch.setattr(
        cli_module, 'AgentRouterClient',
        lambda config: real_client(config, http_post=EndToEndOfflineTransport()),
    )
    with patch.object(docx_renderer, 'render_resume_pdf', side_effect=_fake_pdf_writer(2)), \
            patch.object(cli_module, 'run_extract_master', side_effect=AssertionError('should use cache')):
        result_overflow = pipeline_module.run_resume_pipeline(
            JOB_TEXT,
            preset='compact-1page',
            page_size='A4',
            sleep_fn=lambda _s: None,
            project_root=tmp_path,
        )
    assert result_overflow.compact_tier == 2
    assert result_overflow.pdf_page_count == 2
    assert result_overflow.overflow_warning is not None
    assert result1.run_dir.name.endswith('acme-corp-senior-backend-engineer')
    assert 'acme-corp-senior-backend-engineer' in result_overflow.run_dir.name

    # force_extract=True bypasses the Stage 1 cache.
    monkeypatch.setattr(
        cli_module, 'AgentRouterClient',
        lambda config: real_client(config, http_post=EndToEndOfflineTransport()),
    )
    with patch.object(docx_renderer, 'render_resume_pdf', side_effect=_fake_pdf_writer(1)):
        result3 = pipeline_module.run_resume_pipeline(
            JOB_TEXT,
            force_extract=True,
            sleep_fn=lambda _s: None,
            project_root=tmp_path,
        )
    assert result3.cache_hit is False
    assert result3.compact_tier == 0
    assert result3.pdf_page_count == 1


def test_pipeline_boilerplate_jd_slug_skips_generic_headings():
    boilerplate_jd = (
        'About the job\n'
        'Objective:\n'
        'Summary\n'
        'Position Overview\n'
        'Role Overview\n'
        'About the Role\n'
        'Job Summary:\n'
        'Job description:\n'
        'Senior Python Backend Engineer\n'
        'Build distributed services.\n'
    )
    slug = pipeline_module._job_slug(boilerplate_jd)
    assert slug == 'senior-python-backend-engineer'
    assert 'about-the-job' not in slug
    assert 'objective' not in slug


def test_gradio_ui_build_handlers_and_loopback_launch(tmp_path):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always', DeprecationWarning)
        theme = ui_module.build_theme()
        app = ui_module.build_app()
    assert app is not None
    assert isinstance(theme, gr.themes.Base)
    gradio_deprecations = [
        w for w in caught
        if issubclass(w.category, DeprecationWarning) and 'gradio' in str(w.filename).lower()
    ]
    assert not gradio_deprecations
    assert '16, 185, 129' in ui_module.CUSTOM_CSS
    assert 'var(--background-fill-primary)' in ui_module.CUSTOM_CSS

    empty_kpi, pdf_out, docx_out, empty_audit, empty_prev = ui_module.generate_resume_handler('   ')
    assert 'ra-pill-warn' in empty_kpi
    assert 'Input Required' in empty_audit
    assert pdf_out is None and docx_out is None
    assert 'No preview' in empty_prev

    def _missing_key(**_kwargs):
        raise LLMConfigurationError('AgentRouter API key is missing.')

    cfg_kpi, cfg_pdf, cfg_docx, cfg_audit, cfg_prev = ui_module.generate_resume_handler(
        JOB_TEXT, pipeline_fn=_missing_key
    )
    assert 'Provider Configuration Missing' in cfg_audit
    assert '.env' in cfg_audit
    assert cfg_pdf is None and cfg_docx is None
    assert 'Error' in cfg_kpi
    assert 'ra-error-banner' in cfg_kpi
    assert 'ra-pill-error' in cfg_kpi
    assert 'role="alert"' in cfg_kpi
    assert 'Provider Configuration Missing' in cfg_prev

    def _provider_down(**_kwargs):
        raise LLMProviderError('provider request failed with HTTP 503.')

    err_kpi, err_pdf, err_docx, err_audit, err_prev = ui_module.generate_resume_handler(
        JOB_TEXT, pipeline_fn=_provider_down
    )
    assert 'Provider Request Failed' in err_audit
    assert '503' in err_audit
    assert 'ra-error-banner' in err_kpi
    assert 'ra-pill-error' in err_kpi
    assert 'Provider Request Failed' in err_kpi
    assert '503' in err_kpi
    assert 'Provider Request Failed' in err_prev
    assert err_pdf is None and err_docx is None

    fake_pdf = tmp_path / 'tailored.pdf'
    fake_docx = tmp_path / 'tailored.docx'
    fake_pdf.write_bytes(b'%PDF-1.4')
    fake_docx.write_bytes(b'PK')

    def _ok_pipeline(**_kwargs):
        return pipeline_module.PipelineResult(
            run_dir=tmp_path / 'run',
            master_sha256='a' * 64,
            cache_hit=True,
            candidate_evidence_path=tmp_path / 'cand.json',
            job_description_path=tmp_path / 'jd.txt',
            job_analysis_path=tmp_path / 'ja.json',
            job_match_path=tmp_path / 'jm.json',
            section_plan_path=tmp_path / 'sp.json',
            resume_json_path=tmp_path / 'resume.json',
            resume_docx_path=fake_docx,
            resume_pdf_path=fake_pdf,
            pdf_page_count=1,
            model='gemini-3.5-flash-lite',
            prompt_version='1.5.0',
            traceability_verified=True,
            findings=(),
            skill_count=36,
            supported_count=2,
            partial_count=0,
            gap_count=1,
            not_assessable_count=0,
            supported_requirements=('Python APIs', 'SQL optimization'),
            partial_requirements=(),
            gap_requirements=('GraphQL schema design',),
            planned_section_counts=(('projects', 3), ('certifications', 6)),
            compact_tier=1,
            overflow_warning=None,
            resume_preview_markdown='### Software Engineer\n\n#### Summary\nBuilt Python APIs.',
        )

    ok_kpi, ok_pdf, ok_docx, ok_audit, ok_prev = ui_module.generate_resume_handler(
        JOB_TEXT, pipeline_fn=_ok_pipeline
    )
    assert '1 / 1 (A4)' in ok_kpi
    assert 'tier 1' in ok_kpi
    assert 'ra-pill-ok' in ok_kpi
    assert 'Verified' in ok_kpi
    assert '`True`' in ok_audit
    assert 'Python APIs' in ok_audit
    assert 'GraphQL schema design' in ok_audit
    assert '### Software Engineer' in ok_prev
    assert ok_pdf == str(fake_pdf)
    assert ok_docx == str(fake_docx)

    launch_calls = []

    class FakeBlocks:
        def launch(self, **kwargs):
            launch_calls.append(kwargs)

    with patch.object(ui_module, 'build_app', return_value=FakeBlocks()):
        ui_module.launch_app(port=7865, open_browser=False)

    assert len(launch_calls) == 1
    assert launch_calls[0]['server_name'] == '127.0.0.1'
    assert launch_calls[0]['server_port'] == 7865
    assert launch_calls[0]['share'] is False
    assert launch_calls[0]['inbrowser'] is False
    assert isinstance(launch_calls[0]['theme'], gr.themes.Base)
    assert launch_calls[0]['css'] == ui_module.CUSTOM_CSS


def test_main_entrypoint_env_loading_and_missing_master_guard(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / '.env'
    env_file.write_text(
        'export TEST_UI_PROVIDER="gemini"\n'
        "TEST_UI_KEY='synthetic-value'\n"
        '# comment line\n',
        encoding='utf-8',
    )
    monkeypatch.setenv('TEST_UI_PROVIDER', 'unset')
    monkeypatch.delenv('TEST_UI_PROVIDER')
    monkeypatch.setenv('TEST_UI_KEY', 'unset')
    monkeypatch.delenv('TEST_UI_KEY')
    app_main.load_dotenv_file(env_file)
    assert app_main.os.environ['TEST_UI_PROVIDER'] == 'gemini'
    assert app_main.os.environ['TEST_UI_KEY'] == 'synthetic-value'

    monkeypatch.setattr(app_main, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(app_main, 'DEFAULT_MASTER_PATH', tmp_path / 'data' / 'private' / 'Master_Resume.docx')
    assert app_main.main(['--no-browser']) == 2
    assert 'Master Resume not found' in capsys.readouterr().err


def test_main_wsl_detection_disables_inbrowser_and_prints_url(tmp_path, monkeypatch, capsys):
    master = tmp_path / 'data' / 'private' / 'Master_Resume.docx'
    master.parent.mkdir(parents=True, exist_ok=True)
    master.write_bytes(b'PK')

    monkeypatch.setattr(app_main, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(app_main, 'DEFAULT_MASTER_PATH', master)
    monkeypatch.setenv('WSL_DISTRO_NAME', 'Ubuntu')
    assert app_main.is_wsl() is True

    captured_launch = []
    with patch.object(app_main, 'launch_app', side_effect=lambda **kw: captured_launch.append(kw)):
        rc = app_main.main(['--port', '7861'])

    assert rc == 0
    assert captured_launch == [{'port': 7861, 'open_browser': False}]
    assert 'Open in your browser: http://127.0.0.1:7861' in capsys.readouterr().out

    monkeypatch.delenv('WSL_DISTRO_NAME')
    monkeypatch.delenv('WSL_INTEROP', raising=False)
    with patch.object(app_main.platform, 'release', return_value='5.15.146.1-microsoft-standard-WSL2'):
        assert app_main.is_wsl() is True
    with patch.object(app_main.platform, 'release', return_value='6.8.0-generic'):
        assert app_main.is_wsl() is False


def test_build_theme_matches_gradio_builtin_comparison():
    import gradio.utils as gr_utils

    theme = ui_module.build_theme()
    theme_dict = theme.to_dict()
    builtin_classes = (
        gr.themes.Default,
        gr.themes.Soft,
        gr.themes.Monochrome,
        gr.themes.Glass,
        gr.themes.Base,
        gr.themes.Ocean,
        gr.themes.Origin,
        gr.themes.Citrus,
    )
    for built_in in builtin_classes:
        assert (theme_dict == built_in().to_dict()) is False

    is_custom_theme = not any(
        theme_dict == built_in_theme.to_dict()
        for built_in_theme in gr_utils.BUILT_IN_THEMES.values()
    )
    assert is_custom_theme is True
