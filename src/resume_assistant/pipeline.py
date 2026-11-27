'''End-to-end Stages 1 -> 5 pipeline orchestrator with SHA-256 Stage 1 caching.'''

from __future__ import annotations

import hashlib
import os
import re
import stat
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from resume_assistant import cli as cli_module
from resume_assistant import docx_renderer, publication_reader
from resume_assistant.llm_client import LLMProviderError
from resume_assistant.private_output import PrivateOutput
from resume_assistant.resume_generator import sanitize_user_instructions

_TRANSIENT_HTTP_STATUS = '503'
_PDF_PAGE_PATTERN = re.compile(rb'/Type\s*/Page(?!s)\b')
_SLUG_TOKEN_PATTERN = re.compile(r'[a-z0-9]+')
_BOILERPLATE_HEADINGS = frozenset({
    'about',
    'about the job',
    'about the role',
    'about us',
    'company overview',
    'description',
    'job description',
    'job objective',
    'job overview',
    'job summary',
    'objective',
    'overview',
    'position objective',
    'position overview',
    'position summary',
    'role description',
    'role objective',
    'role overview',
    'role summary',
    'summary',
    'the opportunity',
    'the role',
    'what you will do',
    'what youll do',
    'who we are',
})


class PipelineInputError(ValueError):
    '''Controlled, content-free error for invalid pipeline inputs.'''


@dataclass(frozen=True)
class PipelineResult:
    '''Structured, PII-safe summary and artifact paths from a full pipeline run.'''

    run_dir: Path
    master_sha256: str
    cache_hit: bool
    candidate_evidence_path: Path
    job_description_path: Path
    job_analysis_path: Path
    job_match_path: Path
    section_plan_path: Path
    resume_json_path: Path
    resume_docx_path: Path
    resume_pdf_path: Path
    pdf_page_count: int
    model: str
    prompt_version: str
    traceability_verified: bool
    findings: tuple[tuple[str, str, str], ...]
    skill_count: int
    supported_count: int
    partial_count: int
    gap_count: int
    not_assessable_count: int
    supported_requirements: tuple[str, ...]
    partial_requirements: tuple[str, ...]
    gap_requirements: tuple[str, ...]
    planned_section_counts: tuple[tuple[str, int], ...]
    compact_tier: int = 0
    overflow_warning: Optional[str] = None
    resume_preview_markdown: str = ''


def _compute_file_sha256(path: Path) -> str:
    '''Stream-hash a regular non-symlink Master Resume DOCX cleanly.'''
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    try:
        info = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise PipelineInputError('Master Resume must be a regular file.')
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise PipelineInputError(
            'Master Resume could not be found at data/private/Master_Resume.docx.'
        ) from None
    except OSError:
        raise PipelineInputError('Master Resume file could not be read.') from None

    digest = hashlib.sha256()
    try:
        handle = os.fdopen(fd, 'rb')
    except BaseException:
        os.close(fd)
        raise
    with handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _slug_from_text(text: str, *, max_tokens: int = 6, max_len: int = 44) -> str:
    tokens = _SLUG_TOKEN_PATTERN.findall(text.lower())
    return '-'.join(tokens[:max_tokens])[:max_len].strip('-')


def _job_slug(job_description_text: str) -> str:
    for raw_line in job_description_text.splitlines():
        cleaned = re.sub(r'^[#*\-\s]+', '', raw_line).rstrip(':').strip()
        if not cleaned:
            continue
        normalized = ' '.join(_SLUG_TOKEN_PATTERN.findall(cleaned.lower()))
        if not normalized or normalized in _BOILERPLATE_HEADINGS:
            continue
        slug = _slug_from_text(cleaned)
        if slug:
            return slug
    return 'job'


def _job_slug_from_analysis(analysis) -> Optional[str]:
    parts: list[str] = []
    company = getattr(getattr(analysis, 'company_name', None), 'value', None)
    role = getattr(getattr(analysis, 'role_title', None), 'value', None)
    if isinstance(company, str) and company.strip():
        parts.append(company.strip())
    if isinstance(role, str) and role.strip():
        parts.append(role.strip())
    if not parts:
        return None
    slug = _slug_from_text(' '.join(parts), max_tokens=7, max_len=48)
    return slug or None


def _allocate_run_dir(
    processed_dir: Path,
    slug: str,
    *,
    stamp: Optional[str] = None,
) -> Path:
    runs_dir = processed_dir / 'runs'
    runs_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(runs_dir, 0o700)
    used_stamp = stamp or datetime.now().strftime('%Y%m%d_%H%M%S')
    base_name = f'{used_stamp}_{slug or "job"}'
    candidate = runs_dir / base_name
    suffix = 1
    while candidate.exists():
        suffix += 1
        candidate = runs_dir / f'{base_name}_{suffix}'
    return candidate


def format_resume_preview_markdown(artifact) -> str:
    '''Render a clean Markdown preview of the generated TailoredResumeArtifact.'''
    content = artifact.content
    lines: list[str] = []
    if content.headline_title.strip():
        lines.append(f'### {content.headline_title.strip()}')
    if content.tagline and content.tagline.strip():
        lines.append(f'*{content.tagline.strip()}*')

    summary = [s.text.strip() for s in content.summary_sentences if s.text.strip()]
    if summary:
        lines.extend(['', '#### Summary', ' '.join(summary)])

    groups = [
        (g.group_name.strip(), [item.skill_text.strip() for item in g.skills if item.skill_text.strip()])
        for g in content.skill_groups
    ]
    groups = [(name, items) for name, items in groups if items]
    if groups:
        lines.extend(['', '#### Technical Skills'])
        for group_name, items in groups:
            lines.append(f'- **{group_name}:** {" · ".join(items)}')

    for section in content.sections:
        if section.section_id in ('professional-summary', 'technical-skills'):
            continue
        if not section.entries:
            continue
        lines.extend(['', f'#### {section.heading_text.strip()}'])
        if section.section_id == 'languages':
            lang_items: list[str] = []
            for entry in section.entries:
                title = entry.title.strip()
                details = [
                    (v or '').strip()
                    for v in (entry.meta, entry.organization, entry.dates, entry.location)
                    if (v or '').strip() and (v or '').strip().casefold() not in title.casefold()
                ]
                if title and details:
                    lang_items.append(f'{title} ({", ".join(details)})')
                elif title:
                    lang_items.append(title)
            if lang_items:
                lines.append(' · '.join(dict.fromkeys(lang_items)))
            continue
        for entry in section.entries:
            title = entry.title.strip()
            org = (entry.organization or '').strip()
            head = f'{title} — {org}' if org and org.casefold() not in title.casefold() else title
            meta_pieces = [
                p.strip() for p in (entry.dates, entry.location, entry.meta)
                if p and p.strip()
            ]
            suffix = f' *({" | ".join(meta_pieces)})*' if meta_pieces else ''
            lines.append(f'**{head}**{suffix}')
            for bullet in entry.bullets:
                text = bullet.text.strip()
                if text:
                    lines.append(f'- {text}')
    return '\n'.join(lines) if lines else '_No preview content available._'


def _retry_on_503(
    fn: Callable[[], object],
    *,
    max_attempts: int = 3,
    delay_seconds: float = 10.0,
    sleep_fn: Callable[[float], None] = time.sleep,
):
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except LLMProviderError as error:
            is_503 = _TRANSIENT_HTTP_STATUS in str(error)
            if is_503 and attempt < max_attempts:
                sleep_fn(delay_seconds)
                continue
            raise


def _count_pdf_pages(pdf_path: Path) -> int:
    data = pdf_path.read_bytes()
    return len(_PDF_PAGE_PATTERN.findall(data))


def _ensure_private_file_mode(path: Path) -> None:
    if path.exists():
        os.chmod(path, 0o600)


def run_resume_pipeline(
    job_description_text: str,
    *,
    master_path: Optional[Path | str] = None,
    user_instructions: Optional[str] = None,
    preset: Optional[str] = 'compact-1page',
    max_projects: Optional[int] = None,
    max_certifications: Optional[int] = None,
    projects_first: bool = False,
    include_languages: bool = False,
    page_size: str = docx_renderer.DEFAULT_PAGE_SIZE,
    strict_metrics: bool = False,
    force_extract: bool = False,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    project_root: Optional[Path] = None,
) -> PipelineResult:
    '''Run Stages 1 -> 5 from raw JD text to validated JSON, DOCX, and PDF.'''

    def notify(fraction: float, message: str) -> None:
        if progress_callback is not None:
            progress_callback(fraction, message)

    if not isinstance(job_description_text, str) or not job_description_text.strip():
        raise PipelineInputError('Please paste a non-empty job description before generating.')
    sanitized_instructions = sanitize_user_instructions(user_instructions)
    docx_renderer.resolve_page_size(page_size)
    resolved_preset = None if preset in (None, '', 'default') else preset

    root = Path(os.path.abspath(project_root if project_root is not None else cli_module.PROJECT_ROOT))
    processed_dir = root / 'data' / 'private' / 'processed'
    resolved_master = (
        Path(os.path.abspath(master_path))
        if master_path is not None
        else root / 'data' / 'private' / 'Master_Resume.docx'
    )
    master_sha256 = _compute_file_sha256(resolved_master)
    sha12 = master_sha256[:12]

    cache_dir = processed_dir / 'cache'
    cached_evidence_path = cache_dir / f'candidate_evidence_{sha12}.json'
    cached_snapshot_path = cache_dir / f'document_snapshot_{sha12}.json'

    previous_root = cli_module.PROJECT_ROOT
    cli_module.PROJECT_ROOT = root
    try:
        notify(0.05, 'Stage 1/5: Checking Master Resume evidence cache...')
        cache_hit = False
        if not force_extract and cached_evidence_path.is_file() and not cached_evidence_path.is_symlink():
            try:
                _snap, cached_catalog, _manifest, _gen_id = publication_reader.load_committed_pair(
                    root, cached_evidence_path
                )
                if cached_catalog.source_document.sha256 == master_sha256:
                    cache_hit = True
            except Exception:  # noqa: BLE001
                cache_hit = False

        if not cache_hit:
            notify(0.10, 'Stage 1/5: Extracting Master Resume evidence...')
            cli_module.run_extract_master(
                master_path=resolved_master,
                evidence_output_path=cached_evidence_path,
                snapshot_output_path=cached_snapshot_path,
            )
            _ensure_private_file_mode(cached_evidence_path)

        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        initial_slug = _job_slug(job_description_text)
        run_dir = _allocate_run_dir(processed_dir, initial_slug, stamp=stamp)
        jd_txt_path = run_dir / 'job_description.txt'
        job_analysis_path = run_dir / 'job_analysis.json'

        with PrivateOutput(root, jd_txt_path) as jd_target:
            os.chmod(run_dir, 0o700)
            jd_target.write_bytes(job_description_text.strip().encode('utf-8') + b'\n')
        _ensure_private_file_mode(jd_txt_path)

        notify(0.25, 'Stage 2/5: Analyzing target job description...')
        _in_p, _out_p, _model_name, analysis = _retry_on_503(
            lambda: cli_module.run_analyze_job(
                input_path=jd_txt_path,
                output_path=job_analysis_path,
            ),
            sleep_fn=sleep_fn,
        )
        _ensure_private_file_mode(job_analysis_path)

        refined_slug = _job_slug_from_analysis(analysis)
        if refined_slug and refined_slug != initial_slug:
            target_dir = _allocate_run_dir(processed_dir, refined_slug, stamp=stamp)
            if not target_dir.exists():
                run_dir.rename(target_dir)
                run_dir = target_dir
                jd_txt_path = run_dir / 'job_description.txt'
                job_analysis_path = run_dir / 'job_analysis.json'

        job_match_path = run_dir / 'job_match.json'
        section_plan_path = run_dir / 'section_plan.json'
        resume_json_path = run_dir / 'tailored_resume.json'
        resume_docx_path = run_dir / 'tailored_resume.docx'
        resume_pdf_path = run_dir / 'tailored_resume.pdf'

        notify(0.45, 'Stage 3/5: Matching candidate evidence against job requirements...')
        match = cli_module.run_match_job(
            analysis_path=job_analysis_path,
            candidate_evidence_path=cached_evidence_path,
            output_path=job_match_path,
        )
        _ensure_private_file_mode(job_match_path)

        notify(0.60, 'Stage 4/5: Planning sections and evidence budgets...')
        _plan_path, plan = cli_module.run_plan_sections(
            analysis_path=job_analysis_path,
            match_path=job_match_path,
            candidate_evidence_path=cached_evidence_path,
            output_path=section_plan_path,
            max_projects=max_projects,
            max_certifications=max_certifications,
            projects_first=projects_first,
            include_languages=include_languages,
            preset=resolved_preset,
        )
        _ensure_private_file_mode(section_plan_path)

        notify(0.75, 'Stage 5/5: Generating tailored resume and rendering DOCX/PDF...')
        _json_out, _docx_out, artifact = _retry_on_503(
            lambda: cli_module.run_generate_resume(
                plan_path=section_plan_path,
                candidate_evidence_path=cached_evidence_path,
                output_json_path=resume_json_path,
                output_docx_path=resume_docx_path,
                output_pdf_path=resume_pdf_path,
                page_size=page_size,
                strict_metrics=strict_metrics,
                user_instructions=sanitized_instructions,
            ),
            sleep_fn=sleep_fn,
        )
        for path in (resume_json_path, resume_docx_path, resume_pdf_path):
            _ensure_private_file_mode(path)

        compact_tier = 0
        pdf_page_count = _count_pdf_pages(resume_pdf_path)
        if resolved_preset == 'compact-1page' and pdf_page_count > 1:
            for tier in (1, 2):
                notify(0.88, f'Auto-fitting 1-page layout (compact tier {tier}/2)...')
                fitted_docx = docx_renderer.render_docx_bytes(
                    artifact, page_size=page_size, compact_tier=tier
                )
                with PrivateOutput(root, resume_docx_path) as docx_target:
                    docx_target.write_bytes(fitted_docx)
                docx_renderer.render_resume_pdf(
                    artifact,
                    resume_pdf_path,
                    page_size=page_size,
                    docx_path=resume_docx_path,
                )
                _ensure_private_file_mode(resume_docx_path)
                _ensure_private_file_mode(resume_pdf_path)
                compact_tier = tier
                pdf_page_count = _count_pdf_pages(resume_pdf_path)
                if pdf_page_count <= 1:
                    break
    finally:
        cli_module.PROJECT_ROOT = previous_root

    notify(0.95, 'Verifying output artifacts...')
    supported_reqs = tuple(
        req.requirement_text for req in match.requirements if req.status == 'supported'
    )
    partial_reqs = tuple(
        req.requirement_text
        for req in match.requirements
        if req.status == 'partially_supported'
    )
    gap_reqs = tuple(
        req.requirement_text
        for req in match.requirements
        if req.status == 'no_evidence_found'
    )
    not_assessable_count = sum(
        1 for req in match.requirements if req.status == 'not_assessable'
    )
    findings = tuple(
        (f.severity, f.code, f.location) for f in artifact.validation.findings
    )
    skill_count = sum(len(group.skills) for group in artifact.content.skill_groups)
    planned_counts = tuple(
        (section.section_id, len(section.entries) or section.item_count)
        for section in plan.ordered_sections
    )
    overflow_warning = (
        f'Generated {page_size} PDF spans {pdf_page_count} pages under compact-1page; '
        'consider lowering max projects or certifications.'
        if resolved_preset == 'compact-1page' and pdf_page_count > 1
        else None
    )

    notify(1.0, 'Done.')
    return PipelineResult(
        run_dir=run_dir,
        master_sha256=master_sha256,
        cache_hit=cache_hit,
        candidate_evidence_path=cached_evidence_path,
        job_description_path=jd_txt_path,
        job_analysis_path=job_analysis_path,
        job_match_path=job_match_path,
        section_plan_path=section_plan_path,
        resume_json_path=resume_json_path,
        resume_docx_path=resume_docx_path,
        resume_pdf_path=resume_pdf_path,
        pdf_page_count=pdf_page_count,
        model=artifact.model,
        prompt_version=artifact.prompt_version,
        traceability_verified=artifact.validation.traceability_verified,
        findings=findings,
        skill_count=skill_count,
        supported_count=len(supported_reqs),
        partial_count=len(partial_reqs),
        gap_count=len(gap_reqs),
        not_assessable_count=not_assessable_count,
        supported_requirements=supported_reqs,
        partial_requirements=partial_reqs,
        gap_requirements=gap_reqs,
        planned_section_counts=planned_counts,
        compact_tier=compact_tier,
        overflow_warning=overflow_warning,
        resume_preview_markdown=format_resume_preview_markdown(artifact),
    )
