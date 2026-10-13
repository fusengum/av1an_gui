# インストーラー（画面付き）。このフォルダに入っているツールが対象
# Windows 標準の PowerShell と .NET だけで動く（Python 不要）。実際の処理は install.ps1 -Gui に任せ、進捗行を表示する
$ErrorActionPreference = 'Stop'
$App = Split-Path -Parent $MyInvocation.MyCommand.Path
# 画面が出る前のエラーは非表示ウィンドウの中で消えてしまうため、ダイアログとログで知らせる
trap {
    $msg = "$($_.Exception.Message)`r`n$($_.InvocationInfo.PositionMessage)"
    try { $ld = Join-Path $App 'logs'; New-Item -ItemType Directory -Force $ld | Out-Null
          Set-Content -Path (Join-Path $ld 'install_gui_error.log') -Value $msg -Encoding UTF8 } catch {}
    try { Add-Type -AssemblyName System.Windows.Forms
          [void][System.Windows.Forms.MessageBox]::Show("インストーラーを起動できませんでした。`r`n`r`n$msg`r`n`r`nlogs\install_gui_error.log にも保存しました。install_cli.bat なら文字だけで実行できます。", 'セットアップ', 'OK', 'Error') } catch {}
    exit 1
}
. (Join-Path $App 'shared\apps.ps1')
$MainPkg = if ($AppList.Count) { $AppList[0].Pkg } else { 'shared' }

Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type @'
using System;
using System.Diagnostics;
using System.Collections.Concurrent;
using System.Runtime.InteropServices;
using System.Text;

public class DvmRunner {
    public ConcurrentQueue<string> Lines = new ConcurrentQueue<string>();
    public Process P;
    volatile bool outDone, errDone;
    public bool Finished { get { return P != null && P.HasExited && outDone && errDone; } }
    public int ExitCode { get { return P.ExitCode; } }
    public void Start(string file, string args, string cwd) {
        ProcessStartInfo si = new ProcessStartInfo(file, args);
        si.UseShellExecute = false;
        si.RedirectStandardOutput = true;
        si.RedirectStandardError = true;
        si.CreateNoWindow = true;
        si.StandardOutputEncoding = Encoding.UTF8;
        si.StandardErrorEncoding = Encoding.UTF8;
        si.WorkingDirectory = cwd;
        P = new Process();
        P.StartInfo = si;
        P.OutputDataReceived += (s, e) => { if (e.Data == null) outDone = true; else Lines.Enqueue(e.Data); };
        P.ErrorDataReceived += (s, e) => { if (e.Data == null) errDone = true; else Lines.Enqueue("!" + e.Data); };
        P.Start();
        P.BeginOutputReadLine();
        P.BeginErrorReadLine();
    }
    public void KillTree() {
        try {
            if (P != null && !P.HasExited) {
                ProcessStartInfo k = new ProcessStartInfo("taskkill", "/T /F /PID " + P.Id);
                k.CreateNoWindow = true;
                k.UseShellExecute = false;
                Process.Start(k).WaitForExit(5000);
            }
        } catch { }
    }
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
}
'@
[void][DvmRunner]::SetProcessDPIAware()
[System.Windows.Forms.Application]::EnableVisualStyles()

# ---------------------------------------------------------------- 見た目
$g = [System.Drawing.Graphics]::FromHwnd([IntPtr]::Zero)
$script:DpiScale = $g.DpiX / 96.0   # PowerShell の変数名は大文字小文字を区別しないため、短い名前にしない
$g.Dispose()
function px([double]$v) { [int][Math]::Round($v * $script:DpiScale) }
function rgb($hex) { [System.Drawing.ColorTranslator]::FromHtml($hex) }
$Clr = @{
    Head = rgb '#141B2D'; HeadSub = rgb '#9AA4B8'; Bg = rgb '#F6F7F9'; Card = rgb '#FFFFFF'; Line = rgb '#E3E6EB'
    Text = rgb '#1F2430'; Sub = rgb '#6B7280'; Accent = rgb '#3B82F6'; Ok = rgb '#16A34A'; Ng = rgb '#DC2626'
    Warn = rgb '#D97706'; Track = rgb '#E5E7EB'; Pending = rgb '#B8BEC8'
}
$UiFont = 'Yu Gothic UI'
if (-not ([System.Drawing.FontFamily]::Families | Where-Object { $_.Name -eq $UiFont })) { $UiFont = 'Segoe UI' }
function F([double]$size, [System.Drawing.FontStyle]$style = 'Regular', $family = $UiFont) { New-Object System.Drawing.Font($family, $size, $style) }
$SymFont = 'Segoe UI Symbol'

function New-Label($text, $x, $y, $w, $h, $font = (F 9.5), $color = $Clr.Text) {
    $l = New-Object System.Windows.Forms.Label
    $l.Text = $text; $l.Location = New-Object System.Drawing.Point((px $x), (px $y)); $l.Size = New-Object System.Drawing.Size((px $w), (px $h))
    $l.Font = $font; $l.ForeColor = $color; $l.BackColor = [System.Drawing.Color]::Transparent
    return $l
}
function New-Button($text, $w = 120, $primary = $false) {
    $b = New-Object System.Windows.Forms.Button
    $b.Text = $text; $b.Size = New-Object System.Drawing.Size((px $w), (px 34)); $b.Font = F 9.5
    $b.FlatStyle = 'Flat'; $b.Cursor = 'Hand'
    if ($primary) { $b.BackColor = $Clr.Accent; $b.ForeColor = [System.Drawing.Color]::White; $b.FlatAppearance.BorderSize = 0 }
    else { $b.BackColor = $Clr.Card; $b.ForeColor = $Clr.Text; $b.FlatAppearance.BorderColor = $Clr.Line }
    return $b
}
# 角の無いシンプルなプログレスバー（色を自由に付けるため自作）
function New-Bar($x, $y, $w, $h) {
    $track = New-Object System.Windows.Forms.Panel
    $track.Location = New-Object System.Drawing.Point((px $x), (px $y)); $track.Size = New-Object System.Drawing.Size((px $w), (px $h))
    $track.BackColor = $Clr.Track
    $fill = New-Object System.Windows.Forms.Panel
    $fill.Location = New-Object System.Drawing.Point(0, 0); $fill.Size = New-Object System.Drawing.Size(0, (px $h)); $fill.BackColor = $Clr.Accent
    $track.Controls.Add($fill)
    return @{ Track = $track; Fill = $fill; Value = 0.0; Marquee = $false; Pos = 0 }
}
function Set-Bar($bar, [double]$v) {
    $bar.Marquee = ($v -lt 0)
    if ($v -ge 0) {
        $bar.Value = [Math]::Min(1.0, $v)
        $bar.Fill.Left = 0
        $bar.Fill.Width = [int]($bar.Track.Width * $bar.Value)
    }
}
function Step-Marquee($bar) {
    if (-not $bar.Marquee) { return }
    $w = [int]($bar.Track.Width * 0.25)
    $bar.Pos = ($bar.Pos + (px 8)) % ($bar.Track.Width + $w)
    $bar.Fill.Width = $w
    $bar.Fill.Left = $bar.Pos - $w
}

# ---------------------------------------------------------------- フォーム
$form = New-Object System.Windows.Forms.Form
$form.Text = "$SuiteName セットアップ"
$form.ClientSize = New-Object System.Drawing.Size((px 720), (px 600))
$form.FormBorderStyle = 'FixedSingle'; $form.MaximizeBox = $false; $form.StartPosition = 'CenterScreen'
$form.BackColor = $Clr.Bg; $form.Font = F 9.5
$ico = Join-Path $App "$MainPkg\assets\icon.ico"
if (Test-Path $ico) { $form.Icon = New-Object System.Drawing.Icon($ico) }

# ヘッダー
$head = New-Object System.Windows.Forms.Panel
$head.Dock = 'Top'; $head.Height = px 84; $head.BackColor = $Clr.Head
$png = Join-Path $App "$MainPkg\assets\icon.png"
if (Test-Path $png) {
    $pic = New-Object System.Windows.Forms.PictureBox
    $pic.Image = [System.Drawing.Image]::FromFile($png); $pic.SizeMode = 'Zoom'
    $pic.Location = New-Object System.Drawing.Point((px 20), (px 14)); $pic.Size = New-Object System.Drawing.Size((px 56), (px 56))
    $head.Controls.Add($pic)
}
$title = New-Label "$SuiteName セットアップ" 90 16 600 30 (F 15 'Bold') ([System.Drawing.Color]::White)
$sub = New-Label (($AppList | ForEach-Object { "$($_.Desc)（$($_.Name)）" }) -join ' / ') 92 48 600 22 (F 9) $Clr.HeadSub
$head.Controls.AddRange(@($title, $sub))

# フッター
$foot = New-Object System.Windows.Forms.Panel
$foot.Dock = 'Bottom'; $foot.Height = px 60; $foot.BackColor = $Clr.Card
$footLine = New-Object System.Windows.Forms.Panel
$footLine.Dock = 'Top'; $footLine.Height = 1; $footLine.BackColor = $Clr.Line
$foot.Controls.Add($footLine)
$btnMain = New-Button 'インストール' 140 $true
$btnCancel = New-Button 'キャンセル' 110
$btnLog = New-Button '詳細ログ ▾' 110
$btnMain.Location = New-Object System.Drawing.Point((px (720 - 20 - 140)), (px 13))
$btnCancel.Location = New-Object System.Drawing.Point((px (720 - 20 - 140 - 10 - 110)), (px 13))
$btnLog.Location = New-Object System.Drawing.Point((px 20), (px 13))
$btnLog.Visible = $false
$foot.Controls.AddRange(@($btnMain, $btnCancel, $btnLog))

$body = New-Object System.Windows.Forms.Panel
$body.Dock = 'Fill'; $body.BackColor = $Clr.Bg
$form.Controls.Add($body); $form.Controls.Add($foot); $form.Controls.Add($head)

function New-Card($x, $y, $w, $h) {
    $p = New-Object System.Windows.Forms.Panel
    $p.Location = New-Object System.Drawing.Point((px $x), (px $y)); $p.Size = New-Object System.Drawing.Size((px $w), (px $h))
    $p.BackColor = $Clr.Card; $p.BorderStyle = 'FixedSingle'
    return $p
}

# ================================================================ ページ 1: 設定
$pgOpt = New-Object System.Windows.Forms.Panel
$pgOpt.Dock = 'Fill'
$body.Controls.Add($pgOpt)

$pgOpt.Controls.Add((New-Label 'インストール先' 24 18 300 20 (F 9.5 'Bold')))
$tbApp = New-Object System.Windows.Forms.TextBox
$tbApp.Text = $App; $tbApp.ReadOnly = $true; $tbApp.Location = New-Object System.Drawing.Point((px 24), (px 42)); $tbApp.Width = px 672
$pgOpt.Controls.Add($tbApp)
$lbAppNote = New-Label 'Python・ライブラリ・変換ツールはすべてこのフォルダの中に入ります（PC 全体には入れません）。管理者権限は不要です' 24 68 672 20 (F 8.5) $Clr.Sub
$pgOpt.Controls.Add($lbAppNote)
$appWritable = $true
try { $t = Join-Path $App '.write_test'; [IO.File]::WriteAllText($t, 'x'); Remove-Item $t -Force } catch { $appWritable = $false }
if (-not $appWritable) {
    $lbAppNote.Text = '✖  このフォルダには書き込めません。Program Files などではなく、C:\tools のような場所に移してから実行してください'
    $lbAppNote.ForeColor = $Clr.Ng
}

# 手動で指定するフォルダ（自動取得できない外部ツールの場所など。必要なツールが入っている場合だけ表示）
$folderRows = @()
$fy = 100
foreach ($it in $Folders) {
    $fd = $it.Def
    $card = New-Card 24 $fy 672 118
    $pgOpt.Controls.Add($card)
    $card.Controls.Add((New-Label ([string]$fd.title) 14 10 600 22 (F 9.5 'Bold')))
    $card.Controls.Add((New-Label ([string]$fd.note) 14 32 640 20 (F 8.5) $Clr.Sub))
    $tb = New-Object System.Windows.Forms.TextBox
    $tb.Location = New-Object System.Drawing.Point((px 14), (px 58)); $tb.Width = px 540
    $bt = New-Button '参照…' 90
    $bt.Location = New-Object System.Drawing.Point((px 566), (px 55)); $bt.Height = px 28
    $lb = New-Label '' 14 88 640 22 (F 9)
    $card.Controls.AddRange(@($tb, $bt, $lb))
    $row = @{ Item = $it; Text = $tb; Status = $lb }
    $tb.Tag = $row; $bt.Tag = $row
    $tb.Add_TextChanged({ param($sender, $e) Update-FolderStatus $sender.Tag })
    $bt.Add_Click({
        param($sender, $e)
        $r = $sender.Tag
        $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
        $dlg.Description = [string]$r.Item.Def.title
        if ($r.Text.Text -and (Test-Path $r.Text.Text)) { $dlg.SelectedPath = $r.Text.Text }
        if ($dlg.ShowDialog($form) -eq 'OK') { $r.Text.Text = $dlg.SelectedPath }
    })
    $folderRows += $row
    $fy += 132
}
function Update-FolderStatus($row) {
    $d = $row.Text.Text.Trim().Trim('"')
    $fd = $row.Item.Def
    if (-not $d) { $row.Status.Text = '○  未指定（後でアプリの環境設定から指定できます）'; $row.Status.ForeColor = $Clr.Sub }
    elseif (Test-AppFolder $row.Item $d) {
        $miss = if ($fd.extra_file -and -not (Test-Path (Join-Path $d $fd.extra_file))) { "（$($fd.extra_file) がありません）" } else { '' }
        $row.Status.Text = "✔  確認できました$miss"; $row.Status.ForeColor = $(if ($miss) { $Clr.Warn } else { $Clr.Ok })
    } else { $row.Status.Text = "✖  このフォルダに $($fd.file) がありません"; $row.Status.ForeColor = $Clr.Ng }
}
foreach ($row in $folderRows) { $row.Text.Text = Find-AppFolder $row.Item; Update-FolderStatus $row }

# Web 画面
$cardWeb = New-Card 24 232 672 112
$pgOpt.Controls.Add($cardWeb)
$cbWeb = New-Object System.Windows.Forms.CheckBox
$cbWeb.Text = 'Web 画面を使う（ログオン時に自動起動。ブラウザから別の PC でも操作できます）'
$cbWeb.Checked = $true; $cbWeb.Font = F 9.5 'Bold'
$cbWeb.Location = New-Object System.Drawing.Point((px 14), (px 10)); $cbWeb.Size = New-Object System.Drawing.Size((px 640), (px 24))
$cardWeb.Controls.Add($cbWeb)
$cardWeb.Controls.Add((New-Label 'ポート' 36 44 60 22))
$nuPort = New-Object System.Windows.Forms.NumericUpDown
$nuPort.Minimum = 1024; $nuPort.Maximum = 65535
# 既定のポート: Web 画面の設定（settings_web.json）か登録済みの自動起動のポート。無ければ 8765 から空いている番号（別のツールの Web 画面とぶつけない）
function Test-PortFree([int]$p) {
    try { $l = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Any, $p); $l.Start(); $l.Stop(); return $true } catch { return $false }
}
$defPort = 0
try { $sw = Join-Path $App 'settings_web.json'; if (Test-Path $sw) { $p0 = (Get-Content $sw -Raw -Encoding UTF8 | ConvertFrom-Json).port; if ($p0) { $defPort = [int]$p0 } } } catch {}
if (-not $defPort) { try {
    $tk = Get-ScheduledTask -TaskName $WebName -ErrorAction Stop
    if ("$($tk.Actions[0].Arguments)" -match '--port\s+(\d+)') { $defPort = [int]$Matches[1] }
} catch {} }
if (-not $defPort) {
    $lnk = Join-Path ([Environment]::GetFolderPath('Startup')) "$WebName.lnk"
    if (Test-Path $lnk) { try { $a0 = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk).Arguments; if ($a0 -match '--port\s+(\d+)') { $defPort = [int]$Matches[1] } } catch {} }
}
if (-not $defPort) { $defPort = 8765; while ($defPort -lt 8800 -and -not (Test-PortFree $defPort)) { $defPort++ } }
$nuPort.Value = $defPort
$nuPort.Location = New-Object System.Drawing.Point((px 96), (px 41)); $nuPort.Width = px 90
$cardWeb.Controls.Add($nuPort)
$cardWeb.Controls.Add((New-Label 'ファイアウォールの受信許可も追加します。ここだけ管理者権限が必要なので、途中で確認（UAC）が 1 回出ます。認証は無いので LAN 内だけで使ってください' 36 70 620 36 (F 8.5) $Clr.Sub))
$cbWeb.Add_CheckedChanged({ $nuPort.Enabled = $cbWeb.Checked })

# その他
$cbShortcut = New-Object System.Windows.Forms.CheckBox
$cbShortcut.Text = 'デスクトップにショートカットを作る'; $cbShortcut.Checked = $true
$cbShortcut.Location = New-Object System.Drawing.Point((px 26), (px 360)); $cbShortcut.Size = New-Object System.Drawing.Size((px 400), (px 24))
$cbForce = New-Object System.Windows.Forms.CheckBox
$cbForce.Text = '変換ツールを取り直す（不具合があるときの再インストール）'
$cbForce.Location = New-Object System.Drawing.Point((px 26), (px 388)); $cbForce.Size = New-Object System.Drawing.Size((px 500), (px 24))
$pgOpt.Controls.AddRange(@($cbShortcut, $cbForce))
$lbDl = New-Label 'ダウンロードは合計 数百 MB です。回線にもよりますが 5〜15 分ほどかかります' 24 424 672 20 (F 8.5) $Clr.Sub
$pgOpt.Controls.Add($lbDl)
# フォルダ指定の欄の数に合わせて下の欄をずらす（配置は 1 欄ある前提で作ってある）
$shift = 132 * ($Folders.Count - 1)
if ($shift) { foreach ($ctl in @($cardWeb, $cbShortcut, $cbForce, $lbDl)) { $ctl.Top += (px $shift) } }

# ================================================================ ページ 2: 進捗
$pgRun = New-Object System.Windows.Forms.Panel
$pgRun.Dock = 'Fill'; $pgRun.Visible = $false
$body.Controls.Add($pgRun)

$lbAll = New-Label '準備中…' 24 16 520 22 (F 10 'Bold')
$lbAllPct = New-Label '0%' 576 16 120 22 (F 10 'Bold') $Clr.Accent
$lbAllPct.TextAlign = 'TopRight'
$barAll = New-Bar 24 42 672 10
$pgRun.Controls.AddRange(@($lbAll, $lbAllPct, $barAll.Track))

$cardSteps = New-Card 24 66 672 300
$cardSteps.AutoScroll = $true
$pgRun.Controls.Add($cardSteps)

$lbNow = New-Label '' 24 378 672 20 (F 9) $Clr.Text
$lbNowSub = New-Label '' 24 398 672 20 (F 8.5) $Clr.Sub
$lbNowSub.AutoEllipsis = $true
$barNow = New-Bar 24 422 672 6
$pgRun.Controls.AddRange(@($lbNow, $lbNowSub, $barNow.Track))

$rtLog = New-Object System.Windows.Forms.RichTextBox
$rtLog.Location = New-Object System.Drawing.Point((px 24), (px 66)); $rtLog.Size = New-Object System.Drawing.Size((px 672), (px 300))
$rtLog.ReadOnly = $true; $rtLog.BackColor = rgb '#0F1420'; $rtLog.ForeColor = rgb '#D5DAE3'
$rtLog.Font = F 8.5 'Regular' 'Consolas'; $rtLog.BorderStyle = 'None'; $rtLog.Visible = $false; $rtLog.WordWrap = $false
$pgRun.Controls.Add($rtLog)

# 手順の行
$steps = [ordered]@{}
function Add-Step($id, $label) {
    if ($steps.Contains($id)) { return }
    $i = $steps.Count
    $y = 8 + $i * 22
    $ic = New-Label '○' 14 $y 26 24 (F 11 'Regular' $SymFont) $Clr.Pending
    $tx = New-Label $label 42 ($y + 1) 470 22 (F 9.5) $Clr.Sub
    $stx = New-Label '' 512 ($y + 1) 140 22 (F 9) $Clr.Sub
    $stx.TextAlign = 'TopRight'
    $cardSteps.Controls.AddRange(@($ic, $tx, $stx))
    $steps[$id] = @{ Icon = $ic; Text = $tx; Status = $stx; S = 'pending'; T0 = $null }
}
$spin = @('◐', '◓', '◑', '◒')
$tips = New-Object System.Windows.Forms.ToolTip

# ================================================================ ページ 3: 完了
$pgDone = New-Object System.Windows.Forms.Panel
$pgDone.Dock = 'Fill'; $pgDone.Visible = $false
$body.Controls.Add($pgDone)
$lbBig = New-Label '✔' 24 24 64 64 (F 36 'Regular' $SymFont) $Clr.Ok
$lbDoneTitle = New-Label 'セットアップ完了' 96 30 560 34 (F 16 'Bold')
$lbDoneSub = New-Label '' 98 66 580 22 (F 9.5) $Clr.Sub
$pgDone.Controls.AddRange(@($lbBig, $lbDoneTitle, $lbDoneSub))
$cardSum = New-Card 24 110 672 150
$pgDone.Controls.Add($cardSum)
$lbSumInfo = New-Label '' 14 8 640 48 (F 9.5)
$lbSumWebT = New-Label 'Web 画面' 14 60 120 22 (F 9.5 'Bold')
$lnkWeb = New-Object System.Windows.Forms.LinkLabel
$lnkWeb.Location = New-Object System.Drawing.Point((px 140), (px 60)); $lnkWeb.Size = New-Object System.Drawing.Size((px 500), (px 22)); $lnkWeb.Font = F 9.5
$lbSumNote = New-Label '' 14 92 640 48 (F 8.5) $Clr.Sub
$cardSum.Controls.AddRange(@($lbSumInfo, $lbSumWebT, $lnkWeb, $lbSumNote))
$lnkWeb.Add_LinkClicked({ Start-Process $lnkWeb.Text })
function Start-App($mod) {
    $pyw = Join-Path $App '.venv\Scripts\pythonw.exe'
    if (Test-Path $pyw) { Start-Process -FilePath $pyw -ArgumentList "-m $mod" -WorkingDirectory $App }
}
# 入っているツールごとに起動ボタン
$runButtons = @()
$bx = 24
foreach ($ap in $AppList) {
    $b = New-Button "$($ap.Name) を起動" 205 $true
    $b.Location = New-Object System.Drawing.Point((px $bx), (px 280))
    $b.Tag = $ap.Pkg
    $b.Add_Click({ param($sender, $e) Start-App $sender.Tag })
    $pgDone.Controls.Add($b)
    $runButtons += $b
    $bx += 220
}

# ================================================================ 実行
$st = @{ Runner = $null; Running = $false; Tick = 0; Web = ''; FolderInfo = @(); Failed = @(); Fatal = ''; Current = $null; NowFrac = -1.0 }

function Log-Line([string]$line, $color = $null) {
    $rtLog.SelectionStart = $rtLog.TextLength
    $rtLog.SelectionColor = $(if ($color) { $color } else { $rtLog.ForeColor })
    $rtLog.AppendText($line + "`n")
    if ($rtLog.Visible) { $rtLog.ScrollToCaret() }
}

function Set-StepState($id, $state, $label) {
    if (-not $steps.Contains($id)) { Add-Step $id ($label -replace '（導入済み）$', '') }
    $r = $steps[$id]
    if ($state -eq 'ng' -and $label -match '[:：]\s*(.+)$') { $lbNowSub.Text = $Matches[1]; $tips.SetToolTip($r.Status, $Matches[1]) }
    $r.S = $state
    switch ($state) {
        'run' {
            $r.Icon.ForeColor = $Clr.Accent; $r.Text.ForeColor = $Clr.Text; $r.Text.Font = F 9.5 'Bold'
            $r.Status.Text = '処理中'; $r.Status.ForeColor = $Clr.Accent; $r.T0 = Get-Date
            $st.Current = $id; $st.NowFrac = -1.0
            $lbNow.Text = $r.Text.Text; $lbNowSub.Text = ''; Set-Bar $barNow -1
        }
        'ok' { $r.Icon.Text = '✔'; $r.Icon.ForeColor = $Clr.Ok; $r.Status.Text = (Elapsed $r); $r.Status.ForeColor = $Clr.Ok }
        'skip' { $r.Icon.Text = '✔'; $r.Icon.ForeColor = $Clr.Pending; $r.Status.Text = '導入済み'; $r.Status.ForeColor = $Clr.Sub }
        'ng' { $r.Icon.Text = '✖'; $r.Icon.ForeColor = $Clr.Ng; $r.Status.Text = '失敗'; $r.Status.ForeColor = $Clr.Ng; $st.Failed += $label }
    }
    if ($state -ne 'run') {
        $r.Text.Font = F 9.5
        $r.Text.ForeColor = $(if ($state -eq 'ng') { $Clr.Ng } else { $Clr.Text })
        if ($st.Current -eq $id) { $st.Current = $null }
    }
    Update-Overall
}
function Elapsed($r) {
    if (-not $r.T0) { return '完了' }
    $s = [int]((Get-Date) - $r.T0).TotalSeconds
    if ($s -lt 60) { return "完了  ${s} 秒" } else { return ('完了  {0}:{1:00}' -f [int][Math]::Floor($s / 60), ($s % 60)) }
}
function Update-Overall {
    $n = $steps.Count; if ($n -eq 0) { return }
    $done = @($steps.Values | Where-Object { $_.S -in 'ok', 'skip', 'ng' }).Count
    $part = 0.0
    if ($st.Current -and $st.NowFrac -ge 0) { $part = $st.NowFrac }
    $v = ($done + $part) / $n
    Set-Bar $barAll $v
    $lbAllPct.Text = ('{0}%' -f [int]($v * 100))
    $lbAll.Text = "インストール中…  $done / $n"
}

function Handle-Line([string]$line) {
    if ($line.StartsWith('@@STEP ')) {
        $p = $line.Substring(7).Split(' ', 3)
        Set-StepState $p[0] $p[1] $(if ($p.Count -gt 2) { $p[2] } else { $p[0] })
        Log-Line ('── ' + $line.Substring(7)) (rgb '#7AA2F7')
    } elseif ($line.StartsWith('@@PROG ')) {
        $p = $line.Substring(7).Split(' ', 2)
        $f = 0.0; [void][double]::TryParse($p[0], [Globalization.NumberStyles]::Float, [Globalization.CultureInfo]::InvariantCulture, [ref]$f)
        $st.NowFrac = $f
        Set-Bar $barNow $f
        if ($p.Count -gt 1) { $lbNowSub.Text = $p[1] }
        Update-Overall
    } elseif ($line.StartsWith('@@INFO ')) {
        $p = $line.Substring(7).Split(' ', 2)
        $v = $(if ($p.Count -gt 1) { $p[1] } else { '' })
        if ($p[0] -eq 'web') { $st.Web = $v } elseif ($p[0] -eq 'folder') { $st.FolderInfo += $v }
    } elseif ($line.StartsWith('@@DONE')) {
    } elseif ($line.StartsWith('@@FAIL ')) {
        $st.Fatal = $line.Substring(7)
        Log-Line $line $Clr.Ng
    } else {
        $err = $line.StartsWith('!')
        $t = $(if ($err) { $line.Substring(1) } else { $line })
        if ($t.Trim()) {
            Log-Line $t $(if ($err -or $t -match 'エラー|失敗|error|Error') { rgb '#F7768E' } else { $null })
            if ($st.NowFrac -lt 0) { $lbNowSub.Text = $t.Trim() }   # 割合が分からない処理は最新の出力を見せる
        }
    }
}

function Show-Done([int]$code) {
    $st.Running = $false
    $pgRun.Visible = $false; $pgDone.Visible = $true
    $btnCancel.Visible = $false
    $btnMain.Text = '閉じる'; $btnMain.Enabled = $true
    $btnLog.Visible = $true
    if ($code -ne 0 -or $st.Fatal) {
        $lbBig.Text = '✖'; $lbBig.ForeColor = $Clr.Ng
        $lbDoneTitle.Text = 'セットアップに失敗しました'
        $lbDoneSub.Text = $(if ($st.Fatal) { $st.Fatal } else { "終了コード $code。詳細ログを確認してください" })
        foreach ($b in $runButtons) { $b.Enabled = $false }
    } elseif ($st.Failed.Count) {
        $lbBig.Text = '!'; $lbBig.ForeColor = $Clr.Warn; $lbBig.Font = F 36 'Bold'
        $lbDoneTitle.Text = 'セットアップ完了（一部失敗）'
        $lbDoneSub.Text = '失敗: ' + (($st.Failed | ForEach-Object { ($_ -split ':')[0] }) -join '、') + '　— もう一度インストーラーを実行すると取り直します'
    } else {
        $lbDoneSub.Text = 'デスクトップのショートカット、またはこの下のボタンから起動できます'
    }
    $lines = @("入れたツール:  $SuiteName")
    $warn = $false
    foreach ($fi in $st.FolderInfo) {
        $p = "$fi" -split "`t"
        if ($p[1]) { $lines += "$($p[0]):  $($p[1])" } else { $lines += "$($p[0]):  未設定（使うときはアプリの環境設定で指定してください）"; $warn = $true }
    }
    $lbSumInfo.Text = $lines -join "`n"
    $lbSumInfo.ForeColor = $(if ($warn) { $Clr.Warn } else { $Clr.Text })
    if ($st.Web) { $lnkWeb.Text = $st.Web; $lbSumNote.Text = '同じ LAN の PC やスマートフォンのブラウザから開けます。ログオンすると自動で起動します' }
    else { $lnkWeb.Text = ''; $lbSumWebT.Text = 'Web 画面'; $lnkWeb.Visible = $false; $lbSumNote.Text = 'Web 画面は使わない設定です（インストーラーを再実行すると有効にできます）' }
}

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 120
$timer.Add_Tick({
  try {
    $st.Tick++
    $r = $st.Runner
    if ($r) {
        $line = $null; $k = 0
        while ($k -lt 200 -and $r.Lines.TryDequeue([ref]$line)) { Handle-Line $line; $k++ }
    }
    if ($st.Current -and $steps.Contains($st.Current)) { $steps[$st.Current].Icon.Text = $spin[$st.Tick % 4] }
    Step-Marquee $barNow
    if ($st.Running -and $r -and $r.Finished -and $r.Lines.IsEmpty) { $timer.Stop(); Show-Done $r.ExitCode }
  } catch { Log-Line ("画面の更新でエラー: " + $_.Exception.Message) $Clr.Ng }
})

function Start-Install {
    $pgOpt.Visible = $false; $pgRun.Visible = $true
    $btnMain.Enabled = $false; $btnMain.Text = 'インストール中…'
    $btnLog.Visible = $true
    # 入っているツールが使う外部ツールだけを並べる（各ツールの app.json の setup_steps。実際の処理も同じツールが対象）
    $rows = @(@('uv', 'uv（Python の管理ツール）'), @('python', 'Python 3.12 以上'), @('libs', 'ライブラリ（画面・Web・解析）'))
    foreach ($ap in $AppList) { foreach ($x in $ap.SetupSteps) { $rows += , @($x[0], $x[1]) } }
    foreach ($stp in $rows) { Add-Step $stp[0] $stp[1] }
    if ($cbShortcut.Checked) { Add-Step 'shortcut' 'デスクトップのショートカット' }
    if ($cbWeb.Checked) { Add-Step 'firewall' "ファイアウォールの許可（TCP $($nuPort.Value)）"; Add-Step 'web' "Web 画面の自動起動（ポート $($nuPort.Value)）" }
    Add-Step 'check' '環境チェック'
    Update-Overall

    $a = "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $App 'install.ps1')`" -Gui -Port $($nuPort.Value)"
    if (-not $cbWeb.Checked) { $a += ' -NoWeb' }
    if (-not $cbShortcut.Checked) { $a += ' -NoShortcut' }
    if ($cbForce.Checked) { $a += ' -Force' }
    $fl = @()
    foreach ($row in $folderRows) {
        $d = $row.Text.Text.Trim().Trim('"').TrimEnd('\')
        if ($d -and (Test-AppFolder $row.Item $d)) { $fl += "$($row.Item.Def.key)=$d" }
    }
    if ($fl.Count) { $a += " -Folder `"$($fl -join '|')`"" }
    $st.Runner = New-Object DvmRunner
    $st.Runner.Start((Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'), $a, $App)
    $st.Running = $true
    $timer.Start()
}

$btnMain.Add_Click({
    try { if ($pgOpt.Visible) { Start-Install } else { $form.Close() } }
    catch { [void][System.Windows.Forms.MessageBox]::Show($form, "インストーラーの内部エラー:`n$($_.Exception.Message)`n$($_.InvocationInfo.PositionMessage)", 'エラー', 'OK', 'Error') }
})
$btnCancel.Add_Click({ $form.Close() })
$btnLog.Add_Click({
    if ($pgDone.Visible) { $pgDone.Visible = $false; $pgRun.Visible = $true }
    $rtLog.Visible = -not $rtLog.Visible
    $cardSteps.Visible = -not $rtLog.Visible
    $btnLog.Text = $(if ($rtLog.Visible) { '手順に戻す ▴' } else { '詳細ログ ▾' })
    if ($rtLog.Visible) { $rtLog.SelectionStart = $rtLog.TextLength; $rtLog.ScrollToCaret() }
    if (-not $st.Running -and -not $rtLog.Visible) { $pgRun.Visible = $false; $pgDone.Visible = $true }
})
$form.Add_FormClosing({
    param($s, $e)
    if ($st.Running) {
        $ans = [System.Windows.Forms.MessageBox]::Show($form, "インストールを中止しますか？`n途中まで入れたものは残ります（もう一度実行すると続きから入ります）", '中止の確認', 'YesNo', 'Question')
        if ($ans -ne 'Yes') { $e.Cancel = $true; return }
        $st.Runner.KillTree()
    }
    $timer.Stop()
})

if (-not $appWritable) { $btnMain.Enabled = $false }
[void]$form.ShowDialog()
