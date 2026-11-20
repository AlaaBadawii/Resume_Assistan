'''Actual header/footer relationships, linkage, ownership, and coverage tests.'''

from copy import deepcopy
from io import BytesIO
from unittest.mock import Mock
import zipfile

import pytest
from docx import Document
from docx.enum.section import WD_SECTION_START
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches

from resume_assistant import cli, evidence, extractor
from resume_assistant.models import CandidateEvidenceCatalog, DocumentSnapshot


def _document():
    document = Document()
    document.add_heading('1. Technical Profile', level=1)
    document.add_paragraph('Python development')
    return document


def _save_extract(document, tmp_path, name='parts.docx'):
    source = tmp_path / name
    document.save(source)
    before = source.read_bytes()
    snapshot = extractor.MasterResumeExtractor(source).extract()
    assert source.read_bytes() == before
    return snapshot, evidence.build_evidence_catalog(snapshot)


def _container(document, kind, variant='default', section_index=0):
    section = document.sections[section_index]
    if variant == 'default':
        return section.header if kind == 'header' else section.footer
    if variant == 'first':
        section.different_first_page_header_footer = True
        return section.first_page_header if kind == 'header' else section.first_page_footer
    document.settings.odd_and_even_pages_header_footer = True
    return section.even_page_header if kind == 'header' else section.even_page_footer


def test_absent_header_footer_references_do_not_create_phantom_parts(tmp_path, monkeypatch):
    document = _document()
    real_parse = extractor.Document
    parsed_parts = []
    parsed_documents = []

    def parse(stream):
        loaded = real_parse(stream)
        parsed_parts.append({str(part.partname) for part in loaded.part.package.parts})
        parsed_documents.append(loaded)
        return loaded

    monkeypatch.setattr(extractor, 'Document', parse)
    snapshot, catalog = _save_extract(document, tmp_path)
    assert snapshot.headers == snapshot.footers == []
    assert snapshot.header_footer_parts == snapshot.header_footer_references == []
    assert snapshot.statistics.header_paragraph_count == snapshot.statistics.footer_paragraph_count == 0
    assert {str(part.partname) for part in parsed_documents[0].part.package.parts} == parsed_parts[0]
    assert catalog.extraction_completeness.complete is True


@pytest.mark.parametrize('kind', ['header', 'footer'])
def test_default_parts_preserve_within_part_order_and_exact_ownership(tmp_path, kind):
    document = _document()
    container = _container(document, kind)
    container.paragraphs[0].text = 'SYNTHETIC_FIRST_PARAGRAPH'
    container.add_paragraph('SYNTHETIC_SECOND_PARAGRAPH')
    part_name = str(container.part.partname).lstrip('/')
    snapshot, catalog = _save_extract(document, tmp_path)
    part = snapshot.header_footer_parts[0]
    assert part.document_part == part_name and part.kind == kind
    assert part.source_document == snapshot.source_filename
    assert part.source_sha256 == snapshot.source_sha256
    assert part.extraction_status == 'extracted'
    assert [block.paragraph.text for block in part.blocks] == ['SYNTHETIC_FIRST_PARAGRAPH', 'SYNTHETIC_SECOND_PARAGRAPH']
    assert [block.source_location.paragraph_index for block in part.blocks] == [0, 1]
    assert all(block.source_location.document_part == part_name for block in part.blocks)
    assert all(block.source_location.body_block_index is None for block in part.blocks)
    assert catalog.extraction_completeness.complete is True
    assert not any('SYNTHETIC_FIRST_PARAGRAPH' in record.original_content for record in catalog.evidence_records)


def test_linked_parts_across_three_sections_are_extracted_once_with_effective_references(tmp_path):
    document = _document()
    document.sections[0].header.paragraphs[0].text = 'SYNTHETIC_HEADER_ONCE'
    document.sections[0].footer.paragraphs[0].text = 'SYNTHETIC_FOOTER_ONCE'
    document.add_section(WD_SECTION_START.NEW_PAGE)
    document.add_section(WD_SECTION_START.NEW_PAGE)
    snapshot, catalog = _save_extract(document, tmp_path)
    assert len(snapshot.headers) == len(snapshot.footers) == 1
    assert len(snapshot.header_footer_parts) == 2
    assert snapshot.statistics.header_paragraph_count == snapshot.statistics.footer_paragraph_count == 1
    references = snapshot.header_footer_references
    assert len(references) == 6
    for section_index in range(3):
        section_refs = [reference for reference in references if reference.section_index == section_index]
        assert {reference.kind for reference in section_refs} == {'header', 'footer'}
        assert all(reference.inherited is (section_index != 0) for reference in section_refs)
        assert all(reference.source_section_index == 0 for reference in section_refs)
        assert all(reference.resolution_status == 'resolved' for reference in section_refs)
    assert catalog.extraction_completeness.complete is True


@pytest.mark.parametrize('kind', ['header', 'footer'])
@pytest.mark.parametrize('variant', ['first', 'even'])
def test_nondefault_variants_are_resolved_located_and_explicitly_unsupported(tmp_path, kind, variant):
    document = _document()
    container = _container(document, kind, variant)
    container.paragraphs[0].text = 'SYNTHETIC_NONDEFAULT_CONTENT'
    part_name = str(container.part.partname).lstrip('/')
    snapshot, catalog = _save_extract(document, tmp_path)
    assert snapshot.headers == snapshot.footers == []
    reference = snapshot.header_footer_references[0]
    assert (reference.kind, reference.variant, reference.document_part) == (kind, variant, part_name)
    assert reference.resolution_status == 'resolved'
    part = snapshot.header_footer_parts[0]
    assert part.extraction_status == 'unsupported' and part.blocks == []
    assert any(gap.source_location.document_part == part_name for gap in snapshot.unsupported_content)
    assert snapshot.coverage_assessed is True
    assert catalog.extraction_completeness.complete is False


@pytest.mark.parametrize('kind', ['header', 'footer'])
@pytest.mark.parametrize('variant', ['default', 'first', 'even'])
def test_broken_references_are_not_confused_with_absent_parts(tmp_path, kind, variant):
    document = _document()
    reference = OxmlElement('w:' + kind + 'Reference', attrs={
        qn('w:type'): variant, qn('r:id'): 'rIdSyntheticMissing',
    })
    document.sections[0]._sectPr.insert(0, reference)
    snapshot, catalog = _save_extract(document, tmp_path)
    assert snapshot.headers == snapshot.footers == snapshot.header_footer_parts == []
    persisted = snapshot.header_footer_references[0]
    assert persisted.kind == kind and persisted.variant == variant
    assert persisted.relationship_id == 'rIdSyntheticMissing'
    assert persisted.resolution_status == 'unresolved' and persisted.document_part is None
    gap = next(gap for gap in snapshot.unsupported_content if gap.reason == 'unresolved_reference')
    assert gap.parent_path.endswith(f'{kind}Reference[1]')
    assert gap.source_location.document_part == 'word/document.xml'
    assert snapshot.coverage_assessed is False
    assert catalog.extraction_completeness.source_preservation_complete is False
    assert catalog.extraction_completeness.section_classification_complete is True
    assert catalog.extraction_completeness.complete is False


@pytest.mark.parametrize('kind', ['header', 'footer'])
@pytest.mark.parametrize('variant', ['default', 'first', 'even'])
def test_header_footer_tables_have_explicit_owning_part_table_diagnostics(tmp_path, kind, variant):
    document = _document()
    container = _container(document, kind, variant)
    container.add_table(rows=1, cols=1, width=Inches(2)).cell(0, 0).text = 'SYNTHETIC_UNEXTRACTED_TABLE'
    part_name = str(container.part.partname).lstrip('/')
    snapshot, catalog = _save_extract(document, tmp_path)
    gap = next(gap for gap in snapshot.unsupported_content if gap.tag == qn('w:tbl'))
    assert gap.parent_path == part_name + '/tbl[1]'
    assert gap.source_location.document_part == part_name
    assert gap.source_location.table_index == 0
    assert gap.source_location.body_block_index is None
    assert catalog.extraction_completeness.complete is False
    assert not any('SYNTHETIC_UNEXTRACTED_TABLE' in record.original_content for record in catalog.evidence_records)
    assert snapshot.header_footer_parts[0].extraction_status == ('partial' if variant == 'default' else 'unsupported')


@pytest.mark.parametrize('kind', ['header', 'footer'])
def test_duplicate_references_to_one_physical_part_do_not_duplicate_extraction(tmp_path, kind):
    document = _document()
    container = _container(document, kind)
    container.paragraphs[0].text = 'SYNTHETIC_SHARED_PART'
    part_name = str(container.part.partname).lstrip('/')
    element = next(element for element in document.sections[0]._sectPr if element.tag == qn('w:' + kind + 'Reference'))
    document.sections[0]._sectPr.append(deepcopy(element))
    snapshot, catalog = _save_extract(document, tmp_path)
    assert len(snapshot.header_footer_parts) == 1
    assert len(snapshot.header_footer_references) == 2
    assert {reference.document_part for reference in snapshot.header_footer_references} == {part_name}
    assert len({reference.source_path for reference in snapshot.header_footer_references}) == 2
    projection = snapshot.headers if kind == 'header' else snapshot.footers
    assert len(projection) == 1
    assert catalog.extraction_completeness.complete is True


def test_default_and_first_references_sharing_a_part_preserve_both_owners_without_duplicate_text(tmp_path):
    document = _document()
    header = document.sections[0].header
    header.paragraphs[0].text = 'SYNTHETIC_DEFAULT_AND_FIRST'
    original = next(element for element in document.sections[0]._sectPr if element.tag == qn('w:headerReference'))
    first = deepcopy(original)
    first.set(qn('w:type'), 'first')
    document.sections[0]._sectPr.append(first)
    snapshot, catalog = _save_extract(document, tmp_path)
    assert len(snapshot.header_footer_parts) == len(snapshot.headers) == 1
    assert {reference.variant for reference in snapshot.header_footer_references} == {'default', 'first'}
    assert snapshot.header_footer_parts[0].extraction_status == 'extracted'
    assert catalog.extraction_completeness.complete is True


@pytest.mark.parametrize('kind', ['header', 'footer'])
def test_referenced_empty_parts_are_retained_without_creating_other_parts(tmp_path, kind):
    document = _document()
    _container(document, kind).paragraphs[0].text = ''
    snapshot, catalog = _save_extract(document, tmp_path)
    assert len(snapshot.header_footer_parts) == 1
    projection = snapshot.headers if kind == 'header' else snapshot.footers
    other = snapshot.footers if kind == 'header' else snapshot.headers
    assert len(projection) == 1 and projection[0].paragraph.text == ''
    assert other == []
    assert snapshot.header_footer_parts[0].extraction_status == 'extracted'
    assert catalog.extraction_completeness.complete is True


def test_unreferenced_but_reachable_part_is_diagnosed_without_guessing_a_section_owner(tmp_path):
    document = _document()
    header = document.sections[0].header
    header.paragraphs[0].text = 'SYNTHETIC_UNREFERENCED_PART'
    reference = next(element for element in document.sections[0]._sectPr if element.tag == qn('w:headerReference'))
    document.sections[0]._sectPr.remove(reference)
    snapshot, catalog = _save_extract(document, tmp_path)
    assert snapshot.header_footer_references == []
    assert snapshot.headers == []
    assert len(snapshot.header_footer_parts) == 1
    assert snapshot.header_footer_parts[0].extraction_status == 'unsupported'
    assert catalog.extraction_completeness.complete is False


@pytest.mark.parametrize('failure', ['external', 'wrong_kind', 'wrong_content_type', 'missing_id', 'unknown_variant'])
def test_invalid_reference_relationships_are_reported_without_dereferencing_external_targets(tmp_path, failure):
    document = _document()
    attrs = {qn('w:type'): 'default'}
    if failure == 'external':
        rid = document.part.relate_to('https://synthetic.invalid/header.xml', RT.HEADER, is_external=True)
    elif failure == 'wrong_kind':
        footer = document.sections[0].footer
        footer.paragraphs[0].text = 'SYNTHETIC_FOOTER'
        rid = next(element.get(qn('r:id')) for element in document.sections[0]._sectPr if element.tag == qn('w:footerReference'))
    elif failure == 'wrong_content_type':
        rid = document.part.relate_to(document.part.part_related_by(RT.STYLES), RT.HEADER)
    elif failure == 'unknown_variant':
        header = document.sections[0].header
        header.paragraphs[0].text = 'SYNTHETIC_HEADER'
        original = next(element for element in document.sections[0]._sectPr if element.tag == qn('w:headerReference'))
        rid = original.get(qn('r:id'))
        document.sections[0]._sectPr.remove(original)
        attrs[qn('w:type')] = 'SYNTHETIC_UNSUPPORTED_VARIANT'
    else:
        rid = None
    if rid is not None:
        attrs[qn('r:id')] = rid
    document.sections[0]._sectPr.append(OxmlElement('w:headerReference', attrs=attrs))
    snapshot, catalog = _save_extract(document, tmp_path)
    assert any(reference.resolution_status == 'unresolved' for reference in snapshot.header_footer_references)
    assert any(gap.reason == 'unresolved_reference' for gap in snapshot.unsupported_content)
    assert snapshot.coverage_assessed is False
    assert catalog.extraction_completeness.complete is False


def test_unavailable_owning_part_inventory_is_distinct_from_known_unsupported_content(tmp_path, monkeypatch):
    document = _document()
    document.sections[0].header.paragraphs[0].text = 'SYNTHETIC_HEADER'
    real_parse = extractor.Document

    def parse(stream):
        loaded = real_parse(stream)
        relationship = next(rel for rel in loaded.part.rels.values() if rel.reltype == RT.HEADER)
        relationship.target_part._element = None
        return loaded

    monkeypatch.setattr(extractor, 'Document', parse)
    snapshot, catalog = _save_extract(document, tmp_path)
    assert snapshot.coverage_assessed is False
    assert any(gap.reason == 'inventory_unavailable' for gap in snapshot.unsupported_content)
    assert snapshot.header_footer_parts[0].extraction_status == 'inventory_unavailable'
    assert snapshot.headers == []
    assert catalog.extraction_completeness.complete is False


def test_ownership_and_diagnostics_survive_snapshot_and_catalog_reconstruction(tmp_path):
    document = _document()
    document.sections[0].header.paragraphs[0].text = 'SYNTHETIC_DEFAULT'
    _container(document, 'footer', 'first').paragraphs[0].text = 'SYNTHETIC_FIRST'
    snapshot, catalog = _save_extract(document, tmp_path)
    restored = DocumentSnapshot.model_validate_json(snapshot.model_dump_json())
    restored_catalog = CandidateEvidenceCatalog.model_validate_json(catalog.model_dump_json())
    assert restored.header_footer_parts == snapshot.header_footer_parts
    assert restored.header_footer_references == snapshot.header_footer_references
    assert restored.unsupported_content == restored_catalog.extraction_completeness.unsupported_content
    assert evidence.build_evidence_catalog(restored) == restored_catalog


@pytest.mark.parametrize('kind', ['header', 'footer'])
@pytest.mark.parametrize('variant', ['first', 'even'])
def test_nondefault_linkage_is_retained_across_sections_without_duplicate_part_inventory(tmp_path, kind, variant):
    document = _document()
    container = _container(document, kind, variant)
    container.paragraphs[0].text = 'SYNTHETIC_LINKED_NONDEFAULT'
    part_name = str(container.part.partname).lstrip('/')
    document.add_section(WD_SECTION_START.NEW_PAGE)
    document.add_section(WD_SECTION_START.NEW_PAGE)
    snapshot, catalog = _save_extract(document, tmp_path)
    assert len(snapshot.header_footer_parts) == 1
    references = snapshot.header_footer_references
    assert len(references) == 3
    assert [reference.section_index for reference in references] == [0, 1, 2]
    assert all(reference.source_section_index == 0 for reference in references)
    assert all(reference.document_part == part_name for reference in references)
    assert [reference.inherited for reference in references] == [False, True, True]
    assert snapshot.header_footer_parts[0].extraction_status == 'unsupported'
    assert sum(gap.tag == 'document_part' and gap.source_location.document_part == part_name
               for gap in snapshot.unsupported_content) == 1
    assert catalog.extraction_completeness.complete is False


@pytest.mark.parametrize('kind', ['header', 'footer'])
def test_distinct_relationship_ids_pointing_to_one_part_preserve_refs_and_deduplicate_content(tmp_path, kind):
    document = _document()
    container = _container(document, kind)
    container.paragraphs[0].text = 'SYNTHETIC_PHYSICAL_PART'
    original = next(element for element in document.sections[0]._sectPr if element.tag == qn('w:' + kind + 'Reference'))
    reltype = RT.HEADER if kind == 'header' else RT.FOOTER
    document.part.rels.add_relationship(reltype, container.part, 'rIdSyntheticAlias', is_external=False)
    alias = deepcopy(original)
    alias.set(qn('r:id'), 'rIdSyntheticAlias')
    document.sections[0]._sectPr.append(alias)
    snapshot, catalog = _save_extract(document, tmp_path)
    assert len(snapshot.header_footer_parts) == 1
    assert len(snapshot.headers if kind == 'header' else snapshot.footers) == 1
    assert {reference.relationship_id for reference in snapshot.header_footer_references} == {
        original.get(qn('r:id')), 'rIdSyntheticAlias',
    }
    assert len({reference.document_part for reference in snapshot.header_footer_references}) == 1
    assert catalog.extraction_completeness.complete is True


def test_conflicting_default_references_are_located_without_guessing_which_content_wins(tmp_path):
    document = _document()
    document.sections[0].header.paragraphs[0].text = 'SYNTHETIC_HEADER_A'
    document.add_section(WD_SECTION_START.NEW_PAGE)
    second = document.sections[1].header
    second.is_linked_to_previous = False
    second.paragraphs[0].text = 'SYNTHETIC_HEADER_B'
    second_reference = next(element for element in document.sections[1]._sectPr if element.tag == qn('w:headerReference'))
    document.sections[0]._sectPr.append(deepcopy(second_reference))
    snapshot, catalog = _save_extract(document, tmp_path)
    assert len(snapshot.header_footer_parts) == 2
    assert {block.paragraph.text for block in snapshot.headers} == {'SYNTHETIC_HEADER_A', 'SYNTHETIC_HEADER_B'}
    assert any(gap.tag == 'header_footer_reference_conflict' for gap in snapshot.unsupported_content)
    assert snapshot.coverage_assessed is False
    assert catalog.extraction_completeness.complete is False


@pytest.mark.parametrize('kind', ['header', 'footer'])
@pytest.mark.parametrize('corruption', ['missing_part', 'malformed_part'])
def test_unloadable_referenced_package_parts_fail_closed_with_sanitized_cli_errors(
    tmp_path, monkeypatch, capsys, kind, corruption,
):
    monkeypatch.setattr(cli, 'PROJECT_ROOT', tmp_path)
    document = _document()
    container = _container(document, kind)
    container.paragraphs[0].text = 'SYNTHETIC_PRIVATE_PART_CONTENT'
    part_name = str(container.part.partname).lstrip('/')
    source = tmp_path / 'SYNTHETIC_PRIVATE_PATH.docx'
    document.save(source)
    output = BytesIO()
    with zipfile.ZipFile(source) as incoming, zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as outgoing:
        for item in incoming.infolist():
            if item.filename == part_name and corruption == 'missing_part':
                continue
            data = incoming.read(item.filename)
            if item.filename == part_name:
                data = b'<SYNTHETIC_PRIVATE_MALFORMED_PART'
            outgoing.writestr(item, data)
    source.write_bytes(output.getvalue())
    before = source.read_bytes()
    writer = Mock()
    monkeypatch.setattr(cli, '_atomic_write_json', writer)
    assert cli.main([
        'extract-master', '--master', str(source), '--output', str(tmp_path / 'data/private/processed/evidence.json'),
        '--snapshot-output', str(tmp_path / 'data/private/processed/snapshot.json'),
    ]) == 2
    captured = capsys.readouterr()
    assert 'SYNTHETIC_PRIVATE' not in captured.out + captured.err
    writer.assert_not_called()
    assert source.read_bytes() == before
