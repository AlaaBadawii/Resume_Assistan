'''Builders that create small synthetic DOCX files for extraction tests.'''

import zipfile
from io import BytesIO
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def save(document: Document, path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    document.save(target)
    return target


def build_interleaved_document(path):
    document = Document()
    document.add_paragraph('Intro paragraph for the synthetic document.')
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = 'Name'
    table.cell(0, 1).text = 'Value'
    table.cell(1, 0).text = 'Alpha'
    table.cell(1, 1).text = 'Beta'
    document.add_paragraph('Outro paragraph after the table.')
    return save(document, path)


def build_headings_document(path):
    document = Document()
    for text, size in (
        ('1. Professional Identity', 16.0),
        ('Professional summary body text.', 11.0),
        ('1.1 Sub Heading Inside Section', 13.0),
        ('Sub heading body text.', 11.0),
        ('2. Technical Profile', 16.0),
        ('Technical skills body text.', 11.0),
    ):
        paragraph = document.add_paragraph()
        run = paragraph.add_run(text)
        run.bold = True
        run.font.size = Pt(size)
    return save(document, path)


def build_table_document(path):
    document = Document()
    table = document.add_table(rows=2, cols=3)
    for column, value in enumerate(('A', 'B', 'C')):
        table.cell(0, column).text = f'Header {value}'
    for row in range(1, 2):
        for column in range(3):
            table.cell(row, column).text = f'R{row}C{column}'
    return save(document, path)


def build_nested_table_document(path):
    document = Document()
    outer = document.add_table(rows=1, cols=2)
    outer.cell(0, 0).text = 'outer first'
    outer.cell(0, 1).text = 'outer second'
    outer_tbl = outer._tbl
    tr_element = [c for c in outer_tbl if c.tag == (W + 'tr')][0]
    tc_elements = [c for c in tr_element if c.tag == (W + 'tc')]
    inner_tbl = OxmlElement('w:tbl', tc_elements[1])
    inner_tr = OxmlElement('w:tr', inner_tbl)
    inner_tc = OxmlElement('w:tc', inner_tr)
    inner_p = OxmlElement('w:p', inner_tc)
    inner_r = OxmlElement('w:r', inner_p)
    inner_t = OxmlElement('w:t', inner_r)
    inner_t.text = 'nested inner cell'
    inner_r.append(inner_t)
    inner_p.append(inner_r)
    inner_tc.append(inner_p)
    inner_tr.append(inner_tc)
    inner_tbl.append(inner_tr)
    tc_elements[1].append(inner_tbl)
    return save(document, path)


def build_hyperlink_document(path):
    document = Document()
    paragraph = document.add_paragraph()
    run = paragraph.add_run('See the documentation at ')
    hyperlink = OxmlElement('w:hyperlink', paragraph._p)
    hyperlink_run = OxmlElement('w:r', hyperlink)
    hyperlink_text = OxmlElement('w:t', hyperlink_run)
    hyperlink_text.text = 'https://example.com/project'
    hyperlink_run.append(hyperlink_text)
    hyperlink.append(hyperlink_run)
    paragraph._p.append(hyperlink)
    return save(document, path)


def build_header_footer_document(path):
    document = Document()
    document.add_paragraph('Body content for the header footer fixture.')
    section = document.sections[0]
    section.header.add_paragraph('Synthetic Header Text')
    section.footer.add_paragraph('Synthetic Footer Text')
    return save(document, path)


def build_textbox_document(path):
    document = Document()
    paragraph = document.add_paragraph()
    textbox = OxmlElement('w:txbxContent', paragraph._p)
    textbox_paragraph = OxmlElement('w:p', textbox)
    textbox_run = OxmlElement('w:r', textbox_paragraph)
    textbox_text = OxmlElement('w:t', textbox_run)
    textbox_text.text = 'inside textbox'
    textbox.append(textbox_paragraph)
    paragraph._p.append(textbox)
    return save(document, path)


def build_hyperlink_relationship_document(path):
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run('See ')
    from docx.opc.constants import RELATIONSHIP_TYPE

    rel = document.part.rels.add_relationship(
        reltype=RELATIONSHIP_TYPE.HYPERLINK,
        target='https://example.org/real-project',
        rId='rId99',
        is_external=True,
    )
    hyperlink = OxmlElement('w:hyperlink', paragraph._p)
    hyperlink.set(qn('r:id'), rel.rId)
    hyperlink_run = OxmlElement('w:r', hyperlink)
    hyperlink_text = OxmlElement('w:t', hyperlink_run)
    hyperlink_text.text = 'the real project'
    hyperlink_run.append(hyperlink_text)
    hyperlink.append(hyperlink_run)
    paragraph._p.append(hyperlink)
    return save(document, path)


def build_unresolved_hyperlink_document(path):
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run('See ')
    hyperlink = OxmlElement('w:hyperlink', paragraph._p)
    hyperlink.set(qn('r:id'), 'rIdZZZ')
    hyperlink_run = OxmlElement('w:r', hyperlink)
    hyperlink_text = OxmlElement('w:t', hyperlink_run)
    hyperlink_text.text = 'the missing resource'
    hyperlink_run.append(hyperlink_text)
    hyperlink.append(hyperlink_run)
    paragraph._p.append(hyperlink)
    return save(document, path)


def build_table_cell_hyperlink_document(path):
    document = Document()
    table = document.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)
    cell.text = 'See the project'
    paragraph = cell.paragraphs[0]
    paragraph.add_run('See the project')
    from docx.opc.constants import RELATIONSHIP_TYPE

    rel = document.part.rels.add_relationship(
        reltype=RELATIONSHIP_TYPE.HYPERLINK,
        target='https://example.org/rel-project',
        rId='rId99',
        is_external=True,
    )
    hyperlink = OxmlElement('w:hyperlink', paragraph._p)
    hyperlink.set(qn('r:id'), rel.rId)
    hyperlink_run = OxmlElement('w:r', hyperlink)
    hyperlink_text = OxmlElement('w:t', hyperlink_run)
    hyperlink_text.text = 'the real project'
    hyperlink_run.append(hyperlink_text)
    hyperlink.append(hyperlink_run)
    paragraph._p.append(hyperlink)
    return save(document, path)


def build_multi_table_document(path):
    document = Document()
    table_one = document.add_table(rows=2, cols=2)
    table_one.cell(0, 0).text = 'T1H0'
    table_one.cell(0, 1).text = 'T1H1'
    table_one.cell(1, 0).text = 'T1R0'
    table_one.cell(1, 1).text = 'T1R1'
    table_two = document.add_table(rows=2, cols=2)
    table_two.cell(0, 0).text = 'T2H0'
    table_two.cell(0, 1).text = 'T2H1'
    table_two.cell(1, 0).text = 'T2R0'
    table_two.cell(1, 1).text = 'T2R1'
    return save(document, path)


def build_table_cell_multi_paragraph_document(path):
    document = Document()
    table = document.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)
    cell.text = 'First part.'
    cell.add_paragraph('Second part.')
    return save(document, path)


def _append_nested_table_to_cell(table_element, row_index: int, cell_index: int, text: str) -> None:
    rows = [child for child in table_element if child.tag == (W + 'tr')]
    tr_element = rows[row_index]
    tc_elements = [child for child in tr_element if child.tag == (W + 'tc')]
    tc_element = tc_elements[cell_index]
    nested_tbl = OxmlElement('w:tbl', tc_element)
    nested_tr = OxmlElement('w:tr', nested_tbl)
    nested_tc = OxmlElement('w:tc', nested_tr)
    nested_p = OxmlElement('w:p', nested_tc)
    nested_r = OxmlElement('w:r', nested_p)
    nested_t = OxmlElement('w:t', nested_r)
    nested_t.text = text
    nested_r.append(nested_t)
    nested_p.append(nested_r)
    nested_tc.append(nested_p)
    nested_tr.append(nested_tc)
    nested_tbl.append(nested_tr)
    tc_element.append(nested_tbl)


def build_multiple_nested_document(path):
    document = Document()
    outer = document.add_table(rows=2, cols=2)
    outer.cell(0, 0).text = 'outer A0'
    outer.cell(0, 1).text = 'outer A1'
    _append_nested_table_to_cell(outer._tbl, 0, 1, 'nested A0')
    outer.cell(1, 0).text = 'outer B0'
    outer.cell(1, 1).text = 'outer B1'
    _append_nested_table_to_cell(outer._tbl, 1, 1, 'nested B1')
    return save(document, path)


NUMBERING_OVERRIDE_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:abstractNum w:abstractNumId="1">'
    '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/></w:lvl>'
    '</w:abstractNum>'
    '<w:num w:numId="1">'
    '<w:abstractNumId w:val="1"/>'
    '<w:lvlOverride w:ilvl="0">'
    '<w:lvl w:ilvl="0"><w:start w:val="3"/><w:numFmt w:val="lowerLetter"/></w:lvl>'
    '</w:lvlOverride>'
    '</w:num>'
    '<w:num w:numId="2">'
    '<w:abstractNumId w:val="1"/>'
    '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="5"/></w:lvlOverride>'
    '</w:num>'
    '</w:numbering>'
)


def _set_num_pr(paragraph, num_id: int, ilvl: int) -> None:
    pPr = paragraph._p.pPr
    if pPr is None:
        pPr = OxmlElement('w:pPr', paragraph._p)
        paragraph._p.append(pPr)
    numPr = OxmlElement('w:numPr')
    ilvl_el = OxmlElement('w:ilvl')
    ilvl_el.set(qn('w:val'), str(ilvl))
    num_id_el = OxmlElement('w:numId')
    num_id_el.set(qn('w:val'), str(num_id))
    numPr.append(ilvl_el)
    numPr.append(num_id_el)
    pPr.append(numPr)


def build_numbering_override_document(path):
    source_path = Path(path)
    document = Document()
    _set_num_pr(document.add_paragraph('Numbered item one'), 1, 0)
    _set_num_pr(document.add_paragraph('Numbered item two'), 2, 0)
    _set_num_pr(document.add_paragraph('Unresolved numbering item'), 999, 0)
    document.save(source_path)
    tmp_path = Path(str(source_path) + '.new')
    with zipfile.ZipFile(str(source_path), 'r') as zin, zipfile.ZipFile(str(tmp_path), 'w', zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == 'word/numbering.xml':
                data = NUMBERING_OVERRIDE_XML.encode('utf-8')
            zout.writestr(item, data)
    tmp_path.replace(source_path)
    return source_path


def build_malformed_numbering_document(path, value):
    document = Document()
    document.add_heading('1. Technical Profile', level=1)
    paragraph = document.add_paragraph('Synthetic malformed numbering item')
    num_id = paragraph._p.get_or_add_pPr().get_or_add_numPr().get_or_add_numId()
    num_id.set(qn('w:val'), value)
    return save(document, path)


def _synthetic_paragraph_element(text):
    paragraph = OxmlElement('w:p')
    run = OxmlElement('w:r')
    word = OxmlElement('w:t')
    word.text = text
    run.append(word)
    paragraph.append(run)
    return paragraph


def build_completeness_document(path, gap=None, preamble=False, unresolved=None):
    document = Document()
    if preamble:
        document.add_paragraph('Synthetic unclassified preamble')
    document.add_heading('1. Technical Profile', level=1)
    paragraph = document.add_paragraph('Synthetic supported body evidence')
    gap_text = 'Synthetic unextracted evidence'
    if gap in ('sdt_body', 'sdt_cell'):
        sdt = OxmlElement('w:sdt')
        content = OxmlElement('w:sdtContent')
        content.append(_synthetic_paragraph_element(gap_text))
        sdt.append(content)
        if gap == 'sdt_body':
            document.element.body.insert(len(document.element.body) - 1, sdt)
        else:
            cell = document.add_table(rows=1, cols=1).cell(0, 0)
            cell._tc.insert(len(cell._tc) - 1, sdt)
    elif gap in ('header_table', 'footer_table'):
        container = document.sections[0].header if gap == 'header_table' else document.sections[0].footer
        container.add_table(rows=1, cols=1, width=Inches(2)).cell(0, 0).text = gap_text
    elif gap == 'alternate_content':
        from lxml import etree

        mc = '{http://schemas.openxmlformats.org/markup-compatibility/2006}'
        alternate = etree.Element(mc + 'AlternateContent', nsmap={
            'mc': mc[1:-1], 'w14': 'http://schemas.microsoft.com/office/word/2010/wordml',
        })
        choice = etree.Element(mc + 'Choice', Requires='w14')
        choice.append(_synthetic_paragraph_element(gap_text))
        fallback = etree.Element(mc + 'Fallback')
        fallback.append(_synthetic_paragraph_element(gap_text))
        alternate.append(choice)
        alternate.append(fallback)
        document.element.body.insert(len(document.element.body) - 1, alternate)
    elif gap == 'unknown_body':
        container = OxmlElement('w:unknownContent')
        container.append(_synthetic_paragraph_element(gap_text))
        document.element.body.insert(len(document.element.body) - 1, container)
    elif gap == 'first_page_header':
        document.sections[0].different_first_page_header_footer = True
        document.sections[0].first_page_header.paragraphs[0].text = gap_text
    elif gap == 'comments':
        document.add_comment(paragraph.runs[0], text=gap_text, author='Synthetic reviewer')
    elif gap == 'internal_anchor':
        hyperlink = OxmlElement('w:hyperlink', attrs={qn('w:anchor'): 'SyntheticBookmark'})
        run = OxmlElement('w:r')
        text = OxmlElement('w:t')
        text.text = 'Supported bookmark display'
        run.append(text)
        hyperlink.append(run)
        paragraph._p.append(hyperlink)
    elif gap in ('footnotes', 'endnotes'):
        from docx.opc.constants import CONTENT_TYPE, RELATIONSHIP_TYPE
        from docx.opc.packuri import PackURI
        from docx.opc.part import Part

        content_type = CONTENT_TYPE.WML_FOOTNOTES if gap == 'footnotes' else CONTENT_TYPE.WML_ENDNOTES
        relationship_type = RELATIONSHIP_TYPE.FOOTNOTES if gap == 'footnotes' else RELATIONSHIP_TYPE.ENDNOTES
        singular = 'footnote' if gap == 'footnotes' else 'endnote'
        blob = (
            f'<w:{gap} xmlns:w="{W[1:-1]}"><w:{singular} w:id="1">'
            f'<w:p><w:r><w:t>{gap_text}</w:t></w:r></w:p>'
            f'</w:{singular}></w:{gap}>'
        ).encode('utf-8')
        part = Part(PackURI(f'/word/{gap}.xml'), content_type, blob, document.part.package)
        document.part.relate_to(part, relationship_type)
    if unresolved == 'hyperlink':
        hyperlink = OxmlElement('w:hyperlink', attrs={qn('r:id'): 'rIdSyntheticMissing'})
        run = OxmlElement('w:r')
        text = OxmlElement('w:t')
        text.text = 'Synthetic unresolved display'
        run.append(text)
        hyperlink.append(run)
        paragraph._p.append(hyperlink)
    elif unresolved == 'numbering':
        num_pr = paragraph._p.get_or_add_pPr().get_or_add_numPr()
        num_pr.get_or_add_numId().val = 999999
        num_pr.get_or_add_ilvl().val = 0
    elif unresolved == 'list_style':
        paragraph.style = document.styles['List Number']
    elif unresolved == 'custom_numbered_style':
        from docx.enum.style import WD_STYLE_TYPE

        base = document.styles.add_style('Synthetic Numbered Base', WD_STYLE_TYPE.PARAGRAPH)
        properties = base.element.get_or_add_pPr()
        num_pr = OxmlElement('w:numPr')
        num_pr.append(OxmlElement('w:numId', attrs={qn('w:val'): '1'}))
        properties.append(num_pr)
        derived = document.styles.add_style('Synthetic Numbered Derived', WD_STYLE_TYPE.PARAGRAPH)
        derived.base_style = base
        paragraph.style = derived
    return save(document, path)


def build_integrity_nested_document(path):
    document = Document()
    document.add_heading('1. Technical Profile', level=1)
    document.add_paragraph('Before first table')
    first = document.add_table(rows=2, cols=2)
    first.cell(0, 0).text = 'root-one r0c0'
    first.cell(0, 1).text = 'root-one r0c1'
    first.cell(1, 0).text = 'root-one r1c0 first'
    first.cell(1, 0).add_paragraph('root-one r1c0 second')
    first.cell(1, 1).text = 'root-one r1c1'

    child = first.cell(0, 0).add_table(rows=1, cols=2)
    child.cell(0, 0).text = 'child left'
    child.cell(0, 1).text = 'child right'
    deep = child.cell(0, 1).add_table(rows=2, cols=1)
    deep.cell(0, 0).text = 'deep r0c0 first'
    deep.cell(0, 0).add_paragraph('deep r0c0 second')
    deep.cell(1, 0).text = 'deep r1c0'
    first.cell(0, 0).add_table(rows=1, cols=1).cell(0, 0).text = 'sibling child'
    first.cell(0, 1).add_table(rows=1, cols=1).cell(0, 0).text = 'other-cell child'

    document.add_paragraph('Between tables')
    second = document.add_table(rows=1, cols=2)
    second.cell(0, 0).text = 'root-two r0c0'
    second.cell(0, 1).text = 'root-two r0c1'
    second.cell(0, 1).add_table(rows=1, cols=1).cell(0, 0).text = 'second-parent child'
    return save(document, path)


def replace_docx_part(path, part_name, data):
    source = Path(path)
    output = BytesIO()
    with zipfile.ZipFile(source) as incoming, zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as outgoing:
        for item in incoming.infolist():
            if item.filename == part_name:
                if data is not None:
                    outgoing.writestr(item, data)
            else:
                outgoing.writestr(item, incoming.read(item.filename))
    source.write_bytes(output.getvalue())
    return source


def build_numbering_fidelity_document(
    path, *, numbering_xml=NUMBERING_OVERRIDE_XML, num_id=1, ilvl=0, style_depth=0,
):
    from docx.enum.style import WD_STYLE_TYPE

    document = Document()
    document.add_heading('1. Technical Profile', level=1)
    paragraph = document.add_paragraph('Python numbered evidence')
    if style_depth:
        base = document.styles.add_style('Synthetic Numbering 0', WD_STYLE_TYPE.PARAGRAPH)
        properties = base.element.get_or_add_pPr().get_or_add_numPr()
        properties.get_or_add_numId().val = num_id
        properties.get_or_add_ilvl().val = ilvl
        for depth in range(1, style_depth):
            derived = document.styles.add_style(f'Synthetic Numbering {depth}', WD_STYLE_TYPE.PARAGRAPH)
            derived.base_style = base
            base = derived
        paragraph.style = base
    else:
        paragraph._p.get_or_add_pPr()
        _set_num_pr(paragraph, num_id, ilvl)
    source = save(document, path)
    return replace_docx_part(source, 'word/numbering.xml', numbering_xml.encode('utf-8'))


def append_hyperlink(paragraph, text, *, relationship_id=None, anchor=None):
    hyperlink = OxmlElement('w:hyperlink')
    if relationship_id is not None:
        hyperlink.set(qn('r:id'), relationship_id)
    if anchor is not None:
        hyperlink.set(qn('w:anchor'), anchor)
    run = OxmlElement('w:r')
    word = OxmlElement('w:t')
    word.text = text
    run.append(word)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)
    return hyperlink
