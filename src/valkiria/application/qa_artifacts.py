from __future__ import annotations

from typing import Any
from uuid import uuid4

MAX_TEST_CASES = 30
MAX_AUTOMATION_BATCH = 15
SUPPORTED_PERFORMANCE_TOOLS = {'jmeter', 'k6', 'locust'}
SUPPORTED_AUTOMATION_FRAMEWORKS = {
    'selenium', 'playwright', 'restassured', 'postman-newman',
    'appium', 'winium', 'sikulix',
}

class PolicyViolation(ValueError):
    pass

def build_test_matrix(story_id: str, criteria: list[dict[str, Any]]) -> dict[str, Any]:
    if not criteria:
        raise PolicyViolation('acceptance_criteria_required')
    cases = []
    for criterion in criteria:
        cid = str(criterion['id'])
        text = str(criterion['text'])
        for kind, suffix in [('positive', 'valid behavior'), ('negative', 'invalid behavior'), ('edge', 'boundary behavior')]:
            cases.append({'id': f'TC-{len(cases)+1:03d}', 'criterion_id': cid, 'scenario': f'{text} ({suffix})', 'preconditions': [], 'steps': [], 'data': {}, 'expected_result': text, 'priority': 'medium', 'type': kind})
    if len(cases) > MAX_TEST_CASES:
        raise PolicyViolation(f'test_matrix_requires_batches:max={MAX_TEST_CASES}:generated={len(cases)}')
    return {'id': str(uuid4()), 'story_id': story_id, 'cases': cases, 'status': 'draft'}

def assess_risk(story_id: str, *, complexity: int, dependencies: int, business_criticality: int, defect_history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    values = [complexity, dependencies, business_criticality]
    if any(value < 1 or value > 5 for value in values):
        raise PolicyViolation('risk_inputs_must_be_1_to_5')
    score = sum(values)
    level = 'high' if score >= 12 else 'medium' if score >= 7 else 'low'
    return {'id': str(uuid4()), 'story_id': story_id, 'level': level, 'score': score, 'justification': f'complexity={complexity}; dependencies={dependencies}; business_criticality={business_criticality}', 'mitigation': 'isolate dependencies, add focused regression coverage and require QA review' if level != 'low' else 'execute standard QA plan', 'defect_history_considered': bool(defect_history), 'execution_order': 1 if level == 'high' else 2 if level == 'medium' else 3}

def build_azure_work_item(story: dict[str, Any], *, project: str, field_mapping: dict[str, str], approval_id: str, existing_work_item_id: str | None = None) -> dict[str, Any]:
    if not approval_id:
        raise PolicyViolation('explicit_approval_required')
    if not project or not field_mapping:
        raise PolicyViolation('project_and_field_mapping_required')
    fields = {field_mapping.get('title', 'System.Title'): story['title'], field_mapping.get('description', 'System.Description'): story['description']}
    return {'operation': 'update' if existing_work_item_id else 'create', 'project': project, 'work_item_id': existing_work_item_id, 'fields': fields, 'approval_id': approval_id, 'status': 'preview', 'idempotency_key': f"{project}:{existing_work_item_id or story.get('id')}"}

def generate_pipeline_yaml(*, scripts: list[str] | None = None, variable_group: str = 'valkiria-secrets') -> str:
    script_lines = scripts or ['echo "Placeholder: add approved test scripts"']
    safe_scripts = '\n'.join(f'      - script: {script!r}' for script in script_lines)
    return f'''trigger: none\npr: none\nvariables:\n- group: {variable_group}\nstages:\n- stage: Build\n  jobs:\n  - job: Build\n    steps:\n    - script: python -m compileall src\n- stage: Test\n  jobs:\n  - job: Test\n    steps:\n{safe_scripts}\n    - task: PublishTestResults@2\n      inputs:\n        testResultsFormat: JUnit\n        testResultsFiles: '**/test-results.xml'\n- stage: Publish\n  dependsOn: Test\n  jobs:\n  - job: Publish\n    steps:\n    - script: echo "Publication requires DevOps approval"\n'''

def performance_plan(story_id: str, *, tool: str, scenario_type: str, users: int, duration_seconds: int, sla_ms: int) -> dict[str, Any]:
    if tool.lower() not in SUPPORTED_PERFORMANCE_TOOLS:
        raise PolicyViolation(f'unsupported_performance_tool:{tool}')
    if users < 1 or duration_seconds < 1 or sla_ms < 1:
        raise PolicyViolation('performance_parameters_must_be_positive')
    return {'id': str(uuid4()), 'story_id': story_id, 'tool': tool.lower(), 'scenario_type': scenario_type, 'users': users, 'duration_seconds': duration_seconds, 'target_sla_ms': sla_ms, 'trigger': 'manual_or_high_risk_suggestion', 'execution': 'isolated_resource_with_limits'}

def automation_batch(cases: list[dict[str, Any]], *, framework: str, repository: str, base_branch: str = 'main') -> dict[str, Any]:
    if not cases:
        raise PolicyViolation('cases_required')
    if len(cases) > MAX_AUTOMATION_BATCH:
        raise PolicyViolation(f'automation_requires_batches:max={MAX_AUTOMATION_BATCH}:received={len(cases)}')
    normalized = framework.lower()
    if normalized not in SUPPORTED_AUTOMATION_FRAMEWORKS:
        raise PolicyViolation(f'unsupported_automation_framework:{framework}')
    return {'id': str(uuid4()), 'framework': normalized, 'repository': repository, 'base_branch': base_branch, 'case_ids': [str(case['id']) for case in cases], 'traceability': True, 'pattern': 'page-object-model', 'data_externalized': True, 'delivery': 'pull_request_only', 'direct_commit': False, 'status': 'draft'}
