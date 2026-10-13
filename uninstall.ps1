# アンインストーラー（このフォルダに入っているツール一式。ツールが使う外部のフォルダには触れない）
param([switch]$Yes)
$ErrorActionPreference = 'Continue'
function Quit($code) { Read-Host "Enter で閉じます" | Out-Null; exit $code }
$App = Split-Path -Parent $MyInvocation.MyCommand.Path
$App = (Resolve-Path $App).Path.TrimEnd('\')
if (-not (Test-Path (Join-Path $App 'shared\__init__.py'))) {
    Write-Host "このツールのフォルダではありません: $App" -ForegroundColor Red
    Quit 1
}
. (Join-Path $App 'shared\apps.ps1')
Write-Host "=== $SuiteName アンインストール ===" -ForegroundColor Cyan

# 安全確認: 手動指定する外部ツールのフォルダそのものなら中止（そのツールまで消してしまうため）
foreach ($it in $Folders) {
    if (Test-AppFolder $it $App) {
        Write-Host "このフォルダは「$($it.Def.title)」のフォルダでもあります。消すと困るため中止します。" -ForegroundColor Red
        Quit 1
    }
}

# 設定から作業フォルダの候補を集める
$roots = @()
foreach ($a in $AppList) {
    $settings = Join-Path $App $a.SettingsFile
    if (Test-Path $settings) {
        try {
            $s = Get-Content $settings -Raw -Encoding UTF8 | ConvertFrom-Json
            foreach ($p in @($s.temp_dir, $s.output_dir)) { if ($p) { $roots += $p } }
        } catch {}
    }
}
$tempNames = @('EncodeTemp', 'encode_tools') + @($AppList | ForEach-Object { $_.TempDirs })
$tempDirs = @()
foreach ($n in $tempNames) {
    foreach ($d in Get-PSDrive -PSProvider FileSystem) {
        $c = Join-Path $d.Root $n
        if (Test-Path $c) { $tempDirs += $c }
    }
    $c = Join-Path $env:TEMP $n
    if (Test-Path $c) { $tempDirs += $c }
}
$pk = ($AppList | ForEach-Object { [regex]::Escape($_.Pkg) }) -join '|'
$leftovers = @()
foreach ($r in $roots) {
    if ($pk -and (Test-Path $r)) {
        $leftovers += Get-ChildItem -LiteralPath $r -Directory -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match "^\.?($pk)(_av1an)?_[0-9a-f]{8,12}$" } | ForEach-Object { $_.FullName }
    }
}
$lnks = $AppList | ForEach-Object { Join-Path ([Environment]::GetFolderPath('Desktop')) "$($_.Name).lnk" }
$startLnk = Join-Path ([Environment]::GetFolderPath('Startup')) "$WebName.lnk"
$task = Get-ScheduledTask -TaskName $WebName -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "削除するもの:"
Write-Host "  - アプリ本体と Python・自動取得したツール・設定: $App"
foreach ($lnk in $lnks) { if (Test-Path $lnk) { Write-Host "  - デスクトップのショートカット: $lnk" } }
foreach ($t in $tempDirs + $leftovers) { Write-Host "  - 作業フォルダ: $t" }
if ($task -or (Test-Path $startLnk)) { Write-Host "  - Web 画面の自動起動（$WebName）とファイアウォール規則" }
Write-Host ""
$keep = @('変換済みの動画') + @($Folders | ForEach-Object { $_.Def.title })
Write-Host ("残すもの: " + ($keep -join '、'))
Write-Host ""

if (-not $Yes) {
    $ans = Read-Host "アンインストールしますか？ (y/N)"
    if ($ans -notmatch '^[yY]') { Write-Host "中止しました"; Quit 0 }
}

# 起動中のツールを終了（venv のランチャーは本体 Python を子プロセスで起動するためコマンドラインで判定）
$mods = @($AppList | ForEach-Object { $_.Pkg }) + @('shared')
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $pr = $_; $pr.Name -like 'python*' -and $pr.CommandLine -and ($pr.CommandLine -like "*$App*" -or ($mods | Where-Object { $pr.CommandLine -like "*-m $_*" })) } |
    ForEach-Object { Write-Host "終了: PID $($_.ProcessId)"; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($App, [StringComparison]::OrdinalIgnoreCase) } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 500

# Web 画面の自動起動とファイアウォール規則
if ($task) {
    Stop-ScheduledTask -TaskName $WebName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $WebName -Confirm:$false -ErrorAction SilentlyContinue
}
if (Test-Path $startLnk) { Remove-Item $startLnk -Force -ErrorAction SilentlyContinue }
if (Get-NetFirewallRule -DisplayName $WebName -ErrorAction SilentlyContinue) {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($isAdmin) { Remove-NetFirewallRule -DisplayName $WebName -ErrorAction SilentlyContinue }
    else { Start-Process powershell -Verb RunAs -WindowStyle Hidden -ArgumentList "-NoProfile -Command Remove-NetFirewallRule -DisplayName '$WebName'" -ErrorAction SilentlyContinue }
}

# ショートカット（このアプリを指しているものだけ）
foreach ($lnk in $lnks) { if (Test-Path $lnk) {
    try {
        $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
        if ($sc.TargetPath.StartsWith($App, [StringComparison]::OrdinalIgnoreCase)) { Remove-Item -LiteralPath $lnk -Force }
    } catch {}
} }

foreach ($t in $tempDirs + $leftovers) {
    Remove-Item -LiteralPath $t -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path $t) { Write-Host "削除できませんでした: $t" -ForegroundColor Yellow }
}

Write-Host ""
Write-Host "アンインストール完了（閉じると数秒後にフォルダが消えます）" -ForegroundColor Green
Read-Host "Enter で閉じます" | Out-Null

# 本体は、このスクリプト終了後に別プロセスで削除する（実行中のファイルを消せないため）
Set-Location $env:TEMP
$cmd = "/c timeout /t 3 /nobreak >nul & rd /s /q `"$App`""
Start-Process -FilePath cmd.exe -ArgumentList $cmd -WindowStyle Hidden
