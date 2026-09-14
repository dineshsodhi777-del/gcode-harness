<div align="center">

# Gcode Harness

[![License](https://img.shields.io/github/license/bitan-del/gcode-harness?style=flat-square)](LICENSE)
[![Platforms](https://img.shields.io/badge/platforms-Linux%20%7C%20macOS%20%7C%20Windows-blue?style=flat-square)](https://github.com/bitan-del/gcode-harness/releases)
[![Language](https://img.shields.io/badge/language-Rust-orange?style=flat-square)](https://www.rust-lang.org/)

A fast, customizable coding agent harness built in Rust. <br>
Designed for multi-session workflows, multi-model orchestration, and a streamlined terminal UI.

[Features](#features) · [Install](#installation) · [Quick Start](#quick-start) · [Contributing](CONTRIBUTING.md)

</div>

---

## Features

- **Blazing-fast TUI** — a responsive terminal interface that stays out of the way while you work.
- **Multi-model support** — plug in different LLM providers and switch between them per task.
- **Multi-session workflows** — run several agent sessions in parallel, each with its own state.
- **Swarm coordination** — orchestrate multiple agents on a single goal, with shared context and tool access.
- **Extensible tooling** — 30+ built-in tools, plus MCP support for adding your own.
- **Compaction & memory** — long-running sessions stay coherent thanks to background compaction and a persistent memory layer.
- **Cross-platform** — Linux, macOS, and Windows binaries.

---

## Installation

### macOS & Linux

```bash
curl -fsSL https://raw.githubusercontent.com/bitan-del/gcode-harness/master/scripts/install.sh | bash
```

### Windows (PowerShell)

```powershell
iwr -useb https://raw.githubusercontent.com/bitan-del/gcode-harness/master/scripts/install.ps1 | iex
```

### From source

Requires a recent Rust toolchain (stable):

```bash
git clone https://github.com/bitan-del/gcode-harness.git
cd gcode-harness
cargo build --release
./target/release/gcode --help
```

---

## Quick Start

After installing, point Gcode at your project directory and start a session:

```bash
cd /path/to/your/project
gcode
```

On first run you'll be prompted to configure a model provider. See [OAUTH.md](OAUTH.md) for provider authentication details.

> **Heads up — Google OAuth setup.** The Gemini and Antigravity "Sign in with Google" flows require your own Google Cloud "Desktop app" OAuth client. Register one at the [Google Cloud Console](https://console.cloud.google.com/apis/credentials) and export the credentials before signing in:
>
> ```bash
> export GEMINI_CLIENT_ID="your-id.apps.googleusercontent.com"
> export GEMINI_CLIENT_SECRET="your-secret"
> export GCODE_ANTIGRAVITY_CLIENT_ID="your-id.apps.googleusercontent.com"
> export GCODE_ANTIGRAVITY_CLIENT_SECRET="your-secret"
> ```
>
> API-key based providers (OpenAI, OpenRouter, etc.) work without this step.

---

## Project Layout

This is a Rust workspace. The top-level `Cargo.toml` lists every member crate. The most important ones live under `crates/`:

- `gcode-core` — core agent runtime and orchestration.
- `gcode-tui-*` — terminal UI components (rendering, markdown, session picker, etc.).
- `gcode-provider-*` — model provider integrations (OpenAI, Gemini, OpenRouter, etc.).
- `gcode-tool-*` — tool registry and tool implementations.
- `gcode-mobile-*` — mobile companion app glue.
- `gcode-storage`, `gcode-memory-types` — persistence and memory.

Documentation lives in [docs/](docs/), and contributor guidance is in [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md).

---

## Telemetry

Anonymous usage telemetry is opt-in. See [TELEMETRY.md](TELEMETRY.md) for what is collected and how to disable it.

---

## Releasing

Release process and packaging notes are in [RELEASING.md](RELEASING.md).

---

## License

Released under the [MIT License](LICENSE).

<!-- ci-verification-trigger: 2026-09-14 -->
