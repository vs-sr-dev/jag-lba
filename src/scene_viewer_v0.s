;
; JagLBA scene viewer V0
; ----------------------
; Loads a packed scene cart binary (scene_000.bin produced by tools/cart_pack_scene.py)
; and displays a single brick at the screen center to validate the cart pipeline:
; HQR -> cart binary -> Jaguar palette upload -> per-pixel RGB16 conversion -> blit -> OP display.
;
; No Blitter, no masking, no grid walk. Manual CPU 8bpp -> RGB16 conversion + dump.
; If V0 displays brick local-idx 0 correctly, V1 will add Blitter and grid walk.
;
; Target: BigPEmu primario, GameDrive bonus.
;

    .68000
    .include "jaguar.inc"

    .org    $802000

; ---------------------------------------------------------------------------
; DRAM layout (variables and buffers)
; ---------------------------------------------------------------------------
PALETTE_DRAM    EQU     $2000
BRICK_RGB_BUF   EQU     $20000
FB_A            EQU     $100000

VDB_VAL         EQU     $2200
VDE_VAL         EQU     $2202
OP_PH0_LO_VAL   EQU     $2204

; Cart binary header field offsets (must match tools/cart_pack_scene.py)
HDR_VERSION     EQU     8
HDR_SCENE_ID    EQU     12
HDR_PAL_OFF     EQU     16
HDR_PAL_SIZE    EQU     20
HDR_GRID_OFF    EQU     24
HDR_GRID_SIZE   EQU     28
HDR_BLL_OFF     EQU     32
HDR_BLL_SIZE    EQU     36
HDR_BLL_COUNT   EQU     40
HDR_BDIR_OFF    EQU     44
HDR_BDIR_COUNT  EQU     48
HDR_BPIX_OFF    EQU     52
HDR_BPIX_SIZE   EQU     56
HDR_FILE_SIZE   EQU     60

; Brick directory entry layout (8 bytes per brick)
BDIR_PIX_OFF    EQU     0
BDIR_DX         EQU     4
BDIR_DY         EQU     5
BDIR_HOTX       EQU     6
BDIR_HOTY       EQU     7
BDIR_STRIDE     EQU     8

; ---------------------------------------------------------------------------
Start:
    move.l  #$00200000, a7
    move.w  #$2700, sr

; --- Install defensive RTE handlers + ViHandler at $100 ---
    move.l  #ViHandler, $100
    move.l  #IrqRte, $64
    move.l  #IrqRte, $68
    move.l  #IrqRte, $6C
    move.l  #IrqRte, $70
    move.l  #IrqRte, $74
    move.l  #IrqRte, $78
    move.l  #IrqRte, $7C

; --- NTSC/PAL detect (bit 4 of CONFIG = JOYBUTS+2) ---
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

; --- Pre-compute FB obj ph0_lo with correct YPOS = VDB ---
; ph0_lo = LINK_LO=$02 (STOP at $1010) | HEIGHT:240 | YPOS:VDB | TYPE:BITOBJ
    moveq   #0, d3
    move.w  d5, d3
    and.l   #$7FF, d3
    lsl.l   #3, d3
    or.l    #$023C0000, d3
    move.l  d3, OP_PH0_LO_VAL

; --- Halt GPU + endianness ---
    move.l  #0, G_CTRL
    move.l  #$00070007, G_END

; --- Clear framebuffer (320x240x2 = 153600 bytes / 4 = 38400 longwords) ---
    movea.l #FB_A, a0
    move.w  #$95FF, d0
ClearFB:
    clr.l   (a0)+
    dbra    d0, ClearFB

; --- Locate cart scene binary + sanity-check magic ---
    lea     SceneData, a6
    cmpi.l  #$4A41474C, (a6)
    bne     BadCart
    cmpi.l  #$42413031, 4(a6)
    bne     BadCart

; --- Copy palette from cart to DRAM (256 RGB16 BE words = 512 bytes) ---
; cart palette is already Jaguar RGB16 BE, so straight copy.
    move.l  HDR_PAL_OFF(a6), d0
    lea     0(a6,d0.l), a0
    movea.l #PALETTE_DRAM, a1
    move.w  #127, d0
CopyPal:
    move.l  (a0)+, (a1)+
    dbra    d0, CopyPal

; --- Pre-convert brick local-idx 0 from 8bpp paletted to RGB16 in DRAM ---
; Read BDIR entry 0: pixel_offset (4B BE), dx, dy, hot_x, hot_y (1B each)
    move.l  HDR_BDIR_OFF(a6), d0
    lea     0(a6,d0.l), a2
    move.l  BDIR_PIX_OFF(a2), d4
    moveq   #0, d5
    move.b  BDIR_DX(a2), d5
    moveq   #0, d6
    move.b  BDIR_DY(a2), d6

; Source pointer = bpix_off + pix_off + cart_base
    move.l  HDR_BPIX_OFF(a6), d0
    add.l   d4, d0
    lea     0(a6,d0.l), a3
; Dest pointer = BRICK_RGB_BUF
    movea.l #BRICK_RGB_BUF, a4
; Palette base
    movea.l #PALETTE_DRAM, a5

    move.w  d6, d7
    subq.w  #1, d7
ConvRow:
    move.w  d5, d2
    subq.w  #1, d2
ConvCol:
    moveq   #0, d0
    move.b  (a3)+, d0
    add.w   d0, d0
    move.w  0(a5,d0.w), d1
    move.w  d1, (a4)+
    dbra    d2, ConvCol
    dbra    d7, ConvRow

; --- Manual blit pre-converted brick to FB center ---
; Center: screen 320x240, brick DX*DY, top-left = ((320-DX)/2, (240-DY)/2)
; FB stride = 640 bytes per row. moveq immediate is signed byte (-128..127),
; so use move.l for values > 127. XPOS=14 quirk compensated via 160-14=146 X.
    move.l  #160, d0
    lsr.w   #1, d5
    sub.w   d5, d0
    add.w   d5, d5
    move.l  #120, d1
    lsr.w   #1, d6
    sub.w   d6, d1
    add.w   d6, d6
    mulu.w  #640, d1
    add.w   d0, d0
    add.l   d0, d1
    movea.l #FB_A, a0
    adda.l  d1, a0

    movea.l #BRICK_RGB_BUF, a1
    move.w  d6, d7
    subq.w  #1, d7
BlitRow:
    movea.l a0, a2
    move.w  d5, d2
    lsr.w   #1, d2
    subq.w  #1, d2
BlitCol:
    move.l  (a1)+, (a2)+
    dbra    d2, BlitCol
    adda.w  #640, a0
    dbra    d7, BlitRow

; --- Setup OP chain (Cybermorph pattern, BigPEmu compat) ---
; STOP at $0FE0..$0FEC (4x $00000004)
    move.l  #$00000004, $0FE0
    move.l  #$00000004, $0FE4
    move.l  #$00000004, $0FE8
    move.l  #$00000004, $0FEC
; BRANCH ph_hi = link_hi = $0FE0>>11 = 1
    move.l  #$00000001, $0FF0
    move.l  #$00000001, $0FF8
; BRANCH 1 ph_lo: BR_YPOSLT + VDE
    moveq   #0, d0
    move.w  VDE_VAL, d0
    lsl.l   #3, d0
    or.l    #$FC008003, d0
    move.l  d0, $0FF4
; BRANCH 2 ph_lo: BR_YPOSGT + VDB
    moveq   #0, d0
    move.w  VDB_VAL, d0
    lsl.l   #3, d0
    or.l    #$FC004003, d0
    move.l  d0, $0FFC

; --- FB bitmap object at $1000 (data=FB_A, link=STOP at $1010) ---
; ph0_hi: data[23:11]=$10000000, link_hi=$02
    move.l  #$10000002, $1000
; ph0_lo: pre-computed with VDB-derived YPOS
    move.l  OP_PH0_LO_VAL, $1004
; ph1_hi: RELEASE | NOTRANS | dwidth=80
    move.l  #$00010005, $1008
; ph1_lo: iwidth=80, depth=4 (16bpp), pitch=1, xpos=14
    move.l  #$0140C00E, $100C
; STOP at $1010
    move.l  #$00000000, $1010
    move.l  #$00000004, $1014

; --- OLP = $0FF0 (BRANCH chain entry) ---
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

; --- Idle forever ---
MainLoop:
    bra.s   MainLoop

; --- Bad cart magic: red border ---
BadCart:
    move.l  #$F8000000, BORD1
.bc_loop:
    bra.s   .bc_loop

; --- VI handler: re-install bitmap obj each VBL.
; Quirk: OP decrements HEIGHT and advances DATA each scanline rendered. Without
; refreshing the obj at $1000 every frame, after frame 1 HEIGHT=0 and the bitmap
; stops rendering. silicon_relics handles this via staging slots; for V0 we just
; rewrite all 4 longwords each VBL.
ViHandler:
    movem.l d0/d1, -(sp)
    move.l  #$10000002, $1000
    move.l  OP_PH0_LO_VAL, $1004
    move.l  #$00010005, $1008
    move.l  #$0140C00E, $100C
    move.w  #C_VIDENA, INT1
    movem.l (sp)+, d0/d1
IrqRte:
    rte

; ---------------------------------------------------------------------------
; Cart scene binary
; ---------------------------------------------------------------------------
    .phrase
SceneData:
    .incbin "cart/scene_000.bin"
SceneEnd:

    END
