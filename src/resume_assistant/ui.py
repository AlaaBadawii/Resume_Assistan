'''Local Gradio web UI bound strictly to 127.0.0.1 with share=False.'''

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any, Callable, Optional

import gradio as gr

from resume_assistant.docx_renderer import DocxRenderError
from resume_assistant.job_analyzer import JobAnalysisError, JobAnalysisInputError
from resume_assistant.llm_client import (
    LLMClientError,
    LLMConfigurationError,
    LLMNetworkError,
    LLMProviderError,
    LLMUnexpectedError,
)
from resume_assistant.matching import CandidateJobMatchError
from resume_assistant.pipeline import PipelineInputError, PipelineResult, run_resume_pipeline
from resume_assistant.private_input import PrivateInputError
from resume_assistant.private_output import PrivateOutputError
from resume_assistant.publication import PublicationError
from resume_assistant.resume_generator import ResumeGenerationError
from resume_assistant.section_planner import SectionPlannerError

DEFAULT_HOST = '127.0.0.1'
DEFAULT_PORT = 7860
_INSTRUCTIONS_PLACEHOLDER = (
    'Optional: emphasize FastAPI & concurrency, keep summary focused on backend engineering...'
)
_JD_PLACEHOLDER = 'Paste the target job description here...'

THEME_TOGGLE_JS = '''() => {
  const targets = [
    document.body,
    document.documentElement,
    document.querySelector('gradio-app'),
  ].filter(Boolean);
  const currentlyDark = document.body.classList.contains('dark');
  targets.forEach((el) => el.classList.toggle('dark', !currentlyDark));
  return [];
}'''

CUSTOM_CSS = '''
.ra-shell {
  max-width: 1320px;
  margin: 0 auto;
}
.ra-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 16px;
  margin-bottom: 12px;
  background: var(--background-fill-secondary);
  border: 1px solid var(--border-color-primary);
  border-radius: 12px;
}
.ra-header-title {
  margin: 0;
  font-size: 1.25rem;
  font-weight: 600;
  color: var(--body-text-color);
  letter-spacing: -0.01em;
}
.ra-header-sub {
  margin: 3px 0 0 0;
  font-size: 0.875rem;
  color: var(--body-text-color-subdued);
}
.ra-kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 8px;
}
@media (max-width: 768px) {
  .ra-kpi-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
.ra-kpi-card {
  background: var(--background-fill-secondary);
  border: 1px solid var(--border-color-primary);
  border-radius: 10px;
  padding: 10px 12px;
}
.ra-kpi-label {
  font-size: 0.72rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--body-text-color-subdued);
  margin-bottom: 4px;
}
.ra-kpi-value {
  font-size: 0.98rem;
  font-weight: 600;
  color: var(--body-text-color);
}
.ra-pill-ok {
  border-color: rgba(16, 185, 129, 0.55);
  background: rgba(16, 185, 129, 0.12);
}
.ra-pill-warn {
  border-color: rgba(245, 158, 11, 0.6);
  background: rgba(245, 158, 11, 0.14);
}
.ra-pill-error {
  border-color: rgba(239, 68, 68, 0.65);
  background: rgba(239, 68, 68, 0.14);
}
.ra-error-banner {
  border: 1px solid rgba(239, 68, 68, 0.65);
  background: rgba(239, 68, 68, 0.14);
  border-radius: 10px;
  padding: 10px 14px;
  margin-bottom: 10px;
  color: var(--body-text-color);
}
.ra-error-banner-title {
  font-size: 0.92rem;
  font-weight: 700;
  margin-bottom: 2px;
}
.ra-error-banner-detail {
  font-size: 0.85rem;
  color: var(--body-text-color-subdued);
}
.ra-panel {
  background: var(--background-fill-primary);
  border: 1px solid var(--border-color-primary);
  border-radius: 10px;
  padding: 14px 16px;
}
'''


def build_theme() -> gr.themes.Base:
    '''Return the neutral slate + cyan engineering theme (light/dark adaptive).'''
    return gr.themes.Base(
        primary_hue=gr.themes.colors.cyan,
        secondary_hue=gr.themes.colors.slate,
        neutral_hue=gr.themes.colors.slate,
        font=(gr.themes.Font('Inter'), 'ui-sans-serif', 'system-ui', 'sans-serif'),
    )


def format_kpi_strip_html(
    *,
    pages_value: str = '—',
    pages_class: str = '',
    traceability_value: str = '—',
    traceability_class: str = '',
    skills_value: str = '—',
    cache_value: str = '—',
    error_heading: Optional[str] = None,
    error_detail: Optional[str] = None,
) -> str:
    '''Render the 4-card adaptive KPI status strip (plus optional error alert banner).'''
    cards = [
        ('PDF Pages', pages_value, pages_class),
        ('Traceability', traceability_value, traceability_class),
        ('Skills', skills_value, ''),
        ('Cache', cache_value, ''),
    ]
    rendered = []
    for label, value, extra_cls in cards:
        cls_attr = f'ra-kpi-card {extra_cls}'.strip()
        rendered.append(
            f'<div class="{cls_attr}">'
            f'<div class="ra-kpi-label">{escape(label)}</div>'
            f'<div class="ra-kpi-value">{escape(value)}</div>'
            f'</div>'
        )
    banner_html = ''
    if error_heading or error_detail:
        banner_html = (
            '<div class="ra-error-banner" role="alert">'
            f'<div class="ra-error-banner-title">{escape(error_heading or "Generation Error")}</div>'
            f'<div class="ra-error-banner-detail">{escape(error_detail or "")}</div>'
            '</div>'
        )
    return f'{banner_html}<div class="ra-kpi-grid">{"".join(rendered)}</div>'


def _initial_kpi_html() -> str:
    return format_kpi_strip_html()


def format_result_kpi_html(result: PipelineResult, page_size: str) -> str:
    page_text = f'{result.pdf_page_count} / 1 ({page_size})' if result.pdf_page_count == 1 else f'{result.pdf_page_count} ({page_size})'
    if result.compact_tier > 0:
        page_text += f' · tier {result.compact_tier}'
    return format_kpi_strip_html(
        pages_value=page_text,
        pages_class='ra-pill-ok' if result.pdf_page_count == 1 else 'ra-pill-warn',
        traceability_value='Verified' if result.traceability_verified else 'Failed',
        traceability_class='ra-pill-ok' if result.traceability_verified else 'ra-pill-warn',
        skills_value=f'{result.skill_count} items',
        cache_value='Hit' if result.cache_hit else 'Refreshed',
    )


def format_result_markdown(result: PipelineResult, page_size: str) -> str:
    '''Build a PII-safe Markdown audit and JD match breakdown.'''
    page_badge = f'{result.pdf_page_count} page' + ('s' if result.pdf_page_count != 1 else '')
    lines = [
        f'- **PDF Output:** `{page_badge}` ({page_size}, `compact_tier={result.compact_tier}`)',
        f'- **Traceability Verified:** `{result.traceability_verified}`',
        f'- **Model / Prompt:** `{result.model}` (`v{result.prompt_version}`)',
        f'- **Technical Skills Emitted:** `{result.skill_count}`',
        f'- **Master Resume Cache:** `{"cache hit" if result.cache_hit else "re-extracted"}` (`{result.master_sha256[:12]}`)',
        f'- **Run Directory:** `{result.run_dir}`',
    ]

    if result.overflow_warning:
        lines.extend(['', f'> **Warning:** {result.overflow_warning}'])

    if result.findings:
        lines.extend(['', '#### Validation Findings'])
        for severity, code, location in result.findings:
            lines.append(f'- `{severity}` — `{code}` at `{location}`')
    else:
        lines.extend(['', '#### Validation Findings', '- None (0 errors, 0 warnings).'])

    section_summary = ', '.join(
        f'`{section_id}` ({count})' for section_id, count in result.planned_section_counts
    )
    if section_summary:
        lines.extend(['', f'**Planned Sections:** {section_summary}'])

    lines.extend([
        '',
        '#### JD Match Breakdown',
        (
            f'- **Supported:** `{result.supported_count}` | '
            f'**Partial:** `{result.partial_count}` | '
            f'**Gap:** `{result.gap_count}` | '
            f'**Not Assessable:** `{result.not_assessable_count}`'
        ),
    ])
    if result.supported_requirements:
        lines.extend([
            '',
            '<details><summary>Supported JD Requirements</summary>',
            '',
            *[f'- {escape(item)}' for item in result.supported_requirements],
            '',
            '</details>',
        ])
    if result.partial_requirements:
        lines.extend([
            '',
            '<details><summary>Partially Supported JD Requirements</summary>',
            '',
            *[f'- {escape(item)}' for item in result.partial_requirements],
            '',
            '</details>',
        ])
    if result.gap_requirements:
        lines.extend([
            '',
            '<details><summary>Gap JD Requirements (No Candidate Evidence Found)</summary>',
            '',
            *[f'- {escape(item)}' for item in result.gap_requirements],
            '',
            '</details>',
        ])
    return '\n'.join(lines)


def _friendly_error_summary(error: Exception) -> tuple[str, str]:
    '''Return a concise, safe (heading, detail) tuple for UI banners and Markdown.'''
    if isinstance(error, PipelineInputError):
        return 'Input Required', str(error)
    if isinstance(error, LLMConfigurationError):
        return (
            'Provider Configuration Missing or Invalid',
            f'{error} Verify your .env provider API key, model, and base URL configuration.',
        )
    if isinstance(error, (LLMProviderError, LLMNetworkError, LLMUnexpectedError)):
        return (
            'Provider Request Failed',
            f'The configured LLM provider could not complete the request ({error}). '
            'Please retry shortly or check provider availability.',
        )
    if isinstance(
        error,
        (
            LLMClientError,
            ResumeGenerationError,
            DocxRenderError,
            JobAnalysisError,
            JobAnalysisInputError,
            CandidateJobMatchError,
            SectionPlannerError,
            PrivateInputError,
            PrivateOutputError,
            PublicationError,
            ValueError,
        ),
    ):
        return 'Generation Could Not Complete', str(error)
    return (
        'Unexpected Error',
        f'The pipeline stopped due to {type(error).__name__}. '
        'Check inputs and system prerequisites (such as LibreOffice) and try again.',
    )


def _friendly_error_markdown(error: Exception) -> str:
    heading, detail = _friendly_error_summary(error)
    return f'### {heading}\n\n{detail}'


def generate_resume_handler(
    job_description: str,
    user_instructions: str = '',
    preset: str = 'compact-1page',
    page_size: str = 'A4',
    max_projects: int = 3,
    max_certifications: int = 6,
    force_extract: bool = False,
    progress: Any = None,
    *,
    pipeline_fn: Callable[..., PipelineResult] = run_resume_pipeline,
    project_root: Optional[Path] = None,
) -> tuple[str, Optional[str], Optional[str], str, str]:
    '''Gradio click handler returning (kpi_html, pdf_path, docx_path, audit_md, preview_md).'''

    def _on_progress(fraction: float, message: str) -> None:
        if progress is not None:
            progress(fraction, desc=message)

    try:
        resolved_preset = None if preset == 'default' else preset
        result = pipeline_fn(
            job_description_text=job_description or '',
            user_instructions=user_instructions or None,
            preset=resolved_preset,
            max_projects=int(max_projects),
            max_certifications=int(max_certifications),
            projects_first=(resolved_preset == 'compact-1page'),
            include_languages=(resolved_preset == 'compact-1page'),
            page_size=page_size,
            force_extract=bool(force_extract),
            progress_callback=_on_progress,
            project_root=project_root,
        )
    except Exception as error:  # noqa: BLE001
        error_heading, error_detail = _friendly_error_summary(error)
        is_input_error = isinstance(error, PipelineInputError)
        err_kpi = format_kpi_strip_html(
            pages_value='Blocked' if is_input_error else 'Failed',
            pages_class='ra-pill-warn' if is_input_error else 'ra-pill-error',
            traceability_value='Error',
            traceability_class='ra-pill-warn' if is_input_error else 'ra-pill-error',
            skills_value='0 items',
            error_heading=error_heading,
            error_detail=error_detail,
        )
        error_md = _friendly_error_markdown(error)
        return (
            err_kpi,
            None,
            None,
            error_md,
            f'_No preview available due to generation error._\n\n{error_md}',
        )

    preview_md = result.resume_preview_markdown or '_No preview content available._'
    return (
        format_result_kpi_html(result, page_size),
        str(result.resume_pdf_path),
        str(result.resume_docx_path),
        format_result_markdown(result, page_size),
        preview_md,
    )


def build_app(
    *,
    pipeline_fn: Callable[..., PipelineResult] = run_resume_pipeline,
    project_root: Optional[Path] = None,
) -> gr.Blocks:
    '''Construct the adaptive 2-column local Gradio Blocks application.'''
    with gr.Blocks(title='Resume Assistant') as demo:
        with gr.Column(elem_classes=['ra-shell']):
            with gr.Row(elem_classes=['ra-header']):
                gr.HTML(
                    '<div>'
                    '<h1 class="ra-header-title">Resume Assistant</h1>'
                    '<p class="ra-header-sub">'
                    'Evidence-bound, ATS-safe resume generation from your local Master Resume.'
                    '</p>'
                    '</div>'
                )
                theme_toggle_btn = gr.Button(
                    'Light / Dark',
                    variant='secondary',
                    size='sm',
                    scale=0,
                )

            with gr.Row(equal_height=False):
                with gr.Column(scale=5):
                    job_description = gr.Textbox(
                        label='Job Description',
                        lines=13,
                        placeholder=_JD_PLACEHOLDER,
                    )
                    user_instructions = gr.Textbox(
                        label='Optional Instructions',
                        lines=3,
                        placeholder=_INSTRUCTIONS_PLACEHOLDER,
                    )
                    with gr.Accordion('Layout & Budget Options', open=False):
                        with gr.Row():
                            preset = gr.Radio(
                                choices=['compact-1page', 'default'],
                                value='compact-1page',
                                label='Preset',
                            )
                            page_size = gr.Radio(
                                choices=['A4', 'Letter'],
                                value='A4',
                                label='Page Size',
                            )
                        max_projects = gr.Slider(
                            minimum=2,
                            maximum=5,
                            step=1,
                            value=3,
                            label='Max Projects',
                        )
                        max_certifications = gr.Slider(
                            minimum=0,
                            maximum=8,
                            step=1,
                            value=6,
                            label='Max Certifications',
                        )
                        force_extract = gr.Checkbox(
                            value=False,
                            label='Force re-extract Master Resume',
                        )
                    generate_btn = gr.Button(
                        'Generate Resume',
                        variant='primary',
                        size='lg',
                    )

                with gr.Column(scale=6):
                    kpi_strip = gr.HTML(value=_initial_kpi_html())
                    with gr.Row():
                        pdf_file = gr.File(label='Download PDF')
                        docx_file = gr.File(label='Download DOCX')
                    with gr.Tabs():
                        with gr.Tab('Audit & JD Match'):
                            audit_markdown = gr.Markdown(
                                value=(
                                    'Paste a target job description and click '
                                    '**Generate Resume** to run Stages 1 → 5 locally.'
                                ),
                                elem_classes=['ra-panel'],
                            )
                        with gr.Tab('Resume Preview'):
                            preview_markdown = gr.Markdown(
                                value='_Generated tailored resume preview will appear here._',
                                elem_classes=['ra-panel'],
                            )

        theme_toggle_btn.click(
            fn=None,
            inputs=None,
            outputs=None,
            js=THEME_TOGGLE_JS,
        )

        def _click(
            jd_text,
            instructions,
            selected_preset,
            selected_page_size,
            proj_cap,
            cert_cap,
            re_extract,
            progress=gr.Progress(),
        ):
            return generate_resume_handler(
                jd_text,
                instructions,
                selected_preset,
                selected_page_size,
                proj_cap,
                cert_cap,
                re_extract,
                progress=progress,
                pipeline_fn=pipeline_fn,
                project_root=project_root,
            )

        generate_btn.click(
            fn=_click,
            inputs=[
                job_description,
                user_instructions,
                preset,
                page_size,
                max_projects,
                max_certifications,
                force_extract,
            ],
            outputs=[kpi_strip, pdf_file, docx_file, audit_markdown, preview_markdown],
        )
    return demo


def launch_app(
    *,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
    pipeline_fn: Callable[..., PipelineResult] = run_resume_pipeline,
    project_root: Optional[Path] = None,
) -> None:
    '''Launch Gradio strictly on 127.0.0.1 with share=False and Gradio 6 theme/css.'''
    app = build_app(pipeline_fn=pipeline_fn, project_root=project_root)
    app.launch(
        server_name=DEFAULT_HOST,
        server_port=port,
        share=False,
        inbrowser=open_browser,
        theme=build_theme(),
        css=CUSTOM_CSS,
    )
