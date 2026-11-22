'''Publication format models for immutable generation artifacts.

The publication format is independently versioned from the extraction
payload schema (3.0.0). Publication format 1.0.0 wraps schema-3.0.0
snapshots and catalogs in immutable generation envelopes committed by
an atomically replaced manifest. A generation ID identifies a
publication attempt, never the source document.
'''

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PUBLICATION_FORMAT_VERSION = '1.0.0'
MANIFEST_FORMAT_KIND = 'resume-assistant-publication-manifest'
ENVELOPE_FORMAT_KIND = 'resume-assistant-publication-envelope'


class ArtifactEntry(BaseModel):
    '''One committed artifact within a generation.

    ``relative_path`` is generation-relative, for example
    ``generations/<generation-id>/snapshot.json``.
    '''

    model_config = ConfigDict(extra='forbid', frozen=True)

    kind: Literal['snapshot', 'catalog']
    payload_schema_version: str
    relative_path: str = Field(min_length=1)
    sha256: str = Field(min_length=1)
    byte_length: int = Field(ge=0)


class PublicationManifest(BaseModel):
    '''The sole publication commit point, stored at the --output address.

    Replaces a legacy schema-3.0.0 catalog at that address. Points to
    exactly one committed snapshot and one committed catalog inside a
    single generation directory.
    '''

    model_config = ConfigDict(extra='forbid', frozen=True)

    format: Literal[MANIFEST_FORMAT_KIND] = MANIFEST_FORMAT_KIND
    format_version: str = PUBLICATION_FORMAT_VERSION
    bundle_key: str = Field(min_length=1)
    generation_id: str = Field(min_length=1)
    evidence_output_selector: str = Field(min_length=1)
    snapshot_output_selector: str = Field(min_length=1)
    source_filename: str = Field(min_length=1)
    source_sha256: str = Field(min_length=1)
    snapshot: ArtifactEntry
    catalog: ArtifactEntry


class GenerationEnvelope(BaseModel):
    '''Immutable artifact envelope stored inside a generation directory.

    ``payload`` is the raw model_dump() of the schema-3.0.0 snapshot or
    catalog. It is validated against its declared model when read back.
    '''

    model_config = ConfigDict(extra='forbid', frozen=True)

    format: Literal[ENVELOPE_FORMAT_KIND] = ENVELOPE_FORMAT_KIND
    format_version: str = PUBLICATION_FORMAT_VERSION
    generation_id: str = Field(min_length=1, pattern=r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
    artifact_kind: Literal['snapshot', 'catalog']
    payload_schema_version: str
    payload: dict[str, Any]


class PublicationError(ValueError):
    '''Controlled publication integrity error, content-free.'''


def is_manifest_format(document: dict) -> bool:
    '''Return True when a parsed JSON document is a publication manifest.'''
    return isinstance(document, dict) and document.get('format') == MANIFEST_FORMAT_KIND
