'''Deterministic DOCX extraction for the Master Resume.'''

import hashlib
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Literal, Optional

from docx import Document
from docx.opc.constants import CONTENT_TYPE as CT
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from lxml import etree

from resume_assistant import models
from resume_assistant.document_coverage import DocumentCoverage
from resume_assistant.models import (
    DocumentBlock,
    DocumentSnapshot,
    DocumentStatistics,
    HeaderFooterPart,
    ParagraphBlock,
    TableBlock,
    TableCell,
    UnsupportedContent,
)
from resume_assistant.private_input import CapturedSource, capture_docx_source

W_NS = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
A_NS = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
VML_NS = '{urn:schemas-microsoft-com:vml}'

UNSUPPORTED_TRIGGER_LOCAL_NAMES = frozenset([
    'drawing',
    'pict',
    'object',
    'txbxContent',
    'sdt',
    'AlternateContent',
    'altChunk',
    'customXml',
    'smartTag',
    'ins',
    'del',
    'moveTo',
    'moveFrom',
    'dir',
    'bdo',
    'ruby',
    'sym',
    'softHyphen',
    'delText',
    'delInstrText',
    'contentPart',
    'subDoc',
    'fldSimple',
    'fldChar',
    'instrText',
    'footnoteReference',
    'endnoteReference',
    'commentReference',
])

NON_CONTENT_PART_TYPES = frozenset([
    CT.WML_STYLES,
    'application/vnd.ms-word.stylesWithEffects+xml',
    CT.WML_SETTINGS,
    CT.WML_WEB_SETTINGS,
    CT.WML_FONT_TABLE,
    CT.WML_NUMBERING,
    CT.OFC_THEME,
    CT.OFC_THEME_OVERRIDE,
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
        self.unresolved_hyperlinks: List[str] = []
        self.unresolved_numbering_details: List[str] = []
        self.unresolved_field_details: List[str] = []
        self._numbering_cache: Optional[Dict[int, dict]] = None
        self._numbering_source_sha256: Optional[str] = None
        self._numbering_load_error: Optional[str] = None
        self._nested_table_seq: int = 0
        self._captured_source: CapturedSource | None = None
        self._coverage_inventory: DocumentCoverage | None = None

    @property
    def captured_source(self) -> CapturedSource | None:
        '''The immutable input used by the current extraction, never a re-read.'''
        return self._captured_source

    def _capture_source(self) -> CapturedSource:
        captured = capture_docx_source(self.source)
        if self._numbering_source_sha256 != captured.sha256:
            self._numbering_cache = None
            self._numbering_load_error = None
            self._numbering_source_sha256 = captured.sha256
        self._captured_source = captured
        return captured

    def extract(self) -> DocumentSnapshot:
        self.unresolved_hyperlinks = []
        self.unresolved_numbering_details = []
        self.unresolved_field_details = []
        self._nested_table_seq = 0
        self._captured_source = None
        self._coverage_inventory = None
        captured = self._capture_source()
        try:
            if not zipfile.is_zipfile(BytesIO(captured.data)):
                raise InvalidMasterResumeError('Source is not a readable DOCX package.')
            with zipfile.ZipFile(BytesIO(captured.data)) as archive:
                if 'word/document.xml' not in archive.namelist():
                    raise InvalidMasterResumeError('Source DOCX is missing its main document part.')
            document = Document(BytesIO(captured.data))
            if document.element.body is None:
                raise InvalidMasterResumeError('Captured source has no document body.')
        except (zipfile.BadZipFile, KeyError, ValueError, EOFError, OSError, etree.XMLSyntaxError):
            raise InvalidMasterResumeError('Captured source is not a readable DOCX document.') from None
        statistics, blocks, unsupported = self._scan_body(document)
        headers: List[DocumentBlock] = []
        footers: List[DocumentBlock] = []
        part_snapshots = []
        inventory = self._coverage_inventory
        for part_name, part in inventory.parts.items():
            kind = 'header' if part.content_type == CT.WML_HEADER else 'footer'
            root = getattr(part, '_element', None)
            part_gaps = [gap for gap in unsupported if gap.source_location.document_part == part_name]
            part_blocks = []
            if root is None or root.tag != W_NS + ('hdr' if kind == 'header' else 'ftr'):
                status = 'inventory_unavailable'
            elif part_name not in inventory.default_parts:
                status = 'unsupported'
            else:
                status = 'partial' if part_gaps else 'extracted'
                projection = headers if kind == 'header' else footers
                paragraph_index = 0
                self._count_media(root, statistics)
                for child in root:
                    if child.tag != W_NS + 'p':
                        continue
                    paragraph = self._paragraph_block(
                        Paragraph(child, part), index=paragraph_index,
                        body_block_index=None, document=document, part=part,
                    )
                    location = inventory.location(child, part_name)
                    part_blocks.append(DocumentBlock(
                        type='paragraph', index=paragraph_index,
                        paragraph=paragraph, source_location=location,
                    ))
                    # Keep legacy default-part projections and their aggregate indexes.
                    projection.append(DocumentBlock(
                        type='paragraph', index=len(projection),
                        paragraph=paragraph.model_copy(update={'index': len(projection)}, deep=True),
                        source_location=location.model_copy(deep=True),
                    ))
                    paragraph_index += 1
                    if kind == 'header':
                        statistics.header_paragraph_count += 1
                    else:
                        statistics.footer_paragraph_count += 1
                    statistics.word_count += len((paragraph.text or '').split())
                    statistics.hyperlink_count += paragraph.hyperlink_count
                    if not (paragraph.text or '').strip():
                        statistics.empty_paragraph_count += 1
            part_snapshots.append(HeaderFooterPart(
                document_part=part_name, kind=kind, source_document=captured.filename,
                source_sha256=captured.sha256, extraction_status=status, blocks=part_blocks,
            ))

        statistics.total_paragraph_count = (
            statistics.body_paragraph_count
            + statistics.table_paragraph_count
            + statistics.header_paragraph_count
            + statistics.footer_paragraph_count
        )

        return DocumentSnapshot(
            schema_version=models.SCHEMA_VERSION,
            source_filename=captured.filename,
            source_sha256=captured.sha256,
            extraction_timestamp=datetime.now(timezone.utc),
            statistics=statistics,
            blocks=blocks,
            sections=[],
            unsupported_content=unsupported,
            coverage_assessed=inventory.assessed,
            unresolved_extraction_details=list(dict.fromkeys([
                *self.unresolved_hyperlinks,
                *self.unresolved_numbering_details,
                *self.unresolved_field_details,
            ])),
            headers=headers,
            footers=footers,
            header_footer_parts=part_snapshots,
            header_footer_references=inventory.references,
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

        def _note_paragraph(text: Optional[str]) -> None:
            statistics.word_count += len((text or '').split())
            if not (text or '').strip():
                statistics.empty_paragraph_count += 1

        for child in body:
            if child.tag == W_NS + 'p':
                paragraph_block = self._paragraph_block(
                    Paragraph(child, document), index=paragraph_ordinal, body_block_index=block_index, document=document
                )
                blocks.append(
                    DocumentBlock(
                        type='paragraph', index=block_index, paragraph=paragraph_block,
                        source_location=self._coverage_inventory.location(child, 'word/document.xml'),
                    )
                )
                paragraph_ordinal += 1
                statistics.paragraph_count += 1
                statistics.body_paragraph_count += 1
                _note_paragraph(paragraph_block.text)
                statistics.hyperlink_count += paragraph_block.hyperlink_count
                block_index += 1
                statistics.block_count += 1
            elif child.tag == W_NS + 'tbl':
                table_block = self._build_table(child, document, table_id=block_index)
                blocks.append(
                    DocumentBlock(
                        type='table', index=block_index, table=table_block,
                        source_location=self._coverage_inventory.location(child, 'word/document.xml'),
                    )
                )
                self._accumulate_table_statistics(table_block, statistics, _note_paragraph)
                block_index += 1
                statistics.block_count += 1

        self._count_media(body, statistics)
        statistics.total_paragraph_count = (
            statistics.body_paragraph_count
            + statistics.table_paragraph_count
            + statistics.header_paragraph_count
            + statistics.footer_paragraph_count
        )
        return statistics, blocks, unsupported

    # ------------------------------------------------------------------
    # Unsupported content inventory
    # ------------------------------------------------------------------
    def _scan_unsupported(self, document) -> List[UnsupportedContent]:
        inventory = DocumentCoverage(
            document, unsupported_names=UNSUPPORTED_TRIGGER_LOCAL_NAMES,
            ignored_part_types=NON_CONTENT_PART_TYPES,
        )
        self._coverage_inventory = inventory
        return inventory.scan()

    # ------------------------------------------------------------------
    # Paragraph-level extraction helpers
    # ------------------------------------------------------------------
    def _paragraph_block(
        self, paragraph: Paragraph, index: int, body_block_index: Optional[int], document=None, field: Optional[str] = None, part=None
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
        numbering_num_id: Optional[int] = None
        numbering_abstract_num_id: Optional[int] = None
        numbering_start: Optional[int] = None
        pPr = paragraph._p.pPr
        definitions = self._load_numbering_definitions()
        direct_num_pr = pPr.numPr if pPr is not None else None
        has_num_pr = direct_num_pr is not None
        num_id_element = direct_num_pr.find(qn('w:numId')) if has_num_pr else None
        level_element = direct_num_pr.find(qn('w:ilvl')) if has_num_pr else None
        inherited = False
        style = paragraph.style
        visited_styles = set()
        while style is not None and (num_id_element is None or level_element is None):
            if style.style_id in visited_styles:
                self.unresolved_numbering_details.append('Numbering style inheritance is cyclic')
                break
            visited_styles.add(style.style_id)
            properties = style.element.find(qn('w:pPr'))
            style_num_pr = properties.find(qn('w:numPr')) if properties is not None else None
            if style_num_pr is not None:
                has_num_pr = True
                if num_id_element is None:
                    num_id_element = style_num_pr.find(qn('w:numId'))
                    inherited = inherited or num_id_element is not None
                if level_element is None:
                    level_element = style_num_pr.find(qn('w:ilvl'))
                    inherited = inherited or level_element is not None
            style = style.base_style
        if has_num_pr:
            numbering_num_id = int(num_id_element.get(qn('w:val'))) if num_id_element is not None else None
            numbering_level = int(level_element.get(qn('w:val'))) if level_element is not None else 0
            if numbering_num_id == 0:
                numbering_format = None
                bullet = False
            else:
                definition = definitions.get(numbering_num_id)
                numbering_abstract_num_id = definition.get('abstract_num_id') if definition else None
                numbering_start = self._resolve_numbering_start(numbering_num_id, numbering_level)
                resolved_format = self._resolve_numbering_format(numbering_num_id, numbering_level)
                numbering_format = resolved_format if resolved_format is not None else 'unresolved'
                if resolved_format is not None:
                    bullet = resolved_format == 'bullet'
                else:
                    if self._numbering_load_error == 'missing':
                        self.unresolved_numbering_details.append('word/numbering.xml is missing')
                    if inherited:
                        self.unresolved_numbering_details.append('Style-inherited numbering was not resolved')
                    else:
                        self.unresolved_numbering_details.append(
                            f'numbering numId={numbering_num_id} ilvl={numbering_level} unresolved'
                        )

        field_value, field_instructions = self._capture_field(paragraph)
        hyperlinks = self._paragraph_hyperlinks(paragraph, document, part=part)
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
            numbering_num_id=numbering_num_id,
            numbering_abstract_num_id=numbering_abstract_num_id,
            numbering_start=numbering_start,
            hyperlink_count=len(hyperlinks),
            hyperlinks=hyperlinks,
            field=field if field is not None else field_value,
            field_instructions=field_instructions,
        )

    def _capture_field(self, paragraph) -> tuple[Optional[str], List[str]]:
        '''Return (first instruction, every instruction in document order).

        Field structures stay declared coverage gaps; this helper never
        invents an instruction value.
        '''
        instructions: List[str] = []
        sequence: List[bool] = []
        incomplete = False
        for element in paragraph._p.iter():
            if element.tag == (W_NS + 'fldSimple'):
                instruction = (element.get(W_NS + 'instr') or '').strip()
                if instruction:
                    instructions.append(instruction)
            elif element.tag == (W_NS + 'instrText'):
                instruction = (element.text or '').strip()
                if instruction:
                    instructions.append(instruction)
                if not sequence or sequence[-1]:
                    incomplete = True
            elif element.tag == (W_NS + 'fldChar'):
                fld_type = element.get(qn('w:fldCharType'))
                if fld_type == 'begin':
                    sequence.append(False)
                elif fld_type == 'separate':
                    if not sequence or sequence[-1]:
                        incomplete = True
                    else:
                        sequence[-1] = True
                elif fld_type == 'end':
                    if sequence:
                        sequence.pop()
                    else:
                        incomplete = True
                else:
                    incomplete = True
        if sequence or incomplete:
            self.unresolved_field_details.append('field sequence is incomplete')
        return instructions[0] if instructions else None, instructions

    def _paragraph_hyperlinks(
        self, paragraph, document, part=None
    ) -> List[models.HyperlinkBlock]:
        source_part = part if part is not None else document.part
        relationships = source_part.rels
        found: List[models.HyperlinkBlock] = []
        for element in paragraph._p.iter():
            if element.tag != (W_NS + 'hyperlink'):
                continue
            text_parts = []
            for node in element.iter():
                if node.tag == (W_NS + 't'):
                    text_parts.append(node.text or '')
            rid = element.get(qn('r:id'))
            anchor = element.get(qn('w:anchor'))
            url = None
            relationship_id = rid
            if rid is not None:
                relationship = relationships.get(rid) if hasattr(relationships, 'get') else None
                if relationship is not None:
                    if relationship.is_external:
                        url = getattr(relationship, 'target_ref', None) or None
                    else:
                        url = None
                else:
                    # Record the unresolved relationship rather than silently dropping it.
                    self.unresolved_hyperlinks.append(
                        f'hyperlink {anchor or rid} has unresolved relationship {rid}'
                    )
            found.append(
                models.HyperlinkBlock(
                    text=''.join(text_parts),
                    anchor=anchor,
                    relationship_id=relationship_id,
                    url=url,
                    source_location=self._coverage_inventory.location(
                        element, str(source_part.partname).lstrip('/'),
                    ),
                )
            )
        return found



    def _load_numbering_definitions(self) -> Dict[int, dict]:
        """Load Word numbering definitions from word/numbering.xml.

        Returns a mapping of numId -> {'abstract_num_id': int, 'levels': {ilvl: {'numFmt', 'start'}}, 'lvl_overrides': {ilvl: {'numFmt', 'start'}}}.
        """
        if self._numbering_cache is not None:
            self._note_numbering_load_error()
            return self._numbering_cache
        definitions: Dict[int, dict] = {}
        try:
            captured = self._captured_source if self._captured_source is not None else self._capture_source()
            with zipfile.ZipFile(BytesIO(captured.data)) as archive:
                number_name = None
                for candidate in archive.namelist():
                    if candidate == 'word/numbering.xml' or candidate.endswith('/numbering.xml'):
                        number_name = candidate
                        break
                if number_name is None:
                    self._numbering_load_error = 'missing'
                    self._numbering_cache = definitions
                    return definitions
                xml_bytes = archive.read(number_name)
            root = etree.fromstring(xml_bytes)
            abstract_levels: Dict[int, Dict[int, dict]] = {}
            for abstract in root.iter(qn('w:abstractNum')):
                abstract_id = int(abstract.get(qn('w:abstractNumId'), '0') or '0')
                levels: Dict[int, dict] = {}
                for lvl in abstract.findall(qn('w:lvl')):
                    ilvl = int(lvl.get(qn('w:ilvl'), '0') or '0')
                    fmt_el = lvl.find(qn('w:numFmt'))
                    fmt = fmt_el.get(qn('w:val')) if fmt_el is not None else None
                    start_el = lvl.find(qn('w:start'))
                    start = int(start_el.get(qn('w:val'), '0')) if start_el is not None else 1
                    levels[ilvl] = {'numFmt': fmt, 'start': start}
                abstract_levels[abstract_id] = levels
            for num_el in root.iter(qn('w:num')):
                num_id = int(num_el.get(qn('w:numId'), '0') or '0')
                abstract_id = None
                abstract_el = num_el.find(qn('w:abstractNumId'))
                if abstract_el is not None:
                    abstract_id = int(abstract_el.get(qn('w:val'), '0') or '0')
                levels: Dict[int, dict] = dict(abstract_levels.get(abstract_id, {})) if abstract_id is not None else {}
                lvl_overrides: Dict[int, dict] = {}
                for lo in num_el.findall(qn('w:lvlOverride')):
                    ilvl = int(lo.get(qn('w:ilvl'), '0') or '0')
                    lvl_el = lo.find(qn('w:lvl'))
                    override_fmt = None
                    override_start = None
                    if lvl_el is not None:
                        fmt_el = lvl_el.find(qn('w:numFmt'))
                        override_fmt = fmt_el.get(qn('w:val')) if fmt_el is not None else None
                        start_el = lvl_el.find(qn('w:start'))
                        override_start = int(start_el.get(qn('w:val'), '0')) if start_el is not None else None
                    else:
                        start_override_el = lo.find(qn('w:startOverride'))
                        if start_override_el is not None and start_override_el.get(qn('w:val')) is not None:
                            override_start = int(start_override_el.get(qn('w:val')))
                    lvl_overrides[ilvl] = {'numFmt': override_fmt, 'start': override_start}
                definitions[num_id] = {
                    'abstract_num_id': abstract_id,
                    'levels': levels,
                    'lvl_overrides': lvl_overrides,
                }
            self._numbering_load_error = None
            self._numbering_cache = definitions
        except (ValueError, etree.XMLSyntaxError):
            self._numbering_load_error = 'parse'
            self._numbering_cache = definitions
        except (OSError, KeyError, zipfile.BadZipFile):
            self._numbering_load_error = 'unavailable'
            self._numbering_cache = definitions
        self._note_numbering_load_error()
        return self._numbering_cache

    def _note_numbering_load_error(self) -> None:
        if self._numbering_load_error == 'parse':
            self.unresolved_numbering_details.append(
                'word/numbering.xml could not be parsed (invalid numbering value)'
            )
        elif self._numbering_load_error == 'unavailable':
            self.unresolved_numbering_details.append('word/numbering.xml could not be read')

    def _resolve_numbering_format(self, num_id: int, ilvl: int) -> Optional[str]:
        definitions = self._load_numbering_definitions()
        definition = definitions.get(num_id)
        if definition is None:
            return None
        fmt = None
        overrides = definition.get('lvl_overrides') or {}
        if ilvl in overrides and overrides[ilvl].get('numFmt') is not None:
            fmt = overrides[ilvl]['numFmt']
        elif ilvl in definition.get('levels') and definition['levels'][ilvl].get('numFmt') is not None:
            fmt = definition['levels'][ilvl]['numFmt']
        if fmt is None:
            return None
        fmt = str(fmt)
        if fmt == 'bullet':
            return 'bullet'
        return fmt

    def _resolve_numbering_start(self, num_id: int, ilvl: int) -> Optional[int]:
        definitions = self._load_numbering_definitions()
        definition = definitions.get(num_id)
        if definition is None:
            return None
        start = None
        overrides = definition.get('lvl_overrides') or {}
        if ilvl in overrides and overrides[ilvl].get('start') is not None:
            start = overrides[ilvl]['start']
        elif (
            definition.get('levels')
            and ilvl in definition['levels']
            and definition['levels'][ilvl].get('start') is not None
        ):
            start = definition['levels'][ilvl]['start']
        return start

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
        row_index: int,
        cell_index: int,
    ) -> TableCell:
        paragraphs: List[ParagraphBlock] = []
        nested_tables: List[TableBlock] = []
        content_order: List[Literal['paragraph', 'table']] = []
        ordinal = 0
        for child in cell_element:
            if child.tag == (W_NS + 'p'):
                paragraph_block = self._paragraph_block(
                    Paragraph(child, document), index=ordinal, body_block_index=None, document=document
                )
                paragraph_block.index = ordinal
                paragraphs.append(paragraph_block)
                content_order.append('paragraph')
                ordinal += 1
            elif child.tag == (W_NS + 'tbl'):
                self._nested_table_seq -= 1
                nested_tables.append(
                    self._build_table(child, document, table_id=self._nested_table_seq)
                )
                content_order.append('table')
        text = '\n'.join(
            (paragraph.text or '') for paragraph in paragraphs if (paragraph.text or '').strip()
        )
        merged = False
        grid_span: Optional[int] = None
        v_merge: Optional[Literal['restart', 'continue']] = None
        h_merge: Optional[Literal['restart', 'continue']] = None
        cell_properties = None
        for child in cell_element:
            if child.tag == (W_NS + 'tcPr'):
                cell_properties = child
                break
        if cell_properties is not None:
            for property_element in cell_properties:
                if property_element.tag == (W_NS + 'gridSpan'):
                    value = property_element.get(W_NS + 'val')
                    if value is not None and value.isdigit() and int(value) >= 1:
                        grid_span = int(value)
                        merged = grid_span > 1
                if property_element.tag == (W_NS + 'vMerge'):
                    merged = True
                    value = property_element.get(W_NS + 'val')
                    if value is not None and str(value).lower() == 'restart':
                        v_merge = 'restart'
                    else:
                        v_merge = 'continue'
                if property_element.tag == (W_NS + 'hMerge'):
                    merged = True
                    value = property_element.get(W_NS + 'val')
                    if value is not None and str(value).lower() == 'restart':
                        h_merge = 'restart'
                    else:
                        h_merge = 'continue'
        return TableCell(
            index=cell_index,
            text=text,
            paragraphs=paragraphs,
            merged=merged,
            grid_span=grid_span,
            v_merge=v_merge,
            h_merge=h_merge,
            row_index=row_index,
            nested_tables=nested_tables,
            content_order=content_order,
        )

    @classmethod
    def _accumulate_table_statistics(cls, table_block: TableBlock, statistics: DocumentStatistics, _note_paragraph) -> None:
        '''Accumulate statistics for one table including every nested table.'''
        statistics.table_count += 1
        statistics.row_count += table_block.row_count
        statistics.cell_count += sum(len(row) for row in table_block.rows)
        for row in table_block.rows:
            for cell in row:
                statistics.table_paragraph_count += len(cell.paragraphs)
                for paragraph_block in cell.paragraphs:
                    _note_paragraph(paragraph_block.text)
                    statistics.hyperlink_count += paragraph_block.hyperlink_count
                for nested in cell.nested_tables:
                    cls._accumulate_table_statistics(nested, statistics, _note_paragraph)

    @staticmethod
    def _count_media(root, statistics: DocumentStatistics) -> None:
        '''Count media element occurrences reachable from an XML root.'''
        for element in root.iter():
            tag = element.tag
            if not isinstance(tag, str):
                continue
            if tag == W_NS + 'drawing':
                statistics.drawing_count += 1
            elif tag == W_NS + 'txbxContent':
                statistics.textbox_count += 1
            elif tag == W_NS + 'object':
                statistics.embedded_object_count += 1
            elif tag == A_NS + 'blip' or tag == VML_NS + 'imagedata':
                statistics.image_count += 1
