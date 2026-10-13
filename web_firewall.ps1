# Web 画面の受信をファイアウォールで許可する。管理者権限で実行する
#   install.ps1 から自動で呼ばれる。単体でも実行可: web_firewall.bat
param([int]$Port = 8765, [string]$Name = '')
$ErrorActionPreference = 'Continue'
$App = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Name) { . (Join-Path $App 'shared\apps.ps1'); $Name = $WebName }
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    # 自分自身を管理者で起動し直す
    try {
        $p = Start-Process powershell -Verb RunAs -PassThru -Wait -WindowStyle Hidden -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$($MyInvocation.MyCommand.Path)`"", '-Port', $Port, '-Name', $Name
        exit $p.ExitCode
    } catch { Write-Host '管理者権限が得られなかったため、ファイアウォールを設定できませんでした' -ForegroundColor Yellow; exit 1 }
}

# 古い規則を消す
Remove-NetFirewallRule -DisplayName $Name -ErrorAction SilentlyContinue

# 初回起動時の「Windows セキュリティの重要な警告」を閉じると Python へのブロック規則が自動で作られ、
# 許可規則より優先されてしまう。このアプリの Python に対するブロック規則を消す
Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue |
    Where-Object { $_.Program -and $_.Program -like "$App\*" } |
    Get-NetFirewallRule -ErrorAction SilentlyContinue |
    Where-Object { $_.Action -eq 'Block' } |
    ForEach-Object { Write-Host "ブロック規則を削除: $($_.DisplayName)"; Remove-NetFirewallRule -Name $_.Name }

# ポートを許可（ネットワークの種類を問わない。VM はパブリック扱いになることが多い）
New-NetFirewallRule -DisplayName $Name -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -Profile Any | Out-Null
# 実際に待ち受ける Python 本体も許可（venv の pythonw.exe は本体を子プロセスで起動するため両方）
$py = Join-Path $App '.venv\Scripts\python.exe'
$progs = @((Join-Path $App '.venv\Scripts\pythonw.exe'), $py)
if (Test-Path $py) {
    $base = & $py -c "import sys, os; print(os.path.dirname(sys._base_executable))"
    if ($base) { $progs += @((Join-Path $base 'pythonw.exe'), (Join-Path $base 'python.exe')) }
}
foreach ($pg in $progs | Select-Object -Unique) {
    if (Test-Path $pg) {
        New-NetFirewallRule -DisplayName $Name -Direction Inbound -Program $pg -Protocol TCP -LocalPort $Port -Action Allow -Profile Any | Out-Null
    }
}
$n = @(Get-NetFirewallRule -DisplayName $Name -ErrorAction SilentlyContinue).Count
Write-Host "ファイアウォール: TCP $Port の受信を許可しました（規則 $n 件）" -ForegroundColor Green
exit 0
