param(
    [Parameter(Mandatory=$true)][string]$SourceFolder,
    [Parameter(Mandatory=$true)][string]$OutputFolder,
    [Parameter(Mandatory=$true)][int]$TrackCount,
    [int]$TimeoutMinutes = 90
)

$ErrorActionPreference = 'Stop'
$uvrExe = if ($env:UVR_EXE) { $env:UVR_EXE } else { Join-Path $env:LOCALAPPDATA 'Programs\Ultimate Vocal Remover\UVR.exe' }
if (-not (Test-Path -LiteralPath $uvrExe -PathType Leaf)) { throw "UVR executable not found. Set UVR_EXE to its full path." }
Add-Type -AssemblyName System.Windows.Forms
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class ConsoleWindow {
  [DllImport("kernel32.dll")] public static extern IntPtr GetConsoleWindow();
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
  public static void Minimize() { var h = GetConsoleWindow(); if(h != IntPtr.Zero) ShowWindow(h, 6); }
}
'@
Add-Type @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class UvrWindows {
  public delegate bool EnumCallback(IntPtr h, IntPtr l);
  [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left, Top, Right, Bottom; }
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumCallback c, IntPtr l);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out Rect r);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f,uint a,uint b,uint c,UIntPtr d);
  [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int command);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr after, int x, int y, int cx, int cy, uint flags);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, IntPtr processId);
  [DllImport("user32.dll", EntryPoint="GetWindowThreadProcessId")] public static extern uint GetWindowProcessId(IntPtr hWnd, out uint processId);
  [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
  [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool attach);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern IntPtr SetFocus(IntPtr hWnd);
  public static void Click(int x,int y) { SetCursorPos(x,y); mouse_event(2,0,0,0,UIntPtr.Zero); mouse_event(4,0,0,0,UIntPtr.Zero); }
  public static void DirectClick(IntPtr hWnd, int x, int y) {
    int packed = (y << 16) | (x & 0xffff);
    SendMessage(hWnd, 0x0201, new IntPtr(1), new IntPtr(packed));
    SendMessage(hWnd, 0x0202, IntPtr.Zero, new IntPtr(packed));
  }
  public static void Activate(IntPtr hWnd) {
    IntPtr foreground = GetForegroundWindow();
    uint foregroundThread = GetWindowThreadProcessId(foreground, IntPtr.Zero);
    uint targetThread = GetWindowThreadProcessId(hWnd, IntPtr.Zero);
    uint currentThread = GetCurrentThreadId();
    if(foregroundThread != currentThread) AttachThreadInput(currentThread, foregroundThread, true);
    if(targetThread != currentThread) AttachThreadInput(currentThread, targetThread, true);
    ShowWindow(hWnd, 9);
    SetWindowPos(hWnd, new IntPtr(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040);
    BringWindowToTop(hWnd);
    SetForegroundWindow(hWnd);
    SetFocus(hWnd);
    SetWindowPos(hWnd, new IntPtr(-2), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040);
    if(targetThread != currentThread) AttachThreadInput(currentThread, targetThread, false);
    if(foregroundThread != currentThread) AttachThreadInput(currentThread, foregroundThread, false);
  }
  public static IntPtr Find(string title, uint requiredProcessId) {
    IntPtr found = IntPtr.Zero;
    EnumWindows((h,l) => {
      if(!IsWindowVisible(h)) return true;
      var s = new StringBuilder(300); GetWindowText(h,s,300);
      uint processId; GetWindowProcessId(h, out processId);
      if(s.ToString()==title && processId==requiredProcessId) { found=h; return false; }
      return true;
    }, IntPtr.Zero);
    return found;
  }
}
'@

[ConsoleWindow]::Minimize()
Start-Sleep -Seconds 1

function Wait-Window([string]$Title, [int]$ProcessId, [int]$Seconds = 15) {
    $until = (Get-Date).AddSeconds($Seconds)
    do {
        $handle = [UvrWindows]::Find($Title, [uint32]$ProcessId)
        if($handle -ne [IntPtr]::Zero) { return $handle }
        Start-Sleep -Milliseconds 250
    } while((Get-Date) -lt $until)
    throw "Timed out waiting for window: $Title"
}

function Paste-Text([string]$Text) {
    [System.Windows.Forms.Clipboard]::SetText($Text)
    [System.Windows.Forms.SendKeys]::SendWait('^v')
}

$audio = @(Get-ChildItem -LiteralPath $SourceFolder -File | Where-Object { $_.Extension -match '^\.(mp3|wav|flac|m4a|aac|ogg|wma|aiff|aif)$' } | Sort-Object Name)
if($audio.Count -ne $TrackCount) { throw "Expected $TrackCount tracks, found $($audio.Count)" }
New-Item -ItemType Directory -Path $OutputFolder -Force | Out-Null
Get-Process UVR -ErrorAction SilentlyContinue | Stop-Process -Force
while(Get-Process UVR -ErrorAction SilentlyContinue) { Start-Sleep -Milliseconds 250 }
Start-Sleep -Seconds 2
$process = Start-Process -FilePath $uvrExe -PassThru
$until = (Get-Date).AddSeconds(30)
do { $process.Refresh(); Start-Sleep -Milliseconds 250 } while($process.MainWindowHandle -eq 0 -and (Get-Date) -lt $until)
if($process.MainWindowHandle -eq 0) { throw 'UVR main window did not open' }
$mainWindowHandle = $process.MainWindowHandle
$rect = New-Object UvrWindows+Rect
[UvrWindows]::GetWindowRect($mainWindowHandle, [ref]$rect) | Out-Null
$inputDialog = [IntPtr]::Zero
for($attempt = 1; $attempt -le 3; $attempt++) {
    [UvrWindows]::Activate($mainWindowHandle)
    Start-Sleep -Milliseconds 500
    [UvrWindows]::GetWindowRect($mainWindowHandle, [ref]$rect) | Out-Null
    [UvrWindows]::Click($rect.Left + 130, $rect.Top + 232)
    try {
        $inputDialog = Wait-Window 'Select Audio files' $process.Id 8
        break
    } catch {
        if($attempt -eq 3) { throw }
        Start-Sleep -Seconds 1
    }
}
[UvrWindows]::SetForegroundWindow($inputDialog) | Out-Null
[System.Windows.Forms.SendKeys]::SendWait('%d')
Start-Sleep -Milliseconds 250
Paste-Text $SourceFolder
[System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
Start-Sleep -Seconds 2
$inputRect = New-Object UvrWindows+Rect
[UvrWindows]::GetWindowRect($inputDialog, [ref]$inputRect) | Out-Null
[UvrWindows]::Click($inputRect.Left + 400, $inputRect.Top + 180)
Start-Sleep -Milliseconds 250
[System.Windows.Forms.SendKeys]::SendWait('^a')
Start-Sleep -Milliseconds 250
[System.Windows.Forms.SendKeys]::SendWait('%o')
Start-Sleep -Seconds 3

$process.Refresh(); [UvrWindows]::GetWindowRect($mainWindowHandle, [ref]$rect) | Out-Null
$outputDialog = [IntPtr]::Zero
for($attempt = 1; $attempt -le 3; $attempt++) {
    [UvrWindows]::Activate($mainWindowHandle)
    Start-Sleep -Milliseconds 750
    [UvrWindows]::GetWindowRect($mainWindowHandle, [ref]$rect) | Out-Null
    if($attempt % 2 -eq 1) {
        [UvrWindows]::Click($rect.Left + 130, $rect.Top + 275)
    } else {
        [UvrWindows]::Click($rect.Left + 750, $rect.Top + 275)
    }
    try {
        $outputDialog = Wait-Window 'Select Folder' $process.Id 8
        break
    } catch {
        if($attempt -eq 3) { throw }
        Start-Sleep -Seconds 1
    }
}
[UvrWindows]::SetForegroundWindow($outputDialog) | Out-Null
[System.Windows.Forms.SendKeys]::SendWait('%d')
Start-Sleep -Milliseconds 250
Paste-Text $OutputFolder
[System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
Start-Sleep -Seconds 2
$dialogRect = New-Object UvrWindows+Rect
[UvrWindows]::GetWindowRect($outputDialog, [ref]$dialogRect) | Out-Null
[UvrWindows]::Click($dialogRect.Left + 750, $dialogRect.Top + 505)
Start-Sleep -Seconds 3

$process.Refresh(); [UvrWindows]::GetWindowRect($mainWindowHandle, [ref]$rect) | Out-Null
[UvrWindows]::Activate($mainWindowHandle)
Start-Sleep -Milliseconds 500
[UvrWindows]::GetWindowRect($mainWindowHandle, [ref]$rect) | Out-Null
[UvrWindows]::Click($rect.Left + 390, $rect.Top + 603)

$expected = $TrackCount * 6
$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
do {
    Start-Sleep -Seconds 5
    $mp3 = @(Get-ChildItem -LiteralPath $OutputFolder -Filter '*.mp3' -File -ErrorAction SilentlyContinue)
    $temporary = @(Get-ChildItem -LiteralPath $OutputFolder -Filter '*.wav' -File -ErrorAction SilentlyContinue)
    $complete = $mp3.Count -eq $expected -and $temporary.Count -eq 0 -and @($mp3 | Where-Object Length -eq 0).Count -eq 0
    if($complete) { break }
    if($process.HasExited) { throw "UVR exited early after producing $($mp3.Count) of $expected stems" }
} while((Get-Date) -lt $deadline)
if(-not $complete) { throw "UVR timed out after producing $($mp3.Count) of $expected stems" }
$process.CloseMainWindow() | Out-Null
Start-Sleep -Seconds 2
Get-Process -Id $process.Id -ErrorAction SilentlyContinue | Stop-Process -Force
Write-Output "UVR_COMPLETE=$expected"
