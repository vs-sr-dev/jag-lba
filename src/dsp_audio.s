; ===========================================================================
; src/dsp_audio.s - Jerry DSP: l'operatore OPL2, e la misura di quanto costa.
;
; L'ARCHITETTURA, E PERCHE' NON E' QUELLA OVVIA
; ---------------------------------------------
; L'ovvia sarebbe sintetizzare dentro l'interruzione I2S: arriva lo strobe, si
; calcola il campione, lo si scrive nel DAC. Non si fa, per due motivi.
; Il primo e' il tetto: a 20.795 campioni al secondo ci sono ~1279 cicli per
; campione, e diciotto operatori OPL2 li riempiono quasi tutti - un'ISR che
; sfora anche di poco non "rallenta", TACE, perche' perde lo strobe successivo.
; Il secondo e' che il costo diventa invisibile: dentro l'ISR non si puo'
; misurare quanto margine resta, perche' non c'e' nessuno a contare.
;
; Quindi: ISR MINIMA che pesca da un buffer circolare, e SINTESI A BLOCCHI nel
; ciclo di riposo. E' anche il posto dove S35 ha misurato le 529 istruzioni per
; campione, quindi il numero che avevamo non e' l'approssimazione di un altro
; posto: e' proprio questo.
;
; IL BUFFER, E COME UN GUASTO DIVENTA UN NUMERO
; ---------------------------------------------
; 64 campioni, indici a corsa libera (mai azzerati), maschera solo quando si
; indicizza. Vuoto = `rd == wr`; pieno = `wr - rd >= 64`. Un solo produttore e
; un solo consumatore sullo STESSO processore, e l'unico che puo' interrompere
; l'altro e' l'ISR: quindi basta pubblicare l'indice DOPO aver scritto il dato.
;
; Se la sintesi non tiene il passo, l'ISR trova il buffer vuoto. In quel caso
; ripete l'ultimo campione e **incrementa un contatore**: la fame diventa un
; numero da leggere invece di un gracchio da interpretare
; ([[feedback_hang_diventa_dato]]). Un sintetizzatore troppo lento e un
; sintetizzatore sbagliato suonano tutti e due male; solo uno dei due fa salire
; quel contatore.
;
; L'OPERATORE OPL2
; ----------------
; Il chip non moltiplica MAI. Lavora nel dominio logaritmico: la sinusoide sta
; in tabella come -log2(sin), tutte le attenuazioni si SOMMANO invece di
; moltiplicarsi, e una seconda tabella riporta in lineare. E' il motivo per cui
; ci sta in poche decine di istruzioni per operatore.
;
;   idx  = fase & 255, specchiato se bit 8      (un quarto d'onda in tabella)
;   att  = logsin[idx] + attenuazione
;   mag  = (exp[(att & 255) ^ 255] | 1024) >> (att >> 8)
;   out  = -mag se bit 9
;
; Le tabelle le genera tools/opl2_tables.py, che si AUTOVERIFICA: con
; attenuazione zero la catena deve riprodurre una sinusoide, e lo confronta
; campione per campione (scarto massimo 0,152%, cioe' la quantizzazione).
;
; IL COLLAUDO DI QUESTO PASSO
; ---------------------------
; Con attenuazione zero e forma d'onda 0, **un operatore OPL2 E' un seno**.
; Quindi il tono deve suonare IDENTICO a quello di prima, che veniva dalla
; tabella SINE della ROM di Jerry. Non e' una coincidenza comoda: e' il
; discriminatore che permette di cambiare due cose in un colpo solo.
;   - timbro diverso  -> sbaglia la matematica dell'operatore
;   - gracchio o silenzio, contatore di fame che sale -> sbaglia il buffer
; Due difetti, due sintomi distinti.
;
; L'impianto (ingressi $F1B000/$F1B010, sequenza di rientro, D_FLAGS, pila)
; resta quello di silicon_relics che ha suonato sul ferro, e non si tocca.
; ===========================================================================

; Un giro d'onda ogni 64 campioni: il tono vale Fs/64, circa 325 Hz. Stesso
; valore di prima APPOSTA - e' meta' del discriminatore qui sopra.
DSP_TONE_STEP       EQU     $04000000

; L'attenuazione dell'operatore. ZERO per questo passo: e' la condizione sotto
; cui l'operatore deve dare un seno. Diventera' l'inviluppo piu' il total level
; del timbro di SAMPLE.AD.
DSP_OP_ATT          EQU     0

DSP_BUF_LEN         EQU     64
DSP_BUF_MASK        EQU     DSP_BUF_LEN-1

; --- L'inviluppo ------------------------------------------------------------
; Un blocco per operatore: fase, inviluppo, stato, passo di decadimento, passo
; di rilascio, e tre long di riserva per arrivare a una taglia potenza di due
; (l'avanzamento diventa una addq sola invece di una moltiplicazione).
;
; L'inviluppo e' un'ATTENUAZIONE in Q8: 0 = volume massimo, DSP_ENV_SILENCE =
; muto. Sta in logaritmo come tutto il resto dell'OPL2, ed e' per questo che
; decadimento e rilascio sono somme e non moltiplicazioni.
; --- L'INTERRUTTORE DELLA PROVA DEL TIMBRO (S35) ----------------------------
; 0 = catena OPL2 (log-sin + somma + exp + specchio + segno)
; 1 = seno diretto dalla tabella nella ROM di Jerry, $F1D200
;
; Con attenuazione zero le due strade devono dare LA STESSA ONDA: la catena
; OPL2 riproduce la sinusoide allo 0,15% (verificato in tools/opl2_tables.py).
; Quindi se sul Jaguar una delle due suona dura e l'altra no, il difetto sta
; nella catena e il primo indiziato e' `sh` - l'unica istruzione di questo file
; senza verifica indipendente sul silicio. Se suonano ugualmente dure, la
; catena e' innocente e quel che si sente e' il convertitore, che di filtro di
; ricostruzione non ne ha.
;
; E' una costante di build e non un tasto APPOSTA: i registri del ciclo sono
; tutti allocati, e una scelta a caldo costerebbe istruzioni dentro il percorso
; misurato, cioe' sporcherebbe le capienze appena misurate pulite.
DSP_WAVE_SRC        EQU     0

; --- L'IMPUTATO: `sh` -------------------------------------------------------
; 0 = spostamento variabile con `sh` (una istruzione)
; 1 = stesso risultato con un ciclo di `shrq #1` (esatto, piu' lento)
;
; `sh` e' l'unica istruzione di questo file senza verifica indipendente sul
; silicio, e tre indizi la accusano: la catena OPL2 suona dura sul Jaguar e
; pulita su BigPEmu; il seno preso dalla ROM di Jerry - che non passa da `sh` -
; suona perfetto sullo stesso Jaguar; e la durezza E' PEGGIORATA con
; l'inviluppo, che e' proprio quel che allarga l'intervallo dello spostamento
; da 0..8 a 0..24.
;
; Con questa a 1 il risultato e' identico per costruzione ma non passa mai da
; `sh`. Se il suono si pulisce, l'imputato e' colpevole - e la cosa vale ben
; oltre l'audio, perche' e' un'istruzione che si userebbe ovunque.
DSP_NO_SH           EQU     0

; --- LATENZA DELLE LETTURE A 16 BIT -----------------------------------------
; 1 = due nop in piu' dopo ogni `loadw` della catena OPL2.
;
; Oggi fra una `loadw` e l'uso del suo risultato passano TRE istruzioni. Se sul
; silicio la lettura a 16 bit dalla RAM locale di Jerry ne vuole di piu' - e
; BigPEmu non lo modella - il registro contiene ancora il valore VECCHIO, e
; quello spiega da solo tutti e tre i sintomi: il seno sporco (il "saw") nella
; variante con `sh`, la fame catastrofica nella variante col ciclo di `shrq`
; (un `att` enorme fa girare il ciclo centinaia di volte), e il fatto che la
; variante col seno dalla ROM - che usa `load` a 32 bit e una lettura sola -
; suoni perfetta.
;
; Una causa, tre sintomi, e non serve accusare `sh`.
DSP_LOAD_PAD        EQU     0

DSP_OP_BLOCK        EQU     32
DSP_OP_SLOTS        EQU     32
DSP_ENV_SILENCE     EQU     $0001FF00       ; 511 passi da 0,1875 dB = 96 dB
DSP_ENV_DECAY       EQU     16
DSP_ENV_RELEASE     EQU     24

; Il ciclo di riposo conta a blocchi di TRE istruzioni. 1024 passi = 3072
; istruzioni, cioe' ~4 periodi di campione: abbastanza corto da non far mai
; svuotare un buffer da 64, abbastanza lungo da non riempire il bus di
; scritture. IL BLOB C DUPLICA QUESTO NUMERO (DSP_IDLE_BLOCK_INSTR in
; engine_main.c): se si cambia qui, si cambia li'.
DSP_IDLE_INNER      EQU     1024

; ---------------------------------------------------------------------------
; Lato 68000: accensione dell'interfaccia seriale e avvio del DSP.
; L'ordine viene dall'originale e non e' negoziabile: fermare, ordine dei byte,
; SCLK/SMODE, PULIRE le latch, poi copiare e partire. Pulire dopo aver copiato
; vorrebbe dire partire con un'interruzione gia' in coda.
; ---------------------------------------------------------------------------
    .68000

InitDspAudio:
    movem.l d0/a0/a1, -(sp)

    move.l  #0, D_CTRL                      ; DSP fermo
    move.l  #$00050005, D_END               ; ordine dei byte

    ; SCLK=19 -> 20.795 campioni/s MISURATI sul ferro (il TRM ha ragione; il
    ; commento "22050" dell'originale sbagliava del 6%). SMODE=$15 = mode16.
    move.l  #19, SCLK
    move.l  #$15, SMODE

    move.l  #$23E00, D_FLAGS                ; (31<<9)|(1<<17): latch pulite

    ; UN operatore, PRIMA di far partire il DSP. Non e' pignoleria: il DSP
    ; parte prima del blob C, quindi se non lo scrivesse nessuno leggerebbe
    ; quel che c'era in DRAM all'accensione - e uno zero li' dentro fa girare
    ; il conto alla rovescia degli operatori per quattro miliardi di giri.
    move.l  #1, DSP_OPS
    move.l  #0, DSP_ENV_MASK                ; inviluppo a passo pieno
    move.l  #4, DSP_GAIN                    ; x16: un operatore a piena scala

    ; Copia nella RAM locale di Jerry, arrotondata per ECCESSO: le istruzioni
    ; sono da 2 byte e una divisione secca per 4 perderebbe l'ultima meta'
    ; delle volte ([[feedback_gpu_blob_copy_roundup]]).
    move.l  #DspAudioRom, a0
    move.l  #D_RAM, a1
    move.w  #(DspAudioRomEnd-DspAudioRom+3)/4-1, d0
.ida_copy:
    move.l  (a0)+, (a1)+
    dbra    d0, .ida_copy

    move.l  #D_RAM, D_PC
    move.l  #DSPGO, D_CTRL

    movem.l (sp)+, d0/a0/a1
    rts

; ---------------------------------------------------------------------------
; Il codice DSP. Emesso in cartuccia a DspAudioRom, gira a D_RAM ($F1B000).
;
; I DUE BANCHI DI REGISTRI NON SI TOCCANO MAI.
; L'init gira in banco 0 e ci lascia i registri dell'ISR (r13..r28); poi alza
; REGPAGE ($4000) e da quel momento il ciclo di riposo gira in banco 1, mentre
; l'interruzione rientra sempre in banco 0. Non condividono un solo registro:
; parlano solo attraverso la RAM locale (snd_rd, snd_wr, irq_slot). Non e'
; prudenza, e' come funziona l'hardware - e per questo il ciclo di riposo puo'
; usare r0..r30 senza guardare in faccia nessuno.
; ---------------------------------------------------------------------------
    .phrase
DspAudioRom:
    .dsp
    .org    D_RAM

; --- Banco 0: l'interruzione ---
ALT_RISC_FLAGS  .equr   r13
ALT_FLAGSTEMP   .equr   r14
ALT_PCTEMP      .equr   r15
ALT_RD          .equr   r16     ; indice di lettura, a corsa libera
ALT_WR          .equr   r17     ; copia di quel che ha prodotto il ciclo
ALT_LDAC        .equr   r18
ALT_RDAC        .equr   r19
ALT_STACKRESET  .equr   r20
ALT_SAMPLE      .equr   r21     ; sopravvive fra un'interruzione e l'altra:
                                ; e' l'ultimo campione, che si ripete se manca
ALT_RDP         .equr   r22
ALT_WRP         .equr   r23
ALT_BUF         .equr   r24
ALT_TMP         .equr   r25
ALT_MASK        .equr   r26
ALT_STARVE      .equr   r27
ALT_STARVEP     .equr   r28

; --- $F1B000: INT0 / avvio ---
    movei   #dsp_audio_init, r0
    jump    T, (r0)
    nop
    nop
    nop
    nop

; --- $F1B010: INT1 = I2S. Una coppia stereo per interruzione. ---
int_1:
    load    (ALT_RISC_FLAGS), ALT_FLAGSTEMP
    load    (ALT_WRP), ALT_WR               ; quanti ne ha prodotti il ciclo

    ; L'indirizzo della casella si calcola SEMPRE, anche quando poi non serve:
    ; occupa la latenza della load qui sopra invece di costare a parte.
    move    ALT_RD, ALT_TMP
    and     ALT_MASK, ALT_TMP
    shlq    #2, ALT_TMP
    add     ALT_BUF, ALT_TMP

    cmp     ALT_WR, ALT_RD                  ; rd == wr -> buffer vuoto
    jr      EQ, .starved
    nop

    load    (ALT_TMP), ALT_SAMPLE
    addq    #1, ALT_RD
    jr      T, .emit
    store   ALT_RD, (ALT_RDP)               ; casella di ritardo: si esegue

.starved:
    ; Niente dato: si ripete ALT_SAMPLE, che e' ancora l'ultimo buono. E si
    ; conta, perche' e' l'unico modo di distinguere "la sintesi e' lenta" da
    ; "la sintesi e' sbagliata": suonano uguale, ma solo una fa salire questo.
    addq    #1, ALT_STARVE
    store   ALT_STARVE, (ALT_STARVEP)

.emit:
    ; --- Rientro. Sequenza dell'originale, da non toccare. ---
    bclr    #3, ALT_FLAGSTEMP
    bset    #10, ALT_FLAGSTEMP
    load    (r31), ALT_PCTEMP
    move    ALT_STACKRESET, r31
    addq    #2, ALT_PCTEMP
    store   ALT_SAMPLE, (ALT_LDAC)
    store   ALT_SAMPLE, (ALT_RDAC)
    jump    T, (ALT_PCTEMP)
    store   ALT_FLAGSTEMP, (ALT_RISC_FLAGS)

    .long
    .long

int_stack:
    dc.l    0, 0, 0, 0

; --- Il ponte fra i due banchi. Sono le UNICHE celle che vedono tutti e due,
;     e sono anche tutta la strumentazione: non c'e' un contatore in piu' di
;     quelli che il buffer richiede comunque per funzionare.
    .long
snd_rd:
    dc.l    0                               ; scritto dall'ISR, letto dal ciclo
snd_wr:
    dc.l    0                               ; scritto dal ciclo, letto dall'ISR
snd_starve:
    dc.l    0                               ; scritto dall'ISR, letto dal ciclo
i2s_armed:
    dc.l    0                               ; l'I2S e' stato acceso? (una volta)

; --- L'accensione, una volta sola, chiamata da INT0 ---
dsp_audio_init:
    movei   #D_FLAGS, ALT_RISC_FLAGS
    movei   #L_DAC, ALT_LDAC
    movei   #R_DAC, ALT_RDAC
    movei   #int_stack+8, ALT_STACKRESET
    movei   #snd_rd, ALT_RDP
    movei   #snd_wr, ALT_WRP
    movei   #snd_starve, ALT_STARVEP
    movei   #snd_buf, ALT_BUF
    movei   #DSP_BUF_MASK, ALT_MASK
    moveq   #0, ALT_RD
    moveq   #0, ALT_WR
    moveq   #0, ALT_SAMPLE
    moveq   #0, ALT_STARVE
    move    ALT_STACKRESET, r31

    ; REGPAGE SI', I2S ANCORA NO.
    ;
    ; L'originale accendeva tutto insieme, e li' andava bene perche' il
    ; campione lo leggeva da una tabella gia' pronta in DRAM. Qui invece dopo
    ; questa riga ci sono ancora ~400 istruzioni di preparazione (le costanti e
    ; i 32 blocchi degli operatori) mentre un periodo di campione ne dura circa
    ; 540: la prima interruzione arriva a buffer VUOTO e il contatore della
    ; fame parte da uno.
    ;
    ; Un campione di silenzio all'accensione non lo sente nessuno. Il danno e'
    ; un altro: rende ambigua la sonda. Con B che parte da uno, "e' partita
    ; male" e "sta crescendo" si distinguono solo tenendo a mente da dove era
    ; partita - ed e' esattamente il tipo di sonda che questo progetto ha gia'
    ; imparato a non accettare. L'I2S si accende dal ciclo di riposo, la prima
    ; volta che il buffer si riempie.
    movei   #$00004000, r0                  ; solo REGPAGE
    store   r0, (ALT_RISC_FLAGS)

    nop
    nop
    nop
    nop

; ---------------------------------------------------------------------------
; Banco 1: il ciclo di riposo. Riempie il buffer finche' c'e' posto, e quando
; e' pieno CONTA - cosi' la riga 9 del pannello resta "istruzioni libere per
; campione" e si legge contro le 529 della misura a vuoto: quel che manca alle
; 529 e' quel che si e' mangiato il sintetizzatore.
;
;   r1  wr          r8  passo        r15 blocchi       r22 maschera 255
;   r2  &snd_wr     r9  &OplLogSin   r16 &DSP_IRQ_CNT  r23 costante 1024
;   r3  &snd_rd     r10 &OplExp      r17 &DSP_IDLE_CNT r24 &DSP_STARVE
;   r4  snd_buf     r11 appoggio     r18 &DSP_OPS      r25 operatori rimasti
;   r5  maschera    r12 appoggio     r19 contatore     r26 quanti operatori
;   r6  lunghezza   r13 appoggio     r20 attenuazione  r27 accumulatore
;   r7  fase        r14 appoggio     r21 &snd_starve   r28 stato corrente
;                                                      r29 &op_loop
;                                                      r30 &op_state
; ---------------------------------------------------------------------------
dsp_idle_setup:
    movei   #snd_wr, r2
    movei   #snd_rd, r3
    movei   #snd_buf, r4
    movei   #DSP_BUF_MASK, r5
    movei   #DSP_TONE_STEP, r8
    .if DSP_WAVE_SRC=1
    movei   #$00F1D200, r9                  ; SINE nella ROM di Jerry
    .else
    movei   #OplLogSin, r9
    .endif
    movei   #OplExp, r10
    movei   #snd_starve, r21
    movei   #255, r22
    movei   #1024, r23
    movei   #DSP_IRQ_CNT, r16
    movei   #DSP_IDLE_CNT, r17
    movei   #DSP_STARVE, r24
    movei   #DSP_OPS, r18
    movei   #op_loop, r29                   ; il ciclo e' troppo lungo per un jr
    movei   #op_state, r30
    moveq   #0, r1
    moveq   #0, r15

    ; --- Lo stato iniziale dei 32 operatori: zitti e in attacco. Si inizializza
    ;     qui invece di emetterlo come dati perche' sono 256 long, cioe' 256
    ;     occasioni di sbagliarne uno in un blocco che nessuno rileggerebbe.
    movei   #op_state, r12
    movei   #DSP_OP_SLOTS, r13
    movei   #DSP_ENV_SILENCE, r14
    movei   #DSP_ENV_DECAY, r11
    movei   #DSP_ENV_RELEASE, r19
    moveq   #0, r20
.init_op:
    store   r20, (r12)                      ; fase
    addq    #4, r12
    store   r14, (r12)                      ; inviluppo: silenzio
    addq    #4, r12
    store   r20, (r12)                      ; stato: attacco
    addq    #4, r12
    store   r11, (r12)                      ; passo di decadimento
    addq    #4, r12
    store   r19, (r12)                      ; passo di rilascio
    addq    #16, r12                        ; il blocco e' 8 long
    subq    #1, r13
    jr      NE, .init_op
    nop

dsp_idle:
    ; --- C'e' posto? pieni = wr - rd. Il test e' un BIT, non un confronto:
    ;     i pronti stanno fra 0 e 64, quindi "pieno" e' esattamente il bit 6.
    ;     Cosi' la lunghezza non occupa un registro, e ne serviva uno.
    movei   #DSP_ENV_MASK, r0
    load    (r0), r6                        ; ogni quanto gira l'inviluppo
    load    (r3), r11                       ; rd, che l'ISR aggiorna
    load    (r18), r26                      ; quanti operatori vuole il 68000
    ; Il bersaglio si carica PRIMA del test: `movei` aggiorna i flag, e messa
    ; dopo cancellerebbe la risposta appena calcolata
    ; ([[feedback_gpu_branch_pattern]]). Occupa anche la latenza delle load.
    movei   #dsp_spin, r0
    move    r1, r12
    sub     r11, r12                        ; campioni gia' pronti
    btst    #6, r12
    jump    NE, (r0)                        ; pieni == 64 -> avanza tempo
    nop

    ; --- Guardia sullo zero. Il conto alla rovescia parte da r26: uno zero lo
    ;     farebbe girare quattro miliardi di volte, cioe' un blocco travestito
    ;     da lentezza ([[feedback_hang_diventa_dato]]). Quattro istruzioni per
    ;     togliere di mezzo una classe di guasti.
    movei   #.ops_ok, r0
    cmpq    #0, r26
    jump    NE, (r0)
    nop
    moveq   #1, r26
.ops_ok:

    ; --- I contatori si pubblicano OGNI 64 CAMPIONI, non quando il buffer e'
    ;     pieno. Sembra un dettaglio e non lo e': appena gli operatori sono
    ;     troppi il buffer non si riempie MAI, quindi pubblicare solo di la'
    ;     congelerebbe le sonde esattamente nel caso che vogliamo osservare -
    ;     compresa quella della fame. Costa tre istruzioni per campione.
    movei   #.no_pub, r0
    move    r1, r12
    and     r5, r12
    jump    NE, (r0)
    nop
    load    (r21), r12                      ; snd_starve
    nop
    nop
    add     r12, r11                        ; rd + fame = interruzioni I2S
    store   r11, (r16)
    store   r15, (r17)
    store   r12, (r24)
.no_pub:

    ; --- N OPERATORI OPL2, SOMMATI --------------------------------------------
    ; Ognuno ha la SUA fase in RAM locale e se la rilegge: e' il costo vero di
    ; un banco di operatori, load e store compresi. Contarli con una fase sola
    ; in un registro avrebbe dato un numero piu' bello e sbagliato.
    moveq   #0, r27                         ; il mix
    move    r26, r25
    move    r30, r28
op_loop:
    move    r28, r12                        ; puntatore di lavoro nel blocco
    load    (r12), r7                       ; la fase di QUESTO operatore
    nop
    nop
    add     r8, r7                          ; fase += passo
    store   r7, (r12)
    addq    #4, r12                         ; -> inviluppo

    ; L'inviluppo si LEGGE sempre (serve all'attenuazione di questo campione)
    ; ma si AGGIORNA a cadenza: la maschera in r6 decide ogni quanti campioni.
    ; E' la leva piu' importante di tutto il progetto audio, perche' la
    ; macchina a stati con i suoi carichi e le sue riscritture costa quanto
    ; l'operatore stesso, mentre l'orecchio non distingue un inviluppo
    ; aggiornato 20.000 volte al secondo da uno aggiornato 2.500.
    load    (r12), r19                      ; inviluppo (Q8: 0 = massimo)
    ; Anche questo scavalco e' troppo lungo per un salto corto, e il bersaglio
    ; non puo' stare in r0 perche' li' ci va l'uscita interna della macchina a
    ; stati. r14 va bene: serve solo fino al salto, e dentro l'inviluppo torna
    ; a fare l'appoggio. La movei occupa la latenza della load qui sopra.
    movei   #.env_skip, r14
    move    r1, r13
    and     r6, r13
    jump    NE, (r14)
    nop

    addq    #4, r12                         ; -> stato
    load    (r12), r20
    addq    #4, r12                         ; -> passo di decadimento
    load    (r12), r11
    addq    #4, r12                         ; -> passo di rilascio
    load    (r12), r13
    nop

    ; L'uscita comune si precarica UNA volta: il blocco e' troppo lungo perche'
    ; un salto corto ci arrivi (jr copre -16..+15 long) e ripetere una movei in
    ; ogni ramo costerebbe tre parole per ramo. `movei` sporca i flag, quindi
    ; sta prima del primo confronto e non fra un confronto e il suo salto
    ; ([[feedback_gpu_branch_pattern]]).
    movei   #.env_store, r0

    ; ATTACCO: si avvicina al massimo per frazioni, come l'OPL2 vero, che non
    ; sale in linea retta. Tre istruzioni per una curva esponenziale.
    cmpq    #0, r20
    jr      NE, .env_dec
    nop
    move    r19, r14
    shrq    #3, r14
    addq    #1, r14
    sub     r14, r19
    jump    PL, (r0)
    nop
    moveq   #0, r19                         ; arrivato in cima
    moveq   #1, r20
    jump    T, (r0)
    nop

    ; DECADIMENTO e RILASCIO sono LINEARI, e non e' un'approssimazione: qui si
    ; lavora in logaritmo, e una retta in logaritmo E' un decadimento
    ; esponenziale in ampiezza. E' anche il motivo per cui l'OPL2 costa poco.
.env_dec:
    cmpq    #1, r20
    jr      NE, .env_rel
    nop
    add     r11, r19
    btst    #15, r19                        ; soglia di tenuta
    jump    EQ, (r0)
    nop
    moveq   #2, r20
    jump    T, (r0)
    nop
.env_rel:
    add     r13, r19
    btst    #17, r19                        ; silenzio raggiunto
    jump    EQ, (r0)
    nop
    ; Nota finita: si riparte. Per la PROVA e' un pizzicato ciclico, cosi' si
    ; sente che l'inviluppo gira e si esercitano tutti i rami invece del solo
    ; ramo di tenuta, che costerebbe quasi niente e mentirebbe sulla misura.
    movei   #DSP_ENV_SILENCE, r14
    move    r14, r19
    moveq   #0, r20

.env_store:
    ; ATTENZIONE AL CONTO. Le cinque load hanno lasciato r12 sul campo del
    ; RILASCIO, cioe' a +16 dalla base del blocco - non a +12. La disposizione
    ; e': +0 fase, +4 inviluppo, +8 stato, +12 decadimento, +16 rilascio.
    ; Sbagliare di un campo qui non da' un suono storto: da' SILENZIO, perche'
    ; l'inviluppo vero non viene mai riscritto e resta sul valore iniziale, che
    ; e' muto. E' costato una build.
    subq    #8, r12                         ; +16 -> +8, lo stato
    store   r20, (r12)
    subq    #4, r12                         ; +8 -> +4, l'inviluppo
    store   r19, (r12)
.env_skip:

    .if DSP_WAVE_SRC=1
    ; --- LA VIA DI CONTROLLO: seno diretto dalla ROM di Jerry ---------------
    ; Sette bit di fase, 128 voci a long, gia' con segno e a 16 bit pieni. Lo
    ; si riporta alla scala dell'OPL2 (~2048) perche' il confronto a orecchio
    ; abbia lo stesso volume: se cambiasse anche il livello non si saprebbe
    ; piu' quale delle due cose si sta ascoltando. r9 qui punta a $F1D200
    ; invece che a OplLogSin, che in questa variante non serve.
    move    r7, r11
    shrq    #25, r11
    shlq    #2, r11
    add     r9, r11
    load    (r11), r14
    nop
    nop
    sharq   #4, r14
    .else

    move    r7, r11
    shrq    #22, r11                        ; i 10 bit alti = la fase d'onda

    move    r11, r12
    and     r22, r12                        ; idx = fase & 255
    btst    #8, r11
    jr      EQ, .no_mirror
    nop
    xor     r22, r12                        ; secondo/quarto quarto: specchia
.no_mirror:
    shlq    #1, r12                         ; tabella a word
    add     r9, r12
    loadw   (r12), r13                      ; logsin[idx]
    .if DSP_LOAD_PAD=1
    nop
    nop
    .endif
    move    r19, r14
    shrq    #5, r14                         ; inviluppo Q8 -> unita' di att.
    nop
    add     r14, r13                        ; att = logsin + inviluppo

    ; mag = (exp[(att & 255) ^ 255] | 1024) >> (att >> 8)
    move    r13, r14
    and     r22, r14
    xor     r22, r14
    shlq    #1, r14
    add     r10, r14
    loadw   (r14), r14
    .if DSP_LOAD_PAD=1
    nop
    nop
    .endif
    move    r13, r12
    shrq    #8, r12                         ; di quanto scalare
    nop
    or      r23, r14                        ; il bit implicito
    ; ATTENZIONE: `sh` con conteggio POSITIVO scala a destra. E' l'unica
    ; assunzione non verificata di questo file; se il timbro esce stridulo o
    ; muto e' la prima cosa da rovesciare (neg r12 prima dello sh).
    .if DSP_NO_SH=1
    ; Lo stesso spostamento, un bit per volta. `shrq` prende l'ampiezza come
    ; immediato e non ha nulla da interpretare a runtime: se questa versione
    ; suona pulita e quella con `sh` no, la differenza e' l'istruzione.
    movei   #.sh_done, r13
    cmpq    #0, r12
    jump    EQ, (r13)
    nop
.sh_loop:
    shrq    #1, r14
    subq    #1, r12
    jr      NE, .sh_loop
    nop
.sh_done:
    .else
    sh      r12, r14
    .endif

    btst    #9, r11
    jr      EQ, .no_neg
    nop
    neg     r14                             ; seconda meta' dell'onda
.no_neg:
    .endif

    add     r14, r27                        ; nel mix

    addq    #DSP_OP_BLOCK, r28              ; blocco del prossimo operatore
    subq    #1, r25
    jump    NE, (r29)                       ; -> op_loop
    nop

    ; --- GUADAGNO D'USCITA, e non e' una comodita': e' una MISURA ------------
    ; Il picco di un operatore e' 2042 su 32767, cioe' il 6% della scala del
    ; convertitore: con la vecchia scalatura fissa (x2) se ne usava il 12% e si
    ; buttavano via TRE BIT. Su BigPEmu non si sente, perche' li' il percorso e'
    ; in virgola mobile e c'e' un ricampionamento a 48 kHz che liscia i gradini;
    ; sul Jaguar quei gradini vanno dritti al DAC, che di filtro di
    ; ricostruzione non ne ha praticamente. Quantizzazione grossolana =
    ; armoniche a banda larga = il "saw fastidioso".
    ;
    ; Regolabile a caldo apposta: se alzando il guadagno la durezza sparisce
    ; era la risoluzione; se resta identica, e' la FORMA D'ONDA, e allora il
    ; sospettato numero uno diventa `sh` - l'unica istruzione di questo file di
    ; cui non abbiamo verifica indipendente sul silicio.
    ;
    ; Il conteggio si NEGA perche' `sh` positivo scala a destra (verificato in
    ; S35 dal fatto che il seno usciva giusto). Se il verso fosse rovesciato il
    ; suono diventerebbe subito quasi muto invece che piu' forte: anche questo
    ; e' un esperimento, e ha una risposta che si sente.
    movei   #DSP_GAIN, r0
    load    (r0), r13
    nop
    nop
    neg     r13
    sh      r13, r27
    ; SATURA invece di ribaltare: con N operatori in fase la somma cresce con
    ; N, e tosare e' la differenza fra un suono forte e un suono osceno.
    sat16s  r27

    ; --- Nel buffer, e SOLO DOPO pubblica l'indice ---
    move    r1, r12
    and     r5, r12
    shlq    #2, r12
    add     r4, r12
    store   r27, (r12)
    addq    #1, r1
    store   r1, (r2)                        ; da qui l'ISR puo' prenderlo

    movei   #dsp_idle, r0
    jump    T, (r0)
    nop

; --- Tempo che avanza: si conta, e ogni tanto si pubblica in DRAM ---
dsp_spin:
    ; --- L'ACCENSIONE DELL'I2S, UNA VOLTA SOLA -------------------------------
    ; Si arriva qui la prima volta esattamente quando il buffer si e' riempito,
    ; che e' il momento giusto: da adesso c'e' da mangiare per 64 campioni,
    ; quindi la prima interruzione non puo' trovare vuoto. Il contatore della
    ; fame parte da ZERO e resta una sonda che si legge senza ricordarsi da dove
    ; era partita.
    ;
    ; Scrivere D_FLAGS da qui e' sicuro proprio perche' l'I2S e' ancora spento:
    ; non c'e' nessuna ISR che possa stare manipolando le stesse latch. Dopo
    ; questa riga D_FLAGS appartiene all'interruzione e il ciclo di riposo non
    ; lo tocca mai piu' - da cui il colpo secco della bandiera.
    movei   #i2s_armed, r13
    load    (r13), r14
    nop
    nop
    cmpq    #0, r14
    jr      NE, .armed
    nop
    moveq   #1, r14
    store   r14, (r13)
    movei   #D_FLAGS, r13
    movei   #$00004020, r14                 ; REGPAGE + I2S
    store   r14, (r13)
.armed:

    movei   #DSP_IDLE_INNER, r19
.spin_inner:
    subq    #1, r19
    jr      NE, .spin_inner
    nop                                     ; casella di ritardo

    ; LE INTERRUZIONI NON SI CONTANO: si ricavano. `snd_rd` e' quanti campioni
    ; l'ISR ha consumato e `snd_starve` quante volte non ne ha trovati, quindi
    ; la somma e' quante volte e' stata chiamata. Un contatore apposta sarebbe
    ; stato due istruzioni in piu' DENTRO l'interruzione, cioe' nel punto piu'
    ; caro della macchina, per un numero che era gia' li'.
    addq    #1, r15
    load    (r3), r11                       ; snd_rd: campioni consumati
    load    (r21), r12                      ; snd_starve: volte a vuoto
    nop
    nop
    add     r12, r11                        ; = interruzioni I2S totali
    store   r11, (r16)
    store   r15, (r17)
    store   r12, (r24)

    movei   #dsp_idle, r0
    jump    T, (r0)
    nop

; --- Le tabelle e il buffer, tutto in RAM locale di Jerry ---
    .long
    .include "opl2_tables.inc"

; Lo stato degli operatori: un blocco da otto long a testa (vedi DSP_OP_BLOCK).
; Trentadue caselle, che e' piu' del banco OPL2 intero (nove canali per due
; operatori fanno diciotto) apposta, per poter spingere la spazzata OLTRE il
; fabbisogno e vedere dove crolla davvero invece di fermarsi a dove speravo che
; arrivasse. Il contenuto lo scrive dsp_idle_setup: qui basta lo spazio.
    .long
op_state:
    .rept   DSP_OP_SLOTS
    dc.l    0,0,0,0,0,0,0,0
    .endr

    .long
snd_buf:
    dc.l    0,0,0,0,0,0,0,0
    dc.l    0,0,0,0,0,0,0,0
    dc.l    0,0,0,0,0,0,0,0
    dc.l    0,0,0,0,0,0,0,0
    dc.l    0,0,0,0,0,0,0,0
    dc.l    0,0,0,0,0,0,0,0
    dc.l    0,0,0,0,0,0,0,0
    dc.l    0,0,0,0,0,0,0,0

    .phrase
    .68000
DspAudioRomEnd:
