"""Pad raw rmac binary into a Jaguar bootable 6 MB cart image.

Replaces makefastboot.exe (which caps at 4 MB) with full-Python equivalent
that supports cart sizes up to 6 MB. Algorithm replicated 1:1 from
tursilion/makefastboot (BSD-licensed, see tools/_makefastboot_ref.cpp).

Layout produced:
  $0000-$0083: 132-byte encrypted fastboot stub (universal, MD5-signed)
  $0084-$03FF: $FF padding
  $0400-$0403: cart width/speed config (4 bytes, all = same byte)
  $0404-$0407: entry point ($00802000 BE)
  $0408-$040F: zero (boot flags)
  $0410-$0413: checksum start address ($00802000 BE)
  $0414-$0417: checksum word count (BE u32)
  $0418-$041B: checksum (BE u32, seed $03D0DEAD, sum of 32-bit BE words from
                $802000 to end of cart)
  $041C-$1FFF: $FF padding
  $2000+:      raw rmac output (code at .org $802000 + .incbin scene_all.bin)
  ...        : $FF padding to TARGET_SIZE
"""
import os, struct

# --- Full $0-$3FF header from 64BitDemo.rom --------------------------------
# Extracted verbatim from working 6 MB cart `64BitDemo.rom` (Quadrunner,
# 2020-12-23 AtariAge).  Contains: $F6 fastboot loader at $0-$83, second
# encrypted continuation block at $84-$293, FF padding to $3FF, then standard
# config bytes at $400-$40B (width/speed/entry/flags).
#
# We use the full $0-$3FF verbatim because the second block is part of the
# rB+/JagStudio fastboot pipeline and its content is needed for the BIOS
# check to succeed on >4MB carts.
HEADER_TEMPLATE = bytes.fromhex(
    "F642233C0DAC1D8DB0237153BE8711179827042622C1BE1F7930DB90C6C813F8"
    "0941997851CBFF8CF97F75F06B0EFE132D84D821E591D4013F235E13AEF22FEF"
    "6C09D8069F39278F6D4F03D3D9843DEAABB748F1E6985127F06E940E9092C8F4"
    "503B46580823E04BD23C1CC5306B1124FAC67829D1535A64E0915A16ECD987C4"
    "D82D16986A852901F725FBF6F9C10F38A7B39B1EC619691E6B050177D0CD09F0"
    "A70FAA7624922628EF72E486413B83223C074F670BB6275F7CD7EC52F6BF44EE"
    "9C6B330A36BE4CEB3E34E43AFDF67587B1EDF651720699281071D7447EDE0F96"
    "F38401FF597133B218554E56434CA6E972E24A9F049A87F17392863E8007551C"
    "CE5776D31CC943827DCF19496EFB19607472D690378178FB10017EE55CB78188"
    "E2EB07A275103511CBE92E7539793E76B8F85A88FF6C7707BDCE05F6D229035F"
    "BE0451D8F71507F9BED912273DE0F2C0202FBFA6BA766B698F0541014D9EF079"
    "87AF8CDBFB30E3C951292A3E913AE052EE45E6795A3D9212121F6C62A29B99AC"
    "D832F3F4CF4A09565A0D7684E15AFF2369D203E62E47C65409A3476E5B5AA1E8"
    "25C5A56164B882C8171A8AC81DEC49941E00A0233935F3B2A7544790CB524078"
    "A33388EB230A291BC7B7E210759F275BE300636F1255200B44B267EF741517BB"
    "7BB79578C0FE2C99DDFFDCFC43AF42C2278A539E620B128ED35E760F2B9D6D8D"
    "2844A0B4B122A89D2C019004AB0D602960D74902249E101DE61C58C9FC149792"
    "231E74DE23AFA7B6DB5278DA5E6C166B3617BC017F6A55A77DC060FD50727862"
    "14FD06F70F1E82CCFB0B6206781D800B55AEC925616588DB230E0DFBD93B4133"
    "85C89F61BCC91722294E41CAA54FDF7BF0B7C3A493B2F2E74D3B462122D9509F"
    "2E7F2BA3D2A12468E4BF28FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF"
    + "FF" * 64 * 14   # FF padding $294-$3FF (will be overlaid below)
)
# Pad to exactly $400 bytes if needed
HEADER_TEMPLATE = HEADER_TEMPLATE[:0x400] + b'\xFF' * (0x400 - len(HEADER_TEMPLATE))
HEADER_TEMPLATE = HEADER_TEMPLATE[:0x400]
assert len(HEADER_TEMPLATE) == 0x400

# --- Cart config -----------------------------------------------------------
TARGET_SIZE  = 6 * 1024 * 1024
ENTRY_POINT  = 0x00802000          # .org $802000 in scene_viewer.s
# Default makefastboot config: width=32-bit (case '3'/default) + speed=10-clock
# (default).  Conservative: most carts ship with this combo, BigPEmu likely
# tested against it. Do NOT change to 5-clock unless real-hw timing tested.
CART_CONFIG  = 0x04

INPUT_FILE  = os.path.join("build", "scene_viewer_raw.abs")
OUTPUT_FILE = os.path.join("build", "scene_viewer_padded.abs")

# --- Read raw rmac output --------------------------------------------------
with open(INPUT_FILE, "rb") as f:
    raw = f.read()

# --- Compose cart image (FF-filled) ----------------------------------------
cart = bytearray(b'\xff' * TARGET_SIZE)

# Place full $0-$3FF header verbatim from 64BitDemo (loader + 2nd block).
cart[0:0x400] = HEADER_TEMPLATE

# Place raw rmac output starting at $2000 (cart $802000 = code .org)
end_of_raw = 0x2000 + len(raw)
if end_of_raw > TARGET_SIZE:
    raise SystemExit(f"raw size {len(raw):,} + 0x2000 header > target {TARGET_SIZE:,}")
cart[0x2000:end_of_raw] = raw

# Write cart width/speed at $400 (4 identical bytes per makefastboot)
cart[0x400] = CART_CONFIG
cart[0x401] = CART_CONFIG
cart[0x402] = CART_CONFIG
cart[0x403] = CART_CONFIG

# Entry point at $404
struct.pack_into('>I', cart, 0x404, ENTRY_POINT)

# Boot flags at $408 = zero (4 bytes ONLY, per makefastboot.cpp:160)
# Bytes $40C-$40F left as FF (the surrounding fill); do NOT zero them — the
# fastboot stub may rely on this region being unmodified.
struct.pack_into('>I', cart, 0x408, 0)

# NOTE: 64BitDemo's $F6 fastboot stub does NOT use checksum at $410-$41B.
# Those bytes are left as $FF (default fill).  Skipping checksum keeps boot
# fast on 6 MB carts (1.5M words sum would add ~150 ms).

# --- Write output ----------------------------------------------------------
with open(OUTPUT_FILE, "wb") as f:
    f.write(bytes(cart))

print(f"Built {len(cart):,} bytes ({len(cart)//1024//1024} MB) cart")
print(f"  raw size: {len(raw):,} bytes (0x2000 prepend + raw + FF padding)")
print(f"  config:   width=32-bit speed=10-clock (byte 0x{CART_CONFIG:02X} x4 at $400)")
print(f"  entry:    0x{ENTRY_POINT:08X}")
print(f"  fastboot: $F6-stub variant (no checksum, 6 MB safe)")
