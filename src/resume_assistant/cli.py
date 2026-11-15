'''Minimal local CLI for the resume extraction layer.'''

import argparse
from pathlib import Path

from resume_assistant import evidence as evidence_module
from resume_assistant import extractor as extractor_module
from resume_assistant import models
from resume_assistant import section_detector as section_detector_module
from resume_assistant.extractor import InvalidMasterResumeError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MASTER_PATH = PROJECT_ROOT / 'data' / 'private' / 'Master_Resume.docx'
DEFAULT_OUTPUT_PATH = (
    PROJECT_ROOT / 'data' / 'private' / 'processed' / 'candidate_evidence.json'
)


def run_extract_master(master_path, output_path):
    master_path = Path(master_path)
    output_path = Path(output_path)
    extractor = extractor_module.MasterResumeExtractor(source=master_path)
    snapshot = extractor.extract()
    section_detector = section_detector_module.SectionDetector()
    sections = section_detector.detect(snapshot)
    unclassified = section_detector.find_unclassified(snapshot)
    catalog = evidence_module.build_evidence_catalog(snapshot, sections)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(catalog.model_dump_json(indent=2), encoding='utf-8')
    return catalog, snapshot, sections, unclassified, output_path


def _print_summary(catalog, snapshot, sections, unclassified, output_path) -> None:
    statistics = snapshot.statistics
    completeness = catalog.extraction_completeness
    print('Master Resume extraction summary')
    print(f'  source: {snapshot.source_filename}')
    print(f'  source_sha256: {snapshot.source_sha256}')
    print(f'  paragraphs: {statistics.paragraph_count}')
    print(
        f'  tables: {statistics.table_count} rows: {statistics.row_count} '
        f'cells: {statistics.cell_count}'
    )
    print(f'  hyperlinks: {statistics.hyperlink_count}')
    print(f'  detected_sections: {len(sections)}')
    print(f'  evidence_records: {len(catalog.evidence_records)}')
    print(f'  unclassified_blocks: {len(unclassified)}')
    print(f'  unsupported_content_items: {len(snapshot.unsupported_content)}')
    print(f'  complete: {completeness.complete}')
    print(f'  output: {output_path}')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog='resume_assistant',
        description='Local extraction layer for the Master Resume.',
    )
    subparsers = parser.add_subparsers(dest='command', required=True)
    extract_parser = subparsers.add_parser(
        'extract-master',
        help='Extract structured candidate evidence from the Master Resume.',
    )
    extract_parser.add_argument(
        '--master',
        default=str(DEFAULT_MASTER_PATH),
        help='Path to the Master Resume DOCX.',
    )
    extract_parser.add_argument(
        '--output',
        default=str(DEFAULT_OUTPUT_PATH),
        help='Path for the generated candidate_evidence.json artifact.',
    )
    args = parser.parse_args(argv)
    if args.command == 'extract-master':
        try:
            catalog, snapshot, sections, unclassified, output_path = run_extract_master(
                master_path=args.master,
                output_path=args.output,
            )
        except (FileNotFoundError, InvalidMasterResumeError) as error:
            print(f'error: {error}')
            return 2
        except Exception as error:  # noqa: BLE001
            print(f'error: extraction failed: {error}')
            return 3
        _print_summary(catalog, snapshot, sections, unclassified, output_path)
        return 0
    parser.print_help()
    return 2
