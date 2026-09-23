@echo off
rem ==========================================================================
rem JagLBA V6 - build completa
rem   1. rigenera la mappa di memoria (master: tools/memory_map.py)
rem   2. impacchetta le risorse HQR e genera engine_c/hqr_expect.h
rem   3. compila il blob C dell'engine (Docker marsdev-32x, m68k-elf-gcc)
rem   4. assembla il lato RMAC
rem   5. impacchetta la cartuccia agli offset fissi
rem   6. assembla e impacchetta anche l'ALTRO bersaglio (ferro/BigPEmu)
rem
rem L'ordine 2 prima di 3 non e' casuale: il packer HQR calcola i valori attesi
rem delle sonde di M1 e li scrive in engine_c/hqr_expect.h, che il blob C
rem include. Invertirli farebbe compilare il C contro le attese del giro
rem precedente, cioe' farebbe fallire le sonde per un motivo che non c'entra.
rem ==========================================================================
setlocal

rem Le impostazioni di QUESTA macchina stanno in local.cfg (non tracciato,
rem vedi local.cfg.example): LBA1_DIR, la tua copia del gioco (il repository
rem e' BYOA), e JAG_INC, la cartella con jaguar.inc per RMAC. Una variabile
rem d'ambiente gia' definita vince sul file.
if exist local.cfg for /f "usebackq eol=# tokens=1,* delims==" %%a in ("local.cfg") do if not defined %%a set "%%a=%%b"
if not defined LBA1_DIR (echo LBA1_DIR non definito: copia local.cfg.example in local.cfg & exit /b 1)
if not defined JAG_INC (echo JAG_INC non definito: copia local.cfg.example in local.cfg & exit /b 1)
if not exist "%JAG_INC%\jaguar.inc" (echo JAG_INC=%JAG_INC% non contiene jaguar.inc & exit /b 1)

echo [1/5] mappa di memoria
python tools\memory_map.py
if errorlevel 1 (echo MEMORY MAP ERROR & exit /b 1)

rem TUTTE e 120 le scene (S30). Non ci e' voluto il bank switching: gli archivi
rem stanno compressi come li spediva LBA1 tranne BRICK, i payload identici sono
rem deduplicati e le nove schermate a pieno formato di RESS restano fuori. La
rem cartuccia e' 5901532 byte su 6021120, con 116 KB di margine.
echo [2/5] risorse HQR
python tools\hqr_pack.py --scenes tutte
if errorlevel 1 (echo HQR PACK ERROR & exit /b 1)

rem Parser di riferimento della scena: valida il layout su tutte e 120 le scene
rem (il cursore deve arrivare in fondo ESATTO) e genera engine_c/scene_expect.h.
rem Deve girare DOPO il packer, perche' legge il contenitore, e PRIMA del C, che
rem include l'header che produce.
python tools\scene_parse.py
if errorlevel 1 (echo SCENE PARSE ERROR & exit /b 1)

rem Renderizzatore di riferimento del fondo: produce il PNG per il confronto a
rem occhio contro il PC DOS E le attese in engine_c/bg_expect.h. Stessa regola
rem degli altri due: dopo il packer, prima del C.
python tools\bg_ref.py --scene 0
if errorlevel 1 (echo BG REF ERROR & exit /b 1)

rem Riferimento degli attori (M3b): renderizza il fondo CON SOPRA i corpi e
rem genera engine_c/body_expect.h. Dopo bg_ref, perche' parte dal suo fondo.
python tools\body_ref.py
if errorlevel 1 (echo BODY REF ERROR & exit /b 1)

rem Riferimento del FRONT-END (S48): disegna logo Adeline e menu principale con
rem le regole di SOURCES/GAMEMENU.C, ne scrive i PNG in build\ e genera
rem engine_c/menu_expect.h. Stessa regola degli altri generatori: dopo il
rem packer (legge RESS e TEXT), prima del C (che include l'header).
python tools\menu_ref.py
if errorlevel 1 (echo MENU REF ERROR & exit /b 1)

rem Banco di prova su PC (S32). Fa girare LO STESSO engine_c/body_draw.c su x86
rem con i dati veri e confronta le sue sonde di pipeline con body_expect.h.
rem
rem Costa un secondo e separa DUE domande che altrimenti arrivano insieme: "la
rem pipeline calcola i numeri giusti?" e "il Jaguar la esegue come previsto?".
rem Se cade qui, non vale la pena costruire la cartuccia; se tiene qui e cade su
rem BigPEmu, il difetto e' nella mappa, nell'allineamento o nel packer.
python tools\body_host_dump.py
if errorlevel 1 (echo BODY DUMP ERROR & exit /b 1)
docker run --rm -v "%CD%:/work" -w /work/engine_c marsdev-32x:latest "gcc -O2 -Wall -I. -o /tmp/host_body host_body.c && /tmp/host_body /work/build/body_host.bin"
if errorlevel 1 (echo SONDE DEI CORPI CADUTE SU PC & exit /b 1)

rem Banco di prova del FRONT-END (S48). Stessa idea del banco dei corpi: fa
rem girare engine_c/front_menu.c su x86 con i byte veri della cartuccia appena
rem impacchettata e confronta le sue undici sonde con menu_expect.h. Copre i
rem tre formati nuovi - il ritaglio FSCR, il font mascherato, la tabella dei
rem testi - che sono anche i tre modi in cui questa milestone puo' sbagliare
rem senza che il Jaguar c'entri niente.
docker run --rm -v "%CD%:/work" -w /work marsdev-32x:latest "gcc -O2 -Wall -Iengine_c -o /tmp/host_front engine_c/host_front.c && /tmp/host_front cart/data_v6.bin"
if errorlevel 1 (echo SONDE DEL FRONT-END CADUTE SU PC & exit /b 1)

rem S51: JAG_FORK=1 costruisce il blob col motore trapiantato dentro
rem (engine_c/build_fork.sh blob, engine_c/fork/JAGUAR_PORT.md); 0 ricostruisce
rem il blob di S50 (build_engine.sh), per un A/B col percorso vecchio.
if "%JAG_FORK%"=="" set JAG_FORK=1
echo [3/5] blob C dell'engine (JAG_FORK=%JAG_FORK%)
if "%JAG_FORK%"=="1" (
  docker run --rm -v "%CD%:/work" -w /work/engine_c marsdev-32x:latest "sh build_fork.sh blob"
) else (
  docker run --rm -v "%CD%:/work" -w /work/engine_c marsdev-32x:latest "sh build_engine.sh"
)
if errorlevel 1 (echo C BUILD ERROR & exit /b 1)
if not exist engine_c\engine_blob.bin (echo C BUILD: nessun output & exit /b 1)

echo [4/5] assembly RMAC
if not exist build mkdir build
rmac -fb -fr -i"%JAG_INC%"\ -iinclude\ -lbuild\main_v6.lst -o build\main_v6_raw.abs src\main_v6.s
if errorlevel 1 (echo ASSEMBLER ERROR & exit /b 1)

echo [5/5] packer cartuccia
python tools\cart_build.py
if errorlevel 1 (echo PACK ERROR & exit /b 1)

rem [6/6] L'ALTRO bersaglio (S51). Da una copia del sorgente con TARGET_HW
rem rovesciato, cosi' ogni build lascia sia la ROM per il ferro sia quella per
rem BigPEmu, e la seconda si puo' provare sull'emulatore senza toccare il
rem sorgente. Il blob C e i dati sono gli stessi: cambia solo il lato RMAC.
echo [6/6] l'altro bersaglio
python tools\alt_target.py build\_main_v6_alt.s
if errorlevel 1 (echo ALT TARGET ERROR & exit /b 1)
for /f %%h in ('python tools\alt_target.py --hw') do set ALT_HW=%%h
rmac -fb -fr -i"%JAG_INC%"\ -iinclude\ -lbuild\main_v6_alt.lst -o build\main_v6_alt_raw.abs build\_main_v6_alt.s
if errorlevel 1 (echo ASSEMBLER ERROR ALT & exit /b 1)
python tools\cart_build.py --hw %ALT_HW% --code build\main_v6_alt_raw.abs
if errorlevel 1 (echo PACK ERROR ALT & exit /b 1)

echo.
rem Il nome della ROM lo decide TARGET_HW in src\main_v6.s: _hw.j64 oppure
rem _emu.j64. Le due si vedono giuste solo sul rispettivo bersaglio e a occhio
rem sono identiche, quindi il nome e' l'unica difesa contro il provare quella
rem sbagliata e inseguire un fantasma (successo in S33).
echo Build V6 OK.
endlocal
