param(
    [Parameter(Mandatory=$true)][string]$Python,
    [string]$Profile = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ControlCoding Observer'),
    [string]$Project = '',
    [switch]$External,
    [string]$Source = '',
    [string]$Workspace = '',
    [switch]$Check
)
$ErrorActionPreference = 'Stop'
if ($External -and $Project) { throw 'Use -Source for external mode; -Project selects the ordinary panel.' }
if (-not $External -and ($Source -or $Workspace)) { throw '-Source and -Workspace require -External.' }
$packageRoot = [IO.Path]::GetFullPath($PSScriptRoot)
function Assert-Ordinary([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'Linked package/profile paths are unsupported.' }
    return $item
}
# Integrity is checked before launching any packaged program. This detects drift,
# not a forged unsigned package whose manifest was also replaced.
$manifestPath = Join-Path $packageRoot 'MANIFEST.json'
$null = Assert-Ordinary $manifestPath
if ((Get-Item -LiteralPath $manifestPath).Length -gt 2097152) { throw 'Oversized package manifest.' }
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifest.schemaVersion -ne 1 -or $manifest.platform -ne 'win32-x64') { throw 'Unsupported observer package.' }
$expected = @{}
foreach ($property in $manifest.files.PSObject.Properties) {
    $name = $property.Name
    if ($name -match '(^/|\\|:|(^|/)\.\.?(/|$))' -or $property.Value -notmatch '^[a-f0-9]{64}$') { throw 'Invalid package entry.' }
    $full = [IO.Path]::GetFullPath((Join-Path $packageRoot $name))
    if (-not $full.StartsWith($packageRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Package entry escapes its folder.' }
    $expected[$full] = $property.Value
}
if ($expected.Count -gt 5000) { throw 'Package inventory limit.' }
$pending = New-Object 'System.Collections.Generic.Queue[string]'
$pending.Enqueue($packageRoot)
$observed = 0
$entries = 0
while ($pending.Count -gt 0) {
    $directory = $pending.Dequeue()
    $null = Assert-Ordinary $directory
    foreach ($entry in Get-ChildItem -LiteralPath $directory -Force) {
        $entries++
        if ($entries -gt 5000) { throw 'Package inventory limit.' }
        $null = Assert-Ordinary $entry.FullName
        if ($entry.PSIsContainer) { $pending.Enqueue($entry.FullName); continue }
        if ($entry.FullName -eq $manifestPath) { continue }
        if (-not $expected.ContainsKey($entry.FullName)) { throw 'Unexpected file in observer package.' }
        if ($entry.Length -gt 536870912 -or (Get-FileHash -LiteralPath $entry.FullName -Algorithm SHA256).Hash -ne $expected[$entry.FullName]) { throw 'Observer package integrity check failed.' }
        $observed++
    }
}
if ($observed -ne $expected.Count) { throw 'Observer package is incomplete.' }
if (-not [IO.Path]::IsPathRooted($Python) -or -not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Provide the absolute path to an installed Python 3.11 or newer executable.' }
& $Python -I -B -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required.' }
if (-not [IO.Path]::IsPathRooted($Profile) -or ($Project -and -not [IO.Path]::IsPathRooted($Project))) { throw 'Absolute profile and project paths are required.' }
# ASCII transport avoids Windows PowerShell's scope-dependent pipeline encoding.
$request = @{profile=$Profile;project=$Project;package=$packageRoot} | ConvertTo-Json -Compress
if ($External) { $request = @{profile=$Profile;project='';package=$packageRoot;external=$true;source=$Source;workspace=$Workspace} | ConvertTo-Json -Compress }
$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($request))
$resolved = & $Python -I -B (Join-Path $packageRoot 'app\observer_paths.py') $encoded
if ($LASTEXITCODE -ne 0) { throw 'Profile, project and package must be ordinary, physically separate local directories.' }
$paths = $resolved | ConvertFrom-Json
$profilePath = $paths.profile
if ($Check) { Write-Output "Observer integrity and Python check passed ($observed files). No app launched or profile created."; exit 0 }
$electron = Join-Path $packageRoot 'runtime\electron.exe'
$appFolder = Join-Path $packageRoot 'app'
$launchArgs = @($appFolder, ('--core=' + (Join-Path $packageRoot 'core')), ('--python=' + $Python), ('--profile=' + $profilePath))
if ($Project) { $launchArgs += '--project=' + $paths.project }
if ($External) {
    $launchArgs += '--external'
    if ($paths.source) { $launchArgs += '--source=' + $paths.source }
    if ($paths.workspace) { $launchArgs += '--workspace=' + $paths.workspace }
}
if (@($launchArgs | Where-Object { $_.Contains('"') }).Count) { throw 'Quotes in launch paths are unsupported.' }
$savedRunAsNode = $env:ELECTRON_RUN_AS_NODE
try {
    Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
    # Visible interactive application, explicitly requested by this launcher.
    # Windows argv parsing requires doubling trailing backslashes before a quote.
    Start-Process -FilePath $electron -ArgumentList @($launchArgs | ForEach-Object { '"' + ($_ -replace '(\\+)$', '$1$1') + '"' }) -WorkingDirectory $appFolder
} finally {
    if ($null -ne $savedRunAsNode) { $env:ELECTRON_RUN_AS_NODE = $savedRunAsNode }
}
