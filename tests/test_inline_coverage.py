'''Independent unsupported-container and located-diagnostic regressions.'''

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from lxml import etree

from resume_assistant import cli, evidence, extractor
from resume_assistant.models import CandidateEvidenceCatalog, DocumentSnapshot
from resume_assistant.publication_reader import load_committed_pair


def _text_run(text):
    run = OxmlElement('w:r')
    word = OxmlElement('w:t')
    word.text = text
    run.append(word)
    return run


@pytest.mark.parametrize('container', ['moveTo', 'moveFrom', 'dir', 'bdo', 'ruby'])
@pytest.mark.parametrize('location', ['body', 'cell', 'header'])
def test_each_known_unsupported_container_is_located_and_prevents_complete_preservation(
    tmp_path, container, location,
):
    document = Document()
    document.add_heading('1. Technical Profile', level=1)
    document.add_paragraph('SYNTHETIC_SUPPORTED_TEXT')
    if location == 'body':
        paragraph = document.add_paragraph()
    elif location == 'cell':
        paragraph = document.add_table(rows=1, cols=1).cell(0, 0).paragraphs[0]
    else:
        paragraph = document.sections[0].header.paragraphs[0]
    wrapper = OxmlElement('w:' + container)
    if container in {'moveTo', 'moveFrom'}:
        wrapper.set(qn('w:id'), '1')
        wrapper.set(qn('w:author'), 'Synthetic reviewer')
    if container in {'dir', 'bdo'}:
        wrapper.set(qn('w:val'), 'ltr')
    marker = f'SYNTHETIC_UNEXTRACTED_{container}_{location}'
    if container == 'ruby':
        wrapper.append(OxmlElement('w:rubyPr'))
        annotation = OxmlElement('w:rt')
        annotation.append(_text_run('Synthetic pronunciation annotation'))
        base = OxmlElement('w:rubyBase')
        base.append(_text_run(marker))
        wrapper.append(annotation)
        wrapper.append(base)
    else:
        wrapper.append(_text_run(marker))
    paragraph._p.append(wrapper)
    source = tmp_path / 'inline.docx'
    document.save(source)
    before = source.read_bytes()
    snapshot = extractor.MasterResumeExtractor(source).extract()
    catalog = evidence.build_evidence_catalog(snapshot)
    gaps = [gap for gap in snapshot.unsupported_content if gap.tag == qn('w:' + container)]
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.extracted is False
    assert gap.reason == 'unsupported_structure'
    assert gap.parent_path.endswith(f'{container}[1]')
    assert '/p[' in gap.parent_path
    assert gap.source_location is not None
    if location == 'header':
        assert gap.source_location.document_part.startswith('word/header')
        assert gap.source_location.paragraph_index == 0
        assert gap.source_location.body_block_index is None
    else:
        assert gap.source_location.document_part == 'word/document.xml'
        assert gap.source_location.body_block_index == 2
        if location == 'cell':
            assert gap.source_location.table_index == 2
            assert gap.source_location.row_index == gap.source_location.cell_index == 0
            assert gap.source_location.paragraph_position_in_cell == 0
        else:
            assert gap.source_location.paragraph_index == 2
    assert catalog.extraction_completeness.coverage_assessed is True
    assert catalog.extraction_completeness.section_classification_complete is True
    assert catalog.extraction_completeness.source_preservation_complete is False
    assert catalog.extraction_completeness.complete is False
    assert not any(marker in record.original_content for record in catalog.evidence_records)
    restored_snapshot = DocumentSnapshot.model_validate_json(snapshot.model_dump_json())
    restored_catalog = CandidateEvidenceCatalog.model_validate_json(catalog.model_dump_json())
    assert restored_snapshot.unsupported_content == restored_catalog.extraction_completeness.unsupported_content
    assert evidence.build_evidence_catalog(restored_snapshot) == restored_catalog
    assert source.read_bytes() == before


@pytest.mark.parametrize('container', ['contentPart', 'subDoc'])
def test_unhandled_inline_part_references_with_authored_content_are_explicit_gaps(tmp_path, container):
    from docx.opc.packuri import PackURI
    from docx.opc.part import Part

    document = Document()
    document.add_heading('1. Technical Profile', level=1)
    paragraph = document.add_paragraph('SYNTHETIC_SUPPORTED_TEXT')
    part = Part(
        PackURI('/word/synthetic-content.xml'), 'application/xml',
        b'<syntheticContent>SYNTHETIC_UNEXTRACTED_REFERENCED_CONTENT</syntheticContent>',
        document.part.package,
    )
    relationship_type = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/subDocument'
    rid = document.part.relate_to(part, relationship_type)
    reference = OxmlElement('w:' + container, attrs={qn('r:id'): rid})
    paragraph._p.append(reference)
    source = tmp_path / 'referenced-content.docx'
    document.save(source)
    snapshot = extractor.MasterResumeExtractor(source).extract()
    catalog = evidence.build_evidence_catalog(snapshot)
    gap = next(gap for gap in snapshot.unsupported_content if gap.tag == qn('w:' + container))
    assert gap.source_location.paragraph_index == 1
    assert gap.parent_path.endswith(f'{container}[1]')
    assert any(gap.source_location.document_part == 'word/synthetic-content.xml' for gap in snapshot.unsupported_content)
    assert catalog.extraction_completeness.complete is False
    assert not any('SYNTHETIC_UNEXTRACTED_REFERENCED_CONTENT' in record.original_content for record in catalog.evidence_records)


@pytest.mark.parametrize('content', ['sym', 'softHyphen', 'delText', 'delInstrText'])
def test_unhandled_run_content_is_diagnosed_without_claiming_symbol_or_instruction_interpretation(tmp_path, content):
    document = Document()
    document.add_heading('1. Technical Profile', level=1)
    paragraph = document.add_paragraph('SYNTHETIC_SUPPORTED_TEXT')
    run = OxmlElement('w:r')
    element = OxmlElement('w:' + content)
    if content == 'sym':
        element.set(qn('w:font'), 'Symbol')
        element.set(qn('w:char'), '0041')
    else:
        element.text = 'SYNTHETIC_UNHANDLED_RUN_CONTENT'
    run.append(element)
    paragraph._p.append(run)
    source = tmp_path / 'run.docx'
    document.save(source)
    snapshot = extractor.MasterResumeExtractor(source).extract()
    catalog = evidence.build_evidence_catalog(snapshot)
    gap = next(gap for gap in snapshot.unsupported_content if gap.tag == qn('w:' + content))
    assert gap.source_location.body_block_index == gap.source_location.paragraph_index == 1
    assert gap.parent_path.endswith(f'{content}[1]')
    assert gap.reason == 'unsupported_structure'
    assert catalog.extraction_completeness.source_preservation_complete is False
    assert catalog.extraction_completeness.complete is False


@pytest.mark.parametrize('owner', ['paragraph', 'run', 'hyperlink'])
def test_unknown_inline_extensions_with_text_are_conservatively_reported(tmp_path, owner):
    document = Document()
    document.add_heading('1. Technical Profile', level=1)
    paragraph = document.add_paragraph()
    if owner == 'paragraph':
        parent = paragraph._p
    elif owner == 'run':
        parent = paragraph.add_run()._r
    else:
        parent = OxmlElement('w:hyperlink')
        paragraph._p.append(parent)
    extension = etree.Element('{urn:synthetic:extension}authoredContent')
    extension.append(_text_run('SYNTHETIC_FUTURE_EXTENSION_CONTENT'))
    parent.append(extension)
    source = tmp_path / 'extension.docx'
    document.save(source)
    snapshot = extractor.MasterResumeExtractor(source).extract()
    gap = next(gap for gap in snapshot.unsupported_content if gap.tag == extension.tag)
    assert gap.source_location.paragraph_index == 1
    assert gap.parent_path.endswith('authoredContent[1]')
    assert evidence.build_evidence_catalog(snapshot).extraction_completeness.complete is False


def test_supported_run_controls_do_not_create_false_coverage_gaps(tmp_path):
    document = Document()
    document.add_heading('1. Technical Profile', level=1)
    paragraph = document.add_paragraph()
    run = paragraph.add_run('before')
    run._r.append(OxmlElement('w:noBreakHyphen'))
    run._r.append(OxmlElement('w:ptab'))
    run.add_text('middle')
    run.add_break()
    run.add_text('after')
    source = tmp_path / 'controls.docx'
    document.save(source)
    snapshot = extractor.MasterResumeExtractor(source).extract()
    assert snapshot.blocks[1].paragraph.text == 'before-\tmiddle\nafter'
    assert snapshot.unsupported_content == []
    assert evidence.build_evidence_catalog(snapshot).extraction_completeness.complete is True


def test_located_coverage_details_persist_in_both_real_cli_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'PROJECT_ROOT', tmp_path)
    document = Document()
    document.add_heading('1. Technical Profile', level=1)
    paragraph = document.add_paragraph()
    container = OxmlElement('w:moveTo', attrs={qn('w:id'): '1', qn('w:author'): 'Synthetic reviewer'})
    container.append(_text_run('SYNTHETIC_UNEXTRACTED_MOVED_TEXT'))
    paragraph._p.append(container)
    source = tmp_path / 'persist.docx'
    document.save(source)
    catalog_path = tmp_path / 'data/private/processed/evidence.json'
    snapshot_path = tmp_path / 'data/private/processed/snapshot.json'
    cli.run_extract_master(source, catalog_path, snapshot_path)
    restored_snapshot, restored_catalog, _, _ = load_committed_pair(
        cli.PROJECT_ROOT, catalog_path
    )
    assert restored_snapshot.unsupported_content == restored_catalog.extraction_completeness.unsupported_content
    assert restored_snapshot.unsupported_content[0].source_location.paragraph_index == 1
    assert restored_catalog.extraction_completeness.complete is False


@pytest.mark.parametrize('detail', ['hyperlink', 'numbering'])
def test_unresolved_lists_are_reset_between_changed_and_clean_source_calls(tmp_path, detail):
    from tests.fixtures import builders

    source = builders.build_completeness_document(tmp_path / 'reuse.docx', unresolved=detail)
    worker = extractor.MasterResumeExtractor(source)
    initial = worker.extract()
    assert initial.unresolved_extraction_details
    builders.build_completeness_document(source)
    clean = worker.extract()
    assert worker.unresolved_hyperlinks == worker.unresolved_numbering_details == []
    assert clean.unresolved_extraction_details == []
    assert evidence.build_evidence_catalog(clean).extraction_completeness.complete is True


def test_failed_capture_also_clears_old_unresolved_lists(tmp_path):
    from tests.fixtures import builders

    source = builders.build_completeness_document(tmp_path / 'removed.docx', unresolved='hyperlink')
    worker = extractor.MasterResumeExtractor(source)
    assert worker.extract().unresolved_extraction_details
    source.unlink()
    with pytest.raises(FileNotFoundError):
        worker.extract()
    assert worker.unresolved_hyperlinks == worker.unresolved_numbering_details == []
    assert worker.captured_source is None
