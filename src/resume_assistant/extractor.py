'''Deterministic DOCX extraction for the Master Resume.'''

import hashlib
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from docx import Document
from docx.text.paragraph import Paragraph

from resume_assistant import models
from resume_assistant.models import (
    DocumentBlock,
    DocumentSnapshot,
    DocumentStatistics,
    ParagraphBlock,
    SourceLocation,
    TableBlock,
    TableCell,
    UnsupportedContent,
)

W_NS = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

UNSUPPORTED_TRIGGER_LOCAL_NAMES = frozenset([
    'drawing',
    'pict',
    'object',
    'txbxContent',
    'mc',
])



class InvalidMasterResumeError(ValueError):
    '''Raised when the source is not a readable DOCX resume document.'''


def compute_sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


class MasterResumeExtractor:
    '''Extracts an ordered, source-faithful view of a Master Resume DOCX.'''

    def __init__(self, source: Path) -> None:
        self.source = Path(source)

    def extract(self) -> DocumentSnapshot:
        source = self.source
        if not source.exists():
            raise FileNotFoundError(f'Master Resume source not found: {source}')
        if not source.is_file():
            raise ValueError(f'Master Resume source is not a file: {source}')
        if not zipfile.is_zipfile(str(source)):
            raise InvalidMasterResumeError(
                f'Source is not a valid DOCX (zip) file: {source}'
            )
        with zipfile.ZipFile(str(source)) as archive:
            if 'word/document.xml' not in archive.namelist():
                raise InvalidMasterResumeError(
                    f'Source DOCX is missing word/document.xml: {source}'
                )
        source_sha256 = compute_sha256(source)
        document = Document(str(source))
        statistics, blocks, unsupported = self._scan_body(document)
        headers: List[DocumentBlock] = []
        footers: List[DocumentBlock] = []
        for section in document.sections:
            for paragraph in section.header.paragraphs:
                header_block = self._paragraph_block(
                    paragraph, index=len(headers), body_block_index=None, document=document
                )
                headers.append(
                    DocumentBlock(type='paragraph', index=len(headers), paragraph=header_block)
                )
            for paragraph in section.footer.paragraphs:
                footer_block = self._paragraph_block(
                    paragraph, index=len(footers), body_block_index=None, document=document
                )
                self._capture_field(paragraph, footer_block)
                footers.append(
                    DocumentBlock(type='paragraph', index=len(footers), paragraph=footer_block)
                )

        return DocumentSnapshot(
            schema_version=models.SCHEMA_VERSION,
            source_filename=source.name,
            source_sha256=source_sha256,
            extraction_timestamp=datetime.now(timezone.utc),
            statistics=statistics,
            blocks=blocks,
            sections=[],
            unsupported_content=unsupported,
            headers=headers,
            footers=footers,
        )

    # ------------------------------------------------------------------
    # Body scan: ordered paragraphs and tables plus statistics
    # ------------------------------------------------------------------
    def _scan_body(self, document):
        body = document.element.body
        blocks: List[DocumentBlock] = []
        unsupported = self._scan_unsupported(document)
        statistics = DocumentStatistics()
        block_index = 0
        paragraph_ordinal = 0
        hyperlink_total = 0

        for child in body:
            if child.tag == W_NS + 'p':
                paragraph_block = self._paragraph_block(
                    Paragraph(child, document), index=paragraph_ordinal, body_block_index=block_index, document=document
                )
                blocks.append(
                    DocumentBlock(type='paragraph', index=block_index, paragraph=paragraph_block)
                )
                paragraph_ordinal += 1
                statistics.paragraph_count += 1
                statistics.word_count += len((paragraph_block.text or '').split())
                if not (paragraph_block.text or '').strip():
                    statistics.empty_paragraph_count += 1
                hyperlink_total += paragraph_block.hyperlink_count
                block_index += 1
                statistics.block_count += 1
            elif child.tag == W_NS + 'tbl':
                table_block = self._build_table(child, document, table_id=block_index)
                blocks.append(
                    DocumentBlock(type='table', index=block_index, table=table_block)
                )
                table_row_count, table_cell_count = self._table_counts(table_block)
                statistics.table_count += 1
                statistics.row_count += table_row_count
                statistics.cell_count += table_cell_count
                block_index += 1
                statistics.block_count += 1

        statistics.hyperlink_count = hyperlink_total
        return statistics, blocks, unsupported

    # ------------------------------------------------------------------
    # Unsupported content inventory
    # ------------------------------------------------------------------
    def _scan_unsupported(self, document) -> List[UnsupportedContent]:
        found: List[UnsupportedContent] = []

        def walk(element, path: str) -> None:
            local = element.tag.rsplit('}', 1)[-1]
            if local in UNSUPPORTED_TRIGGER_LOCAL_NAMES:
                found.append(
                    UnsupportedContent(
                        tag=str(element.tag),
                        parent_path=path or None,
                        description=f'Unsupported embedded content: {local}',
                        extracted=False,
                    )
                )
                return
            for child in element:
                child_local = child.tag.rsplit('}', 1)[-1]
                child_path = f'{path}/{child_local}' if path else child_local
                if child_local in UNSUPPORTED_TRIGGER_LOCAL_NAMES:
                    found.append(
                        UnsupportedContent(
                            tag=str(child.tag),
                            parent_path=child_path,
                            description=f'Unsupported embedded content: {child_local}',
                            extracted=False,
                        )
                    )
                    continue
                walk(child, child_path)

        walk(document.element.body, '')
        return found

    # ------------------------------------------------------------------
    # Paragraph-level extraction helpers
    # ------------------------------------------------------------------
    def _paragraph_block(
        self, paragraph: Paragraph, index: int, body_block_index: Optional[int], document=None, field: Optional[str] = None
    ) -> ParagraphBlock:
        text = paragraph.text or ''
        runs = [
            {
                'text': run.text,
                'bold': run.bold,
                'italic': run.italic,
                'font_name': run.font.name,
                'font_size_pt': run.font.size.pt if run.font.size is not None else None,
            }
            for run in paragraph.runs
        ]
        bold: Optional[bool] = None
        font_size_pt: Optional[float] = None
        style_bold = None
        style_font = None
        style_name = None
        if paragraph.style is not None:
            style_name = paragraph.style.name
            style_font = paragraph.style.font
            if style_font is not None:
                style_bold = bool(style_font.bold)
        if runs or style_bold is not None:
            bold = bool(style_bold) or any(bool(run['bold']) for run in runs)
        for run in runs:
            if run['font_size_pt'] is not None:
                font_size_pt = run['font_size_pt']
                break
        if font_size_pt is None and style_font is not None and style_font.size is not None:
            font_size_pt = style_font.size.pt
        bold_values = [run['bold'] for run in runs if run['bold'] is not None]
        if bold is None:
            bold = bool(style_bold) if style_bold is not None else None
        if bold_values and style_bold is None:
            bold = any(bold_values)

        alignment = None
        alignment_obj = paragraph.alignment
        if alignment_obj is not None:
            alignment_value = getattr(alignment_obj, 'value', alignment_obj)
            alignment = str(alignment_value)

        bullet = None
        numbering_format = None
        numbering_level = None
        if style_name is not None:
            lowered = style_name.lower()
            if lowered.startswith('list bullet'):
                bullet = True
                numbering_format = 'bullet'
            elif lowered.startswith('list number'):
                bullet = False
                numbering_format = 'decimal'
        pPr = paragraph._p.pPr
        numbered = False
        if pPr is not None and pPr.numPr is not None:
            numbering_level = pPr.numPr.ilvl.val if pPr.numPr.ilvl is not None else 0
            numid = pPr.numPr.numId
            numbered = bool(numid is not None and numid.val)
            numbering_format = 'decimal' if numbered else 'bullet'
        if style_name is not None and style_name.lower().startswith('list bullet') and numbering_format is None:
            numbering_format = 'bullet'

        hyperlinks = self._paragraph_hyperlinks(paragraph, document)
        return ParagraphBlock(
            type='paragraph',
            index=index,
            text=text,
            style=style_name,
            runs=[models.RunBlock(**run) for run in runs],
            bold=bold,
            italic=None,
            font_size_pt=font_size_pt,
            alignment=alignment,
            bullet=bullet,
            numbering_format=numbering_format,
            numbering_level=numbering_level,
            hyperlink_count=len(hyperlinks),
            hyperlinks=hyperlinks,
            field=field if field is not None else self._capture_field(paragraph),
        )

    @staticmethod
    def _capture_field(paragraph, field_value: Optional[str] = None) -> Optional[str]:
        if field_value is not None:
            return field_value
        for element in paragraph._p.iter():
            if element.tag == (W_NS + 'fldSimple'):
                instruction = element.get(W_NS + 'instr')
                return str(instruction).strip() if instruction else 'page'
        return None

    def _paragraph_hyperlinks(self, paragraph, document) -> List[models.HyperlinkBlock]:
        found: List[models.HyperlinkBlock] = []
        for element in paragraph._p.iter():
            if element.tag != (W_NS + 'hyperlink'):
                continue
            text_parts = []
            rid = None
            for node in element.iter():
                if node.tag == (W_NS + 'r'):
                    rid = node.get(W_NS + 'id')
                    for t in node.iter():
                        if t.tag == (W_NS + 't'):
                            text_parts.append(t.text or '')
            url = None
            if rid is not None:
                try:
                    url = document.part.rels[rid].target
                except Exception:
                    url = None
            found.append(
                models.HyperlinkBlock(
                    text=''.join(text_parts),
                    anchor=element.get(W_NS + 'anchor'),
                    relationship_id=rid,
                    url=url,
                )
            )
        return found



    def _build_table(self, table_element, document, table_id: int) -> TableBlock:
        rows_elements = [child for child in table_element if child.tag == (W_NS + 'tr')]
        rows: List[List[TableCell]] = []
        for row_element in rows_elements:
            cells: List[TableCell] = []
            cell_elements = [child for child in row_element if child.tag == (W_NS + 'tc')]
            for cell_element in cell_elements:
                cells.append(
                    self._build_cell(
                        cell_element,
                        document,
                        parent_table_id=table_id,
                        row_index=len(rows),
                        cell_index=len(cells),
                    )
                )
            rows.append(cells)
        return TableBlock(
            type='table',
            index=table_id,
            row_count=len(rows_elements),
            column_count=max([len(row) for row in rows], default=0),
            rows=rows,
        )

    def _build_cell(
        self,
        cell_element,
        document,
        parent_table_id: int,
        row_index: int,
        cell_index: int,
    ) -> TableCell:
        paragraphs: List[ParagraphBlock] = []
        ordinal = 0
        for child in cell_element:
            if child.tag == (W_NS + 'p'):
                paragraph_block = self._paragraph_block(
                    Paragraph(child, document), index=ordinal, body_block_index=None, document=document
                )
                paragraph_block.index = ordinal
                paragraphs.append(paragraph_block)
                ordinal += 1
        text = '\n'.join(
            (paragraph.text or '') for paragraph in paragraphs if (paragraph.text or '').strip()
        )
        merged = False
        cell_properties = None
        for child in cell_element:
            if child.tag == (W_NS + 'tcPr'):
                cell_properties = child
                break
        if cell_properties is not None:
            for property_element in cell_properties:
                if property_element.tag == (W_NS + 'gridSpan'):
                    value = property_element.get(W_NS + 'val')
                    if value is not None and value.isdigit() and int(value) > 1:
                        merged = True
                if property_element.tag == (W_NS + 'vMerge'):
                    value = property_element.get(W_NS + 'val')
                    if value is not None and str(value).lower() in ('restart', 'continue'):
                        merged = True
        nested_tables: List[TableBlock] = []
        for child in cell_element:
            if child.tag == (W_NS + 'tbl'):
                nested_tables.append(
                    self._build_table(child, document, table_id=parent_table_id * 1000 + len(nested_tables))
                )
        return TableCell(
            index=cell_index,
            text=text,
            paragraphs=paragraphs,
            merged=merged,
            row_index=row_index,
            nested_tables=nested_tables,
        )

    @staticmethod
    def _table_counts(table_block: TableBlock) -> tuple:
        rows = table_block.row_count
        cells = sum(len(row) for row in table_block.rows)
        nested_rows = 0
        nested_cells = 0
        for row in table_block.rows:
            for cell in row:
                for nested in cell.nested_tables:
                    nested_rows, nested_cells = MasterResumeExtractor._table_counts(nested)
                    rows = rows + nested_rows
                    cells = cells + nested_cells
        return rows, cells
