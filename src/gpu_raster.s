; ---------------------------------------------------------------------------
; GPU TOM Polygon Clip + Rasterizer (F4)
; ---------------------------------------------------------------------------
; F4.2a:        harness validated (test pixel) -- ARCHIVED.
; F4.2b iter 1: inside-only filter against LEFT edge -- ARCHIVED.
; F4.2b iter 2: full Sutherland-Hodgman 1-edge clip (LEFT, intersect emit)
;               -- ARCHIVED.
; F4.2b iter 3 mini (CURRENT): 2-edge ping-pong (LEFT then RIGHT) via
;               parametric clip routine. Validates ping-pong architecture
;               + parametric dispatch (polarity flag).
; F4.2b iter 3 full: extend to 4-edge (TOP/BOT) -- DEFERRED to S10.
; F4.2c-d:      edge build + scanline fill.
;
; All vertex coordinates Q16.16 signed. ONE = $00010000.
;
; DRAM args region $2280-$23BF:
;   GPU_ARG_FB_BASE     long  reserved (F4.2c)
;   GPU_ARG_COLOR       long  reserved (F4.2c)
;   GPU_ARG_DONE        long  68k clears pre-RISCGO; GPU writes 1 on exit
;   GPU_ARG_IN_COUNT    long  N input vertices (1..8)
;   GPU_ARG_OUT_COUNT   long  GPU writes filtered/clipped count
;   GPU_ARG_IN_BUF      8 vertices x 8 byte (xq long, yq long) = 64 byte
;   GPU_ARG_OUT_BUF     8 vertices x 8 byte = 64 byte
;   GPU_ARG_PING_BUF    8 vertices x 8 byte = 64 byte (intermediate pass 1->2)
; ---------------------------------------------------------------------------

GPU_ARG_BASE        EQU     $2280
GPU_ARG_FB_BASE     EQU     GPU_ARG_BASE+0
GPU_ARG_COLOR       EQU     GPU_ARG_BASE+4
GPU_ARG_DONE        EQU     GPU_ARG_BASE+8
GPU_ARG_IN_COUNT    EQU     GPU_ARG_BASE+12
GPU_ARG_OUT_COUNT   EQU     GPU_ARG_BASE+16

GPU_ARG_IN_BUF      EQU     $2300
GPU_ARG_OUT_BUF     EQU     $2340
GPU_ARG_PING_BUF    EQU     $2380           ; iter 3 mini ping buffer
GPU_ARG_INTENSITY_BUF EQU   $23C0           ; S14 F4.4: 8 verts x 4 byte (Q16.16 long per vertex)
GPU_ARG_PONG_BUF    EQU     $2400           ; iter 3 full pong buffer (S10)

VIEWPORT_XMAX_Q     EQU     $013F0000       ; 319 in Q16.16
VIEWPORT_YMAX_Q     EQU     $00C70000       ; 199 in Q16.16 (S10)

; F4.2c-1 hline_fill primitive args region (S11)
GPU_HLINE_FB_BASE   EQU     $24C0           ; long: framebuffer base addr
GPU_HLINE_Y         EQU     $24C4           ; long: row index (0..199)
GPU_HLINE_XL        EQU     $24C8           ; long: left x (inclusive)
GPU_HLINE_XR        EQU     $24CC           ; long: right x (inclusive)
GPU_HLINE_COLOR     EQU     $24D0           ; long: color (low 8 bits)
GPU_HLINE_DONE      EQU     $24D4           ; long: 68k clears, GPU writes 1

; F4.2c-2a raster edge-build args region (S12)
GPU_RASTER_POLY_COUNT EQU   $24E0           ; long: N input verts in IN_BUF
GPU_RASTER_DONE_E   EQU     $24E4           ; long: 68k clears, GPU writes 1
GPU_RASTER_EDGE_COUNT EQU   $24E8           ; long: GPU writes # edges emitted
GPU_RASTER_EDGE_BUF EQU     $2500           ; 8 edges x 24 byte = 192 byte total (S14)
                                            ;   record: y0_q, y1_q, x0_q, dx_dy_q,
                                            ;           i0_q, di_dy_q       (S14 F4.4)

; F4.2c-2b scan-outer trace buffer (S12): first 6 scanlines (xl_q, xr_q)
; S14: bumped from $2580 to $25C0 to leave room for 8 x 24 byte EDGE_BUF.
GPU_RASTER_SCAN_BUF EQU     $25C0           ; 6 entries x 8 byte = 48 byte
                                            ;   entry[k]: xl_q (long), xr_q (long)

; F4.2c-2c hline dispatch leaf args (S12): direct triangle fill on framebuffer
GPU_RASTER_FB_BASE  EQU     $24EC           ; long: framebuffer base addr
GPU_RASTER_COLOR    EQU     $24F0           ; long: 8-bit fill color

; S19 GpuScale 2x kernel args (8bpp brick scale-2x at-blit-time, idx 0 = transparent)
GPU_SCALE_SRC_BASE  EQU     $25F0           ; long: src brick pixels ptr (post src-skip)
GPU_SCALE_DST_BASE  EQU     $25F4           ; long: dst position in BG_PRISTINE
GPU_SCALE_SRC_DX    EQU     $25F8           ; long: src width pixel (post-clip, 1x)
GPU_SCALE_SRC_DY    EQU     $25FC           ; long: src height pixel (post-clip, 1x)
GPU_SCALE_SRC_PITCH EQU     $2600           ; long: src row stride bytes (= padded_DX)
GPU_SCALE_DST_PITCH EQU     $2604           ; long: dst row stride bytes (= BG_W = 640)
GPU_SCALE_DONE      EQU     $2608           ; long: 68k clears, GPU writes 1 on exit

GPU_RASTER_ENTRY    EQU     $F03100

; ---------------------------------------------------------------------------
; 68k harness
; ---------------------------------------------------------------------------
    .68000

; InitGpu: copy GPU code from cart ROM to GPU local RAM. Boot-time only,
; before any RISCGO. Long-copy (GPU code is long-aligned).
InitGpu:
    movem.l d0/a0/a1, -(sp)
    move.l  #GpuRasterRom, a0
    move.l  #GPU_RASTER_ENTRY, a1
    move.w  #(GpuRasterRomEnd-GpuRasterRom)/4-1, d0
.copy:
    move.l  (a0)+, (a1)+
    dbra    d0, .copy
    movem.l (sp)+, d0/a0/a1
    rts

; CallGpuClip: F4.2b iter 3 full smoke test (S10).
;   Quad encompassing viewport, all 4 verts outside, all 4 edges active:
;     v0 = (-50,  -50)   outside LEFT  + TOP
;     v1 = (400,  -50)   outside RIGHT + TOP
;     v2 = (400,  250)   outside RIGHT + BOT
;     v3 = (-50,  250)   outside LEFT  + BOT
;   GPU runs 4-pass clip (LEFT, RIGHT, TOP, BOT). Expected final 4 verts =
;   viewport rectangle corners:
;     out0 = (319, 199)   bottom-right
;     out1 = (  0, 199)   bottom-left
;     out2 = (  0,   0)   top-left
;     out3 = (319,   0)   top-right
;
;   Visible blocks (16x16, bounds x<304, y<184):
;     only out2 (0, 0) visible. Other 3 OOB at viewport edges (memdump
;     validates all 4 + the 4 intermediate buffers PING/OUT/PONG).
;     Plus sentinel 32x32 bordeaux at (160, 100).
;
; Args: none. Side effect: writes vertex blocks + sentinel to BG_A.
CallGpuClip:
    movem.l d0-d5/a0-a1, -(sp)

    ; --- Sentinel 32x32 at (160, 100) color 100 (bordeaux) ---
    move.l  #$0010FAA0, d3
    moveq   #32-1, d4
.sent_row:
    movea.l d3, a1
    moveq   #32-1, d5
.sent_col:
    move.b  #100, (a1)+
    dbra    d5, .sent_col
    add.l   #640, d3
    dbra    d4, .sent_row

    ; --- Setup test quad in GPU_ARG_IN_BUF (Q16.16 hex) ---
    movea.l #GPU_ARG_IN_BUF, a0
    move.l  #$FFCE0000, (a0)+        ; v0.xq = -50
    move.l  #$FFCE0000, (a0)+        ; v0.yq = -50
    move.l  #$01900000, (a0)+        ; v1.xq = 400
    move.l  #$FFCE0000, (a0)+        ; v1.yq = -50
    move.l  #$01900000, (a0)+        ; v2.xq = 400
    move.l  #$00FA0000, (a0)+        ; v2.yq = 250
    move.l  #$FFCE0000, (a0)+        ; v3.xq = -50
    move.l  #$00FA0000, (a0)+        ; v3.yq = 250

    move.l  #4, GPU_ARG_IN_COUNT
    moveq   #0, d0
    move.l  d0, GPU_ARG_OUT_COUNT
    move.l  d0, GPU_ARG_DONE

    ; --- Launch GPU ---
    moveq   #0, d0
    move.l  d0, G_FLAGS
    move.l  #$00070007, G_END
    move.l  #GPU_RASTER_ENTRY, G_PC
    move.l  #RISCGO, G_CTRL

.wait:
    move.l  GPU_ARG_DONE, d0
    beq.s   .wait

    moveq   #0, d0
    move.l  d0, G_CTRL

    ; --- Render output: 16x16 block per output vertex on BG_A ---
    move.l  GPU_ARG_OUT_COUNT, d2
    movea.l #GPU_ARG_OUT_BUF, a0

.render:
    tst.l   d2
    beq.s   .render_done

    move.l  (a0)+, d0                 ; xq
    move.l  (a0)+, d1                 ; yq

    ; Q16.16 -> int (signed round): (q + $8000) asr.l #16
    add.l   #$8000, d0
    asr.l   #8, d0
    asr.l   #8, d0
    add.l   #$8000, d1
    asr.l   #8, d1
    asr.l   #8, d1

    ; bounds check: 0 <= x < 304, 0 <= y < 184 (fits 16x16 block)
    tst.l   d0
    bmi.s   .vert_done
    cmp.l   #304, d0
    bge.s   .vert_done
    tst.l   d1
    bmi.s   .vert_done
    cmp.l   #184, d1
    bge.s   .vert_done

    ; pixel addr = BG_A + d1 * 640 + d0
    moveq   #0, d3
    move.w  d1, d3
    mulu.w  #640, d3
    add.l   d0, d3
    add.l   #BG_A, d3

    ; 16x16 block of color 200 (gouraud green)
    moveq   #16-1, d4
.blk_row:
    movea.l d3, a1
    moveq   #16-1, d5
.blk_col:
    move.b  #200, (a1)+
    dbra    d5, .blk_col
    add.l   #640, d3
    dbra    d4, .blk_row

.vert_done:
    subq.l  #1, d2
    bra.s   .render

.render_done:
    movem.l (sp)+, d0-d5/a0-a1
    rts

; CallGpuHline: F4.2c-1 hline_fill primitive smoke test (S11).
;   Single horizontal line: y=140, xl=50, xr=270, color=200 (gouraud green).
;   GPU runs GpuHlineMain entry: storeb byte fill loop xl..xr inclusive
;   on BG_A row at y, with read-back barrier before DONE flag.
;
;   Visual: 1 horizontal line green spanning x in [50, 270] at y=140
;     above the existing Citadel + Twinsen wireframe scene.
;
; Args: none. Side effect: writes 221 bytes to BG_A row 140.
CallGpuHline:
    movem.l d0/a0, -(sp)

    ; --- Setup args ---
    move.l  #BG_A, GPU_HLINE_FB_BASE
    move.l  #140, GPU_HLINE_Y
    move.l  #50, GPU_HLINE_XL
    move.l  #270, GPU_HLINE_XR
    move.l  #200, GPU_HLINE_COLOR
    moveq   #0, d0
    move.l  d0, GPU_HLINE_DONE

    ; --- Launch GPU at GpuHlineMain entry ---
    moveq   #0, d0
    move.l  d0, G_FLAGS
    move.l  #$00070007, G_END
    move.l  #GpuHlineMain, G_PC
    move.l  #RISCGO, G_CTRL

.wait:
    move.l  GPU_HLINE_DONE, d0
    beq.s   .wait

    moveq   #0, d0
    move.l  d0, G_CTRL

    movem.l (sp)+, d0/a0
    rts

; CallGpuRaster: F4.2c-2a edge-build smoke test (S12).
;   Hardcoded triangle (50,50), (270,50), (160,180) -- interior to viewport.
;   GPU runs GpuRasterMain entry: per non-horizontal edge, swap so y0<y1,
;   compute slope dx_dy_q = (|dx|<<16)/dy with sign restore, write 16-byte
;   record (y0_q, y1_q, x0_q, dx_dy_q) to GPU_RASTER_EDGE_BUF.
;
;   Expected (GPU semantics, --gpu-mode verifier):
;     edge_count = 2 (horizontal top edge skipped)
;     edge[0] @ $2500: y0=$00320000 y1=$00B40000 x0=$010E0000 dxdy=$FFFF2763
;     edge[1] @ $2510: y0=$00320000 y1=$00B40000 x0=$00320000 dxdy=$0000D89D
;
;   Diagnostic visual on BG_A:
;     Row of 16x16 sentinel blocks at y=10, color 100 (bordeaux).
;     Block count = GPU_RASTER_EDGE_COUNT (expected 2).
;     => visible: 2 bordeaux blocks at top => edge build + skip-horizontal OK.
;
; Args: none. Side effect: writes IN_BUF, raster args, sentinel blocks.
CallGpuRaster:
    movem.l d0-d4/a0-a1, -(sp)

    ; --- Setup test triangle in GPU_ARG_IN_BUF (Q16.16 hex) ---
    movea.l #GPU_ARG_IN_BUF, a0
    move.l  #$00320000, (a0)+        ; v0.xq =  50
    move.l  #$00320000, (a0)+        ; v0.yq =  50
    move.l  #$010E0000, (a0)+        ; v1.xq = 270
    move.l  #$00320000, (a0)+        ; v1.yq =  50
    move.l  #$00A00000, (a0)+        ; v2.xq = 160
    move.l  #$00B40000, (a0)+        ; v2.yq = 180

    move.l  #3, GPU_RASTER_POLY_COUNT
    move.l  #BG_A, GPU_RASTER_FB_BASE         ; 2c: GPU writes directly to BG_A
    move.l  #200, GPU_RASTER_COLOR            ; gouraud green
    moveq   #0, d0
    move.l  d0, GPU_RASTER_DONE_E
    move.l  d0, GPU_RASTER_EDGE_COUNT

    ; --- Launch GPU at GpuRasterMain entry ---
    moveq   #0, d0
    move.l  d0, G_FLAGS
    move.l  #$00070007, G_END
    move.l  #GpuRasterMain, G_PC
    move.l  #RISCGO, G_CTRL

.wait:
    move.l  GPU_RASTER_DONE_E, d0
    beq.s   .wait

    moveq   #0, d0
    move.l  d0, G_CTRL

    ; --- Diagnostic A (S12 a-step): edge_count sentinel blocks at y=10 ---
    ;   d3 = current block top-left addr, advances by 20 (16 px block + 4 gap)
    move.l  GPU_RASTER_EDGE_COUNT, d2
    beq.s   .diag_skip_a                  ; no edges -> skip A
    move.l  #BG_A+(10*640), d3            ; first block addr (row 10, x=0)

.diag_block:
    tst.l   d2
    beq.s   .diag_skip_a

    ; Draw 16x16 block color 100 starting at d3
    move.l  d3, d0
    moveq   #16-1, d4                     ; row counter (16 rows)
.diag_row:
    movea.l d0, a1
    moveq   #16-1, d1                     ; col counter (16 cols)
.diag_col:
    move.b  #100, (a1)+
    dbra    d1, .diag_col
    add.l   #640, d0
    dbra    d4, .diag_row

    add.l   #20, d3                       ; next block: 16 px wide + 4 gap
    subq.l  #1, d2
    bra.s   .diag_block

.diag_skip_a:
    ; F4.2c-2c (S12): GPU now fills triangle directly on BG_A. No 68k preview.
    movem.l (sp)+, d0-d4/a0-a1
    rts

; ---------------------------------------------------------------------------
; CallGpuScale (S19): launches GpuScaleMain GPU TOM kernel for 2x scale of one
; clipped brick into BG_PRISTINE. Caller must populate ALL 6 arg registers
; (GPU_SCALE_SRC_BASE/DST_BASE/SRC_DX/SRC_DY/SRC_PITCH/DST_PITCH) before bsr.
; Routine clears GPU_SCALE_DONE, launches GPU at GpuScaleMain, polls until set.
; Clobbers d0.
; ---------------------------------------------------------------------------
CallGpuScale:
    moveq   #0, d0
    move.l  d0, GPU_SCALE_DONE
    move.l  d0, G_FLAGS
    move.l  #$00070007, G_END
    move.l  #GpuScaleMain, G_PC
    move.l  #RISCGO, G_CTRL
.cgs_wait:
    move.l  GPU_SCALE_DONE, d0
    beq.s   .cgs_wait
    moveq   #0, d0
    move.l  d0, G_CTRL
    rts

; ---------------------------------------------------------------------------
; GPU code section -- assembled at GPU_RASTER_ENTRY ($F03100). The byte stream
; is emitted in cart ROM at GpuRasterRom; InitGpu copies it to GPU local RAM
; at boot.
; ---------------------------------------------------------------------------
GpuRasterRom:
    .gpu
    .org    GPU_RASTER_ENTRY

; F4.2b iter 3 mini: 2-edge ping-pong clip (LEFT -> PING -> RIGHT -> OUT).
;
; Architecture:
;   GpuClipMain (driver) sets up params, calls GpuClipPass twice.
;   GpuClipPass is a parametric Sutherland-Hodgman 1-edge clip routine.
;
; Generic dist formulation (works for both LEFT and RIGHT):
;   polarity = 0 (LEFT):  dist = xq           (inside if dist >= 0, xq >= 0)
;   polarity = 1 (RIGHT): dist = clip - xq    (inside if dist >= 0, xq <= clip)
;   straddle = bit31(dist_S) XOR bit31(dist_E)
;   t = |dist_S| / (|dist_S| + |dist_E|)   [opposite signs in straddle]
;   intersect ix = clip_value (works for both LEFT=0 and RIGHT=XMAX_Q)
;   intersect iy = S.yq + t * (E.yq - S.yq)
;
; Routine call convention:
;   Inputs:  r1=src_count, r4=src_base, r5=dst_base, r9=polarity (0|1),
;            r28=clip_value (Q16.16), r31=return label
;            r12/13/16/17/18 preloaded (loop/done/no_straddle/loop_tail/eok)
;   Output:  r2 = dst_count
;   Clobbers: r0,r2,r3,r6,r7,r10,r11,r14,r15,r19,r20-r26
;   Preserves: r1,r4,r5,r9,r12-r18,r28,r29,r30,r31

GpuClipMain:
    ; --- One-shot init ---
    ; G_DIVCTRL = 1 (16.16 unsigned mode), set once for all 4 passes.
    movei   #G_DIVCTRL, r29
    moveq   #1, r19
    store   r19, (r29)
    nop
    nop
    nop
    nop

    ; Preload X-axis routine targets (Pass 1 + Pass 2 use GpuClipPass).
    movei   #GpuClipPass_Loop, r12
    movei   #GpuClipPass_Done, r13
    movei   #GpuClipPass_NoStraddle, r16
    movei   #GpuClipPass_LoopTail, r17
    movei   #GpuClipPass_EOk, r18

    ; --- Pass 1: LEFT clip (IN_BUF -> PING_BUF) ---
    movei   #GPU_ARG_IN_COUNT, r0
    load    (r0), r1                    ; r1 = src_count from DRAM
    movei   #GPU_ARG_IN_BUF, r4
    movei   #GPU_ARG_PING_BUF, r5
    moveq   #0, r28                      ; clip_value = 0 (LEFT)
    moveq   #0, r9                       ; polarity = 0 (dist = xq)
    movei   #Pass1_Ret, r31
    movei   #GpuClipPass_Entry, r0
    jump    T, (r0)
    nop

Pass1_Ret:
    ; r2 = pass1 dst_count

    ; --- Pass 2: RIGHT clip (PING_BUF -> OUT_BUF) ---
    move    r2, r1                       ; src_count for pass 2
    movei   #GPU_ARG_PING_BUF, r4
    movei   #GPU_ARG_OUT_BUF, r5
    movei   #VIEWPORT_XMAX_Q, r28        ; clip_value = XMAX_Q (RIGHT)
    moveq   #1, r9                       ; polarity = 1 (dist = clip - xq)
    movei   #Pass2_Ret, r31
    movei   #GpuClipPass_Entry, r0
    jump    T, (r0)
    nop

Pass2_Ret:
    ; r2 = pass2 dst_count

    ; Reload Y-axis routine targets (Pass 3 + Pass 4 use GpuClipPassY).
    movei   #GpuClipPassY_Loop, r12
    movei   #GpuClipPassY_Done, r13
    movei   #GpuClipPassY_NoStraddle, r16
    movei   #GpuClipPassY_LoopTail, r17
    movei   #GpuClipPassY_EOk, r18

    ; --- Pass 3: TOP clip (OUT_BUF -> PONG_BUF) ---
    move    r2, r1                       ; src_count for pass 3
    movei   #GPU_ARG_OUT_BUF, r4
    movei   #GPU_ARG_PONG_BUF, r5
    moveq   #0, r28                      ; clip_value = 0 (TOP)
    moveq   #0, r9                       ; polarity = 0 (dist = yq)
    movei   #Pass3_Ret, r31
    movei   #GpuClipPassY_Entry, r0
    jump    T, (r0)
    nop

Pass3_Ret:
    ; r2 = pass3 dst_count

    ; --- Pass 4: BOT clip (PONG_BUF -> OUT_BUF) ---
    move    r2, r1                       ; src_count for pass 4
    movei   #GPU_ARG_PONG_BUF, r4
    movei   #GPU_ARG_OUT_BUF, r5
    movei   #VIEWPORT_YMAX_Q, r28        ; clip_value = YMAX_Q (BOT)
    moveq   #1, r9                       ; polarity = 1 (dist = clip - yq)
    movei   #Pass4_Ret, r31
    movei   #GpuClipPassY_Entry, r0
    jump    T, (r0)
    nop

Pass4_Ret:
    ; r2 = final dst_count
    movei   #GPU_ARG_OUT_COUNT, r0
    store   r2, (r0)
    nop
    nop

    movei   #GPU_ARG_DONE, r0
    moveq   #1, r6
    store   r6, (r0)
    nop
    nop
    nop

GpuClipMain_Halt:
    movei   #GpuClipMain_Halt, r0
    jump    (r0)
    nop

; -------------------------------------------------------------------------
; GpuClipPass -- parametric S-H 1-edge clip routine
; -------------------------------------------------------------------------
GpuClipPass_Entry:
    moveq   #0, r2                       ; dst_count = 0
    moveq   #0, r3                       ; i = 0

GpuClipPass_Loop:
    cmp     r1, r3                        ; i - src_count
    jump    PL, (r13)                     ; i >= N -> Done
    nop

    ; --- Load S = src[i] ---
    move    r3, r6
    shlq    #3, r6
    move    r4, r14
    add     r6, r14
    load    (r14), r10                    ; S.xq
    addq    #4, r14
    load    (r14), r11                    ; S.yq

    ; --- E_idx = (i+1) mod N ---
    move    r3, r7
    addq    #1, r7
    cmp     r1, r7
    jump    MI, (r18)                     ; if r7 < N, skip wrap
    nop
    moveq   #0, r7
GpuClipPass_EOk:

    ; --- Load E = src[E_idx] ---
    shlq    #3, r7
    move    r4, r14
    add     r7, r14
    load    (r14), r20                    ; E.xq
    addq    #4, r14
    load    (r14), r21                    ; E.yq

    ; --- Compute dist_S, dist_E based on polarity ---
    ;   polarity 0 (LEFT):  dist = xq
    ;   polarity 1 (RIGHT): dist = clip_value - xq
    move    r10, r22                      ; default LEFT: dist_S = S.xq
    cmpq    #0, r9
    jr      EQ, GpuClipPass_DistSDone
    nop
    move    r28, r22                      ; r22 = clip_value
    sub     r10, r22                      ; r22 = clip - S.xq (RIGHT)
GpuClipPass_DistSDone:

    move    r20, r23
    cmpq    #0, r9
    jr      EQ, GpuClipPass_DistEDone
    nop
    move    r28, r23
    sub     r20, r23                      ; dist_E = clip - E.xq
GpuClipPass_DistEDone:

    ; --- Straddle test: bit31(dist_S) XOR bit31(dist_E) ---
    move    r22, r6
    xor     r23, r6
    btst    #31, r6
    jump    EQ, (r16)                     ; same sign -> no straddle
    nop

    ; --- Intersect ---
    ; t = |dist_S| / (|dist_S| + |dist_E|)
    ; (opposite signs in straddle, so |a|+|b| = |a-b|)
    move    r22, r24
    abs     r24                            ; |dist_S|
    move    r23, r26
    abs     r26                            ; |dist_E|
    add     r24, r26                       ; r26 = |dS|+|dE|

    ; t_q = r24 / r26  (Q16.16/Q16.16 = Q16.16 in 16.16 mode)
    div     r26, r24                       ; r24 = t_q
    nop                                     ; div latency

    ; iy_q = S.yq + t * (E.yq - S.yq)
    move    r21, r25
    sub     r11, r25                        ; dy_q (signed)
    sharq   #16, r25                        ; dy_int signed
    move    r25, r19                        ; sign tracker
    abs     r25                              ; |dy_int|
    mult    r25, r24                        ; r24 = t * |dy_int| (Q16.16 +)

    btst    #31, r19
    jr      EQ, GpuClipPass_DyPos
    nop
    neg     r24
GpuClipPass_DyPos:
    move    r11, r26
    add     r24, r26                        ; r26 = iy_q

    ; --- Emit (clip_value, iy_q) ---
    move    r2, r6
    shlq    #3, r6
    move    r5, r15
    add     r6, r15
    store   r28, (r15)                      ; ix_q = clip_value
    addq    #4, r15
    store   r26, (r15)                      ; iy_q
    addq    #1, r2

GpuClipPass_NoStraddle:
    ; --- Emit E if E inside (bit31(dist_E) == 0) ---
    btst    #31, r23
    jump    NE, (r17)                       ; E outside -> skip
    nop

    move    r2, r6
    shlq    #3, r6
    move    r5, r15
    add     r6, r15
    store   r20, (r15)                      ; E.xq
    addq    #4, r15
    store   r21, (r15)                      ; E.yq
    addq    #1, r2

GpuClipPass_LoopTail:
    addq    #1, r3
    jump    T, (r12)                        ; back to GpuClipPass_Loop
    nop

GpuClipPass_Done:
    jump    T, (r31)                        ; return to caller
    nop

; -------------------------------------------------------------------------
; GpuClipPassY -- parametric S-H 1-edge clip routine, Y-axis (TOP/BOT)
; Specular twin of GpuClipPass with x<->y swap in dist + intersect:
;   polarity 0 (TOP):  dist = yq          (inside if dist >= 0, yq >= 0)
;   polarity 1 (BOT):  dist = clip - yq   (inside if dist >= 0, yq <= clip)
;   intersect iy = clip_value
;   intersect ix = S.xq + t * (E.xq - S.xq)   [interpolate along X-axis]
;
; Same call convention as GpuClipPass:
;   Inputs:  r1=src_count, r4=src_base, r5=dst_base, r9=polarity (0|1),
;            r28=clip_value (Q16.16), r31=return label
;            r12/13/16/17/18 preloaded for THIS routine's labels
;   Output:  r2 = dst_count
; -------------------------------------------------------------------------
GpuClipPassY_Entry:
    moveq   #0, r2                       ; dst_count = 0
    moveq   #0, r3                       ; i = 0

GpuClipPassY_Loop:
    cmp     r1, r3                        ; i - src_count
    jump    PL, (r13)                     ; i >= N -> Done
    nop

    ; --- Load S = src[i] ---
    move    r3, r6
    shlq    #3, r6
    move    r4, r14
    add     r6, r14
    load    (r14), r10                    ; S.xq
    addq    #4, r14
    load    (r14), r11                    ; S.yq

    ; --- E_idx = (i+1) mod N ---
    move    r3, r7
    addq    #1, r7
    cmp     r1, r7
    jump    MI, (r18)                     ; if r7 < N, skip wrap
    nop
    moveq   #0, r7
GpuClipPassY_EOk:

    ; --- Load E = src[E_idx] ---
    shlq    #3, r7
    move    r4, r14
    add     r7, r14
    load    (r14), r20                    ; E.xq
    addq    #4, r14
    load    (r14), r21                    ; E.yq

    ; --- Compute dist_S, dist_E based on polarity (Y-axis) ---
    ;   polarity 0 (TOP):  dist = yq
    ;   polarity 1 (BOT):  dist = clip_value - yq
    move    r11, r22                      ; default TOP: dist_S = S.yq
    cmpq    #0, r9
    jr      EQ, GpuClipPassY_DistSDone
    nop
    move    r28, r22                      ; r22 = clip_value
    sub     r11, r22                      ; r22 = clip - S.yq (BOT)
GpuClipPassY_DistSDone:

    move    r21, r23
    cmpq    #0, r9
    jr      EQ, GpuClipPassY_DistEDone
    nop
    move    r28, r23
    sub     r21, r23                      ; dist_E = clip - E.yq
GpuClipPassY_DistEDone:

    ; --- Straddle test: bit31(dist_S) XOR bit31(dist_E) ---
    move    r22, r6
    xor     r23, r6
    btst    #31, r6
    jump    EQ, (r16)                     ; same sign -> no straddle
    nop

    ; --- Intersect ---
    ; t = |dist_S| / (|dist_S| + |dist_E|)
    move    r22, r24
    abs     r24                            ; |dist_S|
    move    r23, r26
    abs     r26                            ; |dist_E|
    add     r24, r26                       ; r26 = |dS|+|dE|

    ; t_q = r24 / r26  (Q16.16/Q16.16 = Q16.16 in 16.16 mode)
    div     r26, r24                       ; r24 = t_q
    nop                                     ; div latency

    ; ix_q = S.xq + t * (E.xq - S.xq)   [Y-axis interpolates X]
    move    r20, r25
    sub     r10, r25                        ; dx_q (signed)
    sharq   #16, r25                        ; dx_int signed
    move    r25, r19                        ; sign tracker
    abs     r25                              ; |dx_int|
    mult    r25, r24                        ; r24 = t * |dx_int| (Q16.16 +)

    btst    #31, r19
    jr      EQ, GpuClipPassY_DxPos
    nop
    neg     r24
GpuClipPassY_DxPos:
    move    r10, r26
    add     r24, r26                        ; r26 = ix_q

    ; --- Emit (ix_q, clip_value) ---
    move    r2, r6
    shlq    #3, r6
    move    r5, r15
    add     r6, r15
    store   r26, (r15)                      ; ix_q (interpolated)
    addq    #4, r15
    store   r28, (r15)                      ; iy_q = clip_value
    addq    #1, r2

GpuClipPassY_NoStraddle:
    ; --- Emit E if E inside (bit31(dist_E) == 0) ---
    btst    #31, r23
    jump    NE, (r17)                       ; E outside -> skip
    nop

    move    r2, r6
    shlq    #3, r6
    move    r5, r15
    add     r6, r15
    store   r20, (r15)                      ; E.xq
    addq    #4, r15
    store   r21, (r15)                      ; E.yq
    addq    #1, r2

GpuClipPassY_LoopTail:
    addq    #1, r3
    jump    T, (r12)                        ; back to GpuClipPassY_Loop
    nop

GpuClipPassY_Done:
    jump    T, (r31)                        ; return to caller
    nop

; -------------------------------------------------------------------------
; GpuHlineMain (S11) -- F4.2c-1 horizontal line fill primitive (8bpp).
;
; Reads 5 args from DRAM:
;   GPU_HLINE_FB_BASE  = framebuffer base addr (e.g. BG_A = $100000)
;   GPU_HLINE_Y        = row index (0..199)
;   GPU_HLINE_XL       = left x (inclusive)
;   GPU_HLINE_XR       = right x (inclusive)
;   GPU_HLINE_COLOR    = color (low 8 bits used)
;
; Address: row_addr = fb_base + y*640.
; Loop: storeb color from (row_addr + xl) to (row_addr + xr) inclusive.
; Barrier: read-back DONE addr + 3 nop to flush GPU write buffer
;          (canonical: feedback_meta_jaguar.md / S8 lesson).
; Then store 1 to GPU_HLINE_DONE.
;
; Register usage (this routine, independent from GpuClip*):
;   r0  scratch (movei target)
;   r1  fb_base
;   r2  y
;   r3  xl
;   r4  xr
;   r5  color
;   r6  cur_addr (loop pointer)
;   r7  end_addr (inclusive)
;   r8  scratch (y<<7 partial)
;   r9  scratch (read-back throwaway)
;   r12 preloaded HlineLoop target
; -------------------------------------------------------------------------
GpuHlineMain:
    ; --- Load args from DRAM ---
    movei   #GPU_HLINE_FB_BASE, r0
    load    (r0), r1                        ; fb_base
    movei   #GPU_HLINE_Y, r0
    load    (r0), r2                        ; y
    movei   #GPU_HLINE_XL, r0
    load    (r0), r3                        ; xl
    movei   #GPU_HLINE_XR, r0
    load    (r0), r4                        ; xr
    movei   #GPU_HLINE_COLOR, r0
    load    (r0), r5                        ; color

    ; --- row_addr = fb_base + y*640 = fb + (y<<9) + (y<<7) ---
    move    r2, r6
    shlq    #9, r6                          ; r6 = y*512
    move    r2, r8
    shlq    #7, r8                          ; r8 = y*128
    add     r8, r6                          ; r6 = y*640
    add     r1, r6                          ; r6 = row_addr

    ; --- count = xr - xl + 1 (byte count) ---
    move    r4, r7
    sub     r3, r7                          ; r7 = xr - xl
    addq    #1, r7                          ; r7 = count

    ; --- start: cur_addr = row_addr + xl ---
    add     r3, r6                          ; r6 = start_addr

    movei   #GpuHlineLoop, r12

GpuHlineLoop:
    storeb  r5, (r6)
    addq    #1, r6
    subq    #1, r7                          ; count--
    jump    NE, (r12)                       ; while count != 0 -> continue
    nop

    ; --- Barrier: read-back DONE addr + nop delay ---
    movei   #GPU_HLINE_DONE, r0
    load    (r0), r9                        ; flush write buffer
    nop
    nop
    nop

    ; --- Set DONE flag = 1 ---
    moveq   #1, r9
    store   r9, (r0)
    nop
    nop
    nop

GpuHlineMain_Halt:
    movei   #GpuHlineMain_Halt, r0
    jump    (r0)
    nop

; -------------------------------------------------------------------------
; GpuRasterMain (S12) -- F4.2c-2a: edge build + slope div.
;
; Reads N input vertices from GPU_ARG_IN_BUF (8 bytes each: xq long, yq long).
; For each edge (v[i], v[(i+1) % N]):
;   - skip if y0 == y1 (horizontal edge)
;   - swap (x0,y0) <-> (x1,y1) if y0 > y1 so that y0 < y1
;   - dy_q = y1 - y0 (always > 0 post-swap)
;   - dx_q = x1 - x0 (signed Q16.16)
;   - slope = (|dx_q| / dy_q) with sign restore via abs+neg
;     (canonical: feedback_gpu_q1616_mult.md -- avoids imult bit15 trap;
;      G_DIVCTRL=1 16.16 unsigned mode + external sign tracker)
;   - emit 16-byte record at GPU_RASTER_EDGE_BUF: y0_q, y1_q, x0_q, slope_q
; Stores total edge_count to GPU_RASTER_EDGE_COUNT, sets DONE_E flag.
;
; Register usage:
;   r0       scratch (movei target / addr ptr)
;   r1       N (poly vertex count, constant during loop)
;   r2       poly_base (= GPU_ARG_IN_BUF, constant)
;   r3       edge_buf base (= GPU_RASTER_EDGE_BUF, constant)
;   r4       edge_count (output)
;   r5       i (loop var, 0..N-1)
;   r6,r7    S.xq, S.yq (post-swap if needed)
;   r8,r9    E.xq, E.yq (post-swap if needed)
;   r10,r11  scratch (vertex addr ptrs / swap tmp)
;   r12-r18  preloaded jump targets:
;     r12=Loop  r13=Done  r14=SkipH  r15=LoopTail
;     r16=NoWrap  r17=NoSwap  r18=DxPos
;   r19      dx sign tracker (preserves bit31)
;   r20      dy_q  (post-swap, > 0)
;   r21      dx_q  (signed)
;   r22      |dx_q|  (then quotient)
;   r23      slope_q (signed result)
;   r24,r25  edge record write ptr
;   r29      one-shot G_DIVCTRL setup
; -------------------------------------------------------------------------
GpuRasterMain:
    ; --- One-shot init G_DIVCTRL = 1 (16.16 unsigned mode) ---
    movei   #G_DIVCTRL, r0
    moveq   #1, r19
    store   r19, (r0)
    nop
    nop
    nop
    nop

    ; --- Load N from DRAM ---
    movei   #GPU_RASTER_POLY_COUNT, r0
    load    (r0), r1                        ; r1 = N

    ; --- Constants ---
    movei   #GPU_ARG_IN_BUF, r2             ; r2 = poly_base
    movei   #GPU_RASTER_EDGE_BUF, r3        ; r3 = edge_buf base

    ; --- 2c: load fb_base + color (persist across all phases) ---
    movei   #GPU_RASTER_FB_BASE, r0
    load    (r0), r29                        ; r29 = fb_base
    movei   #GPU_RASTER_COLOR, r0
    load    (r0), r30                        ; r30 = color (low 8 bits used)

    ; --- Loop state ---
    moveq   #0, r4                           ; edge_count = 0
    moveq   #0, r5                           ; i = 0

    ; --- Preload jump targets (canonical: feedback_gpu_branch_pattern.md) ---
    movei   #RasterEdge_Loop, r12
    movei   #RasterEdge_Done, r13
    movei   #RasterEdge_SkipH, r14
    movei   #RasterEdge_LoopTail, r15
    movei   #RasterEdge_NoWrap, r16
    movei   #RasterEdge_NoSwap, r17
    movei   #RasterEdge_DxPos, r18

RasterEdge_Loop:
    cmp     r1, r5                           ; r5 - r1 (i - N)
    jump    PL, (r13)                        ; if i >= N -> Done
    nop

    ; --- Load v[i] (S = current vertex) ---
    move    r5, r10
    shlq    #3, r10
    add     r2, r10                          ; r10 = &v[i]
    load    (r10), r6                        ; r6 = v[i].xq
    addq    #4, r10
    load    (r10), r7                        ; r7 = v[i].yq

    ; --- S14 F4.4: Load v[i].iq (intensity Q16.16) ---
    move    r5, r10
    shlq    #2, r10                          ; *4 (intensity long stride)
    movei   #GPU_ARG_INTENSITY_BUF, r0
    add     r0, r10
    load    (r10), r26                       ; r26 = S.iq

    ; --- Load v[(i+1) mod N] (E = next vertex) ---
    move    r5, r11
    addq    #1, r11                          ; r11 = i+1
    cmp     r1, r11                          ; r11 - r1
    jump    NE, (r16)                        ; if (i+1) != N -> NoWrap
    nop
    moveq   #0, r11                           ; wrap: r11 = 0

RasterEdge_NoWrap:
    ; --- S14 F4.4: Load v[(i+1)%N].iq before r11 is consumed ---
    move    r11, r10
    shlq    #2, r10                          ; *4 (intensity long stride)
    movei   #GPU_ARG_INTENSITY_BUF, r0
    add     r0, r10
    load    (r10), r27                       ; r27 = E.iq

    shlq    #3, r11
    add     r2, r11                          ; r11 = &v[(i+1)%N]
    load    (r11), r8                        ; r8 = E.xq
    addq    #4, r11
    load    (r11), r9                        ; r9 = E.yq

    ; --- Skip horizontal (y0 == y1) ---
    cmp     r9, r7                           ; r7 - r9 (y0 - y1)
    jump    EQ, (r14)                        ; if y0 == y1 -> SkipH
    nop

    ; --- Swap if y0 > y1 (canonical: feedback_gpu_cc_no_signed.md;
    ;     yq in [0..199<<16] small range, MI/PL safe for signed compare) ---
    cmp     r9, r7                           ; r7 - r9 (re-set flags)
    jump    MI, (r17)                        ; if y0 < y1 -> NoSwap
    nop

    ; Swap (r6,r7) <-> (r8,r9) via r10 tmp
    move    r6, r10
    move    r8, r6
    move    r10, r8
    move    r7, r10
    move    r9, r7
    move    r10, r9
    ; S14 F4.4: also swap intensities (r26 <-> r27) to follow vertex order
    move    r26, r10
    move    r27, r26
    move    r10, r27

RasterEdge_NoSwap:
    ; --- dy_q = r9 - r7 (= y1 - y0, always > 0 post-swap) ---
    move    r9, r20
    sub     r7, r20                          ; r20 = dy_q

    ; --- dx_q = r8 - r6 (= x1 - x0, signed) ---
    move    r8, r21
    sub     r6, r21                          ; r21 = dx_q (signed)

    ; --- Sign tracker + abs (canonical: feedback_gpu_q1616_mult.md) ---
    move    r21, r19                         ; r19 = sign tracker (bit31)
    move    r21, r22
    abs     r22                               ; r22 = |dx_q|

    ; --- Slope: |dx_q| / dy_q, both Q16.16 -> Q16.16 quotient ---
    div     r20, r22                          ; r22 = |dx|/dy (16.16 mode)
    nop                                        ; div latency

    ; --- Sign restore: negate if dx was negative ---
    move    r22, r23                          ; r23 = +slope
    btst    #31, r19
    jump    EQ, (r18)                         ; if dx >= 0 -> DxPos (skip neg)
    nop
    neg     r23

RasterEdge_DxPos:
    ; --- S14 F4.4: Compute di_dy_q (signed Q16.16) ---
    ; r31 = signed result. RECOMPUTE r20=dy_q in case dx slope's div corrupted it.
    move    r9, r20                            ; r20 = y1 (post-swap)
    sub     r7, r20                            ; r20 = dy_q (refresh)

    move    r27, r28                          ; r28 = E.iq (post-swap)
    sub     r26, r28                          ; r28 = di_q signed
    move    r28, r19                          ; r19 = sign tracker (overwrites dx)
    abs     r28                                ; r28 = |di_q|
    div     r20, r28                          ; r28 = |di|/dy unsigned (Q16.16)
    nop                                        ; div latency
    move    r28, r31                          ; r31 = +slope copy (signed result)
    btst    #31, r19
    jr      EQ, RasterEdge_DiPos              ; if di >= 0 -> skip neg
    nop
    neg     r31                                ; r31 = -slope (only if di<0)
RasterEdge_DiPos:
    ; r31 now holds signed di_dy_q

    ; --- Emit record at edge_buf + edge_count*24 (S14: stride bumped 16->24) ---
    move    r4, r24
    shlq    #4, r24                            ; *16
    move    r4, r0
    shlq    #3, r0                             ; *8
    add     r0, r24                            ; *24
    move    r3, r25
    add     r24, r25                          ; r25 = &edge_buf[edge_count]

    store   r7, (r25)                         ; +0  y0_q
    addq    #4, r25
    store   r9, (r25)                         ; +4  y1_q
    addq    #4, r25
    store   r6, (r25)                         ; +8  x0_q
    addq    #4, r25
    store   r23, (r25)                        ; +12 dx_dy_q
    addq    #4, r25
    store   r26, (r25)                        ; +16 i0_q (post-swap S.iq)
    addq    #4, r25
    store   r31, (r25)                        ; +20 di_dy_q (S14: signed in r31)

    addq    #1, r4                            ; edge_count++

RasterEdge_LoopTail:
    addq    #1, r5                            ; i++
    jump    T, (r12)                          ; back to Loop
    nop

RasterEdge_SkipH:
    jump    T, (r15)                          ; skip emit, go to LoopTail
    nop

RasterEdge_Done:
    ; --- Store edge_count ---
    movei   #GPU_RASTER_EDGE_COUNT, r0
    store   r4, (r0)
    nop
    nop
    nop

; -------------------------------------------------------------------------
; F4.2c-2b: scan-outer block (S12). After edge build, finds ymin_q across
; the emitted edges, then for the first 6 scanlines Y_int = ymin..ymin+5
; computes 2 active edge x-intersects, sorts (xl, xr), and dumps the pair
; to GPU_RASTER_SCAN_BUF as ground-truth trace.
;
; x-intersect math (avoids 32x32 mult by exploiting Y_q-y0_q decomposition):
;   u = Y_q - y0_q = K*ONE + HALF       (y0_q integer-aligned for test)
;   K = u >> 16  (positive integer)
;   contrib = K * slope_q + (slope_q >> 1)         ; signed
;   x_q = x0_q + contrib                            ; 32-bit wrap OK
; This works because (HALF * slope_q) >> 16 == slope_q >> 1 (sharq).
; For F4.3 production with non-integer-aligned y0_q (post-clip), need
; full 32x32 mult emulation -- out of scope for S12 trace verification.
;
; Scan-phase register map (overlaps edge-build phase regs, repurposed):
;   r0       scratch
;   r1       edge_count (saved from r4)
;   r2       ymin_int
;   r3       edge_buf base (constant, preserved)
;   r4       scan_idx (0..5)
;   r5       Y_int
;   r6       Y_q (pixel-center, integer + HALF)
;   r7       edge idx (inner loop)
;   r8       xs[0]   (first active edge x_q)
;   r9       xs[1]   (second active edge x_q)
;   r10,r11  scratch (vertex/edge ptrs)
;   r12-r18  preloaded jump targets (re-loaded for scan phase)
;   r19      slope sign tracker
;   r20-r23  edge fields: y0_q, y1_q, x0_q, slope_q
;   r24      K / scratch
;   r25      |slope| / prod
;   r26      contrib / x_q
;   r27      ymin_q working / sort tmp
;   r28      xs_count (0..2)
; -------------------------------------------------------------------------

    ; --- Reshuffle for scan phase: r1 = edge_count ---
    move    r4, r1

    ; --- Preload targets for find-minmax + escape paths ---
    movei   #FindMinMax_Loop, r12
    movei   #FindMinMax_Done, r13
    movei   #FindMinMax_NotMin, r14
    movei   #FindMinMax_NotMax, r17
    movei   #ScanRaster_DoneAll, r15

    ; --- Skip scan if no edges were emitted ---
    moveq   #0, r0
    cmp     r0, r1                            ; r1 - 0
    jump    EQ, (r15)                         ; if edge_count == 0 -> DoneAll
    nop

    ; --- Find ymin_q AND ymax_q across emitted edges (combined loop) ---
    load    (r3), r27                         ; r27 = edge[0].y0_q (min cand)
    move    r3, r10
    addq    #4, r10
    load    (r10), r26                        ; r26 = edge[0].y1_q (max cand)
    moveq   #1, r5                             ; idx i = 1

FindMinMax_Loop:
    cmp     r1, r5                            ; r5 - edge_count
    jump    PL, (r13)                         ; if i >= edge_count -> Done
    nop

    ; S14: edge stride bumped from 16 to 24 byte (i*24 = i*16 + i*8).
    move    r5, r10
    shlq    #4, r10                           ; r10 = i*16
    move    r5, r0
    shlq    #3, r0                            ; r0 = i*8
    add     r0, r10                           ; r10 = i*24
    move    r3, r11
    add     r10, r11                          ; r11 = &edge[i]

    ; Min check: edge[i].y0_q vs r27
    load    (r11), r0                         ; r0 = edge[i].y0_q
    cmp     r0, r27                           ; r27 - r0
    jump    MI, (r14)                         ; if r27 < r0 -> NotMin (skip update)
    nop
    move    r0, r27                           ; update min

FindMinMax_NotMin:
    ; Max check: edge[i].y1_q vs r26
    addq    #4, r11
    load    (r11), r0                         ; r0 = edge[i].y1_q
    cmp     r26, r0                           ; r0 - r26
    jump    MI, (r17)                         ; if r0 < r26 -> NotMax (skip update)
    nop
    move    r0, r26                           ; update max

FindMinMax_NotMax:
    addq    #1, r5
    jump    T, (r12)                          ; back to FindMinMax_Loop
    nop

FindMinMax_Done:
    ; r27 = ymin_q, r26 = ymax_q. Compute integer bounds:
    ;   ymin_int      = (ymin_q + (HALF-1)) >> 16     -> r5 (Y_int loop var)
    ;   ymax_int + 1  = (ymax_q + (HALF-1)) >> 16     -> r31 (loop bound)
    movei   #$7FFF, r0
    add     r0, r27
    sharq   #16, r27                          ; r27 = ymin_int
    move    r27, r5                           ; r5 = Y_int (init)
    add     r0, r26
    sharq   #16, r26                          ; r26 = ymax_int + 1
    move    r26, r31                          ; r31 = loop bound

    ; --- Reload jump targets for scan + hline-fill phase ---
    movei   #ScanY_Loop, r12
    movei   #HlineFill_Skip, r13              ; new in 2c
    movei   #ScanY_EdgeLoop, r14
    movei   #ScanY_EdgeDone, r16
    movei   #ScanY_NotActive, r17
    movei   #ScanY_AfterMul, r18

ScanY_Loop:
    cmp     r31, r5                           ; r5 - r31 (Y_int - (ymax+1))
    jump    PL, (r15)                         ; if Y_int >= ymax+1 -> DoneAll
    nop

    ; --- Y_q = (Y_int << 16) + HALF ---
    move    r5, r6
    shlq    #16, r6
    movei   #$8000, r0
    add     r0, r6                            ; r6 = Y_q

    ; --- Inner edge loop: collect xs[] for active edges ---
    moveq   #0, r7                            ; edge idx
    moveq   #0, r28                            ; xs_count = 0

ScanY_EdgeLoop:
    cmp     r1, r7                            ; r7 - edge_count
    jump    PL, (r16)                         ; if i >= ec -> EdgeDone
    nop

    ; --- Load edge[i] = (y0_q, y1_q, x0_q, dx_dy_q, i0_q, di_dy_q) ---
    ; S14 C3: load all 6 fields (24-byte stride). i0_q->r10, di_dy_q->r11.
    move    r7, r10
    shlq    #4, r10
    move    r7, r0
    shlq    #3, r0
    add     r0, r10
    move    r3, r11
    add     r10, r11
    load    (r11), r20                        ; +0  y0_q
    addq    #4, r11
    load    (r11), r21                        ; +4  y1_q
    addq    #4, r11
    load    (r11), r22                        ; +8  x0_q
    addq    #4, r11
    load    (r11), r23                        ; +12 dx_dy_q
    addq    #4, r11
    load    (r11), r10                        ; +16 i0_q (overwrites r10 offset)
    addq    #4, r11
    load    (r11), r11                        ; +20 di_dy_q (self-load, r11 reused)

    ; --- Active test 1: Y_q < y0_q ? ---
    cmp     r20, r6                           ; r6 - r20 (Y_q - y0_q)
    jump    MI, (r17)                         ; if Y_q < y0_q -> NotActive
    nop
    ; --- Active test 2: Y_q >= y1_q ? ---
    cmp     r21, r6                           ; r6 - r21 (Y_q - y1_q)
    jump    PL, (r17)                         ; if Y_q >= y1_q -> NotActive
    nop

    ; --- Compute K = (Y_q - y0_q) >> 16 ---
    move    r6, r24
    sub     r20, r24
    sharq   #16, r24                          ; r24 = K (positive int)

    ; --- Sign tracker + abs of slope (canonical: feedback_gpu_q1616_mult.md) ---
    move    r23, r19                          ; sign tracker
    move    r23, r25
    abs     r25                                ; r25 = |slope|

    ; --- prod = K * |slope| full 32-bit (S13 step 1.5: lo/hi split) ---
    ;     mult r24,r25 alone truncates to low16 of |slope|. For body 0
    ;     edges, |slope| hi16 can reach $0E (43.6% of edges have |dx|>=|dy|
    ;     per body_screen_stats.py). Split into lo16+hi16 partial products.
    move    r25, r0
    shrq    #16, r0                           ; r0  = hi16(|slope|)
    shlq    #16, r25
    shrq    #16, r25                          ; r25 = lo16(|slope|)
    mult    r24, r25                          ; r25 = K * lo16
    mult    r24, r0                           ; r0  = K * hi16
    shlq    #16, r0
    add     r0, r25                           ; r25 = K * |slope| (full)

    ; --- Sign restore ---
    btst    #31, r19
    jump    EQ, (r18)                         ; if dx >= 0 -> AfterMul (skip neg)
    nop
    neg     r25

ScanY_AfterMul:
    ; --- frac contrib = slope_q >> 1 (signed arith shift) ---
    move    r23, r26
    sharq   #1, r26

    ; --- contrib = prod + frac, x_q = x0_q + contrib ---
    add     r26, r25                          ; r25 = contrib
    move    r22, r26
    add     r25, r26                          ; r26 = x_q

    ; --- S14 C3: Compute i_q = i0 + K*didy + didy/2 (signed Q16.16) ---
    ; Single mult ok: didy upper16 = 0 for body intensity (max ~$1D89, see A3).
    ; r10 = i0_q, r11 = didy_q, r24 = K, output r25 = signed i_q.
    move    r11, r19                          ; sign tracker overwrites dx
    move    r11, r25
    abs     r25                                ; r25 = |didy_q|
    mult    r24, r25                          ; r25 = K * |didy| (single mult)
    btst    #31, r19
    jr      EQ, ScanY_DiPos                   ; if didy >= 0 -> skip neg
    nop
    neg     r25                                ; r25 = signed K*|didy|
ScanY_DiPos:
    move    r11, r0                           ; r0 = didy_q signed
    sharq   #1, r0                            ; r0 = didy/2 signed
    add     r0, r25                           ; r25 = contrib_i (K*didy+didy/2)
    add     r10, r25                          ; r25 = i0 + contrib = i_q signed

    ; --- Stash x_q in xs[0]/xs[1] AND i_q in is[0]/is[1] parallel ---
    moveq   #0, r0
    cmp     r0, r28                           ; r28 - 0
    jr      NE, ScanY_SetXs1_jr               ; xs_count != 0 -> set xs[1]/is[1]
    nop
    move    r26, r8                           ; xs[0] = x_q
    move    r25, r4                           ; S14 C3: is[0] = i_q
    addq    #1, r28
    jump    T, (r17)                          ; -> NotActive (continue)
    nop
ScanY_SetXs1_jr:
    move    r26, r9                           ; xs[1] = x_q
    move    r25, r2                           ; S14 C3: is[1] = i_q
    addq    #1, r28
    ; fall through to ScanY_NotActive

ScanY_NotActive:
    addq    #1, r7                            ; i++
    jump    T, (r14)                          ; -> EdgeLoop
    nop

ScanY_EdgeDone:
    ; --- Sort: if xs[0] (r8) > xs[1] (r9), swap (also is[0]/is[1]) ---
    cmp     r9, r8                            ; r8 - r9
    jr      MI, ScanY_AfterSort_jr            ; if r8 < r9, no swap
    nop
    move    r8, r27                           ; tmp = r8
    move    r9, r8
    move    r27, r9
    ; S14 C3: also swap is[0]/is[1] (r4 <-> r2) parallel
    move    r4, r27
    move    r2, r4
    move    r27, r2
ScanY_AfterSort_jr:

    ; --- Top-left rounding: xl_int = (xs[0] + (HALF-1)) >> 16 ---
    movei   #$7FFF, r0
    move    r8, r24
    add     r0, r24
    sharq   #16, r24                          ; r24 = xl_int
    ; --- xr_int = ((xs[1] + (HALF-1)) >> 16) - 1 ---
    move    r9, r25
    add     r0, r25
    sharq   #16, r25
    subq    #1, r25                            ; r25 = xr_int

    ; --- Skip if xl_int > xr_int (HI = unsigned greater; safe for our range) ---
    cmp     r25, r24                          ; r24 - r25
    jump    HI, (r13)                         ; -> HlineFill_Skip
    nop

    ; --- row_addr = fb_base + Y_int*640 = fb + (Y<<9) + (Y<<7) ---
    move    r5, r10
    shlq    #9, r10                           ; Y*512
    move    r5, r11
    shlq    #7, r11                           ; Y*128
    add     r11, r10                          ; Y*640
    add     r29, r10                          ; fb_base + Y*640
    add     r24, r10                          ; r10 = start_addr (+ xl_int)

    ; --- count = xr_int - xl_int + 1 ---
    move    r25, r11
    sub     r24, r11
    addq    #1, r11                            ; r11 = byte count

    ; --- S14 C4: Compute didx_q = (i_right - i_left) / span_q (signed Q16.16) ---
    ; r4 = i_left, r2 = i_right (post-sort). r11 = count = span (int).
    ; Build span_q = r11 << 16 in r27 (= div divisor; div clobbers Rs canonical).
    move    r2, r0                            ; r0 = i_right
    sub     r4, r0                            ; r0 = di_total signed Q16.16
    move    r0, r19                           ; r19 = sign tracker
    abs     r0                                 ; r0 = |di_total|
    move    r11, r27                          ; r27 = count
    shlq    #16, r27                          ; r27 = span_q Q16.16
    div     r27, r0                           ; r0 = |di|/span_q
    nop                                        ; div latency
    move    r0, r27                           ; r27 = +didx copy (will be signed result)
    btst    #31, r19
    jr      EQ, HlineFill_DidxPos             ; if di >= 0 -> skip neg
    nop
    neg     r27                                ; r27 = signed didx_q
HlineFill_DidxPos:
    ; r27 = signed didx_q, r4 = current intensity_q (starts at i_left)

    ; --- S14 C4: Inline hline gouraud fill (per-pixel intensity + CLUT lookup) ---
HlineFill_Loop:
    move    r4, r0                            ; r0 = intensity_q
    sharq   #16, r0                           ; r0 = shade (high 16 bit, signed)
    add     r30, r0                           ; r0 = coul1 + shade (low 8 bit used)
    storeb  r0, (r10)
    addq    #1, r10
    add     r27, r4                           ; intensity_q += didx_q
    subq    #1, r11
    jr      NE, HlineFill_Loop
    nop

HlineFill_Skip:
    addq    #1, r5                             ; Y_int++
    jump    T, (r12)                           ; -> ScanY_Loop
    nop

ScanRaster_DoneAll:
    ; --- Barrier: read-back DONE addr + nop delay ---
    movei   #GPU_RASTER_DONE_E, r0
    load    (r0), r24                         ; flush write buffer
    nop
    nop
    nop

    ; --- Set DONE flag = 1 ---
    moveq   #1, r24
    store   r24, (r0)
    nop
    nop
    nop

GpuRasterMain_Halt:
    movei   #GpuRasterMain_Halt, r0
    jump    (r0)
    nop

; -------------------------------------------------------------------------
; GpuScaleMain (S19) -- 8bpp 2x nearest-neighbor brick scaler with
; transparency on src idx 0 (ghost-prevention vs Blitter B_CMD_SPRITE behavior).
;
; Reads 6 args from DRAM:
;   GPU_SCALE_SRC_BASE   src ptr (= BH_PIXELS + src_skip_y*src_pitch + src_skip_x)
;   GPU_SCALE_DST_BASE   dst ptr (= BG_PRISTINE + dst_y*640 + dst_x)
;   GPU_SCALE_SRC_DX     src cols pixel post-clip (1x space)
;   GPU_SCALE_SRC_DY     src rows pixel post-clip (1x space)
;   GPU_SCALE_SRC_PITCH  src row stride bytes (= padded_DX from PadDxLut)
;   GPU_SCALE_DST_PITCH  dst row stride bytes (= BG_W = 640)
;
; Loop body (per src pixel): load byte, if 0 skip dst writes (transparency),
; else write 2x2 dst block: top[0], top[1], bot[0], bot[1]. Advance src by 1,
; dst cursors by 2. Outer row: src += src_pitch, dst += 2*dst_pitch.
;
; Register usage:
;   r0  scratch (movei target, temp ptr)
;   r1  src_base
;   r2  src_dx (preserved across rows)
;   r3  src_dy (decremented as outer counter)
;   r4  dst_base
;   r5  src_pitch
;   r6  dst_pitch
;   r7  cur_src_row     (advances by src_pitch per row)
;   r8  cur_dst_top_row (advances by 2*dst_pitch per row)
;   r9  cur_dst_bot_row (= cur_dst_top + dst_pitch, advances 2*dst_pitch)
;   r10 sx counter (= src_dx, decremented to 0)
;   r11 src cursor (in-row)
;   r12 dst top cursor (in-row, advances by 2 per pixel)
;   r13 dst bot cursor (in-row, advances by 2 per pixel)
;   r14 pixel value
;   r15 scratch (write-flush load)
;   r20 SxLoop target (preloaded)
;   r21 SxAdv target (preloaded, skip-target)
;   r22 SyLoop target (preloaded)
;   r24 2*dst_pitch (precomputed)
; -------------------------------------------------------------------------
GpuScaleMain:
    ; --- Load args ---
    movei   #GPU_SCALE_SRC_BASE, r0
    load    (r0), r1
    movei   #GPU_SCALE_DST_BASE, r0
    load    (r0), r4
    movei   #GPU_SCALE_SRC_DX, r0
    load    (r0), r2
    movei   #GPU_SCALE_SRC_DY, r0
    load    (r0), r3
    movei   #GPU_SCALE_SRC_PITCH, r0
    load    (r0), r5
    movei   #GPU_SCALE_DST_PITCH, r0
    load    (r0), r6

    ; --- Precompute 2*dst_pitch ---
    move    r6, r24
    shlq    #1, r24

    ; --- Init row cursors ---
    move    r1, r7                            ; cur_src_row = src_base
    move    r4, r8                            ; cur_dst_top_row = dst_base
    move    r4, r9
    add     r6, r9                            ; cur_dst_bot_row = dst_base + dst_pitch

    ; --- Preload jump targets ---
    movei   #GpuScale_SxLoop, r20
    movei   #GpuScale_SxAdv, r21
    movei   #GpuScale_SyLoop, r22

GpuScale_SyLoop:
    move    r2, r10                           ; sx counter = src_dx
    move    r7, r11                           ; src cursor = src_row start
    move    r8, r12                           ; dst top cursor = top_row start
    move    r9, r13                           ; dst bot cursor = bot_row start

GpuScale_SxLoop:
    loadb   (r11), r14                        ; r14 = src pixel
    addq    #1, r11                           ; src++
    cmpq    #0, r14
    jump    EQ, (r21)                         ; transparent: skip stores
    nop
    storeb  r14, (r12)                        ; top[col]
    storeb  r14, (r13)                        ; bot[col]
    move    r12, r0
    addq    #1, r0
    storeb  r14, (r0)                         ; top[col+1]
    move    r13, r0
    addq    #1, r0
    storeb  r14, (r0)                         ; bot[col+1]

GpuScale_SxAdv:                               ; r21 target (convergence)
    addq    #2, r12                           ; advance dst top by 2 cols
    addq    #2, r13                           ; advance dst bot by 2 cols
    subq    #1, r10                           ; counter--
    jump    NE, (r20)                         ; loop while sx != 0
    nop

    ; --- End of inner row: advance row cursors ---
    add     r5, r7                            ; cur_src_row += src_pitch
    add     r24, r8                           ; cur_dst_top_row += 2*dst_pitch
    add     r24, r9                           ; cur_dst_bot_row += 2*dst_pitch

    subq    #1, r3                            ; sy counter--
    jump    NE, (r22)                         ; loop while sy != 0
    nop

    ; --- Done barrier: read-back DONE addr + nop delay ---
    movei   #GPU_SCALE_DONE, r0
    load    (r0), r15                         ; flush write buffer
    nop
    nop
    nop

    ; --- Set DONE flag = 1 ---
    moveq   #1, r15
    store   r15, (r0)
    nop
    nop
    nop

GpuScaleMain_Halt:
    movei   #GpuScaleMain_Halt, r0
    jump    (r0)
    nop

GpuRaster_End:
    .68000
GpuRasterRomEnd:
