'''Pydantic v2 data models for the Master Resume extraction layer.'''

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = '3.0.0'


class BlockType(str, Enum):
    PARAGRAPH = 'paragraph'
    TABLE = 'table'


class EvidenceType(str, Enum):
    IDENTITY = 'identity'
    CONTACT = 'contact'
    PROFESSIONAL_SUMMARY = 'professional_summary'
    TECHNICAL_SKILL = 'technical_skill'
    EXPERIENCE = 'experience'
    PROJECT = 'project'
    EDUCATION = 'education'
    CERTIFICATION = 'certification'
    LANGUAGE = 'language'
    METRIC = 'metric'
    LEADERSHIP = 'leadership_evidence'
    INTERVIEW_EVIDENCE = 'interview_evidence'
    SHOWCASE = 'showcase'
    UNCLASSIFIED = 'unclassified'


class DocumentClassification(str, Enum):
    DETECTED = 'detected'
    UNCLASSIFIED = 'unclassified'


class DocumentStatistics(BaseModel):
    paragraph_count: int = 0
    table_count: int = 0
    row_count: int = 0
    cell_count: int = 0
    image_count: int = 0
    hyperlink_count: int = 0
    drawing_count: int = 0
    embedded_object_count: int = 0
    textbox_count: int = 0
    word_count: int = 0
    empty_paragraph_count: int = 0
    block_count: int = 0
    body_paragraph_count: int = 0
    table_paragraph_count: int = 0
    header_paragraph_count: int = 0
    footer_paragraph_count: int = 0
    total_paragraph_count: int = 0


class RunBlock(BaseModel):
    text: str = ''
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    font_name: Optional[str] = None
    font_size_pt: Optional[float] = None


class HyperlinkBlock(BaseModel):
    text: str = ''
    anchor: Optional[str] = None
    relationship_id: Optional[str] = None
    url: Optional[str] = None
    source_location: Optional['SourceLocation'] = None


class ParagraphBlock(BaseModel):
    type: str = 'paragraph'
    index: int
    text: str
    style: Optional[str] = None
    runs: List[RunBlock] = Field(default_factory=list)
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    font_size_pt: Optional[float] = None
    alignment: Optional[str] = None
    bullet: Optional[bool] = None
    numbering_format: Optional[str] = None
    numbering_level: Optional[int] = None
    numbering_num_id: Optional[int] = None
    numbering_abstract_num_id: Optional[int] = None
    numbering_start: Optional[int] = None
    hyperlink_count: int = 0
    hyperlinks: List[HyperlinkBlock] = Field(default_factory=list)
    field: Optional[str] = None
    field_instructions: List[str] = Field(default_factory=list)


class TableCell(BaseModel):
    index: int
    text: str = ''
    paragraphs: List[ParagraphBlock] = Field(default_factory=list)
    merged: Optional[bool] = None
    grid_span: Optional[int] = None
    v_merge: Optional[Literal['restart', 'continue']] = None
    h_merge: Optional[Literal['restart', 'continue']] = None
    row_index: Optional[int] = None
    nested_tables: List['TableBlock'] = Field(default_factory=list)
    content_order: List[Literal['paragraph', 'table']] = Field(default_factory=list)


class TableBlock(BaseModel):
    type: str = 'table'
    index: int
    row_count: int
    column_count: int
    rows: List[List[TableCell]] = Field(default_factory=list)


class DocumentBlock(BaseModel):
    type: str
    index: int
    paragraph: Optional[ParagraphBlock] = None
    table: Optional[TableBlock] = None
    source_location: Optional['SourceLocation'] = None


class UnsupportedContent(BaseModel):
    tag: str
    parent_path: Optional[str] = None
    description: Optional[str] = None
    extracted: bool = False
    source_location: Optional['SourceLocation'] = None
    reason: Literal['unsupported_structure', 'unresolved_reference', 'inventory_unavailable'] | None = None


class SourceLocation(BaseModel):
    document_part: str = 'word/document.xml'
    body_block_index: Optional[int] = None
    paragraph_index: Optional[int] = None
    table_index: Optional[int] = None
    row_index: Optional[int] = None
    cell_index: Optional[int] = None
    nested_table_path: Optional[List[int]] = None
    paragraph_position_in_cell: Optional[int] = None


DocumentBlock.model_rebuild()
UnsupportedContent.model_rebuild()
TableCell.model_rebuild()
HyperlinkBlock.model_rebuild()


class HeaderFooterReference(BaseModel):
    '''An actual section reference, or its effective linked-to-previous use.'''

    section_index: int = Field(ge=0)
    source_section_index: int = Field(ge=0)
    kind: Literal['header', 'footer']
    variant: str | None
    relationship_id: str | None
    document_part: str | None
    resolution_status: Literal['resolved', 'unresolved']
    inherited: bool = False
    source_path: str
    source_location: SourceLocation


class HeaderFooterPart(BaseModel):
    '''One actual owning part; collections imply no order relative to the body.'''

    document_part: str
    kind: Literal['header', 'footer']
    source_document: str
    source_sha256: str
    extraction_status: Literal['extracted', 'partial', 'unsupported', 'inventory_unavailable']
    blocks: List[DocumentBlock] = Field(default_factory=list)


class DocumentSection(BaseModel):
    identifier: str
    heading_text: str
    numbering: Optional[str] = None
    start_block_index: Optional[int] = None
    end_block_index: Optional[int] = None
    confidence: float = 0.0
    classification_status: DocumentClassification = DocumentClassification.UNCLASSIFIED


class DocumentSnapshot(BaseModel):
    schema_version: str = SCHEMA_VERSION
    source_filename: str
    source_sha256: str
    extraction_timestamp: datetime
    statistics: DocumentStatistics
    blocks: List[DocumentBlock] = Field(default_factory=list)
    sections: List[DocumentSection] = Field(default_factory=list)
    unsupported_content: List[UnsupportedContent] = Field(default_factory=list)
    coverage_assessed: bool = False
    unresolved_extraction_details: List[str] = Field(default_factory=list)
    headers: List[DocumentBlock] = Field(default_factory=list)
    footers: List[DocumentBlock] = Field(default_factory=list)
    header_footer_parts: List[HeaderFooterPart] = Field(default_factory=list)
    header_footer_references: List[HeaderFooterReference] = Field(default_factory=list)


class SourceDocumentInfo(BaseModel):
    filename: str
    sha256: str
    statistics: Optional[DocumentStatistics] = None


class ExtractionCompleteness(BaseModel):
    complete: bool
    coverage_assessed: bool = False
    source_preservation_complete: bool = False
    section_classification_complete: bool = False
    sections_detected: int = Field(default=0, ge=0)
    evidence_records: int = Field(default=0, ge=0)
    unclassified_block_indexes: List[int] = Field(default_factory=list)
    unsupported_content_count: int = Field(default=0, ge=0)
    unresolved_extraction_details: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    unsupported_content: List[UnsupportedContent] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_completeness(self) -> 'ExtractionCompleteness':
        preserved = self.coverage_assessed and self.unsupported_content_count == 0
        classified = self.sections_detected > 0 and not self.unclassified_block_indexes
        if self.source_preservation_complete != preserved:
            raise ValueError('Source preservation must match assessed coverage and coverage gaps.')
        if self.section_classification_complete != classified:
            raise ValueError('Section classification must match detected sections and unclassified blocks.')
        overall = preserved and classified and not self.unresolved_extraction_details
        if self.complete != overall:
            raise ValueError('Overall completeness must match its component states and unresolved details.')
        return self


class TableCellSource(BaseModel):
    '''Provenance for one contributing cell of a table row-level evidence record.'''

    table_index: int
    row_index: int
    cell_index: int
    paragraph_positions: List[int] = Field(default_factory=list)
    nested_table_path: Optional[List[int]] = None


class TableEvidenceContext(BaseModel):
    '''Context for row-level (or cell-level) evidence originating from a table.'''

    table_index: int
    row_index: Optional[int] = None
    cell_index: Optional[int] = None
    column_count: int = 0
    cell_texts: List[str] = Field(default_factory=list)
    nested_table_path: Optional[List[int]] = None
    cell_sources: List[TableCellSource] = Field(default_factory=list)


class EvidenceRecord(BaseModel):
    evidence_id: str
    evidence_type: EvidenceType
    original_content: str
    normalized_content: Optional[str] = None
    section_reference: Optional[str] = None
    source_document: str = 'Master_Resume.docx'
    source_sha256: Optional[str] = None
    source_location: Optional[SourceLocation] = None
    formatting: Optional[Dict[str, Any]] = Field(default_factory=dict)
    hyperlinks: List[HyperlinkBlock] = Field(default_factory=list)
    table_context: Optional[TableEvidenceContext] = None


class CandidateEvidenceCatalog(BaseModel):
    schema_version: str = SCHEMA_VERSION
    source_document: SourceDocumentInfo
    evidence_records: List[EvidenceRecord] = Field(default_factory=list)
    section_references: List[str] = Field(default_factory=list)
    extraction_completeness: ExtractionCompleteness
