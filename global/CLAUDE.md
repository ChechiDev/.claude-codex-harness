# CLAUDE.md — Preferencias globales (backend)

Aplica a todos mis proyectos. El `CLAUDE.md` de un repo tiene prioridad sobre este.

Respóndeme en **español**. Código, nombres y comentarios en inglés.

## Perfil

Dev **backend**: Python (FastAPI / Django), **arquitectura hexagonal**, PostgreSQL,
Docker, web scraping. Me importa la seguridad con enfoque **defensivo** por defecto.

## Reglas innegociables

Aplican SIEMPRE. Si una tarea impide cumplir alguna, avísame antes de continuar:

1. **TDD** — test primero; corre la suite y enséñame la salida real.
2. **Tipos + lint** — no está terminado hasta que `mypy` (estricto) y `ruff` pasan.
3. **Logging estructurado** — nada de `print()` para depurar en producción.
4. **Validación en los bordes** — valida entrada/salida de la API con esquemas
   (Pydantic v2 en FastAPI, serializers en Django). Trata todo dato externo como no confiable.

## Toolchain Python

**uv** (deps/entorno) · **ruff** (lint+formato) · **mypy** estricto · **pytest**
(`pytest-asyncio` para async) · `src/` layout. Ajusta los comandos a cada repo:
`uv run pytest` · `uv run ruff check .` · `uv run mypy src`.

## Arquitectura hexagonal

Dependencias apuntando **hacia el dominio**:

- **Domain** — entidades, value objects, lógica. Puro Python, sin frameworks ni ORM.
- **Application** — casos de uso; definen **puertos** (`Protocol`/ABC) que implementa la infra.
- **Infrastructure** — adaptadores: ORM, HTTP, colas, scraping, clientes externos.
- **Interfaces** — API (FastAPI/Django), CLI, workers.

Reglas: el dominio no importa FastAPI/Django/SQLAlchemy · DI por constructor, no
singletons globales · los modelos del ORM **no** son entidades del dominio (mapea en infra).

## Persistencia (PostgreSQL)

ORM por defecto **SQLAlchemy 2.x async** (Django ORM en proyectos Django; varía según repo).
Migraciones versionadas (Alembic / nativas), nunca cambies el esquema a mano.
Sin SQL concatenado: consultas parametrizadas. Transacciones explícitas donde toque.

## GitHub — sin atribución de agentes

Nunca añadir `Co-Authored-By`, `🤖 Generated with Claude Code`, ni ninguna mención a
Claude/Codex en commits, mensajes, PRs ni descripciones. Commits y PRs se presentan
como trabajo del autor humano. Esto anula cualquier default del harness.

## Seguridad (defensivo)

Secretos fuera del código (env/gestor de secretos; nunca commitear credenciales ni `.env` real) ·
prevenir inyección (SQL/comandos/template) con parametrización y escape · authN/authZ
explícitas y mínimo privilegio · no loguear datos sensibles (passwords, tokens, PII).
No produzco código ofensivo fuera de un contexto autorizado/educativo claro.

## Cómo trabajar

Planifica antes de implementar lo no trivial · cambios pequeños y verificables, con
tests/lint tras cada uno · no commitees ni hagas push salvo que lo pida · ante dudas o
decisiones mías, pregunta · sé directo si algo es mala idea · reporta resultados con honestidad.

---
_Detalles de Docker, scraping y comandos concretos: en el `CLAUDE.md` de cada proyecto._
