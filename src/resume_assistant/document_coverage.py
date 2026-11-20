'''Located structural inventory of a parsed DOCX, with no source-path reads.

Default header/footer content is identified through actual package references.
Known unsupported content and unavailable inventories are recorded explicitly;
this is not a complete interpreter for every WordprocessingML extension.
'''

from docx.opc.constants import CONTENT_TYPE as CT, RELATIONSHIP_TYPE as RT
from docx.oxml.ns import qn

from resume_assistant.models import HeaderFooterReference, SourceLocation, UnsupportedContent

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

SUPPORTED_CHILDREN = {
    W + tag: frozenset(W + name for name in names)
    for tag, names in {
        'body': {'p', 'tbl', 'sectPr'},
        'tbl': {'tblPr', 'tblGrid', 'tr'},
        'tr': {'trPr', 'tblPrEx', 'tc'},
        'tc': {'tcPr', 'p', 'tbl'},
        'hdr': {'p', 'tbl'}, 'ftr': {'p', 'tbl'},
        'p': {
            'pPr', 'r', 'hyperlink', 'bookmarkStart', 'bookmarkEnd',
            'commentRangeStart', 'commentRangeEnd', 'proofErr', 'permStart', 'permEnd',
            'moveFromRangeStart', 'moveFromRangeEnd', 'moveToRangeStart', 'moveToRangeEnd',
        },
        'r': {'rPr', 't', 'tab', 'br', 'cr', 'noBreakHyphen', 'ptab', 'lastRenderedPageBreak'},
        'hyperlink': {'r', 'bookmarkStart', 'bookmarkEnd', 'proofErr'},
    }.items()
}


class DocumentCoverage:
    '''One extraction-local structural assessment and actual part/reference map.'''

    def __init__(self, document, *, unsupported_names, ignored_part_types):
        self.document = document
        self.unsupported_names = unsupported_names
        self.ignored_part_types = ignored_part_types
        self.gaps: list[UnsupportedContent] = []
        self.references: list[HeaderFooterReference] = []
        self.parts: dict[str, object] = {}
        self.default_parts: set[str] = set()
        self.assessed = True
        self._locations: dict[object, SourceLocation] = {}
        self._index_body_locations()

    @staticmethod
    def _children(element, local):
        return [child for child in element if child.tag == W + local]

    @staticmethod
    def source_path(element, part_name):
        '''A stable, indexed XML address within the actual owning package part.'''
        components = []
        current = element
        while current is not None:
            local = current.tag.rsplit('}', 1)[-1]
            if current.tag == W + 'body':
                return '/'.join([part_name, 'body', *reversed(components)])
            if current.tag in (W + 'hdr', W + 'ftr'):
                return '/'.join([part_name, *reversed(components)])
            parent = current.getparent()
            siblings = [child for child in parent if child.tag == current.tag] if parent is not None else [current]
            components.append(f'{local}[{siblings.index(current) + 1}]')
            current = parent
        return '/'.join([part_name, *reversed(components)])

    def _index_body_locations(self):
        nested_index = 0

        def table(element, identifier, owner, path=None):
            nonlocal nested_index
            base = SourceLocation(
                body_block_index=owner, table_index=identifier, nested_table_path=path,
            )
            self._locations[element] = base
            for row_index, row in enumerate(self._children(element, 'tr')):
                row_location = base.model_copy(update={'row_index': row_index})
                self._locations[row] = row_location
                for cell_index, cell in enumerate(self._children(row, 'tc')):
                    cell_location = row_location.model_copy(update={'cell_index': cell_index})
                    self._locations[cell] = cell_location
                    for paragraph_index, paragraph in enumerate(self._children(cell, 'p')):
                        self._locations[paragraph] = cell_location.model_copy(update={
                            'paragraph_position_in_cell': paragraph_index,
                        })
                    for nested in self._children(cell, 'tbl'):
                        nested_index -= 1
                        table(nested, nested_index, owner, [*(path or []), identifier, row_index, cell_index])

        block_index = paragraph_index = 0
        for child in self.document.element.body:
            if child.tag == W + 'p':
                self._locations[child] = SourceLocation(
                    body_block_index=block_index, paragraph_index=paragraph_index,
                )
                block_index += 1
                paragraph_index += 1
            elif child.tag == W + 'tbl':
                table(child, block_index, block_index)
                block_index += 1

    def location(self, element, part_name):
        current = element
        while current is not None:
            if part_name == 'word/document.xml' and current in self._locations:
                return self._locations[current].model_copy(deep=True)
            current = current.getparent()

        location = SourceLocation(document_part=part_name)
        ancestors = [element, *element.iterancestors()]
        cell = next((node for node in ancestors if node.tag == W + 'tc'), None)
        row = next((node for node in ancestors if node.tag == W + 'tr'), None)
        table = next((node for node in ancestors if node.tag == W + 'tbl'), None)
        paragraph = next((node for node in ancestors if node.tag == W + 'p'), None)
        if table is not None and part_name != 'word/document.xml':
            # Diagnostic-only tables use owning-part XML preorder ordinals.
            location.table_index = list(table.getroottree().getroot().iter(W + 'tbl')).index(table)
        if row is not None and row.getparent() is not None:
            location.row_index = self._children(row.getparent(), 'tr').index(row)
        if cell is not None and cell.getparent() is not None:
            location.cell_index = self._children(cell.getparent(), 'tc').index(cell)
        if paragraph is not None and paragraph.getparent() is not None:
            parent = paragraph.getparent()
            if parent.tag == W + 'tc':
                location.paragraph_position_in_cell = self._children(parent, 'p').index(paragraph)
            elif parent.tag in (W + 'hdr', W + 'ftr'):
                location.paragraph_index = self._children(parent, 'p').index(paragraph)
        return location

    def gap(self, tag, part_name, description, *, element=None, reason='unsupported_structure'):
        if reason == 'inventory_unavailable':
            self.assessed = False
        self.gaps.append(UnsupportedContent(
            tag=tag,
            parent_path=self.source_path(element, part_name) if element is not None else part_name,
            description=description, extracted=False, reason=reason,
            source_location=self.location(element, part_name) if element is not None else SourceLocation(document_part=part_name),
        ))

    def _reference(self, element, section_index):
        kind = 'header' if element.tag == W + 'headerReference' else 'footer'
        variant, rid = element.get(qn('w:type')), element.get(qn('r:id'))
        part_name = None
        status = 'unresolved'
        relationship = self.document.part.rels.get(rid)
        expected_type = RT.HEADER if kind == 'header' else RT.FOOTER
        expected_content = CT.WML_HEADER if kind == 'header' else CT.WML_FOOTER
        if relationship is not None and not relationship.is_external:
            part = relationship.target_part
            part_name = str(part.partname).lstrip('/')
            root = getattr(part, '_element', None)
            if (
                variant in {'default', 'first', 'even'} and relationship.reltype == expected_type
                and part.content_type == expected_content and root is not None
                and root.tag == W + ('hdr' if kind == 'header' else 'ftr')
            ):
                status = 'resolved'
                self.parts.setdefault(part_name, part)
                if variant == 'default':
                    self.default_parts.add(part_name)
        if status == 'unresolved':
            self.assessed = False
            self.gap(
                str(element.tag), 'word/document.xml',
                'Header/footer reference could not be resolved within its declared kind and variant',
                element=element, reason='unresolved_reference',
            )
        return HeaderFooterReference(
            section_index=section_index, source_section_index=section_index,
            kind=kind, variant=variant, relationship_id=rid, document_part=part_name,
            resolution_status=status, inherited=False,
            source_path=self.source_path(element, 'word/document.xml'),
            source_location=self.location(element, 'word/document.xml'),
        )

    def _discover_references(self):
        effective = {}
        for section_index, section in enumerate(self.document.sections):
            explicit = {}
            for element in section._sectPr:
                if element.tag not in (W + 'headerReference', W + 'footerReference'):
                    continue
                reference = self._reference(element, section_index)
                self.references.append(reference)
                if reference.variant in {'default', 'first', 'even'}:
                    explicit.setdefault((reference.kind, reference.variant), []).append(reference)
            for key, previous in effective.items():
                if key not in explicit:
                    self.references.extend(reference.model_copy(update={
                        'section_index': section_index, 'inherited': True,
                    }, deep=True) for reference in previous)
            for key, references in explicit.items():
                targets = {reference.document_part for reference in references}
                if len(targets) > 1:
                    self.assessed = False
                    self.gap(
                        'header_footer_reference_conflict', 'word/document.xml',
                        'Conflicting header/footer references for one section and variant',
                        element=section._sectPr, reason='unresolved_reference',
                    )
                effective[key] = references

    def _walk(self, element, part_name, *, header_footer=False):
        local = element.tag.rsplit('}', 1)[-1]
        if element.tag == W + 'hyperlink' and element.get(qn('w:anchor')) is not None:
            self.gap(str(element.tag), part_name, 'Internal hyperlink anchor preservation is unsupported', element=element)
        if local in self.unsupported_names or (header_footer and element.tag == W + 'tbl'):
            self.gap(str(element.tag), part_name, f'Unextracted document content: {local}', element=element)
            return
        allowed = SUPPORTED_CHILDREN.get(element.tag)
        for child in element:
            child_local = child.tag.rsplit('}', 1)[-1]
            if allowed is not None and child.tag not in allowed:
                self.gap(str(child.tag), part_name, f'Unextracted content container: {child_local}', element=child)
            else:
                self._walk(child, part_name, header_footer=header_footer)

    def scan(self):
        self._discover_references()
        self._walk(self.document.element.body, 'word/document.xml')
        for part in sorted(self.document.part.package.parts, key=lambda item: str(item.partname)):
            if part is self.document.part:
                continue
            part_name = str(part.partname).lstrip('/')
            if part.content_type in (CT.WML_HEADER, CT.WML_FOOTER):
                self.parts.setdefault(part_name, part)
                root = getattr(part, '_element', None)
                expected = W + ('hdr' if part.content_type == CT.WML_HEADER else 'ftr')
                if root is None or root.tag != expected:
                    self.gap('document_part', part_name, 'Header/footer content inventory is unavailable', reason='inventory_unavailable')
                else:
                    if part_name not in self.default_parts:
                        self.gap('document_part', part_name, 'Unextracted non-default or unreferenced header/footer part')
                    self._walk(root, part_name, header_footer=True)
            elif part.content_type == CT.WML_NUMBERING:
                root = getattr(part, '_element', None)
                if root is None:
                    self.gap('document_part', part_name, 'Numbering definition coverage is unknown', reason='inventory_unavailable')
                    continue
                for fmt in root.iter(qn('w:numFmt')):
                    if fmt.get(qn('w:val')) is None:
                        self.gap(str(fmt.tag), part_name, 'Numbering format value is missing', element=fmt)
                for override in root.iter(qn('w:lvlOverride')):
                    if override.find(qn('w:startOverride')) is not None and override.find(qn('w:lvl')) is not None:
                        self.gap(str(override.tag), part_name, 'Combined numbering start and level overrides are unsupported', element=override)
            elif (
                part_name.startswith('word/') and part.content_type not in self.ignored_part_types
                and (part.content_type.endswith('+xml') or part.content_type in ('application/xml', 'text/xml'))
            ):
                self.gap('document_part', part_name, 'Unextracted document content part')
        return self.gaps
