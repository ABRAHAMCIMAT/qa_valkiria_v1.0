# Seguridad

## Modelo de amenazas

Se consideran prompt injection, exposición de secretos, SQL destructivo, acceso a producción, commits directos, respuestas LLM malformadas, errores de proveedores y artefactos no confiables.

## Controles de entrada y LLM

- Validación Pydantic con límites.
- Grounding antes de generación.
- JSON validado por esquema.
- No se expone razonamiento interno completo.
- El contenido externo se trata como dato, no como instrucción.

## Datos y conexiones

- Requests sin DSN ni credenciales.
- `VALKIRIA_LLM_API_KEY` y `VALKIRIA_SYNTHETIC_DATABASE_URL` como `SecretStr`: no aparecen en `repr` ni en logs.
- `VALKIRIA_ENVIRONMENT=production` falla al iniciar sin `VALKIRIA_ALLOW_PRODUCTION=true`.
- Perfil de base explícito y permitido.
- Secretos fuera del repositorio.
- SQLite en memoria para unitarias.
- PostgreSQL efímero para integración.
- Producción bloqueada.

## SQL

- Análisis estático antes de ejecutar.
- Bloqueo de sentencias peligrosas.
- `WHERE`, límite, transacción y rollback para mutaciones.
- Errores del driver sanitizados.

## Release

- `VALKIRIA_RELEASE_MODE=preview` por defecto.
- `VALKIRIA_DIRECT_COMMIT=false`.
- PR obligatorio para cambios.
- Aprobación humana de la versión exacta.
- Bandit y Ruff en CI en cada push y Pull Request.

## Contenedores

- Imagen sin herramientas de build, usuario no root (`uid 10001`) y frontend de solo lectura.
- `.dockerignore` impide que `.env` y `.venv` entren al contexto de build.
- Compose: `read_only`, `cap_drop: [ALL]`, `no-new-privileges` y puertos publicados solo en `127.0.0.1`.
- Kubernetes: `runAsNonRoot`, seccomp `RuntimeDefault`, sin escalamiento de privilegios, sistema de archivos de solo lectura y sin token de service account.
- La contraseña `valkiria_synthetic_only` es exclusiva del PostgreSQL efímero local/CI.

## Análisis estático

Bandit no reporta hallazgos. Hay tres supresiones `# nosec B105` justificadas en el código: la clave `"pass"` (resultado de un caso) y los estados INVEST no son contraseñas. Ruff prohíbe capturas genéricas (`BLE001`); las tres que quedan (orquestador, ejecutor de PostgreSQL sintético y runner de Playwright) son fronteras de aislamiento con `noqa` justificado.

## Checklist

- [ ] OIDC/JWT conectado.
- [ ] Gestor de secretos configurado.
- [ ] CORS restringido a orígenes reales (`VALKIRIA_ALLOWED_ORIGINS`).
- [ ] Imagen publicada por digest en un registro controlado.
- [ ] Persistencia durable y migraciones.
- [ ] Retención de auditoría definida.
- [ ] PostgreSQL sintético separado de datos de control.
- [ ] Sin credenciales en Git, prompts, logs o reportes.
- [ ] Producción bloqueada hasta autorización formal.