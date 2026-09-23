@echo off
echo Building JagLBA scene viewer V0...

if not exist build mkdir build

rmac -fb -fr -iD:\Homebrew\Jaguar\include\ -o build\scene_viewer_raw.abs src\scene_viewer.s
if errorlevel 1 (echo ASSEMBLER ERROR & pause & exit /b 1)

python pad_rom.py
if errorlevel 1 (echo PAD ERROR & pause & exit /b 1)

copy build\scene_viewer_padded.abs build\scene_viewer.j64 >nul

echo.
echo Build OK!
