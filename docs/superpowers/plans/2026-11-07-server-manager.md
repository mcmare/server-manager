# Server Manager v1.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a safe local Debian CLI for Nginx sites, systemd services, existing Docker Compose projects, Certbot and readable port diagnostics, including optional project-scoped container/image reclamation without deleting data.

**Architecture:** Python 3 standard-library CLI. All external effects pass through one injectable command runner and explicit operation plans. Read-only discovery is separate from mutations; Nginx file changes use backup/test/reload/rollback and Docker cleanup uses a conservative inventory gate, fresh revalidation and per-object results.

**Tech Stack:** Python 3, `unittest`, `subprocess` (`shell=False`), Debian tools `nginx`, `systemctl`, `docker compose`, `docker inspect`, `ss`, `certbot`.

**Spec:** `docs/superpowers/specs/2026-11-07-server-manager-design.md`

## Global Constraints

- Debian, root-operated local CLI; no network listener or mandatory third-party Python packages.
- Nginx modification confined to `/etc/nginx/sites-available/` and `/etc/nginx/sites-enabled/`; other included files not managed.
- Existing/new systemd service supported; create new units only under `server-manager-<name>.service` namespace.
- Docker Compose: existing projects only, never edit compose YAML; `docker update --restart=no` only with explicit confirmation and saved old value.
- Never remove bind mounts, named or anonymous volumes, source/config/environment files, certificates, shared images, other-project objects, or global Docker cache.
- Docker cleanup is optional, project-scoped, never uses `down -v`, `down --rmi`, global prune or `image rm -f`; block cleanup if anonymous volumes or data preservation cannot be established.
- All mutating calls require an operation preview and explicit interactive confirmation; `--dry-run` performs no mutation.
- Run subprocesses as argv with `shell=False`, timeouts and no secrets in history; track partial failures.

## Review Focus

1. Symlinks in `sites-enabled` pointing outside `sites-available` must be displayed as unmanaged and never followed for mutation (Task 3).
2. `nginx -t` success but `systemctl reload nginx` failure must restore the prior file/link and record rollback failure distinctly (Task 4).
3. Multiple containers or services reporting the same backend port must prevent automatic service stop (Task 6).
4. Compose cleanup discovered against a stale container ID or changed mount must abort before any removal (Task 8).
5. A Docker image whose layers are shared with another project may be removed by ID only if the other project has no image reference; conservative candidate screening must skip ambiguity (Task 8).

## File map

- `pyproject.toml`, `README.md`: Python entrypoint and Debian setup.
- `server_manager/__main__.py`, `cli.py`: CLI parsing, menu, previews, confirmations.
- `server_manager/models.py`: immutable records and operation/result types.
- `server_manager/runner.py`: injectable subprocess executor and timeouts.
- `server_manager/state.py`: root-owned atomic JSON state, locking, redacted audit log.
- `server_manager/ports.py`: listening socket inventory and compact table.
- `server_manager/nginx.py`: managed-vhost inventory, safe generation and file operations.
- `server_manager/transactions.py`: Nginx backup, test, reload and rollback.
- `server_manager/systemd.py`: unit inventory, operation planning, creation.
- `server_manager/compose.py`: Compose project/service discovery, start/stop and restart policy.
- `server_manager/discovery.py`: conservative backend-to-service association.
- `server_manager/cleanup.py`: Compose-only inventory/preflight/apply.
- `server_manager/certbot.py`: independent certificate flows.
- `tests/test_*.py`: mocked unit/integration suites, zero live destructive commands.

## Phase 1 — Read-only foundation and safe Nginx management

### Task 1: Runner, models and state store

**Files:** Create `pyproject.toml`, `server_manager/__init__.py`, `server_manager/models.py`, `server_manager/runner.py`, `server_manager/state.py`; Test `tests/test_runner_state.py`.

**Interfaces:** Produces `run(argv: list[str], timeout: int = 30) -> CommandResult` via injectable `CommandRunner.run`; `CommandResult(returncode:int, stdout:str, stderr:str)`; `StateStore.record(action:str, target:str, result:dict) -> None` and atomic `StateStore.save_state(data:dict) -> None`. Other tasks depend on them.

- [ ] Step 1: Write failing tests `test_runner_uses_argv_without_shell`, `test_runner_timeout_is_reported`, `test_state_write_is_atomic_and_restrictive`, `test_audit_redacts_secret_fields`, `test_state_lock_prevents_concurrent_writer`. Assert no secret values appear in audit files and state writes are mode `0600`.
- [ ] Step 2: Run `python3 -m unittest tests.test_runner_state -v`. **Expected:** FAIL (modules absent).
- [ ] Step 3: Implement only runner, records, state persistence/locking and audit redaction; temporary filesystem in tests.
- [ ] Step 4: Run `python3 -m unittest tests.test_runner_state -v`. **Expected:** PASS.
- [ ] Step 5: Commit `git add pyproject.toml server_manager tests && git commit -m 'feat: add safe execution and state primitives'`.

### Task 2: Compact port inventory

**Files:** Create `server_manager/ports.py`; Test `tests/test_ports.py`.

**Interfaces:** Consumes `CommandRunner`; produces `list_listeners(runner, protocol: str | None = None) -> list[Listener]` and `format_listeners(listeners: list[Listener]) -> str`. `Listener` includes protocol, bind_address, port, process, pid, owner_hint and optional unknowns.

- [ ] Step 1: Write tests with representative `ss -H -lntup` fixtures for IPv4, bracketed IPv6, UDP, multiple PIDs and missing process details. Check numeric port sorting and explicit `?` for unknown owners.
- [ ] Step 2: Run `python3 -m unittest tests.test_ports -v`. **Expected:** FAIL.
- [ ] Step 3: Parse only listening sockets; avoid established connections and invented PID ownership. Add optional Docker correlation only when demonstrable; keep an unresolved owner as unknown.
- [ ] Step 4: Run same command. **Expected:** PASS.
- [ ] Step 5: Commit `git add server_manager/ports.py tests/test_ports.py && git commit -m 'feat: add compact listener inventory'`.

### Task 3: Nginx inventory and guarded vhost generation

**Files:** Create `server_manager/nginx.py`; Test `tests/test_nginx.py`.

**Interfaces:** Produces `list_sites(available_dir: Path, enabled_dir: Path) -> list[Site]`, `render_proxy_site(domains: tuple[str,...], address: str, port: int) -> str`, `plan_site_change(action: str, site: Site) -> SiteChange`. `Site` contains config path, enabled status, domains, proxy endpoints, ambiguity flag.

- [ ] Step 1: Write tests for enabled/disabled sites, several server blocks/domains per file, unsafe/absolute/outside symlink, malformed server config, duplicate domain, malicious domain with directive injection, and foreign `conf.d` exclusion.
- [ ] Step 2: Run `python3 -m unittest tests.test_nginx -v`. **Expected:** FAIL.
- [ ] Step 3: Implement conservative token analysis for preview only; do not claim to parse complete Nginx DSL. Generate only one controlled HTTP proxy block; refuse ambiguous mutations and unsafe targets. Disallow overwriting existing files by default.
- [ ] Step 4: Run same command. **Expected:** PASS.
- [ ] Step 5: Commit `git add server_manager/nginx.py tests/test_nginx.py && git commit -m 'feat: inspect and generate managed nginx sites'`.

### Task 4: Transactional Nginx mutations

**Files:** Create `server_manager/transactions.py`; Test `tests/test_transactions.py`.

**Interfaces:** Consumes `SiteChange`, `CommandRunner`, `StateStore`; produces `apply_site_change(change: SiteChange, runner, state, dry_run: bool = False) -> OperationResult` and `rollback_site_change(backup_id: str, ...) -> OperationResult`.

- [ ] Step 1: Write tests for atomic symlink enable/disable, backup before mutation, failed `nginx -t`, failed reload after a successful test, failed rollback, idempotent repeat, and dry-run making zero mutations.
- [ ] Step 2: Run `python3 -m unittest tests.test_transactions -v`. **Expected:** FAIL.
- [ ] Step 3: Implement root-owned backup and revalidation; apply `nginx -t` then `systemctl reload nginx`; on failure restore exact prior symlink/file state and retry test/reload. Distinguish partial status from success.
- [ ] Step 4: Run same command. **Expected:** PASS.
- [ ] Step 5: Commit `git add server_manager/transactions.py tests/test_transactions.py && git commit -m 'feat: transact nginx changes with rollback'`.

## Phase 2 — Service control and external tool integration

### Task 5: systemd discovery and managed unit creation

**Files:** Create `server_manager/systemd.py`; Test `tests/test_systemd.py`.

**Interfaces:** `list_units(runner) -> list[Unit]`; `render_managed_unit(name:str, user:str, workdir:Path, argv:tuple[str,...], env_file:Path|None=None) -> str`; `plan_systemd_action(unit:Unit, verb:str) -> ServiceAction`; `apply_systemd_action(action, runner, state, dry_run=False) -> OperationResult`.

- [ ] Step 1: Write tests for existing-unit listings, invalid/nonexistent workdir, root user refused for new service, unit-name injection, `ExecStart` quoting, existing unit file protection, disable vs stop distinction, `daemon-reload` failure, dry-run.
- [ ] Step 2: Run `python3 -m unittest tests.test_systemd -v`. **Expected:** FAIL.
- [ ] Step 3: Implement only namespaced managed units; validate Unix user and non-root UID, safe command args and paths. Require distinct confirmations via CLI before start/stop/enable/disable; never delete units with site disable.
- [ ] Step 4: Run same command. **Expected:** PASS.
- [ ] Step 5: Commit `git add server_manager/systemd.py tests/test_systemd.py && git commit -m 'feat: manage systemd services'`.

### Task 6: Compose inventory, actions and backend association

**Files:** Create `server_manager/compose.py`, `server_manager/discovery.py`; Test `tests/test_compose.py`, `tests/test_discovery.py`.

**Interfaces:** `discover_compose(runner) -> list[ComposeProject]`; `plan_compose_action(project:ComposeProject, services:tuple[str,...], verb:str) -> ComposeAction`; `apply_compose_action(action, runner, state, dry_run=False) -> OperationResult`; `plan_restart_override(container_ids:tuple[str,...], runner) -> RestartPlan`; `find_backend_owners(endpoint:Endpoint, runner) -> list[BackendOwner]` with confidence and no auto-selection if ambiguous.

- [ ] Step 1: Write tests for Compose labels and files, missing compose directory, duplicate port on two services, unpublished internal container port, stopped containers, restart policy snapshots and failure partway through `docker update`.
- [ ] Step 2: Run `python3 -m unittest tests.test_compose tests.test_discovery -v`. **Expected:** FAIL.
- [ ] Step 3: Implement discovery from labels/inspect and validated Compose path/project; stop/start through scoped `docker compose -f ... -p ...`. Use `docker update --restart=no` only on selected IDs after capturing previous policy; warn of recreation.
- [ ] Step 4: Run same command. **Expected:** PASS.
- [ ] Step 5: Commit `git add server_manager/compose.py server_manager/discovery.py tests/test_compose.py tests/test_discovery.py && git commit -m 'feat: manage existing compose services safely'`.

### Task 7: Certbot standalone workflow

**Files:** Create `server_manager/certbot.py`; Test `tests/test_certbot.py`.

**Interfaces:** `plan_certificate(site:Site, runner) -> CertPlan`; `run_certificate(plan:CertPlan, runner, state, dry_run=False) -> OperationResult`; `list_certificates(runner) -> list[CertificateInfo]`.

- [ ] Step 1: Write tests proving Certbot cannot run for disabled or invalid domains, is not triggered by site creation, backups precede Certbot invocation, failed `nginx -t` is reported, and renewal dry-run is separate.
- [ ] Step 2: Run `python3 -m unittest tests.test_certbot -v`. **Expected:** FAIL.
- [ ] Step 3: Implement interactive explicit `certbot --nginx -d` on selected managed sites; preserve certificates when site disabled. Flag limits of DNS/Internet reachability checks.
- [ ] Step 4: Run same command. **Expected:** PASS.
- [ ] Step 5: Commit `git add server_manager/certbot.py tests/test_certbot.py && git commit -m 'feat: add opt-in certbot actions'`.

## Phase 3 — Restricted cleanup and user workflows

### Task 8: Compose cleanup preflight and safe executor

**Files:** Create `server_manager/cleanup.py`; Test `tests/test_cleanup.py`.

**Interfaces:** `inspect_cleanup(project:ComposeProject, runner) -> CleanupPlan`; `apply_cleanup(plan:CleanupPlan, runner, state, confirmed:bool, dry_run:bool=False) -> CleanupResult`. Plan includes immutable inventory fingerprint, container IDs, mount classes, mutable-layer warning, image IDs with reference/recoverability verdict, required confirmations and guarded refusal reasons.

- [ ] Step 1: Write failing tests for named/bind mount retention, anonymous-volume refusal, writable-layer uncertainty refusal, common image ID skip, registry/build-context unavailable image skip, shared image/tag collision, project membership change since preview, container ID replacement, zero confirmation, partial stop/rm/image-rm failure, and no global prune/`-v`/`-f` ever. Assert `docker compose rm -f` does not add `-v` and image removal never uses `-f`.
- [ ] Step 2: Run `python3 -m unittest tests.test_cleanup -v`. **Expected:** FAIL.
- [ ] Step 3: Build a read-only plan first. Show exact candidate IDs and exclusions. Reject anonymous volumes and uncertain mounted/user data before **any** removal, require explicit user attestation that writable-layer data may be lost (attestation alone does not override known critical unmounted paths). Verify project/inventory fingerprint immediately before cleanup; stop project, remove only listed containers; recheck all Docker container references and image availability before individual `docker image rm <id>` without force. Do not touch volumes, other projects or builder cache.
- [ ] Step 4: Run same command. **Expected:** PASS.
- [ ] Step 5: Commit `git add server_manager/cleanup.py tests/test_cleanup.py && git commit -m 'feat: add guarded compose-only disk reclamation'`.

### Task 9: CLI menu and coordinated site workflows

**Files:** Create `server_manager/cli.py`, `server_manager/__main__.py`; Test `tests/test_cli.py`.

**Interfaces:** `main(argv: list[str] | None = None) -> int`; `plan_disable_site(site:Site, requested_backend_action:str, ...) -> OperationPlan`; `execute_plan(plan, runner, state, confirmed:bool, dry_run:bool=False) -> OperationResult`.

- [ ] Step 1: Write tests for main menu/subcommands (`ports`, `sites list`, `--dry-run`), cancel/confirmation, shared backend warning, unknown owner forbids stop, Nginx failure prevents backend stop, explicit stop vs disable choice, independent Certbot execution, cleanup never triggered by normal stop.
- [ ] Step 2: Run `python3 -m unittest tests.test_cli -v`. **Expected:** FAIL.
- [ ] Step 3: Wire services with dependency injection and no ad hoc subprocess calls. For site disable, transact Nginx first, then optionally act on backend with partial-result journal. For site creation, validate endpoint and selected owner, then transact Nginx; Certbot remains separate. Provide noninteractive readable port output.
- [ ] Step 4: Run same command. **Expected:** PASS.
- [ ] Step 5: Commit `git add server_manager/cli.py server_manager/__main__.py tests/test_cli.py && git commit -m 'feat: integrate interactive server manager workflows'`.

### Task 10: Safety regression, packaging and installation instructions

**Files:** Create/modify `README.md`, `pyproject.toml`; Create `tests/test_acceptance.py`.

**Interfaces:** Installed executable `server-manager` uses `server_manager.cli:main`; production default paths documented, tests use injected temporary dirs and fake runner.

- [ ] Step 1: Write acceptance tests for the twelve acceptance scenarios in the spec including Nginx rollbacks, Compose isolated cleanup, unknown owners, idempotence, safety of source and volume preservation, and failure logs.
- [ ] Step 2: Run `python3 -m unittest tests.test_acceptance -v`. **Expected:** FAIL until missing behaviors are fixed.
- [ ] Step 3: Implement narrow missing integration behavior, Debian installation instructions, sudo/root requirements, permission guidance, backup recovery, manual dry-run walkthrough and known limitations. Never operate on live services in automated tests.
- [ ] Step 4: Run `python3 -m unittest discover -s tests -v` and `python3 -m compileall -q server_manager`. **Expected:** PASS, exit code 0 for both.
- [ ] Step 5: Commit `git add . && git commit -m 'test: verify safety flows and document deployment'`.

## Release verification gate

- In an isolated Debian VM or disposable containers, exercise `nginx -t`, link changes, systemd unit lifecycle, Compose start/stop, cleanup dry-run and Certbot dry-run with representative fake projects; never run destructive tests on a production host.
- Verify `--dry-run` causes zero mutations, and cleanup cannot issue commands targeting outside selected project.
- Perform whole-branch code review, especially symlink traversal, ownership attribution, output parsing, secret logging and cleanup inventory time-of-check/time-of-use.
- Deployment requires explicit user approval after plan review and all safety tests.

## Self-review record

- Coverage: all spec sections map to Tasks 1–10; cleanup and restoration addressed in Task 8/10.
- Interface dependency sequence: Runner/State → Ports/Nginx → Transactions → systemd/Compose → Certbot/Cleanup → CLI → Acceptance.
- Real-world uncertainty is handled conservatively: unknown process/image ownership, unprovable data preservation, or incomplete Compose inventory block destructive actions rather than guesses.
