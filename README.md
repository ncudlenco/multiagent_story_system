# multiagent_story_system

<p align="center">
  <a href="https://github.com/ncudlenco/multiagent_story_system/releases"><img src="https://img.shields.io/github/v/release/ncudlenco/multiagent_story_system?include_prereleases&style=for-the-badge" alt="Latest release"></a>
  <a href="https://github.com/ncudlenco/multiagent_story_system/stargazers"><img src="https://img.shields.io/github/stars/ncudlenco/multiagent_story_system?style=for-the-badge&logo=github" alt="GitHub stars"></a>
  <a href="https://github.com/ncudlenco/multiagent_story_system/network/members"><img src="https://img.shields.io/github/forks/ncudlenco/multiagent_story_system?style=for-the-badge&logo=github" alt="GitHub forks"></a>
  <a href="https://github.com/ncudlenco/multiagent_story_system/watchers"><img src="https://img.shields.io/github/watchers/ncudlenco/multiagent_story_system?style=for-the-badge&logo=github" alt="GitHub watchers"></a>
</p>

Python tools for procedurally generating Graph of Events in Space and Time (GEST) specifications and executing them deterministically with the [GEST-Engine (mta-sim)](https://github.com/ncudlenco/mta-sim) — either **locally on a single machine** or **in parallel across many VMware Workstation Pro worker VMs**.

This repository contains:

- **`simple_gest_random_generator.py`** — a procedural random GEST generator that reads the engine's capability registry and produces executable GESTs by construction.
- **`main.py`** — single-story / maintenance CLI for one-off operations (export the engine's capability registry, simulate a stored story, configuration utilities). Also exposes the LLM-based concept / casting / detail generation pipeline (see note below).
- **`batch_generate.py`** — local batch runner that drives the GEST-Engine on the host machine. Useful for small batches, smoke tests, debugging, or any setup without VMware.
- **`vmware_orchestrator.py`** — VMware Workstation Pro orchestrator that clones worker VMs from a master snapshot, distributes generation jobs, monitors worker health with auto-restart, merges artifacts, and uploads results to Google Drive. Used for corpus-level production.

> **Two generator backends.** The `simple_random` generator produces GESTs that are **executable by construction**: every action is sampled from the engine's capability registry, every chain follows a valid POI sequence, and every story succeeds in the engine (modulo the engine's own bugs). The `llm` generator produces GESTs that are **narratively richer but not simulatable** out of the box — it operates at a higher semantic level, ignoring the engine's exact action vocabulary and constraints. The released ICLR checkpoint and the GTASA-01 corpus were produced exclusively with `simple_random`. Use `llm` only when you want semantically structured graphs for downstream non-simulation purposes.

## Paper

This system is described in the ICLR 2026 Workshop paper:

> N.~Cudlenco, M.~Masala, M.~Leordeanu. **[Tiny Paper] GEST-Engine: Controllable Multi-Actor Video Synthesis with Perfect Spatiotemporal Annotations.** *ICLR 2026 the 2nd Workshop on World Models: Understanding, Modelling and Scaling.* [OpenReview](https://openreview.net/forum?id=uUofPYVMZH)

The sample corpus of **398 procedurally generated multi-actor stories** produced with this orchestrator is publicly available on HuggingFace: [**nnc-001/gtasa-01**](https://huggingface.co/datasets/nnc-001/gtasa-01).

## Checkpoints

| Tag | Date | Reference |
|---|---|---|
| [`v1.0-iclr2026`](https://github.com/ncudlenco/multiagent_story_system/releases/tag/v1.0-iclr2026) | March 2026 | ICLR 2026 Workshop Tiny Paper — state used to generate the GTASA-01 sample corpus |

Future checkpoints will be listed here as the system evolves.

## Requirements

### Common (both local and VM modes)

- **Windows 10 / 11**
- **Python 3.10+**
- **GTA San Andreas PC v1.0 + MTA 1.6 + the `sv2l` resource**, installed and configured following the [mta-sim installation guide](https://github.com/ncudlenco/mta-sim#installation).
- **Google Drive API credentials** — optional, only required if you pass `--google-drive-folder` / `--output-g-drive` to upload results.

### Additional requirements for VM mode only

- **VMware Workstation Pro 25H2** — free for personal, educational, and commercial use as of November 11, 2024. Release notes and download: [VMware Workstation Pro 25H2](https://techdocs.broadcom.com/us/en/vmware-cis/desktop-hypervisors/workstation-pro/25H2/release-notes/vmware-workstation-pro-25h2-release-notes.html).
- **A pre-built master VM image** containing Windows 10/11, GTA San Andreas, MTA 1.6, and the `sv2l` resource. **This image is not distributed** with the repository because it requires a legitimate Windows license and a legitimate GTA San Andreas license; each user must build their own.

### Known limitation (VM mode): Hyper-V / WSL conflict

VMware Workstation Pro cannot run VMs on a machine where Hyper-V is active. **If WSL (Windows Subsystem for Linux) or any other Hyper-V–based feature is enabled on the host, you must disable the hypervisor before running the VM orchestrator:**

```powershell
# Run as Administrator
bcdedit /set hypervisorlaunchtype off
# Reboot the machine
```

To re-enable WSL afterwards:

```powershell
# Run as Administrator
bcdedit /set hypervisorlaunchtype auto
# Reboot the machine
```

This is a Windows/VMware compatibility issue, not a limitation of this system.

## Installation

### 1. Clone and install Python dependencies

```powershell
git clone https://github.com/ncudlenco/multiagent_story_system.git
cd multiagent_story_system
pip install -r requirements.txt
```

### 2. Install the GEST-Engine

Follow the [mta-sim installation instructions](https://github.com/ncudlenco/mta-sim#installation) to install GTA San Andreas, MTA 1.6, and the `sv2l` resource. This step is required for **both** local and VM modes.

### 3a. Local mode setup

For local single-machine execution, no further setup is needed beyond step 2. Use `main.py` and `batch_generate.py` directly on the host.

### 3b. VM mode setup

Install [VMware Workstation Pro 25H2](https://techdocs.broadcom.com/us/en/vmware-cis/desktop-hypervisors/workstation-pro/25H2/release-notes/vmware-workstation-pro-25h2-release-notes.html). If Hyper-V / WSL is enabled on the host, disable it first (see the limitation note above).

Create a Windows 10/11 master VM containing a working installation of GTA San Andreas, MTA 1.6, and the `sv2l` resource (i.e. complete step 2 inside the guest). The orchestrator on the host invokes scripts from this repository inside the guest via `vmrun runProgramInGuest` to drive the MTA server + client for each input graph.

**Recommended VM hardware** (these match the configuration used to produce GTASA-01):

| Resource | Value |
|---|---|
| Memory | 3 GB |
| Processors | 2 |
| Cores per processor | 1 |
| Hard disk | 200 GB (NVMe) |
| Network adapter | NAT |
| USB controller | Present |
| Sound card | Auto detect |
| Display — number of monitors | 1 |
| Display — maximum resolution | 1280 × 720 |
| Display — **3D graphics acceleration** | **Enabled (required)** |
| Display — graphics memory | 8 GB (recommended by VMware) |
| Virtualization engine | Virtualize Intel VT-x/EPT or AMD-V/RVI ✓ |

GTA San Andreas requires Direct3D 9 acceleration, so the VM **must** have *Display → Accelerate 3D graphics* enabled. The display must also be set to a **single** monitor — multi-monitor configurations confuse the screenshot capture pipeline and prevent reliable simulation.

**Auto-start the worker on logon.** The orchestrator on the host signals each worker by writing a configuration file into a shared folder; inside the guest, `vm_auto_runner.py` watches that file and drives the per-story simulation loop. For this to work, `vm_auto_runner.py` must be **running automatically when the guest user logs in** — the orchestrator only powers the VM on, it does not log in or open a terminal.

Set this up by creating a Task Scheduler entry in the guest with the following definition. Save it as `VMAutoRunner.xml` and import it via *Task Scheduler → Action → Import Task…*, or via PowerShell with `Register-ScheduledTask -Xml (Get-Content VMAutoRunner.xml | Out-String) -TaskName "VMAutoRunner"`.

```xml
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Reads the configuration for the worker from the shared folder on the host and starts simulation and collection.</Description>
    <URI>\VMAutoRunner</URI>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>true</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>true</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT72H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>-NoExit -Command "python 'C:\mta1.6\server\mods\deathmatch\resources\multiagent_story_system\vm_auto_runner.py'"</Arguments>
    </Exec>
  </Actions>
</Task>
```

Adjust the `<Arguments>` path if you cloned the repository to a different location inside the guest. Make sure the task is set to *run with highest privileges* so it can launch MTA, and that the trigger is *At log on* — `LogonTrigger` is what makes the runner come up automatically every time the orchestrator powers the worker VM on.

The default Windows account in the example task is `user`, matching `guest_os.username` in `vmware_config.yaml`. If you use a different guest username, edit the `<UserId>` field (or remove it and re-import; Task Scheduler will fill it in based on whoever imports the XML).

Once the VM is configured, the auto-runner starts on logon, and a manual end-to-end run succeeds, take a snapshot. The default snapshot name in `vmware_config.yaml` is `Ready`.

Edit `vmware_config.yaml` to match your VMware layout and guest credentials:

```yaml
vmware:
  master_vm_path: "W:\\VMs\\2\\Windows 10 and later x64.vmx"
  master_snapshot: "Ready"
  workers_dir: "W:\\VMs\\workers"
  vmrun_paths:
    - "C:\\Program Files (x86)\\VMware\\VMware Workstation\\vmrun.exe"
    - "C:\\Program Files\\VMware\\VMware Workstation\\vmrun.exe"
  guest_os:
    username: "user"
    password: "user"
    work_dir: "C:\\mta1.6\\server\\mods\\deathmatch\\resources\\multiagent_story_system"
```

### 4. Capability registry

The repository ships a pre-generated `data/simulation_environment_capabilities.json` that has been **manually curated** and is the version used by all released checkpoints. **Use it as-is** unless you have a specific reason to regenerate it — the hand edits fix inconsistencies that a fresh export does not.

A fresh capability registry can be produced by running the engine with `"EXPORT_MODE": true` in its `config.json` (see the [mta-sim README](https://github.com/ncudlenco/mta-sim#usage)) or by running `python main.py --export-capabilities`. A raw export will likely not be fully compatible without additional manual fixups.

## Usage

There are three command-line entry points: `main.py` for one-off operations and maintenance, `batch_generate.py` for local batches, and `vmware_orchestrator.py` for VM batches.

Replace `<YOUR_GDRIVE_FOLDER_ID>` in any example with the ID of a Google Drive folder you own, or omit the flag entirely to keep results local.

### Example: produce the GTASA-01–style corpus across 25 VMs

```powershell
python .\vmware_orchestrator.py `
    --num-vms 25 `
    --stories-per-vm 20 `
    --random-chains-per-actor 2 `
    --random-max-actors-per-region 5 `
    --random-max-regions 2 `
    --generate-description prompt `
    --generator-type simple_random `
    --simulation-retries 0 `
    --google-drive-folder <YOUR_GDRIVE_FOLDER_ID> `
    --ensure-target `
    --no-monitor
```

### Example: same generation locally on a single machine

```powershell
python .\batch_generate.py `
    --output-folder local_batch_out\ `
    --story-number 20 `
    --generator-type simple_random `
    --random-chains-per-actor 2 `
    --random-max-actors-per-region 5 `
    --random-max-regions 2 `
    --generate-description prompt `
    --collect-simulation-artifacts `
    --simulation-retries 0 `
    --ensure-target
```

### Example: export the engine capability registry

```powershell
python .\main.py --export-capabilities
```

### Example: re-simulate a stored story by ID

```powershell
python .\main.py --simulate 22597965 --collect-artifacts
```

---

### `main.py` — single-story / maintenance CLI

| Flag | Type | Default | Description |
|---|---|---|---|
| `--export-capabilities` | flag | — | Export the engine's capability registry from MTA. |
| `--preprocess-capabilities`, `--preprocess` | flag | — | Preprocess the exported capabilities into optimized cache files using an LLM. |
| `--invalidate-capabilities-cache` | flag | — | Invalidate any existing capabilities cache files before preprocessing. |
| `--skip-episodes` | flag | — | Skip episode summarization (faster, optional data). |
| `--generate` | flag | — | Generate a story using the (LLM-based) Concept + Casting agents. |
| `--max-num-protagonists` | int | `2` | Maximum number of protagonist actors. Use `-1` to let the LLM decide. |
| `--max-num-extras` | int | `0` | Maximum number of background / extra actors. Use `-1` to let the LLM decide. |
| `--num-actions` | int | `5` | Number of distinct action types to use. |
| `--seeds` | str... | `[]` | Narrative seed sentences (space-separated, in quotes). |
| `--from-text-file FILE` | path | — | Read narrative seeds from a text file (one sentence per line). Overrides `--seeds`. |
| `--stop-phase` | int | — | Number of generation phases to run (default: all). |
| `--scene-number` | int | `0` | How many scenes to generate. |
| `--resume STORY_ID` | str | — | Resume an existing story from checkpoint (8-character UUID). |
| `--from-phase {1,2,3}` | int | — | Phase to resume from when using `--resume` (1=concept, 2=casting, 3=detail). |
| `--resume-from-stage {1..5}` | int | — | Resume the reactive detail workflow from stage N (1=grounding, 2=segmentation, 3=setup, 4=screenplay, 5=translation). Requires `--use-react`. |
| `--use-cached-detail` | flag | — | Use cached detail expansions if available (only for the detail phase). |
| `--use-react` | flag | — | Use the reactive (tool-based) detail workflow instead of the standard one. |
| `--simulate STORY_ID` | str | — | Simulate an existing story in MTA (8-character UUID). |
| `--scene SCENE_ID` | str | — | Specific scene to simulate. Requires `--simulate`. |
| `--timeout SECONDS` | int | `3600` | Simulation timeout in seconds. |
| `--collect-artifacts` | flag | `false` | Enable artifact collection during simulation (videos, logs, spatial relations, segmentation, …). |
| `--capture-segmentations` / `--no-capture-segmentations` | flag | enabled | Toggle per-frame instance segmentation capture. |
| `--save-prompts` | flag | — | Save all LLM prompts and responses during story generation. |
| `--save-raw-responses` | flag | — | Include raw LLM responses in the prompt logs (verbose; requires `--save-prompts`). |
| `--config FILE` | path | `config.yaml` | Path to the configuration file. |
| `--verbose` | flag | — | Enable verbose (DEBUG) logging. |

---

### `batch_generate.py` — local batch runner

**Mode selection** (mutually exclusive — pick one):

| Flag | Type | Description |
|---|---|---|
| `--story-number N` | int | Generate `N` stories. |
| `--from-existing-stories PATH` | path | Path to a folder of existing stories to (re-)simulate. |
| `--from-text-files JSON_FILE` | path | Path to a JSON file listing text files to convert into stories. |
| `--resume-batch BATCH_ID` | str | Resume an interrupted batch (e.g. `batch_20231103_143022`). |
| `--reset-failed BATCH_ID` | str | Reset all failed stories in a batch and clear simulation artifacts. |
| `--reset-success BATCH_ID` | str | Reset all successful stories in a batch and clear simulation artifacts. |
| `--reset-simulations BATCH_ID` | str | Reset **all** simulations (success + failed) and clear all simulation artifacts. |

**General arguments**:

| Flag | Type | Default | Description |
|---|---|---|---|
| `--output-folder PATH` | path | — | Output folder for batch results. Required unless using a reset / resume mode. |
| `--retry-story STORY_ID` | str | — | Retry simulations for a specific story. Requires `--resume-batch`. |
| `--take TAKE_NUMBER` | int | — | Specific take to retry. Requires `--retry-story`. |
| `--config FILE` | path | `config.yaml` | Path to the configuration file. |
| `--verbose` | flag | — | Enable verbose (DEBUG) logging. |

**Story generation parameters**:

| Flag | Type | Default | Description |
|---|---|---|---|
| `--num-actors` | int | `2` | Number of protagonist actors. |
| `--num-extras` | int | `1` | Number of extra / background actors. |
| `--num-actions` | int | `5` | Number of distinct actions. |
| `--scene-number` | int | `4` | Number of scenes per story. |
| `--seeds` | str... | `[]` | Narrative seed sentences. |
| `--generator-type {llm,simple_random}` | str | `llm` | Story generator. **Use `simple_random` for procedural execution-by-construction generation.** |
| `--random-chains-per-actor` | int | `3` | Number of action chains per actor (`simple_random` only). |
| `--random-max-actors-per-region` | int | unlimited | Maximum actors per region (`simple_random` only). |
| `--random-max-regions` | int | unlimited | Maximum number of regions visited (`simple_random` only). |
| `--random-seed` | int | — | Random seed for reproducibility (`simple_random` only). |
| `--episode-type {classroom,gym,garden,house}` | str | random | Constrain to a single episode type (`simple_random` only). |
| `--ensure-target` | flag | — | Keep generating until the target number of *successful* stories is reached. |
| `--parallel-workers N` | int | `1` | Number of parallel workers (only used in text-file mode). |
| `--skip-simulation` | flag | — | Skip the MTA simulation phase (generation only, for text-file mode). |

**Variation parameters**:

| Flag | Type | Default | Description |
|---|---|---|---|
| `--same-story-generation-variations` | int | `1` | Number of detail variations per story. |
| `--same-story-simulation-variations` | int | `1` | Number of simulation runs per detail variation. |

**Retry / timeout parameters**:

| Flag | Type | Default | Description |
|---|---|---|---|
| `--generation-retries` | int | `3` | Maximum generation retry attempts. |
| `--simulation-retries` | int | `3` | Maximum simulation retry attempts. |
| `--simulation-timeout` | int | `3600` | Simulation timeout in seconds. |

**Artifact / output parameters**:

| Flag | Type | Default | Description |
|---|---|---|---|
| `--collect-simulation-artifacts` | flag | `false` | Enable artifact collection during simulations (videos, logs, spatial relations, segmentation, …). |
| `--capture-segmentations` / `--no-capture-segmentations` | flag | enabled | Toggle per-frame instance segmentation capture. |
| `--generate-description {prompt,full}` | str | — | Write the GPT-4o prompt only (`prompt`) or also call the OpenAI API in-line (`full`). |
| `--output-g-drive FOLDER_ID` | str | — | Google Drive folder ID for upload. |
| `--keep-local` | flag | — | Keep the local copy after Google Drive upload. |
| `--force` | flag | — | Force overwrite if the output folder already exists. |

---

### `vmware_orchestrator.py` — VMware batch orchestrator

**Mode selection** (mutually exclusive — pick one):

| Flag | Description |
|---|---|
| `--num-vms N` | Spawn `N` worker VMs and run a batch (requires `--stories-per-vm`). |
| `--update-master` | Update the master VM by pulling the latest code from GitHub repositories and refreshing the `Ready` snapshot. |
| `--purge-vms` | Delete **all** previous worker VM batch folders from disk. |
| `--merge-gdrive-results FOLDER_ID` | Merge per-worker subfolders inside a Google Drive folder into a flat layout (standalone, no VMs). |
| `--merge-flat-folders DEST_ID SRC_ID...` | Merge several flat Google Drive folders into one destination (first ID is the destination). |

**Common arguments**:

| Flag | Type | Default | Description |
|---|---|---|---|
| `--stories-per-vm N` | int | — | Target number of successful stories per worker VM. Required with `--num-vms`. |
| `--config FILE` | path | `vmware_config.yaml` | Path to the orchestrator config. |

**Code-sync arguments** (used with `--update-master`):

| Flag | Description |
|---|---|
| `--github-token TOKEN` | GitHub personal access token (or set `GITHUB_TOKEN` env var). |
| `--purge` | Completely remove repository directories before cloning. |
| `--skip-snapshot` | Don't update the `Ready` snapshot after sync. |
| `--refresh-google-token` | Delete the cached Google token and re-authenticate. |

**Story generation parameters** (override defaults from config):

| Flag | Type | Default | Description |
|---|---|---|---|
| `--num-actors` | int | — | Number of protagonist actors. |
| `--num-extras` | int | — | Number of extra / background actors. |
| `--num-actions` | int | — | Number of distinct actions. |
| `--scene-number` | int | — | Number of scenes per story. |
| `--same-story-generation-variations` | int | — | Detail variations per story. |
| `--same-story-simulation-variations` | int | — | Independent engine simulations per generated GEST. |
| `--ensure-target` | flag | — | Keep generating stories until the target number of successful stories is reached per worker. |
| `--simulation-retries` | int | `3` | Number of retries per failed simulation. |

**Generator selection**:

| Flag | Type | Default | Description |
|---|---|---|---|
| `--generator-type {llm,simple_random}` | str | `llm` | Story generator. **Use `simple_random` for procedural execution-by-construction generation.** |
| `--random-chains-per-actor` | int | — | Action chains per actor (`simple_random` only). |
| `--random-max-actors-per-region` | int | — | Max actors per region (`simple_random` only). |
| `--random-max-regions` | int | — | Max regions to visit (`simple_random` only). |
| `--random-seed` | int | — | Random seed (`simple_random` only). |
| `--episode-type {classroom,gym,garden,house}` | str | — | Constrain to a single episode type (`simple_random` only). |

**Description / capture / statistics**:

| Flag | Type | Default | Description |
|---|---|---|---|
| `--generate-description {prompt,full}` | str | — | Write the GPT-4o prompt only (`prompt`) or also call the OpenAI API in-line (`full`). |
| `--capture-segmentations` / `--no-capture-segmentations` | flag | enabled | Toggle per-frame instance segmentation capture. |
| `--no-count-segmentations` | flag | — | Skip counting segmentation frames in post-run statistics. |
| `--no-count-spatial` | flag | — | Skip counting spatial relations in post-run statistics. |

**Google Drive**:

| Flag | Description |
|---|---|
| `--google-drive-folder FOLDER_ID` | Google Drive parent folder ID for uploads. |
| `--keep-local` | Keep local copies after Google Drive upload. |
| `--merge-gdrive` | Merge all per-worker Google Drive folders into a single batch folder after completion. |

**Worker control / debugging**:

| Flag | Description |
|---|---|
| `--no-restart` | Don't restart crashed/hung workers — show errors and fail fast. |
| `--no-monitor` | Fire-and-forget: launch workers and exit without monitoring. |
| `--keep-vms` | Keep worker VMs after completion (for debugging). |

## Key files

- **`main.py`** — single-story / maintenance entry point.
- **`batch_generate.py`** — local batch runner (no VMs).
- **`vmware_orchestrator.py`** — VMware batch orchestrator (parallel VMs).
- **`vm_auto_runner.py`** — script that runs inside each worker VM; drives the MTA server + client and the `sv2l` resource for each input graph.
- **`vm_monitor.py`** — heartbeat-based worker health monitoring and auto-restart.
- **`simple_gest_random_generator.py`** — procedural GEST generator implementation.
- **`gdrive_manager.py`** — Google Drive upload, folder management, and merging.
- **`gdrive_statistics.py`** / **`download_gdrive_folders.py`** — post-run statistics and retrieval helpers.
- **`vmware_config.yaml`** — VMware paths, snapshot name, worker limits, guest credentials.
- **`config.yaml`** — common configuration (paths, logging, optional OpenAI settings).
- **`data/simulation_environment_capabilities.json`** — pre-generated, manually curated capability registry consumed by the generator.

## Star History

<a href="https://www.star-history.com/#ncudlenco/multiagent_story_system&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=ncudlenco/multiagent_story_system&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=ncudlenco/multiagent_story_system&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=ncudlenco/multiagent_story_system&type=Date" />
 </picture>
</a>

## Citation

If you use this orchestrator in your research, please cite the ICLR 2026 Tiny Paper:

```bibtex
@inproceedings{cudlenco2026tiny,
  title={[Tiny Paper] {GEST}-Engine: Controllable Multi-Actor Video Synthesis with Perfect Spatiotemporal Annotations},
  author={Nicolae Cudlenco and Mihai Masala and Marius Leordeanu},
  booktitle={ICLR 2026 the 2nd Workshop on World Models: Understanding, Modelling and Scaling},
  year={2026},
  url={https://openreview.net/forum?id=uUofPYVMZH}
}
```

## License

See [`LICENSE`](LICENSE). Use of the system requires a licensed copy of Windows and a licensed copy of GTA San Andreas; Rockstar Games / Take-Two Interactive retain all rights to in-game assets. Research data derived from this system is released for non-commercial academic research only.

## Contact

Open an [issue](https://github.com/ncudlenco/multiagent_story_system/issues) or email `nicolae.cudlenco@gmail.com`.
