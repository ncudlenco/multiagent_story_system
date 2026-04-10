# CLAUDE.md

Onboarding instructions for AI coding agents (and human contributors) working in this repository. For an end-user introduction to the project, see the [README](README.md).

## Project overview

**multiagent_story_system** is the Python orchestrator that drives [mta-sim](https://github.com/ncudlenco/mta-sim) — the Lua MTA San Andreas simulation engine — to render *Graphs of Events in Space and Time (GESTs)* into multi-actor narrative videos with frame-level ground-truth annotations. It is the Python half of a two-repo system; the simulation engine itself lives in the companion repo.

The repository contains two GEST-generation paths and they should not be confused:

- **Procedural generators** ([random_gest_generator.py](random_gest_generator.py), [simple_gest_random_generator.py](simple_gest_random_generator.py)) produce simulatable GESTs from formal rules and templates, with no LLM involvement. This is the path that reliably produces graphs mta-sim can execute end-to-end.
- **The LLM agent pipeline** ([agents/](agents/), most of [workflows/](workflows/), [core/](core/)) is **kept for historical reasons only**. It can produce JSON GESTs that pass the schema, but the resulting graphs do not survive end-to-end simulation in mta-sim — they fail at simulation time on subtler invariants the agents do not yet handle. Don't propose any of this code as the solution to a "generate a runnable GEST" problem.

The rest of the repository (batch infrastructure, MTA controller, log parser, VM orchestration, Google Drive integration) is the load-bearing plumbing that drives mta-sim at scale.

## Source layout

Top-level Python entry points:

- [main.py](main.py) — single-story CLI. Runs one generation and renders it through mta-sim.
- [batch_generate.py](batch_generate.py) — batch CLI: produces N stories with retry handling, artifact collection, and optional Google Drive upload.
- [vmware_orchestrator.py](vmware_orchestrator.py), [vm_auto_runner.py](vm_auto_runner.py), [vm_monitor.py](vm_monitor.py) — multi-VM orchestration for parallel batch runs across VMware Workstation guests. Each VM hosts an mta-sim instance and runs a slice of a batch.
- [random_gest_generator.py](random_gest_generator.py), [simple_gest_random_generator.py](simple_gest_random_generator.py) — the procedural GEST generators (the working path).
- [temporal_rules_formalized.py](temporal_rules_formalized.py) — formal temporal-rule definitions used by the procedural generator.
- [gdrive_manager.py](gdrive_manager.py), [gdrive_statistics.py](gdrive_statistics.py), [download_gdrive_folders.py](download_gdrive_folders.py), [gdrive_clip_catalog.py](gdrive_clip_catalog.py), [gdrive_trigger_video_processing.py](gdrive_trigger_video_processing.py) — Google Drive integration helpers.
- [test_gdrive.py](test_gdrive.py) — quick connectivity check for the Drive uploader.

Internal packages:

- [batch/](batch/) — batch infrastructure: [batch_controller.py](batch/batch_controller.py), [batch_reporter.py](batch/batch_reporter.py), [retry_manager.py](batch/retry_manager.py), [google_drive_uploader.py](batch/google_drive_uploader.py), [artifact_collector.py](batch/artifact_collector.py). Load-bearing.
- [utils/](utils/) — shared utilities. The two important ones are [mta_controller.py](utils/mta_controller.py) (start/stop the MTA server and client, write `config.json` next to the sv2l resource, monitor process health, enforce adaptive timeouts) and [log_parser.py](utils/log_parser.py) (extract success/error markers from `server.log` and `clientscript.log`). Everything else in `utils/` is plumbing around these two.
- [schemas/](schemas/) — Pydantic models. [gest.py](schemas/gest.py) is the unified GEST schema everything validates against.
- [agents/](agents/), [workflows/](workflows/), [core/](core/) — the historical LLM-agent pipeline. **Do not touch unless you are explicitly working on the agentic line.** None of it produces reliably simulatable GESTs.
- [tests/](tests/) — pytest test suite (covers the schema and a few utilities, not end-to-end).
- [examples/](examples/), [data/](data/) — reference GESTs, cached game capabilities, and the natural-language GEST specification ([data/documentation/gest_instructions.md](data/documentation/gest_instructions.md)).

Configuration:

- [config.yaml](config.yaml) — runtime configuration: MTA paths, validation settings (success/error patterns parsed from MTA logs, adaptive timeouts), batch settings, Google Drive settings.
- `.env` (gitignored, see [.env.example](.env.example)) — only needed if you're touching the historical LLM path.
- [vmware_config.yaml](vmware_config.yaml) — VM orchestration settings.

## Running the system

The [README](README.md) covers full installation and end-to-end usage; the points below are the ones an agent typically needs:

- **Single story**: `python main.py [args]`. Runs one generation and pipes the resulting GEST to mta-sim through [`MTAController`](utils/mta_controller.py).
- **Batch**: `python batch_generate.py --count N [--google-drive-folder <id>]`. Wraps the single-story flow in retry/timeout/artifact-upload machinery.
- **Multi-VM batch**: `python vmware_orchestrator.py` reads [vmware_config.yaml](vmware_config.yaml) and farms work out to multiple VMs in parallel. This is how large datasets are produced.
- **The system requires both the MTA server and the MTA client running.** [`MTAController`](utils/mta_controller.py) starts both. The client must connect to `localhost` for the server to start processing — starting the server alone does nothing. See the [mta-sim README](https://github.com/ncudlenco/mta-sim) for details.

## Code conventions

- **Always validate GESTs against [schemas/gest.py](schemas/gest.py) before handing them to mta-sim.** The simulator's failure modes for malformed GESTs are noisy and waste a full simulation cycle (minutes per failed attempt). Catch them in Python first.
- **Don't modify [`src/ServerGlobals.lua`](https://github.com/ncudlenco/mta-sim/blob/main/src/ServerGlobals.lua) in mta-sim from Python.** Configure mta-sim by writing `config.json` next to the sv2l resource directory; [`MTAController`](utils/mta_controller.py) does this automatically.
- **`MTAController` and `LogParser` determine success by parsing log markers**, not by process exit codes — mta-sim's exit codes are unreliable. The patterns live in [config.yaml](config.yaml) under `validation.success_patterns` and `validation.error_patterns`. Add new patterns there rather than hardcoding.
- **structlog** for logging (`structlog.get_logger(__name__)`). Pass key-value context, not interpolated strings: `logger.info("batch_finished", n=n, failed=failed)`.
- **No backwards-compatibility narration.** When something changes, write the new code and docs as the only solution. Don't leave "previously this used..." comments or migration notes.

## Critical context

- **End-to-end validation against mta-sim is the only meaningful test.** A GEST that passes the Pydantic schema can still fail at simulation because of subtler invariants (chain ID conflicts, missing prerequisites, infeasible spatial layouts). Plan for slow feedback: a single simulation takes minutes; a batch run takes hours.
- **The LLM-agent code in [agents/](agents/), [workflows/](workflows/), and [core/](core/) does not produce simulatable GESTs.** It's kept in the repo for historical reasons. Don't propose any of these files as the solution to a generation problem.
- **Adaptive timeouts protect against MTA hangs.** [`validation.no_action_progress_timeout_seconds`](config.yaml), `client_connect_timeout_seconds`, `hung_window_timeout_seconds`, etc. — each protects a different failure mode (sim stuck mid-action, client never connecting, GTA window "Not Responding"). Tune them individually; the long absolute caps exist because a healthy run can take a long time and the no-progress timeouts are the real safety net.
- **Google Drive uploads are slow and rate-limited.** Treat them as optional plumbing — the local artifact collection in [batch/artifact_collector.py](batch/artifact_collector.py) is the source of truth, the Drive uploader is just a mirror for sharing.
- **VM orchestration assumes specific VM state.** Each VM has GTA San Andreas, MTA, the sv2l resource, and a checkout of this repo, configured per [vmware_config.yaml](vmware_config.yaml). When a VM run fails mysteriously, the first suspect is configuration drift on that specific VM.
- **Root-cause analysis discipline**: when a generation or simulation fails, read the local artifact collector's output (the per-run JSON dumps) and the MTA `server.log` / `clientscript.log` line by line before changing anything. Most "the pipeline is broken" reports turn out to be one specific GEST that's slightly malformed, not a systemic regression.

## Companion documentation

- [data/documentation/gest_instructions.md](data/documentation/gest_instructions.md) — the natural-language GEST specification. Source of truth for what is and isn't a valid GEST.
- [examples/reference_graphs/](examples/reference_graphs/) — reference GESTs used as fixtures and as test inputs.
- [mta-sim repository](https://github.com/ncudlenco/mta-sim) — the simulation engine these scripts drive. When debugging a "valid GEST that won't simulate" failure, the answer is usually in the mta-sim source rather than here.
