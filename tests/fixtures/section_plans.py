'''Historical artifact fixtures projected only from synthetic current plans.'''

from resume_assistant.legacy_section_plan import (
    LegacyPlanningMetadata, LegacyRequirementCoverage, LegacyResumeSectionPlan,
    LegacySummaryBlueprint,
)


def legacy_plan_payload(plan):
    current = plan.model_dump(mode='json')
    value = {key: current[key] for key in LegacyResumeSectionPlan.model_fields if key in current}
    value['schema_version'] = '1.0.0'
    value['planning_metadata'] = {
        key: current['planning_metadata'][key]
        for key in LegacyPlanningMetadata.model_fields if key in current['planning_metadata']
    }
    value['planning_metadata']['schema_version'] = '1.0.0'
    value['planning_metadata']['planner_version'] = '1.0.0'
    value['requirement_coverage'] = [
        {key: item[key] for key in LegacyRequirementCoverage.model_fields if key in item}
        for item in current['requirement_coverage']
    ]
    value['professional_summary_blueprint'] = {
        key: current['professional_summary_blueprint'][key]
        for key in LegacySummaryBlueprint.model_fields
    }
    return value
