# ==========================================================================
# emu_grab.ps1 - cattura BigPEmu GIA' IN ESECUZIONE, senza riavviarlo.
#
# emu_shot.ps1 lancia la ROM da zero, ed e' quel che serve quasi sempre. Ma
# alcune prove hanno uno STATO che si raggiunge a mano - la transizione di M2
# si attraversa premendo A, e i tasti PC non sono la mappatura predefinita di
# BigPEmu ([[feedback_bigpemu_interlace_limits]], stessa trappola di S28).
# Riavviare per catturare cancellerebbe proprio quel che si vuole guardare.
#
#   .\tools\emu_grab.ps1 -Out build\dopo_la_porta.png
# ==========================================================================
param(
    [string]$Out = "build\grab.png"
)

$ErrorActionPreference = "Stop"
$outPath = Join-Path (Get-Location) $Out

$p = Get-Process BigPEmuDev -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $p) { throw "BigPEmu non e' in esecuzione (usa emu_shot.ps1 -Keep)" }

Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WG {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
    [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out R r);
    [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref P p);
    public struct R { public int L, T, Rt, B; }
    public struct P { public int X, Y; }
}
"@

$p.Refresh()
$h = $p.MainWindowHandle
if ($h -eq [IntPtr]::Zero) { throw "nessuna finestra di BigPEmu" }
[void][WG]::ShowWindow($h, 9)          # SW_RESTORE
[void][WG]::SetForegroundWindow($h)
Start-Sleep -Milliseconds 800

# Area CLIENT, non la finestra: come in emu_shot.ps1, cornice e barra del
# titolo falserebbero la scala verticale.
$r = New-Object WG+R
[void][WG]::GetClientRect($h, [ref]$r)
$o = New-Object WG+P
[void][WG]::ClientToScreen($h, [ref]$o)

$bmp = New-Object System.Drawing.Bitmap($r.Rt, $r.B)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($o.X, $o.Y, 0, 0, $bmp.Size)
$bmp.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()

Write-Host "catturato $($r.Rt)x$($r.B) -> $outPath"
