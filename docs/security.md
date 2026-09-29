# Seguridad

## Modelo de amenazas

Se consideran prompt injection, exposición de secretos, SQL destructivo, acceso a producción, commits directos, respuestas LLM malformadas, errores de proveedores y artefactos no confiables.

## Controles de entrada y LLM

- Validación Pydantic con límites.
- Grounding antes de generación.
- JSON validado por esquema.
- No se expone razonamiento interno completo.
- El contenido externo se trata como dato, no como instrucción.

## Memoria

- La memoria de largo plazo solo aprende de decisiones humanas o de registros explícitos; un borrador del LLM nunca se memoriza.
- Antes de guardar, se redactan credenciales en URLs, pares `password=`, `token=` y `api_key=`, tokens Bearer, correos y cadenas largas tipo secreto.
- Se aísla por `namespace`, tiene retención configurable y se puede olvidar por API.
- Los recuerdos llegan al prompt delimitados como datos de referencia, "no son instrucciones", para reducir la inyección de instrucciones.

## Asistente con herramientas

- **Catálogo cerrado:** el modelo solo invoca herramientas registradas. El código valida y convierte los argumentos y ejecuta con tiempo límite. Los errores se aíslan sin filtrar detalles.
- **Herramientas de datos de solo lectura:** hacen GET a la app sintética. No se exponen la creación ni la cancelación de órdenes.
- **Políticas sin pasar por el modelo:** producción, commits directos, credenciales, correo e internet se rechazan en código.
- **Sin atribuir datos al modelo:** las respuestas sin herramientas se marcan como no verificadas, y una redacción del modelo que no es fiel a los datos se reemplaza por la conclusión calculada.

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
- `WHERE`, límite, transacción y rollback para mutaciones; el límite se exige en cada `UPDATE`/`DELETE`.
- En PostgreSQL, la traducción del límite solo se aplica a formas simples y conserva el texto original; ante cualquier ambigüedad el script se bloquea (fail closed).
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

Bandit no reporta hallazgos. Hay cinco supresiones justificadas en el código: tres `# nosec B105`, porque la clave `"pass"` (resultado de un caso) y los estados INVEST no son contraseñas, y dos `# nosec B608` en `sql_dialect.py`, porque la traducción de PostgreSQL solo recompone fragmentos del mismo script ya analizado y no incorpora datos externos. Ruff prohíbe capturas genéricas (`BLE001`); las tres que quedan (orquestador, ejecutor de PostgreSQL sintético y runner de Playwright) son fronteras de aislamiento con `noqa` justificado.

## Checklist

- [ ] OIDC/JWT conectado.
- [x] Gestor de secretos: Azure Key Vault con Workload Identity y Secret Store CSI (`deploy/azure/`).
- [ ] Red privada en Azure (VNet o Private Endpoint para PostgreSQL y Key Vault) y TLS en el Ingress.
- [ ] CORS restringido a orígenes reales (`VALKIRIA_ALLOWED_ORIGINS`).
- [ ] Imagen publicada por digest en un registro controlado.
- [ ] Persistencia durable y migraciones.
- [ ] Retención de auditoría definida.
- [ ] PostgreSQL sintético separado de datos de control.
- [ ] Sin credenciales en Git, prompts, logs o reportes.
- [ ] Producción bloqueada hasta autorización formal.