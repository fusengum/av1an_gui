# 全自動セットアップ（Windows）— このフォルダに入っているツールが対象
# Python が無い状態から、uv・Python・ライブラリ・外部ツール・ショートカット・Web 画面の自動起動まで構築する
#   通常は install.bat（画面付きインストーラー）から使う。コンソールで実行するなら install_cli.bat
param(
    [switch]$NoWeb,          # Web 画面の自動起動を登録しない
    [int]$Port = 8765,
    [string]$Folder = '',    # 手動で指定するフォルダ（KEY=DIR、複数は | 区切り。必要なツールが入っている場合のみ。省略時は自動検出、無ければ尋ねる）
    [switch]$NoShortcut,     # デスクトップのショートカットを作らない
    [switch]$Force,          # 外部ツールを取り直す
    [switch]$Gui             # インストーラー画面から呼ばれた（進捗行 @@STEP を出し、入力は求めない）
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
if ($Gui) { [Console]::OutputEncoding = [Text.Encoding]::UTF8 }
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$App = Split-Path -Parent $MyInvocation.MyCommand.Path
$Tools = Join-Path $App 'tools'
New-Item -ItemType Directory -Force -Path $Tools | Out-Null
. (Join-Path $App 'shared\apps.ps1')

function Say($m, $color = 'Gray') { if ($Gui) { [Console]::Out.WriteLine($m) } else { Write-Host $m -ForegroundColor $color } }
function Mark($id, $state, $label) {
    # state: run / ok / ng / skip
    if ($Gui) { [Console]::Out.WriteLine("@@STEP $id $state $label"); [Console]::Out.Flush() }
    elseif ($state -eq 'run') { Write-Host "`n=== $label" -ForegroundColor Cyan }
    elseif ($state -eq 'ng') { Write-Host "失敗: $label" -ForegroundColor Red }
}
function Info($key, $val) { if ($Gui) { [Console]::Out.WriteLine("@@INFO $key $val") } }
# 外部コマンドを実行して終了コードを返す（stderr もそのまま表示。PowerShell 5.1 のエラー化を避ける）
function Run($exe, [string[]]$argv) {
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { & $exe @argv 2>&1 | ForEach-Object { Say "$_" } } finally { $ErrorActionPreference = $old }
    return $LASTEXITCODE
}

try {
    # このフォルダに書き込めるか（Program Files などでは、管理者で入れても実行時に設定やログを書けない）
    try { $t = Join-Path $App '.write_test'; [IO.File]::WriteAllText($t, 'x'); Remove-Item $t -Force }
    catch { throw "このフォルダに書き込めません: $App（Program Files などの保護された場所ではなく、C:\tools などに置いてください）" }

    # 1. uv（Python とライブラリの管理）。このフォルダ内 → PATH の順に探し、無ければこのフォルダ内に取得する
    $UvDir = Join-Path $Tools 'uv'
    $Uv = Join-Path $UvDir 'uv.exe'
    Mark 'uv' 'run' 'uv（Python の管理ツール）'
    $sysUv = Get-Command uv.exe -ErrorAction SilentlyContinue
    if (Test-Path $Uv) { Mark 'uv' 'skip' 'uv（導入済み）' }
    elseif ($sysUv -and -not $Force) { $Uv = $sysUv.Source; Mark 'uv' 'skip' 'uv（導入済み）'; Say "uv: $Uv" }
    else {
        $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'aarch64' } else { 'x86_64' }
        $zip = Join-Path $env:TEMP 'uv.zip'
        Invoke-WebRequest "https://github.com/astral-sh/uv/releases/latest/download/uv-$arch-pc-windows-msvc.zip" -OutFile $zip -UseBasicParsing
        New-Item -ItemType Directory -Force -Path $UvDir | Out-Null
        Expand-Archive $zip -DestinationPath $UvDir -Force
        Remove-Item $zip
        Mark 'uv' 'ok' 'uv（Python の管理ツール）'
    }
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $Tools 'python'
    $env:UV_CACHE_DIR = Join-Path $Tools 'uv-cache'
    $env:UV_NO_PROGRESS = '1'

    # 動いている Web 画面を止める（ライブラリのファイルが使用中だと更新できないため。最後に起動し直す）
    Stop-ScheduledTask -TaskName $WebName -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*.web --host*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

    # 2. Python と仮想環境
    #    既存の仮想環境が使えればそのまま。無ければ PC に入っている Python（$PyMin 以上）を使い、それも無いときだけ取得する
    $PyMin = '3.12'          # VapourSynth（av1an 用）が 3.12 以上を必要とする
    $PyMax = '3.14'          # これ未満（新しすぎると PySide6 などの配布物がまだ無いことがある）
    $PyReq = ">=$PyMin,<$PyMax"
    $VenvDir = Join-Path $App '.venv'
    $VenvPy = Join-Path $VenvDir 'Scripts\python.exe'
    $Py = $VenvPy
    $Pyw = Join-Path $VenvDir 'Scripts\pythonw.exe'
    function Test-PyVersion($exe) {
        if (-not (Test-Path $exe)) { return $false }
        try { & $exe -c "import sys; sys.exit(0 if (3, 12) <= sys.version_info[:2] < (3, 14) else 1)" 2>$null; return ($LASTEXITCODE -eq 0) } catch { return $false }
    }
    function New-Venv([string]$pyArg) {
        if (Test-Path $VenvDir) { Remove-Item -Recurse -Force $VenvDir }
        if ((Run $Uv @('venv', '--python', $pyArg, $VenvDir)) -ne 0) { throw '仮想環境を作れませんでした' }
    }
    Mark 'python' 'run' "Python $PyMin 以上"
    if ((Test-PyVersion $VenvPy) -and -not $Force) {
        $v = (& $VenvPy -c "import platform; print(platform.python_version())")
        Mark 'python' 'skip' "Python $v（導入済み）"
    } else {
        # uv は PATH・py ランチャー・このフォルダの tools\python から条件に合う Python を探す
        $found = ''
        $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        try { $found = (& $Uv python find --system $PyReq 2>$null | Select-Object -First 1) } finally { $ErrorActionPreference = $old }
        if ($found -and (Test-PyVersion $found)) {
            Say "PC に入っている Python を使います: $found"
            New-Venv $found
            $v = (& $VenvPy -c "import platform; print(platform.python_version())")
            Mark 'python' 'ok' "Python $v（PC に入っているものを使用）"
        } else {
            if ((Run $Uv @('python', 'install', $PyMin)) -ne 0) { throw 'Python の導入に失敗しました' }
            New-Venv $PyMin
            Mark 'python' 'ok' "Python $PyMin"
        }
    }

    # ライブラリ（入っていて条件を満たすものは入れ直さない）
    Mark 'libs' 'run' 'ライブラリ（画面・Web・解析）'
    $reqs = @('-r', (Join-Path $App 'requirements.txt'))
    foreach ($a in $AppList) { $r = Join-Path $App "$($a.Pkg)\requirements.txt"; if (Test-Path $r) { $reqs += @('-r', $r) } }
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { $dry = (& $Uv pip install --dry-run --python $Py @reqs 2>&1 | Out-String) } finally { $ErrorActionPreference = $old }
    if ($LASTEXITCODE -eq 0 -and $dry -match 'Would make no changes') {
        Mark 'libs' 'skip' 'ライブラリ（導入済み）'
    } else {
        if ((Run $Uv (@('pip', 'install', '--python', $Py) + $reqs)) -ne 0) {
            # PC の Python の版に合う配布物が無い場合などは、決まった版の Python で作り直してもう一度試す
            $cur = (& $VenvPy -c "import platform; print(platform.python_version())")
            if ($cur -notlike "$PyMin.*") {
                Say "Python $cur ではライブラリを入れられませんでした。Python $PyMin で作り直します" 'Yellow'
                if ((Run $Uv @('python', 'install', $PyMin)) -ne 0) { throw 'Python の導入に失敗しました' }
                New-Venv $PyMin
                if ((Run $Uv (@('pip', 'install', '--python', $Py) + $reqs)) -ne 0) { throw 'ライブラリの導入に失敗しました' }
            } else { throw 'ライブラリの導入に失敗しました' }
        }
        Mark 'libs' 'ok' 'ライブラリ（画面・Web・解析）'
    }

    # 3. 手動で指定するフォルダ（自動取得できない外部ツールの場所など。必要なツールが入っている場合だけ）
    Push-Location $App
    $given = @{}
    foreach ($x in ($Folder -split '\|')) { if ($x -match '^([^=]+)=(.*)$') { $given[$Matches[1]] = $Matches[2].Trim().Trim('"') } }
    foreach ($it in $Folders) {
        $k = $it.Def.key
        if ($given[$k] -or $Gui) { continue }
        if (Find-AppFolder $it) { continue }
        Write-Host "$($it.Def.title) が見つかりません。$($it.Def.note)" -ForegroundColor Yellow
        while ($true) {
            $v = (Read-Host 'フォルダのパスを入力してください（後で設定するなら空のまま Enter）').Trim().Trim('"')
            if (-not $v -or (Test-AppFolder $it $v)) { break }
            Write-Host "そのフォルダは違うようです: $v" -ForegroundColor Yellow
        }
        if ($v) { $given[$k] = $v }
    }

    # 4. 外部ツール（入っているツールが使うものだけ）
    $a2 = @('-m', 'shared', 'setup')
    foreach ($k in $given.Keys) { if ($given[$k]) { $a2 += @('--folder', "$k=$($given[$k])") } }
    if ($Force) { $a2 += '--force' }
    if ($Gui) { $a2 += '--progress' }
    Run $Py $a2 | Out-Null
    Pop-Location

    # 5. デスクトップのショートカット
    if (-not $NoShortcut) {
        Mark 'shortcut' 'run' 'デスクトップのショートカット'
        $ws = New-Object -ComObject WScript.Shell
        foreach ($a in $AppList) {
            $s = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) "$($a.Name).lnk"))
            $s.TargetPath = $Pyw
            $s.Arguments = "-m $($a.Pkg)"
            $s.WorkingDirectory = $App
            $s.IconLocation = Join-Path $App "$($a.Pkg)\assets\icon.ico"
            $s.Save()
        }
        Mark 'shortcut' 'ok' 'デスクトップのショートカット'
    }

    # 6. Web 画面（ログオン時に自動起動）
    if (-not $NoWeb) {
        Mark 'firewall' 'run' "ファイアウォールの許可（TCP $Port）"
        # 先に待ち受けを始めると Windows の警告が出て、閉じるとブロック規則ができるため、先に設定する
        $fw = Start-Process powershell -PassThru -Wait -WindowStyle Hidden -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$(Join-Path $App 'web_firewall.ps1')`"", '-Port', $Port, '-Name', $WebName
        if ($fw.ExitCode -eq 0) { Mark 'firewall' 'ok' "ファイアウォールの許可（TCP $Port）" }
        else { Mark 'firewall' 'ng' 'ファイアウォールの許可（管理者の確認が拒否されました。後で web_firewall.bat を実行してください）' }

        Mark 'web' 'run' "Web 画面の自動起動（ポート $Port）"
        $act = New-ScheduledTaskAction -Execute $Pyw -Argument "-m shared.web --host 0.0.0.0 --port $Port" -WorkingDirectory $App
        $trg = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
        $set = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        $startLnk = Join-Path ([Environment]::GetFolderPath('Startup')) "$WebName.lnk"
        $viaTask = $false
        try {
            # 自分のユーザーで動くタスクは管理者権限なしで登録できる
            Register-ScheduledTask -TaskName $WebName -Action $act -Trigger $trg -Settings $set -Force -ErrorAction Stop | Out-Null
            $viaTask = $true
            if (Test-Path $startLnk) { Remove-Item $startLnk -Force }
        } catch {
            # 組織のポリシーなどでタスクを登録できない場合は、スタートアップのショートカットで代わりにする（管理者権限不要）
            Say "タスクを登録できないため、スタートアップに登録します（$($_.Exception.Message)）" 'Yellow'
            $ws2 = New-Object -ComObject WScript.Shell
            $l = $ws2.CreateShortcut($startLnk)
            $l.TargetPath = $Pyw; $l.Arguments = "-m shared.web --host 0.0.0.0 --port $Port"; $l.WorkingDirectory = $App
            $l.IconLocation = Join-Path $App "$($AppList[0].Pkg)\assets\icon.ico"; $l.Save()
        }
        if ($viaTask) {
            Stop-ScheduledTask -TaskName $WebName -ErrorAction SilentlyContinue
            Start-ScheduledTask -TaskName $WebName
        } else {
            Start-Process -FilePath $Pyw -ArgumentList "-m shared.web --host 0.0.0.0 --port $Port" -WorkingDirectory $App
        }
        $ok = $false
        for ($i = 0; $i -lt 15 -and -not $ok; $i++) {
            Start-Sleep -Seconds 1
            try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', $Port); $ok = $c.Connected; $c.Close() } catch {}
        }
        $ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254*' } | Select-Object -First 1).IPAddress
        Info 'web' "http://${ip}:$Port/"
        if ($ok) { Mark 'web' 'ok' "Web 画面の自動起動（http://${ip}:$Port）"; Say "Web 画面: http://${ip}:$Port" 'Green' }
        else { Mark 'web' 'ng' "Web 画面が起動していません（ポート $Port が使用中の可能性があります）" }
    }

    # 7. 確認
    Mark 'check' 'run' '環境チェック'
    Push-Location $App
    Run $Py @('-m', 'shared', 'check') | Out-Null
    $fl = @(& $Py -m shared folders)
    Pop-Location
    Mark 'check' 'ok' '環境チェック'
    Say "`nセットアップ完了。" 'Green'
    foreach ($line in $fl) {
        $p = "$line" -split "`t"
        if ($p.Count -lt 3) { continue }
        Info 'folder' "$($p[2])`t$($p[1])"
        if ($p[1]) { Say "$($p[2]): $($p[1])" 'Green' }
        else { Say "$($p[2]) は未設定です。インストーラーを再実行するか、アプリの環境設定で指定してください。" 'Yellow' }
    }
    if ($Gui) { [Console]::Out.WriteLine('@@DONE') }
    exit 0
} catch {
    Say "エラー: $($_.Exception.Message)" 'Red'
    if ($Gui) { [Console]::Out.WriteLine("@@FAIL $($_.Exception.Message)") }
    exit 1
}
