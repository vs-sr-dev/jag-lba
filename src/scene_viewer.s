;
; JagLBA scene viewer V4.1
; ------------------------
; Multi-scene cart with JagGD bank-switching layout (cart V4 format JAGLBA02).
; Single 6 MB unbanked cart (~5.79 MB used). All 134 scenes packed; bricks stored
; as RLE (HQR-decompressed format), decoded to 8bpp working buffer per scene at
; LoadScene() time. Bank switching deferred to V5 when total exceeds 6 MB.
;
; V4.1 (S7 Step D): Scene transition logic — D-pad at grid edge consults
; scene_table.tx_<dir>; if valid (!=$FF) sets NEXT_SCENE_ID and drives
; .ml_transition (LoadScene + render + atomic swap).
;
; Boot flow: video timing -> magic check -> CLUT upload ->
; LoadScene(0) -> ComputeCamera -> RenderScene -> OPSetup -> MainLoop.
;

    .68000
    .include "jaguar.inc"

    .org    $802000

; ---------------------------------------------------------------------------
; DRAM layout V4
; ---------------------------------------------------------------------------
PALETTE_DRAM    EQU     $2000           ; 256 RGB16 entries (kept for ref)
VDB_VAL         EQU     $2200
VDE_VAL         EQU     $2202
OP_PH0_LO_VAL   EQU     $2204

; Clip scratch (per-cell during clipping math)
TMP_LSK         EQU     $2208           ; long
TMP_TSK         EQU     $220C           ; long

; V3.2 camera scroll state (updated by ViHandler from joypad)
CAM_OFFSET_X    EQU     $2218           ; word
CAM_OFFSET_Y    EQU     $221A           ; word
SCROLL_W_MAX    EQU     320
SCROLL_H_MAX    EQU     352

; V3.3 dynamic camera state
CAM_GX          EQU     $221C           ; word
CAM_GZ          EQU     $221E           ; word
CAM_VX_VAL      EQU     $2220           ; long
CAM_VY_VAL      EQU     $2224           ; long
GX_LO_VAL       EQU     $2228           ; word
GX_HI_VAL       EQU     $222A           ; word
GZ_LO_VAL       EQU     $222C           ; word
GZ_HI_VAL       EQU     $222E           ; word
REDRAW_FLAG     EQU     $2230           ; byte
ACTIVE_BG_ID    EQU     $2231           ; byte
NEXT_OFFSET_X   EQU     $2232           ; word
NEXT_OFFSET_Y   EQU     $2234           ; word

; V4 scene state
CUR_SCENE_ID    EQU     $2236           ; word: current scene index
NEXT_SCENE_ID   EQU     $2238           ; word: scene to load on transition
CUR_BLL_BASE    EQU     $223C           ; long: cart-absolute ptr to current BLL payload
CUR_GRID_BASE   EQU     $2240           ; long: cart-absolute ptr to current grid payload

; LoadScene Stage 4 scratch (saved across LUT lookups + row loop)
LS_BRICK_IDX    EQU     $2244           ; word: outer brick_idx
LS_BRICK_CNT    EQU     $2246           ; word: dbra counter
LS_PAD_COUNT    EQU     $2248           ; word: row padding count (padded_DX - DX)
LS_BLOB_OFF     EQU     $224A           ; long: brick blob offset

; V5 body state (cached pointers + counts for body 0 / Twinsen)
BODY_NB_VTX     EQU     $224E           ; word
BODY_NB_POLY    EQU     $2250           ; word
BODY_VTX_PTR    EQU     $2254           ; long: cart ptr to body 0 vertex array
BODY_POLY_PTR   EQU     $2258           ; long: cart ptr to body 0 poly array
; S15 F4.5: sphere section (cached at boot from Body0Spheres header)
BODY_NB_SPHERE  EQU     $226C           ; word
BODY_SPHERE_PTR EQU     $2270           ; long: cart ptr to first sphere descriptor (post-header)
; S16 Path A Phase 1: parametric body anchor (signed screen coords)
BODY_SX0        EQU     $2274           ; word: body anchor screen X
BODY_SY0        EQU     $2276           ; word: body anchor screen Y
; S22B: deferred atomic swap flag (set by MainLoop, committed by ViHandler at field 0)
PENDING_SWAP    EQU     $2278           ; byte

; S23 sprite-based body via OP layered BMobj.
; Body BMobj phrase 0/1 LO templates (TYPE+HEIGHT+LINK fixed at boot;
; YPOS at PH0 LO and XPOS at PH1 LO updated runtime by ViHandler).
BODY_PH0_LO_VAL EQU     $2280           ; long: PH0 LO scratch (TYPE+YPOS+HEIGHT+LINK lo)
BODY_PH1_LO_VAL EQU     $2284           ; long: PH1 LO scratch (XPOS+DEPTH+PITCH+DWIDTH+IWIDTH lo)
BODY_DRAW_TARGET EQU    $2288           ; long: 0 = use ACTIVE_BG_ID, !=0 = direct FB base addr (= BG_PRISTINE during InitBodyBitmap)
BODY_ANCHOR_SAVE_X EQU  $228C           ; word: BODY_SX0 backup during InitBodyBitmap
BODY_ANCHOR_SAVE_Y EQU  $228E           ; word: BODY_SY0 backup during InitBodyBitmap

; S24 PoC C blob (m68k-elf-gcc via Docker marsdev-32x)
; ATTENZIONE alla scelta degli indirizzi scratch: la mappa DRAM bassa NON e'
; tutta in questo file. src/gpu_raster.s dichiara la regione argomenti GPU
; $2280-$23BF (GPU_ARG_OUT_COUNT = $2290!) piu' $24C0-$260B. La prima stesura
; S24 metteva VBL_TICK a $2290 = GPU_ARG_OUT_COUNT -> boot in hang.
; $260C-$26FF e' l'unico buco libero verificato in tutti e tre i sorgenti,
; fra la fine delle var GPU e POLY_SORT_BUF ($2700).
VBL_TICK        EQU     $2610           ; long: ViHandler field counter
LAST_TICK       EQU     $2614           ; long: last tick consumed by MainLoop
C_BLOB_OK       EQU     $2618           ; byte: 1 solo se poc_init ha dato il magic giusto
; S24 FIX: $1E0000 era DENTRO la griglia. CUBE_DRAM = (gz*64+gx)*50 byte
; = 64*64*50 = $32000, quindi occupa $1C0000..$1F2000. Slot libero reale =
; $1F2000..$1F4000 (8 KB, fra fine griglia e VERT_LIST).
; NOTA: 8 KB bastano per il PoC ma NON per l'engine vero -> per engine_src/
; si esegue in place dal cart ROM, con solo .data/.bss in DRAM.
C_BLOB_BASE     EQU     $1F2000         ; DRAM slot: CUBE_DRAM end ($1F2000) .. VERT_LIST ($1F4000)
C_BLOB_LIMIT    EQU     $2000           ; 8 KB budget disponibile (non asserito a build time)
C_POC_INIT      EQU     $1F2000         ; blob+0: poc_init() -> d0 = $900DF00D
C_POC_FRAME     EQU     $1F2006         ; blob+6: poc_frame(fb) -> d0 = frame_count

; S13 build 4: z-sort painter (one-time boot, body T-pose static + iso fixed)
; 224 entries x 4 byte = 896 byte; each entry = (key WORD signed, idx WORD)
POLY_SORT_BUF   EQU     $2700

; Per-vertex screen-projected coords (sx, sy) WORD pairs, max 1024 vertices
VERT_LIST       EQU     $1F4000

; V5 V0 debug body dot color (palette idx — kept for reference fallback)
BODY_DOT_COLOR  EQU     200

; V5 V0.5 wireframe colors per material bucket (palette idx)
WIRE_PATTERN_COLOR  EQU     100         ; mat 0-6 (TRISTE..TRAME)
WIRE_FLAT_COLOR     EQU     150         ; mat 7-8 (FLAT, GRANIT)
WIRE_GOURAUD_COLOR  EQU     200         ; mat 9-10 (GOURAUD, DITHER)

; Bresenham line drawer scratch (DrawLine)
BR_DX               EQU     $225C       ; long: |dx|
BR_DY_NEG           EQU     $2260       ; long: -|dy|
BR_ERR              EQU     $2264       ; long
BR_SX               EQU     $2268       ; word: ±1
BR_SY               EQU     $226A       ; word: ±1

EDGE_MARGIN     EQU     16
CAM_GY_FIXED    EQU     4

; S18 A1: bound per display 640x480 interlace (SVGA parity).
BODY_MIN_X      EQU     32
BODY_MAX_X      EQU     608
BODY_MIN_Y      EQU     32
BODY_MAX_Y      EQU     448

; V4 buffers
GDBIOS_BUFFER   EQU     $3000           ; 4 KB long-aligned (GD_Install target)
BRICK_HANDLES   EQU     $4000           ; 8619 longs (~34 KB), indexed by global brick_idx
BRICK_USED_BMP  EQU     $D000           ; 1078 bytes (8619 bits) brick_used bitmap
BRICK_USED_SIZE EQU     1080            ; rounded to long (8624 bits)

; V3.4.0 BG layout (8bpp + CLUT, double buffer)
BG_A            EQU     $100000
BG_B            EQU     $160000
; S16 Path A Phase 2.5: pristine cache (scene rendered ONCE per scene-load).
; Used for Blitter copy on body-redraw (no full RenderScene per VBL).
; Free zone $A0000-$0FFFFF (BRICK_BLOBS_WORKING ends at $A0000, BG_A at $100000).
BG_PRISTINE     EQU     $A0000
BG_W            EQU     640
BG_H            EQU     592
CUBE_DRAM       EQU     $1C0000

; S23 sprite-based body bitmap region.
; 32 byte/row x 80 rows = 2.5 KB sprite. IWIDTH=4 phrases, BMobj HEIGHT raw=40.
; DRAM slot $1F5000 = post-VERT_LIST (4 KB at $1F4000), pre-stack (=$200000).
; Body T-pose pre-rendered at boot via InitBodyBitmap with anchor (CX=16, CY=74).
; HEIGHT=40 was empirically dialed in via "brutal test halving" (S23 iter 5): with full
; HEIGHT=80 the bottom of the sprite showed a black rectangle (TRANS bit empirically not
; transparent for idx 0); halving HEIGHT to 40 eliminated the rect AND body still looks
; complete on screen (= body 1 actual visible extent matches HEIGHT=40 mapping in interlace
; 480i, exact mechanism unclear vs naive bbox math).
BODY_BITMAP     EQU     $1F5000
BODY_BITMAP_W   EQU     32
BODY_BITMAP_H   EQU     80
; Body bitmap-relative anchor: where body model Y=0 (= feet) lands inside the bitmap.
; CY=74 = near-bottom of 80-row bitmap (body extends -74..+5 from anchor = full bitmap).
BODY_SPRITE_CX  EQU     16
BODY_SPRITE_CY  EQU     74

; Working buffer for per-scene decoded 8bpp bricks (max-case 587 KB observed
; across 134 scenes; reserve 600 KB phrase-aligned).
BRICK_BLOBS_WORKING EQU $10000          ; $10000-$A0000 = 576 KB available

; ---------------------------------------------------------------------------
; Cart V5 (JAGLBA03) header field offsets
; ---------------------------------------------------------------------------
HDR_SCENE_COUNT     EQU     12          ; word
HDR_BRICK_COUNT     EQU     14          ; word
HDR_PAL_OFF         EQU     16          ; long
HDR_SCENE_TBL_OFF   EQU     20          ; long
HDR_BDIR_OFF        EQU     24          ; long
HDR_BBLOB_OFF       EQU     28          ; long
HDR_BBLOB_SIZE      EQU     32          ; long
HDR_BLL_TBL_OFF     EQU     36          ; long
HDR_BODY_COUNT      EQU     42          ; word (V5)
HDR_BLL_BLOB_OFF    EQU     44          ; long
HDR_GRID_TBL_OFF    EQU     52          ; long
HDR_GRID_BLOB_OFF   EQU     56          ; long
HDR_BODY_TBL_OFF    EQU     64          ; long (V5)
HDR_BODY_BLOB_OFF   EQU     68          ; long (V5)

; Scene table entry (16 B per scene)
TBL_BANK_IDX        EQU     0           ; byte: V4 always 0
TBL_BLL_IDX         EQU     1           ; byte: index into bll_table (0..13)
TBL_PAL_IDX         EQU     2           ; byte: V4 always 0 (single palette)
TBL_GX_INIT         EQU     4           ; byte: starting cam_gx
TBL_GZ_INIT         EQU     5           ; byte: starting cam_gz
TBL_TX_RIGHT        EQU     8           ; byte: scene_idx for east transition, $FF=none
TBL_TX_LEFT         EQU     9           ; byte
TBL_TX_DOWN         EQU     10          ; byte
TBL_TX_UP           EQU     11          ; byte
TBL_ENTRY_SIZE      EQU     16

; Brick directory entry (8 B)
BDR_OFF             EQU     0           ; long: offset in brick_blob to RLE payload
BDR_DX              EQU     4           ; byte
BDR_DY              EQU     5           ; byte
BDR_HOTX            EQU     6           ; byte
BDR_HOTY            EQU     7           ; byte
BDR_ENTRY_SIZE      EQU     8

; ---------------------------------------------------------------------------
; Brick blob layout (working buffer, post-decode 8bpp)
; ---------------------------------------------------------------------------
BH_FLAGS        EQU     0               ; long: A2_FLAGS pre-built (WID|PIXEL8|XADDPIX|PITCH1)
BH_DX           EQU     4               ; byte
BH_DY           EQU     5               ; byte
BH_HOTX         EQU     6               ; byte signed
BH_HOTY         EQU     7               ; byte signed
BH_PIXELS       EQU     8

B_CMD_SPRITE    EQU     $09800601
B_CMD_OPAQUE    EQU     $01800601                ; S22B SRCEN UPDA1 UPDA2 LFU_S no DCOMPEN no transparency

GRID_X          EQU     64
GRID_Y          EQU     25
GRID_Z          EQU     64

; ---------------------------------------------------------------------------
; V4 single-bank: cart fits in 6 MB unbanked, all data visible in default
; ROMSetPages(0x543210) mapping. No bank-switch macro yet.
; V5 expansion will add macro: .macro BANK_SET page,bank ... jsr _GD_ROMSetPage
; ---------------------------------------------------------------------------
Start:
; 64BitDemo-style entry: mask interrupts FIRST, then setup stack.
; Note: this matches the rB+/JagStudio-built carts that boot reliably on
; BigPEmu via the $F6 fastboot stub.
    move.w  #$2700, sr
    move.l  #$00200000, a7

    moveq   #0, d0
    move.w  d0, CAM_OFFSET_X
    move.w  d0, CAM_OFFSET_Y

; S24 DIAG: riempi TUTTA la tabella vettori ($8-$3FF) con una sonda che
; accende MAGENTA e si blocca. Qualunque eccezione o interrupt che finisce
; in un vettore diverso da $100 (illegal, address error, autovector non
; previsto) diventa cosi' un colore invece che un blocco muto.
; Gli install specifici sotto sovrascrivono i vettori che ci servono davvero.
; La tabella e' divisa in 4 fasce, ognuna con una sonda che lampeggia un
; numero diverso di volte (poi pausa lunga, poi ripete). Contando i lampi
; si sa DI CHE NATURA e' il problema senza dipendere dai colori.
;   1 lampo  = $08-$2F  eccezione CPU (bus error, address error, illegal,
;                        divide-by-zero, CHK, TRAPV, privilege, trace, line-A/F)
;   2 lampi  = $30-$5F  vettori riservati
;   3 lampi  = $60-$7F  spurious ($60) o autovettore livello 1-7 ($64-$7C)
;   4 lampi  = $80-$3FF vettore utente diverso da $100
; Il test precedente ha dato UN lampo = fascia $08-$2F = eccezione CPU vera.
; Ora ogni singolo vettore di eccezione ha la sua sonda, numerata per
; probabilita' decrescente. Tutto il resto della tabella -> 20 lampi
; ("tanti"), che significherebbe che il colpevole e' cambiato.
    movea.l #$08, a0
    move.w  #253, d0                      ; ($400-$08)/4 - 1
.vt_fill:
    move.l  #ProbeOther, (a0)+
    dbra    d0, .vt_fill

; L'eccezione e' confermata Address Error, quindi ora SOLO $0C ha una sonda
; dedicata, che usa i lampi per dire la ZONA (1-5). Tutti gli altri vettori
; restano su ProbeOther = 15 lampi, cioe' "non e' piu' Address Error,
; ricontrolla". Cosi' i conteggi 1-5 hanno un significato solo.
    move.l  #ProbeAddrErr, $0C

    move.l  #ViHandler, $100
; S24 DIAG: i 7 autovector NON vengono piu' puntati a IrqRte (che e' un `rte`
; nudo, indistinguibile da "non e' successo niente"). Restano sulla sonda.
;    move.l  #IrqRte, $64               ; S24 DIAG OFF
;    move.l  #IrqRte, $68               ; S24 DIAG OFF
;    move.l  #IrqRte, $6C               ; S24 DIAG OFF
;    move.l  #IrqRte, $70               ; S24 DIAG OFF
;    move.l  #IrqRte, $74               ; S24 DIAG OFF
;    move.l  #IrqRte, $78               ; S24 DIAG OFF
;    move.l  #IrqRte, $7C               ; S24 DIAG OFF

; --- NTSC/PAL detect + video timing ---
; S18 A1 spike fix: per TRM "interlace = number of halflines per field is odd"
; cioe' VP+1 deve essere ODD => VP deve essere EVEN.
; NTSC interlace: VP=524 (525 halflines per field, 2 fields = 1050 total = 16.7ms)
; PAL  interlace: VP=624 (625 halflines per field, 2 fields = 1250 total = 20ms)
; VMID/HEIGHT restano originali (visible window per-field invariato).
    move.w  BASE+$14002, d0
    and.w   #$10, d0
    beq.s   IsPAL
IsNTSC:
    move.w  #823, d2
    move.w  #1409, d0
    move.w  #266, d6
    move.w  #240, d4                  ; was 241; HEIGHT pari => VDB pari (per interlace alignment)
    move.w  #524, VP                  ; NTSC interlace 480i: VP even per TRM
    bra.s   DoVid
IsPAL:
    move.w  #843, d2
    move.w  #1381, d0
    move.w  #322, d6
    move.w  #286, d4                  ; was 287; HEIGHT pari => VDB pari
    move.w  #624, VP                  ; PAL interlace 575i: VP even per TRM
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
    or.l    #$02780000, d3            ; HEIGHT=480 raw (= 240 displayed/field in interlace, decrementa 2/line)
    move.l  d3, OP_PH0_LO_VAL

; ---------------------------------------------------------------------------
; S24 DIAG: accende il video SUBITO, con una OP list minima (solo oggetto
; STOP), cosi' i milestone su BG diventano visibili durante tutto il boot.
; Senza questo, BG e' invisibile: spento fino al VMODE in fondo a OPSetup,
; e coperto dal BMobj di sfondo subito dopo. OPSetup piu' avanti riscrive
; sia la OP list sia OLP sia VMODE, quindi questo setup e' solo temporaneo.
; NESSUN delay: il registro BG conserva l'ultimo valore scritto, quindi il
; colore rimasto a schermo in caso di blocco = ultimo milestone superato.
    move.l  #$00000000, $0FE0
    move.l  #$00000004, $0FE4
    move.l  #$0FE0, d0
    swap    d0
    move.l  d0, OLP
    move.w  #VIDEN|BGEN|PWIDTH2|RGB16|CSYNC, VMODE
    move.w  #$F87C, BG                    ; 1 ARANCIO: video acceso, timing ok

    move.l  #0, G_CTRL
    move.l  #$00070007, G_END

; --- Sanity-check cart magic JAGLBA03 ---
    lea     SceneData, a6
    cmpi.l  #$4A41474C, (a6)              ; "JAGL"
    bne     BadCart
    cmpi.l  #$42413033, 4(a6)             ; "BA03"
    bne     BadCart

; --- Copy palette from cart to DRAM (kept for any direct lookup use) ---
    move.l  HDR_PAL_OFF(a6), d0
    lea     0(a6,d0.l), a0
    movea.l #PALETTE_DRAM, a1
    move.w  #127, d0
CopyPal:
    move.l  (a0)+, (a1)+
    dbra    d0, CopyPal

; --- Upload palette to TOM CLUT at $F00400 (256 RGB16) ---
    movea.l #PALETTE_DRAM, a0
    movea.l #CLUT, a1
    move.w  #127, d0
CopyClut:
    move.l  (a0)+, (a1)+
    dbra    d0, CopyClut

; --- V4: GDBIOS install DEFERRED to V5 ---
; Cart fits in 6 MB unbanked, no bank switching needed for V4.  BigPEmu's
; Force JagGD emulation appears to crash inside GD_Install's firmware
; dispatch (the GDFunc GD_Init dispatch jumps to a buffer that BigPEmu
; doesn't fill correctly).  V5 will reintroduce GDBIOS install with
; explicit hw detection + fallback when bank switching becomes mandatory
; for BODY/ANIM/SAMPLES expansion.

; --- Load scene 0 (full pipeline: grid decode + brick RLE -> 8bpp + handles) ---
    move.w  #$003E, BG                    ; 2 BLU: magic + palette ok, entro in LoadScene
    moveq   #0, d0
    bsr     LoadScene
    move.w  #$F8FC, BG                    ; 3 MAGENTA: LoadScene tornata

; --- S20: scene 0 camera override = ground truth da SCENE.HQR (cella letto Citadel ospedale).
;     Source: extracted/SCENE.HQR scene 0 hero_world_x=26880, hero_world_z=27626 (= cell 52, 53).
;     TODO S21: popolare cart_pack_all.py con i 120 init reali dal CSV invece di placeholder.
    move.w  #52, CAM_GX
    move.w  #53, CAM_GZ

; --- V5: cache body 0 (Twinsen) pointers from cart ---
    bsr     InitBody
    move.w  #$07FC, BG                    ; 4 CIANO: InitBody tornata

; --- F4.2a: load GPU rasterizer code into GPU local RAM (boot-once) ---
    bsr     InitGpu
    move.w  #$07E0, BG                    ; 5 AZZURRO: InitGpu tornata

; --- S24 PoC: azzera i contatori di tick. La copia+init del blob C avviene
;     DOPO OPSetup (vedi sotto il write VMODE): li' il video e' acceso e la
;     diagnostica DbgBar e' visibile, qui invece lo schermo e' ancora spento.
; S24 BASELINE TEST: tutto il codice S24 e' disattivato per stabilire se la
; schermata nera dipende dalle mie aggiunte o preesiste. Per riattivare,
; togliere i ';' dai blocchi marcati "S24 OFF" (qui, post-VMODE, MainLoop,
; ViHandler, incbin PocBlob, InitCBlob).
;    moveq   #0, d0                     ; S24 OFF
;    move.l  d0, VBL_TICK               ; S24 OFF
;    move.l  d0, LAST_TICK              ; S24 OFF
;    move.b  d0, C_BLOB_OK              ; S24 OFF

; --- V3.3 init derived state, render scene to BG_PRISTINE then blit to BG_A ---
    move.b  #0, REDRAW_FLAG
    move.b  #0, PENDING_SWAP              ; S22B: no pending swap at boot
    move.b  #1, ACTIVE_BG_ID              ; ACTIVE=B during boot draw, render target = ~ACTIVE = BG_A
    move.l  #0, BODY_DRAW_TARGET          ; S23 default: 0 = use ACTIVE_BG_ID lookup

; S23 sprite-based body: pre-render T-pose into BODY_BITMAP (clobbers BG_PRISTINE,
; restored by RenderScene below). After this, body is composed by OP overlay
; and is NEVER drawn into BG_A/BG_B during runtime.
    bsr     InitBodyBitmap
    move.w  #$88FF, BG                    ; 6: InitBodyBitmap tornata (era $F800 = Y=00 = NERO, invisibile)

    bsr     ComputeCamera
    bsr     RenderScene                    ; -> BG_PRISTINE (cached, S16 Path A 2.5)
    move.w  #$FFFF, BG                    ; 7 BIANCO: RenderScene tornata
    bsr     BlitPristineToInactive         ; -> BG_A (= ~ACTIVE_BG_ID)
    move.w  #$0FE0, BG                    ; 8 VERDE: blit tornato, entro in OPSetup

    move.b  #0, ACTIVE_BG_ID              ; BG_A is now what ViHandler displays

    bra     OPSetup

; ---------------------------------------------------------------------------
; LoadScene: input d0.w = scene_idx. Loads scene's grid into CUBE_DRAM, scans
; for referenced bricks, decodes their RLE payloads to BRICK_BLOBS_WORKING in
; 8bpp WID-padded format, populates BRICK_HANDLES table. Sets CAM_GX/CAM_GZ
; from scene table init values. Caller must call ComputeCamera + RenderScene.
; ---------------------------------------------------------------------------
LoadScene:
    movem.l d0-d7/a0-a6, -(sp)
    and.l   #$FFFF, d0                    ; zero-extend scene_idx
    move.w  d0, CUR_SCENE_ID

    lea     SceneData, a6                 ; cart base

; Read scene table entry at scene_table + d0*16
    move.l  HDR_SCENE_TBL_OFF(a6), d1
    lea     0(a6,d1.l), a0                ; a0 = scene_table base
    move.l  d0, d1
    lsl.l   #4, d1                        ; * TBL_ENTRY_SIZE
    adda.l  d1, a0                        ; a0 = scene_table[d0]

; Extract init camera + bll_idx
    moveq   #0, d2
    move.b  TBL_GX_INIT(a0), d2
    move.w  d2, CAM_GX
    moveq   #0, d2
    move.b  TBL_GZ_INIT(a0), d2
    move.w  d2, CAM_GZ
    moveq   #0, d3
    move.b  TBL_BLL_IDX(a0), d3           ; bll_uniq_idx

; CUR_BLL_BASE = bll_blob + bll_table[bll_idx]
    move.l  HDR_BLL_TBL_OFF(a6), d1
    lea     0(a6,d1.l), a1                ; a1 = bll_table base
    move.l  d3, d1
    lsl.l   #2, d1
    move.l  0(a1,d1.l), d4                ; d4 = bll_table[idx] (offset within bll_blob)
    move.l  HDR_BLL_BLOB_OFF(a6), d5
    add.l   d5, d4                        ; +bll_blob_off (cart-relative)
    lea     0(a6,d4.l), a1
    move.l  a1, CUR_BLL_BASE

; CUR_GRID_BASE = grid_blob + grid_table[scene_idx]
    move.l  HDR_GRID_TBL_OFF(a6), d1
    lea     0(a6,d1.l), a1                ; a1 = grid_table base
    move.l  d0, d1
    lsl.l   #2, d1
    move.l  0(a1,d1.l), d4                ; offset within grid_blob
    move.l  HDR_GRID_BLOB_OFF(a6), d5
    add.l   d5, d4
    lea     0(a6,d4.l), a1
    move.l  a1, CUR_GRID_BASE

; ===== Stage 1: Decode grid RLE columns -> CUBE_DRAM =====
    movea.l CUR_GRID_BASE, a3
    moveq   #0, d7                        ; gz
.ls_gz_loop:
    moveq   #0, d6                        ; gx
.ls_gx_loop:
    move.w  d7, d0
    lsl.w   #6, d0                        ; gz*64
    add.w   d6, d0
    add.w   d0, d0                        ; *2
    move.w  0(a3,d0.w), d1                ; column byte offset (BE)

    movea.l a3, a2
    adda.w  d1, a2                        ; column data start

    move.w  d7, d0
    lsl.w   #6, d0
    add.w   d6, d0
    mulu.w  #50, d0                       ; *25*2
    movea.l #CUBE_DRAM, a4
    adda.l  d0, a4
    bsr     DecompCol

    addq.w  #1, d6
    cmp.w   #GRID_X, d6
    blt     .ls_gx_loop
    addq.w  #1, d7
    cmp.w   #GRID_Z, d7
    blt     .ls_gz_loop

; ===== Stage 2: Clear brick_used bitmap + handles =====
    lea     BRICK_USED_BMP, a0
    move.w  #(BRICK_USED_SIZE/4)-1, d0
    moveq   #0, d1
.ls_clr_bmp:
    move.l  d1, (a0)+
    dbra    d0, .ls_clr_bmp

    lea     BRICK_HANDLES, a0
    move.w  #8618, d0                     ; 8619 entries - 1
    moveq   #0, d1
.ls_clr_hdl:
    move.l  d1, (a0)+
    dbra    d0, .ls_clr_hdl

; ===== Stage 3: Walk cube + BLL -> mark brick_used bits =====
    movea.l CUR_BLL_BASE, a1
    moveq   #0, d7                        ; gz
.ls_uz:
    moveq   #0, d6                        ; gx
.ls_ux:
    moveq   #0, d5                        ; gy
.ls_uy:
    move.w  d7, d0
    lsl.w   #6, d0
    add.w   d6, d0
    mulu.w  #50, d0
    moveq   #0, d1
    move.w  d5, d1
    add.w   d1, d1
    add.l   d1, d0
    movea.l #CUBE_DRAM, a0
    move.w  0(a0,d0.l), d4                ; cube cell UWORD
    beq     .ls_u_skip

    move.b  d4, d3                        ; num_block (low byte)
    and.w   #$00FF, d3
    beq     .ls_u_skip
    move.w  d4, d2
    lsr.w   #8, d2
    and.w   #$00FF, d2                    ; pos_block (high byte)
    subq.w  #1, d3                        ; 0-based block idx

; BLL block lookup at bll_base + d3*4
    move.w  d3, d0
    lsl.w   #2, d0
    move.l  0(a1,d0.w), d0                ; block byte offset (BE u32)
    addq.l  #3, d0                        ; skip dx,dy,dz header

    moveq   #0, d1
    move.w  d2, d1
    lsl.w   #2, d1                        ; pos_block * 4
    add.l   d1, d0
    addq.l  #2, d0                        ; skip info_uword
    move.w  0(a1,d0.l), d3                ; brick_ref (BE u16, 1-based GLOBAL)
    beq     .ls_u_skip
    subq.w  #1, d3                        ; 0-based global brick_idx

; Set bit d3 in brick_used bitmap
    move.w  d3, d0
    lsr.w   #3, d0                        ; byte index
    move.w  d3, d2
    and.w   #7, d2                        ; bit index
    moveq   #1, d1
    lsl.w   d2, d1                        ; bit mask
    lea     BRICK_USED_BMP, a0
    or.b    d1, 0(a0,d0.w)
.ls_u_skip:
    addq.w  #1, d5
    cmp.w   #GRID_Y, d5
    blt     .ls_uy
    addq.w  #1, d6
    cmp.w   #GRID_X, d6
    blt     .ls_ux
    addq.w  #1, d7
    cmp.w   #GRID_Z, d7
    blt     .ls_uz

; ===== Stage 4: Decode RLE bricks for each brick_used bit set =====
; Loop counters d6 (brick_idx) and d7 (dbra) saved in DRAM scratch across the
; per-brick body, freeing all regs for LUT lookups + RLE decode.
; Pers-brick state (DX, DY, HotX, HotY, padded_DX, blob_off) saved in DRAM too.
    lea     SceneData, a6
    move.l  HDR_BDIR_OFF(a6), d0
    lea     0(a6,d0.l), a2                ; a2 = brick_dir base (preserved)
    move.l  HDR_BBLOB_OFF(a6), d0
    lea     0(a6,d0.l), a3                ; a3 = brick_blob base (preserved)
    movea.l #BRICK_BLOBS_WORKING, a5      ; a5 = working buffer cursor (advances)
    moveq   #0, d6                        ; brick_idx
    move.w  HDR_BRICK_COUNT(a6), d7
    subq.w  #1, d7                        ; dbra counter

.ls_brick_iter:
; Test bit d6 in brick_used bitmap
    move.w  d6, d0
    lsr.w   #3, d0
    move.w  d6, d1
    and.w   #7, d1
    moveq   #1, d2
    lsl.w   d1, d2
    lea     BRICK_USED_BMP, a0
    move.b  0(a0,d0.w), d0
    and.b   d2, d0
    beq     .ls_brick_skip

; Save loop state in DRAM
    move.w  d6, LS_BRICK_IDX
    move.w  d7, LS_BRICK_CNT

; Save handle: BRICK_HANDLES[brick_idx] = a5 (current blob start)
    move.w  d6, d0
    lsl.w   #2, d0
    lea     BRICK_HANDLES, a0
    move.l  a5, 0(a0,d0.w)

; Read brick_dir entry (8 B): blob_off(4) + DX(1) + DY(1) + HotX(1) + HotY(1)
    move.w  d6, d0
    lsl.w   #3, d0                        ; brick_idx * 8
    move.l  0(a2,d0.w), d1                ; d1 = blob_off (BE u32)
    move.l  d1, LS_BLOB_OFF
    moveq   #0, d3
    move.b  4(a2,d0.w), d3                ; d3 = DX
    moveq   #0, d4
    move.b  5(a2,d0.w), d4                ; d4 = DY
    moveq   #0, d5
    move.b  6(a2,d0.w), d5                ; d5 = HotX
    moveq   #0, d6
    move.b  7(a2,d0.w), d6                ; d6 = HotY (note: clobbers brick_idx, saved in DRAM)

; LUT lookup: padded_DX from PadDxLut
    move.w  d3, d0
    add.w   d0, d0                        ; DX*2 (word stride)
    lea     PadDxLut(pc), a0
    move.w  0(a0,d0.w), d2                ; d2 = padded_DX
    move.w  d2, LS_PAD_COUNT              ; will be subtracted to get pad_count later

; LUT lookup: A2_FLAGS from PadFlagsLut
    move.w  d3, d0
    lsl.w   #2, d0                        ; DX*4 (long stride)
    lea     PadFlagsLut(pc), a0
    move.l  0(a0,d0.w), d7                ; d7 = A2_FLAGS

; Write brick header (8 bytes) to working buffer
    move.l  d7, (a5)+                     ; +0 BH_FLAGS
    move.b  d3, (a5)+                     ; +4 BH_DX
    move.b  d4, (a5)+                     ; +5 BH_DY
    move.b  d5, (a5)+                     ; +6 BH_HOTX
    move.b  d6, (a5)+                     ; +7 BH_HOTY

; Compute pad_count = padded_DX - DX, store in DRAM
    sub.w   d3, d2                        ; d2 = pad_count
    move.w  d2, LS_PAD_COUNT

; Setup RLE source pointer: brick_blob + blob_off + 4 (skip 4-byte header)
    movea.l a3, a1
    adda.l  LS_BLOB_OFF, a1
    addq.l  #4, a1                        ; a1 = RLE source (post-header)

; Row loop: for each of DY rows
;   d4 = DY (kept fixed during outer)
;   inner loop variables: per-row decode
    move.w  d4, d0                        ; d0 = row counter
    subq.w  #1, d0
.ls_brk_row:
    move.w  d0, -(sp)                     ; save row counter (regs heavily used inside)
    moveq   #0, d6                        ; d6 = current col (wrote bytes count)

; Read NbBlock byte
    moveq   #0, d1
    move.b  (a1)+, d1                     ; d1 = nb_block
    subq.w  #1, d1
    bmi.s   .ls_brk_no_blocks

.ls_brk_block:
    moveq   #0, d2
    move.b  (a1)+, d2                     ; d2 = opcode
    move.w  d2, d3
    and.w   #$3F, d3
    addq.w  #1, d3                        ; d3 = count
    and.b   #$C0, d2                      ; d2 = mode bits
    beq.s   .ls_brk_skip_pix              ; mode 00: skip count pixels
    cmp.b   #$80, d2
    beq.s   .ls_brk_repeat                ; mode 10: repeat 1 pixel count times

; mode 01 or 11: copy count raw pixels
    subq.w  #1, d3
.ls_brk_copy_loop:
    move.b  (a1)+, (a5)+
    addq.w  #1, d6
    dbra    d3, .ls_brk_copy_loop
    bra.s   .ls_brk_block_next

.ls_brk_skip_pix:
; mode 00: write count zeros (transparent)
    subq.w  #1, d3
    moveq   #0, d2
.ls_brk_skip_loop:
    move.b  d2, (a5)+
    addq.w  #1, d6
    dbra    d3, .ls_brk_skip_loop
    bra.s   .ls_brk_block_next

.ls_brk_repeat:
; mode 10: repeat 1 pixel count times
    subq.w  #1, d3
    move.b  (a1)+, d2
.ls_brk_rep_loop:
    move.b  d2, (a5)+
    addq.w  #1, d6
    dbra    d3, .ls_brk_rep_loop

.ls_brk_block_next:
    dbra    d1, .ls_brk_block

.ls_brk_no_blocks:
; Pad row from d6 (current col) to padded_DX with 0s
; pad_count_for_row = LS_PAD_COUNT + (DX - d6) where DX = recorded in header @ -7(a5_at_row_start)
; Simpler: total bytes per row = padded_DX. Bytes already written = d6.
; bytes_to_pad = padded_DX - d6
; padded_DX recoverable from BRICK_HANDLES[idx] header: but we just wrote it.
; Easiest: compute padded_DX = DX + LS_PAD_COUNT. DX needs reload.
; Use DRAM: was LS_PAD_COUNT updated? Yes pad_count = padded_DX - DX, stored.
; padded_DX = DX + LS_PAD_COUNT. Reload DX from brick_dir using LS_BRICK_IDX.
    move.w  LS_BRICK_IDX, d2
    lsl.w   #3, d2                        ; * 8
    moveq   #0, d3
    move.b  4(a2,d2.w), d3                ; d3 = DX
    add.w   LS_PAD_COUNT, d3              ; d3 = padded_DX
    sub.w   d6, d3                        ; d3 = bytes_to_pad
    ble.s   .ls_brk_row_done              ; nothing to pad (d6 may overshoot DX in malformed RLE)

    subq.w  #1, d3
    moveq   #0, d2
.ls_brk_pad_loop:
    move.b  d2, (a5)+
    dbra    d3, .ls_brk_pad_loop

.ls_brk_row_done:
    move.w  (sp)+, d0                     ; restore row counter
    dbra    d0, .ls_brk_row

; Phrase-align working buffer cursor for next brick
    move.l  a5, d0
    addq.l  #7, d0
    and.l   #$FFFFFFF8, d0
    movea.l d0, a5

; Restore loop counters
    move.w  LS_BRICK_IDX, d6
    move.w  LS_BRICK_CNT, d7

.ls_brick_skip:
    addq.w  #1, d6
    dbra    d7, .ls_brick_iter

.ls_done:
    movem.l (sp)+, d0-d7/a0-a6
    rts

; ---------------------------------------------------------------------------
; RenderScene: BG clear via Blitter pattern fill, walk cube, blit each filled
; cell. Reads CAM_VX_VAL/VY_VAL and walk bounds from DRAM.
; (V3.4.4 logic preserved verbatim, with V4 safety: skip if BRICK_HANDLES[idx]=0)
; ---------------------------------------------------------------------------
RenderScene:
    ; S16 Path A Phase 2.5: render to BG_PRISTINE (cached, copied to BG_INACTIVE
    ; via BlitPristineToInactive on body-redraw). Sganciato da ACTIVE_BG_ID.
    move.l  #BG_PRISTINE, A1_BASE

; Clear BG to 0 via Blitter pattern fill (640x592 8bpp)
    move.l  #$00014A18, A1_FLAGS          ; PIXEL8|XADDPIX|WID640|PITCH1
    move.l  #$0001FD80, A1_STEP
    moveq   #0, d0
    move.l  d0, A1_PIXEL
    move.l  #$02500280, B_COUNT
    move.l  d0, B_PATD
    move.l  d0, B_PATD+4
    move.l  #$01810200, B_CMD
.rs_clr_wait:
    move.l  B_CMD, d0
    btst    #0, d0
    beq.s   .rs_clr_wait

    lea     SceneData, a6

    move.w  GZ_LO_VAL, d7
SceneZLoop:
    move.w  GX_LO_VAL, d6
SceneXLoop:
    moveq   #0, d5
SceneYLoop:
    move.w  d7, d0
    lsl.w   #6, d0
    add.w   d6, d0
    mulu.w  #50, d0
    moveq   #0, d1
    move.w  d5, d1
    add.w   d1, d1
    add.l   d1, d0
    movea.l #CUBE_DRAM, a0
    move.w  0(a0,d0.l), d4
    beq     CellEmpty

    move.b  d4, d3
    and.w   #$00FF, d3
    beq     CellEmpty
    move.w  d4, d2
    lsr.w   #8, d2
    and.w   #$00FF, d2

    subq.w  #1, d3
    movea.l CUR_BLL_BASE, a1              ; V4: BLL base from scene state
    move.w  d3, d0
    lsl.w   #2, d0
    move.l  0(a1,d0.w), d0                ; block byte offset

    addq.l  #3, d0
    moveq   #0, d1
    move.w  d2, d1
    lsl.w   #2, d1
    add.l   d1, d0
    addq.l  #2, d0
    move.w  0(a1,d0.l), d3                ; brick_ref (1-based GLOBAL)
    beq     CellEmpty

    subq.w  #1, d3
    movea.l #BRICK_HANDLES, a0
    move.w  d3, d0
    lsl.w   #2, d0
    movea.l 0(a0,d0.w), a1                ; brick blob ptr (or 0 if not loaded)
    move.l  a1, d0
    beq     CellEmpty                      ; V4 safety: skip if handle is 0

    moveq   #0, d3
    move.b  BH_DX(a1), d3
    moveq   #0, d4
    move.b  BH_DY(a1), d4
    moveq   #0, d2
    move.b  BH_HOTX(a1), d2
    moveq   #0, d1
    move.b  BH_HOTY(a1), d1

    move.w  d6, d0
    sub.w   d7, d0
    muls.w  #24, d0                  ; S19: X pitch 24 (revert S18 #48); LBA1 native iso 2:1
    add.l   CAM_VX_VAL, d0
    add.w   d2, d0

    move.w  d6, d2
    add.w   d7, d2
    muls.w  #12, d2                  ; S19: Y pitch 12 (revert S18 #24); LBA1 native iso 2:1
    move.w  d5, d4
    muls.w  #15, d4                  ; S19: GY mult 15 (revert S18 #30)
    sub.l   d4, d2
    add.l   CAM_VY_VAL, d2
    add.l   d1, d2

    moveq   #0, d3
    move.b  BH_DX(a1), d3
    moveq   #0, d4
    move.b  BH_DY(a1), d4

    move.l  d0, d1
    add.l   d3, d1
    ble     CellEmpty
    cmp.l   #BG_W, d0
    bge     CellEmpty
    move.l  d2, d1
    add.l   d4, d1
    ble     CellEmpty
    cmp.l   #BG_H, d2
    bge     CellEmpty

    moveq   #0, d1
    tst.l   d0
    bge.s   .clp_no_left
    move.l  d0, d1
    neg.l   d1
    moveq   #0, d0
.clp_no_left:
    move.l  d1, TMP_LSK

    moveq   #0, d1
    tst.l   d2
    bge.s   .clp_no_top
    move.l  d2, d1
    neg.l   d1
    moveq   #0, d2
.clp_no_top:
    move.l  d1, TMP_TSK

    move.l  d0, d1
    add.l   d3, d1
    sub.l   TMP_LSK, d1
    cmp.l   #BG_W, d1
    ble.s   .clp_w_ok
    move.l  #BG_W, d1
.clp_w_ok:
    sub.l   d0, d1
    ble     CellEmpty

    move.l  d1, d3
    move.l  d2, d1
    add.l   d4, d1
    sub.l   TMP_TSK, d1
    cmp.l   #BG_H, d1
    ble.s   .clp_h_ok
    move.l  #BG_H, d1
.clp_h_ok:
    sub.l   d2, d1
    ble     CellEmpty
    move.l  d1, d4

    move.l  d2, d1
    swap    d1
    or.l    d0, d1
    move.l  d1, A1_PIXEL

    moveq   #0, d1
    move.w  d3, d1
    neg.w   d1
    or.l    #$00010000, d1
    move.l  d1, A1_STEP
    move.l  d1, A2_STEP

    lea     BH_PIXELS(a1), a0
    move.l  a0, A2_BASE
    move.l  BH_FLAGS(a1), A2_FLAGS

    move.l  TMP_TSK, d1
    swap    d1
    or.l    TMP_LSK, d1
    move.l  d1, A2_PIXEL

    move.l  d4, d1
    swap    d1
    or.l    d3, d1
    move.l  d1, B_COUNT

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

.rs_final_wait:
    move.l  B_CMD, d0
    btst    #0, d0
    beq.s   .rs_final_wait
    rts

; ---------------------------------------------------------------------------
; BlitPristineToInactive (S16 Path A Phase 2.5):
; Blitter copy BG_PRISTINE -> BG_INACTIVE (= ~ACTIVE_BG_ID), full 640x592 8bpp.
; Cost ~5-7 ms (Blitter ~64 MB/s). Replaces RenderScene in .ml_render path
; (camera fissa, scene cached). Preserves modifiable BG_INACTIVE for body draw.
; B_CMD_SPRITE used: trasparente su idx 0 (caveat: ghost se pristine ha gap idx 0).
; ---------------------------------------------------------------------------
BlitPristineToInactive:
    movem.l d0/a0, -(sp)
    moveq   #0, d0
    move.b  ACTIVE_BG_ID, d0
    bne.s   .bpti_target_a
    movea.l #BG_B, a0                    ; ACTIVE=A -> target = B
    bra.s   .bpti_target_done
.bpti_target_a:
    movea.l #BG_A, a0
.bpti_target_done:

    ; S22B single OPAQUE blit pristine to target B_CMD_OPAQUE replaces dest
    ; unconditionally so any stale body residue gets overwritten with pristine.
    move.l  a0, A1_BASE
    move.l  #BG_PRISTINE, A2_BASE
    move.l  #$00004A18, A1_FLAGS
    move.l  #$00004A18, A2_FLAGS
    move.l  #$0001FD80, A1_STEP
    move.l  #$0001FD80, A2_STEP
    moveq   #0, d0
    move.l  d0, A1_PIXEL
    move.l  d0, A2_PIXEL
    move.l  #$02500280, B_COUNT
    move.l  #B_CMD_OPAQUE, B_CMD
.bpti_wait:
    move.l  B_CMD, d0
    btst    #0, d0
    beq.s   .bpti_wait
    movem.l (sp)+, d0/a0
    rts

; ---------------------------------------------------------------------------
; BlitPristineBodyStrip (S22B):
; Like BlitPristineToInactive but limits the pre-clear+pristine blit to a
; horizontal strip Y = BODY_SY0 +/- 60 (= 120 lines covering body extent +
; movement margin). Reduces Blitter activity on body redraw from ~12ms (full
; 640x592) to ~2.5ms (640x120). Vital for OP/Blitter bus arbitration so the
; Object Processor does not starve mid-display while reading the BG buffer
; (root cause of "yellow stripes + sdoppiamento" corruption visible only in
; bottom 2/3 of display during D-pad held; top 1/3 was clean because OP read
; it before MainLoop kicked Blitter on).
; Used by .ml_render only. .ml_transition still uses full BlitPristineToInactive
; (scene transitions need full BG refresh, are rare events).
; ---------------------------------------------------------------------------
BlitPristineBodyStrip:
    movem.l d0-d2/a0, -(sp)

    moveq   #0, d0
    move.b  ACTIVE_BG_ID, d0
    bne.s   .bpbs_t_a
    movea.l #BG_B, a0
    bra.s   .bpbs_t_done
.bpbs_t_a:
    movea.l #BG_A, a0
.bpbs_t_done:

    move.w  BODY_SY0, d1
    sub.w   #100, d1                        ; S22B body anchor=feet, body extends UP ~100px
    bpl.s   .bpbs_y0_ok
    moveq   #0, d1
.bpbs_y0_ok:

    move.w  #130, d2                        ; S22B 130 lines covers body extent + movement margin
    move.w  d1, d0
    add.w   d2, d0
    cmp.w   #BG_H, d0
    ble.s   .bpbs_h_ok
    move.w  #BG_H, d2
    sub.w   d1, d2
.bpbs_h_ok:

    ; S22B single OPAQUE blit pristine strip to target (no pre-clear needed:
    ; B_CMD_OPAQUE replaces dest unconditionally so any previous body residue
    ; in the strip area gets overwritten with pristine bricks).
    move.l  a0, A1_BASE
    move.l  #BG_PRISTINE, A2_BASE
    move.l  #$00004A18, A1_FLAGS
    move.l  #$00004A18, A2_FLAGS
    move.l  #$0001FD80, A1_STEP
    move.l  #$0001FD80, A2_STEP
    move.l  d1, d0
    swap    d0
    move.l  d0, A1_PIXEL
    move.l  d0, A2_PIXEL
    move.l  d2, d0
    swap    d0
    or.l    #$00000280, d0
    move.l  d0, B_COUNT
    move.l  #B_CMD_OPAQUE, B_CMD
.bpbs_blit_wait:
    move.l  B_CMD, d0
    btst    #0, d0
    beq.s   .bpbs_blit_wait

    movem.l (sp)+, d0-d2/a0
    rts

; ---------------------------------------------------------------------------
; SyncBodyStripToActive (S22B):
; After body draw on ~ACTIVE, copy the body strip area back to ACTIVE so both
; double-buffer halves hold identical strip content. This eliminates the
; perceived "sdoppiamento" alternation as display switches buffers each swap.
; OPAQUE blit (no transparency) so source pixels REPLACE dest pixels in strip.
; Same Y range as BlitPristineBodyStrip (sy-100 to sy+30).
; Cost ~2.5ms (640x130 blit).
; ---------------------------------------------------------------------------
SyncBodyStripToActive:
    movem.l d0-d2/a0-a1, -(sp)

    moveq   #0, d0
    move.b  ACTIVE_BG_ID, d0
    bne.s   .sbsa_active_b
    movea.l #BG_A, a0                       ; ACTIVE = A (display)
    movea.l #BG_B, a1                       ; src = ~ACTIVE = B (just rendered)
    bra.s   .sbsa_setup_done
.sbsa_active_b:
    movea.l #BG_B, a0
    movea.l #BG_A, a1
.sbsa_setup_done:

    move.w  BODY_SY0, d1
    sub.w   #100, d1
    bpl.s   .sbsa_y0_ok
    moveq   #0, d1
.sbsa_y0_ok:

    move.w  #130, d2
    move.w  d1, d0
    add.w   d2, d0
    cmp.w   #BG_H, d0
    ble.s   .sbsa_h_ok
    move.w  #BG_H, d2
    sub.w   d1, d2
.sbsa_h_ok:

    move.l  a0, A1_BASE                     ; dest = ACTIVE (display)
    move.l  a1, A2_BASE                     ; src = ~ACTIVE (just rendered)
    move.l  #$00004A18, A1_FLAGS
    move.l  #$00004A18, A2_FLAGS
    move.l  #$0001FD80, A1_STEP
    move.l  #$0001FD80, A2_STEP
    move.l  d1, d0
    swap    d0
    move.l  d0, A1_PIXEL
    move.l  d0, A2_PIXEL
    move.l  d2, d0
    swap    d0
    or.l    #$00000280, d0
    move.l  d0, B_COUNT
    move.l  #B_CMD_OPAQUE, B_CMD
.sbsa_wait:
    move.l  B_CMD, d0
    btst    #0, d0
    beq.s   .sbsa_wait

    movem.l (sp)+, d0-d2/a0-a1
    rts

; ---------------------------------------------------------------------------
; SyncRenderToActive (S22B):
; After rendering scene+body to ~ACTIVE, copy result back to ACTIVE so both
; double-buffer halves hold identical content. The display still alternates
; buffers via PENDING_SWAP commit at field 0, but the alternation is now
; visually invisible (both buffers = same image). This eliminates the
; per-frame "ghost double-image" perception during D-pad movement, where
; LCD persistence on BigPEmu blends body N (in ACTIVE) and body N+1
; (in ~ACTIVE just rendered) producing visible doubling.
; Cost ~12ms / frame. Performance optimization deferred.
; ---------------------------------------------------------------------------
SyncRenderToActive:
    movem.l d0/a0-a1, -(sp)
    moveq   #0, d0
    move.b  ACTIVE_BG_ID, d0
    bne.s   .srta_active_b
    movea.l #BG_A, a0                     ; ACTIVE = A (display reads A)
    movea.l #BG_B, a1                     ; src = ~ACTIVE = B (just rendered)
    bra.s   .srta_setup_done
.srta_active_b:
    movea.l #BG_B, a0
    movea.l #BG_A, a1
.srta_setup_done:
    move.l  a0, A1_BASE                   ; dest = ACTIVE (= currently displayed)
    move.l  a1, A2_BASE                   ; src  = ~ACTIVE (= just rendered)
    move.l  #$00004A18, A1_FLAGS
    move.l  #$00004A18, A2_FLAGS
    move.l  #$0001FD80, A1_STEP
    move.l  #$0001FD80, A2_STEP
    moveq   #0, d0
    move.l  d0, A1_PIXEL
    move.l  d0, A2_PIXEL
    move.l  #$02500280, B_COUNT
    move.l  #B_CMD_SPRITE, B_CMD          ; sprite mode OK src has full content
.srta_wait:
    move.l  B_CMD, d0
    btst    #0, d0
    beq.s   .srta_wait
    movem.l (sp)+, d0/a0-a1
    rts

; ---------------------------------------------------------------------------
; ComputeCamera (V3.4.4 unchanged)
; ---------------------------------------------------------------------------
ComputeCamera:
    move.w  CAM_GX, d0
    sub.w   CAM_GZ, d0
    muls.w  #24, d0                  ; S19: X pitch 24 (revert S18 #48); LBA1 native iso 2:1
    neg.l   d0
    add.l   #323, d0                        ; S20: cam X center = 323 (calibrated for 640H interlace, was 320)
    move.l  d0, CAM_VX_VAL

    moveq   #0, d0
    move.w  CAM_GX, d0
    add.w   CAM_GZ, d0
    mulu.w  #12, d0                  ; S19: Y pitch 12 (revert S18 #24); LBA1 native iso 2:1
    neg.l   d0
    add.l   #191 + (CAM_GY_FIXED * 15), d0  ; S20: cam Y base = 191 (was 240 progressive, calibrated for 480i framing matching DOS reference)
    move.l  d0, CAM_VY_VAL

    move.w  CAM_GX, d0
    sub.w   #9, d0
    bge.s   .cc_gxl_ok
    moveq   #0, d0
.cc_gxl_ok:
    move.w  d0, GX_LO_VAL

    move.w  CAM_GX, d0
    add.w   #12, d0
    cmp.w   #GRID_X, d0
    blt.s   .cc_gxh_ok
    move.w  #GRID_X, d0
.cc_gxh_ok:
    move.w  d0, GX_HI_VAL

    move.w  CAM_GZ, d0
    sub.w   #12, d0
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

    move.l  #$10000002, $1000               ; BG PH0 HI (DATA placeholder + LINK to $1010 = body)
    move.l  OP_PH0_LO_VAL, $1004             ; BG PH0 LO (HEIGHT=480 + YPOS=VDB)
    move.l  #$00000045, $1008                ; BG PH1 HI (RELEASE=0, INDEX=1, IWIDTH=80)
    move.l  #$0280B000, $100C                ; BG PH1 LO (DWIDTH=160 interlace 480i)

; S23 body BMobj at $1010-$101F (LINK from BG, LINK to STOP at $1020).
; Interlace 480i full sprite (Option alpha, mirrors BG): even field reads bitmap
; rows 0,2,4,...; odd field reads rows 1,3,5,... via DATA+128 offset.
; S23 iter 3 sizing: HEIGHT=80 raw (= 40 iter/field, 80 visible FB rows = exact body 1 bbox).
; HEIGHT 80 = 0b001010000 -> phrase bits 18,20 set -> byte $1015 = $14 -> PH0 LO upper word $0414.
    move.l  #$1F500002, $1010                ; Body PH0 HI (DATA=BODY_BITMAP, LINK to $1020); even field default
    move.l  #$040A0000, BODY_PH0_LO_VAL      ; HEIGHT=40 raw (= 20 iter/field, 40 visible FB rows = body 1 actual visible extent)
    move.l  BODY_PH0_LO_VAL, $1014           ; ViHandler refreshes per VBL with YPOS+field LSB
    move.l  #$00008040, $1018                ; Body PH1 HI (TRANS=1, RELEASE=0, INDEX=1, IWIDTH hi=0; IWIDTH=4 lo bits in PH1 LO)
    move.l  #$4020B000, BODY_PH1_LO_VAL      ; DEPTH=8bpp, PITCH=1, DWIDTH=8 (interlace stride 2 rows of 32 byte), IWIDTH=4 lo
    move.l  BODY_PH1_LO_VAL, $101C           ; ViHandler refreshes per VBL with XPOS

; STOP object terminator at $1020.
    move.l  #$00000000, $1020
    move.l  #$00000004, $1024

    move.l  #$0FF0, d0
    swap    d0
    move.l  d0, OLP

    move.w  #0, INT2
    move.w  #$FF00, INT1
    nop
    nop
    move.w  #C_VIDENA, INT1
    move.w  #$2000, sr
    ; PWIDTH2 (= 640H) + interlace 480i (VP set in IsNTSC/IsPAL) = 640x480 native pixel quadrati.
    move.w  #VIDEN|BGEN|PWIDTH2|RGB16|CSYNC, VMODE
; S24 DIAG: era `move.w #$0000, BG` (nero). Messo ROSSO per distinguere
; "OPSetup completata" da "bloccati dentro OPSetup" (che resta VERDE).
    move.w  #$8FFF, BG                    ; 9: OPSetup completata (era $F800 = Y=00 = NERO, invisibile!)

; ---------------------------------------------------------------------------
; S24 PoC C blob: eseguito QUI, a video acceso, per poter vedere la diagnostica.
; Lettura del risultato a schermo (barre 32x8 a gradiente, colonna sinistra):
;   schermo nero               = hang nel boot PRE-esistente, non nel codice C
;   scena + barra y=8 sola     = poc_init non e' tornato (crash dentro il C)
;   scena + barre y=8 e y=40   = poc_init e' tornato ma il magic e' sbagliato
;   scena + barre y=8 e y=24   = init C OK -> guardare il quadrato 8x8 a (0,0)
;   quadrato 8x8 che lampeggia = poc_frame gira ogni VBL: PoC PASSATO
;    moveq   #8, d1                     ; S24 OFF
;    bsr     DbgBar                     ; S24 OFF
;    bsr     InitCBlob                  ; S24 OFF
;    cmp.l   #$900DF00D, d0             ; S24 OFF
;    bne.s   .boot_c_badmagic           ; S24 OFF
;    move.b  #1, C_BLOB_OK              ; S24 OFF
;    moveq   #24, d1                    ; S24 OFF
;    bsr     DbgBar                     ; S24 OFF
;    bra.s   .boot_c_done               ; S24 OFF
;.boot_c_badmagic:                      ; S24 OFF
;    moveq   #40, d1                    ; S24 OFF
;    bsr     DbgBar                     ; S24 OFF
;.boot_c_done:                          ; S24 OFF

MainLoop:
; S24 PoC: once per VBL tick call poc_frame(active_fb) in the C blob.
;    tst.b   C_BLOB_OK                  ; S24 OFF
;    beq.s   .ml_no_tick                ; S24 OFF
;    move.l  VBL_TICK, d0               ; S24 OFF
;    cmp.l   LAST_TICK, d0              ; S24 OFF
;    beq.s   .ml_no_tick                ; S24 OFF
;    move.l  d0, LAST_TICK              ; S24 OFF
;    bsr     PocFrameHook               ; S24 OFF
;.ml_no_tick:                           ; S24 OFF
    moveq   #0, d0
    move.b  REDRAW_FLAG, d0
    beq.s   MainLoop

    cmp.b   #5, d0
    beq     .ml_body_redraw
    cmp.b   #1, d0
    beq     .ml_right
    cmp.b   #2, d0
    beq     .ml_left
    cmp.b   #3, d0
    beq     .ml_down
    bra     .ml_up

.ml_body_redraw:
    ; S16 Path A Phase 2 body anchor moved by ViHandler. Camera is fixed
    ; so NEXT_OFFSET = current OFFSET. Re-render scene + body.
    move.w  CAM_OFFSET_X, d1
    move.w  d1, NEXT_OFFSET_X
    move.w  CAM_OFFSET_Y, d1
    move.w  d1, NEXT_OFFSET_Y
    bra     .ml_render

.ml_right:
    move.w  CAM_GX, d1
    addq.w  #1, d1
    cmp.w   #GRID_X, d1
    bge     .ml_right_edge
    move.w  CAM_GZ, d1
    subq.w  #1, d1
    bmi     .ml_right_edge
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
.ml_right_edge:
    moveq   #TBL_TX_RIGHT, d0
    bsr     TryTransition
    bne     .ml_done
    bra     .ml_transition

.ml_left:
    move.w  CAM_GX, d1
    subq.w  #1, d1
    bmi     .ml_left_edge
    move.w  CAM_GZ, d1
    addq.w  #1, d1
    cmp.w   #GRID_Z, d1
    bge     .ml_left_edge
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
.ml_left_edge:
    moveq   #TBL_TX_LEFT, d0
    bsr     TryTransition
    bne     .ml_done
    bra     .ml_transition

.ml_down:
    move.w  CAM_GX, d1
    addq.w  #1, d1
    cmp.w   #GRID_X, d1
    bge     .ml_down_edge
    move.w  CAM_GZ, d1
    addq.w  #1, d1
    cmp.w   #GRID_Z, d1
    bge     .ml_down_edge
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
    bra     .ml_render
.ml_down_edge:
    moveq   #TBL_TX_DOWN, d0
    bsr     TryTransition
    bne     .ml_done
    bra     .ml_transition

.ml_up:
    move.w  CAM_GX, d1
    subq.w  #1, d1
    bmi     .ml_up_edge
    move.w  CAM_GZ, d1
    subq.w  #1, d1
    bmi     .ml_up_edge
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
    bra     .ml_render
.ml_up_edge:
    moveq   #TBL_TX_UP, d0
    bsr     TryTransition
    bne     .ml_done
    bra     .ml_transition

.ml_render:
; S23: body sprite composed by OP layered BMobj. BG-only re-blit on scroll.
; BlitPristineToInactive removed: scroll uses CAM_OFFSET via ViHandler DATA ptr math.
; Body draws removed entirely — OP composes body from BODY_BITMAP, no FB writes.
    bsr     ComputeCamera
    move.w  #$2700, sr
    move.w  NEXT_OFFSET_X, d0
    move.w  d0, CAM_OFFSET_X
    move.w  NEXT_OFFSET_Y, d0
    move.w  d0, CAM_OFFSET_Y
    move.w  #$2000, sr
    bra     .ml_done

.ml_transition:
; NEXT_SCENE_ID has been set by .ml_<dir>_edge via TryTransition.
; Load new scene + render BG + atomic swap. Body sprite invariant across scenes
; (= same BODY_BITMAP), so no body redraw needed on transition.
    move.w  NEXT_SCENE_ID, d0
    bsr     LoadScene                      ; sets CAM_GX/GZ from scene_table
    move.w  #0, NEXT_OFFSET_X              ; reset scroll on transition
    move.w  #0, NEXT_OFFSET_Y
    bsr     ComputeCamera
    bsr     RenderScene                     ; -> BG_PRISTINE (refresh on scene change)
    bsr     BlitPristineToInactive          ; -> BG_INACTIVE full BG (transitions are rare)
    move.w  #$2700, sr
    move.w  NEXT_OFFSET_X, d0
    move.w  d0, CAM_OFFSET_X
    move.w  NEXT_OFFSET_Y, d0
    move.w  d0, CAM_OFFSET_Y
    move.b  #1, PENDING_SWAP                ; S22B defer atomic swap to next field 0
    move.w  #$2000, sr

.ml_done:
    move.b  #0, REDRAW_FLAG
    bra     MainLoop

; ---------------------------------------------------------------------------
; TryTransition: input d0.w = TBL_TX_<dir> offset (8/9/10/11) into scene entry.
; Reads scene_table[CUR_SCENE_ID].tx_<dir> byte. If != $FF: stores in
; NEXT_SCENE_ID and returns Z=1. If == $FF: returns Z=0 (no transition).
; ---------------------------------------------------------------------------
TryTransition:
    movem.l d1/a0/a6, -(sp)
    lea     SceneData, a6
    move.l  HDR_SCENE_TBL_OFF(a6), d1
    lea     0(a6,d1.l), a0
    moveq   #0, d1
    move.w  CUR_SCENE_ID, d1
    lsl.l   #4, d1                         ; * TBL_ENTRY_SIZE (16)
    adda.l  d1, a0                         ; a0 = scene_table[CUR]
    moveq   #0, d1
    move.b  0(a0,d0.w), d1                 ; tx_<dir>
    cmp.b   #$FF, d1
    beq.s   .tt_no
    move.w  d1, NEXT_SCENE_ID
    moveq   #0, d0                         ; Z=1 (success)
    movem.l (sp)+, d1/a0/a6
    rts
.tt_no:
    moveq   #1, d0                         ; Z=0 (no transition)
    movem.l (sp)+, d1/a0/a6
    rts

; ---------------------------------------------------------------------------
; InitBody (V5): cache body 0 (Twinsen) cart pointers + counts.
; Body cart layout: header (20 B) + vertex array (6 B/vertex) + poly array (16 B/poly)
; Header bytes 16-17 = nb_vertices BE; bytes 18-19 = nb_polys BE.
; ---------------------------------------------------------------------------
InitBody:
    movem.l d0-d4/a0-a1/a6, -(sp)
    lea     SceneData, a6
    move.l  HDR_BODY_TBL_OFF(a6), d0
    lea     0(a6,d0.l), a0                 ; a0 = body_table base
    ; S21: scene 0 = Citadel hospital -> body 1 (Twinsen prisoner) not body 0 (mage)
    move.l  4(a0), d0                       ; d0 = body 1 offset within body_blob
    move.l  HDR_BODY_BLOB_OFF(a6), d1
    add.l   d1, d0                          ; cart-relative offset to body 1 header
    lea     0(a6,d0.l), a0                  ; a0 = body 1 header

    moveq   #0, d2
    move.w  16(a0), d2                      ; nb_vertices BE
    move.w  d2, BODY_NB_VTX
    moveq   #0, d3
    move.w  18(a0), d3                      ; nb_polys BE
    move.w  d3, BODY_NB_POLY

    lea     20(a0), a1                      ; vertex array starts at +20
    move.l  a1, BODY_VTX_PTR

    move.w  d2, d4
    mulu.w  #6, d4                          ; vtx_size = nb_v * 6
    adda.l  d4, a1                           ; a1 = poly array base
    move.l  a1, BODY_POLY_PTR

    ; S21: skip sphere overlay for body 1 (prisoner). Body0Spheres blob is mage-
    ; specific (hands+helmet+ornament). DrawBodySphere is no-op when count = 0.
    moveq   #0, d2
    move.w  d2, BODY_NB_SPHERE

    ; S16 Path A Phase 1: init parametric body anchor to legacy display center.
    ; Phase 2 will mutate these from D-pad input.
    move.w  #320, BODY_SX0           ; body anchor X = display center 640H
    move.w  #240, BODY_SY0           ; S20: body anchor Y = display center 480V interlace

    movem.l (sp)+, d0-d4/a0-a1/a6

    ; S13 build 4: one-time z-sort polys for painter back-to-front
    bsr     SortPolysByZ
    rts

; ---------------------------------------------------------------------------
; SortPolysByZ (S13 build 4): one-time boot insertion sort of body 0 poly
; indices by iso depth key. Body T-pose static + camera iso fixed = sort
; valid for entire game runtime.
; Key per poly = sum over first 3 verts of (X + Y + Z). LBA1 iso painter
; convention: ASCENDING key = back-to-front draw order.
; Output: POLY_SORT_BUF[i] = (key WORD signed, poly_idx WORD), i in 0..nb-1.
; DrawBodyFlat reads idx field, dispatches in sorted order.
; ---------------------------------------------------------------------------
SortPolysByZ:
    movem.l d0-d7/a0-a3, -(sp)

    ; --- Pass 1: compute key per poly, write (key, idx) to POLY_SORT_BUF ---
    movea.l BODY_VTX_PTR, a0
    movea.l BODY_POLY_PTR, a1
    movea.l #POLY_SORT_BUF, a2
    moveq   #0, d7
    move.w  BODY_NB_POLY, d7
    subq.w  #1, d7
    bmi     .spz_done

    moveq   #0, d6                          ; poly_idx
.spz_p1:
    moveq   #0, d0                          ; key sum (signed long)
    moveq   #0, d5                          ; vert iter (0..2)
.spz_v:
    move.w  d5, d1
    add.w   d1, d1
    moveq   #0, d2
    move.w  4(a1,d1.w), d2                  ; vidx (BE u16)

    ; vert byte offset = vidx * 6
    move.w  d2, d3
    add.w   d3, d3                          ; vidx*2
    add.w   d2, d3                          ; vidx*3
    add.w   d3, d3                          ; vidx*6
    movea.l a0, a3
    adda.w  d3, a3

    move.w  (a3)+, d4                       ; X
    ext.l   d4
    add.l   d4, d0
    move.w  (a3)+, d4                       ; Y
    ext.l   d4
    add.l   d4, d0
    move.w  (a3), d4                        ; Z
    ext.l   d4
    add.l   d4, d0

    addq.w  #1, d5
    cmp.w   #3, d5
    blt.s   .spz_v

    move.w  d0, (a2)+                       ; key (low 16 bits, signed)
    move.w  d6, (a2)+                       ; poly idx

    adda.l  #16, a1
    addq.w  #1, d6
    dbra    d7, .spz_p1

    ; --- Pass 2: insertion sort POLY_SORT_BUF by key.w ascending ---
    moveq   #0, d7
    move.w  BODY_NB_POLY, d7
    cmp.w   #2, d7
    blt     .spz_done

    moveq   #1, d6                          ; j = 1
.spz_outer:
    cmp.w   d7, d6
    bge     .spz_done

    ; val = buf[j] (long: key | idx)
    move.w  d6, d0
    lsl.w   #2, d0                          ; j*4
    movea.l #POLY_SORT_BUF, a2
    adda.w  d0, a2
    move.l  (a2), d4                        ; d4 = val

    move.l  d4, d5
    swap    d5                              ; d5.w = key_j (signed)
    ext.l   d5

    move.w  d6, d3                          ; i = j-1
    subq.w  #1, d3

.spz_inner:
    tst.w   d3
    bmi.s   .spz_insert

    move.w  d3, d0
    lsl.w   #2, d0
    movea.l #POLY_SORT_BUF, a3
    adda.w  d0, a3
    move.w  (a3), d1                        ; d1.w = key_i
    ext.l   d1

    cmp.l   d5, d1                          ; key_i - key_j
    ble.s   .spz_insert                     ; key_i <= key_j -> stop

    move.l  (a3), 4(a3)                     ; buf[i+1] = buf[i]
    subq.w  #1, d3
    bra.s   .spz_inner

.spz_insert:
    addq.w  #1, d3                          ; insert position = i+1
    move.w  d3, d0
    lsl.w   #2, d0
    movea.l #POLY_SORT_BUF, a3
    adda.w  d0, a3
    move.l  d4, (a3)

    addq.w  #1, d6
    bra     .spz_outer

.spz_done:
    movem.l (sp)+, d0-d7/a0-a3
    rts

; ---------------------------------------------------------------------------
; InitBodyBitmap (S23): pre-render body 1 T-pose into BODY_BITMAP for sprite-
; based OP composition. Renders body via existing DrawBodyFlat + DrawBodySphere
; into BG_PRISTINE (= temporary canvas, restored later by RenderScene), then
; copies the 128x192 region around the body anchor into BODY_BITMAP.
; Idx 0 in BG_PRISTINE = transparent (body BMobj sets TRANS=1).
; Called once at boot AFTER InitBody + InitGpu + BEFORE RenderScene.
; ---------------------------------------------------------------------------
InitBodyBitmap:
    movem.l d0-d7/a0-a2, -(sp)

    ; --- Save body anchor ---
    move.w  BODY_SX0, BODY_ANCHOR_SAVE_X
    move.w  BODY_SY0, BODY_ANCHOR_SAVE_Y

    ; --- Set anchor for sprite-frame render ---
    ;     BODY_SX0 = 320 = horizontal center of BG_PRISTINE 640-wide
    ;     BODY_SY0 = BODY_SPRITE_CY = 96 = vertical center of 192-row sub-region
    ;     => body bitmap rendered with center at (320, 96), top-left at (256, 0)
    move.w  #320, BODY_SX0
    move.w  #BODY_SPRITE_CY, BODY_SY0

    ; --- Project vertices with new anchor ---
    bsr     TransformBody

    ; --- Set draw target = BG_PRISTINE (override ACTIVE_BG_ID) ---
    move.l  #BG_PRISTINE, BODY_DRAW_TARGET

    ; --- Clear top 192 rows of BG_PRISTINE (640*192 = 122880 byte = $1E000) ---
    movea.l #BG_PRISTINE, a0
    move.l  #(640*192/4)-1, d0
    moveq   #0, d1
.ibb_clr:
    move.l  d1, (a0)+
    dbra    d0, .ibb_clr

    ; --- Render body to BG_PRISTINE via existing pipeline ---
    bsr     DrawBodyFlat
    bsr     DrawBodySphere

    ; --- Copy BODY_BITMAP_W x BODY_BITMAP_H region from BG_PRISTINE to BODY_BITMAP ---
    ;     src offset = (320 - BODY_SPRITE_CX) = body sprite left-edge in BG_PRISTINE
    ;     src stride = 640 (BG_W); dst stride = BODY_BITMAP_W
    movea.l #BG_PRISTINE+(320-BODY_SPRITE_CX), a0
    movea.l #BODY_BITMAP, a1
    move.w  #BODY_BITMAP_H-1, d7
.ibb_row:
    move.w  #(BODY_BITMAP_W/4)-1, d6
.ibb_col:
    move.l  (a0)+, (a1)+
    dbra    d6, .ibb_col
    adda.l  #BG_W-BODY_BITMAP_W, a0
    dbra    d7, .ibb_row

    ; --- Clear draw target override + restore body anchor ---
    move.l  #0, BODY_DRAW_TARGET
    move.w  BODY_ANCHOR_SAVE_X, BODY_SX0
    move.w  BODY_ANCHOR_SAVE_Y, BODY_SY0

    movem.l (sp)+, d0-d7/a0-a2
    rts

; ---------------------------------------------------------------------------
; TransformBody (V5 + S16 Path A): iterate body 0 vertices, iso-project to
; (sx, sy) WORD pairs in VERT_LIST. Anchor parametrico via BODY_SX0/SY0
; (boot init = 160,100; runtime mutated by ViHandler in Phase 2).
; Iso projection LBA1: sx_off = (X-Z)*24 / 512, sy_off = ((X+Z)*12 - Y*30) / 512
; Vertices are signed BE i16 X, Y, Z (post-rest-pose-transform from packer).
; ---------------------------------------------------------------------------
TransformBody:
    movem.l d0-d6/a0-a1, -(sp)
    movea.l BODY_VTX_PTR, a0
    movea.l #VERT_LIST, a1
    move.w  BODY_SX0, d6                    ; cache anchor X (signed)
    ext.l   d6
    moveq   #0, d7
    move.w  BODY_NB_VTX, d7
    subq.w  #1, d7
    bmi.s   .tb_done
.tb_loop:
    move.w  (a0)+, d0                       ; X (signed BE)
    move.w  (a0)+, d1                       ; Y
    move.w  (a0)+, d2                       ; Z

    ; sx = (X - Z) * 48 / 512 + BODY_SX0   (S18 B: mult 24 -> 48 X scale 2x)
    move.w  d0, d3
    sub.w   d2, d3
    muls.w  #48, d3
    asr.l   #8, d3
    asr.l   #2, d3                           ; X total /1024 (S18 baseline preserved)
    add.l   d6, d3                           ; + body_screen_x

    ; sy = ((X+Z)*12 - Y*30) / 1024 + BODY_SY0   (S18 B halve: divisor /512 -> /1024 = body Y 50%)
    move.w  d0, d4
    add.w   d2, d4
    muls.w  #12, d4
    move.w  d1, d5
    muls.w  #30, d5
    sub.l   d5, d4                            ; d4 = (X+Z)*12 - Y*30
    asr.l   #8, d4
    asr.l   #1, d4                          ; S20: Y total /512 (was /1024 S18, doubled to match 480V interlace = body aspect 1:1 preserved)
    move.w  BODY_SY0, d5                    ; reuse d5: signed anchor Y
    ext.l   d5
    add.l   d5, d4                           ; + body_screen_y

    move.w  d3, (a1)+                        ; sx
    move.w  d4, (a1)+                        ; sy
    dbra    d7, .tb_loop
.tb_done:
    movem.l (sp)+, d0-d6/a0-a1
    rts

; ---------------------------------------------------------------------------
; DrawBodyDots (V5 V0 scaffolding): plot each vertex as 1-pixel dot on the
; inactive BG (= where RenderScene wrote to = ~ACTIVE_BG_ID).
; Replaces F4 GPU rasterizer: validates iso projection only.
; Color = BODY_DOT_COLOR palette idx; bounds-clipped to BG_W x BG_H.
; ---------------------------------------------------------------------------
DrawBodyDots:
    movem.l d0-d3/d7/a0-a1, -(sp)

    ; Target = inactive BG (= ~ACTIVE_BG_ID = same buffer as RenderScene wrote)
    moveq   #0, d0
    move.b  ACTIVE_BG_ID, d0
    bne.s   .dbd_target_a                    ; ACTIVE=B → target A
    movea.l #BG_B, a0                        ; ACTIVE=A → target B
    bra.s   .dbd_target_done
.dbd_target_a:
    movea.l #BG_A, a0
.dbd_target_done:

    movea.l #VERT_LIST, a1
    moveq   #0, d7
    move.w  BODY_NB_VTX, d7
    subq.w  #1, d7
    bmi.s   .dbd_done
.dbd_loop:
    move.w  (a1)+, d0                        ; sx (signed)
    move.w  (a1)+, d1                        ; sy

    ; Bounds check
    tst.w   d0
    bmi.s   .dbd_skip
    cmp.w   #BG_W, d0
    bge.s   .dbd_skip
    tst.w   d1
    bmi.s   .dbd_skip
    cmp.w   #BG_H, d1
    bge.s   .dbd_skip

    ; pixel_addr = BG + sy*640 + sx
    moveq   #0, d2
    move.w  d1, d2
    mulu.w  #BG_W, d2
    moveq   #0, d3
    move.w  d0, d3
    add.l   d3, d2

    move.b  #BODY_DOT_COLOR, 0(a0,d2.l)
.dbd_skip:
    dbra    d7, .dbd_loop
.dbd_done:
    movem.l (sp)+, d0-d3/d7/a0-a1
    rts

; ---------------------------------------------------------------------------
; DrawLine (V0.5 wireframe scaffolding): Bresenham line from (x0,y0) to (x1,y1)
; with bounds-clipped per-pixel writes to BG buffer.
; Input: d0=x0, d1=y0, d2=x1, d3=y1, d4.b=color, a0=BG_base
; Clobbers: d0-d7, a1; preserves a0; uses BR_* DRAM scratch.
; ---------------------------------------------------------------------------
DrawLine:
    movem.l d0-d7/a1, -(sp)

    ; dx = |x1-x0|, sx = sign
    move.w  d2, d5
    sub.w   d0, d5
    bge.s   .dl_dx_pos
    neg.w   d5
    move.w  #-1, BR_SX
    bra.s   .dl_dx_done
.dl_dx_pos:
    move.w  #1, BR_SX
.dl_dx_done:
    ext.l   d5
    move.l  d5, BR_DX

    ; dy_neg = -|y1-y0|, sy = sign
    move.w  d3, d6
    sub.w   d1, d6
    bge.s   .dl_dy_pos
    neg.w   d6
    move.w  #-1, BR_SY
    bra.s   .dl_dy_done
.dl_dy_pos:
    move.w  #1, BR_SY
.dl_dy_done:
    ext.l   d6
    neg.l   d6
    move.l  d6, BR_DY_NEG

    ; err = dx + dy_neg
    move.l  BR_DX, d7
    add.l   BR_DY_NEG, d7
    move.l  d7, BR_ERR

.dl_loop:
    ; Bounds check (skip plot if outside BG)
    tst.w   d0
    bmi.s   .dl_skip_plot
    cmp.w   #BG_W, d0
    bge.s   .dl_skip_plot
    tst.w   d1
    bmi.s   .dl_skip_plot
    cmp.w   #BG_H, d1
    bge.s   .dl_skip_plot

    ; Plot: BG + y*640 + x = color
    moveq   #0, d5
    move.w  d1, d5
    mulu.w  #BG_W, d5
    moveq   #0, d6
    move.w  d0, d6
    add.l   d6, d5
    move.b  d4, 0(a0,d5.l)

.dl_skip_plot:
    ; Termination: x0==x1 && y0==y1
    cmp.w   d2, d0
    bne.s   .dl_step
    cmp.w   d3, d1
    beq     .dl_done
.dl_step:
    ; e2 = 2 * err (signed)
    move.l  BR_ERR, d5
    add.l   d5, d5

    ; if e2 >= dy_neg: err += dy_neg; x += sx
    cmp.l   BR_DY_NEG, d5
    blt.s   .dl_skip_x
    move.l  BR_ERR, d6
    add.l   BR_DY_NEG, d6
    move.l  d6, BR_ERR
    add.w   BR_SX, d0
.dl_skip_x:

    ; if e2 <= dx: err += dx; y += sy
    cmp.l   BR_DX, d5
    bgt.s   .dl_skip_y
    move.l  BR_ERR, d6
    add.l   BR_DX, d6
    move.l  d6, BR_ERR
    add.w   BR_SY, d1
.dl_skip_y:

    bra     .dl_loop

.dl_done:
    movem.l (sp)+, d0-d7/a1
    rts

; ---------------------------------------------------------------------------
; DrawBodyWireframe (V0.5 scaffolding): per body 0 poly, draw nbp Bresenham
; edges between consecutive vertices (wrap last->first). Color by material
; bucket. Replaces DrawBodyDots; validates poly dispatch + vertex connectivity
; before transitioning to GPU TOM flat rasterizer (F4).
; ---------------------------------------------------------------------------
DrawBodyWireframe:
    movem.l d0-d7/a0-a3, -(sp)

    ; Target = inactive BG (= ~ACTIVE)
    moveq   #0, d0
    move.b  ACTIVE_BG_ID, d0
    bne.s   .dwf_t_a
    movea.l #BG_B, a3
    bra.s   .dwf_t_done
.dwf_t_a:
    movea.l #BG_A, a3
.dwf_t_done:

    movea.l BODY_POLY_PTR, a2
    move.w  BODY_NB_POLY, d7
    subq.w  #1, d7
    bmi     .dwf_done

.dwf_poly_loop:
    ; Color from material bucket (poly[0] = mat byte)
    moveq   #0, d4
    move.b  (a2), d4
    cmp.b   #9, d4
    bge.s   .dwf_col_g
    cmp.b   #7, d4
    bge.s   .dwf_col_f
    move.b  #WIRE_PATTERN_COLOR, d4
    bra.s   .dwf_col_done
.dwf_col_f:
    move.b  #WIRE_FLAT_COLOR, d4
    bra.s   .dwf_col_done
.dwf_col_g:
    move.b  #WIRE_GOURAUD_COLOR, d4
.dwf_col_done:

    ; nbp_minus_1 in d6 (poly[1] = nbp byte)
    moveq   #0, d6
    move.b  1(a2), d6
    subq.w  #1, d6
    bmi     .dwf_next_poly                  ; nbp == 0: skip degenerate

    ; Edge loop: edge_iter d5 from 0 to nbp_minus_1
    moveq   #0, d5
.dwf_edge_loop:
    ; v0_idx = poly[4 + edge_iter*2] BE
    move.w  d5, d0
    add.w   d0, d0
    move.w  4(a2,d0.w), d0                  ; d0 = v0_idx
    add.w   d0, d0
    add.w   d0, d0                           ; d0 = v0_idx * 4
    movea.l #VERT_LIST, a1
    adda.w  d0, a1
    move.w  (a1)+, d0                        ; d0 = sx0
    move.w  (a1), d1                         ; d1 = sy0

    ; v1_idx = poly[4 + ((edge_iter+1) % nbp) * 2]
    move.w  d5, d2
    cmp.w   d6, d2
    bne.s   .dwf_no_wrap
    moveq   #0, d2                           ; wrap to vertex 0
    bra.s   .dwf_wrap_done
.dwf_no_wrap:
    addq.w  #1, d2
.dwf_wrap_done:
    add.w   d2, d2
    move.w  4(a2,d2.w), d2                   ; d2 = v1_idx
    add.w   d2, d2
    add.w   d2, d2
    movea.l #VERT_LIST, a1
    adda.w  d2, a1
    move.w  (a1)+, d2                        ; d2 = sx1
    move.w  (a1), d3                         ; d3 = sy1

    ; d0=sx0, d1=sy0, d2=sx1, d3=sy1, d4=color
    movea.l a3, a0
    bsr     DrawLine

    addq.w  #1, d5
    cmp.w   d6, d5
    ble     .dwf_edge_loop

.dwf_next_poly:
    adda.l  #16, a2                          ; advance to next poly record
    dbra    d7, .dwf_poly_loop

.dwf_done:
    movem.l (sp)+, d0-d7/a0-a3
    rts

; ---------------------------------------------------------------------------
; DrawBodyFlat (S13 F4.3-alpha): per body 0 poly, dispatch GpuRasterMain to
; flat-fill on inactive BG. Color = poly.coul1 (all 3 mat buckets degraded
; to flat for now). Uses VERT_LIST (post-TransformBody screen coords) -> Q16.16
; in IN_BUF, then 1 RISCGO per poly + busy-wait DONE_E.
; ---------------------------------------------------------------------------
DrawBodyFlat:
    movem.l d0-d7/a0-a5, -(sp)

    ; Target FB = inactive BG (= ~ACTIVE) -> d6 (preserved across loop).
    ; S23: BODY_DRAW_TARGET override (!=0 = direct FB base, used by InitBodyBitmap).
    move.l  BODY_DRAW_TARGET, d6
    bne.s   .dbf_t_done
    moveq   #0, d0
    move.b  ACTIVE_BG_ID, d0
    bne.s   .dbf_t_a
    move.l  #BG_B, d6
    bra.s   .dbf_t_done
.dbf_t_a:
    move.l  #BG_A, d6
.dbf_t_done:

    ; S13 build 4: iterate via POLY_SORT_BUF (back-to-front painter order)
    movea.l #POLY_SORT_BUF, a4
    move.w  BODY_NB_POLY, d7
    subq.w  #1, d7
    bmi     .dbf_done

.dbf_poly_loop:
    ; Read next sorted entry: skip key word, fetch poly_idx
    addq.l  #2, a4
    moveq   #0, d0
    move.w  (a4)+, d0                       ; d0 = poly_idx
    movea.l BODY_POLY_PTR, a2
    lsl.w   #4, d0                          ; idx * 16
    adda.w  d0, a2                          ; a2 = &poly[idx]

    ; nbp = poly[1] (degenerate if < 3, skip)
    moveq   #0, d5
    move.b  1(a2), d5
    cmp.b   #3, d5
    blt     .dbf_next_poly

    ; S13 build 3: back-face cull. Cross of first 3 verts in screen-space.
    ; cross = (sx1-sx0)*(sy2-sy0) - (sy1-sy0)*(sx2-sx0)
    ; Y-down screen + LBA1 CCW math winding => front when cross < 0.
    ; Convention assumed; flip "bge -> ble" if Twinsen renders inverted.
    moveq   #0, d0
    move.w  4(a2), d0
    lsl.w   #2, d0
    movea.l #VERT_LIST, a1
    adda.w  d0, a1
    move.w  (a1)+, d0                          ; sx0
    move.w  (a1), d1                           ; sy0

    moveq   #0, d2
    move.w  6(a2), d2
    lsl.w   #2, d2
    movea.l #VERT_LIST, a1
    adda.w  d2, a1
    move.w  (a1)+, d2
    sub.w   d0, d2                             ; d2 = dx1
    move.w  (a1), d3
    sub.w   d1, d3                             ; d3 = dy1

    moveq   #0, d4
    move.w  8(a2), d4
    lsl.w   #2, d4
    movea.l #VERT_LIST, a1
    adda.w  d4, a1
    move.w  (a1)+, d4
    sub.w   d0, d4                             ; d4 = dx2
    moveq   #0, d0
    move.w  (a1), d0
    sub.w   d1, d0                             ; d0 = dy2

    muls.w  d2, d0                             ; d0.l = dx1 * dy2
    muls.w  d3, d4                             ; d4.l = dy1 * dx2
    sub.l   d4, d0                             ; d0.l = cross
    bge     .dbf_next_poly                     ; cross >= 0 -> back/degenerate, skip

    ; coul1 = poly[2] (flat color)
    moveq   #0, d4
    move.b  2(a2), d4

    ; S14 B2: setup a5 = &intensity[poly_idx*4]. Recover poly_idx from a2.
    ; poly_byte_off = a2 - BODY_POLY_PTR = poly_idx * 16 (poly stride).
    ; intensity stride = 4 byte/poly, so >> 2 to get poly_idx*4.
    ; S21: switched Body0Intensity -> Body1Intensity (prisoner). 230-poly blob.
    movea.l BODY_POLY_PTR, a1
    move.l  a2, d0
    sub.l   a1, d0                            ; d0 = poly_idx * 16
    lsr.l   #2, d0                            ; d0 = poly_idx * 4
    movea.l #Body1Intensity, a5
    adda.l  d0, a5                            ; a5 = &intensity_slot[poly_idx]

    ; --- Build IN_BUF[i] = (sx<<16, sy<<16) for i in 0..nbp-1 ---
    ; S14 B2: read per-corner intensity 4-bit from blob, write Q16.16 to INTENSITY_BUF.
    movea.l #GPU_ARG_IN_BUF, a3
    movea.l #GPU_ARG_INTENSITY_BUF, a0
    moveq   #0, d3
.dbf_v_loop:
    ; vidx = poly[4 + d3*2] BE u16
    move.w  d3, d0
    add.w   d0, d0
    moveq   #0, d1
    move.w  4(a2,d0.w), d1

    ; VERT_LIST entry @ vidx*4: WORD sx, WORD sy
    add.w   d1, d1
    add.w   d1, d1                            ; vidx * 4
    movea.l #VERT_LIST, a1
    adda.w  d1, a1

    ; sx_q = sx << 16  (signed sx in word; swap+clr.w preserves sign in hi)
    moveq   #0, d0
    move.w  (a1)+, d0
    swap    d0
    clr.w   d0
    move.l  d0, (a3)+

    ; sy_q = sy << 16
    moveq   #0, d0
    move.w  (a1), d0
    swap    d0
    clr.w   d0
    move.l  d0, (a3)+

    ; S14 B2: per-corner intensity from blob. Slot byte k = 4-bit shade (0..15).
    ; swap d2 = intensity << 16 (Q16.16), e.g. shade 8 -> $00080000.
    moveq   #0, d2
    move.b  (a5)+, d2
    swap    d2
    move.l  d2, (a0)+

    addq.w  #1, d3
    cmp.w   d5, d3
    blt.s   .dbf_v_loop

    ; --- GpuRasterMain args ---
    move.l  d5, GPU_RASTER_POLY_COUNT
    move.l  d6, GPU_RASTER_FB_BASE
    move.l  d4, GPU_RASTER_COLOR
    moveq   #0, d0
    move.l  d0, GPU_RASTER_DONE_E
    move.l  d0, GPU_RASTER_EDGE_COUNT

    ; --- Launch GPU ---
    move.l  d0, G_FLAGS
    move.l  #$00070007, G_END
    move.l  #GpuRasterMain, G_PC
    move.l  #RISCGO, G_CTRL

.dbf_wait:
    move.l  GPU_RASTER_DONE_E, d0
    beq.s   .dbf_wait

    moveq   #0, d0
    move.l  d0, G_CTRL

.dbf_next_poly:
    dbra    d7, .dbf_poly_loop

.dbf_done:
    movem.l (sp)+, d0-d7/a0-a5
    rts

; ---------------------------------------------------------------------------
; DrawBodySphere (S15 F4.5 V0): render body 0 sphere section as flat-color
; disks on inactive BG. Reads (sx, sy) from VERT_LIST for each sphere center,
; uses pre-computed half-width LUT (DiskHwLUT) for per-row span.
; Input: VERT_LIST populated by TransformBody. BODY_NB_SPHERE / BODY_SPHERE_PTR
; cached at boot by InitBody.
; Output: pixels written to inactive BG with palette idx = sphere.coul1.
; V0 caveats: ignores coul2/mat (always flat), bbox-clip (skip if any edge off
; viewport), no z-sort with poly painter (overlay drawn after DrawBodyFlat).
; ---------------------------------------------------------------------------
DrawBodySphere:
    movem.l d0-d7/a0-a4, -(sp)

    ; Target FB = inactive BG (= ~ACTIVE_BG_ID = where this frame is composed).
    ; S23: BODY_DRAW_TARGET override (!=0 = direct FB base, used by InitBodyBitmap).
    move.l  BODY_DRAW_TARGET, d0
    beq.s   .dbs_target_active
    movea.l d0, a0
    bra.s   .dbs_target_done
.dbs_target_active:
    moveq   #0, d0
    move.b  ACTIVE_BG_ID, d0
    bne.s   .dbs_target_a
    movea.l #BG_B, a0
    bra.s   .dbs_target_done
.dbs_target_a:
    movea.l #BG_A, a0
.dbs_target_done:

    ; Sphere iteration setup
    movea.l BODY_SPHERE_PTR, a1
    moveq   #0, d7
    move.w  BODY_NB_SPHERE, d7
    subq.w  #1, d7
    bmi     .dbs_done

.dbs_loop:
    ; Read 8-byte sphere descriptor
    moveq   #0, d4
    move.b  (a1)+, d4                       ; coul1
    moveq   #0, d5
    move.b  (a1)+, d5                       ; rs
    moveq   #0, d2
    move.w  (a1)+, d2                       ; vert_off (BE WORD)
    addq.l  #4, a1                           ; skip reserved long

    ; Defensive rs clamp 0..15
    cmp.w   #15, d5
    ble.s   .dbs_rs_ok
    moveq   #15, d5
.dbs_rs_ok:
    tst.w   d5
    beq     .dbs_next                       ; rs=0: degenerate, skip

    ; Read center (sx, sy) from VERT_LIST + vert_off
    movea.l #VERT_LIST, a2
    adda.l  d2, a2
    move.w  (a2)+, d0                       ; sx (signed)
    move.w  (a2), d1                        ; sy (signed)

    ; Conservative bbox clip: skip if any disk edge off viewport
    move.w  d0, d3
    sub.w   d5, d3                           ; sx - rs
    bmi     .dbs_next
    move.w  d0, d3
    add.w   d5, d3                           ; sx + rs
    cmp.w   #BG_W, d3
    bge     .dbs_next
    move.w  d1, d3
    sub.w   d5, d3                           ; sy - rs
    bmi     .dbs_next
    move.w  d1, d3
    add.w   d5, d3                           ; sy + rs
    cmp.w   #BG_H, d3
    bge     .dbs_next

    ; LUT row base: a3 = DiskHwLUT + rs*16
    lea     DiskHwLUT, a3
    move.w  d5, d3
    lsl.w   #4, d3
    adda.w  d3, a3

    ; dy iterator: d6 = -rs..+rs
    move.w  d5, d6
    neg.w   d6

.dbs_dy_loop:
    ; |dy| -> d3
    move.w  d6, d3
    bge.s   .dbs_abs_done
    neg.w   d3
.dbs_abs_done:
    moveq   #0, d2
    move.b  0(a3, d3.w), d2                 ; d2 = hw

    ; row_y = sy + dy
    move.w  d1, d3
    add.w   d6, d3
    ext.l   d3
    mulu.w  #BG_W, d3                        ; row_y * 640 -> long
    movea.l a0, a4
    adda.l  d3, a4                           ; FB + row_y*640

    ; xL = sx - hw -> a4 += xL
    move.w  d0, d3
    sub.w   d2, d3
    adda.w  d3, a4                           ; FB + row_y*640 + (sx-hw)

    ; pixel_count = 2*hw + 1, dbra adjust = 2*hw
    move.w  d2, d3
    add.w   d3, d3                           ; 2*hw

.dbs_pix_loop:
    move.b  d4, (a4)+
    dbra    d3, .dbs_pix_loop

    addq.w  #1, d6
    cmp.w   d5, d6
    ble.s   .dbs_dy_loop                    ; while dy <= rs

.dbs_next:
    dbra    d7, .dbs_loop

.dbs_done:
    movem.l (sp)+, d0-d7/a0-a4
    rts

BadCart:
    move.l  #$F800F800, BORD1             ; magenta = our magic-check failed (NOT BIOS red!)
.bc_loop:
    bra.s   .bc_loop

; GdbiosFail kept as stub for V5 reintroduction (currently unreachable).
GdbiosFail:
    move.l  #$0000F800, BORD1             ; green border = JagGD firmware too old
.gd_loop:
    bra.s   .gd_loop

; ---------------------------------------------------------------------------
; DecompCol (V3.4.4 unchanged)
; ---------------------------------------------------------------------------
DecompCol:
    moveq   #0, d0
    move.b  (a2)+, d0
    subq.w  #1, d0
    bmi.s   .dc_done
.dc_entity:
    move.b  (a2)+, d1
    move.w  d1, d2
    and.w   #$3F, d2
    addq.w  #1, d2
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
; ViHandler (V4.1: D-pad scroll, edge transition driven by MainLoop)
; ---------------------------------------------------------------------------
ViHandler:
    movem.l d0-d4, -(sp)
    move.w  #$FFFF, BG                    ; 10 BIANCO: entrato in ViHandler

;    addq.l  #1, VBL_TICK               ; S24 OFF

    tst.b   REDRAW_FLAG
    bne     .vh_op_refresh

    move.w  #$F1FE, JOYSTICK
    nop
    nop
    nop
    nop
    move.w  JOYSTICK, d1

    moveq   #0, d0

    btst    #8, d1
    bne.s   .vh_no_up
    move.w  BODY_SY0, d2
    cmp.w   #BODY_MIN_Y, d2
    ble.s   .vh_no_up
    subq.w  #1, BODY_SY0
    moveq   #1, d0
.vh_no_up:
    btst    #9, d1
    bne.s   .vh_no_dn
    move.w  BODY_SY0, d2
    sub.w   #BODY_MAX_Y, d2
    bpl.s   .vh_no_dn
    addq.w  #1, BODY_SY0
    moveq   #1, d0
.vh_no_dn:
    btst    #10, d1
    bne.s   .vh_no_lf
    move.w  BODY_SX0, d2
    cmp.w   #BODY_MIN_X, d2
    ble.s   .vh_no_lf
    subq.w  #2, BODY_SX0
    moveq   #1, d0
.vh_no_lf:
    btst    #11, d1
    bne.s   .vh_no_rt
    move.w  BODY_SX0, d2
    sub.w   #BODY_MAX_X, d2
    bpl.s   .vh_no_rt
    addq.w  #2, BODY_SX0
    moveq   #1, d0
.vh_no_rt:

    tst.b   d0
    beq.s   .vh_op_refresh
    move.b  #5, REDRAW_FLAG

.vh_op_refresh:
    move.w  VC, d2                          ; S22B field id (bit 11 = 0/1) read after D-pad consumed d1
    ; S22B atomic swap committed only at field 0 boundary so display stays
    ; coherent across both fields of a frame. MainLoop sets PENDING_SWAP=1.
    btst    #11, d2
    bne.s   .vh_no_swap
    tst.b   PENDING_SWAP
    beq.s   .vh_no_swap
    move.b  ACTIVE_BG_ID, d1
    not.b   d1
    andi.b  #1, d1
    move.b  d1, ACTIVE_BG_ID
    clr.b   PENDING_SWAP
.vh_no_swap:

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

    ; S22B per-field DATA + YPOS LSB deterministic via VC bit 11.
    ; Was bchg blind (race-prone vs sub-field timing). Now reads HW field id.
    btst    #11, d2
    beq.s   .vh_even_field
    add.l   #640, d0                        ; field 1 DATA += 1 FB line
    bset.b  #3, OP_PH0_LO_VAL+3             ; field 1 YPOS LSB = 1 (odd)
    bra.s   .vh_field_done
.vh_even_field:
    bclr.b  #3, OP_PH0_LO_VAL+3             ; field 0 YPOS LSB = 0 (even)
.vh_field_done:

    lsl.l   #8, d0
    or.l    #$00000002, d0

    move.l  d0, $1000
    move.l  OP_PH0_LO_VAL, $1004
    move.l  #$00000045, $1008                ; S22B clear bit48 RELEASE OP holds bus prevent Blitter contention
    move.l  #$0280B000, $100C

    ; --- S23 body BMobj refresh ($1010-$101F) ---
    ; Body PH0 HI: DATA = BODY_BITMAP (even field) or BODY_BITMAP+32 (odd field).
    ; Sprite is 32 byte/row -> 1-row offset for odd field = 32 byte = 4 DATA-units.
    ; Long bit 11 (= DATA bit 0) -> +$0800 per DATA-unit -> 4 units = +$2000.
    btst    #11, d2
    beq.s   .vh_body_data_even
    move.l  #$1F502002, $1010                ; odd field: DATA = BODY_BITMAP + 32
    bra.s   .vh_body_data_done
.vh_body_data_even:
    move.l  #$1F500002, $1010                ; even field: DATA = BODY_BITMAP
.vh_body_data_done:

    ; Body PH0 LO: YPOS = VDB + 2*(BODY_SY0 - BODY_SPRITE_CY) (signed -> unsigned 11-bit)
    ; merged with HEIGHT=192 + LINK lo + per-field YPOS LSB.
    moveq   #0, d3
    move.w  BODY_SY0, d3
    sub.w   #BODY_SPRITE_CY, d3
    ext.l   d3
    add.l   d3, d3                          ; *2 (halflines)
    moveq   #0, d4
    move.w  VDB_VAL, d4
    add.l   d4, d3                          ; YPOS_halfline
    and.l   #$7FF, d3                       ; 11-bit field
    lsl.l   #3, d3                          ; bits 3-13

    move.l  BODY_PH0_LO_VAL, d4
    and.l   #$FFFFC007, d4                  ; clear YPOS bits 3-13 (mask = ~$3FF8)
    or.l    d3, d4

    btst    #11, d2
    beq.s   .vh_body_yp_even
    bset.l  #3, d4                          ; odd field: YPOS LSB = 1
.vh_body_yp_even:
    move.l  d4, $1014

    ; Body PH1 LO: XPOS = BODY_SX0 - BODY_SPRITE_CX (signed 12-bit)
    moveq   #0, d3
    move.w  BODY_SX0, d3
    sub.w   #BODY_SPRITE_CX, d3
    and.l   #$FFF, d3                       ; 12-bit XPOS field

    move.l  BODY_PH1_LO_VAL, d4
    and.l   #$FFFFF000, d4                  ; clear XPOS bits 0-11
    or.l    d3, d4
    move.l  d4, $101C

    move.w  #C_VIDENA, INT1
    move.w  #$07FC, BG                    ; 11 CIANO: ViHandler arrivata in fondo
    movem.l (sp)+, d0-d4
IrqRte:
    rte

; ---------------------------------------------------------------------------
; S24 DIAG: sonda su tutti i vettori tranne $100. Qualunque eccezione o
; interrupt che NON finisce in ViHandler atterra qui e blocca su MAGENTA.
; Serve a distinguere "l'interrupt e' arrivato altrove" da "non e' arrivato".
; Magenta stabile = sonda (il milestone 3 usa lo stesso colore ma viene
; subito sovrascritto dai milestone 4-8, quindi non e' ambiguo).
; La sonda LAMPEGGIA invece di usare un colore fisso: in CRY16 il byte basso
; e' l'intensita', quindi valori scelti a occhio collidono di cromaticita'
; ($F87C e $F8FC sono lo stesso colore a due luminosita') e "che colore e'"
; non e' un segnale affidabile. Uno schermo che PULSA lo e': nient'altro nel
; programma lampeggia, quindi lampeggio = sonda scattata, punto.
ProbeIllegal:
    moveq   #1, d1                        ; $10 Illegal Instruction
    bra.s   ProbeCommon
; $0C Address Error. Il frame di gruppo 0 del 68000 e':
;   sp+0 special status word | sp+2 indirizzo accesso (long)
;   sp+6 instruction register | sp+8 SR | sp+10 PC (long)
; Misure accumulate su questa eccezione:
;   - PC nel blocco $802800-$802BFF (quello che contiene OPSetup e MainLoop)
;   - indirizzo dell'accesso fallito nella CARTUCCIA ($800000-$EFFFFF)
; Quindi: fetch di istruzione a indirizzo DISPARI, cioe' un salto non allineato.
; Escluso staticamente: nessun dc.b nel progetto (nessuna etichetta dispari),
; nessun jmp/jsr (il flusso e' solo bsr/rts + branch relativi), movem bilanciati.
; Resta un rts con indirizzo di ritorno corrotto.
; Indirizzo dispari localizzato in $802880-$8028BF, cioe' DENTRO
; SyncBodyStripToActive, che pero' e' bilanciata: il salto ci e' solo atterrato
; in mezzo a un'istruzione, non e' lei a generarlo.
; Ipotesi ora sotto test: ViHandler non azzera l'interrupt pendente (scrive
; C_VIDENA senza C_VIDCLR e non tocca mai INT2), quindi al rte rientra subito.
; Ogni rientro impila 6 byte di frame + 20 di movem e non torna mai: lo stack
; scende senza fine, arriva a sovrascrivere OP list e tabella vettori, e da
; quel momento un interrupt salta a un indirizzo spazzatura (dispari).
; Misuriamo quindi DOVE e' arrivato lo stack pointer:
;   1 lampo = >= $1FFF00  stack sano -> corruzione isolata, storm escluso
;   2 lampi = >= $1FF000  cresciuto di centinaia di byte
;   3 lampi = >= $1F0000  cresciuto di decine di KB -> storm in corso
;   4 lampi = >= $100000  fuggito di centinaia di KB
;   5 lampi = sotto $100000 -> stack in fuga totale, storm confermato
ProbeAddrErr:
    move.l  sp, d3                        ; dove e' arrivato lo stack pointer
    moveq   #1, d1
    cmp.l   #$1FFF00, d3
    bcc     ProbeCommon                   ; stack sano (partiva da $200000)
    moveq   #2, d1
    cmp.l   #$1FF000, d3
    bcc     ProbeCommon                   ; cresciuto di qualche centinaio di byte
    moveq   #3, d1
    cmp.l   #$1F0000, d3
    bcc     ProbeCommon                   ; cresciuto di decine di KB
    moveq   #4, d1
    cmp.l   #$100000, d3
    bcc     ProbeCommon                   ; fuggito di centinaia di KB
    moveq   #5, d1                        ; fuggito del tutto / wrap
ProbeBusErr:
    moveq   #3, d1                        ; $08 Bus Error
    bra.s   ProbeCommon
ProbePriv:
    moveq   #4, d1                        ; $20 Privilege Violation
    bra.s   ProbeCommon
ProbeLineF:
    moveq   #5, d1                        ; $2C Line-F ($Fxxx)
    bra.s   ProbeCommon
ProbeLineA:
    moveq   #6, d1                        ; $28 Line-A ($Axxx)
    bra.s   ProbeCommon
ProbeDiv0:
    moveq   #7, d1                        ; $14 Divide by Zero
    bra.s   ProbeCommon
ProbeChk:
    moveq   #8, d1                        ; $18 CHK
    bra.s   ProbeCommon
ProbeTrapV:
    moveq   #9, d1                        ; $1C TRAPV
    bra.s   ProbeCommon
ProbeTrace:
    moveq   #10, d1                       ; $24 Trace
    bra.s   ProbeCommon
ProbeOther:
    moveq   #20, d1                       ; vettore != $0C (era 15, collideva con .pae_wild)

; Lampeggia d1 volte, pausa lunga, ripete. Non torna mai.
ProbeCommon:
    move.w  d1, d2
.pc_blink:
    move.w  #$FFFF, BG
    move.l  #$00040000, d0
.pc_on:
    subq.l  #1, d0
    bne.s   .pc_on
    move.w  #$0000, BG
    move.l  #$00040000, d0
.pc_off:
    subq.l  #1, d0
    bne.s   .pc_off
    subq.w  #1, d2
    bne.s   .pc_blink
    move.l  #$00280000, d0                ; pausa lunga fra i gruppi
.pc_pause:
    subq.l  #1, d0
    bne.s   .pc_pause
    bra.s   ProbeCommon

; ---------------------------------------------------------------------------
PadDxLut:
    dc.w  2,2,2,4,4,6,6,8,8,10,10,12,12,14,14,16,16
    dc.w  20,20,20,20,24,24,24,24,28,28,28,28,32,32,32,32
    dc.w  40,40,40,40,40,40,40,40,48,48,48,48,48,48,48,48
    dc.w  56,56,56,56,56,56,56,56,64,64,64,64,64,64,64,64

PadFlagsLut:
    dc.l  $00010818,$00010818,$00010818
    dc.l  $00011018,$00011018
    dc.l  $00011418,$00011418
    dc.l  $00011818,$00011818
    dc.l  $00011A18,$00011A18
    dc.l  $00011C18,$00011C18
    dc.l  $00011E18,$00011E18
    dc.l  $00012018,$00012018
    dc.l  $00012218,$00012218,$00012218,$00012218
    dc.l  $00012418,$00012418,$00012418,$00012418
    dc.l  $00012618,$00012618,$00012618,$00012618
    dc.l  $00012818,$00012818,$00012818,$00012818
    dc.l  $00012A18,$00012A18,$00012A18,$00012A18
    dc.l  $00012A18,$00012A18,$00012A18,$00012A18
    dc.l  $00012C18,$00012C18,$00012C18,$00012C18
    dc.l  $00012C18,$00012C18,$00012C18,$00012C18
    dc.l  $00012E18,$00012E18,$00012E18,$00012E18
    dc.l  $00012E18,$00012E18,$00012E18,$00012E18
    dc.l  $00013018,$00013018,$00013018,$00013018
    dc.l  $00013018,$00013018,$00013018,$00013018

; ---------------------------------------------------------------------------
; S24 PoC C blob glue
; ---------------------------------------------------------------------------
; InitCBlob: copia PocBlob dal cart a C_BLOB_BASE e chiama poc_init.
; Ritorna d0 = valore restituito da poc_init (atteso $900DF00D).
; Nessun milestone BG qui: BG e' invisibile sia prima di OPSetup (video
; spento fino al write VMODE) sia dopo (il BMobj di sfondo copre l'active
; display). La diagnostica usa DbgBar = write diretto nel framebuffer.
;InitCBlob:                             ; S24 OFF
;    move.l  #PocBlob, a0               ; S24 OFF
;    move.l  #C_BLOB_BASE, a1           ; S24 OFF
;    move.l  #PocBlobEnd, d0            ; S24 OFF
;    sub.l   #PocBlob, d0               ; S24 OFF
;    addq.l  #3, d0                     ; S24 OFF
;    lsr.l   #2, d0                     ; S24 OFF
;.icb_copy:                             ; S24 OFF
;    move.l  (a0)+, (a1)+               ; S24 OFF
;    subq.l  #1, d0                     ; S24 OFF
;    bne.s   .icb_copy                  ; S24 OFF
;    jsr     C_POC_INIT                 ; S24 OFF
;    rts                                ; S24 OFF

; DbgBar: barra 32x8 a gradiente di palette index in BG_A, riga y = d1.w.
; Gradiente 0,8,16..248 cosi' almeno una colonna e' visibile qualunque sia
; la CLUT della scena (con un singolo index rischiavamo di pescare il nero).
; Clobbera d0-d2, a0-a1.
;DbgBar:                                ; S24 OFF
;    move.l  #BG_A, a0                  ; S24 OFF
;    moveq   #0, d0                     ; S24 OFF
;    move.w  d1, d0                     ; S24 OFF
;    mulu.w  #640, d0                   ; S24 OFF
;    adda.l  d0, a0                     ; S24 OFF
;    moveq   #7, d2                     ; S24 OFF
;.dbar_row:                             ; S24 OFF
;    movea.l a0, a1                     ; S24 OFF
;    moveq   #31, d0                    ; S24 OFF
;    moveq   #0, d1                     ; S24 OFF
;.dbar_col:                             ; S24 OFF
;    move.b  d1, (a1)+                  ; S24 OFF
;    addq.b  #8, d1                     ; S24 OFF
;    dbra    d0, .dbar_col              ; S24 OFF
;    lea     640(a0), a0                ; S24 OFF
;    dbra    d2, .dbar_row              ; S24 OFF
;    rts                                ; S24 OFF

; PocFrameHook: poc_frame(active_fb). GCC m68k ABI: arg long on stack,
; result d0, clobbers d0-d1/a0-a1 only.
;PocFrameHook:                          ; S24 OFF
;    move.l  #BG_A, d1                  ; S24 OFF
;    tst.b   ACTIVE_BG_ID               ; S24 OFF
;    beq.s   .pf_base_ok                ; S24 OFF
;    move.l  #BG_B, d1                  ; S24 OFF
;.pf_base_ok:                           ; S24 OFF
;    move.l  d1, -(sp)                  ; S24 OFF
;    jsr     C_POC_FRAME                ; S24 OFF
;    addq.l  #4, sp                     ; S24 OFF
;    rts                                ; S24 OFF

; ---------------------------------------------------------------------------
; JagGD BIOS bindings (RetroHQ official)
; ---------------------------------------------------------------------------
; S53: the RetroHQ GD BIOS bindings that were included here are gone from the
; repository (no licence to redistribute them; nothing here called them). The
; GameDrive channel is src/gd.s, run from DRAM (see src/main_v6.s).

; ---------------------------------------------------------------------------
; F4.2a GPU TOM rasterizer (harness + V0 test pixel)
; ---------------------------------------------------------------------------
    .include "src/gpu_raster.s"

; ---------------------------------------------------------------------------
; S14 F4.4 B2: body 0 per-corner intensity blob (4 byte slot per poly).
; Generated by tools/body_intensity_pack.py (cosine mode, NormalLight default).
    .phrase
Body0Intensity:
    .incbin "extracted/BODY_PACK/body_000_int.bin"

; ---------------------------------------------------------------------------
; S21: body 1 (prisoner) per-corner intensity blob.
; Generated by tools/body_intensity_pack.py --body-idx 1.
    .phrase
Body1Intensity:
    .incbin "extracted/BODY_PACK/body_001_int.bin"

; ---------------------------------------------------------------------------
; S15 F4.5 V0: body 0 sphere section pack (WORD nb + N x 8 byte descriptor).
; Generated by tools/body_sphere_pack.py.
    .phrase
Body0Spheres:
    .incbin "extracted/BODY_PACK/body_000_sph.bin"

; ---------------------------------------------------------------------------
; S15 F4.5 V0: DiskHwLUT (16 radii x 16 byte half-width table, 256 byte total).
; LUT[rs*16 + |dy|] = floor(sqrt(rs*rs - dy*dy)) for |dy| <= rs.
; Generated by tools/body_sphere_pack.py emit_disk_lut_inc().
    .phrase
    .include "extracted/BODY_PACK/disk_hw_lut.inc"

; ---------------------------------------------------------------------------
; S24: C blob PoC (m68k-elf-gcc -> objcopy binary, linked at $1E0000).
; Generated by engine_c/build_c.sh inside Docker marsdev-32x.
;    .phrase                            ; S24 OFF
;PocBlob:                               ; S24 OFF
;    .incbin "engine_c/poc_blob.bin"    ; S24 OFF
;PocBlobEnd:                            ; S24 OFF

; ---------------------------------------------------------------------------
    .phrase
SceneData:
    .incbin "cart/scene_all.bin"
SceneEnd:

    END
