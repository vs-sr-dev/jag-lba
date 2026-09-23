; ---------------------------------------------------------------------------
; gpu_shade.s - S40: lo stadio dell'INTENSITA' sulla GPU TOM.
;
; Il secondo kernel, e il primo che trasloca uno stadio invece di un ciclo
; interno. Forma B della voce 3 di docs/todo_s39.md: la GPU carica un programma
; per STADIO e lo fa girare su tutti gli attori, uno per lancio.
;
; COSA FA
; -------
; Una intensita' per normale, cioe' esattamente `shade_stage` di
; engine_c/body_draw.c:
;
;     r  = (M . n) >> 14          una matrice per GRUPPO, tre righe
;     dot = r . luce
;     out = (dot < 0 || rng == 0) ? 0 : dot / rng
;
; Il C NON e' un ripiego ne' una copia di comodo: e' il MODELLO, il banco di
; prova su PC lo verifica bit per bit contro l'oracolo Python, e questo file lo
; trascrive. Se le sonde SHADE1/SHADE2 (pagina 2, etichette 5 e 6) cambiano di
; un bit dopo lo scambio, il difetto e' QUI e da nessun'altra parte.
;
; PERCHE' IL GUADAGNO C'E' DAVVERO: IMULTN/IMACN/RESMAC
; -----------------------------------------------------
; Il blob C non contiene NEMMENO UNA moltiplicazione hardware:
;
;     grep -c 'muls\.'   engine_c/engine_blob.lst  ->  0
;     grep -c '__mulsi3' engine_c/engine_blob.lst  -> 22
;
; Ogni prodotto di questo stadio - dodici per normale - e' una chiamata a
; libgcc a 32x32 bit, mentre gli operandi sono a 16 (matrice Q14 entro +-16384,
; normali WORD, luce lunga 59). Qui gli stessi dodici prodotti sono dodici
; istruzioni sul moltiplicatore-accumulatore, una per tick, e la divisione e' il
; divisore hardware invece di `__divsi3`.
;
; NON si usa MMULT, ed e' una scelta, non una dimenticanza.
; MMULT vuole la matrice in RAM LOCALE e il vettore nel BANCO SECONDARIO dei
; registri, impacchettato due elementi per registro (TRM, "Systolic Matrix
; Multiplies"). Nel nostro caso e' il contrario di quel che serve: la matrice
; sta ferma per tutta una voce del descrittore e a scorrere sono i VETTORI, che
; arrivano dalla DRAM. Con MMULT ogni normale costerebbe due store in RAM locale
; piu' il ballo dei banchi (MOVETA), e soprattutto il TRM non dice QUALE meta'
; del registro impacchettato sia il primo elemento - un dettaglio che si
; verificherebbe solo masterizzando. IMULTN/IMACN e' lo STESSO silicio (il TRM
; dice che MMULT genera internamente proprio quella sequenza) senza nessuna di
; quelle incognite: "the bottom 16-bits of each of the source and destination
; registers", e basta.
;
; IL TRUCCO DEI 16 BIT BASSI, che vale sei istruzioni per normale.
; Una normale in forma nativa e' 4 word big endian = una phrase:
;
;     long alto  =  x<<16 | y          long basso =  z<<16 | rng
;
; e IMULTN/IMACN guardano SOLO i 16 bit bassi del registro. Quindi `y` e' gia'
; pronto nel long alto e `rng` nel long basso: non si estraggono, si usano.
; Solo `x` e `z` vogliono una `sharq #16`.
;
; LE LATENZE
; ----------
; La divisione E' INTERBLOCCATA, e questo il TRM lo dice in chiaro (pag. 42):
; "If another instruction attempts to read the quotient or start another divide
; operation while the divide unit is active, then wait states will be inserted
; until the divide unit has completed". Qui percio' NON ci sono i sedici nop che
; src/gpu_body.s mette dopo ogni `div`: si legge il quoziente e la GPU aspetta
; da se'. (Quei sedici nop in gpu_body.s si possono togliere per la stessa
; ragione - sono due per riga di poligono. Non si tocca in questa sessione: e'
; un altro file e un'altra sonda.)
;
; Restano due cose che l'interblocco NON copre, e sono BUG del silicio
; dichiarati nel TRM (appendice "TOM and JERRY Bugs List"):
;   bug 2   gli store con indirizzamento indicizzato non sono protetti dallo
;           scoreboard quando il dato viene da una divisione o da una lettura
;           esterna. Qui lo store e' diretto - `storew r0, (r15)` - ma la
;           `or r0, r0` che lo precede e' la protezione che il TRM stesso
;           suggerisce, e costa un tick su sessanta.
;   bug 13  due scritture allo stesso registro senza una lettura in mezzo
;           possono atterrare al contrario. Nessun registro qui viene scritto
;           due volte di fila senza essere letto.
;
; IL CONTRATTO CON IL 68000
; -------------------------
; Tutto in GPU_VARS, dichiarato da tools/memory_map.py (mai qui: e' la lezione
; di S24). Sono ESATTAMENTE gli argomenti di shade_stage():
;
;   GPU_SH_NORM    normali native, 4 word BE per normale, allineate a phrase
;   GPU_SH_NN      quante normali ha il corpo
;   GPU_SH_OUT     dove scrivere, una word per normale (= wint)
;   GPU_SH_SPANS   descrittore, UN LONG per voce:  moff<<16 | count
;   GPU_SH_NS      quante voci
;   GPU_SH_MATS    base delle matrici di gruppo, 9 long l'una (= wmat)
;   GPU_SH_LX/Y/Z  la luce, gia' ruotata
;
; `moff` e' lo scostamento della matrice dentro wmat IN LONG (g*9), come nel C.
;
; TRE COSE CHE NON SI POSSONO SBAGLIARE, e i limiti che le rendono vere
; ---------------------------------------------------------------------
;  1. La matrice sta in 16 bit CON SEGNO. Le voci sono Q14, cioe' +-16384 piu'
;     la deriva del troncamento lungo la catena dei gruppi (meno di un'unita'
;     per composizione, al piu' 25 gruppi): il margine su 32767 e' il doppio.
;  2. `r` sta in 16 bit CON SEGNO. Le righe della matrice sono unitarie, quindi
;     |r| <= |n|, e le normali di LBA1 sono lunghe 64 (NORMAL_UNIT,
;     P_DEFINE.ASH:2). Misurato sull'archivio: |dot| non passa 3733.
;  3. `rng` e' CON SEGNO nel dato (LE16 torna uno short) ma sull'archivio sta
;     sempre fra 256 e 2048 su tutte e 15827 le normali di BODY.HQR. Il
;     divisore della GPU e' SENZA segno: un `rng` negativo darebbe qui un
;     numero e nel C un altro. E' una divergenza dichiarata su un caso che nei
;     dati non esiste, e se un giorno esistesse lo direbbero le sonde SHADE.
;
; LA DIAGNOSTICA CHE SI VERIFICA DA SOLA
; --------------------------------------
; GPU_SH_SAWN e GPU_SH_SAWS sono quel che la GPU ha LETTO, riscritti PRIMA di
; qualsiasi ciclo: separano "la GPU sbaglia i conti" da "la GPU non ha mai visto
; i dati giusti", che e' il paio che in S33 ha trovato tutti e quattro i difetti
; della stretta di mano. GPU_SH_LEFT sono le normali rimaste scoperte
; all'uscita: DEVE valere zero, perche' il descrittore ha una voce di coda
; apposta. E' un numero che si verifica da se' invece di richiedere il confronto
; fra due schermate.
; ---------------------------------------------------------------------------

; La RAM locale della GPU e' 4 KB a $F03000 e la sua mappa sta in
; tools/memory_map.py (GPU_SRAM_*), NON qui: in S44 il kernel dei corpi e'
; cresciuto (fase A dei bordi) sopra il $F03400 che questo file si era
; dichiarato da solo, e al boot le due copie si sono sovrapposte. I due
; programmi sono residenti INSIEME: si copiano al boot e non si ricarica
; niente per lancio. La .assert in fondo ferma la build se questo blob
; arriva a toccare le celle fisse del kernel dei corpi.
GPU_SHADE_ENTRY     EQU     GPU_SRAM_SHADE

; ---------------------------------------------------------------------------
; Lato 68000: la copia nella RAM locale. Una volta sola, al boot.
;
; Il conteggio si arrotonda per ECCESSO, come per gpu_body: la divisione secca
; per 4 lascia fuori l'ultima istruzione ogni volta che il blocco non e' un
; multiplo di quattro byte, e l'ultima istruzione qui e' il `nop` nella casella
; di ritardo del salto di arresto.
; ---------------------------------------------------------------------------
    .68000

InitGpuShade:
    movem.l d0/a0/a1, -(sp)
    move.l  #GpuShadeRom, a0
    move.l  #GPU_SHADE_ENTRY, a1
    move.w  #(GpuShadeRomEnd-GpuShadeRom+3)/4-1, d0
.igs_copy:
    move.l  (a0)+, (a1)+
    dbra    d0, .igs_copy
    movem.l (sp)+, d0/a0/a1
    rts

; ---------------------------------------------------------------------------
; MAPPA DEI REGISTRI - allocati una volta, nessuna ricarica per fase.
;
;   r0        appoggio, poi `dot` e il quoziente
;   r1        appoggio degli indirizzi
;   r2..r10   la matrice del gruppo corrente, m0..m8
;   r11..r13  la luce X, Y, Z
;   r14       la normale corrente
;   r15       l'intensita' corrente
;   r16       normali rimaste in tutto (la guardia `i < nb_normals`)
;   r17       voci del descrittore rimaste
;   r18       normali rimaste in questa voce
;   r19       il descrittore
;   r20       base delle matrici
;   r21       x<<16 | y     (i 16 bit bassi SONO y)
;   r22       z<<16 | rng   (i 16 bit bassi SONO rng)
;   r23       x
;   r24       z
;   r25       rng, con segno
;   r26..r28  r0, r1, r2 della normale ruotata
;   r29..r31  target di salto, precaricati (feedback_gpu_branch_pattern)
;
; Trentadue esatti. I tre target stanno in fondo perche' un `movei` aggiorna i
; flag: precaricarli una volta sola vuol dire che nessun confronto viene
; cancellato fra il `cmpq` e il `jump` che lo usa.
; ---------------------------------------------------------------------------
GpuShadeRom:
    .gpu
    .org    GPU_SHADE_ENTRY

GpuShadeMain:
    ; --- G_DIVCTRL = 0: divisione intera senza segno.
    ; Non si eredita da gpu_body: i due kernel partono ognuno per conto suo, e
    ; dare per scontato lo stato lasciato dall'altro e' esattamente il genere di
    ; legame che si paga tre sessioni dopo.
    movei   #G_DIVCTRL, r0
    moveq   #0, r1
    store   r1, (r0)
    nop
    nop
    nop
    nop

    ; --- Argomenti ---
    movei   #GPU_SH_NORM, r0
    load    (r0), r14
    movei   #GPU_SH_OUT, r0
    load    (r0), r15
    movei   #GPU_SH_NN, r0
    load    (r0), r16
    movei   #GPU_SH_SPANS, r0
    load    (r0), r19
    movei   #GPU_SH_NS, r0
    load    (r0), r17
    movei   #GPU_SH_MATS, r0
    load    (r0), r20
    movei   #GPU_SH_LX, r0
    load    (r0), r11
    movei   #GPU_SH_LY, r0
    load    (r0), r12
    movei   #GPU_SH_LZ, r0
    load    (r0), r13
    nop
    nop
    nop
    nop
    nop
    nop

    ; --- Rimando indietro quel che ho LETTO, prima di qualsiasi ciclo ---
    movei   #GPU_SH_SAWN, r0
    store   r16, (r0)
    movei   #GPU_SH_SAWS, r0
    store   r17, (r0)
    nop
    nop
    nop

    ; --- Target di salto ---
    movei   #GpuShade_NormLoop, r29
    movei   #GpuShade_SpanLoop, r30
    movei   #GpuShade_Done, r31

; ---------------------------------------------------------------------------
; Una VOCE del descrittore: una matrice, e quante normali la usano.
;
; Le due guardie sono le stesse due del C, nello stesso ordine: `s < ns` e
; `i < nb_normals`. La seconda qui in testa e non solo nel ciclo interno perche'
; una voce con conteggio zero non deve nemmeno caricare la matrice.
; ---------------------------------------------------------------------------
GpuShade_SpanLoop:
    cmpq    #0, r17
    jump    EQ, (r31)
    nop
    cmpq    #0, r16
    jump    EQ, (r31)
    nop
    subq    #1, r17

    load    (r19), r0                       ; moff<<16 | count
    addq    #4, r19
    nop
    nop
    nop
    nop

    move    r0, r18
    shlq    #16, r18
    shrq    #16, r18                        ; count, SENZA segno
    shrq    #16, r0                         ; moff, in long
    shlq    #2, r0                          ; -> byte
    add     r20, r0                         ; &matrice del gruppo

    move    r0, r1
    load    (r1), r2
    addq    #4, r1
    load    (r1), r3
    addq    #4, r1
    load    (r1), r4
    addq    #4, r1
    load    (r1), r5
    addq    #4, r1
    load    (r1), r6
    addq    #4, r1
    load    (r1), r7
    addq    #4, r1
    load    (r1), r8
    addq    #4, r1
    load    (r1), r9
    addq    #4, r1
    load    (r1), r10

; ---------------------------------------------------------------------------
; Una NORMALE.
; ---------------------------------------------------------------------------
GpuShade_NormLoop:
    cmpq    #0, r18
    jump    EQ, (r30)                       ; voce finita -> la prossima
    nop
    cmpq    #0, r16
    jump    EQ, (r31)                       ; normali finite -> fine
    nop
    subq    #1, r18
    subq    #1, r16

    ; --- la normale e' UNA PHRASE: due long, e la meta' del lavoro e' gia'
    ; fatta perche' y e rng vivono nei 16 bit bassi dei due long. ---
    move    r14, r1
    load    (r14), r21                      ; x<<16 | y
    addq    #4, r1
    load    (r1), r22                       ; z<<16 | rng
    addq    #8, r14                         ; avanti di una phrase
    nop
    nop
    nop

    move    r21, r23
    sharq   #16, r23                        ; x
    move    r22, r24
    sharq   #16, r24                        ; z
    move    r22, r25
    shlq    #16, r25
    sharq   #16, r25                        ; rng, con segno

    ; --- r = (M . n) >> 14, una riga per volta ---
    ; Il troncamento e' PER COMPONENTE, come `Rot` (P_TRIGO.ASM:502) e come il
    ; C: non si puo' piegare la luce dentro la matrice (M^T . luce) e fare una
    ; sola riga, perche' darebbe un altro numero. `sharq` e' aritmetica, cioe'
    ; arrotonda verso il basso come lo `>>` del C su un LONG con segno.
    imultn  r2, r23                         ; m0*x
    imacn   r3, r21                         ; m1*y
    imacn   r4, r24                         ; m2*z
    resmac  r26
    imultn  r5, r23
    imacn   r6, r21
    imacn   r7, r24
    resmac  r27
    imultn  r8, r23
    imacn   r9, r21
    imacn   r10, r24
    resmac  r28
    sharq   #14, r26
    sharq   #14, r27
    nop
    sharq   #14, r28

    ; --- dot = r . luce ---
    imultn  r11, r26
    imacn   r12, r27
    imacn   r13, r28
    resmac  r0
    nop
    nop
    nop

    ; --- La faccia che guarda dall'altra parte e' BUIO, non un'intensita' da
    ; ribaltare (`or dx,dx / js nointensity`, P_OB_ISO.ASM:1687). Il ramo salta
    ; anche i sedici tick del divisore, e sono normali frequenti. ---
    cmpq    #0, r25
    jr      EQ, GpuShade_Zero               ; rng == 0: come il C, e non si
    nop                                     ; divide per zero
    cmpq    #0, r0
    jr      MI, GpuShade_Zero               ; dot < 0
    nop

    div     r25, r0                         ; interbloccato (TRM pag. 42)
    nop
    nop
    jr      T, GpuShade_Store
    nop

GpuShade_Zero:
    moveq   #0, r0

GpuShade_Store:
    or      r0, r0                          ; protezione dello scoreboard
    storew  r0, (r15)
    addq    #2, r15
    jump    T, (r29)
    nop

; ---------------------------------------------------------------------------
GpuShade_Done:
    ; Le normali rimaste scoperte. DEVE essere zero: il descrittore ha una voce
    ; di coda che copre quel che i conteggi dei gruppi non coprono.
    movei   #GPU_SH_LEFT, r0
    store   r16, (r0)
    nop
    nop
    nop

    ; Barriera, bandiera, barriera, auto-arresto: da S46 la sequenza sta in
    ; UNA copia sola, GpuCommon_Done in gpu_body.s - i tre kernel sono
    ; residenti insieme nella stessa SRAM, quindi saltarci e' un salto
    ; qualsiasi. Si entra con r0 = la bandiera DONE di QUESTO kernel.
    movei   #GPU_SH_DONE, r0
    movei   #GpuCommon_Done, r1
    jump    T, (r1)
    nop

GpuShade_End:
    ; Sotto l'ingresso del kernel front-end (mappa in memory_map.py).
    .assert GpuShade_End <= GPU_SRAM_FRONT
    .68000
GpuShadeRomEnd:
