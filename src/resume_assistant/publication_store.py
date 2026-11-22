'''Secure immutable-generation storage for publication artifacts.

Creates generation directories and artifact envelopes under the private
processed boundary using pinned no-follow directory handles, exclusive
file creation, and descriptor-relative atomic replacement. Manifest
replacement at the --output address is the sole publication commit point.
'''

import hashlib
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any

from resume_assistant import private_input
from resume_assistant.models import (
    CandidateEvidenceCatalog,
    DocumentSnapshot,
    SCHEMA_VERSION,
)
from resume_assistant.private_output import normalize_output_path
from resume_assistant.publication import (
    ArtifactEntry,
    GenerationEnvelope,
    MANIFEST_FORMAT_KIND,
    PUBLICATION_FORMAT_VERSION,
    PublicationManifest,
    PublicationError,
)

EXTRACTIONS_DIR_NAME = '.extractions'
GENERATIONS_DIR_NAME = 'generations'
PRIVATE_PROCESSED_COMPONENTS = ('data', 'private', 'processed')


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key.')
        result[key] = value
    return result


def _reject_json_constant(value):
    raise ValueError('Nonstandard JSON constant.')


def resolve_bundle_key(project_root: Path, manifest_address: Path) -> str:
    '''SHA-256 of the manifest address relative to the private processed root.'''
    root = Path(os.path.abspath(project_root))
    target = Path(os.path.abspath(manifest_address))
    try:
        relative = target.relative_to(root)
    except ValueError as error:
        raise PublicationError('Manifest address is not under the project root.') from error
    if relative.parts[:3] != PRIVATE_PROCESSED_COMPONENTS:
        raise PublicationError('Manifest address is not under data/private/processed.')
    digest_input = str(Path(*relative.parts[3:]))
    return hashlib.sha256(digest_input.encode('utf-8')).hexdigest()


def generation_store_path(project_root: Path, bundle_key: str) -> Path:
    return (
        Path(os.path.abspath(project_root))
        / 'data'
        / 'private'
        / 'processed'
        / EXTRACTIONS_DIR_NAME
        / bundle_key
    )


def generation_dir_path(
    project_root: Path, bundle_key: str, generation_id: str
) -> Path:
    return generation_store_path(project_root, bundle_key) / GENERATIONS_DIR_NAME / generation_id


def _open_directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0)


def _write_file_flags() -> int:
    return (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_NOFOLLOW
        | getattr(os, 'O_CLOEXEC', 0)
    )


def _ensure_private_processed_dir(project_root: Path) -> None:
    '''Create data/private/processed/.extractions with pinned no-follow handles.'''
    root = Path(os.path.abspath(project_root))
    flags = _open_directory_flags()
    root_fd = os.open(str(root), flags)
    fds_to_close = [root_fd]
    try:
        components = ('data', 'private', 'processed', EXTRACTIONS_DIR_NAME)
        parent_fd = root_fd
        for component in components:
            try:
                os.mkdir(component, mode=0o700, dir_fd=parent_fd)
            except FileExistsError:
                pass
            child_fd = os.open(component, flags, dir_fd=parent_fd)
            fds_to_close.append(child_fd)
            current = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
            pinned = os.fstat(child_fd)
            if not stat.S_ISDIR(current.st_mode):
                raise PublicationError(
                    'Private output directory chain is not a directory.'
                )
            if (current.st_dev, current.st_ino) != (pinned.st_dev, pinned.st_ino):
                raise PublicationError(
                    'Private output directory identity changed during creation.'
                )
            parent_fd = child_fd
    finally:
        for fd in fds_to_close:
            try:
                os.close(fd)
            except OSError:
                pass


def reserve_generation(
    project_root: Path, bundle_key: str, generation_id: str
) -> Path:
    '''Exclusively reserve a generation directory. Never overwrites existing.'''
    _ensure_private_processed_dir(project_root)
    store = generation_store_path(project_root, bundle_key)
    try:
        store.mkdir(mode=0o700, exist_ok=True)
    except FileExistsError:
        if not store.is_dir():
            raise PublicationError('Bundle store path is not a directory.') from None
    generations = store / GENERATIONS_DIR_NAME
    try:
        generations.mkdir(mode=0o700, exist_ok=True)
    except FileExistsError:
        if not generations.is_dir():
            raise PublicationError('Generations path is not a directory.') from None
    target = generations / generation_id
    try:
        target.mkdir(mode=0o700)
    except FileExistsError:
        raise PublicationError('Generation directory already exists.') from None
    return target


def _serialize_envelope(
    *,
    generation_id: str,
    artifact_kind: str,
    payload_schema_version: str,
    payload: dict[str, Any],
) -> str:
    envelope = GenerationEnvelope(
        generation_id=generation_id,
        artifact_kind=artifact_kind,  # type: ignore[arg-type]
        payload_schema_version=payload_schema_version,
        payload=payload,
    )
    return envelope.model_dump_json(indent=2, warnings='error')


def write_artifact_envelope(
    generation_dir: Path,
    *,
    kind: str,
    payload_schema_version: str,
    payload: dict[str, Any],
    generation_id: str,
) -> ArtifactEntry:
    '''Write an immutable artifact envelope and return its manifest entry.

    The envelope is exclusively created, written, closed, and atomically
    replaced into the generation directory. Bytes are read back and
    checksum/length verified before returning.
    '''
    artifact_name = f'{kind}.json'
    relative_path = f'{GENERATIONS_DIR_NAME}/{generation_dir.name}/{artifact_name}'
    payload_json = _serialize_envelope(
        generation_id=generation_id,
        artifact_kind=kind,
        payload_schema_version=payload_schema_version,
        payload=payload,
    )
    payload_bytes = payload_json.encode('utf-8')
    sha256 = hashlib.sha256(payload_bytes).hexdigest()
    byte_length = len(payload_bytes)

    dir_fd = os.open(str(generation_dir), _open_directory_flags())
    try:
        fd = None
        temporary_name = None
        try:
            for _ in range(32):
                candidate = f'.resume-assistant-{secrets.token_hex(16)}.tmp'
                try:
                    fd = os.open(
                        candidate,
                        _write_file_flags(),
                        0o600,
                        dir_fd=dir_fd,
                    )
                    break
                except FileExistsError:
                    continue
            if fd is None:
                raise OSError('Could not create an exclusive artifact temporary file.')
            temporary_name = candidate
            handle = os.fdopen(fd, 'w', encoding='utf-8')
            fd = None
            with handle:
                handle.write(payload_json)
            os.replace(
                temporary_name, artifact_name,
                src_dir_fd=dir_fd, dst_dir_fd=dir_fd,
            )
            temporary_name = None
        finally:
            if fd is not None:
                os.close(fd)
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name, dir_fd=dir_fd)
                except FileNotFoundError:
                    pass
    finally:
        os.close(dir_fd)

    # Read back and verify exact bytes.
    written = (generation_dir / artifact_name).read_bytes()
    if written != payload_bytes:
        raise PublicationError('Artifact envelope write verification failed.')
    if hashlib.sha256(written).hexdigest() != sha256:
        raise PublicationError('Artifact envelope checksum verification failed.')

    return ArtifactEntry(
        kind=kind,  # type: ignore[arg-type]
        payload_schema_version=payload_schema_version,
        relative_path=relative_path,
        sha256=sha256,
        byte_length=byte_length,
    )


def write_manifest(
    project_root: Path,
    manifest_address: Path,
    manifest: PublicationManifest,
) -> None:
    '''Atomically replace the manifest at --output address.'''
    manifest_path = normalize_output_path(project_root, manifest_address)
    parent = manifest_path.parent
    parent_fd = os.open(str(parent), _open_directory_flags())
    try:
        manifest_json = manifest.model_dump_json(indent=2, warnings='error')
        manifest_bytes = manifest_json.encode('utf-8')

        fd = None
        temporary_name = None
        try:
            for _ in range(32):
                candidate = f'.resume-assistant-{secrets.token_hex(16)}.tmp'
                try:
                    fd = os.open(
                        candidate,
                        _write_file_flags(),
                        0o600,
                        dir_fd=parent_fd,
                    )
                    break
                except FileExistsError:
                    continue
            if fd is None:
                raise OSError('Could not create an exclusive manifest temporary file.')
            temporary_name = candidate
            handle = os.fdopen(fd, 'w', encoding='utf-8')
            fd = None
            with handle:
                handle.write(manifest_json)
            os.replace(
                temporary_name, manifest_path.name,
                src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
            )
            temporary_name = None
        finally:
            if fd is not None:
                os.close(fd)
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
    finally:
        os.close(parent_fd)


def read_manifest(project_root: Path, manifest_address: Path) -> PublicationManifest:
    '''Read and validate a publication manifest from the --output address.'''
    manifest_path = normalize_output_path(project_root, manifest_address)
    text = private_input.read_artifact_text(manifest_path, artifact='candidate_evidence')
    try:
        document = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (ValueError, RecursionError) as error:
        raise PublicationError('Manifest must contain valid, unambiguous JSON.') from error
    if not isinstance(document, dict):
        raise PublicationError('Manifest must be a JSON object.')
    if document.get('format') != MANIFEST_FORMAT_KIND:
        raise PublicationError('Input is not a publication manifest.')
    return PublicationManifest.model_validate(document, strict=True)


def read_generation_artifacts(
    project_root: Path,
    bundle_key: str,
    generation_id: str,
    manifest: PublicationManifest,
) -> tuple[DocumentSnapshot, CandidateEvidenceCatalog]:
    '''Load and verify the mandatory snapshot/catalog pair of a committed generation.

    Both artifacts are mandatory. A missing snapshot or catalog rejects the
    managed generation; publication failure before the manifest commit leaves
    no committed generation to read, so there is no optional snapshot path.
    '''
    gen_dir = generation_dir_path(project_root, bundle_key, generation_id)
    snapshot_path = gen_dir / 'snapshot.json'
    catalog_path = gen_dir / 'catalog.json'

    if not catalog_path.exists():
        raise PublicationError('Committed catalog artifact is missing.')
    catalog_bytes = catalog_path.read_bytes()
    catalog_entry = manifest.catalog

    if len(catalog_bytes) != catalog_entry.byte_length:
        raise PublicationError('Catalog byte length disagrees with manifest.')
    if hashlib.sha256(catalog_bytes).hexdigest() != catalog_entry.sha256:
        raise PublicationError('Catalog checksum disagrees with manifest.')

    catalog_envelope = GenerationEnvelope.model_validate_json(
        catalog_bytes, strict=True
    )

    if catalog_envelope.generation_id != generation_id:
        raise PublicationError('Catalog envelope generation ID disagrees with manifest.')
    if catalog_envelope.artifact_kind != 'catalog':
        raise PublicationError('Catalog envelope artifact kind disagrees with manifest.')
    if catalog_envelope.payload_schema_version != SCHEMA_VERSION:
        raise PublicationError('Catalog payload schema version is not supported.')

    catalog = CandidateEvidenceCatalog.model_validate_json(
        json.dumps(catalog_envelope.payload), strict=True
    )

    if not snapshot_path.exists():
        raise PublicationError('Committed snapshot artifact is missing.')
    snapshot_bytes = snapshot_path.read_bytes()
    snapshot_entry = manifest.snapshot

    if len(snapshot_bytes) != snapshot_entry.byte_length:
        raise PublicationError('Snapshot byte length disagrees with manifest.')
    if hashlib.sha256(snapshot_bytes).hexdigest() != snapshot_entry.sha256:
        raise PublicationError('Snapshot checksum disagrees with manifest.')

    snapshot_envelope = GenerationEnvelope.model_validate_json(
        snapshot_bytes, strict=True
    )
    if snapshot_envelope.format_version != PUBLICATION_FORMAT_VERSION:
        raise PublicationError('Snapshot envelope format version is not supported.')
    if snapshot_envelope.generation_id != generation_id:
        raise PublicationError('Snapshot envelope generation ID disagrees with manifest.')
    if snapshot_envelope.artifact_kind != 'snapshot':
        raise PublicationError('Snapshot envelope artifact kind disagrees with manifest.')
    if snapshot_envelope.payload_schema_version != SCHEMA_VERSION:
        raise PublicationError('Snapshot payload schema version is not supported.')

    snapshot = DocumentSnapshot.model_validate_json(
        json.dumps(snapshot_envelope.payload), strict=True
    )

    if manifest.catalog.relative_path != f'generations/{generation_id}/catalog.json':
        raise PublicationError('Manifest catalog path disagrees with committed generation.')
    if manifest.snapshot.relative_path != f'generations/{generation_id}/snapshot.json':
        raise PublicationError('Manifest snapshot path disagrees with committed generation.')

    return snapshot, catalog
