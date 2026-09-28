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
- Secret scan y análisis estático en CI.

## Checklist

- [ ] OIDC/JWT conectado.
- [ ] Gestor de secretos configurado.
- [ ] CORS restringido a orígenes reales.
- [ ] Persistencia durable y migraciones.
- [ ] Retención de auditoría definida.
- [ ] PostgreSQL sintético separado de datos de control.
- [ ] Sin credenciales en Git, prompts, logs o reportes.
- [ ] Producción bloqueada hasta autorización formal.