# 入っているツールの判定（インストーラー・アンインストーラー共通。Python が無くても動くよう、各ツールの app.json を読む）
#   . (Join-Path $App 'shared\apps.ps1') で読み込むと、次が使える
#   $AppList   : ツールの一覧（Pkg / Name / Desc / Order / SettingsFile / Folders / TempDirs / WebName）
#   $Folders   : 手動で指定するフォルダの定義（自動取得できない外部ツールの場所など）
#   $WebName   : Web 画面の自動起動・ファイアウォール規則の名前
#   $SuiteName : 表示用の名前
$AppList = @()
foreach ($f in Get-ChildItem -Path $App -Filter 'app.json' -Recurse -Depth 1 -ErrorAction SilentlyContinue) {
    $dir = $f.Directory
    if ($dir.Parent.FullName.TrimEnd('\') -ne $App.TrimEnd('\')) { continue }
    if (-not (Test-Path (Join-Path $dir.FullName '__init__.py'))) { continue }
    try { $j = Get-Content $f.FullName -Raw -Encoding UTF8 | ConvertFrom-Json } catch { continue }
    $AppList += [pscustomobject]@{
        Pkg = $dir.Name
        Name = $(if ($j.name) { $j.name } else { $dir.Name })
        Desc = [string]$j.desc
        Order = $(if ($null -ne $j.order) { [int]$j.order } else { 100 })
        SettingsFile = $(if ($j.settings_file) { $j.settings_file } else { "settings_$($dir.Name).json" })
        Folders = @($j.folders | Where-Object { $_ })
        TempDirs = @($j.temp_dirs | Where-Object { $_ })
        WebName = [string]$j.web_name
        SetupSteps = @($j.setup_steps | Where-Object { $_ })
    }
}
$AppList = @($AppList | Sort-Object Order, Pkg)
$Folders = @()
foreach ($a in $AppList) { foreach ($fd in $a.Folders) { $Folders += [pscustomobject]@{ Def = $fd; App = $a } } }
$WebName = ($AppList | Where-Object { $_.WebName } | Select-Object -First 1).WebName
if (-not $WebName) { $WebName = if ($AppList.Count) { "$($AppList[0].Name)_web" } else { 'tools_web' } }
$SuiteName = if ($AppList.Count -eq 1) { $AppList[0].Name } else { ($AppList | ForEach-Object { $_.Name }) -join ' / ' }

# 手動指定フォルダの自動検出（設定済み → このフォルダの親 → よくある場所）
function Find-AppFolder($item) {
    $fd = $item.Def
    $cands = @()
    $sj = Join-Path $App $item.App.SettingsFile
    if (Test-Path $sj) { try { $v = (Get-Content $sj -Raw -Encoding UTF8 | ConvertFrom-Json).($fd.key); if ($v) { $cands += $v } } catch {} }
    if ($fd.search_parents) {
        $p = Split-Path $App -Parent
        if ($p) { $cands += $p; $pp = Split-Path $p -Parent; if ($pp) { $cands += $pp } }
    }
    foreach ($dr in [IO.DriveInfo]::GetDrives() | Where-Object { $_.DriveType -eq 'Fixed' }) {
        $r = $dr.RootDirectory.FullName.TrimEnd('\')
        foreach ($rel in @($fd.search)) { if ($rel) { $cands += "$r\$rel" } }
    }
    foreach ($x in $cands) { if ($x -and (Test-AppFolder $item $x)) { return $x } }
    return ''
}
function Test-AppFolder($item, $path) {
    if (-not $path) { return $false }
    $file = $item.Def.file
    if ($file) { return (Test-Path (Join-Path $path $file)) }
    return (Test-Path $path -PathType Container)
}
