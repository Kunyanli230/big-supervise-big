param(
    [string]$Project = "model-shelter-513813-v8",
    [string]$Zone = "us-central1-a",
    [string]$Instance = "swebench-pro-v2-runner",
    [string]$User = "swebench",
    [string]$RemoteExpRoot = "/home/swebench/big-supervise-big-exp",
    [string]$RemoteSnapDir = "/home/swebench/snapshots",
    [string]$Milestone = "batch10"
)

$ErrorActionPreference = 'Stop'
$projectRoot = 'D:\big-supervise-big'
$syncRoot = Join-Path $projectRoot 'synced_data'
$archivesDir = Join-Path $syncRoot 'archives'
$snapshotsDir = Join-Path $syncRoot 'snapshots'
$latestDir = Join-Path $syncRoot 'latest'
$utf8 = New-Object System.Text.UTF8Encoding($false)

[IO.Directory]::CreateDirectory($syncRoot) | Out-Null
[IO.Directory]::CreateDirectory($archivesDir) | Out-Null
[IO.Directory]::CreateDirectory($snapshotsDir) | Out-Null

$remoteCmd = "/home/swebench/big-supervise-big/.venv/bin/python /home/swebench/big-supervise-big/scripts/export_snapshot.py --exp-root $RemoteExpRoot --out-dir $RemoteSnapDir --milestone $Milestone"
$metaText = & gcloud compute ssh "$User@$Instance" --project=$Project --zone=$Zone --quiet --ssh-flag="-batch" --command=$remoteCmd
if ($LASTEXITCODE -ne 0) { throw "Remote snapshot export failed." }

$meta = (($metaText -join "`n") | ConvertFrom-Json)
if ($meta.id -notmatch '^\d{8}T\d{6}Z$') { throw "Unexpected snapshot id: $($meta.id)" }

$archiveName = "bsb-pro-v2-$($meta.id).tar.gz"
$archivePath = Join-Path $archivesDir $archiveName

Push-Location -LiteralPath $syncRoot
try {
    & gcloud compute scp ("$User@${Instance}:" + $meta.archive) ("archives/" + $archiveName) --project=$Project --zone=$Zone --quiet --scp-flag="-batch"
    if ($LASTEXITCODE -ne 0) { throw "Archive download failed." }
} finally {
    Pop-Location
}

$archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($archiveHash -ne $meta.sha256) {
    throw "Archive SHA256 mismatch: expected $($meta.sha256), got $archiveHash"
}

$snapshotDir = Join-Path $snapshotsDir $meta.id
[IO.Directory]::CreateDirectory($snapshotDir) | Out-Null
& tar.exe -xzf $archivePath -C ('\\?\' + $snapshotDir)
if ($LASTEXITCODE -ne 0) { throw "Archive extraction failed." }

$dataDir = Join-Path $snapshotDir 'snapshot'
$manifestPath = Join-Path $dataDir 'file-manifest.json'
$manifest = ([IO.File]::ReadAllText($manifestPath, [Text.Encoding]::UTF8) | ConvertFrom-Json)

$hasher = [Security.Cryptography.SHA256]::Create()
$verified = 0
try {
    foreach ($entry in $manifest.files) {
        if ($entry.path -match '(^|/)\.\.(/|$)|^[\\/]|:') {
            throw ("Unsafe manifest path: " + $entry.path)
        }
        $filePath = Join-Path $dataDir ($entry.path.Replace('/', '\'))
        $stream = [IO.File]::OpenRead('\\?\' + $filePath)
        try {
            if ($stream.Length -ne $entry.bytes) {
                throw ("Size mismatch: " + $entry.path)
            }
            $actual = [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
        } finally {
            $stream.Dispose()
        }
        if ($actual -ne $entry.sha256) {
            throw ("File SHA256 mismatch: " + $entry.path)
        }
        $verified++
    }
} finally {
    $hasher.Dispose()
}

[IO.Directory]::CreateDirectory($latestDir) | Out-Null
& robocopy $dataDir $latestDir /MIR /NFL /NDL /NJH /NJS /NC /NS | Out-Null
if ($LASTEXITCODE -ge 8) { throw "Robocopy mirror to latest failed with code $LASTEXITCODE" }

[IO.File]::WriteAllText((Join-Path $syncRoot 'CURRENT_SNAPSHOT.txt'), $dataDir, $utf8)
$receipt = [ordered]@{
    snapshot_id      = $meta.id
    milestone        = $meta.milestone
    local_data_dir   = $dataDir
    latest_mirror    = $latestDir
    archive          = $archivePath
    archive_sha256   = $archiveHash
    verified_files   = $verified
    metrics_summary  = $meta.metrics_summary
    integrity_errors = $meta.integrity_errors
    synced_at_local  = (Get-Date).ToString('o')
}
$receiptJson = $receipt | ConvertTo-Json -Depth 20
[IO.File]::WriteAllText((Join-Path $syncRoot 'LATEST_SYNC.json'), $receiptJson, $utf8)
[IO.File]::WriteAllText((Join-Path $archivesDir ("export-info-$($meta.id).json")), ($meta | ConvertTo-Json -Depth 20), $utf8)

Write-Output $receiptJson
if (@($meta.integrity_errors).Count -gt 0) {
    throw "Transferred snapshot has experiment data audit errors; inspect LATEST_SYNC.json."
}
