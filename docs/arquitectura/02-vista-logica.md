# Vista lógica

[← Índice de arquitectura](../arquitectura.md)

La vista lógica describe las abstracciones de Valkiria, sus responsabilidades y sus relaciones, sin entrar en hilos, procesos ni despliegue. Los nombres de clases y funciones coinciden con el código de `src/valkiria/`.

## 1. Componentes

Valkiria tiene cuatro **puntos de coordinación**, cada uno para un tipo de interacción, que comparten los mismos servicios de aplicación:

| Coordinador | Interacción | Estado | Decide con |
|---|---|---|---|
| `ConversationController` | Chat: mensaje o acción de la interfaz | Sesión (memoria corta) ligada a un flujo | Reglas sobre el mensaje y el estado del flujo |
| `WorkflowEngine` | Ciclo de vida de una historia (HU-002 a HU-011) | `WorkflowState` persistente y versionado | Planificador determinista sobre el grafo de capacidades |
| `MultiAgentOrchestrator` | Petición aislada (`/v1/agent/execute`) | `AgentContext` efímero | `AgentRouter` con `can_handle` de cada agente |
| `ReasoningAssistant` | Pregunta o petición fuera del flujo | Pasos del razonamiento | LLM dentro de un catálogo cerrado y verificaciones en código |

```mermaid
flowchart TB
    subgraph PRES["Presentación"]
        FE["Frontend SPA<br/>index.html"]
        API["API FastAPI<br/>create_app"]
    end

    subgraph COORD["Coordinación"]
        CONV["ConversationController<br/>flow_view"]
        ENG["WorkflowEngine<br/>plan · EXECUTORS"]
        ORCH["MultiAgentOrchestrator<br/>AgentRegistry · AgentRouter"]
        ASSIST["ReasoningAssistant<br/>ToolBox"]
    end

    subgraph APP["Aplicación"]
        SVC["ValkiriaService<br/>casos de uso con LLM"]
        PROMPTS["prompts<br/>PROMPT_VERSION v4"]
        QAART["qa_artifacts<br/>políticas, pipeline, performance"]
        AUTO["automation_execution<br/>análisis SQL, lotes, evidencia"]
        SCRIPTS["script_generation · web_steps"]
        RUNNER["automation_runner<br/>casos API y consultas"]
        DEF["defects<br/>triage determinista"]
        MEM["MemoryService<br/>corto y largo plazo"]
        OPS["LLMOpsLifecycle<br/>fases y gates"]
    end

    subgraph DOM["Dominio"]
        MODELS["Modelos: UserStory, TestMatrix,<br/>InvestEvaluation, RiskAssessment…"]
        PORTS["Puertos: LLMPort, DatabaseExecutor,<br/>AzureDevOpsPort, CloudPort…"]
    end

    subgraph INFRA["Infraestructura y adaptadores"]
        LLM["OpenAICompatibleLLM"]
        DBX["SyntheticPostgresExecutor<br/>SyntheticSQLiteExecutor"]
        PW["PlaywrightRunner"]
        STORES["WorkflowStore · SessionStore ·<br/>LongTermStore · InMemory*"]
        SET["Settings · logging"]
        ADP["SafeAzureDevOpsAdapter · CloudAdapter"]
    end

    FE --> API
    API --> CONV
    API --> ENG
    API --> ORCH
    API --> ASSIST
    CONV --> ENG
    CONV --> ASSIST
    CONV --> SVC
    ENG --> SVC
    ENG --> ASSIST
    ENG --> MEM
    ORCH --> ASSIST
    ORCH --> MEM
    ORCH --> OPS
    ASSIST --> SVC
    ASSIST --> ENG
    ASSIST --> MEM
    SVC --> PROMPTS
    SVC --> OPS
    SVC --> MODELS
    ENG --> QAART
    ENG --> AUTO
    ENG --> SCRIPTS
    ENG --> RUNNER
    ENG --> DEF
    SVC --> LLM
    RUNNER --> DBX
    RUNNER --> PW
    ENG --> STORES
    MEM --> STORES
    LLM -. "implementa" .-> PORTS
    DBX -. "implementa" .-> PORTS
    ADP -. "implementa" .-> PORTS
```

`create_app()` en `api/app.py` es la **raíz de composición**: construye `Settings`, los almacenes, el proveedor LLM, el ejecutor SQL, el runner opcional, `ValkiriaService`, `MemoryService`, `WorkflowEngine`, `ReasoningAssistant`, `ConversationController` y `MultiAgentOrchestrator`, e inyecta las dependencias por constructor. No hay contenedor de inyección ni estado global.

## 2. Modelo de dominio

Entidades y objetos de valor de `domain/`. No dependen de ningún otro paquete de Valkiria.

```mermaid
classDiagram
    direction LR
    class UserStory {
        +UUID id
        +str title
        +str description
        +list~str~ business_rules
        +list~AcceptanceCriterion~ acceptance_criteria
        +Status status
        +int version
        +bool generated_by_ai
    }
    class AcceptanceCriterion {
        +str id
        +str text
    }
    class InvestEvaluation {
        +UUID id
        +UUID story_id
        +list~InvestCriterion~ criteria
        +str model
        +str prompt_version
        +datetime created_at
    }
    class InvestCriterion {
        +str name
        +str status
        +str justification
        +str suggestion
    }
    class TestMatrix {
        +UUID id
        +UUID story_id
        +list~TestCase~ cases
        +Status status
    }
    class TestCase {
        +str id
        +str criterion_id
        +str scenario
        +list~str~ preconditions
        +list~str~ steps
        +dict data
        +str expected_result
        +str priority
        +CaseType type
        +bool template
    }
    class RiskAssessment {
        +UUID id
        +UUID story_id
        +RiskLevel level
        +str justification
        +str mitigation
        +dict scores
        +int score_total
        +int execution_order
    }
    class AuditEvent {
        +UUID event_id
        +str trace_id
        +str actor
        +str action
        +ArtifactType artifact_type
        +int version
        +str outcome
        +datetime timestamp
    }
    class Metric {
        +str name
        +float value
        +str unit
        +str trace_id
    }
    class AutomationBatch {
        +UUID id
        +str framework
        +str platform
        +str repository
        +list~str~ case_ids
        +dict scripts
        +bool direct_commit = false
        +bool pr_required = true
    }
    class DatabaseExecution {
        +UUID id
        +str engine
        +str case_id
        +str status
        +bool blocked
        +dict static_analysis
    }
    class Status {
        <<enumeration>>
        draft
        approved
        rejected
        published
    }
    class CaseType {
        <<enumeration>>
        positive
        negative
        edge
    }
    class RiskLevel {
        <<enumeration>>
        low
        medium
        high
    }

    UserStory "1" *-- "1..30" AcceptanceCriterion
    InvestEvaluation "1" *-- "6" InvestCriterion
    InvestEvaluation ..> UserStory : story_id
    TestMatrix "1" *-- "0..30" TestCase
    TestMatrix ..> UserStory : story_id
    TestCase ..> AcceptanceCriterion : criterion_id
    RiskAssessment ..> UserStory : story_id
    AutomationBatch ..> TestCase : case_ids 1..15
    DatabaseExecution ..> TestCase : case_id
    UserStory --> Status
    TestCase --> CaseType
    RiskAssessment --> RiskLevel
```

Reglas de negocio que el modelo impone por validación Pydantic:
- una HU tiene entre 1 y 30 criterios;
- INVEST tiene exactamente 6 criterios;
- una matriz tiene como máximo 30 casos;
- un lote de automatización tiene entre 1 y 15 casos;
- `UserStory` prohíbe campos extra.

El nivel de riesgo se deriva de la suma de complejidad, dependencias y criticidad, cada una de 1 a 5: 3–6 es bajo, 7–11 medio y 12–15 alto.

## 3. Modelo del flujo por historia

El flujo por historia (`workflow/`) es el núcleo de Valkiria. `WorkflowState` es el agregado: todo cambio entra por `WorkflowEngine` y se guarda completo, con control optimista por `revision`.

```mermaid
classDiagram
    direction TB
    class WorkflowState {
        +str id
        +int revision
        +str actor
        +str trace_id
        +list~str~ goals
        +dict params
        +dict~str, ArtifactRecord~ artifacts
        +dict~str, StepFailure~ failures
        +list~ArtifactRecord~ history
        +list~ReasoningEntry~ reasoning
        +list answers
        +put_artifact(key, payload, produced_by, based_on) ArtifactRecord
        +think(decision, reason, capability)
    }
    class ArtifactRecord {
        +str key
        +int version
        +dict payload
        +str content_hash
        +dict~str, int~ based_on
        +str produced_by
        +list~str~ warnings
        +Approval approval
        +list memory_used
        +list~str~ assumptions
        +dict inputs
        +str model
        +str prompt_version
        +approved() bool
    }
    class Approval {
        +int version
        +str content_hash
        +str decision
        +str actor
        +str comment
        +dict details
        +datetime at
    }
    class StepFailure {
        +str capability
        +str error_code
        +str message
        +int attempts
        +bool retryable
    }
    class ReasoningEntry {
        +str capability
        +str decision
        +str reason
    }
    class Capability {
        <<frozen>>
        +str key
        +str hu
        +str title
        +tuple~Requirement~ requires
        +tuple~str~ optional
        +tuple~str~ optional_approved
        +bool approvable
        +tuple~str~ inputs
        +bool available
    }
    class Requirement {
        <<frozen>>
        +str artifact
        +bool approved
    }
    class WorkflowPlan {
        +list~str~ goals
        +list~PlanStep~ steps
        +runnable() list
        +pending_approvals() list
        +missing_inputs() list
        +status() str
        +next_actions(state) list
    }
    class PlanStep {
        +str capability
        +str hu
        +Action action
        +str reason
        +list~str~ blocked_by
        +list~str~ missing_inputs
    }
    class WorkflowStore {
        <<interface>>
        +get(workflow_id) WorkflowState
        +save(state) WorkflowState
    }
    class InMemoryWorkflowStore
    class SqlWorkflowStore
    class WorkflowEngine {
        +WorkflowStore store
        +ValkiriaService service
        +MemoryService memory
        +ReasoningAssistant assistant
        +int max_attempts = 3
        +float backoff_seconds = 1.0
        +start(request, goals, params, actor) tuple
        +request(workflow_id, request, goals, params, actor) tuple
        +approve(workflow_id, artifact, version, content_hash, decision) tuple
        +edit(workflow_id, artifact, payload, actor) tuple
        +adopt_story(story, requirement, actor) tuple
        +revise_story(workflow_id, payload, actor, instruction) tuple
        +record_pipeline_results(workflow_id, results, run_id) tuple
        +resume(workflow_id) tuple
        +get(workflow_id) tuple
        -_run(state) tuple
        -_execute(state, key) bool
    }
    class StepOutput {
        +dict payload
        +dict based_on
        +list~str~ warnings
        +str summary
        +list~str~ assumptions
    }
    class StepError {
        +str code
        +bool retryable
    }

    WorkflowState "1" *-- "0..*" ArtifactRecord : artifacts
    WorkflowState "1" *-- "0..*" ArtifactRecord : history
    WorkflowState "1" *-- "0..*" StepFailure
    WorkflowState "1" *-- "0..*" ReasoningEntry
    ArtifactRecord "1" o-- "0..1" Approval
    Capability "1" *-- "0..*" Requirement
    WorkflowPlan "1" *-- "1..*" PlanStep
    PlanStep ..> Capability : capability
    WorkflowStore <|.. InMemoryWorkflowStore
    WorkflowStore <|.. SqlWorkflowStore
    WorkflowEngine --> WorkflowStore
    WorkflowEngine ..> WorkflowPlan : plan()
    WorkflowEngine ..> StepOutput : EXECUTORS
    WorkflowEngine ..> StepError : captura
    WorkflowEngine ..> WorkflowState : carga y guarda
```

Conceptos clave:
- **`based_on`**: versiones de las dependencias con que se generó el artefacto. Si una dependencia declarada cambia de versión, el planificador marca el artefacto como `rerun`.
- **`approved`**: es una propiedad calculada, no un campo. Una aprobación solo vale si su `version` y su `content_hash` coinciden con los vigentes. Editar el artefacto invalida la aprobación sin tener que borrarla.
- **`inputs`**: datos del usuario con los que se generó (stack, repositorio, parámetros de performance). Si cambian en `params`, el artefacto se regenera.
- **`history`**: versiones anteriores de cualquier artefacto (auditoría y panel de versiones).

### Grafo de capacidades

`workflow/graph.py: CAPABILITIES` declara 14 capacidades. Se valida al importar el módulo: no puede haber ciclos ni dependencias desconocidas, y el orden topológico queda en `TOPOLOGICAL_ORDER`.

```mermaid
flowchart LR
    story["story<br/>HU-003B<br/>aprobable · pide requirement"]
    invest["invest<br/>HU-002<br/>aprobable"]
    rev["story_revision<br/>HU-003A<br/>produce story"]
    matrix["matrix<br/>HU-004<br/>aprobable"]
    risk["risk<br/>HU-005"]
    auto["automation<br/>HU-009<br/>aprobable · framework, repository"]
    dv["data_validation<br/>HU-011<br/>aprobable"]
    exe["execution<br/>HU-010<br/>un solo uso"]
    tri["triage<br/>HU-010<br/>aprobable · un solo uso"]
    pipe["pipeline<br/>HU-007<br/>aprobable"]
    perf["performance_design<br/>HU-008A<br/>aprobable · usuarios, duración, SLA"]
    wi["azure_work_item<br/>HU-006<br/>aprobable · azure_project"]
    sync["matrix_sync<br/>HU-004B<br/>NO DISPONIBLE"]
    prun["performance_run<br/>HU-008B<br/>NO DISPONIBLE"]

    story --> invest
    invest == "aprobado" ==> rev
    rev -. "nueva versión" .-> story
    story --> matrix
    story == "aprobada" ==> risk
    matrix --> auto
    matrix --> dv
    auto -. "opcional, aprobado" .-> exe
    dv -. "opcional, aprobado" .-> exe
    exe --> tri
    auto -. "opcional" .-> pipe
    dv -. "opcional, aprobado" .-> pipe
    story --> perf
    story == "aprobada" ==> wi
    matrix == "aprobada" ==> sync
    perf --> prun

    classDef off fill:#eee,stroke:#999,color:#777,stroke-dasharray: 4 3
    class sync,prun off
```

Leyenda: `-->` requiere el artefacto (basta el borrador); `==>` requiere el artefacto **aprobado** por una persona; `-.->` dependencia opcional: se usa si existe y, cuando cambia, obliga a regenerar.

### Acciones del planificador

`workflow/planner.py: plan()` clasifica cada capacidad alcanzable desde los objetivos en una de ocho acciones:

```mermaid
flowchart TD
    A(["decide(capacidad)"]) --> B{"¿available?"}
    B -- no --> U["unavailable"]
    B -- sí --> C{"¿artefacto existe y<br/>no está desactualizado?"}
    C -- sí --> R["reuse"]
    C -- no --> D{"¿cada requisito está en reuse<br/>y aprobado si lo exige?"}
    D -- no --> BL["blocked<br/>blocked_by: dep o approval:dep"]
    D -- sí --> E{"¿story_revision sin<br/>sugerencias aprobadas?"}
    E -- sí --> S["skipped"]
    E -- no --> F{"¿faltan inputs<br/>en params?"}
    F -- sí --> N["needs_input"]
    F -- no --> G{"¿agotó reintentos<br/>en esta llamada?"}
    G -- sí --> FA["failed"]
    G -- no --> H{"¿existe pero<br/>desactualizado?"}
    H -- sí --> RR["rerun"]
    H -- no --> RU["run"]
```

El estado global del plan se deriva de las acciones con esta precedencia: `running` > `waiting_approval` > `needs_input` > `failed` > `partially_completed` > `completed`.

## 4. Agentes y orquestación multiagente

`agents/` implementa la orquestación de una petición aislada. Cada agente cumple el protocolo `SpecializedAgent` y hereda de `BaseAgent` los constructores de resultado (`success`, `pending`, `blocked`, `failure`).

```mermaid
classDiagram
    direction TB
    class SpecializedAgent {
        <<Protocol>>
        +str name
        +Phase phase
        +can_handle(context) bool
        +execute(context) AgentResult
    }
    class BaseAgent {
        <<abstract>>
        +success(context, message, artifacts, decisions) AgentResult
        +pending(context, message, artifacts) AgentResult
        +blocked(context, message, artifacts) AgentResult
        +failure(context, message, retryable) AgentResult
    }
    class IntakeAgent { phase = INTAKE }
    class GroundingAgent { phase = GROUNDING }
    class GenerationAgent { phase = GENERATION; +llm }
    class AssistantAgent { phase = GENERATION; +assistant }
    class EvaluationAgent { phase = EVALUATION }
    class DatabaseAgent { phase = EVALUATION; +executor }
    class AutomationAgent { phase = EVALUATION; +runner }
    class ApprovalAgent { phase = APPROVAL }
    class ReleaseAgent { phase = RELEASE }
    class OperationsAgent { phase = OPERATE; +audit; +metrics }

    class AgentRegistry {
        -OrderedDict agents
        +register(agent)
        +get(name) SpecializedAgent
        +names() list
    }
    class AgentRouter {
        +plan(context) AgentPlan
    }
    class MultiAgentOrchestrator {
        +run(user_request, actor, trace_id, session_id, namespace) OrchestrationResult
        -_execute_with_retry(agent, context) AgentResult
        -_apply_result(context, result)
    }
    class AgentContext {
        +str request_id
        +str trace_id
        +str actor
        +str user_request
        +dict artifacts
        +list decisions
        +dict quality_gates
        +dict memory
        +follow_up() bool
    }
    class AgentResult {
        +str agent
        +Phase phase
        +str status
        +str message
        +str trace_id
        +dict artifacts
        +str gate_status
        +bool retryable
    }
    class AgentPlan {
        +list~str~ agents
        +list~str~ rationale
    }
    class OrchestrationResult {
        +str status
        +AgentPlan plan
        +dict artifacts
        +dict quality_gates
        +str error
    }
    class LLMOpsLifecycle {
        +record(ctx, phase, action, outcome)
        +record_gate(ctx, gate, status)
        +finish(ctx, outcome)
    }

    SpecializedAgent <|.. BaseAgent
    BaseAgent <|-- IntakeAgent
    BaseAgent <|-- GroundingAgent
    BaseAgent <|-- GenerationAgent
    BaseAgent <|-- AssistantAgent
    BaseAgent <|-- EvaluationAgent
    BaseAgent <|-- DatabaseAgent
    BaseAgent <|-- AutomationAgent
    BaseAgent <|-- ApprovalAgent
    BaseAgent <|-- ReleaseAgent
    BaseAgent <|-- OperationsAgent
    AgentRegistry o-- "10" SpecializedAgent
    AgentRouter --> AgentRegistry
    MultiAgentOrchestrator --> AgentRegistry
    MultiAgentOrchestrator --> AgentRouter
    MultiAgentOrchestrator --> LLMOpsLifecycle
    MultiAgentOrchestrator ..> AgentContext : crea
    MultiAgentOrchestrator ..> OrchestrationResult : devuelve
    AgentRouter ..> AgentPlan : crea
    SpecializedAgent ..> AgentResult : devuelve
```

| Agente | Fase (gate) | `can_handle` | Resultado típico |
|---|---|---|---|
| `intake` | INTAKE (G0) | Siempre | Intenciones por palabras clave; hereda la de la sesión en un seguimiento |
| `grounding` | GROUNDING (G1) | Siempre | Políticas y fuentes; `blocked` si la petición es ambigua |
| `generation` | GENERATION (G2) | `route_message == "story"` | Artefacto JSON del LLM o borrador determinista sin LLM |
| `assistant` | GENERATION (G2) | `route_message == "assistant"` | Respuesta con herramientas |
| `evaluation` | EVALUATION (G3) | `route_message == "story"` | INVEST pendiente de revisión, cobertura y riesgo |
| `database` | EVALUATION (G3) | SQL, base de datos, inventario, vehículos | Consulta de referencia en la base sintética |
| `automation` | EVALUATION (G3) | Playwright, Selenium, E2E | Preview o ejecución si hay runner |
| `approval` | APPROVAL (G4) | Aprobar, release, PR, publicar | `waiting_approval` sin hash aprobado |
| `release` | RELEASE (G5) | Release, PR, preview | Preview con `direct_commit=false` |
| `operations` | OPERATE (G6) | Siempre (se fuerza al final) | Resumen de trazabilidad |

El orden del plan es fijo: `intake`, `grounding`, `generation`, `evaluation` y `assistant` van primero (los que apliquen), y los demás después, en el orden del registro. El router falla si el plan no empieza con `intake` y `grounding`.

## 5. Conductor de la conversación

```mermaid
classDiagram
    direction LR
    class ConversationController {
        +ValkiriaService service
        +WorkflowEngine workflows
        +ReasoningAssistant assistant
        +MemoryService memory
        +DatabaseExecutor database_executor
        +handle(session_id, message, action, actor, namespace, trace_id) dict
        -_session(session_id, workflow_id) tuple
        -_remember(session_id, said, result, ctx)
    }
    class Turno["_Turn"] {
        +dict facts
        +WorkflowState state
        +understand(message) dict
        +first_contact(message) dict
        +in_flow(message, text) dict
        +act(action) dict
        +create_story(requirement) dict
        +edit_story(instruction) dict
        +new_story(requirement) dict
        +run(goal, params) dict
        +approve(artifact, assumptions_confirmed) dict
        +reject(artifact, comment) dict
        +decide_suggestions(decisions) dict
        +review_defects(decisions) dict
        +continue_flow() dict
        +tool(name, args) dict
        +validate_database() dict
        +ask(message) dict
        +chat(message) dict
    }
    class flow {
        <<module>>
        +STEPS: 14 pasos
        +flow_view(state) dict
        +next_actions(state, steps) list
        +resume_line(view) str
        +performance_reason(state) str
        +data_reason(state) str
    }
    ConversationController ..> Turno : crea uno por mensaje
    Turno --> ConversationController : c
    ConversationController ..> flow : flow_view
    Turno ..> WorkflowEngine : start, request, approve, revise_story
    Turno ..> ReasoningAssistant : ask
```

`_Turn` encapsula un turno: carga los `facts` de la sesión (flujo vigente, aclaración pendiente, división propuesta, último SQL) y el `WorkflowState`. Al terminar, `ConversationController._remember` guarda el turno y los `facts` en la memoria de corto plazo. La sesión es la única liga entre el chat y el flujo.

## 6. Asistente de razonamiento

```mermaid
classDiagram
    direction LR
    class ReasoningAssistant {
        +llm
        +ToolBox toolbox
        +int max_steps = 4
        +float tool_timeout = 20.0
        +ask(question, ctx, context) AssistantAnswer
        -_execute_tool(number, reason, data, ctx, run) Step
        -_compose(question, run, draft, ctx) AssistantAnswer
        -_deterministic(question, ctx, steps, why) AssistantAnswer
    }
    class ToolBox {
        +register(tool)
        +attach(name, summarize)
        +resolve(name) Tool
        +closest(query, limit) list
        +catalog() str
    }
    class Tool {
        <<frozen>>
        +str name
        +str kind
        +str title
        +Handler handler
        +tuple~Param~ params
        +tuple~str~ keywords
        +Summarizer summarize
        +validate(raw) dict
    }
    class Param {
        <<frozen>>
        +str name
        +str type
        +bool required
        +tuple choices
        +extract
    }
    class ToolContext {
        +str actor
        +str namespace
        +str session_id
        +str trace_id
        +str workflow_id
        +dict outputs
    }
    class Summary {
        +str text
        +tuple anchors
        +tuple exclusive
        +bool verbatim
    }
    class AssistantAnswer {
        +str status
        +str answer
        +bool grounded
        +list tools_used
        +list~Step~ steps
        +str mode
        +str missing_capability
        +dict outputs
    }
    class Step {
        +int number
        +str action
        +str tool
        +dict arguments
        +str observation
        +str error
    }
    class PolicyBlock {
        +str capability
        +str reason
        +str alternative
    }
    ReasoningAssistant --> ToolBox
    ToolBox o-- "15" Tool
    Tool *-- Param
    Tool ..> Summary : summarize
    ReasoningAssistant ..> AssistantAnswer
    AssistantAnswer *-- Step
    ReasoningAssistant ..> PolicyBlock : policy_block()
    Tool ..> ToolContext : handler(args, ctx)
```

El catálogo (`assistant/catalog.py: build_toolbox`) es **cerrado**: 11 herramientas y 4 skills. Cada herramienta tiene un `Summarizer` que calcula en código la conclusión verificable. `faithful()` acepta la redacción del modelo solo si menciona los `anchors` y respeta los `exclusive`. Ver [Asistente](../asistente.md).

## 7. Memoria

```mermaid
classDiagram
    direction TB
    class MemoryService {
        +bool enabled
        +bool persistent
        +recall(query, task, namespace) list~Recall~
        +remember(kind, content, source, actor, namespace)
        +session(session_id) Session
        +session_context(session_id) str
        +add_turn(session_id, role, content, facts)
    }
    class ShortTermMemory {
        +int max_turns = 12
        +int ttl_seconds = 7200
        +append(session_id, role, content, facts)
        +history(session_id, limit) list
        +clear(session_id) bool
    }
    class LongTermMemory {
        +int top_k = 4
        +int retention_days = 365
        +remember(kind, content, source, actor, namespace) tuple
        +recall(query, namespace, kinds, k) list~Recall~
        +search(query, namespace, kinds, limit) list
        +forget(record_id) bool
        +purge_expired() int
    }
    class SessionStore {
        <<interface>>
        +get(session_id)
        +save(session)
        +delete(session_id)
        +purge(older_than)
    }
    class LongTermStore {
        <<interface>>
        +upsert(record) tuple
        +list(namespace, kinds) list
        +get(record_id)
        +delete(record_id)
        +touch(record_ids, at)
        +purge(older_than)
    }
    class Session {
        +str id
        +list~Turn~ turns
        +list~str~ summary
        +dict facts
        +float updated_at
    }
    class Turn {
        +str role
        +str content
        +float at
        +dict meta
    }
    class MemoryRecord {
        +str id
        +str namespace
        +MemoryKind kind
        +str content
        +str source
        +str actor
        +list~str~ tags
        +float created_at
        +int uses
        +content_hash() str
    }
    class Recall {
        +MemoryRecord record
        +float score
        +list~str~ matched
    }
    class MemoryKind {
        <<enumeration>>
        approved_story
        po_preference
        human_correction
        lesson
        domain_fact
    }
    MemoryService --> ShortTermMemory
    MemoryService --> LongTermMemory
    ShortTermMemory --> SessionStore
    LongTermMemory --> LongTermStore
    SessionStore <|.. InMemorySessionStore
    SessionStore <|.. SqlSessionStore
    LongTermStore <|.. InMemoryLongTermStore
    LongTermStore <|.. SqlLongTermStore
    Session *-- Turn
    LongTermMemory ..> Recall : rank BM25
    Recall --> MemoryRecord
    MemoryRecord --> MemoryKind
```

`KINDS_BY_TASK` filtra qué tipos de recuerdo recibe cada tarea; por ejemplo, el riesgo solo recibe `lesson` y `domain_fact`. La búsqueda es BM25 léxica con decaimiento por antigüedad (vida media de 90 días). Ver [Memoria](../memoria.md).

## 8. Puertos y adaptadores

```mermaid
flowchart LR
    subgraph CORE["Núcleo: dominio y aplicación"]
        direction TB
        P1(["LLMPort<br/>generate_json"])
        P2(["DatabaseExecutor<br/>execute"])
        P3(["WorkflowStore<br/>get · save"])
        P4(["SessionStore · LongTermStore"])
        P5(["AuditPort · StoryRepository"])
        P6(["AzureDevOpsPort<br/>publish_story · validate_pipeline"])
        P7(["CloudPort<br/>provision_isolated_performance_job"])
        P8(["Web runner<br/>run(base_url, cases)"])
    end

    A1["OpenAICompatibleLLM<br/>Ollama · Azure OpenAI"] --> P1
    A2a["SyntheticPostgresExecutor"] --> P2
    A2b["SyntheticSQLiteExecutor"] --> P2
    A3a["SqlWorkflowStore"] --> P3
    A3b["InMemoryWorkflowStore"] --> P3
    A4a["SqlSessionStore · SqlLongTermStore"] --> P4
    A4b["InMemory*Store"] --> P4
    A5["InMemoryAudit · InMemoryMetrics · InMemoryStories"] --> P5
    A6["SafeAzureDevOpsAdapter<br/>referencia, solo preview"] --> P6
    A7["CloudAdapter azure<br/>referencia, plan"] --> P7
    A8["PlaywrightRunner<br/>Chromium"] --> P8

    classDef ref fill:#fff7e6,stroke:#d48806
    class A6,A7 ref
```

| Puerto | Adaptador por defecto | Adaptador alterno | Se elige con |
|---|---|---|---|
| `LLMPort` | `OpenAICompatibleLLM` → Ollama local | Mismo adaptador con `api-key` (endpoint administrado) | `VALKIRIA_LLM_BASE_URL`, `VALKIRIA_LLM_AUTH_HEADER` |
| `DatabaseExecutor` | `SyntheticSQLiteExecutor` (en memoria) | `SyntheticPostgresExecutor` | `VALKIRIA_DB_PROFILE=synthetic_postgresql` + URL |
| `WorkflowStore` | `InMemoryWorkflowStore` | `SqlWorkflowStore` (SQLite o PostgreSQL) | `VALKIRIA_WORKFLOW_DATABASE_URL` |
| `SessionStore`, `LongTermStore` | En memoria | SQL | `VALKIRIA_MEMORY_DATABASE_URL` o la de flujos |
| Web runner | Ninguno (no se ejecuta web) | `PlaywrightRunner` | `VALKIRIA_AUTOMATION_EXECUTE=true` |
| Auditoría, métricas, historias, lotes, reportes | En memoria | — (pendiente de persistencia durable) | — |

## 9. Modelo de datos persistente

### Base de control: flujos y memoria

Las tablas se crean al arrancar (`metadata.create_all`). El estado se guarda como JSON en una columna `TEXT`, sin ORM por entidad: el agregado completo se valida con Pydantic al leerse.

```mermaid
erDiagram
    valkiria_workflows {
        string id PK "UUID del flujo"
        int revision "control optimista"
        text state "WorkflowState JSON"
    }
    valkiria_memory_sessions {
        string id PK "session_id"
        float updated_at "índice; expiración TTL"
        text data "Session JSON: turns, summary, facts"
    }
    valkiria_memory_records {
        string id PK
        string namespace "aislamiento por equipo"
        string kind "approved_story, po_preference..."
        string content_hash "único por namespace"
        float created_at "retención"
        text data "MemoryRecord JSON"
    }
    valkiria_memory_sessions ||..o| valkiria_workflows : "facts.workflow_id"
```

### Base sintética Nissan

`synthetic_db/migrations/V1__create_schema.sql` con datos ficticios en `V2__seed_nissan_data.sql`. La usan la app sintética, el ejecutor HU-011 y la etapa `DataValidation` del pipeline.

```mermaid
erDiagram
    vehicles {
        int vehicle_id PK
        string model
        int year
        decimal price
        int stock
    }
    dealers {
        int dealer_id PK
        string name
        string region
        boolean active
    }
    customers {
        int customer_id PK
        string name
        string email "dominio .test"
    }
    inventory {
        int inventory_id PK
        int vehicle_id FK
        int dealer_id FK
        boolean available
    }
    sales_orders {
        int order_id PK
        int dealer_id FK
        int vehicle_id FK
        int customer_id FK
        string status
        decimal total
    }
    service_appointments {
        int appointment_id PK
        int customer_id FK
        int dealer_id FK
        string status
    }
    parts {
        int part_id PK
        string name
        int stock
    }
    test_cases {
        string case_id PK
        string scenario
        string expected_result
    }
    vehicles ||--o{ inventory : "está en"
    dealers ||--o{ inventory : "tiene"
    dealers ||--o{ sales_orders : "vende"
    vehicles ||--o{ sales_orders : "se vende en"
    customers ||--o{ sales_orders : "compra"
    customers ||--o{ service_appointments : "agenda"
    dealers ||--o{ service_appointments : "atiende"
```

El esquema no declara llaves foráneas en el DDL; las relaciones son lógicas. Es intencional para una base de pruebas que se recrea, pero significa que la integridad referencial no la garantiza el motor.

### Almacenes en memoria (no persistentes)

`InMemoryAudit`, `InMemoryMetrics`, `InMemoryStories`, `InMemoryBatchStore`, `InMemoryExecutionStore` e `InMemoryReportStore` viven en el proceso de la API. Se pierden al reiniciar y no se comparten entre réplicas. Ver el riesgo [R-1](06-decisiones-calidad-riesgos.md#riesgos-y-deuda-técnica).
