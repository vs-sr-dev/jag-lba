; ---------------------------------------------------------------------------
; gpu_front.s - S45: il FRONT-END dei corpi sulla GPU TOM.
;
; Il terzo kernel, e il primo che trasloca DUE stadi in un lancio solo:
;
;   fase V   la posa dei vertici (matrice del PROPRIO gruppo, gia' calcolata
;            dal 68000 in wmat) + ancora + proiezione isometrica + rettangolo
;            dell'attore, in una passata sola sui vertici;
;   fase C   la faccia nascosta (testpoly: prodotto vettoriale 2D sui primi
;            tre angoli proiettati) + la chiave del pittore (minimo di X+Y+Z
;            sugli angoli), che scrive le coppie (chiave, indice) in wlist.
;
; Il C NON e' un ripiego: vert_stage() e cull_stage() in body_draw.c sono i
; MODELLI, il banco su PC li verifica al bit contro l'oracolo Python, e questo
; file li trascrive. Se GEOM1/2, CULL o ORDER1/2 cambiano di un bit dopo lo
; scambio, il difetto e' QUI e da nessun'altra parte.
;
; COSA RESTA AL 68000, E PERCHE'
; ------------------------------
; Le MATRICI dei gruppi (al piu' 25 per attore, con la tabella dei seni: roba
; da poche centinaia di moltiplicazioni) e l'ORDINAMENTO (pieno di salti, e la
; GPU TOM non ha i confronti con segno - feedback_gpu_cc_no_signed). Qui si
; muove il lavoro PER VERTICE e PER POLIGONO, che e' dove stavano POSE 26% +
; CULL 18% del profilo S44.
;
; IL CONTRATTO (tutto in GPU_VARS, dichiarato da tools/memory_map.py)
; -------------------------------------------------------------------
;   GPU_FR_STAGE   1 = solo fase V (bisezione), 2 = V + C
;   GPU_FR_DESC    il descrittore di posa: una voce per gruppo, 4 long
;                    +0  moff<<16 | nbv     moff = matrice, in LONG (g*9)
;                    +4  vstart<<16 | anchor  ($FFFF = nessuna ancora)
;                    +8  dx<<16 | dy        spostamento TRANSLATE, firmato
;                    +12 dz<<16 | 0
;   GPU_FR_ND      quante voci
;   GPU_FR_VERTS   vertici JBD: x,y,z,pad word BE = una phrase per vertice
;   GPU_FR_NPTS    nb_points (guardia degli indici della fase C)
;   GPU_FR_MATS    base delle matrici di gruppo (= wmat), 9 long l'una
;   GPU_FR_WV      dove scrivere la posa locale: x,y,z,pad word per vertice
;   GPU_FR_WSCR    dove scrivere il record di schermo: (sx<<16|sy), poi x+y+z
;   GPU_FR_POSX/Y/Z, GPU_FR_VX/VY   la traslazione dell'attore e la camera
;   GPU_FR_BBX/BBY  ESCONO: minx<<16|maxx, miny<<16|maxy (word firmate)
;   GPU_FR_POLYS   intestazioni JBD: mat,nbp,coul1,pad, flatn, corner_off
;   GPU_FR_NPOLY   quante
;   GPU_FR_CORNERS gli angoli: un long l'uno, normale<<16 | vertice
;   GPU_FR_WLIST   dove scrivere le coppie (chiave, idx<<16|off)
;   GPU_FR_LIVE    ESCE: quante coppie
;   GPU_FR_SAWD/SAWN  diagnostica: nd e nb_points LETTI, prima di ogni ciclo
;
; I LIMITI SU CUI QUESTO KERNEL SCOMMETTE - misurati dal banco, non sperati
; -------------------------------------------------------------------------
; IMULTN/IMACN guardano solo i 16 bit bassi CON SEGNO dei due operandi. Il
; banco su PC misura sui dati veri (FrontBound, host_body.c):
;   |vertice + spostamento|  793     |posa + ancora|  1491
;   |coordinate proiettate|  629     matrice: gia' coperta da ShadeBound
; tutti comodi dentro 32767. La proiezione NON usa moltiplicazioni: 24, 12 e
; 30 sono somme di shift (24t = t<<4 + t<<3), quindi i 17 bit di px+pz non
; toccano mai un moltiplicatore.
;
; LE LATENZE SI PAGANO, NON SI SPERANO (S33): ogni load ha almeno due
; istruzioni fra se' e il primo uso del suo registro. Niente btst/bset (mai
; confermati su hardware): i test di segno sono jr MI/PL sul risultato di una
; sub, i cicli sono count-down + jump NE. I target dei salti lunghi sono
; precaricati con movei PRIMA del confronto che li usa.
;
; GLI INDIRIZZI NON SONO QUI: li dichiara tools/memory_map.py (GPU_SRAM_*,
; GPU_FR_*). La .assert in fondo ferma la build se questo blob tocca le celle
; scratch - e' la lezione della sovrapposizione di S44.
; ---------------------------------------------------------------------------

GPU_FRONT_ENTRY     EQU     GPU_SRAM_FRONT

; Le celle scratch nella SRAM (mappa: GPU_SRAM_FRDXYZ, 6 long liberi prima di
; HDRSLOT). I registri della fase V sono TUTTI E 32 occupati: lo spostamento
; TRANSLATE del gruppo corrente e il conteggio dei gruppi vivono qui, scritti
; al cambio di gruppo e riletti nel ciclo (dx,dy,dz una volta per vertice).
FRC_DX              EQU     GPU_SRAM_FRDXYZ
FRC_DY              EQU     GPU_SRAM_FRDXYZ+4
FRC_DZ              EQU     GPU_SRAM_FRDXYZ+8
FRC_GN              EQU     GPU_SRAM_FRDXYZ+12

; ---------------------------------------------------------------------------
; Lato 68000: la copia nella RAM locale. Una volta sola, al boot. Il conteggio
; si arrotonda per ECCESSO come per gli altri due kernel
; (feedback_gpu_blob_copy_roundup).
; ---------------------------------------------------------------------------
    .68000

InitGpuFront:
    movem.l d0/a0/a1, -(sp)
    move.l  #GpuFrontRom, a0
    move.l  #GPU_FRONT_ENTRY, a1
    move.w  #(GpuFrontRomEnd-GpuFrontRom+3)/4-1, d0
.igf_copy:
    move.l  (a0)+, (a1)+
    dbra    d0, .igf_copy
    movem.l (sp)+, d0/a0/a1
    rts

; ---------------------------------------------------------------------------
; MAPPA DEI REGISTRI - fase V (ricaricati al cambio di fase,
; feedback_gpu_multiphase_reload):
;
;   r0,r1,r2  x,y,z del vertice corrente, poi appoggio
;   r3        puntatore di scrittura in wv
;   r4,r5     vx, vy
;   r6,r7     rettangolo: minx, maxx
;   r8,r9     rettangolo: miny, maxy
;   r10       vertici rimasti nel gruppo (count-down)
;   r11..r13  PosObjX, PosObjY, PosObjZ
;   r14       puntatore di scrittura in wscr
;   r15       puntatore di lettura dei vertici JBD
;   r16..r18  l'ancora del gruppo: ax, ay, az (locali, senza Pos)
;   r19       puntatore nel descrittore
;   r20..r28  la matrice del gruppo, m0..m8
;   r29       target del ciclo dei vertici
;   r30,r31   appoggio
;
; Trentadue esatti: dx,dy,dz e il conteggio dei gruppi stanno nelle celle
; scratch FRC_*.
;
; Fase C:
;   r0,r1,r2  appoggio
;   r3        puntatore nelle intestazioni dei poligoni
;   r4        base degli angoli
;   r5        base di wscr
;   r6        puntatore di scrittura in wlist
;   r7        live (quante coppie scritte)
;   r8        nb_points
;   r9        i, l'indice del poligono corrente
;   r10       poligoni rimasti (count-down)
;   r11..r13  i tre angoli proiettati, impacchettati
;   r14       la chiave (minimo corrente), e appoggio del cross
;   r15       angoli rimasti nel ciclo della chiave (count-down)
;   r19       puntatore agli angoli del poligono corrente
;   r29..r31  target: PLoop, PNext, KeyLoop
; ---------------------------------------------------------------------------
GpuFrontRom:
    .gpu
    .org    GPU_FRONT_ENTRY

GpuFrontMain:
    ; --- marcatore = 1: il kernel e' partito ---

    ; --- argomenti della fase V ---
    movei   #GPU_FR_DESC, r0
    load    (r0), r19
    movei   #GPU_FR_ND, r0
    load    (r0), r1
    movei   #GPU_FR_NPTS, r0
    load    (r0), r2
    movei   #FRC_GN, r30
    store   r1, (r30)

    ; --- rimando indietro quel che ho LETTO, prima di ogni ciclo ---
    movei   #GPU_FR_SAWD, r0
    store   r1, (r0)
    movei   #GPU_FR_SAWN, r0
    store   r2, (r0)

    movei   #GPU_FR_POSX, r0
    load    (r0), r11
    movei   #GPU_FR_POSY, r0
    load    (r0), r12
    movei   #GPU_FR_POSZ, r0
    load    (r0), r13
    movei   #GPU_FR_VX, r0
    load    (r0), r4
    movei   #GPU_FR_VY, r0
    load    (r0), r5
    nop
    nop

    ; --- rettangolo: 32767 / -32767, come P_OB_ISO.ASM:231-236 ---
    movei   #$00007FFF, r6
    movei   #$FFFF8001, r7
    move    r6, r8
    move    r7, r9

    ; --- target del ciclo dei vertici, vivo per tutta la fase ---
    movei   #GpuFront_VLoop, r29

; ---------------------------------------------------------------------------
; Un GRUPPO: una voce del descrittore.
; ---------------------------------------------------------------------------
GpuFront_GLoop:
    ; marcatore = 2: sto consumando gruppi

    ; gruppi rimasti (cella scratch)
    movei   #GpuFront_CPhase, r30
    movei   #FRC_GN, r0
    load    (r0), r1
    nop
    nop
    cmpq    #0, r1
    jump    EQ, (r30)
    nop
    subq    #1, r1
    store   r1, (r0)

    ; --- la voce: 4 long ---
    load    (r19), r0                       ; moff<<16 | nbv
    addq    #4, r19
    load    (r19), r1                       ; vstart<<16 | anchor
    addq    #4, r19
    load    (r19), r2                       ; dx<<16 | dy
    addq    #4, r19
    load    (r19), r30                      ; dz<<16 | 0
    addq    #4, r19

    ; nbv -> r10 (count-down del ciclo)
    move    r0, r10
    shlq    #16, r10
    shrq    #16, r10

    ; --- dx, dy, dz nelle celle scratch, firmati ---
    move    r2, r31
    sharq   #16, r31                        ; dx
    shlq    #16, r2
    sharq   #16, r2                         ; dy
    sharq   #16, r30                        ; dz
    movei   #FRC_DX, r3
    store   r31, (r3)
    movei   #FRC_DY, r3
    store   r2, (r3)
    movei   #FRC_DZ, r3
    store   r30, (r3)

    ; --- la matrice del gruppo: moff in long -> byte, 9 load ---
    shrq    #16, r0                         ; moff
    shlq    #2, r0
    movei   #GPU_FR_MATS, r2
    load    (r2), r2
    nop
    nop
    add     r2, r0
    load    (r0), r20
    addq    #4, r0
    load    (r0), r21
    addq    #4, r0
    load    (r0), r22
    addq    #4, r0
    load    (r0), r23
    addq    #4, r0
    load    (r0), r24
    addq    #4, r0
    load    (r0), r25
    addq    #4, r0
    load    (r0), r26
    addq    #4, r0
    load    (r0), r27
    addq    #4, r0
    load    (r0), r28

    ; --- i tre puntatori del gruppo: base + vstart*8 ---
    move    r1, r2
    shrq    #16, r2                         ; vstart
    shlq    #3, r2                          ; *8: phrase dei vertici, e anche
                                            ; il passo di wv e di wscr
    movei   #GPU_FR_VERTS, r15
    load    (r15), r15
    movei   #GPU_FR_WV, r3
    load    (r3), r3
    movei   #GPU_FR_WSCR, r14
    load    (r14), r14
    nop
    add     r2, r15
    add     r2, r3
    add     r2, r14

    ; --- l'ancora: $FFFF = nessuna, se no tre word da wv (gia' posate) ---
    moveq   #0, r16
    moveq   #0, r17
    moveq   #0, r18
    movei   #GpuFront_NoAnchor, r31
    shlq    #16, r1
    shrq    #16, r1                         ; anchor
    movei   #$0000FFFF, r0
    cmp     r0, r1
    jump    EQ, (r31)
    nop
    shlq    #3, r1                          ; *8, il passo di wv
    movei   #GPU_FR_WV, r0
    load    (r0), r0
    nop
    nop
    add     r1, r0
    load    (r0), r2                        ; ax<<16 | ay
    addq    #4, r0
    load    (r0), r0                        ; az<<16 | 0
    move    r2, r16
    sharq   #16, r16                        ; ax
    move    r2, r17
    shlq    #16, r17
    sharq   #16, r17                        ; ay
    move    r0, r18
    sharq   #16, r18                        ; az

GpuFront_NoAnchor:
    ; gruppo vuoto: dritto al prossimo
    movei   #GpuFront_GLoop, r0
    cmpq    #0, r10
    jump    EQ, (r0)
    nop
    jump    T, (r29)
    nop

; ---------------------------------------------------------------------------
; UN VERTICE. Due load, nove moltiplicazioni MAC, due store in wv, proiezione
; a shift (24t = t<<4+t<<3, 12a = a<<3+a<<2, 30b = b<<5-b<<1), rettangolo,
; due store in wscr.
; ---------------------------------------------------------------------------
GpuFront_VLoop:
    ; --- x<<16|y, z<<16|pad: big endian, come li ha messi il packer ---
    load    (r15), r30
    addq    #4, r15
    load    (r15), r31
    addq    #4, r15
    move    r30, r0
    sharq   #16, r0                         ; x
    move    r30, r1
    shlq    #16, r1
    sharq   #16, r1                         ; y
    move    r31, r2
    sharq   #16, r2                         ; z

    ; --- + lo spostamento TRANSLATE del gruppo (quasi sempre zero) ---
    movei   #FRC_DX, r30
    load    (r30), r30
    movei   #FRC_DY, r31
    load    (r31), r31
    add     r30, r0
    movei   #FRC_DZ, r30
    load    (r30), r30
    add     r31, r1
    nop
    add     r30, r2

    ; --- le tre righe: (m . v) >> 14, troncamento PER COMPONENTE come Rot ---
    imultn  r20, r0
    imacn   r21, r1
    imacn   r22, r2
    resmac  r30
    imultn  r23, r0
    imacn   r24, r1
    imacn   r25, r2
    resmac  r31
    imultn  r26, r0
    imacn   r27, r1
    imacn   r28, r2
    resmac  r0                              ; x e' morto: riga 3 ci atterra
    sharq   #14, r30
    sharq   #14, r31
    nop
    sharq   #14, r0

    ; --- + l'ancora: la posa LOCALE t0,t1,t2 in r30,r31,r0 ---
    add     r16, r30
    add     r17, r31
    add     r18, r0

    ; --- wv: (t0<<16)|(t1&FFFF), (t2<<16)|0 - una phrase per vertice ---
    move    r31, r2
    shlq    #16, r2
    shrq    #16, r2
    move    r30, r1
    shlq    #16, r1
    or      r2, r1
    store   r1, (r3)
    addq    #4, r3
    move    r0, r1
    shlq    #16, r1
    store   r1, (r3)
    addq    #4, r3

    ; --- proiezione: px,py,pz = posa + PosObj ---
    add     r11, r30                        ; px
    add     r12, r31                        ; py
    add     r13, r0                         ; pz

    ; profondita' = px+py+pz, resta in r1 fino allo store
    move    r30, r1
    add     r31, r1
    add     r0, r1

    ; sx = ((px-pz)*24 >> 9) + vx      24t = (t<<4)+(t<<3)
    move    r30, r2
    sub     r0, r2                          ; t = px-pz
    add     r0, r30                         ; a = px+pz (pz muore dopo)
    move    r2, r0
    shlq    #4, r0
    shlq    #3, r2
    add     r2, r0
    sharq   #9, r0
    add     r4, r0                          ; sx

    ; 30*py = (py<<5)-(py<<1)
    move    r31, r2
    shlq    #5, r2
    shlq    #1, r31
    sub     r31, r2                         ; 30*py (py muore)

    ; sy = ((12a - 30py) >> 9) + vy    12a = (a<<3)+(a<<2)
    move    r30, r31
    shlq    #3, r31
    shlq    #2, r30
    add     r30, r31
    sub     r2, r31
    sharq   #9, r31
    add     r5, r31                         ; sy

    ; --- il rettangolo dell'attore, su TUTTI i vertici proiettati ---
    move    r0, r2
    sub     r6, r2
    jr      PL, .bb1                        ; sx < minx ?
    nop
    move    r0, r6
.bb1:
    move    r7, r2
    sub     r0, r2
    jr      PL, .bb2                        ; sx > maxx ?
    nop
    move    r0, r7
.bb2:
    move    r31, r2
    sub     r8, r2
    jr      PL, .bb3                        ; sy < miny ?
    nop
    move    r31, r8
.bb3:
    move    r9, r2
    sub     r31, r2
    jr      PL, .bb4                        ; sy > maxy ?
    nop
    move    r31, r9
.bb4:

    ; --- wscr: (sx<<16)|(sy&FFFF), poi la profondita' ---
    shlq    #16, r0
    move    r31, r2
    shlq    #16, r2
    shrq    #16, r2
    or      r2, r0
    store   r0, (r14)
    addq    #4, r14
    store   r1, (r14)
    addq    #4, r14

    subq    #1, r10
    jump    NE, (r29)
    nop
    ; gruppo finito
    movei   #GpuFront_GLoop, r0
    jump    T, (r0)
    nop

; ---------------------------------------------------------------------------
; FINE FASE V: il rettangolo esce, e se STAGE=1 si chiude qui (bisezione).
; ---------------------------------------------------------------------------
GpuFront_CPhase:
    ; marcatore = 3

    ; BBX = minx<<16 | (maxx & FFFF), BBY idem
    move    r6, r0
    shlq    #16, r0
    move    r7, r1
    shlq    #16, r1
    shrq    #16, r1
    or      r1, r0
    movei   #GPU_FR_BBX, r1
    store   r0, (r1)
    move    r8, r0
    shlq    #16, r0
    move    r9, r1
    shlq    #16, r1
    shrq    #16, r1
    or      r1, r0
    movei   #GPU_FR_BBY, r1
    store   r0, (r1)

    movei   #GpuFront_Done, r30
    movei   #GPU_FR_STAGE, r0
    load    (r0), r0
    nop
    nop
    cmpq    #1, r0
    jump    EQ, (r30)
    nop

    ; --- ricarica per la fase C (feedback_gpu_multiphase_reload) ---
    movei   #GPU_FR_POLYS, r0
    load    (r0), r3
    movei   #GPU_FR_NPOLY, r0
    load    (r0), r10
    movei   #GPU_FR_CORNERS, r0
    load    (r0), r4
    movei   #GPU_FR_WSCR, r0
    load    (r0), r5
    movei   #GPU_FR_WLIST, r0
    load    (r0), r6
    movei   #GPU_FR_NPTS, r0
    load    (r0), r8
    moveq   #0, r7                          ; live
    moveq   #0, r9                          ; i
    movei   #GpuFront_PLoop, r29
    movei   #GpuFront_PNext, r30
    movei   #GpuFront_KeyLoop, r31

; ---------------------------------------------------------------------------
; UN POLIGONO: intestazione, tre angoli, prodotto vettoriale, chiave.
; ---------------------------------------------------------------------------
GpuFront_PLoop:
    movei   #GpuFront_CDone, r0
    cmpq    #0, r10
    jump    EQ, (r0)
    nop
    subq    #1, r10

    ; --- l'intestazione: 2 long ---
    load    (r3), r0                        ; mat<<24 | nbp<<16 | coul1<<8 | 0
    addq    #4, r3
    load    (r3), r1                        ; flatn<<16 | corner_off
    addq    #4, r3
    move    r0, r15
    shlq    #8, r15
    shrq    #24, r15                        ; nbp

    ; nbp < 3: non e' un poligono (stessa guardia del C)
    move    r15, r0
    subq    #3, r0
    jump    MI, (r30)
    nop

    ; --- gli angoli del poligono ---
    move    r1, r19
    shlq    #16, r19
    shrq    #16, r19                        ; corner_off, in elementi
    shlq    #2, r19
    add     r4, r19                         ; &corners[off]

    ; i primi tre vertici -> schermo impacchettato in r11,r12,r13.
    ; La guardia e' quella del C: v >= nb_points diventa v = 0.
    move    r19, r2
    load    (r2), r0                        ; normale<<16 | v0
    addq    #4, r2
    shlq    #16, r0
    shrq    #16, r0
    move    r0, r1
    sub     r8, r1
    jr      MI, .c0ok
    nop
    moveq   #0, r0
.c0ok:
    shlq    #3, r0                          ; *8: il passo di wscr
    add     r5, r0
    load    (r0), r11                       ; p0

    load    (r2), r0
    addq    #4, r2
    shlq    #16, r0
    shrq    #16, r0
    move    r0, r1
    sub     r8, r1
    jr      MI, .c1ok
    nop
    moveq   #0, r0
.c1ok:
    shlq    #3, r0
    add     r5, r0
    load    (r0), r12                       ; p1

    load    (r2), r0
    shlq    #16, r0
    shrq    #16, r0
    move    r0, r1
    sub     r8, r1
    jr      MI, .c2ok
    nop
    moveq   #0, r0
.c2ok:
    shlq    #3, r0
    add     r5, r0
    load    (r0), r13                       ; p2
    nop
    nop

    ; --- testpoly: (x0-x2)*(y1-y0) - (y0-y2)*(x1-x0) < 0 = visibile ---
    ; Le differenze stanno in 16 bit (schermo a +-700), quindi la MAC firmata
    ; e' il silicio giusto, come per lo shade.
    move    r11, r0
    sharq   #16, r0                         ; x0
    move    r13, r1
    sharq   #16, r1                         ; x2
    sub     r1, r0                          ; x0-x2
    move    r12, r1
    shlq    #16, r1
    sharq   #16, r1                         ; y1
    move    r11, r2
    shlq    #16, r2
    sharq   #16, r2                         ; y0
    sub     r2, r1                          ; y1-y0
    imultn  r1, r0
    resmac  r0                              ; A = (x0-x2)*(y1-y0)

    move    r13, r1
    shlq    #16, r1
    sharq   #16, r1                         ; y2
    sub     r1, r2                          ; y0-y2
    move    r12, r1
    sharq   #16, r1                         ; x1
    move    r11, r14
    sharq   #16, r14                        ; x0
    sub     r14, r1                         ; x1-x0
    imultn  r1, r2
    resmac  r2                              ; B = (y0-y2)*(x1-x0)
    nop
    sub     r2, r0                          ; cross = A - B

    ; si tiene solo il NEGATIVO (`jnl badpoly`)
    jump    PL, (r30)
    nop

    ; --- la chiave: minimo della profondita' sugli angoli. La guardia qui
    ; SALTA l'angolo fuori campo invece di mandarlo al vertice 0: e' la regola
    ; del C, tale e quale. r19 punta ancora al primo angolo. ---
    movei   #$7FFFFFFF, r14
GpuFront_KeyLoop:
    load    (r19), r0
    addq    #4, r19
    shlq    #16, r0
    shrq    #16, r0                         ; v
    move    r0, r1
    sub     r8, r1
    jr      PL, .knext                      ; fuori campo: si salta
    nop
    shlq    #3, r0
    add     r5, r0
    addq    #4, r0                          ; la profondita' sta a +4
    load    (r0), r1
    nop
    nop
    move    r1, r0
    sub     r14, r0
    jr      PL, .knext                      ; non e' un nuovo minimo
    nop
    move    r1, r14
.knext:
    subq    #1, r15
    jump    NE, (r31)
    nop

    ; --- la coppia: chiave, poi indice<<16 | scostamento (== i | i) ---
    store   r14, (r6)
    addq    #4, r6
    move    r9, r0
    shlq    #16, r0
    or      r9, r0
    store   r0, (r6)
    addq    #4, r6
    addq    #1, r7

GpuFront_PNext:
    addq    #1, r9
    jump    T, (r29)
    nop

; ---------------------------------------------------------------------------
GpuFront_CDone:
    movei   #GPU_FR_LIVE, r0
    store   r7, (r0)

GpuFront_Done:
    ; marcatore = 4

    ; Barriera, bandiera, barriera, auto-arresto: da S46 la sequenza sta in
    ; UNA copia sola, GpuCommon_Done in gpu_body.s (kernel residenti insieme,
    ; saltarci e' un salto qualsiasi). r0 = la bandiera DONE di QUESTO kernel.
    movei   #GPU_FR_DONE, r0
    movei   #GpuCommon_Done, r1
    jump    T, (r1)
    nop

GpuFront_End:
    ; Sotto le celle scratch: e' la .assert che in S44 mancava quando due
    ; kernel si sono sovrapposti.
    .assert GpuFront_End <= GPU_SRAM_FRDXYZ
    .68000
GpuFrontRomEnd:


