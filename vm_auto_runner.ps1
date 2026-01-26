# VM Auto Runner - PowerShell version
# Runs on VM startup via Task Scheduler
# This script replaces the Python subprocess orchestration to avoid Windows path escaping issues

param(
    [string]$JobConfigPath = "\\vmware-host\Shared Folders\job\worker_job.yaml",
    [string]$WorkDir = "C:\mta1.6\server\mods\deathmatch\resources\multiagent_story_system",
    [int]$StartupDelay = 30
)

# Create logs directory if needed
$LogDir = "$WorkDir\logs"
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

$LogFile = "$LogDir\vm_auto_runner_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $logLine = "[$timestamp] [$Level] $Message"
    $logLine | Tee-Object -FilePath $LogFile -Append
}

function Parse-SimpleYaml {
    param([string]$Path)

    $config = @{}
    if (-not (Test-Path $Path)) {
        return $config
    }

    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        # Skip comments and empty lines
        if ($line -and -not $line.StartsWith('#')) {
            if ($line -match '^\s*(\w+):\s*(.*)$') {
                $key = $Matches[1]
                $value = $Matches[2].Trim().Trim('"').Trim("'")
                # Handle boolean values
                if ($value -eq 'true') { $value = $true }
                elseif ($value -eq 'false') { $value = $false }
                $config[$key] = $value
            }
        }
    }
    return $config
}

# ============================================================================
# MAIN SCRIPT
# ============================================================================

Write-Log "=========================================="
Write-Log "VM Auto Runner (PowerShell) Starting"
Write-Log "WorkDir: $WorkDir"
Write-Log "JobConfigPath: $JobConfigPath"
Write-Log "=========================================="

# Wait for shared folders to be available
Write-Log "Waiting $StartupDelay seconds for shared folders to mount..."
Start-Sleep -Seconds $StartupDelay

# Check if job config exists
if (-not (Test-Path $JobConfigPath)) {
    Write-Log "No job config found at $JobConfigPath - exiting" "WARN"
    Write-Log "This VM may have been started manually (not by orchestrator)"
    exit 0
}

Write-Log "Found job config at $JobConfigPath"

# Map network drive O: to avoid UNC path issues
Write-Log "Mapping O: drive to VMware shared folder..."

# First, disconnect any existing O: mapping
$deleteResult = net use O: /delete /y 2>&1
Write-Log "Drive O: disconnect result: $deleteResult"

# Map the shared folder
$mapResult = net use O: "\\vmware-host\Shared Folders\output" /persistent:no 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Log "ERROR: Failed to map O: drive: $mapResult" "ERROR"
    exit 1
}
Write-Log "Successfully mapped O: drive to \\vmware-host\Shared Folders\output"

# Parse YAML job config
Write-Log "Parsing job configuration..."
$jobConfig = Parse-SimpleYaml -Path $JobConfigPath

# Log parsed config
Write-Log "Job config loaded:"
Write-Log "  batch_id: $($jobConfig['batch_id'])"
Write-Log "  worker_id: $($jobConfig['worker_id'])"
Write-Log "  story_number: $($jobConfig['story_number'])"
Write-Log "  num_actors: $($jobConfig['num_actors'])"
Write-Log "  num_extras: $($jobConfig['num_extras'])"
Write-Log "  generator_type: $($jobConfig['generator_type'])"
Write-Log "  episode_type: $($jobConfig['episode_type'])"
Write-Log "  simulation_timeout: $($jobConfig['simulation_timeout'])"

# Build batch_generate.py command arguments
# IMPORTANT: Use "O:" without trailing backslash to avoid escaping issues
$pythonArgs = @(
    "batch_generate.py"
    "--output-folder", "O:"
    "--story-number", $jobConfig['story_number']
)

# Add optional arguments if present
if ($jobConfig['num_actors']) {
    $pythonArgs += "--num-actors"
    $pythonArgs += $jobConfig['num_actors']
}

if ($jobConfig['num_extras']) {
    $pythonArgs += "--num-extras"
    $pythonArgs += $jobConfig['num_extras']
}

if ($jobConfig['generator_type']) {
    $pythonArgs += "--generator-type"
    $pythonArgs += $jobConfig['generator_type']
}

if ($jobConfig['episode_type']) {
    $pythonArgs += "--episode-type"
    $pythonArgs += $jobConfig['episode_type']
}

if ($jobConfig['simulation_timeout']) {
    $pythonArgs += "--simulation-timeout"
    $pythonArgs += $jobConfig['simulation_timeout']
}

if ($jobConfig['collect_simulation_artifacts'] -eq $true) {
    $pythonArgs += "--collect-simulation-artifacts"
}

if ($jobConfig['google_drive_folder_id']) {
    $pythonArgs += "--output-g-drive"
    $pythonArgs += $jobConfig['google_drive_folder_id']

    if ($jobConfig['keep_local'] -eq $true) {
        $pythonArgs += "--keep-local"
    }
}

if ($jobConfig['force'] -eq $true) {
    $pythonArgs += "--force"
}

# Additional story parameters
if ($jobConfig['num_actions']) {
    $pythonArgs += "--num-actions"
    $pythonArgs += $jobConfig['num_actions']
}

if ($jobConfig['scene_number']) {
    $pythonArgs += "--scene-number"
    $pythonArgs += $jobConfig['scene_number']
}

# Variation parameters
if ($jobConfig['same_story_generation_variations']) {
    $pythonArgs += "--same-story-generation-variations"
    $pythonArgs += $jobConfig['same_story_generation_variations']
}

if ($jobConfig['same_story_simulation_variations']) {
    $pythonArgs += "--same-story-simulation-variations"
    $pythonArgs += $jobConfig['same_story_simulation_variations']
}

# Retry parameters
if ($jobConfig['generation_retries']) {
    $pythonArgs += "--generation-retries"
    $pythonArgs += $jobConfig['generation_retries']
}

if ($jobConfig['simulation_retries']) {
    $pythonArgs += "--simulation-retries"
    $pythonArgs += $jobConfig['simulation_retries']
}

# Simple random generator parameters
if ($jobConfig['random_chains_per_actor']) {
    $pythonArgs += "--random-chains-per-actor"
    $pythonArgs += $jobConfig['random_chains_per_actor']
}

if ($jobConfig['random_max_actors_per_region']) {
    $pythonArgs += "--random-max-actors-per-region"
    $pythonArgs += $jobConfig['random_max_actors_per_region']
}

if ($jobConfig['random_max_regions']) {
    $pythonArgs += "--random-max-regions"
    $pythonArgs += $jobConfig['random_max_regions']
}

# Description generation mode
if ($jobConfig['generate_description']) {
    $pythonArgs += "--generate-description"
    $pythonArgs += $jobConfig['generate_description']
}

# Ensure target number of successful stories
if ($jobConfig['ensure_target'] -eq $true) {
    $pythonArgs += "--ensure-target"
}

# Log the command we're about to run
$cmdString = "python " + ($pythonArgs -join ' ')
Write-Log "Executing command: $cmdString"
Write-Log "Working directory: $WorkDir"

# Change to work directory
Set-Location $WorkDir

# Run batch_generate.py
# Using Start-Process with -Wait to run synchronously
# -NoNewWindow keeps output in same console
$startTime = Get-Date
Write-Log "Starting batch_generate.py at $startTime"

try {
    # Run Python directly (not through subprocess.Popen which causes escaping issues)
    $process = Start-Process -FilePath "python" -ArgumentList $pythonArgs -NoNewWindow -Wait -PassThru
    $exitCode = $process.ExitCode
}
catch {
    Write-Log "ERROR: Failed to start batch_generate.py: $_" "ERROR"
    $exitCode = -1
}

$endTime = Get-Date
$duration = $endTime - $startTime
Write-Log "batch_generate.py completed with exit code: $exitCode"
Write-Log "Duration: $($duration.ToString())"

# Write completion marker to shared folder
$completionData = @{
    worker_id = [int]$jobConfig['worker_id']
    batch_id = $jobConfig['batch_id']
    exit_code = $exitCode
    completed_at = (Get-Date -Format "o")
    duration_seconds = [int]$duration.TotalSeconds
}

$completionJson = $completionData | ConvertTo-Json -Compress
$completionPath = "O:\worker_complete.json"

try {
    $completionJson | Out-File -FilePath $completionPath -Encoding UTF8 -Force
    Write-Log "Wrote completion marker to $completionPath"
}
catch {
    Write-Log "ERROR: Failed to write completion marker: $_" "ERROR"
}

# Shutdown if configured (default: yes)
$shutdownOnComplete = $jobConfig['shutdown_on_complete']
if ($shutdownOnComplete -ne $false) {
    Write-Log "Scheduling shutdown in 60 seconds..."
    shutdown /s /t 60 /c "VM Auto Runner: Batch generation complete (exit code: $exitCode)"
}
else {
    Write-Log "shutdown_on_complete is false - VM will remain running"
}

Write-Log "VM Auto Runner completed. Exit code: $exitCode"
exit $exitCode
