'''Pydantic v2 data models for the Master Resume extraction layer.'''

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = '1.0.0'


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
    hyperlink_count: int = 0
    hyperlinks: List[HyperlinkBlock] = Field(default_factory=list)
    field: Optional[str] = None


class TableCell(BaseModel):
    index: int
    text: str = ''
    paragraphs: List[ParagraphBlock] = Field(default_factory=list)
    merged: Optional[bool] = None
    row_index: Optional[int] = None
    nested_tables: List['TableBlock'] = Field(default_factory=list)


class TableBlock(BaseModel):
    type: str = 'table'
    index: int
    row_count: int
    column_count: int
    rows: List[List[TableCell]] = Field(default_factory=list)


TableCell.model_rebuild()


class DocumentBlock(BaseModel):
    type: str
    index: int
    paragraph: Optional[ParagraphBlock] = None
    table: Optional[TableBlock] = None


class UnsupportedContent(BaseModel):
    tag: str
    parent_path: Optional[str] = None
    description: Optional[str] = None
    extracted: bool = False


class SourceLocation(BaseModel):
    document_part: str = 'word/document.xml'
    body_block_index: Optional[int] = None
    paragraph_index: Optional[int] = None
    table_index: Optional[int] = None
    row_index: Optional[int] = None
    cell_index: Optional[int] = None
    nested_table_path: Optional[List[int]] = None
    paragraph_position_in_cell: Optional[int] = None


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
    headers: List[DocumentBlock] = Field(default_factory=list)
    footers: List[DocumentBlock] = Field(default_factory=list)


class SourceDocumentInfo(BaseModel):
    filename: str
    sha256: str
    statistics: Optional[DocumentStatistics] = None


class ExtractionCompleteness(BaseModel):
    complete: bool
    sections_detected: int = 0
    evidence_records: int = 0
    unclassified_block_indexes: List[int] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


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


class CandidateEvidenceCatalog(BaseModel):
    schema_version: str = SCHEMA_VERSION
    source_document: SourceDocumentInfo
    evidence_records: List[EvidenceRecord] = Field(default_factory=list)
    section_references: List[str] = Field(default_factory=list)
    extraction_completeness: ExtractionCompleteness
