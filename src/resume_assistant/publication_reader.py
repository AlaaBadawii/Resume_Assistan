'''Manifest-aware committed-pair reader for publication artifacts.

Reads one manifest using pinned no-follow input operations, resolves the
generation referenced by that manifest, loads and checksum-verifies both
artifacts, validates snapshot/catalog integrity, and returns them as one
coherent pair. Explicitly distinguishes legacy catalog JSON from the new
manifest format. Never falls back to an older generation when the current
manifest is invalid. Allows a bounded three-attempt retry only for a
manifest replacement race.
'''

import json
import os
from pathlib import Path
from typing import Optional

from resume_assistant import private_input
from resume_assistant.models import (
    CandidateEvidenceCatalog,
    DocumentSnapshot,
    SCHEMA_VERSION,
)
from resume_assistant.publication import (
    PublicationManifest,
    PublicationError,
    is_manifest_format,
)
from resume_assistant.publication_integrity import (
    validate_catalog_structure,
    validate_publication_pair,
)
from resume_assistant.publication_store import (
    read_generation_artifacts,
    resolve_bundle_key,
)

MAX_MANIFEST_READ_ATTEMPTS = 3


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key.')
        result[key] = value
    return result


def _reject_json_constant(value):
    raise ValueError('Nonstandard JSON constant.')


def _parse_json_document(text: str) -> object:
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (ValueError, RecursionError) as error:
        raise PublicationError('Input must contain valid, unambiguous JSON.') from error


def load_committed_pair(
    project_root: Path,
    manifest_address: Path,
) -> tuple[Optional[DocumentSnapshot], CandidateEvidenceCatalog, Optional[PublicationManifest], Optional[str]]:
    '''Load the committed snapshot/catalog pair referenced by a manifest.

    Returns (snapshot, catalog, manifest, generation_id). For legacy
    standalone catalogs, snapshot and manifest are None.
    '''
    manifest_path = Path(os.path.abspath(manifest_address))

    last_error: Optional[Exception] = None
    document = None
    for attempt in range(MAX_MANIFEST_READ_ATTEMPTS):
        try:
            text = private_input.read_artifact_text(
                manifest_path, artifact='candidate_evidence'
            )
            document = _parse_json_document(text)
            break
        except private_input.PrivateInputMissingError as error:
            last_error = error
            if attempt == MAX_MANIFEST_READ_ATTEMPTS - 1:
                raise PublicationError(str(error)) from error
        except private_input.PrivateInputError as error:
            raise PublicationError(str(error)) from error

    # A non-object document is not a legacy catalog and cannot be a manifest.
    # Reject it explicitly rather than failing later on ``document.get``.
    if not isinstance(document, dict):
        raise PublicationError('Candidate evidence input must be a JSON object.')

    if is_manifest_format(document):
        bundle_key = resolve_bundle_key(project_root, manifest_path)
        manifest = PublicationManifest.model_validate(document, strict=True)
        if manifest.bundle_key != bundle_key:
            raise PublicationError('Manifest bundle key disagrees with address.')
        snapshot, catalog = read_generation_artifacts(
            project_root, bundle_key, manifest.generation_id, manifest
        )
        validate_publication_pair(snapshot, catalog)
        validate_catalog_structure(catalog)
        return snapshot, catalog, manifest, manifest.generation_id

    # Legacy standalone schema-3.0.0 catalog.
    if document.get('schema_version') != SCHEMA_VERSION:
        raise PublicationError('Catalog schema version is not supported.')
    catalog = CandidateEvidenceCatalog.model_validate_json(
        json.dumps(document), strict=True
    )
    validate_catalog_structure(catalog)
    return None, catalog, None, None


def load_candidate_evidence_input(
    project_root: Path,
    candidate_evidence_path: Path,
) -> tuple[CandidateEvidenceCatalog, Optional[DocumentSnapshot], Optional[PublicationManifest]]:
    '''Convenience loader for match-job candidate evidence input.

    Returns (catalog, snapshot_or_None, manifest_or_None).
    '''
    snapshot, catalog, manifest, _ = load_committed_pair(
        project_root, candidate_evidence_path
    )
    return catalog, snapshot, manifest
