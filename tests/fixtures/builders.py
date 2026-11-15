'''Builders that create small synthetic DOCX files for extraction tests.'''

from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.shared import Pt

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
