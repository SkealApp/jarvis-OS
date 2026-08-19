# ------------------------------------------------------------------------------
# Garantit la presence du binaire livekit-server.exe (serveur WebRTC du pipeline
# vocal). Equivalent Windows de scripts/ensure_livekit.sh, appele par jarvis.ps1
# avant de demarrer le serveur.
#
#   - no-op s'il est deja disponible (bundle, bin\ du projet, ou PATH)
#   - sinon telechargement automatique de la derniere release Windows officielle
#     (github.com/livekit/livekit) dans bin\livekit-server.exe — meme emplacement
#     que celui resolu par jarvis.kernel.bundle.resolve_livekit_binary().
#
# Dot-source ce fichier puis appelle Ensure-LivekitServer -ProjectRoot <racine> :
# renvoie le chemin (ou nom PATH) du binaire utilisable, ou $null si echec.
# ------------------------------------------------------------------------------

function Get-JarvisLivekitBinary {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)
    $candidates = @(
        (Join-Path $ProjectRoot "bundle\bin\livekit-server.exe"),
        (Join-Path $ProjectRoot "bin\livekit-server.exe")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }
    if (Get-Command "livekit-server" -ErrorAction SilentlyContinue) {
        return "livekit-server"
    }
    return $null
}

function Ensure-LivekitServer {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $existing = Get-JarvisLivekitBinary -ProjectRoot $ProjectRoot
    if ($existing) { return $existing }

    Write-Host "  livekit-server introuvable - telechargement automatique..." -ForegroundColor Yellow

    $arch = if ([Environment]::Is64BitOperatingSystem -and
                $env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "arm64" } else { "amd64" }

    try {
        $release = Invoke-RestMethod `
            -Uri "https://api.github.com/repos/livekit/livekit/releases/latest" `
            -Headers @{ "User-Agent" = "jarvis-os-setup" } `
            -TimeoutSec 30
    } catch {
        Write-Host "  Impossible de joindre l'API GitHub : $_" -ForegroundColor Red
        Write-Host "  Installe manuellement : https://github.com/livekit/livekit/releases" -ForegroundColor White
        Write-Host "  puis place livekit-server.exe dans $ProjectRoot\bin\" -ForegroundColor White
        return $null
    }

    $asset = $release.assets | Where-Object {
        $_.name -match "windows" -and $_.name -match $arch -and $_.name -match "\.zip$"
    } | Select-Object -First 1
    if (-not $asset) {
        Write-Host "  Aucun binaire Windows ($arch) dans la release $($release.tag_name)." -ForegroundColor Red
        Write-Host "  Installe manuellement : https://github.com/livekit/livekit/releases" -ForegroundColor White
        return $null
    }

    $staging = Join-Path $env:TEMP "jarvis-livekit-download"
    Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $staging -Force | Out-Null
    $zipPath = Join-Path $staging $asset.name
    $binDir = Join-Path $ProjectRoot "bin"
    $dest = Join-Path $binDir "livekit-server.exe"

    try {
        Write-Host "  $($asset.browser_download_url)" -ForegroundColor DarkGray
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zipPath -UseBasicParsing
        Expand-Archive -Path $zipPath -DestinationPath $staging -Force

        $exe = Get-ChildItem -Path $staging -Recurse -Filter "livekit-server.exe" |
            Select-Object -First 1
        if (-not $exe) {
            throw "livekit-server.exe absent de l'archive $($asset.name)"
        }

        if (-not (Test-Path $binDir)) {
            New-Item -ItemType Directory -Path $binDir -Force | Out-Null
        }
        Copy-Item $exe.FullName $dest -Force
        Write-Host "  livekit-server $($release.tag_name) installe dans bin\" -ForegroundColor Green
        return $dest
    } catch {
        Write-Host "  Echec du telechargement de livekit-server : $_" -ForegroundColor Red
        Write-Host "  Installe manuellement : https://github.com/livekit/livekit/releases" -ForegroundColor White
        Write-Host "  puis place livekit-server.exe dans $ProjectRoot\bin\" -ForegroundColor White
        return $null
    } finally {
        Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
    }
}
