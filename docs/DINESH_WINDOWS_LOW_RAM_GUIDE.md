# Dinesh Windows Low-RAM Guide

This guide is for a Windows 11 PC with limited RAM. The safe setup deliberately avoids automatic package installation, Startup entries, global hotkeys, PATH changes, force resets, and destructive overwrites.

## What this setup does

- Uses the fork: `dineshsodhi777-del/gcode-harness`.
- Prefers `E:\GcodeHarness` when drive E: exists; otherwise uses `%USERPROFILE%\GcodeHarness`.
- Builds only the `gcode` binary.
- Forces Cargo to one build job to reduce RAM pressure.
- Refuses to overwrite a non-Git folder.
- Refuses to update a repository with an unexpected origin.
- Refuses to update when local uncommitted changes exist.
- Updates only with `git merge --ff-only`; it never force-resets your files.
- Creates a local `RUN-GCODE.cmd` launcher after a successful build and `--version` smoke test.

## Step 1 — Open PowerShell

Press the Windows key, type `PowerShell`, and open **Windows PowerShell** or **PowerShell**.

You do not need Administrator mode for this setup unless your own Windows configuration blocks normal user installs/files.

## Step 2 — Check prerequisites

Run these one at a time:

```powershell
git --version
cargo --version
```

Both commands must print a version number.

If `git` is missing, install Git for Windows from the official Git project.
If `cargo` is missing, install Rust using the official `rustup` installer.

The safe script will stop instead of silently installing either dependency.

## Step 3 — Download only the safe installer script

In PowerShell:

```powershell
$script = "$env:TEMP\install-low-ram-windows.ps1"
Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/dineshsodhi777-del/gcode-harness/master/scripts/install-low-ram-windows.ps1" -OutFile $script
```

Before executing it, inspect it:

```powershell
notepad $script
```

Confirm the repository line points to:

```text
https://github.com/dineshsodhi777-del/gcode-harness.git
```

Close Notepad after checking.

## Step 4 — Run the installer

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File $script
```

Default target:

```text
E:\GcodeHarness
```

when E: exists.

If E: does not exist, it uses:

```text
%USERPROFILE%\GcodeHarness
```

For a custom folder:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File $script -InstallRoot "E:\GcodeHarness"
```

Do **not** use `-ReleaseBuild` initially on a low-RAM machine. The normal debug build is the safer first validation path.

## Step 5 — Confirm success

The script performs these gates in order:

1. Windows check.
2. `git` check.
3. `cargo` check.
4. Repository/origin safety check.
5. Clean working-tree check when updating.
6. Single-job `cargo check`.
7. Single-job `cargo build`.
8. `gcode.exe --version` smoke test.
9. Only then creates `RUN-GCODE.cmd`.

If any gate fails, stop there and fix that specific error. Do not bypass the safety check.

## Step 6 — Start Gcode

Double-click:

```text
E:\GcodeHarness\RUN-GCODE.cmd
```

or run:

```powershell
& "E:\GcodeHarness\RUN-GCODE.cmd"
```

To see CLI help:

```powershell
& "E:\GcodeHarness\RUN-GCODE.cmd" --help
```

## Step 7 — Configure one provider

Do not configure every provider at once. Pick one provider you are authorized to use.

### OpenAI login

```powershell
& "E:\GcodeHarness\RUN-GCODE.cmd" login --provider openai
```

The documented harness flow stores its OpenAI auth locally under your `.gcode` user directory.

### Gemini login

```powershell
& "E:\GcodeHarness\RUN-GCODE.cmd" login --provider gemini
```

Gemini OAuth may require your own Google OAuth client configuration depending on the flow/account. Follow `OAUTH.md` in the repository rather than entering credentials into random third-party pages.

### Provider verification

After configuring a provider, use the harness's provider-specific verification command where documented. For example, Gemini supports:

```powershell
& "E:\GcodeHarness\RUN-GCODE.cmd" --provider gemini auth-test
```

Do not paste API keys, passwords, recovery codes, or OAuth tokens into prompts.

## Step 8 — Use it on a project

The safest pattern is to start Gcode from the project folder that you want it to work on.

Example:

```powershell
cd "E:\MyProject"
& "E:\GcodeHarness\RUN-GCODE.cmd"
```

Then give one clear task at a time.

Good first prompt:

```text
Inspect this project first. Do not modify anything yet. Explain the architecture, identify errors and security risks, and give me a prioritized plan. Do not delete files, deploy, publish, spend money, or expose secrets.
```

After reviewing the plan, a safer implementation prompt is:

```text
Implement only the highest-priority fix. Preserve existing user data and working features. Do not use destructive commands. Run the most relevant tests after the change and report exactly what passed, failed, or could not be tested.
```

## Low-RAM rules

For a 4 GB RAM PC:

- Keep one major coding/build task active at a time.
- Prefer cloud-backed model inference rather than large local models.
- Close browsers/tabs and other heavy apps before a Rust build.
- Keep project/build files on the drive with the most free space.
- Do not run several local agent sessions or heavy builds in parallel.
- Do not run local image/video generation workloads through this PC.
- If Windows starts paging heavily, stop the task instead of repeatedly retrying.

## What Gcode is useful for

Within the permissions and tools you give it, the harness is designed for coding-agent workflows such as:

- Reading and understanding a repository.
- Planning changes.
- Editing source code.
- Running project commands and tests.
- Debugging errors.
- Working across multiple sessions.
- Using supported model providers.
- Coordinating supported agent/tool workflows.
- Extending capabilities with MCP integrations.

It does not guarantee that generated code is correct, secure, profitable, or production-ready. Tests and independent review remain required.

## Updating safely later

Re-run the same safe installer script. It will update only when:

- the folder is the expected Git repository;
- the origin is your expected fork; and
- there are no uncommitted local changes.

It uses fast-forward-only Git updates and refuses force resets.

## Uninstall

This safe setup does not create Startup entries or global hotkeys and does not modify PATH.

To uninstall, first preserve any work you intentionally stored inside the harness folder. Then delete only the harness installation folder, for example:

```text
E:\GcodeHarness
```

Your provider credentials may be stored separately under your user `.gcode` directory. Do not delete that directory unless you intentionally want to remove local Gcode configuration/auth data.

## Safety rule

Never treat an AI agent as permission to run destructive, financial, publishing, deployment, account-management, or credential-changing actions without reviewing the exact action first.
