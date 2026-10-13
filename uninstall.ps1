# アンインストーラー（このフォルダに入っているツール一式。ツールが使う外部のフォルダには触れない）
param([switch]$Yes)
$ErrorActionPreference = 'Continue'
function Quit($code) { Read-Host "Enter で閉じます" | Out-Null; exit $code }
$App = Split-Path -Parent $MyInvocation.MyCommand.Path
$App = (Resolve-Path $App).Path.TrimEnd('\')
$AppDir = $App + '\'          # 前方一致で C:\tools\av1an が C:\tools\av1an2 に当てはまらないよう、区切りまで含める
function In-App([string]$p) { return $p -and $p.StartsWith($AppDir, [StringComparison]::OrdinalIgnoreCase) }
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
# 作業フォルダ（英数字のパスが要るときに作る EncodeTemp など）も候補に入れる。消すのは中の、このツールが作った
# 作業フォルダ（av1gui_<番号>）だけで、EncodeTemp などのフォルダ自体は空になったときだけ消す
# （利用者が同じ名前のフォルダを使っていても、その中身には触れない）
$tempNames = @('EncodeTemp', 'encode_tools') + @($AppList | ForEach-Object { $_.TempDirs })
$tempParents = @()
foreach ($n in $tempNames) {
    foreach ($d in Get-PSDrive -PSProvider FileSystem) {
        $c = Join-Path $d.Root $n
        if (Test-Path $c) { $tempParents += $c }
    }
    $c = Join-Path $env:TEMP $n
    if (Test-Path $c) { $tempParents += $c }
}
$roots += $tempParents
$tempDirs = @()
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
# 自動起動は、このフォルダを起動するものだけ消す（同じツールを別のフォルダに入れている場合、そちらは残す）
$task = Get-ScheduledTask -TaskName $WebName -ErrorAction SilentlyContinue
$taskMine = $false
if ($task) {
    $a0 = @($task.Actions)[0]
    $taskMine = (In-App $a0.Execute) -or (In-App (($a0.WorkingDirectory + '').TrimEnd('\') + '\'))
    if (-not $taskMine) { Write-Host "自動起動のタスク $WebName は別のフォルダのものなので残します: $($a0.Execute)"; $task = $null }
}
if (Test-Path $startLnk) {
    try { if (-not (In-App (New-Object -ComObject WScript.Shell).CreateShortcut($startLnk).TargetPath)) { $startLnk = '' } } catch { $startLnk = '' }
}
$otherAutostart = (-not $taskMine) -and (Get-ScheduledTask -TaskName $WebName -ErrorAction SilentlyContinue)

Write-Host ""
Write-Host "削除するもの:"
Write-Host "  - アプリ本体と Python・自動取得したツール・設定: $App"
foreach ($lnk in $lnks) { if (Test-Path $lnk) { Write-Host "  - デスクトップのショートカット: $lnk" } }
foreach ($t in $tempDirs + $leftovers) { Write-Host "  - 作業フォルダ: $t" }
if ($task -or ($startLnk -and (Test-Path $startLnk))) { Write-Host "  - Web 画面の自動起動（$WebName）とファイアウォール規則" }
Write-Host ""
$keep = @('変換済みの動画') + @($Folders | ForEach-Object { $_.Def.title })
Write-Host ("残すもの: " + ($keep -join '、'))
Write-Host ""

if (-not $Yes) {
    $ans = Read-Host "アンインストールしますか？ (y/N)"
    if ($ans -notmatch '^[yY]') { Write-Host "中止しました"; Quit 0 }
}

# 起動中のツールを終了する。対象はこのフォルダのものだけ:
#   実行ファイルがこのフォルダの中（.venv の python、取得したエンコーダなど）か、コマンドラインにこのフォルダがある python。
#   venv の python は本体（PC に入っている Python のこともある）を子として起動するので、その子も終了する
$all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
$targets = @{}
foreach ($pr in $all) {
    # コマンドラインで見るのは python だけ（このフォルダのファイルを開いているエディタなどは終了させない）
    if ((In-App $pr.ExecutablePath) -or ($pr.Name -like 'python*' -and $pr.CommandLine -and
        $pr.CommandLine.IndexOf($AppDir, [StringComparison]::OrdinalIgnoreCase) -ge 0)) {
        $targets[[int]$pr.ProcessId] = $pr
    }
}
do {
    $added = $false
    foreach ($pr in $all) {
        if (-not $targets.ContainsKey([int]$pr.ProcessId) -and $targets.ContainsKey([int]$pr.ParentProcessId) -and
            $pr.Name -like 'python*') { $targets[[int]$pr.ProcessId] = $pr; $added = $true }
    }
} while ($added)
$targets.Remove([int]$PID)
foreach ($pr in $targets.Values) {
    Write-Host "終了: PID $($pr.ProcessId) $($pr.Name)"
    Stop-Process -Id $pr.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Milliseconds 500

# Web 画面の自動起動とファイアウォール規則
if ($task) {
    Stop-ScheduledTask -TaskName $WebName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $WebName -Confirm:$false -ErrorAction SilentlyContinue
}
if ($startLnk -and (Test-Path $startLnk)) { Remove-Item $startLnk -Force -ErrorAction SilentlyContinue }
# ファイアウォールの規則は名前が同じなので、別のフォルダの自動起動が残っているときは消さない
if (-not $otherAutostart -and (Get-NetFirewallRule -DisplayName $WebName -ErrorAction SilentlyContinue)) {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($isAdmin) { Remove-NetFirewallRule -DisplayName $WebName -ErrorAction SilentlyContinue }
    else { Start-Process powershell -Verb RunAs -WindowStyle Hidden -ArgumentList "-NoProfile -Command Remove-NetFirewallRule -DisplayName '$WebName'" -ErrorAction SilentlyContinue }
}

# ショートカット（このアプリを指しているものだけ）
foreach ($lnk in $lnks) { if (Test-Path $lnk) {
    try {
        $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
        if (In-App $sc.TargetPath) { Remove-Item -LiteralPath $lnk -Force }
    } catch {}
} }

foreach ($t in $tempDirs + $leftovers) {
    & cmd.exe /c rd /s /q "`"$t`""                # rd はリンク（ジャンクション）をたどらない
    if (Test-Path $t) { Write-Host "削除できませんでした: $t" -ForegroundColor Yellow }
}
# EncodeTemp などは、空になったときだけ消す
foreach ($t in $tempParents) {
    if ((Test-Path $t) -and -not (Get-ChildItem -LiteralPath $t -Force -ErrorAction SilentlyContinue)) {
        Remove-Item -LiteralPath $t -Force -ErrorAction SilentlyContinue
    }
}

# 以前の版のインストーラーで uv が利用者の環境に置いたもの（このフォルダの Python を指すものだけ）
#   - %USERPROFILE%\.local\bin の python*.exe（中に、このフォルダの Python のパスが書かれているもの）
#   - レジストリ HKCU\Software\Python\Astral の登録（InstallPath がこのフォルダの中のもの）
$bin = Join-Path $env:USERPROFILE '.local\bin'
if (Test-Path $bin) {
    foreach ($f in Get-ChildItem -LiteralPath $bin -Filter 'python*.exe' -File -ErrorAction SilentlyContinue) {
        try {
            $bytes = [IO.File]::ReadAllBytes($f.FullName)
            $text = [Text.Encoding]::UTF8.GetString($bytes).ToLower()
            if ($text.Contains($AppDir.ToLower())) { Write-Host "削除: $($f.FullName)"; Remove-Item -LiteralPath $f.FullName -Force }
        } catch {}
    }
}
$reg = 'HKCU:\Software\Python\Astral'
if (Test-Path $reg) {
    foreach ($k in Get-ChildItem -Path $reg -ErrorAction SilentlyContinue) {
        try {
            $ip = (Get-ItemProperty -Path (Join-Path $k.PSPath 'InstallPath') -ErrorAction Stop).'(default)'
            if (In-App ($ip.TrimEnd('\') + '\')) { Write-Host "登録を削除: $($k.PSChildName)"; Remove-Item -Path $k.PSPath -Recurse -Force }
        } catch {}
    }
}

Write-Host ""
Write-Host "アンインストール完了（閉じると数秒後にフォルダが消えます）" -ForegroundColor Green
Read-Host "Enter で閉じます" | Out-Null

# 本体は、このスクリプト終了後に別プロセスで削除する（実行中のファイルを消せないため）
Set-Location $env:TEMP
$cmd = "/c timeout /t 3 /nobreak >nul & rd /s /q `"$App`""
Start-Process -FilePath cmd.exe -ArgumentList $cmd -WindowStyle Hidden
