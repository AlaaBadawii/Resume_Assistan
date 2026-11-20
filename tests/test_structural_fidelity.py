'''Synthetic structural fidelity, provenance, and failure-path regressions.'''

import hashlib
import json
import zipfile

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from lxml import etree

from resume_assistant import cli, evidence, extractor
from resume_assistant.extractor import InvalidMasterResumeError, MasterResumeExtractor
from resume_assistant.models import DocumentSnapshot, SourceLocation
from resume_assistant.publication_reader import load_committed_pair
from tests.fixtures import builders


PRIVATE_MARKER = 'SYNTHETIC_PRIVATE_NUMBERING_VALUE'
PARSE_DETAIL = 'word/numbering.xml could not be parsed (invalid numbering value)'
FIELD_DETAIL = 'field sequence is incomplete'


def _document():
    document = Document()
    document.add_heading('1. Technical Profile', level=1)
    return document


def _extract(document, path):
    source = builders.save(document, path)
    snapshot = MasterResumeExtractor(source).extract()
    return source, snapshot, evidence.build_evidence_catalog(snapshot)


def _simple_field(paragraph, instruction, result='SYNTHETIC_CACHED_RESULT'):
    element = OxmlElement('w:fldSimple')
    if instruction is not None:
        element.set(qn('w:instr'), instruction)
    run = OxmlElement('w:r')
    text = OxmlElement('w:t')
    text.text = result
    run.append(text)
    element.append(run)
    paragraph._p.append(element)


def _field_tokens(paragraph, tokens):
    for kind, value in tokens:
        if kind == 'result':
            paragraph.add_run(value)
            continue
        run = OxmlElement('w:r')
        element = OxmlElement(f'w:{kind}')
        if kind == 'fldChar':
            element.set(qn('w:fldCharType'), value)
        else:
            element.text = value
        run.append(element)
        paragraph._p.append(run)


def _remove_numbering(source):
    with zipfile.ZipFile(source) as package:
        relationships = etree.fromstring(package.read('word/_rels/document.xml.rels'))
    for relationship in list(relationships):
        if relationship.get('Type') == RT.NUMBERING:
            relationships.remove(relationship)
    builders.replace_docx_part(source, 'word/_rels/document.xml.rels', etree.tostring(relationships))
    builders.replace_docx_part(source, 'word/numbering.xml', None)


@pytest.mark.parametrize('bookmark_present', [False, True])
def test_internal_anchor_value_survives_without_claiming_target_resolution(tmp_path, bookmark_present):
    document = _document()
    paragraph = document.add_paragraph('See ')
    if bookmark_present:
        paragraph._p.append(OxmlElement('w:bookmarkStart', attrs={qn('w:id'): '1', qn('w:name'): 'SyntheticBookmark'}))
        paragraph._p.append(OxmlElement('w:bookmarkEnd', attrs={qn('w:id'): '1'}))
    builders.append_hyperlink(paragraph, 'section', anchor='SyntheticBookmark')
    _, snapshot, catalog = _extract(document, tmp_path / 'anchor.docx')
    link = snapshot.blocks[1].paragraph.hyperlinks[0]
    assert (link.text, link.anchor, link.relationship_id, link.url) == ('section', 'SyntheticBookmark', None, None)
    assert link.source_location == SourceLocation(body_block_index=1, paragraph_index=1)
    record = next(record for record in catalog.evidence_records if record.original_content == 'See section')
    assert record.hyperlinks[0] == link
    assert len(snapshot.unsupported_content) == 1
    assert snapshot.unsupported_content[0].source_location == link.source_location
    assert catalog.extraction_completeness.source_preservation_complete is False
    assert catalog.extraction_completeness.complete is False
    assert snapshot.unresolved_extraction_details == []


def test_external_and_internal_links_keep_distinct_targets_and_part_local_locations(tmp_path):
    document = _document()
    paragraph = document.add_paragraph('Python ')
    document.part.rels.add_relationship(RT.HYPERLINK, 'https://example.invalid/body', 'rIdSynthetic', is_external=True)
    builders.append_hyperlink(paragraph, 'external', relationship_id='rIdSynthetic')
    builders.append_hyperlink(paragraph, 'internal', anchor='SyntheticBookmark')
    header = document.sections[0].header.paragraphs[0]
    header.part.rels.add_relationship(RT.HYPERLINK, 'https://example.invalid/header', 'rIdSynthetic', is_external=True)
    builders.append_hyperlink(header, 'header', relationship_id='rIdSynthetic')
    _, snapshot, catalog = _extract(document, tmp_path / 'parts.docx')
    external, internal = snapshot.blocks[1].paragraph.hyperlinks
    assert (external.url, external.anchor) == ('https://example.invalid/body', None)
    assert (internal.url, internal.anchor) == (None, 'SyntheticBookmark')
    part = snapshot.header_footer_parts[0]
    header_link = part.blocks[0].paragraph.hyperlinks[0]
    assert header_link.url == 'https://example.invalid/header'
    assert header_link.source_location == SourceLocation(document_part='word/header1.xml', paragraph_index=0)
    assert snapshot.statistics.hyperlink_count == 3
    assert catalog.extraction_completeness.complete is False


def test_unresolved_anchor_relationship_has_a_diagnostic_and_keeps_the_anchor(tmp_path):
    document = _document()
    paragraph = document.add_paragraph('Python ')
    builders.append_hyperlink(paragraph, 'missing', relationship_id='rIdMissing', anchor='SyntheticBookmark')
    _, snapshot, catalog = _extract(document, tmp_path / 'missing-link.docx')
    assert snapshot.blocks[1].paragraph.hyperlinks[0].anchor == 'SyntheticBookmark'
    assert snapshot.unresolved_extraction_details == ['hyperlink SyntheticBookmark has unresolved relationship rIdMissing']
    assert catalog.extraction_completeness.complete is False


def test_row_hyperlink_provenance_keeps_cells_paragraphs_nested_paths_and_ids(tmp_path, monkeypatch):
    source = builders.build_integrity_nested_document(tmp_path / 'nested-links.docx')
    document = Document(source)
    document.part.rels.add_relationship(RT.HYPERLINK, 'https://example.invalid/shared', 'rIdSynthetic', is_external=True)
    root = document.tables[0]
    builders.append_hyperlink(root.cell(0, 0).paragraphs[0], 'same display', relationship_id='rIdSynthetic')
    builders.append_hyperlink(root.cell(0, 1).add_paragraph(''), 'same display', relationship_id='rIdSynthetic')
    deep = root.cell(0, 0).tables[0].cell(0, 1).tables[0]
    builders.append_hyperlink(deep.cell(0, 0).paragraphs[1], 'same display', relationship_id='rIdSynthetic')
    document.save(source)
    before = source.read_bytes()
    snapshot = MasterResumeExtractor(source).extract()
    catalog = evidence.build_evidence_catalog(snapshot)
    root_record = next(record for record in catalog.evidence_records if record.table_context and (record.table_context.table_index, record.table_context.row_index) == (2, 0))
    expected_root = [
        SourceLocation(body_block_index=2, table_index=2, row_index=0, cell_index=0, paragraph_position_in_cell=0),
        SourceLocation(body_block_index=2, table_index=2, row_index=0, cell_index=1, paragraph_position_in_cell=2),
    ]
    assert [link.source_location for link in root_record.hyperlinks] == expected_root
    deep_record = next(record for record in catalog.evidence_records if record.table_context and (record.table_context.table_index, record.table_context.row_index) == (-2, 0))
    assert deep_record.hyperlinks[0].source_location == SourceLocation(
        body_block_index=2, table_index=-2, row_index=0, cell_index=0,
        nested_table_path=[2, 0, 0, -1, 0, 1], paragraph_position_in_cell=1,
    )
    digest = hashlib.sha256(before).hexdigest()
    assert root_record.evidence_id == hashlib.sha256(f'{digest}|2|row:0'.encode()).hexdigest()
    assert deep_record.evidence_id == hashlib.sha256(f'{digest}|-2|row:0'.encode()).hexdigest()
    assert all(record.source_document == source.name and record.source_sha256 == digest for record in catalog.evidence_records)
    monkeypatch.setattr(cli, 'PROJECT_ROOT', tmp_path)
    output = tmp_path / 'data/private/processed/catalog.json'
    cli.run_extract_master(source, output, output.with_name('snapshot.json'))
    restored_snapshot, restored_catalog, _, _ = load_committed_pair(tmp_path, output)
    assert restored_catalog == catalog
    assert evidence.build_evidence_catalog(restored_snapshot) == catalog
    assert source.read_bytes() == before


@pytest.mark.parametrize('num_id, ilvl, expected', [
    (1, 0, ('lowerLetter', 1, 3)),
    (2, 0, ('decimal', 1, 5)),
    (999, 0, ('unresolved', None, None)),
    (1, 7, ('unresolved', 1, None)),
])
def test_numbering_identifiers_and_effective_start_values_are_preserved(tmp_path, num_id, ilvl, expected):
    source = builders.build_numbering_fidelity_document(tmp_path / 'numbering.docx', num_id=num_id, ilvl=ilvl)
    snapshot = MasterResumeExtractor(source).extract()
    paragraph = snapshot.blocks[1].paragraph
    assert paragraph.numbering_num_id == num_id
    assert paragraph.numbering_level == ilvl
    assert (paragraph.numbering_format, paragraph.numbering_abstract_num_id, paragraph.numbering_start) == expected
    state = evidence.build_evidence_catalog(snapshot).extraction_completeness
    assert state.source_preservation_complete is True
    assert state.complete is (expected[0] != 'unresolved')
    assert bool(snapshot.unresolved_extraction_details) is (expected[0] == 'unresolved')


def test_combined_start_and_level_override_retains_level_start_and_coverage_gap(tmp_path):
    xml = builders.NUMBERING_OVERRIDE_XML.replace('<w:lvlOverride w:ilvl="0">', '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="9"/>', 1)
    source = builders.build_numbering_fidelity_document(tmp_path / 'combined.docx', numbering_xml=xml)
    snapshot = MasterResumeExtractor(source).extract()
    paragraph = snapshot.blocks[1].paragraph
    assert (paragraph.numbering_format, paragraph.numbering_start) == ('lowerLetter', 3)
    assert (paragraph.numbering_num_id, paragraph.numbering_abstract_num_id) == (1, 1)
    assert len(snapshot.unsupported_content) == 1
    assert snapshot.unsupported_content[0].source_location.document_part == 'word/numbering.xml'
    assert evidence.build_evidence_catalog(snapshot).extraction_completeness.complete is False


@pytest.mark.parametrize('style_depth', [0, 1, 4])
def test_num_id_zero_disables_numbering_without_unresolved_diagnostics(tmp_path, style_depth):
    source = builders.build_numbering_fidelity_document(tmp_path / 'zero.docx', num_id=0, style_depth=style_depth)
    snapshot = MasterResumeExtractor(source).extract()
    paragraph = snapshot.blocks[1].paragraph
    assert paragraph.numbering_num_id == 0
    assert paragraph.numbering_format is paragraph.numbering_start is paragraph.numbering_abstract_num_id is None
    assert snapshot.unresolved_extraction_details == []
    assert evidence.build_evidence_catalog(snapshot).extraction_completeness.complete is True


def test_direct_zero_does_not_reapply_list_bullet_style_inference(tmp_path):
    document = _document()
    paragraph = document.add_paragraph('Python evidence', style='List Bullet')
    builders._set_num_pr(paragraph, 0, 0)
    _, snapshot, catalog = _extract(document, tmp_path / 'no-bullet.docx')
    assert snapshot.blocks[1].paragraph.numbering_format is None
    assert snapshot.blocks[1].paragraph.bullet is not True
    assert snapshot.unresolved_extraction_details == []
    assert catalog.extraction_completeness.complete is True


@pytest.mark.parametrize('style_depth', [1, 2, 4])
def test_based_on_style_chains_resolve_actual_format_ids_and_start(tmp_path, style_depth):
    source = builders.build_numbering_fidelity_document(tmp_path / 'style.docx', style_depth=style_depth, num_id=2)
    snapshot = MasterResumeExtractor(source).extract()
    paragraph = snapshot.blocks[1].paragraph
    assert (paragraph.numbering_format, paragraph.numbering_level, paragraph.numbering_start) == ('decimal', 0, 5)
    assert (paragraph.numbering_num_id, paragraph.numbering_abstract_num_id) == (2, 1)
    assert snapshot.unresolved_extraction_details == []
    assert evidence.build_evidence_catalog(snapshot).extraction_completeness.complete is True


@pytest.mark.parametrize('num_id, ilvl', [(999, 0), (1, 7)])
def test_unresolvable_style_numbering_never_implies_complete_extraction(tmp_path, num_id, ilvl):
    source = builders.build_numbering_fidelity_document(tmp_path / 'bad-style.docx', num_id=num_id, ilvl=ilvl, style_depth=3)
    snapshot = MasterResumeExtractor(source).extract()
    paragraph = snapshot.blocks[1].paragraph
    assert paragraph.numbering_num_id == num_id
    assert paragraph.numbering_format == 'unresolved'
    assert 'Style-inherited numbering was not resolved' in snapshot.unresolved_extraction_details
    assert evidence.build_evidence_catalog(snapshot).extraction_completeness.complete is False


def test_standard_list_bullet_uses_the_known_template_numbering_without_false_diagnostics(tmp_path):
    document = _document()
    document.add_paragraph('Python bullet evidence', style='List Bullet')
    _, snapshot, catalog = _extract(document, tmp_path / 'standard.docx')
    paragraph = snapshot.blocks[1].paragraph
    assert (paragraph.numbering_format, paragraph.numbering_level, paragraph.numbering_start) == ('bullet', 0, 1)
    assert (paragraph.numbering_num_id, paragraph.numbering_abstract_num_id) == (1, 8)
    assert snapshot.unresolved_extraction_details == []
    assert catalog.extraction_completeness.complete is True


@pytest.mark.parametrize('invalid_xml', [
    builders.NUMBERING_OVERRIDE_XML.replace('w:start w:val="1"', f'w:start w:val="{PRIVATE_MARKER}"', 1),
    builders.NUMBERING_OVERRIDE_XML.replace(
        '<w:num w:numId="2"><w:abstractNumId w:val="1"/>',
        f'<w:num w:numId="2"><w:abstractNumId w:val="{PRIVATE_MARKER}"/>',
    ),
])
def test_numbering_parser_failure_is_sanitized_even_when_an_earlier_definition_resolves(tmp_path, invalid_xml):
    source = builders.build_numbering_fidelity_document(tmp_path / 'invalid-values.docx', numbering_xml=invalid_xml)
    worker = MasterResumeExtractor(source)
    first, second = worker.extract(), worker.extract()
    assert PARSE_DETAIL in first.unresolved_extraction_details
    assert first.unresolved_extraction_details == second.unresolved_extraction_details
    assert all(PRIVATE_MARKER not in detail for detail in first.unresolved_extraction_details)
    assert evidence.build_evidence_catalog(first).extraction_completeness.complete is False
    assert evidence.build_evidence_catalog(second).extraction_completeness.complete is False
    restored = DocumentSnapshot.model_validate_json(first.model_dump_json())
    assert evidence.build_evidence_catalog(restored).extraction_completeness.unresolved_extraction_details == first.unresolved_extraction_details


def test_missing_numbering_part_is_distinct_from_missing_definition_and_cached_parser_failure(tmp_path):
    source = builders.build_numbering_fidelity_document(tmp_path / 'missing-part.docx')
    _remove_numbering(source)
    worker = MasterResumeExtractor(source)
    snapshots = [worker.extract(), worker.extract()]
    assert all(snapshot.unresolved_extraction_details == ['word/numbering.xml is missing', 'numbering numId=1 ilvl=0 unresolved'] for snapshot in snapshots)
    assert all(evidence.build_evidence_catalog(snapshot).extraction_completeness.complete is False for snapshot in snapshots)
    builders.build_numbering_fidelity_document(source, num_id=999)
    missing_definition = worker.extract()
    assert missing_definition.unresolved_extraction_details == ['numbering numId=999 ilvl=0 unresolved']
    assert missing_definition.source_sha256 != snapshots[0].source_sha256
    builders.build_numbering_fidelity_document(source, num_id=2)
    clean = worker.extract()
    assert clean.unresolved_extraction_details == []
    assert clean.blocks[1].paragraph.numbering_start == 5
    assert evidence.build_evidence_catalog(clean).extraction_completeness.complete is True


def test_malformed_numbering_xml_fails_closed_without_disclosing_values_or_replacing_publication(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, 'PROJECT_ROOT', tmp_path)
    source = builders.build_numbering_fidelity_document(tmp_path / 'malformed.docx')
    output = tmp_path / 'data/private/processed/catalog.json'
    cli.run_extract_master(source, output, output.with_name('snapshot.json'))
    previous = output.read_bytes()
    builders.replace_docx_part(source, 'word/numbering.xml', f'<{PRIVATE_MARKER}'.encode())
    before = source.read_bytes()
    with pytest.raises(InvalidMasterResumeError) as failure:
        MasterResumeExtractor(source).extract()
    assert PRIVATE_MARKER not in str(failure.value)
    assert cli.main(['extract-master', '--master', str(source), '--output', str(output), '--snapshot-output', str(output.with_name('snapshot.json'))]) == 2
    captured = capsys.readouterr()
    assert PRIVATE_MARKER not in captured.out + captured.err
    assert output.read_bytes() == previous
    assert source.read_bytes() == before
    assert load_committed_pair(tmp_path, output)[1].extraction_completeness.complete is True


@pytest.mark.parametrize('instruction', [None, '', '   '])
def test_fld_simple_without_an_instruction_does_not_invent_page_or_claim_result_extraction(tmp_path, instruction):
    document = _document()
    paragraph = document.add_paragraph('Python ')
    _simple_field(paragraph, instruction)
    _, snapshot, catalog = _extract(document, tmp_path / 'simple.docx')
    block = snapshot.blocks[1].paragraph
    assert block.field is None
    assert block.field_instructions == []
    assert block.text == 'Python '
    assert snapshot.unresolved_extraction_details == []
    assert len(snapshot.unsupported_content) == 1
    assert catalog.extraction_completeness.source_preservation_complete is False
    assert catalog.extraction_completeness.complete is False


def test_multiple_simple_fields_keep_instruction_order_and_the_legacy_first_field(tmp_path):
    document = _document()
    paragraph = document.add_paragraph('Python ')
    _simple_field(paragraph, ' FIRST ')
    paragraph.add_run('between ')
    _simple_field(paragraph, 'SECOND')
    _, snapshot, catalog = _extract(document, tmp_path / 'multiple-fields.docx')
    block = snapshot.blocks[1].paragraph
    assert block.field == 'FIRST'
    assert block.field_instructions == ['FIRST', 'SECOND']
    assert block.text == 'Python between '
    assert snapshot.unresolved_extraction_details == []
    assert len(snapshot.unsupported_content) == 2
    assert catalog.extraction_completeness.complete is False


def test_complex_field_fragments_and_cached_result_remain_distinct(tmp_path):
    document = _document()
    paragraph = document.add_paragraph('Python ')
    _field_tokens(paragraph, [
        ('fldChar', 'begin'), ('instrText', ' REF '), ('instrText', 'SyntheticBookmark '),
        ('fldChar', 'separate'), ('result', 'visible result'), ('fldChar', 'end'),
    ])
    _, snapshot, catalog = _extract(document, tmp_path / 'complex.docx')
    block = snapshot.blocks[1].paragraph
    assert block.field == 'REF'
    assert block.field_instructions == ['REF', 'SyntheticBookmark']
    assert block.text == 'Python visible result'
    assert snapshot.unresolved_extraction_details == []
    assert len(snapshot.unsupported_content) == 5
    assert catalog.extraction_completeness.complete is False


@pytest.mark.parametrize('tokens', [
    [('fldChar', 'begin'), ('instrText', PRIVATE_MARKER)],
    [('instrText', PRIVATE_MARKER)],
    [('fldChar', 'end')],
    [('fldChar', 'separate')],
    [('fldChar', 'begin'), ('instrText', 'REF'), ('fldChar', 'separate'), ('fldChar', 'separate'), ('fldChar', 'end')],
    [('fldChar', 'begin'), ('fldChar', 'separate'), ('instrText', PRIVATE_MARKER), ('fldChar', 'end')],
    [('fldChar', 'begin'), ('instrText', 'FIRST'), ('fldChar', 'end'), ('fldChar', 'begin'), ('instrText', PRIVATE_MARKER)],
    [('fldChar', 'begin'), ('fldChar', 'begin'), ('instrText', PRIVATE_MARKER), ('fldChar', 'end')],
])
def test_malformed_complex_field_sequences_have_one_content_free_diagnostic(tmp_path, tokens):
    document = _document()
    paragraph = document.add_paragraph('Python ')
    _field_tokens(paragraph, tokens)
    source, snapshot, catalog = _extract(document, tmp_path / 'incomplete.docx')
    assert snapshot.unresolved_extraction_details == [FIELD_DETAIL]
    assert PRIVATE_MARKER not in ''.join(snapshot.unresolved_extraction_details)
    assert catalog.extraction_completeness.complete is False
    worker = MasterResumeExtractor(source)
    assert worker.extract().unresolved_extraction_details == [FIELD_DETAIL]
    builders.save(_document(), source)
    assert worker.extract().unresolved_extraction_details == []


def test_nested_and_resultless_complex_fields_do_not_get_false_incomplete_diagnostics(tmp_path):
    document = _document()
    paragraph = document.add_paragraph('Python ')
    _field_tokens(paragraph, [
        ('fldChar', 'begin'), ('instrText', 'OUTER'), ('fldChar', 'begin'),
        ('instrText', 'INNER'), ('fldChar', 'end'), ('fldChar', 'separate'),
        ('result', 'cached'), ('fldChar', 'end'),
    ])
    _, snapshot, catalog = _extract(document, tmp_path / 'nested-field.docx')
    assert snapshot.blocks[1].paragraph.field_instructions == ['OUTER', 'INNER']
    assert snapshot.blocks[1].paragraph.text == 'Python cached'
    assert snapshot.unresolved_extraction_details == []
    assert catalog.extraction_completeness.complete is False


@pytest.mark.parametrize('value, expected', [(None, 'continue'), ('continue', 'continue'), ('restart', 'restart')])
def test_vertical_merge_marker_retains_its_default_or_explicit_state(tmp_path, value, expected):
    document = _document()
    table = document.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)
    cell.text = 'Python merged evidence'
    marker = OxmlElement('w:vMerge')
    if value is not None:
        marker.set(qn('w:val'), value)
    cell._tc.get_or_add_tcPr().append(marker)
    _, snapshot, catalog = _extract(document, tmp_path / 'merge.docx')
    result = snapshot.blocks[1].table.rows[0][0]
    assert result.merged is True
    assert result.v_merge == expected
    assert result.text == 'Python merged evidence'
    assert catalog.extraction_completeness.complete is True


@pytest.mark.parametrize('span', [1, 2, 3])
def test_column_span_is_retained_without_redefining_physical_columns(tmp_path, span):
    document = _document()
    table = document.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)
    cell.text = 'Python spanning evidence'
    cell._tc.get_or_add_tcPr().append(OxmlElement('w:gridSpan', attrs={qn('w:val'): str(span)}))
    _, snapshot, catalog = _extract(document, tmp_path / 'span.docx')
    block = snapshot.blocks[1].table
    assert block.column_count == 1
    assert block.rows[0][0].grid_span == span
    assert block.rows[0][0].merged is (span > 1)
    assert catalog.evidence_records[-1].original_content == 'Python spanning evidence'


def test_cell_paragraph_table_interleaving_and_empty_content_remain_reconstructable(tmp_path):
    document = _document()
    cell = document.add_table(rows=1, cols=2).cell(0, 0)
    cell.paragraphs[0].text = 'before'
    nested = cell.add_table(rows=1, cols=1)
    nested.cell(0, 0).text = 'nested Python'
    cell.add_paragraph('after')
    _, snapshot, catalog = _extract(document, tmp_path / 'order.docx')
    outer = snapshot.blocks[1].table
    result = outer.rows[0][0]
    assert result.content_order == ['paragraph', 'table', 'paragraph', 'paragraph']
    assert [paragraph.text for paragraph in result.paragraphs] == ['before', '', 'after']
    assert result.text == 'before\nafter'
    assert result.nested_tables[0].rows[0][0].text == 'nested Python'
    empty = outer.rows[0][1]
    assert (empty.text, empty.content_order) == ('', ['paragraph'])
    assert [paragraph.text for paragraph in empty.paragraphs] == ['']
    rows = [record for record in catalog.evidence_records if record.table_context]
    assert [record.original_content for record in rows] == ['before\nafter', 'nested Python']
    assert rows[0].table_context.cell_sources[0].paragraph_positions == [0, 2]
    assert rows[1].source_location.nested_table_path == [1, 0, 0]


def test_cell_containing_only_a_nested_table_still_emits_nested_evidence(tmp_path):
    document = _document()
    cell = document.add_table(rows=1, cols=1).cell(0, 0)
    nested = cell.add_table(rows=1, cols=1)
    nested.cell(0, 0).text = 'nested Python'
    for paragraph in list(cell.paragraphs):
        cell._tc.remove(paragraph._p)
    _, snapshot, catalog = _extract(document, tmp_path / 'only-nested.docx')
    outer_cell = snapshot.blocks[1].table.rows[0][0]
    assert (outer_cell.text, outer_cell.paragraphs, outer_cell.content_order) == ('', [], ['table'])
    rows = [record for record in catalog.evidence_records if record.table_context]
    assert len(rows) == 1
    assert rows[0].original_content == 'nested Python'
    assert rows[0].source_location == SourceLocation(body_block_index=1, table_index=-1, row_index=0, nested_table_path=[1, 0, 0])
    assert catalog.extraction_completeness.complete is True


@pytest.mark.parametrize('value, expected', [(None, 'continue'), ('continue', 'continue'), ('restart', 'restart')])
def test_horizontal_merge_marker_retains_its_default_or_explicit_state(tmp_path, value, expected):
    document = _document()
    table = document.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)
    cell.text = 'Python hmerge evidence'
    marker = OxmlElement('w:hMerge')
    if value is not None:
        marker.set(qn('w:val'), value)
    cell._tc.get_or_add_tcPr().append(marker)
    _, snapshot, catalog = _extract(document, tmp_path / 'hmerge.docx')
    result = snapshot.blocks[1].table.rows[0][0]
    assert result.merged is True
    assert result.h_merge == expected
    assert result.text == 'Python hmerge evidence'
    assert catalog.extraction_completeness.complete is True


def test_cyclic_style_inheritance_is_detected_and_diagnosed(tmp_path):
    from docx.enum.style import WD_STYLE_TYPE

    document = _document()
    base = document.styles.add_style('Cyclic Base', WD_STYLE_TYPE.PARAGRAPH)
    derived = document.styles.add_style('Cyclic Derived', WD_STYLE_TYPE.PARAGRAPH)
    derived.base_style = base
    base.base_style = derived
    paragraph = document.add_paragraph('Cyclic evidence')
    paragraph.style = derived
    _, snapshot, catalog = _extract(document, tmp_path / 'cyclic.docx')
    assert 'Numbering style inheritance is cyclic' in snapshot.unresolved_extraction_details
    assert catalog.extraction_completeness.complete is False


def test_unavailable_numbering_part_reports_could_not_be_read_diagnostic(tmp_path):
    import unittest.mock

    source = builders.build_numbering_fidelity_document(tmp_path / 'unavail.docx')
    worker = MasterResumeExtractor(source)
    orig_read = zipfile.ZipFile.read
    state = {'in_numbering': False}

    def mock_read(archive_self, name, *args, **kwargs):
        if state['in_numbering'] and (name == 'word/numbering.xml' or name.endswith('/numbering.xml')):
            raise zipfile.BadZipFile('corrupted numbering part')
        return orig_read(archive_self, name, *args, **kwargs)

    orig_load = worker._load_numbering_definitions

    def wrapped_load():
        state['in_numbering'] = True
        try:
            return orig_load()
        finally:
            state['in_numbering'] = False

    worker._load_numbering_definitions = wrapped_load
    with unittest.mock.patch.object(zipfile.ZipFile, 'read', mock_read):
        snapshot = worker.extract()
    assert 'word/numbering.xml could not be read' in snapshot.unresolved_extraction_details
    assert evidence.build_evidence_catalog(snapshot).extraction_completeness.complete is False


def test_fields_inside_table_cells_are_captured_with_instructions(tmp_path):
    document = _document()
    table = document.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)
    _simple_field(cell.paragraphs[0], 'FILENAME')
    _, snapshot, catalog = _extract(document, tmp_path / 'tbl_field.docx')
    cell_paragraph = snapshot.blocks[1].table.rows[0][0].paragraphs[0]
    assert cell_paragraph.field == 'FILENAME'
    assert cell_paragraph.field_instructions == ['FILENAME']
    assert any('fldSimple' in gap.tag for gap in snapshot.unsupported_content)


def test_legacy_paragraph_properties_and_formatting_metadata_are_preserved(tmp_path):
    document = Document()
    document.add_heading('1. Technical Profile', level=1)
    paragraph = document.add_paragraph()
    paragraph.alignment = 1
    run1 = paragraph.add_run('Bold Run ')
    run1.bold = True
    run2 = paragraph.add_run('Italic Run')
    run2.italic = True
    _, snapshot, catalog = _extract(document, tmp_path / 'legacy_para.docx')
    block = snapshot.blocks[1].paragraph
    assert block.bold is True
    assert '1' in str(block.alignment) or 'CENTER' in str(block.alignment)
    assert len(block.runs) == 2
    assert block.runs[0].bold is True
    assert block.runs[1].italic is True


def test_deeply_nested_tables_accumulate_all_statistics_recursively(tmp_path):
    document = _document()
    root_table = document.add_table(rows=1, cols=1)
    root_cell = root_table.cell(0, 0)
    root_cell.paragraphs[0].text = 'Root cell paragraph'
    builders.append_hyperlink(root_cell.paragraphs[0], 'Link 1', anchor='root_anchor')
    nested_1 = root_cell.add_table(rows=1, cols=1)
    nested_1_cell = nested_1.cell(0, 0)
    nested_1_cell.paragraphs[0].text = 'Nested 1 paragraph'
    nested_2 = nested_1_cell.add_table(rows=1, cols=1)
    nested_2_cell = nested_2.cell(0, 0)
    nested_2_cell.paragraphs[0].text = 'Nested 2 paragraph'
    nested_2_cell.add_paragraph('')
    _, snapshot, catalog = _extract(document, tmp_path / 'deep_tables.docx')
    stats = snapshot.statistics
    assert stats.table_count == 3
    assert stats.row_count == 3
    assert stats.cell_count == 3
    assert stats.table_paragraph_count == 6
    assert stats.hyperlink_count == 1
    assert stats.empty_paragraph_count == 3


def test_media_counters_in_body_and_headers_are_accurately_recorded(tmp_path):
    document = _document()
    p = document.add_paragraph('Body with media')
    drawing = OxmlElement('w:drawing')
    p._p.append(drawing)
    textbox = OxmlElement('w:txbxContent')
    p._p.append(textbox)
    obj = OxmlElement('w:object')
    p._p.append(obj)
    blip = etree.Element('{http://schemas.openxmlformats.org/drawingml/2006/main}blip')
    p._p.append(blip)
    section = document.sections[0]
    header = section.header
    header_p = header.paragraphs[0]
    header_p.text = 'Header media'
    header_p._p.append(OxmlElement('w:drawing'))
    header_p._p.append(etree.Element('{urn:schemas-microsoft-com:vml}imagedata'))
    _, snapshot, catalog = _extract(document, tmp_path / 'media.docx')
    stats = snapshot.statistics
    assert stats.drawing_count == 2
    assert stats.textbox_count == 1
    assert stats.embedded_object_count == 1
    assert stats.image_count == 2


def test_empty_header_and_footer_handling(tmp_path):
    document = _document()
    section = document.sections[0]
    header = section.header
    header.paragraphs[0].text = ''
    footer = section.footer
    footer.paragraphs[0].text = '   '
    _, snapshot, catalog = _extract(document, tmp_path / 'empty_hf.docx')
    stats = snapshot.statistics
    assert stats.header_paragraph_count == 1
    assert stats.footer_paragraph_count == 1
    assert stats.empty_paragraph_count >= 2
