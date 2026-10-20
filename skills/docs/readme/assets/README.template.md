# <Project Name>

<Two or three plain sentences a new teammate could read and understand:
what this project does, the problem it solves, and who uses it. Write it the
way you would explain it out loud — technical, but human. No icons.>

## Prerequisites

<Everything required before installing: runtime versions and system
dependencies. Be exact.>

- <e.g. Python 3.12+>
- <e.g. uv>
- <e.g. PostgreSQL 16>

## Install

```bash
<exact install commands — e.g. uv sync>
```

## Quickstart

<!--
Adapt this block to the project type (see the `readme` skill decision gate):
  CLI       → the command invocation and its expected output
  Library   → import + the smallest useful call
  Service   → how to run it locally + one sample request (curl/httpie)
  App       → how to launch it and reach the first screen
First success MUST be reachable in under 5 minutes.
-->
```bash
<first runnable example>
```

## Architecture

<One or two paragraphs describing the main components and how a request or a
piece of data moves through the system. Then the two diagrams below.>

Components:

```mermaid
graph TD
    <component A> --> <component B>
    <component B> --> <component C>
```

Request / data flow:

```mermaid
sequenceDiagram
    <actor> ->> <entry point>: <action>
    <entry point> ->> <service>: <call>
    <service> -->> <actor>: <result>
```

See [docs/architecture.md](docs/architecture.md) for the full detail.

<!-- Optional sections below — keep only the ones that apply. -->

## Documentation

- [Architecture](docs/architecture.md)
- [Configuration](docs/configuration.md)

## Configuration

<Key environment variables / settings, or a link to docs/configuration.md.>

## Development

```bash
make test
```

## License

Proprietary — internal use only.
