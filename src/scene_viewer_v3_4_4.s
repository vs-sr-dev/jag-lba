;
; JagLBA scene viewer V3.4.0
; --------------------------
; 8bpp + CLUT refactor. BG buffer and brick blobs hold palette indices (1 B/px)
; instead of RGB16 (2 B/px). Halves brick storage and BG memory; OP composites
; via CLUT lookup. Step toward V3.4.1 double-buffer (no tearing on re-render).
;
; Boot flow unchanged from V3.3: cart parse -> palette to DRAM -> palette to
; CLUT $F00400 -> brick pre-decode (8bpp idx, padded with 0=transparent) ->
; grid RLE -> cube -> ComputeCamera -> RenderScene -> OPSetup -> MainLoop.
;

    .68000
    .include "jaguar.inc"

    .org    $802000

; ---------------------------------------------------------------------------
; DRAM layout
; ---------------------------------------------------------------------------
PALETTE_DRAM    EQU     $2000
VDB_VAL         EQU     $2200
VDE_VAL         EQU     $2202
OP_PH0_LO_VAL   EQU     $2204

; Clip scratch: used per-cell during clipping math
TMP_LSK         EQU     $2208           ; long: left skip in source pixels
TMP_TSK         EQU     $220C           ; long: top skip in source pixels

; V3.2 camera scroll state (updated by ViHandler from joypad)
CAM_OFFSET_X    EQU     $2218           ; word: BG window X offset, [0..320], step 4
CAM_OFFSET_Y    EQU     $221A           ; word: BG window Y offset, [0..352], step 1
SCROLL_W_MAX    EQU     320             ; BG_W - VIEW_W = 640 - 320
SCROLL_H_MAX    EQU     352             ; BG_H - VIEW_H = 592 - 240

; V3.3 dynamic camera + re-render on edge approach
CAM_GX          EQU     $221C           ; word: camera cube X
CAM_GZ          EQU     $221E           ; word: camera cube Z
CAM_VX_VAL      EQU     $2220           ; long: 160 - (cam_gx-cam_gz)*24
CAM_VY_VAL      EQU     $2224           ; long: 120 - (cam_gx+cam_gz)*12 + cam_gy*15
GX_LO_VAL       EQU     $2228           ; word: walk gx lower bound
GX_HI_VAL       EQU     $222A           ; word: walk gx upper bound (exclusive)
GZ_LO_VAL       EQU     $222C           ; word: walk gz lower bound
GZ_HI_VAL       EQU     $222E           ; word: walk gz upper bound (exclusive)
REDRAW_FLAG     EQU     $2230           ; byte: 1 if MainLoop should re-render
ACTIVE_BG_ID    EQU     $2231           ; byte: 0 = BG_A on display, 1 = BG_B on display
NEXT_OFFSET_X   EQU     $2232           ; word: offset_x to commit after re-render
NEXT_OFFSET_Y   EQU     $2234           ; word: offset_y to commit after re-render
EDGE_MARGIN     EQU     16              ; trigger threshold from BG edge
CAM_GY_FIXED    EQU     4               ; for now camera height is fixed

BRICK_HANDLES   EQU     $3000
BRICK_BLOBS     EQU     $4000           ; phrase-aligned ($4000 mod 8 = 0)

; V3.4.0: BG is 8bpp paletted. 640x592 = 378880 B = ~370 KB per buffer.
; OP composites via CLUT (loaded at $F00400). IWIDTH=80 phrases (640 B/row /8).
; V3.4.1 will add BG_B for ping-pong; for now V3.4.0 keeps single buffer at BG_A.
BG_A            EQU     $100000         ; 640x592 8bpp = ~370 KB ($100000-$15C800)
BG_B            EQU     $160000         ; second buffer ($160000-$1BC800), pre CUBE
BG_W            EQU     640
BG_H            EQU     592
CUBE_DRAM       EQU     $1C0000

; Cart binary header field offsets
HDR_PAL_OFF     EQU     16
HDR_GRID_OFF    EQU     24
HDR_BLL_OFF     EQU     32
HDR_BLL_COUNT   EQU     40
HDR_BDIR_OFF    EQU     44
HDR_BDIR_COUNT  EQU     48
HDR_BPIX_OFF    EQU     52

; Brick blob layout (V2.1: 8-byte phrase-aligned header + padded RGB16 pixels)
BH_FLAGS        EQU     0               ; long: A2_FLAGS pre-built (WID|PIXEL16|XADDPIX|PITCH1)
BH_DX           EQU     4               ; byte: visible width
BH_DY           EQU     5               ; byte: height
BH_HOTX         EQU     6               ; byte signed: hotspot X offset
BH_HOTY         EQU     7               ; byte signed: hotspot Y offset
BH_PIXELS       EQU     8               ; padded RGB16 pixel data (rows = padded_DX wide)

; Blitter command for masked sprite blit (A1=dest, A2=src):
;   SRCEN | UPDA1 | UPDA2 | DCOMPEN | LFU_REPLACE
B_CMD_SPRITE    EQU     $09800601

; Iso projection constants
GRID_X          EQU     64
GRID_Y          EQU     25
GRID_Z          EQU     64

; Camera at room center (gx=8, gy=4, gz=50)
; iso(8,4,50) = ((8-50)*24, (8+50)*12 - 4*15) = (-1008, 636)
; viewport_x = 160 - (-1008) = 1168
; viewport_y = 120 - 636 = -516
CAM_VX          EQU     1168
CAM_VY          EQU     -516

; V3.1 bounding box: cells whose iso projection lands within BG 640x592.
; Width constraint: gx-gz in [-50, -20] (sx range -50..690 with margin).
; Height constraint: gx+gz in [38, 96] for gy=0, up to [68, 126] for gy=24.
; Tall structures (high gy) at back of room (high gx+gz) project to upper-left.
; Bounds restored to V2.5 superset to keep all viewport content visible.
GX_MIN          EQU     0
GX_MAX          EQU     18
GZ_MIN          EQU     40
GZ_MAX          EQU     63              ; +1 vs V2.5 to catch high-gy back walls

; ---------------------------------------------------------------------------
Start:
    move.l  #$00200000, a7
    move.w  #$2700, sr

; --- Init scroll offsets to 0 (visual identity to V3.1 at boot) ---
    moveq   #0, d0
    move.w  d0, CAM_OFFSET_X
    move.w  d0, CAM_OFFSET_Y

; --- Install defensive RTE handlers + ViHandler at $100 ---
    move.l  #ViHandler, $100
    move.l  #IrqRte, $64
    move.l  #IrqRte, $68
    move.l  #IrqRte, $6C
    move.l  #IrqRte, $70
    move.l  #IrqRte, $74
    move.l  #IrqRte, $78
    move.l  #IrqRte, $7C

; --- NTSC/PAL detect + video timing ---
    move.w  BASE+$14002, d0
    and.w   #$10, d0
    beq.s   IsPAL
IsNTSC:
    move.w  #823, d2
    move.w  #1409, d0
    move.w  #266, d6
    move.w  #241, d4
    bra.s   DoVid
IsPAL:
    move.w  #843, d2
    move.w  #1381, d0
    move.w  #322, d6
    move.w  #287, d4
DoVid:
    move.w  d0, d1
    asr     #1, d1
    sub.w   d1, d2
    add.w   #4, d2
    sub.w   #1, d1
    or.w    #$400, d1
    move.w  d1, HDE
    move.w  d2, HDB1
    move.w  d2, HDB2
    move.w  d6, d5
    sub.w   d4, d5
    move.w  d5, VDB
    move.w  d5, VDB_VAL
    move.w  d6, d0
    add.w   d4, d0
    move.w  d0, VDE
    move.w  d0, VDE_VAL
    move.w  d5, d0
    subq.w  #4, d0
    move.w  d0, VI
    move.l  #$0, BORD1

    moveq   #0, d3
    move.w  d5, d3
    and.l   #$7FF, d3
    lsl.l   #3, d3
    or.l    #$023C0000, d3
    move.l  d3, OP_PH0_LO_VAL

    move.l  #0, G_CTRL
    move.l  #$00070007, G_END

; --- Sanity-check cart magic ---
    lea     SceneData, a6
    cmpi.l  #$4A41474C, (a6)
    bne     BadCart
    cmpi.l  #$42413031, 4(a6)
    bne     BadCart

; --- Copy palette from cart to DRAM (kept for any future direct lookup use) ---
    move.l  HDR_PAL_OFF(a6), d0
    lea     0(a6,d0.l), a0
    movea.l #PALETTE_DRAM, a1
    move.w  #127, d0
CopyPal:
    move.l  (a0)+, (a1)+
    dbra    d0, CopyPal

; --- V3.4.0: upload palette to TOM CLUT at $F00400 (256 RGB16 = 512 B) ---
    movea.l #PALETTE_DRAM, a0
    movea.l #CLUT, a1
    move.w  #127, d0
CopyClut:
    move.l  (a0)+, (a1)+
    dbra    d0, CopyClut

; ---------------------------------------------------------------------------
; Pre-decode all 526 bricks: cart 8bpp paletted -> DRAM 8bpp padded for Blitter.
; Each blob: 8-byte header [A2_FLAGS,DX,DY,HotX,HotY] + padded_DX*DY index bytes.
; Rows padded to next valid Blitter WID with 0 (transparent). Blob sizes rounded
; up to phrase boundary so each blob starts phrase-aligned.
; BRICK_HANDLES[i] = pointer to start of brick i blob.
; ---------------------------------------------------------------------------
    move.l  HDR_BDIR_OFF(a6), d0
    lea     0(a6,d0.l), a2          ; a2 = bdir cursor
    move.l  HDR_BDIR_COUNT(a6), d7
    subq.l  #1, d7                   ; outer brick counter

    movea.l #BRICK_HANDLES, a3       ; a3 = handle cursor
    movea.l #BRICK_BLOBS, a4         ; a4 = blob cursor (sequential alloc)
    movea.l #PALETTE_DRAM, a5

DecodeBrickLoop:
    move.l  a4, (a3)+                ; save blob start as handle (phrase-aligned)

    move.l  (a2)+, d4                ; pix_off (BE u32)
    moveq   #0, d5
    move.b  (a2)+, d5                ; d5 = DX
    moveq   #0, d6
    move.b  (a2)+, d6                ; d6 = DY
    moveq   #0, d3
    move.b  (a2)+, d3                ; d3 = HotX
    moveq   #0, d2
    move.b  (a2)+, d2                ; d2 = HotY

; Lookup padded_DX (word) and A2_FLAGS (long) from LUT indexed by DX
    move.w  d5, d0
    add.w   d0, d0                    ; DX*2 (word LUT stride)
    lea     PadDxLut(pc), a1
    move.w  0(a1,d0.w), d1            ; d1 = padded_DX

    move.w  d5, d0
    lsl.w   #2, d0                    ; DX*4 (long LUT stride)
    lea     PadFlagsLut(pc), a1
    move.l  0(a1,d0.w), d0            ; d0 = A2_FLAGS

; Write 8-byte header (FLAGS long + DX/DY/HotX/HotY bytes)
    move.l  d0, (a4)+                 ; +0 BH_FLAGS
    move.b  d5, (a4)+                 ; +4 BH_DX
    move.b  d6, (a4)+                 ; +5 BH_DY
    move.b  d3, (a4)+                 ; +6 BH_HOTX
    move.b  d2, (a4)+                 ; +7 BH_HOTY

; pad_count = padded_DX - DX (transparent pixels appended per row)
    move.w  d1, d2
    sub.w   d5, d2                    ; d2 = pad_count (>= 0)

; Cart 8bpp source pointer
    move.l  HDR_BPIX_OFF(a6), d0
    add.l   d4, d0
    lea     0(a6,d0.l), a1            ; a1 = src 8bpp pixels in cart

; Row loop -- copy DX index bytes verbatim, then pad with 0 to padded_DX.
    move.w  d6, d4                    ; d4 = DY counter
    subq.w  #1, d4
.brk_row:
    move.w  d5, d3                    ; d3 = DX visible cols
    subq.w  #1, d3
.brk_col:
    move.b  (a1)+, (a4)+              ; idx byte direct (V3.4.0)
    dbra    d3, .brk_col

    tst.w   d2
    beq.s   .brk_no_pad
    move.w  d2, d3
    subq.w  #1, d3
    moveq   #0, d0
.brk_pad:
    move.b  d0, (a4)+                 ; transparent (idx 0) padding to padded_DX
    dbra    d3, .brk_pad
.brk_no_pad:
    dbra    d4, .brk_row

; Round blob ptr up to phrase boundary so next blob starts phrase-aligned
    move.l  a4, d0
    addq.l  #7, d0
    and.l   #$FFFFFFF8, d0
    movea.l d0, a4

    dbra    d7, DecodeBrickLoop

; ---------------------------------------------------------------------------
; Decode grid 0 RLE columns -> flat cube at CUBE_DRAM.
; cube[z][x][y] -> address CUBE_DRAM + ((z*64+x)*25 + y) * 2
; Source: grid section, offset table 4096 BE u16 + RLE columns.
; ---------------------------------------------------------------------------
    move.l  HDR_GRID_OFF(a6), d0
    lea     0(a6,d0.l), a3           ; a3 = grid section base

    moveq   #0, d7                    ; d7 = gz
GzLoop:
    moveq   #0, d6                    ; d6 = gx
GxLoop:
    move.w  d7, d0
    lsl.w   #6, d0                    ; d0 = gz*64
    add.w   d6, d0                    ; d0 = gz*64 + gx
    add.w   d0, d0                    ; d0 *= 2 (offset table stride)
    move.w  0(a3,d0.w), d1            ; d1 = column byte offset (BE u16)

    movea.l a3, a2
    adda.w  d1, a2                    ; a2 = column data start

    move.w  d7, d0
    lsl.w   #6, d0                    ; gz*64
    add.w   d6, d0                    ; gz*64+gx
    mulu.w  #50, d0                   ; *25 *2 = bytes per column
    movea.l #CUBE_DRAM, a4
    adda.l  d0, a4                    ; a4 = cube column dest (25 UWORDs)

    bsr     DecompCol

    addq.w  #1, d6
    cmp.w   #GRID_X, d6
    blt     GxLoop
    addq.w  #1, d7
    cmp.w   #GRID_Z, d7
    blt     GzLoop

; ---------------------------------------------------------------------------
; V3.3: init camera, compute derived values, render scene.
; ---------------------------------------------------------------------------
    move.w  #8, CAM_GX
    move.w  #50, CAM_GZ
    move.b  #0, REDRAW_FLAG
; V3.4.1 force first render to BG_A, then mark BG_A active for ViHandler.
    move.b  #1, ACTIVE_BG_ID              ; render target = NOT active = BG_A
    bsr     ComputeCamera
    bsr     RenderScene
    move.b  #0, ACTIVE_BG_ID              ; BG_A is now what ViHandler displays

    bra     OPSetup                       ; skip past the render subroutine body

; ---------------------------------------------------------------------------
; RenderScene: BG clear via Blitter pattern fill, then walk cube and blit
; each filled cell. Reads CAM_VX_VAL/VY_VAL and walk bounds from DRAM.
; Called at boot and on REDRAW_FLAG set from MainLoop.
; ---------------------------------------------------------------------------
RenderScene:
; --- V3.4.1 select render target = the buffer NOT currently active ---
;     ACTIVE_BG_ID 0 -> render to BG_B; 1 -> render to BG_A.
    moveq   #0, d0
    move.b  ACTIVE_BG_ID, d0
    bne.s   .rs_target_a
    move.l  #BG_B, A1_BASE
    bra.s   .rs_target_done
.rs_target_a:
    move.l  #BG_A, A1_BASE
.rs_target_done:

; --- Clear BG to 0 via Blitter pattern fill (640x592, 8bpp) ---
    move.l  #$00014A18, A1_FLAGS          ; PIXEL8|XADDPIX|WID640|PITCH1
    move.l  #$0001FD80, A1_STEP           ; dy=+1, dx=-640
    moveq   #0, d0
    move.l  d0, A1_PIXEL
    move.l  #$02500280, B_COUNT           ; 592 rows << 16 | 640 px
    move.l  d0, B_PATD
    move.l  d0, B_PATD+4
    move.l  #$01810200, B_CMD             ; PATDSEL|LFU_REPLACE -> fire
.rs_clr_wait:
    move.l  B_CMD, d0
    btst    #0, d0
    beq.s   .rs_clr_wait

; --- Re-confirm Blitter constants for sprite blit (A1_BASE/FLAGS preserved,
;     B_PATD remains 0 from clear above) ---
    lea     SceneData, a6                 ; cart base needed by render

    move.w  GZ_LO_VAL, d7             ; d7 = gz, restricted to [GZ_LO_VAL, GZ_HI_VAL)
SceneZLoop:
    move.w  GX_LO_VAL, d6             ; d6 = gx, restricted to [GX_LO_VAL, GX_HI_VAL)
SceneXLoop:
    moveq   #0, d5                    ; d5 = gy (full range)
SceneYLoop:

; Read cell from cube
    move.w  d7, d0
    lsl.w   #6, d0                    ; gz*64
    add.w   d6, d0                    ; gz*64+gx
    mulu.w  #50, d0                   ; *25*2 (mulu cleans d0 high word)
    moveq   #0, d1
    move.w  d5, d1                    ; d1 = gy zero-extended
    add.w   d1, d1                    ; gy*2
    add.l   d1, d0
    movea.l #CUBE_DRAM, a0
    move.w  0(a0,d0.l), d4            ; d4 = cell UWORD
    beq     CellEmpty

; Decode num_block, pos_block (low byte = num_block, high byte = pos_block)
; Cube was decoded preserving cart's BE layout; cart was already BE-converted
; so here d4 high byte = pos_block, low byte = num_block (matches Python parser).
    move.b  d4, d3                    ; d3 = num_block (low byte of d4)
    and.w   #$00FF, d3
    beq     CellEmpty                  ; num_block 0 = empty
    move.w  d4, d2
    lsr.w   #8, d2                    ; d2 = pos_block (high byte)
    and.w   #$00FF, d2

; Look up BLL block: bll[(num_block-1) * 4] = block byte offset within BLL
    subq.w  #1, d3
    move.l  HDR_BLL_OFF(a6), d0
    lea     0(a6,d0.l), a1            ; a1 = BLL section base
    move.w  d3, d0
    lsl.w   #2, d0                    ; (num_block-1) * 4
    move.l  0(a1,d0.w), d0            ; d0 = block offset (BE u32)

; cell at block + 3(header) + pos_block*4, brick_ref UWORD at offset +2 within cell
    addq.l  #3, d0                    ; skip dx, dy, dz
    moveq   #0, d1
    move.w  d2, d1                    ; d1 = pos_block zero-extended
    lsl.w   #2, d1                    ; pos_block*4
    add.l   d1, d0
    addq.l  #2, d0                    ; skip info_uword
    move.w  0(a1,d0.l), d3            ; d3 = brick_ref (BE u16, 1-based)
    beq     CellEmpty                  ; 0 = empty

; brick_local_idx = brick_ref - 1
    subq.w  #1, d3

; Lookup brick handle (a1 = brick blob start = header + pixels)
    movea.l #BRICK_HANDLES, a0
    move.w  d3, d0
    lsl.w   #2, d0                    ; idx * 4
    movea.l 0(a0,d0.w), a1            ; a1 = brick blob

    moveq   #0, d3
    move.b  BH_DX(a1), d3             ; DX
    moveq   #0, d4
    move.b  BH_DY(a1), d4             ; DY
    moveq   #0, d2
    move.b  BH_HOTX(a1), d2           ; HotX
    moveq   #0, d1
    move.b  BH_HOTY(a1), d1           ; HotY

; Compute screen iso position:
;   sx = (gx - gz) * 24 + CAM_VX + HotX
;   sy = (gx + gz) * 12 - gy * 15 + CAM_VY + HotY
    move.w  d6, d0                    ; gx
    sub.w   d7, d0                    ; gx - gz
    muls.w  #24, d0                    ; *24
    add.l   CAM_VX_VAL, d0
    add.w   d2, d0                    ; + HotX
                                       ; d0 = sx (signed)

    move.w  d6, d2                    ; gx
    add.w   d7, d2                    ; gx + gz
    muls.w  #12, d2
    move.w  d5, d4
    muls.w  #15, d4
    sub.l   d4, d2
    add.l   CAM_VY_VAL, d2
    add.l   d1, d2                    ; + HotY
                                       ; d2 = sy (signed)

; Reload DX, DY (clobbered)
    moveq   #0, d3
    move.b  BH_DX(a1), d3
    moveq   #0, d4
    move.b  BH_DY(a1), d4

; --- V1.1 rectangular clipping ---
; Reject if completely off-screen (signed comparisons).
    move.l  d0, d1
    add.l   d3, d1                    ; right_edge = sx + DX
    ble     CellEmpty                  ; right_edge <= 0 -> off left
    cmp.l   #BG_W, d0
    bge     CellEmpty                  ; sx >= 320 -> off right
    move.l  d2, d1
    add.l   d4, d1                    ; bot_edge = sy + DY
    ble     CellEmpty                  ; bot_edge <= 0 -> off top
    cmp.l   #BG_H, d2
    bge     CellEmpty                  ; sy >= 240 -> off bottom

; left_skip = max(0, -sx); clamp sx in d0 to [0..].
    moveq   #0, d1
    tst.l   d0
    bge.s   .clp_no_left
    move.l  d0, d1
    neg.l   d1                        ; d1 = -sx = left_skip
    moveq   #0, d0                    ; clamped sx = 0
.clp_no_left:
    move.l  d1, TMP_LSK

; top_skip = max(0, -sy); clamp sy in d2 to [0..].
    moveq   #0, d1
    tst.l   d2
    bge.s   .clp_no_top
    move.l  d2, d1
    neg.l   d1
    moveq   #0, d2
.clp_no_top:
    move.l  d1, TMP_TSK

; visible_w = min(BG_W, sx_orig + DX) - clamped_sx
;   sx_orig + DX = clamped_sx + DX - left_skip
    move.l  d0, d1
    add.l   d3, d1
    sub.l   TMP_LSK, d1               ; right_edge (signed)
    cmp.l   #BG_W, d1
    ble.s   .clp_w_ok
    move.l  #BG_W, d1
.clp_w_ok:
    sub.l   d0, d1                    ; visible_w
    ble     CellEmpty                  ; nothing visible horizontally

; visible_h = min(BG_H, sy_orig + DY) - clamped_sy
    move.l  d1, d3                    ; d3 = visible_w (overwrite full DX)
    move.l  d2, d1
    add.l   d4, d1
    sub.l   TMP_TSK, d1               ; bot_edge (signed)
    cmp.l   #BG_H, d1
    ble.s   .clp_h_ok
    move.l  #BG_H, d1
.clp_h_ok:
    sub.l   d2, d1                    ; visible_h
    ble     CellEmpty
    move.l  d1, d4                    ; d4 = visible_h

; --- V2.1 Blitter masked sprite blit ---
; Inputs: d0=clamped_sx, d2=clamped_sy, d3=visible_w, d4=visible_h
;         a1=brick blob start, TMP_LSK/TMP_TSK = source skip offsets
; A1=FB (dest, set once), A2=brick (src, per-blit). B_PATD=0 + DCOMPEN -> mask.

; A1_PIXEL = (clamped_sy << 16) | clamped_sx
    move.l  d2, d1
    swap    d1                        ; d1 = sy << 16 (low word zero, sy was 0..239)
    or.l    d0, d1                    ; OR sx into low word
    move.l  d1, A1_PIXEL

; STEP value: high=+1 Y, low=-visible_w X (signed 16). Same for A1 and A2.
    moveq   #0, d1
    move.w  d3, d1
    neg.w   d1                        ; low word = -visible_w (signed 16)
    or.l    #$00010000, d1            ; high word = +1
    move.l  d1, A1_STEP
    move.l  d1, A2_STEP

; A2 source = brick pixels (blob+8). FLAGS pre-built in header.
    lea     BH_PIXELS(a1), a0
    move.l  a0, A2_BASE
    move.l  BH_FLAGS(a1), A2_FLAGS

; A2_PIXEL = (top_skip << 16) | left_skip
    move.l  TMP_TSK, d1
    swap    d1                        ; top_skip in high word
    or.l    TMP_LSK, d1               ; left_skip in low word
    move.l  d1, A2_PIXEL

; B_COUNT = (visible_h << 16) | visible_w
    move.l  d4, d1
    swap    d1
    or.l    d3, d1
    move.l  d1, B_COUNT

; Fire and wait
    move.l  #B_CMD_SPRITE, B_CMD
.bl_wait:
    move.l  B_CMD, d1
    btst    #0, d1
    beq.s   .bl_wait

CellEmpty:
    addq.w  #1, d5
    cmp.w   #GRID_Y, d5
    blt     SceneYLoop
    addq.w  #1, d6
    cmp.w   GX_HI_VAL, d6
    blt     SceneXLoop
    addq.w  #1, d7
    cmp.w   GZ_HI_VAL, d7
    blt     SceneZLoop

; Final blit fence (last sprite blit may still be in flight)
.rs_final_wait:
    move.l  B_CMD, d0
    btst    #0, d0
    beq.s   .rs_final_wait
    rts

; ---------------------------------------------------------------------------
; ComputeCamera: from CAM_GX, CAM_GZ, derive CAM_VX_VAL/VY_VAL and walk bounds.
;   CAM_VX_VAL = 160 - (cam_gx - cam_gz) * 24
;   CAM_VY_VAL = 120 - (cam_gx + cam_gz) * 12 + cam_gy * 15  (cam_gy fixed = 4)
;   GX_LO_VAL = max(0, cam_gx - 8); GX_HI_VAL = min(GRID_X, cam_gx + 10)
;   GZ_LO_VAL = max(0, cam_gz - 10); GZ_HI_VAL = min(GRID_Z, cam_gz + 13)
; Asymmetric offsets match V3.1 known-good bounds for cam (8, 50).
; ---------------------------------------------------------------------------
ComputeCamera:
    move.w  CAM_GX, d0
    sub.w   CAM_GZ, d0                    ; (gx - gz) signed word
    muls.w  #24, d0                       ; (gx-gz)*24 signed long (cleans high)
    neg.l   d0                            ; -(gx-gz)*24
    add.l   #160, d0                      ; 160 - (gx-gz)*24
    move.l  d0, CAM_VX_VAL

    moveq   #0, d0
    move.w  CAM_GX, d0
    add.w   CAM_GZ, d0                    ; cam_gx + cam_gz
    mulu.w  #12, d0
    neg.l   d0
    add.l   #120 + (CAM_GY_FIXED * 15), d0
    move.l  d0, CAM_VY_VAL

; V3.4.3 walk bounds widened by +1 each side vs V3.3 baseline. Eliminates the
; "pezzettino diverso" artifact at edge re-render: cells just outside V3.3's
; tight bounds were skipped in BG_A but rendered in BG_B (different walk_gx/gz
; due to camera shift), so the user saw new cells appear at snap. With +1 the
; cells overlap in both buffers.
    move.w  CAM_GX, d0
    sub.w   #9, d0
    bge.s   .cc_gxl_ok
    moveq   #0, d0
.cc_gxl_ok:
    move.w  d0, GX_LO_VAL

    move.w  CAM_GX, d0
    add.w   #12, d0                       ; V3.4.4: +1 vs V3.4.3 (DX scroll fix)
    cmp.w   #GRID_X, d0
    blt.s   .cc_gxh_ok
    move.w  #GRID_X, d0
.cc_gxh_ok:
    move.w  d0, GX_HI_VAL

    move.w  CAM_GZ, d0
    sub.w   #12, d0                       ; V3.4.4: +1 vs V3.4.3 (DX scroll fix)
    bge.s   .cc_gzl_ok
    moveq   #0, d0
.cc_gzl_ok:
    move.w  d0, GZ_LO_VAL

    move.w  CAM_GZ, d0
    add.w   #14, d0
    cmp.w   #GRID_Z, d0
    blt.s   .cc_gzh_ok
    move.w  #GRID_Z, d0
.cc_gzh_ok:
    move.w  d0, GZ_HI_VAL
    rts

; ---------------------------------------------------------------------------
OPSetup:
; OP chain setup (same as V2.x, runs once after first RenderScene).
    move.l  #$00000004, $0FE0
    move.l  #$00000004, $0FE4
    move.l  #$00000004, $0FE8
    move.l  #$00000004, $0FEC
    move.l  #$00000001, $0FF0
    move.l  #$00000001, $0FF8
    moveq   #0, d0
    move.w  VDE_VAL, d0
    lsl.l   #3, d0
    or.l    #$FC008003, d0
    move.l  d0, $0FF4
    moveq   #0, d0
    move.w  VDB_VAL, d0
    lsl.l   #3, d0
    or.l    #$FC004003, d0
    move.l  d0, $0FFC

    move.l  #$10000002, $1000
    move.l  OP_PH0_LO_VAL, $1004
    move.l  #$00010045, $1008             ; PH1 HI: 8bpp INDEX iwidth=80 (probe)
    move.l  #$0140B000, $100C             ; PH1 LO: 8bpp+INDEX, IWIDTH=80, PITCH=1
    move.l  #$00000000, $1010
    move.l  #$00000004, $1014

    move.l  #$0FF0, d0
    swap    d0
    move.l  d0, OLP

    move.w  #0, INT2
    move.w  #$FF00, INT1
    nop
    nop
    move.w  #C_VIDENA, INT1
    move.w  #$2000, sr
    move.w  #VIDEN|BGEN|PWIDTH4|RGB16|CSYNC, VMODE
    move.w  #$0000, BG

MainLoop:
    moveq   #0, d0
    move.b  REDRAW_FLAG, d0
    beq.s   MainLoop

; Direction encoding: 1=right, 2=left, 3=down, 4=up (cell shift in iso geometry)
    cmp.b   #1, d0
    beq     .ml_right
    cmp.b   #2, d0
    beq     .ml_left
    cmp.b   #3, d0
    beq     .ml_down
    bra     .ml_up

.ml_right:                                ; cam_gx +=1, cam_gz -=1, offset_x -= 48
    move.w  CAM_GX, d1
    addq.w  #1, d1
    cmp.w   #GRID_X, d1
    bge     .ml_done
    move.w  CAM_GZ, d1
    subq.w  #1, d1
    bmi     .ml_done
    addq.w  #1, CAM_GX
    subq.w  #1, CAM_GZ
    move.w  CAM_OFFSET_X, d1
    sub.w   #48, d1
    bge.s   .ml_rt_off_ok
    moveq   #0, d1
.ml_rt_off_ok:
    move.w  d1, NEXT_OFFSET_X
    move.w  CAM_OFFSET_Y, d1
    move.w  d1, NEXT_OFFSET_Y
    bra     .ml_render

.ml_left:                                 ; cam_gx -=1, cam_gz +=1, offset_x += 48
    move.w  CAM_GX, d1
    subq.w  #1, d1
    bmi     .ml_done
    move.w  CAM_GZ, d1
    addq.w  #1, d1
    cmp.w   #GRID_Z, d1
    bge     .ml_done
    subq.w  #1, CAM_GX
    addq.w  #1, CAM_GZ
    move.w  CAM_OFFSET_X, d1
    add.w   #48, d1
    cmp.w   #SCROLL_W_MAX, d1
    ble.s   .ml_lf_off_ok
    move.w  #SCROLL_W_MAX, d1
.ml_lf_off_ok:
    move.w  d1, NEXT_OFFSET_X
    move.w  CAM_OFFSET_Y, d1
    move.w  d1, NEXT_OFFSET_Y
    bra     .ml_render

.ml_down:                                 ; cam_gx +=1, cam_gz +=1, offset_y -= 24
    move.w  CAM_GX, d1
    addq.w  #1, d1
    cmp.w   #GRID_X, d1
    bge     .ml_done
    move.w  CAM_GZ, d1
    addq.w  #1, d1
    cmp.w   #GRID_Z, d1
    bge     .ml_done
    addq.w  #1, CAM_GX
    addq.w  #1, CAM_GZ
    move.w  CAM_OFFSET_Y, d1
    sub.w   #24, d1
    bge.s   .ml_dn_off_ok
    moveq   #0, d1
.ml_dn_off_ok:
    move.w  d1, NEXT_OFFSET_Y
    move.w  CAM_OFFSET_X, d1
    move.w  d1, NEXT_OFFSET_X
    bra.s   .ml_render

.ml_up:                                   ; cam_gx -=1, cam_gz -=1, offset_y += 24
    move.w  CAM_GX, d1
    subq.w  #1, d1
    bmi.s   .ml_done
    move.w  CAM_GZ, d1
    subq.w  #1, d1
    bmi.s   .ml_done
    subq.w  #1, CAM_GX
    subq.w  #1, CAM_GZ
    move.w  CAM_OFFSET_Y, d1
    add.w   #24, d1
    cmp.w   #SCROLL_H_MAX, d1
    ble.s   .ml_up_off_ok
    move.w  #SCROLL_H_MAX, d1
.ml_up_off_ok:
    move.w  d1, NEXT_OFFSET_Y
    move.w  CAM_OFFSET_X, d1
    move.w  d1, NEXT_OFFSET_X

.ml_render:
    bsr     ComputeCamera
    bsr     RenderScene
; V3.4.2 coherent atomic swap: in a single int-disabled block, flip the buffer
; AND commit the new offsets. Prevents the "whiplash" where ViHandler showed
; the OLD buffer with the NEW offset during the ~150ms render window.
    move.w  #$2700, sr                ; mask interrupts
    move.b  ACTIVE_BG_ID, d0
    not.b   d0
    andi.b  #1, d0
    move.b  d0, ACTIVE_BG_ID
    move.w  NEXT_OFFSET_X, d0
    move.w  d0, CAM_OFFSET_X
    move.w  NEXT_OFFSET_Y, d0
    move.w  d0, CAM_OFFSET_Y
    move.w  #$2000, sr                ; unmask interrupts
.ml_done:
    move.b  #0, REDRAW_FLAG
    bra     MainLoop

BadCart:
    move.l  #$F8000000, BORD1
.bc_loop:
    bra.s   .bc_loop

; ---------------------------------------------------------------------------
; DecompCol: decompress one column from cart RLE to flat cube column.
; Input: a2 = column data ptr, a4 = dest cube column ptr (25 UWORDs).
; Format per entity: opcode byte (mode in bits 7-6, count-1 in bits 5-0),
;   followed by 0/1/N UWORDs depending on mode.
; mode 00: zero fill count cells
; mode 01 or 11: copy count UWORDs
; mode 10: repeat 1 UWORD count times
; Clobbers d0/d1/d2/d3, advances a2 and a4.
; ---------------------------------------------------------------------------
DecompCol:
    moveq   #0, d0
    move.b  (a2)+, d0                 ; nb_entity
    subq.w  #1, d0
    bmi.s   .dc_done
.dc_entity:
    move.b  (a2)+, d1                 ; opcode
    move.w  d1, d2
    and.w   #$3F, d2                  ; count - 1
    addq.w  #1, d2                    ; count
    move.w  d1, d3
    and.b   #$C0, d3
    beq.s   .dc_zero
    cmp.b   #$80, d3
    beq.s   .dc_same
.dc_diff:
    subq.w  #1, d2
.dc_diff_loop:
    move.w  (a2)+, (a4)+
    dbra    d2, .dc_diff_loop
    bra.s   .dc_next
.dc_same:
    move.w  (a2)+, d3
    subq.w  #1, d2
.dc_same_loop:
    move.w  d3, (a4)+
    dbra    d2, .dc_same_loop
    bra.s   .dc_next
.dc_zero:
    moveq   #0, d3
    subq.w  #1, d2
.dc_zero_loop:
    move.w  d3, (a4)+
    dbra    d2, .dc_zero_loop
.dc_next:
    dbra    d0, .dc_entity
.dc_done:
    rts

; ---------------------------------------------------------------------------
; ViHandler (V3.2): poll D-pad, update CAM_OFFSET_X/Y, recompute OP data ptr,
; refresh OP fields each VBL (HEIGHT decay quirk).
; D-pad strobe pattern from silicon_relics NOTES.md (hw-confirmed PAL).
; ---------------------------------------------------------------------------
ViHandler:
    movem.l d0/d1, -(sp)

; Skip joypad if redraw pending (atomicity vs MainLoop)
    tst.b   REDRAW_FLAG
    bne     .vh_op_refresh

; Read D-pad row (strobe $F1FE, bits[11:8] = R/L/D/U active-low)
    move.w  #$F1FE, JOYSTICK
    nop
    nop
    nop
    nop
    move.w  JOYSTICK, d1

; UP (bit 8): if at edge -> trigger up shift, else decrement
    btst    #8, d1
    bne.s   .vh_no_up
    move.w  CAM_OFFSET_Y, d0
    bne.s   .vh_up_normal
    move.b  #4, REDRAW_FLAG
    bra     .vh_op_refresh
.vh_up_normal:
    subq.w  #1, d0
    move.w  d0, CAM_OFFSET_Y
.vh_no_up:
; DOWN (bit 9): if at edge -> trigger down shift, else increment
    btst    #9, d1
    bne.s   .vh_no_dn
    move.w  CAM_OFFSET_Y, d0
    cmp.w   #SCROLL_H_MAX, d0
    blt.s   .vh_dn_normal
    move.b  #3, REDRAW_FLAG
    bra     .vh_op_refresh
.vh_dn_normal:
    addq.w  #1, d0
    move.w  d0, CAM_OFFSET_Y
.vh_no_dn:
; LEFT (bit 10): if at edge -> trigger left shift, else decrement
    btst    #10, d1
    bne.s   .vh_no_lf
    move.w  CAM_OFFSET_X, d0
    bne.s   .vh_lf_normal
    move.b  #2, REDRAW_FLAG
    bra.s   .vh_op_refresh
.vh_lf_normal:
    subq.w  #8, d0
    bge.s   .vh_lf_ok
    moveq   #0, d0
.vh_lf_ok:
    move.w  d0, CAM_OFFSET_X
.vh_no_lf:
; RIGHT (bit 11): if at edge -> trigger right shift, else increment
    btst    #11, d1
    bne.s   .vh_no_rt
    move.w  CAM_OFFSET_X, d0
    cmp.w   #SCROLL_W_MAX, d0
    blt.s   .vh_rt_normal
    move.b  #1, REDRAW_FLAG
    bra.s   .vh_op_refresh
.vh_rt_normal:
    addq.w  #8, d0
    move.w  d0, CAM_OFFSET_X
.vh_no_rt:

.vh_op_refresh:

; V3.4.1 select active BG base. ACTIVE_BG_ID = 0 -> BG_A, 1 -> BG_B.
; Compute new data ptr = active_bg + offset_y*640 + offset_x  (8bpp = 1 B/pixel)
    moveq   #0, d0
    move.w  CAM_OFFSET_Y, d0
    mulu.w  #640, d0
    moveq   #0, d1
    move.w  CAM_OFFSET_X, d1
    add.l   d1, d0
    tst.b   ACTIVE_BG_ID
    bne.s   .vh_use_b
    add.l   #BG_A, d0
    bra.s   .vh_bg_done
.vh_use_b:
    add.l   #BG_B, d0
.vh_bg_done:

; Encode in PH0 HI: bits 11-31 = data >> 3 (= data << 8 since data low 3 bits = 0).
; OR with LINK low byte = $002 (next obj at $1010).
    lsl.l   #8, d0
    or.l    #$00000002, d0

; Refresh OP each VBL
    move.l  d0, $1000                 ; PH0 HI: scrolled data ptr + LINK
    move.l  OP_PH0_LO_VAL, $1004
    move.l  #$00010045, $1008
    move.l  #$0140B000, $100C
    move.w  #C_VIDENA, INT1
    movem.l (sp)+, d0/d1
IrqRte:
    rte

; ---------------------------------------------------------------------------
; PadDxLut: indexed by DX (0..64), each entry = padded_DX as word.
; PadFlagsLut: indexed by DX (0..64), each entry = full A2_FLAGS long
;   (WID encoding | PIXEL16 | XADDPIX | PITCH1).
; LBA scene 0: max DX = 48; covers up to 64 with margin.
; ---------------------------------------------------------------------------
PadDxLut:
    dc.w  2,2,2,4,4,6,6,8,8,10,10,12,12,14,14,16,16
    dc.w  20,20,20,20,24,24,24,24,28,28,28,28,32,32,32,32
    dc.w  40,40,40,40,40,40,40,40,48,48,48,48,48,48,48,48
    dc.w  56,56,56,56,56,56,56,56,64,64,64,64,64,64,64,64

PadFlagsLut:
    dc.l  $00010818,$00010818,$00010818                  ; dx 0..2  WID2
    dc.l  $00011018,$00011018                            ; dx 3..4  WID4
    dc.l  $00011418,$00011418                            ; dx 5..6  WID6
    dc.l  $00011818,$00011818                            ; dx 7..8  WID8
    dc.l  $00011A18,$00011A18                            ; dx 9..10 WID10
    dc.l  $00011C18,$00011C18                            ; dx 11..12 WID12
    dc.l  $00011E18,$00011E18                            ; dx 13..14 WID14
    dc.l  $00012018,$00012018                            ; dx 15..16 WID16
    dc.l  $00012218,$00012218,$00012218,$00012218        ; dx 17..20 WID20
    dc.l  $00012418,$00012418,$00012418,$00012418        ; dx 21..24 WID24
    dc.l  $00012618,$00012618,$00012618,$00012618        ; dx 25..28 WID28
    dc.l  $00012818,$00012818,$00012818,$00012818        ; dx 29..32 WID32
    dc.l  $00012A18,$00012A18,$00012A18,$00012A18        ; dx 33..36 WID40
    dc.l  $00012A18,$00012A18,$00012A18,$00012A18        ; dx 37..40 WID40
    dc.l  $00012C18,$00012C18,$00012C18,$00012C18        ; dx 41..44 WID48
    dc.l  $00012C18,$00012C18,$00012C18,$00012C18        ; dx 45..48 WID48
    dc.l  $00012E18,$00012E18,$00012E18,$00012E18        ; dx 49..52 WID56
    dc.l  $00012E18,$00012E18,$00012E18,$00012E18        ; dx 53..56 WID56
    dc.l  $00013018,$00013018,$00013018,$00013018        ; dx 57..60 WID64
    dc.l  $00013018,$00013018,$00013018,$00013018        ; dx 61..64 WID64

; ---------------------------------------------------------------------------
    .phrase
SceneData:
    .incbin "cart/scene_000.bin"
SceneEnd:

    END
