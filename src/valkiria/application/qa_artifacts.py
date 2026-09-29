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

# Reportes JUnit de cada stack: Playwright y pytest (test-results.xml), RestAssured/Maven (surefire) y Newman.
_JUNIT = "          **/test-results.xml\n          **/surefire-reports/TEST-*.xml\n          newman/*.xml\n"


def generate_pipeline_yaml(*, scripts: list[str] | None = None, variable_group: str = 'valkiria-secrets', data_validation: bool = False,
                           workflow_id: str | None = None) -> str:
    """YAML de Azure DevOps (HU-007): pruebas con JUnit, validación de datos HU-011 si hay consultas aprobadas y, con un flujo de
    Valkiria, envío de los resultados de vuelta para registrarlos como ejecución HU-010. Los secretos salen del grupo de variables
    ligado a Key Vault y se pasan solo a los pasos que los usan."""
    # Sin scripts, el placeholder marca la etapa con advertencia (SucceededWithIssues); nunca la reporta como pruebas aprobadas (HU-007).
    script_lines = scripts or ['echo "##vso[task.logissue type=warning]Placeholder: aún no hay scripts de prueba aprobados (HU-009)"; echo "##vso[task.complete result=SucceededWithIssues;]"']
    safe_scripts = '\n'.join(f'    - script: {script!r}' for script in script_lines)
    variables = f'variables:\n- group: {variable_group}\n' + (f'- name: VALKIRIA_WORKFLOW_ID\n  value: {workflow_id}\n' if workflow_id else '')
    test = (f'- stage: Test\n  dependsOn: Build\n  jobs:\n  - job: Test\n    steps:\n{safe_scripts}\n'
            "    - task: PublishTestResults@2\n      condition: succeededOrFailed()\n      inputs:\n        testResultsFormat: JUnit\n"
            f"        testResultsFiles: |\n{_JUNIT}        testRunTitle: 'HU-010 Scripts de automatizacion'\n"
            f"    - task: CopyFiles@2\n      condition: succeededOrFailed()\n      inputs:\n        contents: |\n{_JUNIT}"
            "        targetFolder: $(Build.ArtifactStagingDirectory)/junit\n"
            "    - publish: $(Build.ArtifactStagingDirectory)/junit\n      artifact: junit-tests\n      condition: succeededOrFailed()\n")
    data = ('- stage: DataValidation\n  displayName: Validacion de datos (HU-011)\n  dependsOn: Build\n  jobs:\n  - job: DataValidation\n    steps:\n'
            "    - script: pip install 'psycopg[binary]>=3.2,<4'\n"
            '    - script: python valkiria/validate_data.py --queries valkiria/queries.json --junit $(Build.ArtifactStagingDirectory)/junit/data-validation-results.xml\n'
            '      env:\n        SYNTHETIC_DATABASE_URL: $(synthetic-database-url)\n'
            "    - task: PublishTestResults@2\n      condition: succeededOrFailed()\n      inputs:\n        testResultsFormat: JUnit\n"
            "        testResultsFiles: '$(Build.ArtifactStagingDirectory)/junit/data-validation-results.xml'\n        testRunTitle: 'HU-011 Validacion de datos'\n"
            "    - publish: $(Build.ArtifactStagingDirectory)/junit\n      artifact: junit-data\n      condition: succeededOrFailed()\n") if data_validation else ''
    after = '[Test, DataValidation]' if data_validation else '[Test]'
    report = ('- stage: ReportToValkiria\n  displayName: Resultados a Valkiria (HU-010)\n'
              f'  dependsOn: {after}\n  condition: always()\n  jobs:\n  - job: Report\n    steps:\n'
              "    - download: current\n      patterns: '**/*.xml'\n"
              '    - script: python valkiria/report_results.py --results $(Pipeline.Workspace)\n'
              '      env:\n        VALKIRIA_URL: $(valkiria-url)\n        VALKIRIA_PIPELINE_TOKEN: $(valkiria-pipeline-token)\n'
              '        VALKIRIA_WORKFLOW_ID: $(VALKIRIA_WORKFLOW_ID)\n        BUILD_ID: $(Build.BuildId)\n') if workflow_id else ''
    return (f'trigger: none\npr: none\n{variables}stages:\n- stage: Build\n  jobs:\n  - job: Build\n    steps:\n    - script: python -m compileall src\n'
            f'{test}{data}{report}- stage: Publish\n  dependsOn: {after}\n  jobs:\n  - job: Publish\n    steps:\n    - script: echo "Publication requires DevOps approval"\n')


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
