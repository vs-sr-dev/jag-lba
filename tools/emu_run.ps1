# ==========================================================================
# emu_run.ps1 - drive the ROM in BigPEmu from a script and photograph it.
#
#   .\tools\emu_run.ps1 -Press "A@1500+6,A@2100+6" -Watch "front_done" `
#                       -Shots "30,45,60" -Tag s51
#
# 1. tools/emu_drive.py writes Scripts/jaglba_drive.c: presses buttons
#    through the HAL cell EMU_PAD and prints watched variables on the HUD.
#    NOTHING is sent to the desktop keyboard.
# 2. BigPEmuDev compiles every script at startup: delete the old .bigpcvm,
#    start the emulator once, wait for the new one.
# 3. A .bigpcvm with the ROM's name next to the ROM is loaded automatically.
# 4. Run the ROM and capture the emulator WINDOW (PrintWindow, never a copy
#    of the screen: in S51 a screen copy photographed the user's desktop) at
#    each time in -Shots (seconds after launch). Files: build\<Tag>_<t>s.png
# ==========================================================================
param(
    [string]$Rom   = "build\jaglba_v6_emu.j64",
    [string]$Press = "",
    [string]$Watch = "",
    [string]$Sym   = "",
    [int]$Actors   = 0,
    [string]$Poke  = "",
    [string]$Shots = "40",
    [string]$Tag   = "run"
)

$ErrorActionPreference = "Stop"
$root    = Split-Path -Parent $PSScriptRoot
$emu     = "D:\Homebrew5\JagFF4\BigPEmuDEV\BigPEmuDev.exe"
$scripts = "D:\Homebrew5\JagFF4\BigPEmuDEV\Scripts"
$src     = Join-Path $scripts "jaglba_drive.c"
$vm      = Join-Path $scripts "jaglba_drive.bigpcvm"
$romPath = (Resolve-Path (Join-Path $root $Rom)).Path
$romVm   = [System.IO.Path]::ChangeExtension($romPath, ".bigpcvm")

# ---- 1. the script ---------------------------------------------------------
$args1 = @((Join-Path $root "tools\emu_drive.py"), "--out", $src)
if ($Press) { $args1 += @("--press", $Press) }
if ($Watch) { $args1 += @("--watch", $Watch) }
if ($Sym)   { $args1 += @("--sym", $Sym) }
if ($Actors) { $args1 += @("--actors", "$Actors") }
if ($Poke)  { $args1 += @("--poke", $Poke) }
& python @args1
if ($LASTEXITCODE -ne 0) { throw "emu_drive.py failed" }

# ---- 2. compile it ---------------------------------------------------------
Get-Process BigPEmuDev -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 600
Remove-Item $vm -ErrorAction SilentlyContinue
$p = Start-Process -FilePath $emu -PassThru -WindowStyle Minimized
$t0 = Get-Date
while (-not (Test-Path $vm)) {
    if (((Get-Date) - $t0).TotalSeconds -gt 30) {
        Stop-Process -Id $p.Id -Force
        throw "BigPEmuDev did not compile jaglba_drive.c (see its log)"
    }
    Start-Sleep -Milliseconds 300
}
Start-Sleep -Milliseconds 500
Stop-Process -Id $p.Id -Force
Start-Sleep -Milliseconds 600
Copy-Item $vm $romVm -Force
Write-Host "script compiled -> $romVm"

# ---- 3-4. run and photograph -----------------------------------------------
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class W2 {
    [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out R r);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
    public struct R { public int L, T, Rt, B; }
}
"@

$p = Start-Process -FilePath $emu -ArgumentList "`"$romPath`"" -PassThru
$start = Get-Date
foreach ($s in ($Shots.Split(",") | ForEach-Object { [double]$_.Trim() })) {
    while (((Get-Date) - $start).TotalSeconds -lt $s) { Start-Sleep -Milliseconds 100 }
    $p.Refresh()
    $h = $p.MainWindowHandle
    if ($h -eq [IntPtr]::Zero) { Stop-Process -Id $p.Id -Force; throw "no BigPEmu window" }
    # The window may not be sized yet (seen in S51: a 0x0 client area makes
    # the Bitmap constructor throw). Wait for it, a bounded while.
    $r = New-Object W2+R
    $tries = 0
    do {
        [void][W2]::GetClientRect($h, [ref]$r)
        if ($r.Rt -gt 0 -and $r.B -gt 0) { break }
        Start-Sleep -Milliseconds 250; $p.Refresh(); $h = $p.MainWindowHandle
    } while (++$tries -lt 40)
    if ($r.Rt -le 0 -or $r.B -le 0) { Stop-Process -Id $p.Id -Force; throw "BigPEmu window has no area" }
    $bmp = New-Object System.Drawing.Bitmap($r.Rt, $r.B)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $hdc = $g.GetHdc()
    $ok = [W2]::PrintWindow($h, $hdc, 3)       # PW_CLIENTONLY | PW_RENDERFULLCONTENT
    $g.ReleaseHdc($hdc)
    $out = Join-Path $root ("build\{0}_{1}s.png" -f $Tag, $s)
    if ($ok) { $bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png) }
    $g.Dispose(); $bmp.Dispose()
    if (-not $ok) { Stop-Process -Id $p.Id -Force; throw "PrintWindow failed: no capture" }
    Write-Host "shot $s s -> $out"
}
Stop-Process -Id $p.Id -Force
