param(
    [string]$Project = "model-shelter-513813-v8",
    [string]$Zone = "us-central1-a",
    [string]$Instance = "swebench-pro-v2-runner",
    [string]$User = "swebench",
    [int]$PollSeconds = 60,
    [switch]$Once
)

$ErrorActionPreference = 'Stop'
$projectRoot = 'D:\big-supervise-big'
$syncScript = Join-Path $projectRoot 'scripts\sync_every_10.ps1'
$statePath = Join-Path $projectRoot 'synced_data\SYNCED_MILESTONES.json'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

$synced = New-Object 'System.Collections.Generic.List[string]'
if (Test-Path -LiteralPath $statePath) {
    $parsed = ([IO.File]::ReadAllText($statePath, [Text.Encoding]::UTF8) | ConvertFrom-Json)
    foreach ($item in $parsed) {
        if ($null -ne $item -and [string]$item -ne '') {
            [void]$synced.Add([string]$item)
        }
    }
}

while ($true) {
    try {
        $remoteCmd = "ls -1 /home/swebench/big-supervise-big-exp/sync_checkpoints"
        $out = & gcloud compute ssh "$User@$Instance" --project=$Project --zone=$Zone --quiet --ssh-flag="-batch" --command=$remoteCmd
        if ($LASTEXITCODE -eq 0 -and $out) {
            $milestones = @(
                $out |
                    ForEach-Object { ([string]$_).Trim() } |
                    Where-Object { $_ -match '^(.+)\.json$' } |
                    ForEach-Object { $Matches[1] } |
                    Sort-Object
            )
            foreach ($m in $milestones) {
                if (-not $synced.Contains($m)) {
                    Write-Output "New 10-task milestone detected: $m. Starting sync..."
                    & $syncScript -Project $Project -Zone $Zone -Instance $Instance -User $User -Milestone $m
                    [void]$synced.Add($m)
                    [IO.Directory]::CreateDirectory((Split-Path -Parent $statePath)) | Out-Null
                    $json = ConvertTo-Json -InputObject @($synced.ToArray())
                    [IO.File]::WriteAllText($statePath, $json, $utf8NoBom)
                    if ($m -eq "pass_at_1_task_28") {
                        Write-Output "pass@1 final data synced to local!"
                    }
                }
            }
        }
    } catch {
        Write-Output "Watcher poll failed (will retry): $_"
    }
    if ($Once) { break }
    Start-Sleep -Seconds $PollSeconds
}
