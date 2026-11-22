'''Heuristic, source-faithful section detection for the Master Resume.'''

import re
from typing import List

from resume_assistant.models import (
    DocumentSection,
    DocumentSnapshot,
    DocumentClassification,
)

TOP_LEVEL_NUMBER_PATTERN = re.compile(r'^\s*(\d+)\.\s+(.+)$')
SUB_NUMBER_PATTERN = re.compile(r'^\s*(\d+)\.(\d+)\.\s+(.+)$')

CONFIDENCE_THRESHOLD = 0.30
OBSERVED_TOP_LEVEL_SECTION_COUNT = 14


def _identifier_for(heading_text: str) -> str:
    slug = ''.join(
        character.lower() if character.isalnum() else '-'
        for character in heading_text.strip()
    )
    slug = slug.strip('-')
    return slug


class SectionDetector:
    '''Detects top-level resume sections from ordered paragraph blocks.'''

    def detect(self, snapshot: DocumentSnapshot) -> List[DocumentSection]:
        sections: List[DocumentSection] = []
        current = None
        last_block_index = None

        for block in snapshot.blocks:
            last_block_index = block.index
            if block.type != 'paragraph' or block.paragraph is None:
                continue
            paragraph_block = block.paragraph
            score = self._heading_score(paragraph_block)
            if score < CONFIDENCE_THRESHOLD:
                continue
            sub_match = SUB_NUMBER_PATTERN.match(paragraph_block.text or '')
            top_match = TOP_LEVEL_NUMBER_PATTERN.match(paragraph_block.text or '')
            if sub_match is not None:
                continue
            style_heading = (
                paragraph_block.style is not None
                and paragraph_block.style.lower().startswith('heading')
            )
            is_strong_top_level = (
                paragraph_block.bold is True
                and paragraph_block.font_size_pt is not None
                and paragraph_block.font_size_pt >= 14
            )
            is_top_level_heading = style_heading or (
                top_match is not None
                and paragraph_block.bold is True
                and paragraph_block.font_size_pt is not None
                and paragraph_block.font_size_pt >= 12
            )
            if not is_top_level_heading:
                continue
            identifier = _identifier_for(top_match.group(2) if top_match else paragraph_block.text)
            section = DocumentSection(
                identifier=identifier,
                heading_text=(top_match.group(2) if top_match else paragraph_block.text).strip(),
                numbering=(top_match.group(1) + '.') if top_match else None,
                start_block_index=block.index,
                end_block_index=None,
                confidence=float(min(score, 1.0)),
                classification_status=DocumentClassification.DETECTED,
            )
            if current is not None:
                current.end_block_index = block.index - 1
            sections.append(section)
            current = section

        if current is not None and last_block_index is not None:
            current.end_block_index = last_block_index
        snapshot.sections = sections
        return sections

    def find_unclassified(self, snapshot: DocumentSnapshot) -> List[int]:
        if not snapshot.blocks:
            return []
        first_start = snapshot.sections[0].start_block_index if snapshot.sections else None
        unclassified: List[int] = []
        for block in snapshot.blocks:
            if first_start is None or block.index < first_start:
                unclassified.append(block.index)
        return unclassified

    @staticmethod
    def _heading_score(paragraph_block) -> float:
        score = 0.0
        style = paragraph_block.style or ''
        if style.lower().startswith('heading'):
            score += 0.40
        if (
            paragraph_block.bold is True
            and paragraph_block.font_size_pt is not None
            and paragraph_block.font_size_pt >= 12
        ):
            score += 0.25
        top_match = TOP_LEVEL_NUMBER_PATTERN.match(paragraph_block.text or '')
        sub_match = SUB_NUMBER_PATTERN.match(paragraph_block.text or '')
        if top_match is not None:
            score += 0.30
        elif sub_match is not None:
            score += 0.20
        return score
