# ==========================================================================
# emu_shot.ps1 - lancia la ROM in BigPEmu e cattura lo schermo.
#
# In S25 questa procedura e' stata rifatta a mano ogni volta e poi persa.
# Qui e' un file, perche' ogni misura dell'interlace passa da uno screenshot
# e la misura va poter essere ripetuta identica.
#
#   .\tools\emu_shot.ps1 -Out build\shot.png -Wait 8
#
# -Keep lascia l'emulatore aperto (per guardare col proprio occhio o per
# scattare piu' volte e confrontare due frame).
#
# -Hold tiene premuti dei tasti MENTRE si scatta. Nomi separati da virgola e
# racchiusi fra apici (senza, PowerShell li passa come array e arrivano uniti
# da spazi): UP DOWN LEFT RIGHT Z X C ENTER SPACE.
#
#   .\tools\emu_shot.ps1 -Out build\pad.png -Hold "DOWN,Z" -Wait 8
#
# ATTENZIONE, provato in S28 e NON funziona per verificare l'input: le frecce
# non sono la mappatura predefinita di BigPEmu, quindi il gioco non vede niente
# e lo screenshot mostra zero - che si scambia per "il pad non si legge". Il
# modo giusto e' un altro: le righe 12 e 13 della diagnostica ACCUMULANO i bit
# mai visti premuti (engine_c/hal_input.c), quindi si prova il controller a
# mano e si scatta dopo, senza sincronizzare niente. -Hold resta perche' e' utile
# per altro (mettere in pausa, aprire un menu), ma non per questo.
# ==========================================================================
param(
    [string]$Rom  = "build\jaglba_v6.j64",
    [string]$Out  = "build\shot.png",
    [int]$Wait    = 8,
    [string]$Hold = "",
    [switch]$Keep
)

$ErrorActionPreference = "Stop"
$emu = "D:\Homebrew5\JagFF4\BigPEmuDEV\BigPEmuDev.exe"

$romPath = (Resolve-Path $Rom).Path
$outPath = Join-Path (Get-Location) $Out

Get-Process BigPEmuDev -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 600

Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class W {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
    [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out R r);
    [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref P p);
    [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, IntPtr extra);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
    public struct R { public int L, T, Rt, B; }
    public struct P { public int X, Y; }
}
"@

$VK = @{ "UP"=0x26; "DOWN"=0x28; "LEFT"=0x25; "RIGHT"=0x27;
         "Z"=0x5A; "X"=0x58; "C"=0x43; "ENTER"=0x0D; "SPACE"=0x20 }
$held = @()
if ($Hold -ne "") {
    foreach ($k in $Hold.Split(",")) {
        $n = $k.Trim().ToUpper()
        if (-not $VK.ContainsKey($n)) { throw "tasto non riconosciuto: $n" }
        $held += [byte]$VK[$n]
    }
}

Write-Host "avvio: $romPath"
$p = Start-Process -FilePath $emu -ArgumentList "`"$romPath`"" -PassThru
Start-Sleep -Seconds $Wait

$p.Refresh()
$h = $p.MainWindowHandle
if ($h -eq [IntPtr]::Zero) { throw "nessuna finestra di BigPEmu" }
[void][W]::ShowWindow($h, 9)          # SW_RESTORE
[void][W]::SetForegroundWindow($h)
Start-Sleep -Milliseconds 800

# Area CLIENT, non la finestra: cornice e barra del titolo falserebbero la
# scala verticale, che e' la cosa che qui bisogna misurare.
$r = New-Object W+R
[void][W]::GetClientRect($h, [ref]$r)
$o = New-Object W+P
[void][W]::ClientToScreen($h, [ref]$o)

# Premi e TIENI: mezzo secondo prima dello scatto sono ~15 letture del pad a
# 30 Hz, abbastanza perche' lo stato sia stabile e non un caso fortunato.
if ($held.Count -gt 0) {
    foreach ($vk in $held) { [W]::keybd_event($vk, 0, 0, [IntPtr]::Zero) }
    Write-Host "tenuti premuti: $Hold"
    Start-Sleep -Milliseconds 500
}

# S51: MAI copiare lo schermo alla cieca. Windows puo' rifiutare
# SetForegroundWindow a un processo in secondo piano, e allora CopyFromScreen
# fotografa quel che sta SOPRA l'emulatore - in S51 ha catturato il desktop
# dell'utente. Prima PrintWindow (il contenuto lo chiede alla finestra stessa,
# PW_RENDERFULLCONTENT = 2 per le finestre DirectX); la copia dallo schermo
# resta solo come ripiego, e solo se la finestra in primo piano E' BigPEmu.
$bmp = New-Object System.Drawing.Bitmap($r.Rt, $r.B)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
$ok = [W]::PrintWindow($h, $hdc, 3)   # PW_CLIENTONLY | PW_RENDERFULLCONTENT
$g.ReleaseHdc($hdc)
if (-not $ok) {
    if ([W]::GetForegroundWindow() -ne $h) {
        foreach ($vk in $held) { [W]::keybd_event($vk, 0, 2, [IntPtr]::Zero) }
        Stop-Process -Id $p.Id -Force
        throw "PrintWindow fallita e BigPEmu non e' in primo piano: niente cattura"
    }
    $g.CopyFromScreen($o.X, $o.Y, 0, 0, $bmp.Size)
}
$bmp.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()

# Rilascio SEMPRE, anche con -Keep: un tasto lasciato premuto resta premuto per
# tutto il sistema, non solo per l'emulatore.
foreach ($vk in $held) { [W]::keybd_event($vk, 0, 2, [IntPtr]::Zero) }   # KEYEVENTF_KEYUP

Write-Host "catturato $($r.Rt)x$($r.B) -> $outPath"
if (-not $Keep) { Stop-Process -Id $p.Id -Force }
