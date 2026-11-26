'''Minimal local CLI for the resume extraction layer.'''

import argparse
import contextlib
import json
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ValidationError
from pydantic_core import PydanticSerializationError

from resume_assistant import docx_renderer
from resume_assistant import evidence as evidence_module
from resume_assistant import extractor as extractor_module
from resume_assistant import models
from resume_assistant import publication_integrity
from resume_assistant import publication_reader
from resume_assistant import publication_store
from resume_assistant import section_detector as section_detector_module
from resume_assistant.generation_models import TailoredResumeArtifact
from resume_assistant.extractor import InvalidMasterResumeError
from resume_assistant.job_analyzer import JobAnalysisError, JobAnalysisInputError, JobDescriptionAnalyzer
from resume_assistant.job_models import JobDescriptionAnalysis
from resume_assistant.llm_client import AgentRouterClient, LLMClientError, LLMConfig, LLMUnexpectedError
from resume_assistant.matching import CandidateJobMatcher, CandidateJobMatchError, CandidateJobVersionError
from resume_assistant.matching_models import CandidateJobMatch
from resume_assistant.models import CandidateEvidenceCatalog, DocumentSnapshot
from resume_assistant.private_input import PrivateInputError, read_artifact_text
from resume_assistant.private_output import (
    PrivateOutput, PrivateOutputError, is_private_output, normalize_output_path,
)
from resume_assistant.publication import PublicationManifest, PublicationError
from resume_assistant.resume_generator import (
    ResumeGenerationError,
    generate_tailored_resume,
)
from resume_assistant.section_planner import (
    PLAN_SCHEMA_VERSION,
    ExtractionGenerationIdentity,
    ResumeSectionPlan,
    SectionPlanner,
    SectionPlannerError,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MASTER_PATH = PROJECT_ROOT / 'data' / 'private' / 'Master_Resume.docx'
DEFAULT_PROCESSED_DIR = PROJECT_ROOT / 'data' / 'private' / 'processed'
DEFAULT_EVIDENCE_OUTPUT_PATH = DEFAULT_PROCESSED_DIR / 'candidate_evidence.json'
DEFAULT_SNAPSHOT_OUTPUT_PATH = DEFAULT_PROCESSED_DIR / 'document_snapshot.json'
DEFAULT_ANALYSIS_OUTPUT_PATH = DEFAULT_PROCESSED_DIR / 'job_description_analysis.json'
DEFAULT_JOB_MATCH_OUTPUT_PATH = DEFAULT_PROCESSED_DIR / 'job_candidate_match.json'
DEFAULT_SECTION_PLAN_OUTPUT_PATH = DEFAULT_PROCESSED_DIR / 'resume_section_plan.json'
DEFAULT_RESUME_JSON_OUTPUT_PATH = DEFAULT_PROCESSED_DIR / 'tailored_resume.json'
DEFAULT_RESUME_DOCX_OUTPUT_PATH = DEFAULT_PROCESSED_DIR / 'tailored_resume.docx'
_DEFAULT_RENDER_RESUME_PDF = docx_renderer.render_resume_pdf


class ExtractionConfigurationError(ValueError):
    '''Controlled, content-free output configuration error.'''


class ExtractionIntegrityError(ValueError):
    '''Controlled, content-free source or artifact integrity error.'''


class AnalysisIntegrityError(ValueError):
    '''Controlled, content-free analysis artifact integrity error.'''


@dataclass(frozen=True)
class PublicationCommit:
    '''Result of a successful coherent publication commit.'''

    manifest_path: Path
    generation_id: str
    bundle_key: str
    snapshot_path: Path
    catalog_path: Path


def _is_private_output(path: Path) -> bool:
    return is_private_output(PROJECT_ROOT, path)


def _paths_alias(left: Path, right: Path) -> bool:
    if left == right:
        return True
    try:
        return left.samefile(right)
    except FileNotFoundError:
        return False


def _atomic_write_json(path, payload_json: str, *, destination=None) -> None:
    if destination is not None:
        if normalize_output_path(PROJECT_ROOT, path) != destination.path:
            raise PrivateOutputError('Artifact destination does not match its pinned directory handle.')
        destination.write_json(payload_json)
    else:
        with PrivateOutput(PROJECT_ROOT, path) as target:
            target.write_json(payload_json)


def _validation_data(value):
    '''Read mutable model fields without invoking any Pydantic serializer.'''
    if isinstance(value, BaseModel):
        return {key: _validation_data(item) for key, item in {
            **value.__dict__, **(value.model_extra or {}),
        }.items()}
    if isinstance(value, dict):
        return {key: _validation_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_validation_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_validation_data(item) for item in value)
    return value


def _serialize_extraction_artifact(model, artifact) -> tuple[str, BaseModel]:
    '''Validate a fresh raw-field projection, then serialize without value warnings.'''
    validated = model.model_validate(_validation_data(artifact), strict=True)
    payload = validated.model_dump_json(indent=2, warnings='error')
    restored = model.model_validate_json(payload)
    if restored != validated:
        raise ExtractionIntegrityError('Extraction artifact JSON round-trip validation failed.')
    return payload, restored


def _check_captured_hashes(captured, catalog, snapshot) -> None:
    if (
        snapshot.source_sha256 != captured.sha256
        or catalog.source_document.sha256 != captured.sha256
        or any(record.source_sha256 != captured.sha256 for record in catalog.evidence_records)
        or any(part.source_sha256 != captured.sha256 for part in snapshot.header_footer_parts)
    ):
        raise ExtractionIntegrityError('Extraction artifact hashes do not match the captured document input.')


def run_extract_master(master_path, evidence_output_path, snapshot_output_path=None):
    master_path = Path(master_path).resolve()
    evidence_output_path = normalize_output_path(PROJECT_ROOT, evidence_output_path)
    snapshot_output_path = normalize_output_path(
        PROJECT_ROOT, snapshot_output_path if snapshot_output_path is not None else DEFAULT_SNAPSHOT_OUTPUT_PATH,
    )
    if _paths_alias(evidence_output_path, snapshot_output_path):
        raise ExtractionConfigurationError('Evidence and snapshot outputs must be distinct files.')
    if any(_paths_alias(master_path, output) for output in (evidence_output_path, snapshot_output_path)):
        raise ExtractionConfigurationError('Extraction outputs must not alias the source DOCX.')

    # Pin output directories and check identity before any extraction or
    # validation work. This rejects symlinked directory components up front.
    evidence_target = PrivateOutput(PROJECT_ROOT, evidence_output_path)
    snapshot_target = PrivateOutput(PROJECT_ROOT, snapshot_output_path)
    evidence_target.__enter__()
    snapshot_target.__enter__()
    try:
        # Capture the source and extract the snapshot using the existing
        # Mission 2.2.1 immutable byte capture mechanism.
        extractor = extractor_module.MasterResumeExtractor(source=master_path)
        snapshot = extractor.extract()
        captured = extractor.captured_source
        if captured is None:
            raise ExtractionIntegrityError('Extraction did not retain a captured document input.')

        # Validate snapshot structure before evidence projection can hide
        # duplicate or conflicting source structures.
        publication_integrity.validate_snapshot_structure(snapshot)

        # Section detection and evidence catalog construction.
        section_detector = section_detector_module.SectionDetector()
        sections = section_detector.detect(snapshot)
        unclassified = section_detector.find_unclassified(snapshot)
        catalog = evidence_module.build_evidence_catalog(snapshot, sections)

        # Validate catalog structure and the complete snapshot/catalog pair.
        publication_integrity.validate_catalog_structure(catalog)
        publication_integrity.validate_publication_pair(snapshot, catalog)

        # Strict raw-field revalidation and warning-error serialization for both
        # artifacts, then captured-source hash checks.
        catalog_payload, restored_catalog = _serialize_extraction_artifact(CandidateEvidenceCatalog, catalog)
        snapshot_payload, restored_snapshot = _serialize_extraction_artifact(DocumentSnapshot, snapshot)
        _check_captured_hashes(captured, restored_catalog, restored_snapshot)

        # Detect directory-chain mutation before replacing either artifact.
        evidence_target.ensure_current()
        snapshot_target.ensure_current()

        # Reserve an immutable generation and write both artifact envelopes.
        bundle_key = publication_store.resolve_bundle_key(PROJECT_ROOT, evidence_output_path)
        generation_id = uuid.uuid4().hex
        generation_dir = publication_store.reserve_generation(PROJECT_ROOT, bundle_key, generation_id)

        catalog_entry = publication_store.write_artifact_envelope(
            generation_dir,
            kind='catalog',
            payload_schema_version=models.SCHEMA_VERSION,
            payload=restored_catalog.model_dump(mode='json'),
            generation_id=generation_id,
        )
        snapshot_entry = publication_store.write_artifact_envelope(
            generation_dir,
            kind='snapshot',
            payload_schema_version=models.SCHEMA_VERSION,
            payload=restored_snapshot.model_dump(mode='json'),
            generation_id=generation_id,
        )

        # Build the publication manifest. The manifest replacement is the only
        # publication commit point.
        manifest = PublicationManifest(
            bundle_key=bundle_key,
            generation_id=generation_id,
            evidence_output_selector=str(evidence_output_path),
            snapshot_output_selector=str(snapshot_output_path),
            source_filename=captured.filename,
            source_sha256=captured.sha256,
            snapshot=snapshot_entry,
            catalog=catalog_entry,
        )
        publication_store.write_manifest(PROJECT_ROOT, evidence_output_path, manifest)

        commit = PublicationCommit(
            manifest_path=evidence_output_path,
            generation_id=generation_id,
            bundle_key=bundle_key,
            snapshot_path=generation_dir / 'snapshot.json',
            catalog_path=generation_dir / 'catalog.json',
        )
        return catalog, snapshot, sections, unclassified, evidence_output_path, snapshot_output_path, commit
    finally:
        evidence_target.__exit__(None, None, None)
        snapshot_target.__exit__(None, None, None)


def run_analyze_job(input_path, output_path=None):
    try:
        input_path = Path(input_path).resolve()
    except (OSError, TypeError, ValueError, RuntimeError):
        raise JobAnalysisInputError('Job description input path is not readable.') from None
    output_path = normalize_output_path(
        PROJECT_ROOT, output_path if output_path is not None else DEFAULT_ANALYSIS_OUTPUT_PATH,
    )
    try:
        if _paths_alias(input_path, output_path):
            raise ExtractionConfigurationError('Analysis output must not alias the input file.')
    except OSError:
        raise JobAnalysisInputError('Job description input/output file identity could not be checked.') from None

    with PrivateOutput(PROJECT_ROOT, output_path) as target:
        source_text = _read_job_text(input_path)
        client = AgentRouterClient(LLMConfig.from_env())
        analyzer = JobDescriptionAnalyzer(client)
        analysis = analyzer.analyze(source_text)

        payload = analysis.model_dump_json(indent=2)
        restored = JobDescriptionAnalysis.model_validate_json(payload)
        if restored.model_dump() != analysis.model_dump():
            raise AnalysisIntegrityError('Analysis JSON round-trip validation failed.')

        _atomic_write_json(output_path, payload, destination=target)
    return input_path, output_path, client.model, analysis


def _read_job_text(path: Path) -> str:
    '''Read a regular UTF-8 input; expected filesystem failures are controlled.'''
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise JobAnalysisInputError('Job description input must be a regular file.')
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        fd = os.open(path, flags)
        try:
            handle = os.fdopen(fd, 'rb')
        except BaseException:
            os.close(fd)
            raise
        with handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise JobAnalysisInputError('Job description input must be a regular file.')
            return handle.read().decode('utf-8')
    except FileNotFoundError:
        raise JobAnalysisInputError('Job description input file could not be found.') from None
    except UnicodeDecodeError:
        raise JobAnalysisInputError('Job description input file is not valid UTF-8 text.') from None
    except OSError:
        raise JobAnalysisInputError('Job description input file could not be read.') from None


def _parse_match_artifact(text, *, label, schema_version):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key.')
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError('Nonstandard JSON constant.')

    try:
        document = json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (ValueError, RecursionError):
        raise PrivateInputError(f'{label} must contain valid, unambiguous JSON.') from None
    if not isinstance(document, dict) or document.get('schema_version') != schema_version:
        raise CandidateJobVersionError(f'{label} must use schema {schema_version}.')
    return document


def _load_job_analysis_artifact(path) -> JobDescriptionAnalysis:
    text = read_artifact_text(path, artifact='job_analysis')
    document = _parse_match_artifact(text, label='Job analysis artifact', schema_version='2.0.0')
    return JobDescriptionAnalysis.model_validate_json(json.dumps(document))


def _load_candidate_evidence_artifact(
    path, *, include_publication: bool = False,
) -> CandidateEvidenceCatalog | tuple[
    DocumentSnapshot | None, CandidateEvidenceCatalog, PublicationManifest | None, str | None,
]:
    '''Load a standalone catalog or the complete validated publication handoff.

    The publication-aware reader also handles legacy catalogs. Returning its
    complete handoff avoids recapturing an input to obtain planning metadata.
    '''
    if include_publication:
        return publication_reader.load_committed_pair(PROJECT_ROOT, path)
    text = read_artifact_text(path, artifact='candidate_evidence')
    document = _parse_match_artifact(text, label='Candidate evidence artifact', schema_version='3.0.0')
    return CandidateEvidenceCatalog.model_validate_json(json.dumps(document), strict=True)


def run_match_job(analysis_path, candidate_evidence_path, output_path=None) -> CandidateJobMatch:
    output_path = normalize_output_path(
        PROJECT_ROOT, output_path if output_path is not None else DEFAULT_JOB_MATCH_OUTPUT_PATH,
    )
    with PrivateOutput(PROJECT_ROOT, output_path) as target:
        analysis = _load_job_analysis_artifact(analysis_path)
        catalog, _snapshot, manifest = publication_reader.load_candidate_evidence_input(
            PROJECT_ROOT, candidate_evidence_path
        )
        protected_paths = [Path(os.path.abspath(path)) for path in (analysis_path, candidate_evidence_path)]
        if manifest is not None:
            bundle_key = publication_store.resolve_bundle_key(PROJECT_ROOT, protected_paths[1])
            generation_dir = publication_store.generation_dir_path(PROJECT_ROOT, bundle_key, manifest.generation_id)
            protected_paths.extend((generation_dir / 'catalog.json', generation_dir / 'snapshot.json'))
        _reject_matching_output_aliases(output_path, protected_paths)
        matcher = CandidateJobMatcher(catalog)
        match = matcher.assess(analysis)
        payload = match.model_dump_json(indent=2, warnings='error')
        restored = CandidateJobMatch.model_validate_json(payload)
        matcher.validate_result(restored, analysis)
        if restored != match:
            raise AnalysisIntegrityError('Job match JSON round-trip validation failed.')
        _reject_matching_output_aliases(output_path, protected_paths)
        _atomic_write_json(output_path, payload, destination=target)
    return match


def _reject_matching_output_aliases(output_path: Path, protected_paths: list[Path]) -> None:
    try:
        aliases_input = any(_paths_alias(path, output_path) for path in protected_paths)
    except (OSError, ValueError):
        raise PrivateInputError('Matching input/output artifact identity could not be checked.') from None
    if aliases_input:
        raise ExtractionConfigurationError('Job match output must not alias an input artifact.')


def run_plan_sections(
    analysis_path: Path,
    match_path: Path,
    candidate_evidence_path: Path,
    output_path: Path | None = None,
) -> tuple[Path, ResumeSectionPlan]:
    output_path = normalize_output_path(
        PROJECT_ROOT, output_path if output_path is not None else DEFAULT_SECTION_PLAN_OUTPUT_PATH,
    )
    # Normalize lexically, preserving the readers' no-follow symlink checks.
    try:
        analysis_path = Path(os.path.abspath(os.fspath(analysis_path)))
        match_path = Path(os.path.abspath(os.fspath(match_path)))
        candidate_path = Path(os.path.abspath(os.fspath(candidate_evidence_path)))
    except (TypeError, ValueError, OSError):
        raise PrivateInputError('Section planning input paths must be readable local file paths.') from None
    protected_paths = [analysis_path, match_path, candidate_path]

    # Reject explicit aliases before opening any input or output handle.
    _reject_section_plan_aliases(output_path, protected_paths)

    with PrivateOutput(PROJECT_ROOT, output_path) as target:
        analysis = _load_job_analysis_artifact(analysis_path)

        match_text = read_artifact_text(match_path, artifact='candidate_job_match')
        match_document = _parse_match_artifact(
            match_text, label='Job match artifact', schema_version='2.0.0'
        )
        match = CandidateJobMatch.model_validate_json(json.dumps(match_document))

        # Explicit readers already report controlled missing-input errors;
        # committed generation reads can also fail between existence and open.
        try:
            snapshot, catalog, manifest, generation_id = _load_candidate_evidence_artifact(
                candidate_path, include_publication=True,
            )
        except FileNotFoundError:
            raise PrivateInputError('Candidate evidence artifact input file could not be found.') from None

        # The actual manifest address is the path passed to the reader. Manifest
        # selector fields are metadata, not alternate input addresses.
        if manifest is not None:
            bundle_key = publication_store.resolve_bundle_key(PROJECT_ROOT, candidate_path)
            generation_dir = publication_store.generation_dir_path(
                PROJECT_ROOT, bundle_key, generation_id
            )
            protected_paths.extend((
                candidate_path, generation_dir / 'catalog.json', generation_dir / 'snapshot.json',
            ))
            _reject_section_plan_aliases(output_path, protected_paths)

        extraction_generation = ExtractionGenerationIdentity.from_manifest(manifest) if manifest is not None else None
        planner = SectionPlanner()
        plan = planner.plan(
            analysis, match, catalog, snapshot, manifest_available=manifest is not None,
            extraction_generation=extraction_generation,
        )

        payload = plan.model_dump_json(indent=2, warnings='error')
        restored = ResumeSectionPlan.model_validate_json(payload)
        if restored != plan:
            raise AnalysisIntegrityError('Section plan JSON round-trip validation failed.')
        plan = planner.validate_result(
            restored, analysis, match, catalog, snapshot,
            manifest_available=manifest is not None, extraction_generation=extraction_generation,
        )
        # Recheck identities after planning/serialization, before publication.
        _reject_section_plan_aliases(output_path, protected_paths)
        target.write_json(payload)

        _print_plan_summary(plan, output_path)
    return output_path, plan


def _reject_section_plan_aliases(output_path: Path, protected_paths: list[Path]) -> None:
    '''Reject lexical equality and existing filesystem aliases without content reads.'''
    try:
        aliases_input = any(_paths_alias(path, output_path) for path in protected_paths)
    except (OSError, ValueError):
        raise PrivateInputError(
            'Section planning input/output artifact identity could not be checked.'
        ) from None
    if aliases_input:
        raise ExtractionConfigurationError('Section plan output must not alias an input artifact.')


def _load_section_plan_artifact(path) -> ResumeSectionPlan:
    text = read_artifact_text(path, artifact='section_plan')
    document = _parse_match_artifact(
        text, label='Section plan artifact', schema_version=PLAN_SCHEMA_VERSION
    )
    return ResumeSectionPlan.model_validate_json(json.dumps(document))


def _reject_generation_aliases(
    output_paths: list[Path], protected_paths: list[Path]
) -> None:
    '''Reject input aliases and a JSON/DOCX output collision without content reads.'''
    try:
        aliases_input = any(
            _paths_alias(path, output)
            for output in output_paths
            for path in protected_paths
        )
        outputs_alias = any(
            _paths_alias(output_paths[i], output_paths[j])
            for i in range(len(output_paths))
            for j in range(i + 1, len(output_paths))
        )
    except (OSError, ValueError):
        raise PrivateInputError(
            'Resume generation input/output artifact identity could not be checked.'
        ) from None
    if aliases_input:
        raise ExtractionConfigurationError(
            'Resume generation outputs must not alias an input artifact.'
        )
    if outputs_alias:
        raise ExtractionConfigurationError(
            'Resume generation output paths must be distinct files.'
        )


def run_generate_resume(
    plan_path: Path,
    candidate_evidence_path: Path,
    output_json_path: Path | None = None,
    output_docx_path: Path | None = None,
    output_pdf_path: Path | None = None,
    *,
    page_size: str = docx_renderer.DEFAULT_PAGE_SIZE,
    strict_metrics: bool = False,
) -> tuple[Path, Path | None, TailoredResumeArtifact]:
    if output_docx_path is None and output_pdf_path is None:
        raise ExtractionConfigurationError(
            'At least one of --output-docx or --output-pdf must be provided.'
        )
    output_json_path = normalize_output_path(
        PROJECT_ROOT,
        output_json_path if output_json_path is not None else DEFAULT_RESUME_JSON_OUTPUT_PATH,
    )
    if output_docx_path is not None:
        output_docx_path = normalize_output_path(PROJECT_ROOT, output_docx_path)
    if output_pdf_path is not None:
        output_pdf_path = normalize_output_path(PROJECT_ROOT, output_pdf_path)
    docx_renderer.resolve_page_size(page_size)
    if output_pdf_path is not None and docx_renderer.render_resume_pdf is _DEFAULT_RENDER_RESUME_PDF:
        docx_renderer.find_pdf_converter()
    try:
        plan_path = Path(os.path.abspath(os.fspath(plan_path)))
        candidate_path = Path(os.path.abspath(os.fspath(candidate_evidence_path)))
    except (TypeError, ValueError, OSError):
        raise PrivateInputError(
            'Resume generation input paths must be readable local file paths.'
        ) from None

    outputs = [output_json_path]
    if output_docx_path is not None:
        outputs.append(output_docx_path)
    if output_pdf_path is not None:
        outputs.append(output_pdf_path)
    protected_paths = [plan_path, candidate_path]
    _reject_generation_aliases(outputs, protected_paths)

    json_target = PrivateOutput(PROJECT_ROOT, output_json_path)
    docx_target = (
        PrivateOutput(PROJECT_ROOT, output_docx_path)
        if output_docx_path is not None else None
    )
    pdf_target = (
        PrivateOutput(PROJECT_ROOT, output_pdf_path)
        if output_pdf_path is not None else None
    )
    with contextlib.ExitStack() as stack:
        stack.enter_context(json_target)
        if docx_target is not None:
            stack.enter_context(docx_target)
        if pdf_target is not None:
            stack.enter_context(pdf_target)
        plan = _load_section_plan_artifact(plan_path)

        try:
            snapshot, catalog, manifest, generation_id = _load_candidate_evidence_artifact(
                candidate_path, include_publication=True,
            )
        except FileNotFoundError:
            raise PrivateInputError(
                'Candidate evidence artifact input file could not be found.'
            ) from None

        if manifest is not None:
            bundle_key = publication_store.resolve_bundle_key(PROJECT_ROOT, candidate_path)
            generation_dir = publication_store.generation_dir_path(
                PROJECT_ROOT, bundle_key, generation_id
            )
            protected_paths.extend((
                candidate_path,
                generation_dir / 'catalog.json',
                generation_dir / 'snapshot.json',
            ))
            _reject_generation_aliases(outputs, protected_paths)

        client = AgentRouterClient(LLMConfig.from_env())
        artifact = generate_tailored_resume(
            plan, catalog, snapshot, manifest, client, strict_metrics=strict_metrics,
        )

        payload = artifact.model_dump_json(indent=2, warnings='error')
        restored = TailoredResumeArtifact.model_validate_json(payload)
        if restored != artifact:
            raise AnalysisIntegrityError(
                'Tailored resume JSON round-trip validation failed.'
            )

        docx_bytes = (
            docx_renderer.render_docx_bytes(artifact, page_size=page_size)
            if docx_target is not None else None
        )

        # Recheck identities after generation/serialization, before publication.
        _reject_generation_aliases(outputs, protected_paths)
        json_target.ensure_current()
        if docx_target is not None:
            docx_target.ensure_current()
        if pdf_target is not None:
            pdf_target.ensure_current()

        if pdf_target is not None and docx_target is None:
            docx_renderer.render_resume_pdf(
                artifact, output_pdf_path, page_size=page_size
            )
            pdf_target.ensure_current()
            json_target.write_json(payload)
        else:
            json_target.write_json(payload)
            if docx_target is not None and docx_bytes is not None:
                docx_target.write_bytes(docx_bytes)
            if pdf_target is not None and output_pdf_path is not None:
                docx_renderer.render_resume_pdf(
                    artifact,
                    output_pdf_path,
                    page_size=page_size,
                    docx_path=output_docx_path,
                )
                pdf_target.ensure_current()

    _print_generation_summary(artifact, output_json_path, output_docx_path, output_pdf_path)
    return output_json_path, output_docx_path, artifact


def _print_generation_summary(
    artifact,
    output_json_path,
    output_docx_path,
    output_pdf_path=None,
) -> None:
    # Aggregate counts and verification flags only; never candidate content.
    report = artifact.validation
    counts = {'error': 0, 'warning': 0}
    for finding in report.findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    print('Tailored resume generation summary')
    print(f'  schema_version: {artifact.schema_version}')
    print(f'  prompt_version: {artifact.prompt_version}')
    print(f'  plan_schema_version: {artifact.plan_schema_version}')
    print(f'  model: {artifact.model}')
    print(f'  header_source_evidence_records: {len(artifact.header.source_evidence_ids)}')
    print(f'  summary_sentences: {len(artifact.content.summary_sentences)}')
    print(f'  skill_groups: {len(artifact.content.skill_groups)}')
    print(f'  sections: {len(artifact.content.sections)}')
    print(f'  traceability_verified: {str(report.traceability_verified).lower()}')
    print(
        f'  semantic_entailment_verified: '
        f'{str(report.semantic_entailment_verified).lower()}'
    )
    print(f'  human_review_required: {str(report.human_review_required).lower()}')
    print(f'  findings_error: {counts.get("error", 0)}')
    print(f'  findings_warning: {counts.get("warning", 0)}')
    print(f'  output_json: {output_json_path}')
    if output_docx_path is not None:
        print(f'  output_docx: {output_docx_path}')
    if output_pdf_path is not None:
        print(f'  output_pdf: {output_pdf_path}')


def _print_match_summary(match: CandidateJobMatch, output_path) -> None:
    # Aggregate counts, provenance, and limitation indicators only; never
    # candidate content or complete matching records.
    counts = {
        'supported': 0,
        'partially_supported': 0,
        'no_evidence_found': 0,
        'not_assessable': 0,
    }
    for requirement in match.requirements:
        counts[requirement.status] = counts.get(requirement.status, 0) + 1
    print('Candidate job match summary')
    print(f'  source_job_schema_version: {match.source_job_schema_version}')
    print(f'  candidate_evidence_schema_version: {match.candidate_evidence_schema_version}')
    print(f'  candidate_source_hash_present: {bool(match.candidate_source_sha256)}')
    print(f'  coverage_status: {match.coverage_status}')
    for status in ('supported', 'partially_supported', 'no_evidence_found', 'not_assessable'):
        print(f'  {status}: {counts.get(status, 0)}')
    print(f'  human_review_required: {str(match.human_review_required).lower()}')
    print(f'  limitations: {len(match.limitations)}')
    print(f'  output: {output_path}')


def _print_plan_summary(plan: ResumeSectionPlan, output_path) -> None:
    # Aggregate counts, provenance, and limitation indicators only; never
    # candidate content or complete matching records.
    counts = {
        'supported': 0,
        'partially_supported': 0,
        'no_evidence_found': 0,
        'not_assessable': 0,
    }
    for coverage in plan.requirement_coverage:
        counts[coverage.status] = counts.get(coverage.status, 0) + 1
    print('Resume section plan summary')
    print(f'  schema_version: {plan.schema_version}')
    print(
        f'  source_candidate_evidence_schema_version: '
        f'{plan.planning_metadata.source_candidate_evidence_schema_version}'
    )
    print(
        f'  source_job_analysis_schema_version: '
        f'{plan.planning_metadata.source_job_analysis_schema_version}'
    )
    print(
        f'  source_job_match_schema_version: '
        f'{plan.planning_metadata.source_job_match_schema_version}'
    )
    print(f'  selected_evidence_records: {len(plan.selected_evidence_ids)}')
    print(f'  planned_sections: {len(plan.ordered_sections)}')
    print(f'  prioritized_skills: {len(plan.prioritized_skills)}')
    for status in ('supported', 'partially_supported', 'no_evidence_found', 'not_assessable'):
        print(f'  {status}: {counts.get(status, 0)}')
    print(f'  excluded_evidence: {len(plan.excluded_evidence)}')
    print(f'  human_review_required: {str(plan.human_review_required).lower()}')
    print(f'  output: {output_path}')


def _print_analysis_summary(model, analysis, input_path, output_path) -> None:
    # Aggregate statistics and presence booleans only — never extracted field
    # values, source text, or model responses.
    print('Job description analysis summary')
    print(f'  source: {input_path.name}')
    print(f'  model: {model}')
    print(f'  role_title_present: {analysis.role_title is not None}')
    print(f'  company_name_present: {analysis.company_name is not None}')
    print(f'  seniority_level_present: {analysis.seniority_level is not None}')
    print(f'  employment_type_present: {analysis.employment_type is not None}')
    print(f'  work_arrangement_present: {analysis.work_arrangement is not None}')
    print(f'  responsibilities: {len(analysis.responsibilities)}')
    print(f'  required_skills: {len(analysis.required_skills)}')
    print(f'  preferred_skills: {len(analysis.preferred_skills)}')
    print(f'  uncertain_skills: {len(analysis.uncertain_skills)}')
    print('  interpretations_verified: False')
    print(f'  education_requirements: {len(analysis.education_requirements)}')
    print(f'  experience_requirements: {len(analysis.experience_requirements)}')
    print(f'  languages: {len(analysis.languages)}')
    print(f'  certifications: {len(analysis.certifications)}')
    print(f'  ambiguities: {len(analysis.ambiguities)}')
    print(f'  missing_information: {len(analysis.missing_information)}')
    print(f'  output: {output_path}')


def _print_summary(catalog, snapshot, sections, unclassified, output_path, snapshot_output_path, commit) -> None:
    statistics = snapshot.statistics
    completeness = catalog.extraction_completeness
    print('Master Resume extraction summary')
    print(f'  source: {snapshot.source_filename}')
    print(f'  source_sha256: {snapshot.source_sha256}')
    print(f'  body_blocks: {statistics.block_count}')
    print(f'  body_paragraphs: {statistics.body_paragraph_count}')
    print(f'  table_paragraphs: {statistics.table_paragraph_count}')
    print(f'  header_paragraphs: {statistics.header_paragraph_count}')
    print(f'  footer_paragraphs: {statistics.footer_paragraph_count}')
    print(f'  total_paragraphs: {statistics.total_paragraph_count}')
    print(
        f'  tables: {statistics.table_count} rows: {statistics.row_count} '
        f'cells: {statistics.cell_count}'
    )
    print(f'  empty_paragraphs: {statistics.empty_paragraph_count}')
    print(f'  words: {statistics.word_count}')
    print(f'  hyperlinks: {statistics.hyperlink_count}')
    print(
        f'  images: {statistics.image_count} drawings: {statistics.drawing_count} '
        f'embedded_objects: {statistics.embedded_object_count} textboxes: {statistics.textbox_count}'
    )
    print(f'  detected_sections: {len(sections)}')
    print(f'  evidence_records: {len(catalog.evidence_records)}')
    print(f'  unclassified_blocks: {len(unclassified)}')
    print(f'  unsupported_content_items: {len(snapshot.unsupported_content)}')
    print(f'  source_preservation_complete: {completeness.source_preservation_complete}')
    print(f'  section_classification_complete: {completeness.section_classification_complete}')
    print(f'  complete: {completeness.complete}')
    print(f'  evidence_output: {output_path}')
    print(f'  snapshot_artifact: {commit.snapshot_path}')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog='resume_assistant',
        description='Local extraction layer for the Master Resume.',
    )
    subparsers = parser.add_subparsers(dest='command', required=True)
    extract_parser = subparsers.add_parser(
        'extract-master',
        help='Extract structured candidate evidence from the Master Resume.',
    )
    extract_parser.add_argument(
        '--master',
        default=str(DEFAULT_MASTER_PATH),
        help='Path to the Master Resume DOCX.',
    )
    extract_parser.add_argument(
        '--output',
        default=str(DEFAULT_EVIDENCE_OUTPUT_PATH),
        help='Path for the generated candidate_evidence.json artifact.',
    )
    extract_parser.add_argument(
        '--snapshot-output',
        default=str(DEFAULT_SNAPSHOT_OUTPUT_PATH),
        help='Logical snapshot selector recorded in the publication manifest; the immutable snapshot is stored in the committed generation.',
    )
    analyze_parser = subparsers.add_parser(
        'analyze-job',
        help='Analyze a job description into structured, validated JSON.',
    )
    analyze_parser.add_argument(
        '--input',
        required=True,
        help='Path to the UTF-8 job description text file.',
    )
    analyze_parser.add_argument(
        '--output',
        default=str(DEFAULT_ANALYSIS_OUTPUT_PATH),
        help='Path for the generated job_description_analysis.json artifact.',
    )
    match_parser = subparsers.add_parser(
        'match-job',
        help='Assess candidate evidence against a validated job description analysis.',
    )
    match_parser.add_argument(
        '--analysis',
        default=str(DEFAULT_ANALYSIS_OUTPUT_PATH),
        help='Path to the validated JobDescriptionAnalysis JSON artifact.',
    )
    match_parser.add_argument(
        '--candidate-evidence',
        default=str(DEFAULT_EVIDENCE_OUTPUT_PATH),
        help='Path to the validated CandidateEvidenceCatalog JSON artifact.',
    )
    match_parser.add_argument(
        '--output',
        default=str(DEFAULT_JOB_MATCH_OUTPUT_PATH),
        help='Path for the generated candidate job match JSON artifact.',
    )
    plan_parser = subparsers.add_parser(
        'plan-sections',
        help='Plan resume sections from candidate evidence, job analysis, and job match.',
    )
    plan_parser.add_argument(
        '--analysis',
        required=True,
        help='Path to the validated JobDescriptionAnalysis JSON artifact.',
    )
    plan_parser.add_argument(
        '--match',
        required=True,
        help='Path to the validated CandidateJobMatch JSON artifact.',
    )
    plan_parser.add_argument(
        '--candidate-evidence',
        required=True,
        help='Path to the validated CandidateEvidenceCatalog JSON artifact.',
    )
    plan_parser.add_argument(
        '--output',
        default=str(DEFAULT_SECTION_PLAN_OUTPUT_PATH),
        help='Path for the generated resume section plan JSON artifact.',
    )
    generate_parser = subparsers.add_parser(
        'generate-resume',
        help='Generate a validated, evidence-bound tailored resume JSON, DOCX, and/or PDF.',
    )
    generate_parser.add_argument(
        '--plan',
        required=True,
        help='Path to the validated schema-2.0.0 resume section plan JSON artifact.',
    )
    generate_parser.add_argument(
        '--candidate-evidence',
        required=True,
        help='Path to the validated candidate evidence manifest or catalog JSON artifact.',
    )
    generate_parser.add_argument(
        '--output-json',
        default=str(DEFAULT_RESUME_JSON_OUTPUT_PATH),
        help='Path for the validated tailored resume JSON artifact.',
    )
    generate_parser.add_argument(
        '--output-docx',
        default=None,
        help='Optional path for the rendered tailored resume DOCX artifact.',
    )
    generate_parser.add_argument(
        '--output-pdf',
        default=None,
        help='Optional path for the LibreOffice-converted tailored resume PDF artifact.',
    )
    generate_parser.add_argument(
        '--page-size',
        default=docx_renderer.DEFAULT_PAGE_SIZE,
        choices=sorted(docx_renderer.PAGE_SIZES),
        help=f'DOCX page size (default: {docx_renderer.DEFAULT_PAGE_SIZE}).',
    )
    generate_parser.add_argument(
        '--strict-metrics',
        action='store_true',
        help='Treat ungrounded numeric claims as hard failures.',
    )
    args = parser.parse_args(argv)
    if args.command == 'extract-master':
        try:
            catalog, snapshot, sections, unclassified, output_path, snapshot_output_path, _commit = run_extract_master(
                master_path=args.master,
                evidence_output_path=Path(args.output),
                snapshot_output_path=Path(args.snapshot_output),
            )
        except (PrivateOutputError, ExtractionConfigurationError, ExtractionIntegrityError) as error:
            print(f'error: {error}')
            return 2
        except FileNotFoundError:
            print('error: a required source or output path could not be found')
            return 2
        except InvalidMasterResumeError:
            print('error: source is not a readable DOCX document')
            return 2
        except ValidationError:
            print('error: artifact validation failed: validation error')
            return 2
        except PydanticSerializationError:
            print('error: artifact serialization failed')
            return 2
        except PrivateInputError as error:
            print(f'error: {error}')
            return 2
        except PublicationError as error:
            print(f'error: {error}')
            return 2
        except OSError:
            print('error: extraction failed: OSError')
            return 3
        except ValueError:
            print('error: invalid document data encountered during extraction')
            return 2
        except Exception as error:  # noqa: BLE001
            print(f'error: extraction failed: {type(error).__name__}')
            return 3
        _print_summary(catalog, snapshot, sections, unclassified, output_path, snapshot_output_path, _commit)
        return 0
    if args.command == 'analyze-job':
        try:
            input_path, output_path, model, analysis = run_analyze_job(
                input_path=args.input,
                output_path=Path(args.output),
            )
        except FileNotFoundError:
            print('error: a required source or output path could not be found')
            return 2
        except UnicodeDecodeError:
            print('error: input file is not valid UTF-8 text')
            return 2
        except (PrivateOutputError, ExtractionConfigurationError, AnalysisIntegrityError) as error:
            print(f'error: {error}')
            return 2
        except LLMUnexpectedError as error:
            print(f'error: analysis failed: {error.failure_type}')
            return 3
        except (JobAnalysisError, LLMClientError) as error:
            print(f'error: {error}')
            return 2
        except ValidationError:
            print('error: artifact validation failed: validation error')
            return 2
        except Exception as error:  # noqa: BLE001
            print(f'error: analysis failed: {type(error).__name__}')
            return 3
        _print_analysis_summary(model, analysis, input_path, output_path)
        return 0
    if args.command == 'match-job':
        try:
            match = run_match_job(
                analysis_path=Path(args.analysis),
                candidate_evidence_path=Path(args.candidate_evidence),
                output_path=Path(args.output),
            )
        except FileNotFoundError:
            print('error: a required source or output path could not be found')
            return 2
        except (PrivateOutputError, ExtractionConfigurationError, AnalysisIntegrityError) as error:
            print(f'error: {error}')
            return 2
        except CandidateJobVersionError as error:
            print(f'error: {error}')
            return 2
        except (CandidateJobMatchError, PrivateInputError, PublicationError) as error:
            print(f'error: {error}')
            return 2
        except ValidationError:
            print('error: artifact validation failed: validation error')
            return 2
        except PydanticSerializationError:
            print('error: matching artifact serialization failed')
            return 2
        except OSError:
            print('error: job match failed: OSError')
            return 3
        except Exception as error:  # noqa: BLE001
            print(f'error: job match failed: {type(error).__name__}')
            return 3
        _print_match_summary(match, Path(args.output))
        return 0
    if args.command == 'plan-sections':
        try:
            output_path, _plan = run_plan_sections(
                analysis_path=Path(args.analysis),
                match_path=Path(args.match),
                candidate_evidence_path=Path(args.candidate_evidence),
                output_path=Path(args.output),
            )
        except UnicodeDecodeError:
            print('error: input file is not valid UTF-8 text')
            return 2
        except (
            PrivateOutputError,
            ExtractionConfigurationError,
            AnalysisIntegrityError,
            SectionPlannerError,
            PrivateInputError,
            CandidateJobMatchError,
        ) as error:
            print(f'error: {error}')
            return 2
        except ValidationError:
            print('error: artifact validation failed: validation error')
            return 2
        except PydanticSerializationError:
            print('error: section plan serialization failed')
            return 2
        except PublicationError as error:
            print(f'error: {error}')
            return 2
        except OSError:
            print('error: section planning failed: OSError')
            return 3
        except Exception as error:  # noqa: BLE001
            print(f'error: section planning failed: {type(error).__name__}')
            return 3
        return 0
    if args.command == 'generate-resume':
        try:
            run_generate_resume(
                plan_path=Path(args.plan),
                candidate_evidence_path=Path(args.candidate_evidence),
                output_json_path=Path(args.output_json),
                output_docx_path=Path(args.output_docx) if args.output_docx is not None else None,
                output_pdf_path=Path(args.output_pdf) if args.output_pdf is not None else None,
                page_size=args.page_size,
                strict_metrics=args.strict_metrics,
            )
        except UnicodeDecodeError:
            print('error: input file is not valid UTF-8 text')
            return 2
        except (
            PrivateOutputError,
            ExtractionConfigurationError,
            AnalysisIntegrityError,
            ResumeGenerationError,
            docx_renderer.DocxRenderError,
            PrivateInputError,
            CandidateJobMatchError,
        ) as error:
            print(f'error: {error}')
            return 2
        except PublicationError as error:
            print(f'error: {error}')
            return 2
        except ValidationError:
            print('error: artifact validation failed: validation error')
            return 2
        except PydanticSerializationError:
            print('error: resume generation serialization failed')
            return 2
        except LLMUnexpectedError as error:
            print(f'error: resume generation failed: {error.failure_type}')
            return 3
        except LLMClientError as error:
            print(f'error: {error}')
            return 2
        except OSError:
            print('error: resume generation failed: OSError')
            return 3
        except Exception as error:  # noqa: BLE001
            print(f'error: resume generation failed: {type(error).__name__}')
            return 3
        return 0
    parser.print_help()
    return 2
