param(
    [string]$Python = 'C:\Python313\python.exe',
    [string]$Electron = 'A:\DevTools\Orbifex-UI-Runtime\node_modules\electron\dist\electron.exe',
    [string]$Modules = 'A:\DevTools\ControlCoding-Panel\node_modules',
    [string]$Node = 'A:\Programmi Utility AI\nodejs\node.exe',
    [string]$Parser = 'A:\AI Coding\cursor\resources\app\node_modules\@babel\parser',
    [string]$Output = 'J:\ProgettiAI\Workbenchs\ControlCoding_Workbench\panel-02-20260920-a\exports\desktop',
    [string]$Profile = 'J:\ProgettiAI\Workbenchs\ControlCoding_Workbench\panel-02-20260920-a\scratch\panel-profile',
    [string]$Project = ''
)
$ErrorActionPreference = 'Stop'
$core = Split-Path -Parent $PSScriptRoot
foreach ($file in @($Python, $Electron, $Node)) {
    if (-not [System.IO.Path]::IsPathRooted($file) -or -not (Test-Path -LiteralPath $file -PathType Leaf)) { throw 'An installed runtime path is missing.' }
}
& $Node (Join-Path $PSScriptRoot 'build.cjs') $Modules $Output $Parser
if ($LASTEXITCODE -ne 0) { throw 'Panel build failed.' }
# Explicitly launching the visible interactive panel requested by the user.
$launchArgs = @(('"' + $Output + '"'), ('"--core=' + $core + '"'), ('"--python=' + $Python + '"'), ('"--profile=' + $Profile + '"'))
if ($Project) { $launchArgs += ('"--project=' + $Project + '"') }
$savedRunAsNode = $env:ELECTRON_RUN_AS_NODE
try {
    # Codex or another Electron parent may export this flag for its Node helpers.
    Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
    Start-Process -FilePath $Electron -ArgumentList $launchArgs -WorkingDirectory $Output
} finally {
    if ($null -ne $savedRunAsNode) { $env:ELECTRON_RUN_AS_NODE = $savedRunAsNode }
}
