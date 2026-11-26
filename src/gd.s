; SPDX-License-Identifier: MIT
; Copyright (c) 2026 Samuele Voltan.  See LICENSE.
;===========================================================================
; The GameDrive's debug channel: lines to COM8 on the PC.
;
; JagLBA (S53): from JaguarMI src/gd.s, with one change: it is POSITION
; INDEPENDENT (GD_SEND_FN/GD_CALL_FN are set PC-relative), because JagLBA runs
; from the cartridge and src/main_v6.s copies this block to GD_CODE in DRAM and
; calls it there.  Called from code in the cartridge the GD BIOS install hung
; the boot on hardware (S53: black screen, no line; the same ROM without
; GdInstall booted).  Every project that uses the channel runs from DRAM.
; The cells it uses
; (GD_BIOS, GD_SEND_FN, GD_CALL_FN, GD_STATE, GD_VER, GD_PEND, GD_DROP,
; GD_TIMEOUT) and GDB_BUF are declared by tools/memory_map.py.
;
; The JaguarDocs bench's code (tests/bench/jag/bench_lib.s), itself Prowl's
; boot.s gd_install and hal.s gd_send - the user's own, proven on this
; Jaguar; protocol and traps in JaguarDocs metodi/gamedrive_usb.md.  Every
; wait has a ceiling: no GameDrive, an old firmware or nobody reading COM8
; cost a dropped line, never a stopped machine.  NEVER the GD BIOS's own
; GD_DebugString, which waits with no ceiling for the PC to read the line.
;
;   GdInstall   at boot, interrupts off: GD_BIOS = the BIOS's base, or 0
;               (nothing is ever sent), GD_STATE = where it failed
;   GdSendC     C ABI: void gd_send(const char *), via the GD_SEND_FN cell
;               (0 when there is no GameDrive)
;   GdCallC     C ABI: long gd_call(long fn, long d0, long d1, void *a0), via
;               the GD_CALL_FN cell: a GD BIOS function (the SD card's files)
;===========================================================================
GD_STATUS       EQU     $F16002
GD_DATA         EQU     $F16004
GD_DATA_BYTE    EQU     $F16005
GD_PACKET       EQU     $10
GD_SELECT       EQU     $01
GD_LATCH_FULL   EQU     $20

; GD_STATE: 0 ok; 1 no answer, 2 firmware < 1.11, 3 latch, 4 no answer to
; $80, 5 too big, 6 a block, 7 BIOS version, 8 function count
GdInstall:
    movem.l d0-d7/a0-a6, -(sp)
    move.l  #0, GD_BIOS
    move.l  #0, GD_SEND_FN
    move.l  #0, GD_CALL_FN
    move.w  #1, GD_STATE
    bsr     gd_packet                       ; $0C: the firmware's version
    bne     .gi_no
    moveq   #$0C, d0
    bsr     gd_word
    moveq   #0, d0
    bsr     gd_word
    move.w  #GD_PACKET, GD_STATUS
    bsr     gd_wait
    bne     .gi_no
    bsr     gd_word
    move.w  d0, d2                          ; firmware (BCD), then the ASIC's
    bsr     gd_word
    move.w  d2, GD_VER
    move.w  d0, GD_VER+2
    clr.w   GD_STATUS
    move.w  #500, d0
.gi_pause:
    dbra    d0, .gi_pause
    move.w  #2, GD_STATE
    cmp.w   #$111, d2                       ; 1.11: the first with the GD BIOS
    blt     .gi_no
    move.w  #3, GD_STATE
    move.w  #1000, d1
.gi_latch:
    move.w  GD_STATUS, d0                   ; drain the latch
    and.w   #GD_LATCH_FULL, d0
    beq.s   .gi_empty
    tst.w   GD_DATA
    dbra    d1, .gi_latch
    bra     .gi_no
.gi_empty:
    move.w  #4, GD_STATE
    bsr     gd_packet                       ; $80: the BIOS image, size first
    bne     .gi_no
    move.w  #$80, d0
    bsr     gd_word
    moveq   #0, d0
    bsr     gd_word
    move.w  #GD_PACKET, GD_STATUS
    bsr     gd_wait
    bne     .gi_no
    bsr     gd_word
    move.w  #GD_PACKET, GD_STATUS
    move.w  #5, GD_STATE
    cmp.w   #GDB_BUF_SIZE, d0
    bhi     .gi_no                          ; it would not fit
    move.w  #6, GD_STATE
    move.w  d0, d1
    lea     GDB_BUF, a0
.gi_block:
    bsr     gd_wait
    bne     .gi_no
    move.w  d1, d0
    cmp.w   #512, d0
    ble.s   .gi_size
    move.w  #512, d0
.gi_size:
    sub.w   d0, d1
    subq.w  #1, d0
.gi_byte:
    move.w  d0, GD_DATA
    bsr     gd_busy
    bne.s   .gi_no
    move.b  GD_DATA_BYTE, (a0)+
    dbra    d0, .gi_byte
    move.w  #GD_PACKET, GD_STATUS
    tst.w   d1
    bne.s   .gi_block
    clr.w   GD_STATUS
    lea     GDB_BUF, a0
    move.w  #7, GD_STATE
    cmp.w   #$100, (a0)                     ; the BIOS's own version
    blt.s   .gi_no
    move.w  #8, GD_STATE                    ; and enough functions (BigPEmu's
    cmp.w   #26, 2(a0)                      ; buffer is garbage: Init would
    blt.s   .gi_no                          ; crash it)
    cmp.w   #255, 2(a0)
    bhi.s   .gi_no
    movea.l a0, a6
    jsr     4(a6)                           ; function 1: GD_Init
    move.l  #GDB_BUF, GD_BIOS
    lea     GdSendC(pc), a0                 ; PC-relative: the copy in DRAM
    move.l  a0, GD_SEND_FN
    lea     GdCallC(pc), a0
    move.l  a0, GD_CALL_FN
    clr.w   GD_STATE
    movem.l (sp)+, d0-d7/a0-a6
    rts
.gi_no:
    clr.w   GD_STATUS
    movem.l (sp)+, d0-d7/a0-a6
    rts

gd_packet:                                  ; Z: acknowledged; NZ: no answer
    move.w  #GD_PACKET, GD_STATUS
gd_wait:
    move.l  #200000, d4
.gw_low:
    move.w  GD_STATUS, d3
    btst    #3, d3
    beq.s   .gw_sel
    subq.l  #1, d4
    bne.s   .gw_low
    bra.s   .gw_none
.gw_sel:
    move.w  #GD_PACKET|GD_SELECT, GD_STATUS
    move.l  #200000, d4
.gw_high:
    move.w  GD_STATUS, d3
    btst    #3, d3
    bne.s   .gw_ack
    subq.l  #1, d4
    bne.s   .gw_high
.gw_none:
    moveq   #1, d3
    rts
.gw_ack:
    moveq   #0, d3
    rts

gd_busy:                                    ; Z: the byte is there
    move.l  #200000, d4
.gb_spin:
    tst.w   GD_STATUS
    bpl.s   .gb_ok
    subq.l  #1, d4
    bne.s   .gb_spin
    moveq   #1, d4
    rts
.gb_ok:
    moveq   #0, d4
    rts

gd_word:                                    ; a word each way, RetroHQ's byte order
    move.w  d0, GD_DATA
    bsr.s   gd_busy
    move.b  GD_DATA_BYTE, d0
    ror.w   #8, d0
    move.w  d0, GD_DATA
    bsr.s   gd_busy
    move.b  GD_DATA_BYTE, d0
    rts

;---------------------------------------------------------------------------
; GdSendC: void gd_send(const char *s), C calling convention (the string on
; the stack; d0-d1/a0-a1 are the caller's to lose).
; The GD BIOS's DebugString minus its last wait: command $92, the length
; with the NUL, the bytes, $10 (process it); then the micro's answer (bit 3
; low, $11, bit 3 high, a word, $390, 0) collected with a ceiling, so the
; micro is never left waiting.  Not taken (nobody reads COM8): pending, and
; the next lines are dropped until it is (GD_DROP).
;---------------------------------------------------------------------------
GdSendC:
    movea.l 4(sp), a0
    tst.l   GD_BIOS
    beq.s   .gs_none
    tst.w   GD_PEND
    beq.s   .gs_free
    moveq   #100, d1                        ; the last line's answer: ready
    bsr     gd_reply                        ; long ago, or drop this one
    bne     .gs_drop
.gs_free:
    moveq   #100, d1                        ; drain the latch
.gs_drain:
    move.w  GD_STATUS, d0
    andi.w  #$20, d0
    beq.s   .gs_start
    tst.w   GD_DATA
    dbra    d1, .gs_drain
    bra.s   .gs_abort
.gs_start:
    move.w  #$10, GD_STATUS                 ; packet start
    move.l  #30000, d1
    bsr     gd_ack
    bne.s   .gs_abort
    move.w  #$92, d0                        ; the command
    bsr     gd_w
    movea.l a0, a1                          ; the length, NUL included
    moveq   #0, d0
.gs_len:
    addq.w  #1, d0
    tst.b   (a1)+
    bne.s   .gs_len
    bsr     gd_w
.gs_byte:
    moveq   #0, d0                          ; the bytes, NUL included
    move.b  (a0)+, d0
    bsr     gd_b
    tst.w   d0
    bne.s   .gs_byte
    move.w  #$10, GD_STATUS                 ; process it
    move.w  #1, GD_PEND
    move.l  #5000, d1                       ; the answer now (~5 ms with the
    bsr.s   gd_reply                        ; PC reading)
.gs_none:
    rts
.gs_abort:
    clr.w   GD_STATUS
.gs_drop:
    addq.l  #1, GD_DROP
    rts

;---------------------------------------------------------------------------
; GdCallC: long gd_call(long fn, long d0, long d1, void *a0), C calling
; convention.  The GD BIOS's function fn (jsr fn*4(base)) with d0, d1 and a0
; as given; its d0 back.  Which registers the BIOS keeps is not documented
; (JaguarDocs metodi/gamedrive_usb.md 3.3): all but d0 are saved here.  The
; channel is the log's too: a line's answer still pending is collected first
; (with its ceiling), so the micro sees one conversation at a time.
;---------------------------------------------------------------------------
GdCallC:
    movem.l d1-d7/a0-a6, -(sp)              ; 14 registers, 56 bytes
    move.l  GD_BIOS, d2
    beq.s   .gc_none
    tst.w   GD_PEND
    beq.s   .gc_free
    move.l  #200000, d1
    bsr.s   gd_reply
.gc_free:
    movea.l d2, a6
    move.l  60(sp), d3                      ; fn
    move.l  64(sp), d0
    move.l  68(sp), d1
    movea.l 72(sp), a0
    lsl.l   #2, d3
    adda.l  d3, a6
    jsr     (a6)
    movem.l (sp)+, d1-d7/a0-a6
    rts
.gc_none:
    moveq   #-1, d0
    movem.l (sp)+, d1-d7/a0-a6
    rts

gd_reply:                                   ; d1 = ceiling; Z: taken
    bsr.s   gd_ack
    bne.s   .gr_no
    bsr.s   gd_w                            ; its word, clocked in, not looked at
    move.w  #$390, GD_STATUS
    clr.w   GD_STATUS
    clr.w   GD_PEND
    moveq   #0, d0
.gr_no:
    rts

gd_ack:                                     ; bit 3 low (d1 its ceiling), $11,
.ga_low:                                    ; bit 3 high; Z: acknowledged
    move.w  GD_STATUS, d0
    btst    #3, d0
    beq.s   .ga_sel
    subq.l  #1, d1
    bne.s   .ga_low
    bra.s   .ga_fail
.ga_sel:
    move.w  #$11, GD_STATUS
    move.l  #100000, d1
.ga_high:
    move.w  GD_STATUS, d0
    btst    #3, d0
    bne.s   .ga_ok
    subq.l  #1, d1
    bne.s   .ga_high
.ga_fail:
    moveq   #1, d0
    rts
.ga_ok:
    moveq   #0, d0
    rts

gd_w:                                       ; a word, the low byte first
    bsr.s   gd_b
    lsr.w   #8, d0
gd_b:                                       ; a byte out (the answer byte read
    move.w  d0, GD_DATA                     ; and dropped); d1 used
    move.w  #50000, d1
.gb2_busy:
    tst.w   GD_STATUS
    bpl.s   .gb2_done
    dbra    d1, .gb2_busy
    addq.l  #1, GD_TIMEOUT
.gb2_done:
    tst.w   GD_DATA
    rts
