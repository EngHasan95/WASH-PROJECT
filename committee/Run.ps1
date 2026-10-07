param([ValidateSet('Start','Stop','Backup')][string]$Action = 'Start')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
function Invoke-Docker {
    & docker @script:DockerArgs @args
    if ($LASTEXITCODE -ne 0) { throw 'Docker operation failed. Keep this window open for the error above.' }
}
try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw 'Install and start Rancher Desktop once: https://rancherdesktop.io/ . Select dockerd (Moby) as the container engine, then run Start.cmd again.'
    }
    $endpoint = $env:DOCKER_HOST
    if (-not $endpoint) {
        $endpoint = (& docker context inspect --format '{{.Endpoints.docker.Host}}' | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) { throw 'Start Rancher Desktop first, using dockerd (Moby).' }
    }
    if ($endpoint -notmatch '^(unix://|npipe://)') { throw 'Use a local Docker engine. Remote Docker endpoints are not supported by this computer demo.' }
    Remove-Item Env:DOCKER_CONTEXT -ErrorAction SilentlyContinue
    Remove-Item Env:DOCKER_HOST -ErrorAction SilentlyContinue
    $script:DockerArgs = @('--host', $endpoint)
    $engine = (Invoke-Docker info --format '{{.OSType}}' | Out-String).Trim()
    if ($engine -ne 'linux') { throw 'Select the Linux container engine (dockerd / Moby), then retry.' }
    Invoke-Docker compose version
    $envfile = Join-Path $PSScriptRoot '.committee.env'
    if (-not (Test-Path -LiteralPath $envfile)) {
        if ($Action -ne 'Start') { throw 'Run Start.cmd first.' }
        $retainedVolumes = @(Invoke-Docker volume ls -q --filter 'label=com.docker.compose.project=wash-committee')
        $retainedContainers = @(Invoke-Docker ps -aq --filter 'label=com.docker.compose.project=wash-committee')
        if ($retainedVolumes.Count -or $retainedContainers.Count) {
            throw 'Existing committee data has no local settings file. Restore the original .committee.env from your existing copy. No containers or data were changed.'
        }
        $random = New-Object byte[] 32
        $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($random) } finally { $rng.Dispose() }
        $secret = -join ($random | ForEach-Object { $_.ToString('x2') })
        $stream = [IO.File]::Open($envfile, [IO.FileMode]::CreateNew)
        $stream.Dispose()
        $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        & icacls $envfile /inheritance:r /grant:r "*${sid}:(F)" | Out-Null
        if ($LASTEXITCODE -ne 0) { Remove-Item -LiteralPath $envfile; throw 'Could not protect the local settings file. Extract the package to a local NTFS folder.' }
        [IO.File]::WriteAllText($envfile, "WASH_COMMITTEE_DB_PASSWORD=$secret`nWASH_COMMITTEE_PORT=8765`n", [Text.Encoding]::ASCII)
    }
    $script:ComposeArgs = @('compose','--project-name','wash-committee','--env-file',$envfile,'-f',(Join-Path $PSScriptRoot 'compose.yaml'))
    switch ($Action) {
        'Start' {
            $expected = ((Get-Content -LiteralPath 'images.sha256' -Raw).Trim() -split '\s+')[0]
            if ((Get-FileHash -LiteralPath 'images.tar' -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) { throw 'Package checksum failed. Download and extract the complete package again.' }
            $present = @(Invoke-Docker image ls --no-trunc --format '{{.Repository}}:{{.Tag}} {{.ID}}')
            $required = @(Get-Content -LiteralPath 'images.ids' | Where-Object { $_.Trim() })
            if (@($required | Where-Object { $present -notcontains $_ }).Count) { Invoke-Docker load --input (Join-Path $PSScriptRoot 'images.tar') }
            $present = @(Invoke-Docker image ls --no-trunc --format '{{.Repository}}:{{.Tag}} {{.ID}}')
            if (@($required | Where-Object { $present -notcontains $_ }).Count) { throw 'The expected application images are missing.' }
            Invoke-Docker @script:ComposeArgs up -d --wait --wait-timeout 180
            $portLine = Get-Content -LiteralPath $envfile | Where-Object { $_ -match '^WASH_COMMITTEE_PORT=\d+$' }
            $port = ($portLine -split '=')[1]
            Start-Process "http://127.0.0.1:$port/committee/"
            Write-Host 'The committee guide is open. Stop.cmd preserves your test data. No hosting subscription is needed.'
        }
        'Stop' { Invoke-Docker @script:ComposeArgs stop; Write-Host 'Stopped. Test data and local unsent reports are preserved.' }
        'Backup' {
            Invoke-Docker @script:ComposeArgs exec -T app python scripts/backup.py /committee/backups
            $folder = Join-Path $PSScriptRoot ('backups-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
            New-Item -ItemType Directory -Path $folder | Out-Null
            Invoke-Docker @script:ComposeArgs cp 'app:/committee/backups/.' $folder
            Write-Host "Backup copied to $folder"
        }
    }
} catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }
