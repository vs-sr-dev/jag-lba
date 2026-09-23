; ---------------------------------------------------------------------------
; gpu_body.s - M3c mossa 2: il riempimento dei poligoni dei corpi sulla GPU TOM.
;
; COSA FA, E COSA NON FA
; ----------------------
; Da S44 costruisce ANCHE i bordi. Il flusso porta i VERTICI nudi (x, y,
; intensita' per angolo) e la fase A di ogni lavoro trascrive build_edges di
; engine_c/body_draw.c: le due divisioni per bordo che sul 68000 erano
; chiamate a __udivsi3 (~300 cicli l'una, il 49% del tempo corpi nel profilo
; S43) qui sono il divisore hardware, interbloccato (S40). I record escono
; nella SRAM locale a GPU_BODY_ETAB, 8 long l'uno, la STESSA forma che fino a
; S43 viaggiava in DRAM - cosi' la fase B (il riempitore, verificato su ferro
; da S34) resta identica: cambia solo la base da cui legge, e le sue otto
; letture per bordo per riga passano dalla DRAM alla SRAM.
;
; La divisione del gradiente lungo la riga resta dov'era: una per campata.
;
; PERCHE' QUESTA SPARTIZIONE
; --------------------------
; Il 68000 ora scrive 2 long per vertice invece di 8 per bordo (un terzo dei
; byte) e non divide piu' niente. L'aritmetica delicata (bias di mezzo pixel,
; resto/2, verso del gradiente) e' trascritta 1:1 dal C che il banco di prova
; su PC verifica al bit: se il risultato non combacia, il difetto e' in questa
; trascrizione e da nessun'altra parte. BODY_FILL_ON_GPU=0 rimette in servizio
; il modello C sulla stessa cartuccia: e' la bisezione, costa una build.
;
; LA VERIFICA E' PER IDENTITA'
; ----------------------------
; Le sonde 13..17 (BBOX, NONZERO, SUM del composto) devono restare IDENTICHE a
; quelle che il C produceva prima dello scambio. Non "cambiare in modo
; previsto": identiche. La regola dei bordi e' stata decisa e verificata a
; parte, nel passo A di S33, proprio per poter fare questo confronto.
;
; UN LANCIO PER ATTORE (S34)
; --------------------------
; Fino a S33 il 68000 lanciava la GPU una volta per POLIGONO: ~320 strette di
; mano per stanza, cioe' 320 occasioni di corsa fra i due processori. S33 ne ha
; chiuse QUATTRO, tutte scoperte sul ferro e nessuna visibile su BigPEmu - e
; quattro difetti tappati non dicono niente sui non visti. La soluzione non e'
; tapparne altri: e' non correre. Il C consegna un FLUSSO di poligoni e il ciclo
; su di essi sta QUI DENTRO.
;
; LA FORMA DI UN LAVORO (4 long di intestazione, poi i suoi VERTICI - S44)
;   +0  vertici   quanti vertici. ZERO = fine del flusso; DUE = una tratta di
;                 linea o sfera (una riga sola, colore piatto); >=3 poligono
;   +4  ymin      prima riga, gia' limitata allo schermo dal C
;   +8  ymax      ultima riga, compresa
;   +12 pixel     LA GPU CI SCRIVE il totale corrente uscendo dal lavoro
;   +16 i vertici, GPU_VTX_LONGS long l'uno:
;         +0  (x<<16) | (y & $FFFF)     due word FIRMATE in un long
;         +4  intensita' 0..255 nuda
;
; Il long +12 e' un totale CORRENTE e non i pixel del singolo poligono perche'
; cosi' non serve un registro in piu' per ricordare da dove era partito - e qui
; i registri sono tutti occupati. La differenza fra due totali consecutivi la fa
; il 68000, che di registri ne ha.
;
; La fine del flusso e' un terminatore e non un contatore per la stessa ragione:
; un contatore vorrebbe un registro vivo per tutto il ciclo. GPU_NJOB arriva lo
; stesso, ma solo per farsi rimandare indietro come diagnostica.
;
; LA FORMA DI UN BORDO IN SRAM (8 long, = build_edges in body_draw.c)
;   +0  right   1 = bordo di destra
;   +4  ytop    prima riga coperta
;   +8  ybot    ultima riga coperta, compresa
;   +12 bx      x dell'estremo da cui parte il DDA
;   +16 a0      frazione 16.16 alla riga ytop
;   +20 m       passo della frazione per riga, CON SEGNO
;   +24 i0      intensita' 8.8 alla riga ytop
;   +28 mi      passo dell'intensita' per riga, CON SEGNO
;
;   x(y) = bx + ((a0 + j*m) >> 16)      j = y - ytop
;   i(y) =       i0 + j*mi
;
; LE LATENZE SI PAGANO, NON SI SPERANO
; ------------------------------------
; Ogni `mult` e ogni `load` qui dentro e' seguito da attesa prima che il suo
; risultato venga letto. Su un emulatore i risultati sono istantanei e nessuna
; di queste attese e' mai stata messa alla prova; sul ferro hanno latenza, e la
; latenza di una lettura dalla DRAM dipende anche da quanto l'Object Processor
; sta occupando il bus in quel momento.
;
; E' il motivo per cui in S33 il guasto era INTERMITTENTE e a ogni passata il
; corpo arrivava un po' piu' su: non c'era un poligono cattivo, c'era un margine
; troppo stretto che passava o falliva a seconda del punto del quadro in cui la
; GPU partiva. Un difetto che si sposta da solo non e' un difetto di logica.
;
; Costano sei cicli l'una e Body_DrawAll gira una volta per stanza.
;
; NESSUN btst, NESSUN bset - E NON E' STILE
; -----------------------------------------
; I test di segno si fanno con `jump MI` sul risultato della `sub` che li
; precede, e i bit dei lati con `moveq` + `or`. La ragione e' che in S33, sul
; ferro vero, questa routine leggeva bene gli argomenti (verificato: la GPU
; riscrive ymin/ymax e combaciavano) e poi non tornava piu'. La parte che
; funzionava non ha salti condizionati; quella che si rompeva dipendeva tutta da
; `btst #31`, compreso il test che fa TERMINARE il ciclo delle righe.
;
; `btst`/`bset` sono le uniche istruzioni di questo file mai confermate su
; hardware: src/gpu_raster.s le usa, ma quel file non e' mai girato su un Jaguar
; vero. `jump MI` dopo un'aritmetica e' l'idioma canonico, usa il flag N che la
; `sub` imposta comunque, ed e' anche piu' corto.
;
; GLI INDIRIZZI NON SONO QUI. Li dichiara tools/memory_map.py e escono in
; include/dram_map.inc come GPU_*, e in engine_c/mem_map.h come JAG_GPU_*: e' la
; lezione di S24, dove due sorgenti che si dichiaravano gli indirizzi da soli
; hanno prodotto due significati per lo stesso long.
; ---------------------------------------------------------------------------

; Un bordo e' 8 long = 32 byte, cioe' `shlq #5` per l'indice: la costante non
; si dichiara perche' entra in un campo di shift, non in un operando.
;
; GLI INDIRIZZI DELLA SRAM NON SONO PIU' QUI (S44). Li dichiara
; tools/memory_map.py (GPU_SRAM_*), per la stessa lezione di S24 che vale per
; la DRAM: questo kernel e' cresciuto sopra l'ingresso dello shade, che era
; dichiarato NELL'ALTRO file, e al boot le due copie si sono sovrapposte -
; meta' del riempitore era codice altrui, e la GPU "finiva" scrivendo il DONE
; sbagliato. La .assert in fondo al blob ferma la build se ricapita.
GPU_BODY_ENTRY      EQU     GPU_SRAM_BODY
GPU_BODY_ETAB       EQU     GPU_SRAM_ETAB
GPU_BODY_HDRSLOT    EQU     GPU_SRAM_HDRSLOT
; L'ultima riga scrivibile del buffer (592 righe). Serve alla fase E per
; limitare ymin/ymax PRIMA del riempitore: nel modo flusso li limitava il C.
BODY_YMAX           EQU     BG_H-1
; Marcatore di progresso: l'ultimo punto del kernel raggiunto. Vale durante
; il debug di un blocco (S44: e' quel che ha inchiodato la sovrapposizione) e
; costa due store per lavoro: si tiene, come GPU_BODY_STAGE.
GPU_BODY_MARK       EQU     GPU_SRAM_MARK
BODY_XMAX           EQU     BG_W-1
; La porta del terminatore del ciclo dei lavori (S46): nel modo flusso vale
; GpuBody_Done, dentro un blocco di tratte vale GpuBody_EmBlkEnd. E' una
; cella dello stato del walker (EMST+16, dopo LISTP/CNT/RUNP/NPOLY).
GPU_BODY_EMDONE     EQU     GPU_SRAM_EMST+16

; L'ATTESA DOPO LA DIVISIONE. Mettere a 1 per rimettere i sedici nop.
;
; Non servono, e non e' una deduzione: il TRM pag. 42 lo dice in chiaro - "wait
; states will be inserted until the divide unit has completed". Il divisore E'
; interbloccato, quindi il registro del risultato si legge subito e l'attesa e'
; tempo buttato. I nop erano prudenza scritta quando la riga del manuale non era
; ancora stata trovata (la trova S40), e la prudenza costava DUE volte per
; campata, con migliaia di campate per stanza.
;
; Resta un interruttore invece di sparire perche' e' la bisezione se un giorno
; una stanza uscisse con i colori sbagliati sul ferro: una build, non una
; sessione. Vedi feedback_gpu_handshake_hw - questa e' esattamente la classe di
; difetti che BigPEmu non prova mai.
DIV_WAIT_NOPS       EQU     0

; SCALA DI BISEZIONE. Il difetto di S33 - "alcuni pixel, poi si pianta" - non
; si trova rileggendo: i valori dei bordi sono tutti minuscoli (dy al massimo
; 37, campate al massimo 20 pixel), quindi nessun ciclo puo' girare a lungo e
; il blocco e' di CONTROLLO. Questa costante taglia la routine in tre pezzi che
; si provano uno alla volta.
;
;   0  alza DONE e basta. Prova la copia in RAM GPU, l'indirizzo d'ingresso, il
;      lancio dal C e la stretta di mano. Nessun pixel, ma le righe di debug
;      DEVONO comparire. Se si pianta gia' qui, il difetto non e' nel disegno.
;   1  percorre righe e bordi, calcola x e intensita', ma non riempie. Prova la
;      terminazione dei due cicli e la decodifica dei bordi. Se 0 va e 1 no, il
;      difetto e' nei cicli; se vanno tutti e due, e' nella campata.
;   2  tutto.
GPU_BODY_STAGE      EQU     2

; ---------------------------------------------------------------------------
; Lato 68000: la copia del codice GPU nella sua RAM locale. Una volta sola, al
; boot, prima di qualsiasi RISCGO.
; ---------------------------------------------------------------------------
    .68000

; Il conteggio si arrotonda per ECCESSO. La divisione secca per 4 lascia fuori
; l'ultima istruzione ogni volta che il blocco non e' un multiplo di quattro
; byte, e le istruzioni GPU sono da 2 byte (6 la movei): meta' delle volte.
; L'ultima istruzione qui e' il `nop` nella casella di ritardo del salto di
; arresto - perderlo vuol dire che dopo aver alzato DONE la GPU esegue quel che
; trova, e quel che trova e' il codice del giro precedente.
; Copiare 3 byte in piu' dalla cartuccia non costa niente.
InitGpuBody:
    movem.l d0/a0/a1, -(sp)
    move.l  #GpuBodyRom, a0
    move.l  #GPU_BODY_ENTRY, a1
    move.w  #(GpuBodyRomEnd-GpuBodyRom+3)/4-1, d0
.igb_copy:
    move.l  (a0)+, (a1)+
    dbra    d0, .igb_copy
    movem.l (sp)+, d0/a0/a1
    rts

; ---------------------------------------------------------------------------
; Il codice GPU. Emesso nella ROM della cartuccia a GpuBodyRom, gira a
; GPU_BODY_ENTRY.
;
; MAPPA DEI REGISTRI - DUE fasi per lavoro da S44, coi target ricaricati al
; cambio di fase (feedback_gpu_multiphase_reload): la fase A (bordi) ha la sua
; mappa, scritta sopra GpuBody_Build. Qui sotto la fase B, il riempitore, che
; e' la stessa dal S34:
;
;   r0        appoggio
;   r1        PROSSIMA intestazione (la fase A ci arriva leggendo i vertici;
;             la fase B non lo tocca, JobNext se lo trova pronto)
;   r2        numero di bordi nella tabella SRAM
;   r3        base del framebuffer
;   r4        y, la riga corrente
;   r5        ymax, compresa
;   r6        pixel disegnati (esce in GPU_PIXELS)
;   r7        lati trovati: bit0 sinistro, bit1 destro
;   r8,r10    xl, sl
;   r9,r11    xr, sr
;   r12..r18  target di salto della fase, precaricati
;             (feedback_gpu_branch_pattern)
;   r19       j, poi appoggio della campata
;   r20..r29  campi del bordo, poi appoggio della campata
;   r30,r31   tracciatori di segno / appoggio
; ---------------------------------------------------------------------------
GpuBodyRom:
    .gpu
    .org    GPU_BODY_ENTRY

GpuBodyMain:
    ; --- G_DIVCTRL = 0: divisione intera senza segno.
    ; Anche le pendenze dei bordi (S44) si dividono cosi': il numeratore
    ; arriva GIA' scalato (dxa<<16), identico al C, quindi la modalita' 16.16
    ; del divisore non serve a nessuno qui dentro.
    movei   #G_DIVCTRL, r0
    moveq   #0, r19
    store   r19, (r0)
    nop
    nop
    nop
    nop

    ; --- Argomenti ---
    ; Solo tre, e nessuno per il poligono: ymin, ymax e il conteggio dei
    ; vertici stanno nell'intestazione di ogni lavoro, non qui.
    movei   #GPU_JOBS, r0
    load    (r0), r1                        ; base del flusso = 1a intestazione
    movei   #GPU_FB, r0
    load    (r0), r3
    movei   #GPU_NJOB, r0
    load    (r0), r2                        ; solo per la diagnostica qui sotto
    moveq   #0, r6                          ; pixel disegnati, TOTALE del lancio
    nop
    nop
    nop
    nop
    nop
    nop

    ; --- Rimando indietro quel che ho LETTO, prima di qualsiasi ciclo.
    ; Se la GPU parte con argomenti diversi da quelli che il 68000 ha scritto -
    ; scritture ancora in volo, che sul ferro esistono e su BigPEmu no - questi
    ; due long lo dicono, e lo dicono ANCHE se poi la routine impazzisce e non
    ; arriva mai in fondo. Confrontare il letto col scritto separa "la GPU
    ; sbaglia i conti" da "la GPU non ha mai visto i dati giusti". E' il paio
    ; che in S33 ha trovato tutti e quattro i difetti della stretta di mano.
    movei   #GPU_SAWJ, r0
    store   r1, (r0)
    movei   #GPU_SAWC, r0
    store   r2, (r0)
    nop
    nop
    nop

    .if GPU_BODY_STAGE=0
    movei   #GpuBody_Done, r30
    jump    T, (r30)
    nop
    .endif

    ; --- S46: IL MODO. 0 = flusso (il ciclo qui sotto), 1 = fase E. ---
    ; La continuazione di JobNext e la porta del terminatore si mettono
    ; SUBITO ai valori del modo flusso: i percorsi della fase E le
    ; sovrascrivono prima di ogni tuffo nel riempitore, quindi il modo 0 non
    ; deve mai toccarle di nuovo.
    movei   #GpuBody_JobLoop, r19
    movei   #GPU_SRAM_EMNEXT, r0
    store   r19, (r0)
    movei   #GpuBody_Done, r19
    movei   #GPU_BODY_EMDONE, r0
    store   r19, (r0)
    movei   #GpuBody_EmInit, r14
    movei   #GPU_EM_MODE, r0
    load    (r0), r0
    nop
    nop
    nop
    nop
    cmpq    #0, r0
    jump    NE, (r14)
    nop

; ---------------------------------------------------------------------------
; Il ciclo esterno: un lavoro per volta, finche' l'intestazione non dice zero.
;
; r30 e' usa e getta qui: fra un lavoro e l'altro non c'e' niente di vivo dentro
; (e' il tracciatore di segno del ciclo dei bordi), quindi i target di livello
; lavoro non consumano nessuno dei registri fissi.
;
; DA S44 il lavoro e' in DUE FASI: A costruisce i bordi in SRAM dai vertici, B
; riempie leggendo la tabella. r12..r18 valgono una cosa per fase e si
; ricaricano al cambio (feedback_gpu_multiphase_reload): e' il trucco di S13,
; e qui serve davvero perche' le due fasi insieme vorrebbero piu' target di
; quanti registri restino.
;
; L'intestazione corrente si mette da parte in GpuBodyHdrSlot (SRAM): il
; totale dei pixel ci si scrive a fine lavoro, quando r1 punta gia' al
; successivo. E' una cella di SRAM e non un registro perche' un registro
; libero per tutto il lavoro non c'e'.
; ---------------------------------------------------------------------------
GpuBody_JobLoop:
    ; PRECARICATO PRIMA DEL cmpq: la movei aggiorna i flag e cancellerebbe il
    ; confronto (feedback_gpu_branch_pattern). S46: il bersaglio arriva dalla
    ; porta EMDONE - Done nel modo flusso, EmBlkEnd dentro un blocco di
    ; tratte. La latenza e' coperta dalle letture dell'intestazione.
    movei   #GPU_BODY_EMDONE, r30
    load    (r30), r30

    movei   #GPU_BODY_HDRSLOT, r19
    store   r1, (r19)                       ; intestazione del lavoro corrente

    move    r1, r0
    load    (r0), r2                        ; quanti vertici (0 = terminatore)
    addq    #4, r0
    load    (r0), r4                        ; ymin
    addq    #4, r0
    load    (r0), r5                        ; ymax
    addq    #16, r1                         ; r1 = base dei vertici del lavoro
    nop
    nop
    nop
    nop
    nop

    cmpq    #0, r2
    jump    EQ, (r30)                       ; terminatore -> finito
    nop

    ; DUE vertici = una tratta: il conteggio e' il marcatore (un poligono ne
    ; ha almeno tre, e due vertici sulla stessa riga non produrrebbero nessun
    ; bordo: same_y). PRECARICATO prima del cmpq, come sopra.
    movei   #GpuBody_BuildRun, r30
    cmpq    #2, r2
    jump    EQ, (r30)
    nop

; ---------------------------------------------------------------------------
; FASE A, poligono: build_edges di body_draw.c, trascritto 1:1.
;
; REGISTRI DELLA FASE (si buttano al passaggio alla fase B):
;   r7          ne, i bordi scritti in tabella
;   r8,r9,r10   x0, y0, i0 - il vertice 0, per il bordo di chiusura
;   r19,r20,r21 xa, ya, ia - il vertice di coda del bordo corrente
;   r22,r23,r24 xb, yb, ib - il vertice di testa (SOLO LETTI da BEdge:
;               BPairNext li fa scivolare in coda DOPO il ritorno)
;   r25         il puntatore di scrittura nella tabella
;   r15         l'ingresso di BEdge, r16 il ritorno (feedback_gpu_indirect_call)
;   r2          conto alla rovescia delle coppie (il conteggio scende a zero e
;               si chiude con NE: sulla GPU i confronti firmati non esistono,
;               feedback_gpu_cc_no_signed)
;   dentro BEdge: r26 dy, r27 right, r28 by, r29 i1, r30 i2, r31 oy-by,
;               r17 slope, r18 fx, r11 di, r12 istep, r13 iacc0, r0/r14 appoggio
; ---------------------------------------------------------------------------
GpuBody_Build:
    moveq   #0, r7                          ; ne = 0
    movei   #GPU_BODY_ETAB, r25
    movei   #GpuBody_BEdge, r15

    ; il vertice 0: spacchetta e salva
    load    (r1), r0                        ; (x<<16)|y
    addq    #4, r1
    load    (r1), r21                       ; intensita'
    addq    #4, r1
    nop
    nop
    nop
    nop
    nop
    move    r0, r19
    sharq   #16, r19                        ; xa = x0 (firmato)
    move    r0, r20
    shlq    #16, r20
    sharq   #16, r20                        ; ya = y0 (firmato)
    move    r19, r8
    move    r20, r9
    move    r21, r10

    subq    #1, r2                          ; nv-1 coppie consecutive

GpuBody_BPair:
    load    (r1), r0
    addq    #4, r1
    load    (r1), r24                       ; ib
    addq    #4, r1
    nop
    nop
    nop
    nop
    nop
    move    r0, r22
    sharq   #16, r22                        ; xb
    move    r0, r23
    shlq    #16, r23
    sharq   #16, r23                        ; yb
    movei   #GpuBody_BPairNext, r16
    jump    T, (r15)                        ; elabora il bordo (a -> b)
    nop

GpuBody_BPairNext:
    move    r22, r19                        ; il vertice di testa diventa coda
    move    r23, r20
    move    r24, r21
    movei   #GpuBody_BPair, r0              ; PRIMA del subq: movei tocca i flag
    subq    #1, r2
    jump    NE, (r0)
    nop

    ; il bordo di chiusura: ultimo vertice -> vertice 0
    move    r8, r22
    move    r9, r23
    move    r10, r24
    movei   #GpuBody_BWrapDone, r16
    jump    T, (r15)
    nop

GpuBody_BWrapDone:
    move    r7, r2                          ; ne per la fase B
GpuBody_FillTargets:
    ; --- FASE B: i bersagli del riempitore, ricaricati a ogni lavoro ---
    movei   #GpuBody_RowLoop, r12
    movei   #GpuBody_EdgeLoop, r13
    movei   #GpuBody_EdgeNext, r14
    movei   #GpuBody_RowNext, r15
    movei   #GpuBody_JobNext, r16
    movei   #GpuBody_Reverse, r17
    movei   #GpuBody_EdgeDone, r18
    jump    T, (r12)
    nop

; ---------------------------------------------------------------------------
; FASE A, tratta (2 vertici): due bordi verticali piatti in tabella, e il
; riempitore generico fa il resto - m=0 e j=0 danno x=bx esatto, l'intensita'
; uguale ai due capi annulla il gradiente. E' la stessa coppia che emit_run
; costruiva sul 68000 fino a S43.
; ---------------------------------------------------------------------------
GpuBody_BuildRun:
    load    (r1), r0                        ; xy sinistro
    addq    #4, r1
    load    (r1), r19                       ; colore sinistro
    addq    #4, r1
    load    (r1), r20                       ; xy destro
    addq    #4, r1
    load    (r1), r21                       ; colore destro (= sinistro)
    addq    #4, r1
    movei   #$7FFF, r14                     ; a0: il mezzo pixel dei bordi veri
    moveq   #0, r11
    move    r0, r22
    sharq   #16, r22                        ; xl

    movei   #GPU_BODY_ETAB, r0
    store   r11, (r0)                       ; [0] right = 0
    addq    #4, r0
    store   r4, (r0)                        ; [1] ytop = la riga
    addq    #4, r0
    store   r4, (r0)                        ; [2] ybot
    addq    #4, r0
    store   r22, (r0)                       ; [3] bx = xl
    addq    #4, r0
    store   r14, (r0)                       ; [4] a0
    addq    #4, r0
    store   r11, (r0)                       ; [5] m = 0
    addq    #4, r0
    move    r19, r13
    shlq    #8, r13
    store   r13, (r0)                       ; [6] i0 = colore<<8
    addq    #4, r0
    store   r11, (r0)                       ; [7] mi = 0
    addq    #4, r0

    moveq   #1, r12
    store   r12, (r0)                       ; [0] right = 1
    addq    #4, r0
    store   r4, (r0)
    addq    #4, r0
    store   r4, (r0)
    addq    #4, r0
    move    r20, r22
    sharq   #16, r22                        ; xr
    store   r22, (r0)
    addq    #4, r0
    store   r14, (r0)                       ; a0
    addq    #4, r0
    store   r11, (r0)                       ; m = 0
    addq    #4, r0
    move    r21, r13
    shlq    #8, r13
    store   r13, (r0)
    addq    #4, r0
    store   r11, (r0)

    ; r2 vale gia' 2 dall'intestazione = i bordi in tabella
    movei   #GpuBody_FillTargets, r0
    jump    T, (r0)
    nop

; ---------------------------------------------------------------------------
; UN BORDO: (xa,ya,ia) -> (xb,yb,ib), record in tabella se ya != yb.
; Ingresso da (r15), ritorno a (r16). E' build_edges riga per riga; i
; commenti con la C originale accanto.
; ---------------------------------------------------------------------------
GpuBody_BEdge:
    ; dy = yb - ya; ya == yb -> niente bordo (same_y)
    move    r23, r26
    sub     r20, r26
    jump    EQ, (r16)
    nop

    ; right = (yb < ya), e dy diventa |dy|
    moveq   #0, r27
    cmpq    #0, r26
    jr      PL, .bnr
    nop
    moveq   #1, r27
    neg     r26
.bnr:

    ; l'estremo di partenza: a destra parte da X maggiore, a sinistra da X
    ; minore. I due confronti sono lo stesso PL con gli operandi scambiati
    ; (niente LE/GE sulla GPU: feedback_gpu_cc_no_signed).
    cmpq    #0, r27
    jr      NE, .bselr
    nop
    move    r22, r0                         ; sinistro: parte da A se xb-xa >= 0
    sub     r19, r0
    jr      T, .bselt
    nop
.bselr:
    move    r19, r0                         ; destro: parte da A se xa-xb >= 0
    sub     r22, r0
.bselt:
    jr      MI, .bselb
    nop
    ; parte da A: bx=xa, by=ya, i1=ia, i2=ib, oy=yb
    move    r25, r0
    addq    #12, r0
    store   r19, (r0)                       ; [3] bx = xa
    move    r20, r28                        ; by
    move    r21, r29                        ; i1
    move    r24, r30                        ; i2
    move    r23, r31                        ; oy
    sub     r28, r31                        ; r31 = oy - by (il verso, +-dy)
    jr      T, .bseld
    nop
.bselb:
    ; parte da B: bx=xb, by=yb, i1=ib, i2=ia, oy=ya
    move    r25, r0
    addq    #12, r0
    store   r22, (r0)                       ; [3] bx = xb
    move    r23, r28
    move    r24, r29
    move    r21, r30
    move    r20, r31
    sub     r28, r31
.bseld:
    move    r25, r0
    store   r27, (r0)                       ; [0] right

    ; --- slope = (dxa<<16) / dy, resto ricomputato ---
    ; La divisione e' interbloccata (S40); il divisore si copia perche' la
    ; div lo corrompe (feedback_gpu_div_clobbers_divisor). Il resto NON si
    ; chiede all'hardware: ur = unum - uq*dy, col prodotto spezzato lo16/hi16
    ; perche' uq arriva a 26 bit e la mult e' 16x16.
    move    r22, r0
    sub     r19, r0
    abs     r0                              ; dxa = |xb - xa|
    move    r0, r14
    shlq    #16, r14                        ; unum = dxa<<16
    move    r14, r17
    move    r26, r18                        ; copia del divisore
    div     r18, r17                        ; r17 = uq = unum / dy
    .if DIV_WAIT_NOPS
    .rept   16
    nop
    .endr
    .endif
    move    r17, r0
    shlq    #16, r0
    shrq    #16, r0                         ; lo16(uq)
    move    r17, r18
    shrq    #16, r18                        ; hi16(uq)
    mult    r26, r0
    mult    r26, r18
    nop
    nop
    nop
    nop
    nop
    nop
    shlq    #16, r18
    add     r18, r0                         ; uq * dy
    sub     r0, r14                         ; r14 = ur
    shrq    #1, r14                         ; resto/2, come `shr ax,1`
    movei   #$7FFF, r18
    cmpq    #0, r27
    jr      NE, .bfxr
    nop
    add     r14, r18                        ; sinistra: 7FFF + resto/2
    jr      T, .bfxd
    nop
.bfxr:
    sub     r14, r18                        ; destra:   7FFF - resto/2
.bfxd:                                      ; r18 = fx

    ; --- il gradiente del bordo, 8.8: iq = (|di|<<8) / dy ---
    ; Numeratore e quoziente stanno in 16 bit (|di| <= 255), quindi il
    ; prodotto del resto e' una mult sola.
    move    r30, r11
    sub     r29, r11                        ; di = i2 - i1 (firmato)
    move    r11, r0
    abs     r0
    shlq    #8, r0                          ; inum = |di|<<8
    move    r0, r12
    move    r26, r14                        ; copia del divisore
    div     r14, r12                        ; r12 = iq
    .if DIV_WAIT_NOPS
    .rept   16
    nop
    .endr
    .endif
    move    r12, r14
    mult    r26, r14                        ; iq * dy
    nop
    nop
    nop
    nop
    nop
    nop
    sub     r14, r0                         ; r0 = ir
    ; `shr al,1` lavora sul byte basso e `add al,7Fh` non propaga: il
    ; mascheramento a 8 bit FA PARTE della regola (vedi build_edges).
    shlq    #24, r0
    shrq    #24, r0                         ; ir & $FF
    shrq    #1, r0
    movei   #$7F, r14
    cmpq    #0, r11
    jr      MI, .bineg
    nop
    add     r14, r0                         ; di >= 0: t + $7F
    jr      T, .bidone
    nop
.bineg:
    sub     r0, r14                         ; di < 0: ($7F - t) & $FF
    shlq    #24, r14
    shrq    #24, r14
    move    r14, r0
.bidone:
    move    r29, r13
    shlq    #8, r13
    add     r0, r13                         ; r13 = iacc0 = (i1<<8) + agg

    ; istep col segno di di; slope negata sui bordi di destra
    cmpq    #0, r11
    jr      PL, .bis
    nop
    neg     r12                             ; istep = -iq
.bis:
    cmpq    #0, r27
    jr      EQ, .bsl
    nop
    neg     r17                             ; slope = -slope
.bsl:

    ; --- riassociazione su j = y - ytop: il verso decide dove parte il DDA ---
    movei   #GpuBody_BUp, r14
    cmpq    #0, r31                         ; oy - by: MI = il bordo sale
    jump    MI, (r14)
    nop

    ; il bordo SCENDE: ytop = by, a0 = fx, m = slope, i0 = iacc0, mi = istep
    move    r25, r0
    addq    #4, r0
    store   r28, (r0)                       ; [1] ytop = by
    addq    #4, r0
    move    r28, r14
    add     r26, r14
    store   r14, (r0)                       ; [2] ybot = by + dy
    addq    #8, r0
    store   r18, (r0)                       ; [4] a0 = fx
    addq    #4, r0
    store   r17, (r0)                       ; [5] m = slope
    addq    #4, r0
    store   r13, (r0)                       ; [6] i0 = iacc0
    addq    #4, r0
    store   r12, (r0)                       ; [7] mi = istep
    movei   #GpuBody_BEdgeFin, r0
    jump    T, (r0)
    nop

GpuBody_BUp:
    ; il bordo SALE: si riporta tutto alla riga piu' alta.
    ;   ytop = by - dy          a0 = fx + slope*dy       m  = -slope
    ;   ybot = by               i0 = iacc0 + istep*dy    mi = -istep
    move    r25, r0
    addq    #4, r0
    move    r28, r14
    sub     r26, r14
    store   r14, (r0)                       ; [1] ytop = by - dy
    addq    #4, r0
    store   r28, (r0)                       ; [2] ybot = by
    addq    #8, r0

    ; slope*dy col prodotto spezzato (|slope| fino a 26 bit), segno fuori
    ; (feedback_gpu_q1616_mult). i1/i2 (r29/r30) sono gia' morti: appoggio.
    move    r17, r14
    abs     r14
    move    r14, r29
    shlq    #16, r29
    shrq    #16, r29                        ; lo16(|slope|)
    shrq    #16, r14                        ; hi16(|slope|)
    mult    r26, r29
    mult    r26, r14
    nop
    nop
    nop
    nop
    nop
    nop
    shlq    #16, r14
    add     r29, r14                        ; |slope| * dy
    cmpq    #0, r17
    jr      PL, .bupsp
    nop
    neg     r14
.bupsp:
    add     r18, r14
    store   r14, (r0)                       ; [4] a0 = fx + slope*dy
    addq    #4, r0
    move    r17, r14
    neg     r14
    store   r14, (r0)                       ; [5] m = -slope
    addq    #4, r0

    ; istep*dy: iq*dy <= inum < 2^16, una mult sola; segno fuori
    move    r12, r14
    abs     r14
    mult    r26, r14
    nop
    nop
    nop
    nop
    nop
    nop
    cmpq    #0, r12
    jr      PL, .bupip
    nop
    neg     r14
.bupip:
    add     r13, r14
    store   r14, (r0)                       ; [6] i0 = iacc0 + istep*dy
    addq    #4, r0
    move    r12, r14
    neg     r14
    store   r14, (r0)                       ; [7] mi = -istep

GpuBody_BEdgeFin:
    addq    #32, r25                        ; prossimo record
    addq    #1, r7                          ; ne++
    jump    T, (r16)                        ; ritorno al chiamante
    nop

; ---------------------------------------------------------------------------
GpuBody_RowLoop:
    ; y > ymax -> finito.  (ymax - y) < 0 ?
    move    r5, r0
    sub     r4, r0
    jump    MI, (r16)
    nop

    moveq   #0, r7                          ; nessun lato trovato
    moveq   #0, r20                         ; e = 0

GpuBody_EdgeLoop:
    cmp     r2, r20                         ; e - numero di bordi
    jump    PL, (r18)                       ; e >= n -> EdgeDone
    nop

    ; --- &bordo[e] = tabella SRAM + e*32 (S44: i bordi non stanno piu' nel
    ; flusso; queste otto letture per bordo per riga erano DRAM, ora SRAM) ---
    move    r20, r21
    shlq    #5, r21
    movei   #GPU_BODY_ETAB, r0
    add     r0, r21

    move    r21, r0
    load    (r0), r22                       ; right
    addq    #4, r0
    load    (r0), r23                       ; ytop
    addq    #4, r0
    load    (r0), r24                       ; ybot
    addq    #4, r0
    load    (r0), r25                       ; bx
    addq    #4, r0
    load    (r0), r26                       ; a0
    addq    #4, r0
    load    (r0), r27                       ; m
    addq    #4, r0
    load    (r0), r28                       ; i0
    addq    #4, r0
    load    (r0), r29                       ; mi

    ; --- j = y - ytop, fuori campo se j < 0 oppure y > ybot ---
    move    r4, r19
    sub     r23, r19                        ; r19 = j
    jump    MI, (r14)                       ; j < 0 -> bordo non attivo
    nop
    move    r24, r0
    sub     r4, r0                          ; ybot - y
    jump    MI, (r14)                       ; y > ybot -> non attivo
    nop

    ; --- x = bx + ((a0 + j*m) >> 16) ---
    ; |m| arriva a 26 bit, percio' il prodotto va spezzato lo16/hi16: la `mult`
    ; della GPU TOM e' 16x16 e da sola troncherebbe la pendenza. Il segno sta
    ; fuori (feedback_gpu_q1616_mult): abs, moltiplica, e nega alla fine.
    move    r27, r30                        ; tracciatore di segno
    move    r27, r31
    abs     r31                             ; |m|
    move    r31, r0
    shrq    #16, r0                         ; hi16
    shlq    #16, r31
    shrq    #16, r31                        ; lo16
    mult    r19, r31                        ; j * lo16
    mult    r19, r0                         ; j * hi16
    nop
    nop
    nop
    nop
    nop
    nop
    shlq    #16, r0
    add     r0, r31                         ; j * |m|, intero
    cmpq    #0, r30
    jr      PL, GpuBody_MPos
    nop
    neg     r31
GpuBody_MPos:
    add     r26, r31                        ; a0 + j*m
    sharq   #16, r31
    add     r25, r31                        ; r31 = x

    ; --- s = i0 + j*mi ---
    ; |mi| sta in 16 bit (255<<8 al massimo), quindi una `mult` sola.
    move    r29, r30
    move    r29, r0
    abs     r0                              ; |mi|
    mult    r19, r0                         ; j * |mi|
    nop
    nop
    nop
    nop
    nop
    nop
    cmpq    #0, r30
    jr      PL, GpuBody_MiPos
    nop
    neg     r0
GpuBody_MiPos:
    add     r28, r0                         ; r0 = s

    ; --- il lato lo dice il bordo, non un ordinamento ---
    cmpq    #0, r22
    jr      EQ, GpuBody_IsLeft
    nop
    move    r31, r9                         ; xr
    move    r0, r11                         ; sr
    moveq   #2, r0
    or      r0, r7
    jump    T, (r14)
    nop
GpuBody_IsLeft:
    move    r31, r8                         ; xl
    move    r0, r10                         ; sl
    moveq   #1, r0
    or      r0, r7

GpuBody_EdgeNext:
    addq    #1, r20
    jump    T, (r13)
    nop

; ---------------------------------------------------------------------------
GpuBody_EdgeDone:
    ; Servono TUTTI E DUE i lati. Il C fa lo stesso controllo, e non e' teorico:
    ; sulle tre stanze del riferimento 669 righe hanno zero bordi attivi (e'
    ; l'ultima riga di ogni poligono con la regola semiaperta). Senza questo
    ; controllo si riempirebbe con la riga precedente.
    cmpq    #3, r7
    jump    NE, (r15)
    nop

    ; xr < xl -> la riga non ha spessore (`jc nextlig`: il gioco non scambia).
    move    r9, r0
    sub     r8, r0
    jump    MI, (r15)
    nop

    ; tutta fuori a sinistra o a destra
    cmpq    #0, r9                          ; xr < 0
    jump    MI, (r15)
    nop
    movei   #BODY_XMAX, r0
    sub     r8, r0                          ; 639 - xl
    jump    MI, (r15)                       ; xl >= 640
    nop

    .if GPU_BODY_STAGE=1
    jump    T, (r15)
    nop
    .endif

    ; --- passo del gradiente: il VERSO decide il segno ---
    ; La divisione e' senza segno e il numeratore sempre positivo, come in
    ; SVGAPolyGouraud/GouraudInverse. Cosi' l'accumulatore parte dall'estremo di
    ; intensita' piu' bassa e non puo' scendere sotto il minimo: un'unita' sotto
    ; la base di una rampa da 16 e' la cima della rampa PRECEDENTE, cioe' i
    ; puntini bianchi di S32.
    move    r11, r0
    sub     r10, r0                         ; sr - sl
    jump    MI, (r17)                       ; sr < sl -> all'indietro
    nop

; --- campata in avanti: da xl verso destra, partendo da sl ---
    move    r9, r19
    sub     r8, r19                         ; dx
    moveq   #0, r20                         ; passo = 0 se dx == 0
    cmpq    #0, r19
    jr      EQ, GpuBody_FwdNoDiv
    nop
    move    r11, r20
    sub     r10, r20
    div     r19, r20                        ; (sr - sl) / dx
GpuBody_FwdNoDiv:
    ; LA DIVISIONE ERA IL PUNTO PIU' ESPOSTO ALLA DIFFERENZA EMULATORE/FERRO, e
    ; adesso non lo e' piu': il TRM pag. 42 dice che il divisore INSERISCE LUI
    ; gli stati di attesa finche' non ha finito. Il risultato si legge subito.
    ;
    ; Il testo che stava qui diceva "non e' documentato con certezza se il
    ; registro del risultato sia interbloccato" e ne concludeva sedici nop di
    ; prudenza. La riga del manuale c'era, non era stata trovata: e' il costo di
    ; una prudenza scritta contro una domanda aperta invece che contro un fatto.
    ;
    ; L'ETICHETTA RESTA PRIMA del blocco condizionale, com'era: se un giorno si
    ; rimettessero i nop, l'attesa deve tornare sul percorso COMUNE ai due rami
    ; senza allontanare il salto corto (il `jr` porta quindici parole).
    .if DIV_WAIT_NOPS
    .rept   16
    nop
    .endr
    .endif
    move    r10, r21                        ; acc = sl
    move    r8, r22                         ; x = xl

    ; taglio a sinistra: l'accumulatore avanza lo stesso sui pixel scartati,
    ; altrimenti tagliare la campata sfaserebbe il gradiente su quelli che
    ; restano. Non trabocca: -xl <= xr-xl, quindi passo*(-xl) <= sr-sl.
    cmpq    #0, r22
    jr      PL, GpuBody_FwdNoClipL
    nop
    move    r22, r0
    neg     r0
    mult    r20, r0                         ; passo * (-x)
    nop
    nop
    nop
    nop
    nop
    nop
    add     r0, r21
    moveq   #0, r22
GpuBody_FwdNoClipL:
    movei   #BODY_XMAX, r23
    move    r23, r0
    sub     r9, r0                          ; 639 - xr
    jr      MI, GpuBody_FwdXe
    nop
    move    r9, r23                         ; xr sta dentro: xe = xr
GpuBody_FwdXe:
    move    r23, r24
    sub     r22, r24
    addq    #1, r24                         ; conteggio = xe - x + 1

    ; indirizzo = fb + y*640 + x   (640 = 512 + 128)
    move    r4, r25
    shlq    #9, r25
    move    r4, r26
    shlq    #7, r26
    add     r26, r25
    add     r3, r25
    add     r22, r25

GpuBody_FwdLoop:
    move    r21, r0
    sharq   #8, r0                          ; il byte ALTO, come `mov [edi],dh`
    storeb  r0, (r25)
    addq    #1, r25
    add     r20, r21
    addq    #1, r6
    subq    #1, r24
    jr      NE, GpuBody_FwdLoop
    nop
    jump    T, (r15)
    nop

; --- campata all'indietro: da xr verso sinistra, partendo da sr ---
GpuBody_Reverse:
    move    r9, r19
    sub     r8, r19                         ; dx
    moveq   #0, r20
    cmpq    #0, r19
    jr      EQ, GpuBody_RevNoDiv
    nop
    move    r10, r20
    sub     r11, r20                        ; sl - sr, positivo
    div     r19, r20
GpuBody_RevNoDiv:
    ; Come sul percorso in avanti: il divisore si interblocca da se'.
    .if DIV_WAIT_NOPS
    .rept   16
    nop
    .endr
    .endif
    move    r11, r21                        ; acc = sr
    move    r9, r22                         ; x = xr

    movei   #BODY_XMAX, r23
    move    r23, r0
    sub     r22, r0                         ; 639 - x
    jr      PL, GpuBody_RevNoClipR
    nop
    move    r22, r0
    sub     r23, r0                         ; x - 639
    mult    r20, r0
    nop
    nop
    nop
    nop
    nop
    nop
    add     r0, r21
    move    r23, r22                        ; x = 639
GpuBody_RevNoClipR:
    move    r8, r23                         ; xe = xl
    cmpq    #0, r23
    jr      PL, GpuBody_RevXe
    nop
    moveq   #0, r23                         ; xl < 0 -> xe = 0
GpuBody_RevXe:
    move    r22, r24
    sub     r23, r24
    addq    #1, r24                         ; conteggio = x - xe + 1

    move    r4, r25
    shlq    #9, r25
    move    r4, r26
    shlq    #7, r26
    add     r26, r25
    add     r3, r25
    add     r22, r25

GpuBody_RevLoop:
    move    r21, r0
    sharq   #8, r0
    storeb  r0, (r25)
    subq    #1, r25
    add     r20, r21
    addq    #1, r6
    subq    #1, r24
    jr      NE, GpuBody_RevLoop
    nop

GpuBody_RowNext:
    addq    #1, r4
    jump    T, (r12)
    nop

; ---------------------------------------------------------------------------
; Fine di UN lavoro: si lascia il totale nell'intestazione e si passa oltre.
;
; r1 punta GIA' alla prossima intestazione: la fase A l'ha fatto avanzare
; leggendo i vertici, e la fase B non lo tocca. L'intestazione del lavoro
; appena chiuso si ripesca da GpuBodyHdrSlot, dov'era stata messa in testa.
; ---------------------------------------------------------------------------
GpuBody_JobNext:
    ; S46: la continuazione non e' piu' fissa. Nel modo flusso la cella vale
    ; GpuBody_JobLoop (scritta all'ingresso); nella fase E ogni tuffo la
    ; scrive col proprio ritorno. La latenza della load e' coperta dalle sei
    ; istruzioni che seguono prima del salto.
    movei   #GPU_SRAM_EMNEXT, r30
    load    (r30), r30

    movei   #GPU_BODY_HDRSLOT, r0
    load    (r0), r19
    nop
    nop
    nop
    addq    #12, r19
    store   r6, (r19)                       ; totale corrente dei pixel
    jump    T, (r30)
    nop

; ---------------------------------------------------------------------------
; FASE E (S46): il WALKER della lista ordinata - list_run_c/gather_poly di
; body_draw.c, trascritti 1:1. La GPU cammina wlist: una voce poligono si
; RACCOGLIE nell'area di regia (GPU_EM_STAGE, in DRAM, stessa forma di un
; lavoro del flusso) leggendo intestazione e angoli JBD dalla cartuccia -
; come gpu_front.s fa gia' da S45 - lo schermo da wscr e l'intensita' da
; wint; poi si salta in GpuBody_Build con r1 puntato alla regia: le fasi A e
; B, provate sul ferro da S34/S44, NON CAMBIANO DI UNA RIGA. Una voce
; entita' consuma il BLOCCO successivo del flusso ([conteggio][tratte]), che
; il 68000 ha scritto nello stesso ordine: la corrispondenza e' posizionale.
;
; LO STATO STA NELLE CELLE EMST, NON NEI REGISTRI: fra un tuffo nel
; riempitore e l'altro le fasi A+B li consumano tutti. Le celle:
;   +0 LISTP   +4 CNT   +8 RUNP   +12 NRUN
;   +16 DRAWN  +20 PREV +24 NPOLY
;
; wint SI LEGGE A LONG e la word si sceglie col bit 0 dell'indice: loadw
; dalla DRAM non e' mai stato provato da un kernel GPU, load si' da S33. Il
; mascheramento a $FF della tinta rende il segno della word irrilevante
; (mod 256, come il C).
; ---------------------------------------------------------------------------
GpuBody_EmInit:

    movei   #GPU_EM_LIST, r0
    load    (r0), r19                       ; base di wlist
    addq    #4, r0
    load    (r0), r20                       ; live
    addq    #8, r0
    load    (r0), r22                       ; nb_polys
    movei   #GPU_JOBS, r0
    load    (r0), r23                       ; base dei blocchi di tratte
    movei   #GPU_SRAM_EMST, r14
    store   r19, (r14)                      ; +0  LISTP
    addq    #4, r14
    store   r20, (r14)                      ; +4  CNT
    addq    #4, r14
    store   r23, (r14)                      ; +8  RUNP
    addq    #4, r14
    store   r22, (r14)                      ; +12 NPOLY

    ; Rimando indietro modo e voci PRIMA di qualsiasi ciclo (regola di S33).
    movei   #$10000, r14
    or      r20, r14
    movei   #GPU_EM_SAWL, r0
    store   r14, (r0)

; Il giro: una voce per iterazione. A lista esaurita si esce dalla porta di
; sempre: i totali stanno gia' tutti nelle chiavi di wlist.
GpuBody_EmLoop:
    movei   #GpuBody_Done, r30
    movei   #GPU_SRAM_EMST, r14
    move    r14, r0
    addq    #4, r0
    load    (r0), r2                        ; CNT
    load    (r14), r1                       ; LISTP
    nop
    nop
    nop
    cmpq    #0, r2
    jump    EQ, (r30)                       ; lista finita
    nop
    subq    #1, r2
    store   r2, (r0)
    addq    #4, r1                          ; la chiave non serve: si legge +4
    load    (r1), r7                        ; idx<<16 | off
    addq    #4, r1
    store   r1, (r14)                       ; LISTP -> voce successiva
    move    r14, r0
    addq    #12, r0
    load    (r0), r8                        ; NPOLY
    move    r7, r9
    shrq    #16, r9                         ; gi - riempie la latenza di r8
    movei   #GpuBody_EmRunBlk, r30
    move    r9, r0
    sub     r8, r0                          ; gi - nb_polys
    jump    PL, (r30)                       ; entita' -> blocco di tratte
    nop

    ; --- VOCE POLIGONO: la raccolta (gather_poly) ---
    movei   #GPU_EM_POLYS, r0
    load    (r0), r10
    move    r7, r11
    shlq    #16, r11
    shrq    #16, r11                        ; off
    shlq    #3, r11
    nop
    add     r11, r10                        ; &intestazione JBD
    load    (r10), r19                      ; mat|nbp|coul1|-
    addq    #4, r10
    load    (r10), r20                      ; fn<<16 | cstart

    ; le basi, mentre le due letture volano (argomenti ADIACENTI in mappa)
    movei   #GPU_EM_WSCR, r0
    load    (r0), r8
    addq    #4, r0
    load    (r0), r9                        ; wint
    addq    #4, r0
    load    (r0), r12                       ; nb_points
    addq    #4, r0
    load    (r0), r13                       ; nb_normals

    move    r19, r15
    shrq    #24, r15                        ; mat
    move    r19, r16
    shlq    #8, r16
    shrq    #24, r16                        ; nbp
    move    r19, r17
    shlq    #16, r17
    shrq    #24, r17                        ; coul1

    ; n = min(nbp, GPU_EDGE_MAX); n < 3 -> voce successiva
    moveq   #GPU_EDGE_MAX, r0
    sub     r16, r0
    jr      PL, .egn
    nop
    moveq   #GPU_EDGE_MAX, r16
.egn:
    movei   #GpuBody_EmSkip, r30
    move    r16, r0
    subq    #3, r0
    jump    MI, (r30)
    nop

    ; il puntatore degli angoli
    movei   #GPU_EM_CORNERS, r0
    load    (r0), r1
    move    r20, r11
    shlq    #16, r11
    shrq    #16, r11                        ; cstart
    shlq    #2, r11
    nop
    add     r11, r1

    ; piatto o gouraud: r15 diventa la bandiera, r18 il raw piatto.
    ; I rami scavalcano il blocco della lettura di wint[fn], che e' oltre la
    ; portata di un jr: bersagli precaricati (feedback_gpu_branch_pattern).
    moveq   #0, r18
    movei   #GpuBody_EmFlat, r30
    movei   #GpuBody_EmGou, r31
    move    r15, r0
    subq    #7, r0
    jump    MI, (r30)                       ; mat < 7: tinta nuda
    nop
    move    r15, r0
    subq    #9, r0
    jump    PL, (r31)                       ; mat >= 9: gouraud
    nop
    move    r20, r19                        ; 7 <= mat < 9: raw = wint[fn]
    shrq    #16, r19                        ; fn
    move    r19, r0
    sub     r13, r0
    jump    PL, (r30)                       ; fn >= nb_normals -> 0
    nop
    move    r19, r14
    shlq    #1, r14
    shrq    #2, r14
    shlq    #2, r14                         ; (fn*2) & ~3
    add     r9, r14
    load    (r14), r18
    move    r19, r0
    shlq    #31, r0                         ; parita' di fn nel segno
    nop
    nop
    cmpq    #0, r0
    jr      MI, .egfo
    nop
    shrq    #16, r18                        ; pari: word alta
    jr      T, GpuBody_EmFlat
    nop
.egfo:
    shlq    #16, r18
    shrq    #16, r18                        ; dispari: word bassa
GpuBody_EmFlat:
    moveq   #0, r15
    jr      T, GpuBody_EmCini
    nop
GpuBody_EmGou:
    moveq   #1, r15
GpuBody_EmCini:
    ; rettangolo di righe e regia
    movei   #$7FFF, r4                      ; miny
    movei   #$FFFF8001, r5                  ; maxy = -32767
    movei   #GPU_EM_STAGE, r10
    addq    #16, r10                        ; i vertici della regia
    move    r16, r2
    movei   #GpuBody_EmCornS, r30           ; la coda della tinta, per i due
    movei   #GpuBody_EmCorn, r31            ; rami lunghi dentro il ciclo

; un angolo per giro: due letture (angolo JBD, wscr) piu' una per la tinta
GpuBody_EmCorn:
    load    (r1), r0                        ; normale<<16 | vertice
    addq    #4, r1
    nop
    nop
    nop
    nop
    move    r0, r11
    shlq    #16, r11
    shrq    #16, r11                        ; v
    move    r0, r19
    shrq    #16, r19                        ; ni
    move    r11, r0
    sub     r12, r0                         ; v - nb_points
    jr      MI, .ecv
    nop
    moveq   #0, r11                         ; fuori campo -> vertice 0
.ecv:
    shlq    #3, r11
    add     r8, r11
    load    (r11), r14                      ; xy impacchettato di wscr
    move    r19, r0
    shlq    #31, r0                         ; parita' di ni nel segno
    nop
    nop
    nop
    store   r14, (r10)                      ; vertice +0: si copia, non si
    addq    #4, r10                         ; ricompone
    move    r14, r20
    shlq    #16, r20
    sharq   #16, r20                        ; y firmata
    move    r20, r21
    sub     r4, r21
    jr      PL, .ecmin
    nop
    move    r20, r4                         ; miny
.ecmin:
    move    r5, r21
    sub     r20, r21
    jr      PL, .ecmax
    nop
    move    r20, r5                         ; maxy
.ecmax:
    ; la tinta: piatto -> raw; gouraud -> wint[ni] con la guardia. I due rami
    ; che scavalcano la lettura passano da (r30), fuori portata per un jr.
    move    r18, r21
    cmpq    #0, r15
    jump    EQ, (r30)
    nop
    moveq   #0, r21
    move    r19, r14
    sub     r13, r14                        ; ni - nb_normals
    jump    PL, (r30)
    nop
    move    r19, r14
    shlq    #1, r14
    shrq    #2, r14
    shlq    #2, r14                         ; (ni*2) & ~3
    add     r9, r14
    load    (r14), r21
    nop
    nop
    nop
    cmpq    #0, r0                          ; la parita' calcolata sopra
    jr      MI, .eco
    nop
    shrq    #16, r21                        ; pari: word alta
    jr      T, GpuBody_EmCornS
    nop
.eco:
    shlq    #16, r21
    shrq    #16, r21                        ; dispari: word bassa
GpuBody_EmCornS:
    add     r17, r21
    shlq    #24, r21
    shrq    #24, r21                        ; (coul1 + ii) & $FF
    store   r21, (r10)                      ; vertice +4
    addq    #4, r10
    subq    #1, r2
    jump    NE, (r31)
    nop

    ; --- il limite delle righe, e il salto se non ne resta nessuna ---
    cmpq    #0, r4
    jr      PL, .ecy0
    nop
    moveq   #0, r4
.ecy0:
    movei   #BODY_YMAX, r19
    move    r19, r0
    sub     r5, r0
    jr      PL, .ecy1
    nop
    move    r19, r5                         ; maxy oltre il buffer -> 591
.ecy1:
    movei   #GpuBody_EmSkip, r30
    move    r5, r0
    sub     r4, r0
    jump    MI, (r30)                       ; tutto sopra o sotto lo schermo
    nop

    ; --- il tuffo nel riempitore: IL TOTALE VA NELLA CHIAVE MORTA. ---
    ; La chiave del pittore della voce appena consumata e' dato morto dopo
    ; il sort: HDRSLOT = LISTP-20 fa atterrare la scrittura di JobNext
    ; (HDRSLOT+12) proprio li', a LISTP-8. Il 68000 ricostruisce i poligoni
    ; disegnati camminando wlist, come faceva coi totali del flusso - e il
    ; kernel non paga NIENTE: la scrittura c'era gia'.
    movei   #GpuBody_EmLoop, r19
    movei   #GPU_SRAM_EMNEXT, r0
    store   r19, (r0)
    movei   #GPU_SRAM_EMST, r14
    load    (r14), r19                      ; LISTP, gia' sulla voce dopo
    movei   #GPU_BODY_HDRSLOT, r0
    nop
    nop
    subq    #20, r19
    store   r19, (r0)                       ; JobNext scrivera' a LISTP-8
    movei   #GPU_EM_STAGE, r1
    addq    #16, r1                         ; r1 = i vertici, come dal flusso
    move    r16, r2                         ; n
    movei   #GpuBody_Build, r0
    jump    T, (r0)
    nop

; --- VOCE ENTITA': il blocco lo consuma il ciclo del flusso di sempre. ---
; r1 = RUNP, la porta EMDONE punta alla coda del blocco e la continuazione
; delle tratte torna a JobLoop: nessun secondo ciclo da scrivere.
GpuBody_EmRunBlk:
    movei   #GpuBody_EmBlkEnd, r19
    movei   #GPU_BODY_EMDONE, r0
    store   r19, (r0)
    movei   #GpuBody_JobLoop, r19
    movei   #GPU_SRAM_EMNEXT, r0
    store   r19, (r0)
    movei   #GPU_SRAM_EMST, r14
    move    r14, r0
    addq    #8, r0
    load    (r0), r1                        ; RUNP
    move    r19, r30                        ; = GpuBody_JobLoop
    nop
    nop
    jump    T, (r30)
    nop

; la coda del blocco: il ciclo ha consumato il terminatore avanzando r1 di
; 16 come per un'intestazione vera, quindi il long successivo e' r1-12.
; Anche l'entita' lascia il totale nella propria chiave morta - e' la
; stessa identica scrittura del poligono saltato, quindi si CADE in EmSkip.
GpuBody_EmBlkEnd:
    movei   #GPU_SRAM_EMST, r14
    move    r14, r0
    addq    #8, r0
    subq    #12, r1
    store   r1, (r0)                        ; RUNP = blocco o poligono dopo

; il poligono SALTATO (e la coda del blocco qui sopra) scrive comunque il
; totale, invariato, nella chiave morta: senza, la voce terrebbe la chiave
; del pittore e il confronto del 68000 la leggerebbe come pixel dal nulla.
GpuBody_EmSkip:
    movei   #GPU_SRAM_EMST, r14
    load    (r14), r19
    movei   #GpuBody_EmLoop, r0
    nop
    subq    #8, r19
    store   r6, (r19)
    jump    T, (r0)
    nop

; ---------------------------------------------------------------------------
GpuBody_Done:
    movei   #GPU_PIXELS, r0
    store   r6, (r0)
    nop
    nop
    nop

    ; Barriera: rilettura dell'indirizzo + ritardo, per svuotare il buffer di
    ; scrittura prima di alzare la bandiera. Senza, il 68000 puo' vedere DONE
    ; prima dei pixel.
    ;
    ; IL REGISTRO DELLA RILETTURA E' USA E GETTA, E NON E' PIGNOLERIA.
    ; Fino a S33 la rilettura andava in r19 e due istruzioni dopo r19 veniva
    ; riusato per il valore da scrivere. Su un emulatore il `load` e' istantaneo
    ; e il `moveq` vince; sul ferro il `load` ha latenza, atterra DOPO, e
    ; sovrascrive r19 con quel che ha letto - cioe' lo zero che il 68000 aveva
    ; appena messo in DONE. Risultato: la GPU finisce il lavoro e comunica di
    ; non averlo finito. Il 68000 aspetta il guardiano e passa oltre.
    ;
    ; Il sintomo non assomiglia alla causa: si vede un poligono disegnato ogni
    ; guardiano, cioe' "disegna lentissimo", non "non segnala".
    movei   #GPU_DONE, r0

; --- EPILOGO CONDIVISO DEI TRE KERNEL (S46). Entra con r0 = indirizzo della
; bandiera DONE del chiamante. E' la sequenza provata dal S33 - barriera,
; bandiera, barriera, auto-arresto - in UNA copia sola: shade e front ci
; saltano da fuori, e possono farlo perche' i tre blob sono residenti
; insieme nella stessa SRAM. Prima erano tre copie identiche, e tre copie
; di una sequenza delicata sono tre posti dove correggerla.
GpuCommon_Done:
    load    (r0), r30
    nop
    nop
    nop
    nop
    nop
    nop

    moveq   #1, r19
    store   r19, (r0)

    ; RILETTURA DOPO, NON SOLO PRIMA.
    ; La barriera qui sopra spingeva fuori i PIXEL; questa spinge fuori DONE.
    ; Serve perche' subito dopo la GPU non tocca piu' il bus, e una scrittura
    ; rimasta in coda non ha piu' niente che la spinga: il 68000 aspetta un
    ; valore che e' gia' stato scritto ma non e' ancora arrivato.
    load    (r0), r30
    nop
    nop
    nop
    nop
    nop
    nop

    ; AUTO-ARRESTO, invece di girare a vuoto.
    ;
    ; Fino a qui la GPU restava viva in un ciclo infinito e la fermava il 68000
    ; scrivendo zero in G_CTRL. Due difetti in uno: la GPU continuava a
    ; consumare bus mentre l'OP disegnava, e soprattutto il 68000 doveva
    ; fermarla PRIMA di riprogrammarle il PC - se lo stop non era ancora passato
    ; la riprogrammazione si perdeva e il lancio dopo rileggeva gli argomenti
    ; vecchi. Fermandosi da sola, quando il 68000 vede DONE la GPU e' gia' ferma.
    ;
    ; E la scrittura a G_CTRL e' anche un accesso al bus, quindi trascina fuori
    ; qualsiasi cosa fosse rimasta in coda.
    movei   #G_CTRL, r0
    moveq   #0, r19
    store   r19, (r0)
    nop
    nop
    nop
    nop
    nop
    nop

; Rete di sicurezza: se l'auto-arresto non dovesse prendere, si finisce qui e
; ferma il 68000 come prima.
GpuBody_Halt:
    movei   #GpuBody_Halt, r0
    jump    (r0)
    nop

GpuBody_End:
    ; Il codice deve stare SOTTO l'ingresso dello shade: e' la .assert che
    ; mancava quando i due kernel si sono sovrapposti (S44). Se scatta, o si
    ; accorcia il codice o si rivede la mappa in tools/memory_map.py.
    .assert GpuBody_End <= GPU_SRAM_SHADE
    .68000
GpuBodyRomEnd:
