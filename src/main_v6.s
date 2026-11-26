;===========================================================================
; JagLBA V6 - lato RMAC: boot, video 480i, OP list, interrupt
;===========================================================================
; Costruito in M0 (scheletro di boot) e da M1 stabile: la logica sta nel blob C
; e questo file e' il guscio hardware. Chiude per costruzione i due difetti
; strutturali che hanno affossato la V4:
;
;   1. La mappa di memoria ha UN solo padrone (tools/memory_map.py, che genera
;      include/dram_map.inc e include/cart_map.inc). Qui dentro non c'e' un
;      solo indirizzo di DRAM o di cartuccia scritto a mano.
;   2. Il boot DICHIARA di essere arrivato in fondo, e ogni vettore di
;      eccezione porta a un segnale. In V4 solo $100 e i sette autovettori
;      erano inizializzati: un Address Error si e' presentato come schermo
;      nero muto per tre mesi.
;
; Flusso: vettori -> timing video -> video acceso su OP list minima ->
;         CLUT -> pulizia fondo -> OP list vera -> engine_init() dal cart ->
;         interrupt video -> MainLoop che chiama engine_frame() per VBL.
;
; Segnale di vita (criterio di verifica di M0): il blob C muove un marcatore
; 16x16 di uno slot per VBL lungo un righello di 32 tacche. E' un MOVIMENTO,
; non un colore: se si ferma si vede subito, e non dipende dall'interpretazione
; di una tinta (lezione CRY16 di S24). Da M1 sotto il marcatore ci sono anche
; sedici righe di valori esadecimali disegnate dal blob C: le sonde HQR, il
; tempo e l'input (engine_c/engine_main.c ne ha la mappa).
;
; Segnale di guasto: qualunque eccezione spegne la OP list e fa LAMPEGGIARE lo
; schermo intero. Il numero di lampi e' il numero del vettore (bus error = 2,
; address error = 3, illegal = 4, ... line-F = 11), 20 lampi per tutto il resto.
;===========================================================================

    .68000
    .include "jaguar.inc"
    .include "dram_map.inc"

; S52: cornice dell'ultima eccezione del 68000, scritta da ExcCommon (36 byte)
EXC_SAVE        EQU SPARE_LO
    .include "cart_map.inc"

; Prova a mano del percorso di guasto (criterio M0). Mettere a:
;   0 = boot normale
;   1 = istruzione illegale a fine boot     -> attesi 4 lampi
;   2 = fetch a indirizzo dispari a fine    -> attesi 3 lampi (Address Error,
;       boot                                   la stessa eccezione della V4)
FORCE_EXCEPTION EQU 0

; Scrittura della tabella video COMPLETA (18 registri) invece dei soli 9 che
; V4 e V6-M0 scrivevano. Mettere a 0 per tornare al comportamento precedente.
; Vedi il blocco "Perche' i 18 registri" sotto InitVideoTiming.
FULL_VIDEO_TABLE EQU 1

; Quale delle due parita' di VI corrisponde al campo che parte dalla riga 0 del
; framebuffer. Non e' ricavabile dal TRM: e' un bit, e si stabilisce provando.
; Se l'immagine esce con le righe scambiate, invertire questo.
FIELD_PHASE     EQU 0

; Strumento di misura S27: invece di azzerare il framebuffer, riempie ogni riga
; con un valore che ne codifica indice e parita' (vedi FillFrameBufferRows).
; Serve a leggere dallo schermo QUALI righe di framebuffer vengono disegnate,
; invece di dedurlo da una scala misurata a occhio. Mettere a 0 per il fondo
; nero normale.
;   1 = rampa per riga (parita' + conto assoluto, calibrazione)
;   2 = marcatori a righe note (misura FINE del disallineamento fra campi)
;   3 = due blocchi affiancati con bordi di parita' opposta: allineamento fra
;       campi leggibile A OCCHIO, senza twitter (per l'hardware vero)
DEBUG_ROWFILL   EQU 0

;===========================================================================
; UN SOLO INTERRUTTORE: su che cosa gira questa build
;===========================================================================
;   0 = BigPEmu        1 = Jaguar reale
;
; Le due piattaforme vogliono configurazioni DIVERSE e NON intercambiabili, e una
; combinazione mista riproduce in silenzio il difetto che e' costato da S23 a S26
; (un campo su due non disegnato). Per questo i sei valori non si toccano a mano:
; li deriva questo switch.
;
; SU HARDWARE VERO (misurato in S27, RetroTink: 1716x525i, 15.74 kHz, 59.95 Hz,
; "CP 480i"):
;   - il bit 11 di VC alterna per campo, quindi dice quale campo arriva;
;   - alternare la parita' di VI da' davvero UN INTERRUPT PER CAMPO, percio' il
;     ripristino si fa nell'interrupt e non serve sorvegliare niente;
;   - il campo PARI era in ritardo di una riga di campo: da cui il bias di 2 righe.
;
; SU BIGPEMU niente di tutto questo vale:
;   - il bit 11 di VC non alterna (misurato basso su 344 interrupt su 344);
;   - non c'e' un interrupt per campo: 30/s contro 60 campi/s. Alternare la
;     parita' di VI fa scattare DUE interrupt nello STESSO campo, e il secondo
;     riscrive DATA annullando il primo -> si vede un campo solo;
;   - quindi il secondo campo si arma sorvegliando HEIGHT, che l'OP azzera a fine
;     passata, con soglia tarata sul write-back in ritardo dell'emulatore.
; Il changelog di BigPEmu 1.02 lo dice da se': "Added interlaced display support.
; This is definitely not working the way it works on hardware."
;===========================================================================
TARGET_HW       EQU 1

    .if TARGET_HW=1
FIELD_SRC        EQU 1       ; parita' del campo dal bit 11 di VC (Zerosquare)
VI_ALTERNATE     EQU 1       ; riarma VI per il prossimo campo
SECOND_FIELD_POLL EQU 0      ; non serve: c'e' un interrupt per campo
POLL_THRESH      EQU 3       ; irrilevante qui, il ciclo non viene compilato
BG_RELEASE       EQU 0       ; TRM: bus tenuto per oggetti a colore alto
FIELD_A_ROW_BIAS EQU 2       ; corregge l'off-by-one del campo pari
    .endif

    .if TARGET_HW=0
FIELD_SRC        EQU 0       ; contatore software: il bit 11 di VC non alterna
VI_ALTERNATE     EQU 0       ; VI fisso: alternarlo darebbe interrupt appaiati
SECOND_FIELD_POLL EQU 1      ; il secondo campo si arma sorvegliando HEIGHT
POLL_THRESH      EQU 3       ; HEIGHT < 12: tarato 1:1, annulla il disallineamento
BG_RELEASE       EQU 1       ; senza, restano 3 righe di scarto fra i campi
FIELD_A_ROW_BIAS EQU 0       ; l'off-by-one del campo pari qui non c'e'
    .endif

;---------------------------------------------------------------------------
; Cadenza degli interrupt video, che il blob C usa per derivare TimerRef a
; 50 Hz (M1, engine_c/hal_time.c). NON e' una costante del Jaguar: dipende da
; NTSC/PAL, che si sa solo a runtime, E da quanti interrupt arrivano per quadro,
; che dipende dalla configurazione qui sopra.
;
;   TARGET_HW=1  VI alternato  -> un interrupt per CAMPO   -> 60 / 50
;   TARGET_HW=0  VI fisso      -> un interrupt per QUADRO  -> 30 / 25
;                (i 30,0 al secondo misurati in S27, contro 60 campi/s)
;
; Il valore finisce in TICK_HZ, che InitVideoTiming scrive nel ramo giusto. Se
; il C lo trovasse a zero scorrerebbe a 60 invece di dividere per zero, ma
; sarebbe una build incoerente: qui i due rami lo scrivono entrambi.
;---------------------------------------------------------------------------
    .if TARGET_HW=1
TICK_HZ_NTSC     EQU 60
TICK_HZ_PAL      EQU 50
    .endif
    .if TARGET_HW=0
TICK_HZ_NTSC     EQU 30
TICK_HZ_PAL      EQU 25
    .endif

    .org    ROM_CODE

;---------------------------------------------------------------------------
; Le variabili dell'HAL (VDB_VAL, VBL_TICK, TICK_HZ, DBG_*, ...) NON sono
; dichiarate qui: le dichiara tools/memory_map.py dentro include/dram_map.inc,
; e lo stesso master le riemette in engine_c/mem_map.h come JAG_HAL_*.
;
; In M0 erano scritte a mano in questo file E in engine_main.c. Ha funzionato
; finche' i campi non si toccavano, ma e' la stessa forma di guasto che in S24
; ha prodotto VBL_TICK sopra GPU_ARG_OUT_COUNT: due sorgenti che dichiarano gli
; stessi indirizzi. Un padrone solo, come per il resto della mappa (3.5).
;---------------------------------------------------------------------------
; Campi degli oggetti dell'Object Processor.
; Il LINK e' l'indirizzo diviso 8, spezzato fra i due long della phrase:
;   bit 8-18 del LINK -> bit 0-10 del long ALTO
;   bit 0-7  del LINK -> bit 24-31 del long BASSO
; Da cui le due meta' qui sotto. Verificate contro i valori noti-buoni della
; V4 ($FC008003 per il branch su VDE, $0280B000 per la phrase 1 del fondo).
;---------------------------------------------------------------------------
LINK_HI_STOP    EQU     (OP_STOP>>11)&$7FF
LINK_LO_STOP    EQU     (OP_STOP>>3)&$FF
LINK_HI_TAIL    EQU     (OP_TAIL_STOP>>11)&$7FF
LINK_LO_TAIL    EQU     (OP_TAIL_STOP>>3)&$FF
; Il LINK verso OP_BG_F1 non c'e' piu': da S26 l'oggetto di fondo e' UNO SOLO e
; il campo lo sceglie il software spostando DATA. Lo slot resta riservato nella
; mappa, ma se ricomparisse un secondo oggetto tornerebbe il difetto di S25 -
; YPOS e' una soglia, non un confronto di parita' (vedi InitOpList).

OBJ_TYPE_BMP    EQU     0
OBJ_TYPE_BRANCH EQU     3
OBJ_TYPE_STOP   EQU     4

; Condizione dei branch object. Il TRM la esprime dal punto di vista di YPOS,
; non di VC (e jaguar.inc fa lo stesso: O_BRGT = 1<<14, O_BRLT = 2<<14):
;   1 = branch se YPOS > VC   (siamo sopra la finestra visibile)
;   2 = branch se YPOS < VC   (siamo sotto la finestra visibile)
CC_YPOS_GT      EQU     1
CC_YPOS_LT      EQU     2

; Fondo: 8bpp, PITCH=1 (righe contigue), IWIDTH=80 phrase (640 byte per riga),
; INDEX=1, nessuna trasparenza, RELEASE=0 (l'OP tiene il bus: evita la contesa
; col Blitter che in S22 produceva spazzatura sulle scanline).
BG_DEPTH_8BPP   EQU     3
BG_IWIDTH       EQU     BG_W/8              ; 80 phrase
BG_DWIDTH       EQU     (BG_W*2)/8          ; 160 phrase: ogni campo salta una riga (480i)
; HEIGHT = righe disegnate IN UN CAMPO, cioe' meta' delle 480 totali.
;
; Il TRM (phrase 0, campo HEIGHT) dice che in interlace l'altezza cala di DUE
; per linea, il che farebbe tornare i conti anche con 480. Il TRM QUI E'
; SBAGLIATO: nella pratica l'OP scala HEIGHT di uno per linea sempre, che il
; display sia interlacciato o no (Zerosquare, forum Jaguar).
;
; Con 480 l'oggetto resta quindi attivo per 480 righe mentre il campo ne
; disegna 240: per tutta la seconda meta' DATA corre oltre il framebuffer e
; l'OP disegna la memoria che c'e' dopo - il logo di boot Atari. E' il difetto
; che dal S25 sembrava "un campo disallineato per tutta la sua altezza".
BG_HEIGHT_RAW   EQU     240

; ---------------------------------------------------------------------------
; Rinfresco dell'oggetto di fondo, e perche' VI si inverte a ogni interrupt
; ---------------------------------------------------------------------------
; L'OP consuma l'oggetto mentre disegna: avanza DATA di DWIDTH per linea e
; scala HEIGHT fino a zero, riscrivendoli in memoria. In interlace lo consuma
; UNA VOLTA PER CAMPO, quindi va ripristinato due volte per frame.
;
; Il problema e' agganciare entrambi i campi con un solo registro VI. In
; interlace il contatore verticale ha valori PARI in un campo e DISPARI
; nell'altro, quindi un dato valore di VI puo' scattare in un campo solo:
; misurati 30,0 interrupt al secondo, uno per frame. Il campo non ripristinato
; ripartiva 480 righe piu' avanti, usciva dalle 592 righe del buffer e mostrava
; quel che c'e' oltre - il logo di boot Atari. Sembrava ghosting dell'intro,
; era memoria fuori buffer.
;
; Soluzione: ViHandler INVERTE la parita' di VI a ogni passaggio. Cosi' lo
; scatto successivo cade nell'altro campo (misurati 63,1 interrupt al secondo)
; e la parita' appena scritta dice quale campo si sta per disegnare, senza
; interrogare VC - il cui bit 11, che la V4 usava per questo, non si alza mai.
;
; VI va anche piazzato DOPO VDE: con VDB-4 il gestore entrava a VC = 92, cioe'
; 33 righe dentro l'area visibile, a ripristinare un oggetto gia' in uso. Le
; tabelle di timing di Matthias (AtariAge 2004, "The Jaguar Engine") annotano
; in tutte e tre le configurazioni "Vertical Interrupt, nur ungerade Werte!".
;
; RESIDUO APERTO. Restano ~32 righe sporche in UN solo campo (righe di
; framebuffer 113..175 circa), stabili fra un frame e l'altro. La risoluzione
; piena e' confermata (scala verticale 1,00 = 480 linee) e la cadenza pure
; (63,1 interrupt/s), quindi manca il sincronismo fra la parita' che scriviamo
; in VI e il campo che l'OP disegna davvero.
;
; Due vicoli ciechi gia' battuti, da non ripetere:
;   - VC non serve a riconoscere il campo: misurato, legge sempre 508 costante.
;     Ne' il bit 11 (meccanismo V4 S22B) ne' il bit 0 dicono qualcosa.
;   - Abbassare DWIDTH a 80 fa sparire l'artefatto ma DIMEZZA la verticale a
;     240 linee (misurato dalla scala 2,00 dell'immagine): e' un aggiramento
;     travestito da correzione. Verificare sempre la scala confrontando la
;     distanza a schermo fra due elementi a righe note (righello a riga 8,
;     tacche a riga 48 = 40 righe di distanza).
;
; Nota: le righe sporche sotto la 175 sembrano pulite solo perche' la memoria
; letta li' e' a zero. Il campo sbagliato e' disallineato per TUTTA la sua
; altezza, e quando M2 riempira' griglia e sprite quella zona diventera'
; sfarfallio. Non e' un difetto che si possa lasciare li'.
BG_INDEX        EQU     1

;---------------------------------------------------------------------------
; Correzione dell'off-by-one del campo PARI su hardware vero (S27).
;
; MISURATO sul Jaguar reale con lo strumento a blocchi (DEBUG_ROWFILL = 3):
; il campo che disegna le righe pari mostra la riga L-2 alla riga di output L,
; cioe' e' in ritardo di UNA riga di campo. Le tre osservazioni che lo provano,
; e che un solo modello spiega tutte:
;   - blocco righe 200..299 -> completamente PULITO;
;   - blocco righe 201..300 -> riga NERA in seconda posizione (output 202 pesca
;     la riga 200, fuori dal blocco);
;   - e in fondo una riga nera seguita da UNA riga bianca piu' in basso
;     dell'ultima riga del blocco pulito (output 301 nera, 302 = riga 300).
;
; Non e' un errore di scala e non e' un effetto di bordo dello scaler: e' un
; off-by-one su un campo solo. Su BigPEmu NON c'e' (i due blocchi misurano run
; contigui di 200 px), quindi la correzione va tenuta a 0 per l'emulatore.
;
; La correzione e' aritmetica, non un'ipotesi: se la riga 0 esce alla riga di
; output 2, il campo pari deve partire dalla riga 2. Deve restare PARI, altrimenti
; si scambia la parita' del campo invece di spostarlo.
; FIELD_A_ROW_BIAS lo fissa TARGET_HW, in testa al file. Verificato nel listing
; che RMAC valuti bene questa espressione (con bias 2: FIELD_A_DATA = $000A0500 e
; l'istruzione esce come move.l #$0A050001), perche' le immediate con
; moltiplicazione a volte le sbaglia - vedi [[feedback_rmac_immediate_expr]].
FIELD_A_DATA    EQU     BG_FB+(FIELD_A_ROW_BIAS*BG_W)

; RELEASE, bit 48 della phrase 1 = bit 16 di PH1_HI.
;
; TRM: "This bit forces the Object Processor to release the bus between data
; fetches. [...] For high colour resolution objects the bus should be held by the
; Object Processor because there is very little time between data fetches and
; other bus masters would probably cause DRAM page faults thereby slowing the
; system."
;
; Percio' fin qui e' stato 0 (ed era anche la cura di S22 contro la contesa col
; Blitter, vedi [[feedback_blitter_bus_contention]]). Ma con il bus tenuto
; dall'OP il ciclo che sorveglia HEIGHT in ViHandler avanza a passo d'uomo, ed e'
; il sospetto principale per le 8 righe di campo di ritardo con cui viene armato
; il campo B. Messo a 1 per misurare quel ritardo; se non serve, tornare a 0.
; MISURATO, e non e' indifferente come avevo scritto prima:
;   RELEASE = 1, POLL_THRESH = 3 -> campi allineati, offset 0
;   RELEASE = 0, POLL_THRESH = 3 -> restano 3 righe di output di scarto
; Il contributo di RELEASE e' ~1,5 righe di campo: invisibile quando l'offset era
; grossolano (34 px in entrambi i casi), decisivo adesso che si lavora a zero.
; La soglia non lo puo' recuperare, perche' ha grana di 4 righe (legge HEIGHT>>2).
;
; COMPROMESSO DICHIARATO: RELEASE = 1 va contro il consiglio del TRM per oggetti
; a colore alto e contro la cura di S22 per la contesa col Blitter
; ([[feedback_blitter_bus_contention]]). Adesso non c'e' Blitter in MainLoop, ma
; quando torna (M2/M3) questa scelta va riverificata: se ricompare spazzatura
; sulle scanline, tornare a 0 e accettare le 3 righe, oppure portare il ciclo di
; sorveglianza a grana di UNA riga leggendo la word invece del byte.
;
; Il valore lo fissa TARGET_HW, in testa al file: 1 su BigPEmu, 0 su hardware
; (dove il ciclo di sorveglianza non esiste, quindi il bus non va liberato).

OP_BG_PH1_HI    EQU     (BG_IWIDTH>>4)|(BG_INDEX<<6)|(BG_RELEASE<<16)
OP_BG_PH1_LO    EQU     (BG_DEPTH_8BPP<<12)|(1<<15)|(BG_DWIDTH<<18)|((BG_IWIDTH&$F)<<28)

;---------------------------------------------------------------------------
; S47: L'ATTORE COME SPRITE DELL'OP - lo spike del TRANS.
;
; Il BMobj dello sprite e' la fotocopia del fondo in scala 128x80: stessa
; profondita' (8bpp CLUT), stesso PITCH, DWIDTH doppio per l'interlace,
; HEIGHT per CAMPO, riarmato da ViHandler a ogni interrupt. Le due differenze:
;   1. il bit TRANS (phrase 1, bit 47 = bit 15 del long alto). I netlist
;      (verilog/tom/ob.v: transen <- d_47; obdata.v: zero8 = NOR degli 8 bit
;      del pixel) dicono che in mode8 il pixel a ZERO non viene scritto nel
;      line buffer. Il "TRANS non funziona" di S23 non era il bit: erano i
;      ~1200 pixel di indice 0 VERI dentro il corpo di Twinsen.
;   2. il LINK: il fondo punta qui invece che al TAIL, e lo sprite chiude al
;      TAIL. NOTA: LINK_HI di OP_ACTORS e OP_TAIL_STOP coincidono (=1),
;      quindi i long ALTI del fondo non cambiano: cambia solo LINK_LO in
;      OP_BG_PH0LO.
;
; ACTOR_SPRITE_TEST=1 riempie il buffer con il MOTIVO DI PROVA (cornice
; opaca + colonne alterne opache/zero): se il TRANS funziona, nelle colonne
; a zero si vedono i mattoni sotto. A 0 la lista OP torna quella di prima,
; byte per byte.
;---------------------------------------------------------------------------
; S49: ACCESO. Il "non compare" di S47 non era ne' l'OP ne' la decisione (che
; il banco su PC prova accesa in tutte e tre le stanze del riferimento): erano
; le quattro scritture HAL del C, avvolte in HW_L su nomi che sono GIA'
; l'lvalue, cioe' dereferenziate due volte. Vedi body_draw.c, draw_actor.
ACTOR_SPRITE    EQU     1
SPR_IWIDTH      EQU     SPR_VIS_W/8         ; 16 phrase: quanto LEGGE per riga
SPR_DWIDTH      EQU     (SPR_PITCH*2)/8     ; quanto AVANZA: due righe (480i)
LINK_HI_ACT0    EQU     (OP_ACTORS>>11)&$7FF
LINK_LO_ACT0    EQU     (OP_ACTORS>>3)&$FF
; Il TRANS (bit 47 = bit 15 del long ALTO): i pixel a indice 0 non entrano nel
; line buffer. Provato sul ferro in S47, e i corpi non ne producono nemmeno uno
; (ZERO_PIX in tools/body_ref.py sorveglia).
OP_SPR_PH1_HI   EQU     (SPR_IWIDTH>>4)|(BG_INDEX<<6)|(1<<15)|(BG_RELEASE<<16)

;---------------------------------------------------------------------------
; Timing orizzontale: i 6 registri che il BIOS ci lasciava impostare da solo.
;
; Valori dalle formule di VIDINIT.S di Atari (demo Checkered, riprodotta in
; D:\Homebrew\Jaguar\demo\Checkered\TG\VIDINIT.S), che li ricava dai tempi
; standard in unita' da 100 ps divisi per il clock video vclk = 376 (26,6 MHz):
;
;   sync 4,605 us = 46050    front porch 1,745 us = 17450
;   eq   2,350 us = 23500    blanking totale 10,95 us = 109500
;   mezza riga NTSC 31,7778 us = 317778   /  PAL 32,0 us = 320000
;
; HBB e HS hanno il bit $400 alto (bit 10 = seconda meta' della riga).
; I risultati sono ripiegati a mano invece di lasciarli a RMAC: vedi
; [[feedback_rmac_immediate_expr]], le espressioni immediate lunghe possono
; essere valutate male. La derivazione resta qui per poterli ricontrollare.
;---------------------------------------------------------------------------
; NTSC 60 Hz interlacciato
N_HP            EQU     844
N_HBB           EQU     1697
N_HBE           EQU     118
N_HS            EQU     1743
N_HVS           EQU     595
N_HEQ           EQU     780
N_VP            EQU     524                 ; 525 mezze righe: dispari => interlace
N_VEB           EQU     512
N_VS            EQU     518
N_VEE           EQU     5
N_VBE           EQU     30
N_VBB           EQU     512
N_VDB           EQU     30                  ; >= VBE
N_VDE           EQU     510                 ; <= VBB; 480 mezze righe = 240 per campo

; PAL 50 Hz interlacciato
P_HP            EQU     850
P_HBB           EQU     1709
P_HBE           EQU     153
P_HS            EQU     1749
P_HVS           EQU     600
P_HEQ           EQU     787
P_VP            EQU     624
P_VEB           EQU     614
P_VS            EQU     619
P_VEE           EQU     4
P_VBE           EQU     40
P_VBB           EQU     614
P_VDB           EQU     40
P_VDE           EQU     612                 ; 572 mezze righe = 286 per campo

; Colori RGB16 ( r5<<11 | b5<<6 | g6 ), come tools/cart_pack_all.py
COL_WHITE       EQU     $FFFF
COL_BLACK       EQU     $0000
COL_BOOT        EQU     $0018               ; verde scuro: "sto bootando"

; Indici di palette usati dal percorso di panico (la CLUT M0 e' una rampa di grigi)
IDX_BAR         EQU     255

; Valore con cui si riempie il fondo al boot. Durante la diagnosi di M0 e'
; stato utile metterci un motivo riconoscibile (grigio uniforme, poi il numero
; di riga) per capire quali indirizzi l'OP stesse leggendo davvero: e' la
; tecnica da riprendere quando si tocca il rinfresco dell'oggetto.
CLEAR_FILL      EQU     $00000000

;===========================================================================
Start:
    move.w  #$2700, sr                      ; interrupt mascherati per tutto il boot
    move.l  #STACK_TOP, a7

;---------------------------------------------------------------------------
; S53: SEQUENZA DI QUIETE. Con la GameDrive una ROM parte dopo un reboot via
; USB, e la GPU, il DSP, gli interrupt e il Blitter della ROM precedente sono
; ancora vivi (JaguarDocs metodi/gamedrive_usb.md 5). L'ordine conta: INT1
; azzera i pendenti PRIMA di INT2=0 - l'ordine inverso lascia ihandler alto e
; ferma GPU e Blitter. L'attesa del Blitter ha un tetto.
;---------------------------------------------------------------------------
    move.l  #0, G_CTRL                      ; GPU ferma
    move.l  #0, D_CTRL                      ; DSP fermo
    move.w  #$1F00, INT1                    ; tutti i pendenti azzerati, nessuna sorgente
    move.w  #0, INT2
    move.l  #200000, d0
.st_blit:
    move.l  B_CMD, d1
    btst    #0, d1                          ; bit 0 = Blitter inattivo
    bne.s   .st_blit_ok
    subq.l  #1, d0
    bne.s   .st_blit
.st_blit_ok:
; E si azzera quel che il codice nuovo legge prima di scrivere: le variabili
; dell'HAL (contatori del canale, del profiler, del DSP) e i cassetti del
; profiler. Il resto della DRAM lo inizializza chi lo usa, come prima.
;
; SOLO SUL FERRO tutti i 4+8 KB. Su BigPEmu i due cicli lunghi rompono il menu
; (righe dei due campi sfalsate, banda verde in mezzo), e non per quel che
; azzerano: bisezione di S53, rompe azzerare la sola HAL e rompe azzerare i
; soli cassetti, due zone senza niente in comune, mentre azzerare i soli campi
; dichiarati no. Conta il TEMPO del boot: su BigPEmu il secondo campo si arma
; sorvegliando HEIGHT e la fase del primo dipende da quando si accende il
; video (IPOTESI, non verificata oltre: BigPEmu non e' il bersaglio del 480i).
; Sul ferro il campo lo dice il bit 11 di VC. All'emulatore bastano i campi.
    .if TARGET_HW=1
    lea     HAL_VARS, a0
    move.w  #(HAL_VARS_SIZE/4)-1, d0
.st_hal:
    clr.l   (a0)+
    dbra    d0, .st_hal
    lea     PROF_BINS, a0
    move.w  #(PROF_BINS_SIZE/4)-1, d0
.st_prof:
    clr.l   (a0)+
    dbra    d0, .st_prof
    .else
    lea     HAL_VARS, a0
    move.w  #((HAL_FIELDS_END-HAL_VARS)/4)-1, d0
.st_hal:
    clr.l   (a0)+
    dbra    d0, .st_hal
    .endif

    bsr     InitVectors

; S53: il canale COM8 (src/gd.s). Ogni attesa ha un tetto: senza GameDrive, o
; con un firmware vecchio, GD_BIOS resta 0 e il gioco prosegue muto. Solo sul
; ferro: su BigPEmu con "Force GD" l'Init del BIOS fa crashare la CPU.
;
; DALLA DRAM. Chiamato dal codice in cartuccia, GdInstall bloccava il boot sul
; ferro: schermo nero, nessuna riga; la stessa ROM senza la chiamata partiva
; (S53, bisezione sul ferro). La cartuccia E' la GameDrive, e tutti i progetti
; che usano il canale eseguono dalla DRAM. Il blocco di gd.s e' indipendente
; dalla posizione: si copia in GD_CODE e si chiama li'.
    lea     GdBlock, a0
    lea     GD_CODE, a1
    move.w  #((GdBlockEnd-GdBlock+3)/4)-1, d0
.st_gdcopy:
    move.l  (a0)+, (a1)+
    dbra    d0, .st_gdcopy
    .if TARGET_HW=1
    lea     GD_CODE, a0
    adda.l  #GdInstall-GdBlock, a0
    jsr     (a0)
    .endif
    bsr     InitVideoTiming
    bsr     VideoOnMinimal                  ; da qui in poi un blocco si VEDE
    bsr     InitClut
    bsr     ClearFrameBuffer
    bsr     InitOpList

; Il codice della GPU TOM nella sua RAM locale. Va fatto PRIMA del blob C,
; perche' il primo Body_DrawAll lancia gia' GpuBodyMain, e una volta sola: la
; RAM della GPU non la tocca nessun altro.
    bsr     InitGpuBody

; E il secondo kernel, lo stadio dell'intensita' (S40). I kernel stanno insieme
; nei 4 KB di RAM locale (la mappa e' GPU_SRAM_* in tools/memory_map.py):
; si copiano tutti qui e per lanciarli si riprogramma solo il PC. Ricaricare
; il codice a ogni lancio sarebbe traffico sul bus in mezzo al disegno.
    bsr     InitGpuShade

; E il terzo: il front-end dei corpi (S45) - posa dei vertici, proiezione,
; faccia nascosta e chiave del pittore, un lancio per attore.
    bsr     InitGpuFront

; L'audio su Jerry, sempre prima del blob C e sempre una volta sola. Da qui in
; poi il DSP gira per conto suo e non lo ferma piu' nessuno: l'ISR I2S produce
; un campione a ogni strobe e il ciclo di riposo conta. Il 68000 non deve
; servirlo - se dovesse, sarebbe gia' l'architettura sbagliata.
    bsr     InitDspAudio

; --- Passaggio di controllo al blob C, eseguito in place dalla cartuccia ---
; engine_init copia .data dalla ROM, azzera .bss, verifica le tre sonde di
; sezione e torna il magic. Qualunque altro valore = linker script sbagliato.
    move.l  #C_ENTRY_INIT, a0
    jsr     (a0)
    cmp.l   #C_ENGINE_MAGIC, d0
    bne     CInitFail

; --- Interrupt video acceso: da qui VBL_TICK avanza ---
    moveq   #0, d0
    move.l  d0, VBL_TICK
    move.l  d0, LAST_TICK
    move.l  d0, DBG_ODD_CNT
    move.l  #$0F0F5A5A, DBG_CAL             ; motivo noto: taratura della lettura
    move.w  #0, INT2
    move.w  #C_VIDENA|C_VIDCLR, INT1
    move.w  #$2000, sr

    .if FORCE_EXCEPTION=1
    illegal                                 ; -> 4 lampi
    .endif
    .if FORCE_EXCEPTION=2
    move.l  #ROM_CODE+1, a0                 ; fetch a indirizzo dispari
    jsr     (a0)                            ; -> 3 lampi (Address Error)
    .endif

;---------------------------------------------------------------------------
; MainLoop: una chiamata a engine_frame() per tick VBL.
; Il `move.l` da VBL_TICK e' atomico rispetto all'interrupt: il 68000 non
; interrompe un'istruzione a meta'.
;---------------------------------------------------------------------------
MainLoop:
    move.l  VBL_TICK, d0
    cmp.l   LAST_TICK, d0
    beq.s   MainLoop
    move.l  d0, LAST_TICK
    move.l  #C_ENTRY_FRAME, a0
    jsr     (a0)
    bra.s   MainLoop

;---------------------------------------------------------------------------
; InitVectors: TUTTA la tabella, non solo i vettori che ci servono.
; Ogni voce non assegnata porta a una sonda che lampeggia 20 volte; le dieci
; eccezioni CPU hanno una sonda propria che lampeggia il proprio numero di
; vettore, cosi' il conteggio identifica il guasto senza dipendere dai colori.
;---------------------------------------------------------------------------
InitVectors:
    movea.l #$08, a0                        ; $00/$04 = SP e PC iniziali: non si toccano
    move.w  #((VECTORS_SIZE-8)/4)-1, d0
.iv_fill:
    move.l  #ExcOther, (a0)+
    dbra    d0, .iv_fill

    move.l  #ExcBus,      $08               ; 2 lampi
    move.l  #ExcAddr,     $0C               ; 3 lampi  <- l'eccezione che uccise la V4
    move.l  #ExcIllegal,  $10               ; 4
    move.l  #ExcDiv0,     $14               ; 5
    move.l  #ExcChk,      $18               ; 6
    move.l  #ExcTrapV,    $1C               ; 7
    move.l  #ExcPriv,     $20               ; 8
    move.l  #ExcTrace,    $24               ; 9
    move.l  #ExcLineA,    $28               ; 10
    move.l  #ExcLineF,    $2C               ; 11
    move.l  #ViHandler,   $100              ; interrupt video
    rts

;---------------------------------------------------------------------------
; InitVideoTiming: rilevamento NTSC/PAL e timing 480i.
; Interlace per il TRM: il numero di mezze righe per campo deve essere DISPARI,
; cioe' VP+1 dispari, cioe' VP pari. Codice conservato dalla V4 (S20), e' la
; parte che funziona.
;---------------------------------------------------------------------------
InitVideoTiming:
    ; --- HC e VC PER PRIMI. Non e' un dettaglio. ---
    ; Il bit 11 di VC dice quale campo si sta disegnando, ma il suo legame con
    ; "campo alto"/"campo basso" e' INDIRETTO: si limita a invertirsi a ogni
    ; campo, per conto suo. Se si accende l'interlace senza far ripartire i
    ; contatori da uno stato noto, c'e' il 50% di probabilita' di avere la
    ; polarita' rovesciata - cioe' le righe pari e dispari scambiate.
    ; In S25 il bit 11 sembrava "non alzarsi mai": era questo.
    move.w  #0, HC
    move.w  #1, VC

    move.w  BASE+$14002, d0
    and.w   #$10, d0
    beq     .ivt_pal                        ; non .s: il ramo NTSC e' piu' lungo
                                            ; di 127 byte, il displacement a 8
                                            ; bit non ci arriva
.ivt_ntsc:
    move.w  #823, d2
    move.w  #1409, d0
    move.w  #N_HP, HP
    move.w  #N_HBB, HBB
    move.w  #N_HBE, HBE
    move.w  #N_HS, HS
    move.w  #N_HVS, HVS
    move.w  #N_HEQ, HEQ
    move.w  #TICK_HZ_NTSC, TICK_HZ          ; base di TimerRef per il blob C
    move.w  #N_VP, VP
    move.w  #N_VP, VP_VAL
    move.w  #N_VEB, VEB
    move.w  #N_VS, VS
    move.w  #N_VEE, VEE
    move.w  #N_VBE, VBE
    move.w  #N_VBB, VBB
    move.w  #N_VDB, d5
    move.w  #N_VDE, d6
    bra     .ivt_common                     ; idem: oltre la portata di bra.s
.ivt_pal:
    move.w  #843, d2
    move.w  #1381, d0
    move.w  #P_HP, HP
    move.w  #P_HBB, HBB
    move.w  #P_HBE, HBE
    move.w  #P_HS, HS
    move.w  #P_HVS, HVS
    move.w  #P_HEQ, HEQ
    move.w  #TICK_HZ_PAL, TICK_HZ           ; base di TimerRef per il blob C
    move.w  #P_VP, VP
    move.w  #P_VP, VP_VAL
    move.w  #P_VEB, VEB
    move.w  #P_VS, VS
    move.w  #P_VEE, VEE
    move.w  #P_VBE, VBE
    move.w  #P_VBB, VBB
    move.w  #P_VDB, d5
    move.w  #P_VDE, d6
.ivt_common:
    move.w  d0, d1
    asr     #1, d1
    sub.w   d1, d2
    add.w   #4, d2
    sub.w   #1, d1
    or.w    #$400, d1
    move.w  d1, HDE
    move.w  d2, HDB1
    move.w  d2, HDB2

    move.w  d5, VDB
    move.w  d5, VDB_VAL
    move.w  d6, VDE
    move.w  d6, VDE_VAL

    ; VI: primo aggancio. La parita' giusta la sceglie ViHandler campo per
    ; campo leggendo VC; qui serve solo un valore di partenza dopo VDE, cioe'
    ; nel blanking, dove l'OP non sta usando gli oggetti.
    move.w  d6, d0
    addq.w  #1, d0
    move.w  d0, VI
    move.l  #0, BORD1
    rts

;---------------------------------------------------------------------------
; VideoOnMinimal: OP list ridotta al solo STOP e video acceso. Tutto lo schermo
; mostra il registro BG, quindi da qui in avanti un blocco durante il boot e'
; visibile invece che nero. InitOpList sostituira' questa lista.
;---------------------------------------------------------------------------
VideoOnMinimal:
    bsr     StopOnlyOpList
    move.w  #COL_BOOT, BG
    rts

StopOnlyOpList:
    move.l  #0, OP_STOP
    move.l  #OBJ_TYPE_STOP, OP_STOP+4
    move.l  #OP_STOP, d0
    swap    d0                              ; OLPLO = word alta, OLPHI = word bassa
    move.l  d0, OLP
    move.w  #VIDEN|BGEN|PWIDTH2|RGB16|CSYNC, VMODE
    rts

;---------------------------------------------------------------------------
; InitClut: rampa di 256 grigi. In M0 non c'e' ancora la palette della scena
; (arriva con M2), ma serve che OGNI indice sia visibile, altrimenti un errore
; di disegno sarebbe indistinguibile da uno schermo vuoto.
;   RGB16 = r5<<11 | b5<<6 | g6, con r5 = b5 = i>>3 e g6 = i>>2.
;---------------------------------------------------------------------------
InitClut:
    movea.l #CLUT, a0
    moveq   #0, d0
    move.w  #255, d7
    moveq   #11, d4                         ; il 68000 accetta shift immediati solo fino a 8
.ic_loop:
    move.w  d0, d1
    lsr.w   #3, d1                          ; r5 = b5
    move.w  d1, d2
    lsl.w   d4, d2                          ; r5 << 11
    move.w  d1, d3
    lsl.w   #6, d3                          ; b5 << 6
    or.w    d3, d2
    move.w  d0, d3
    lsr.w   #2, d3                          ; g6
    or.w    d3, d2
    move.w  d2, (a0)+
    addq.w  #1, d0
    dbra    d7, .ic_loop
    rts

;---------------------------------------------------------------------------
; ClearFrameBuffer: azzera il fondo unico. E' un ciclo su long, non su pixel,
; ed e' one-shot al boot (~95000 scritture, sotto i 100 ms). A regime il 68000
; non tocchera' piu' il framebuffer: il fondo si disegna una volta per stanza e
; gli attori sono oggetti dell'OP.
; dbra conta a 16 bit e qui i passi sono 94720, quindi contatore a 32 bit.
;---------------------------------------------------------------------------
ClearFrameBuffer:
    .if DEBUG_ROWFILL=1
    bra     FillFrameBufferRows
    .endif
    .if DEBUG_ROWFILL=2
    bra     FillFrameBufferMarks
    .endif
    .if DEBUG_ROWFILL=3
    bra     FillFrameBufferSteps
    .endif
    movea.l #BG_FB, a0
    move.l  #(BG_W*BG_H)/4, d0
    move.l  #CLEAR_FILL, d1
.cf_loop:
    move.l  d1, (a0)+
    subq.l  #1, d0
    bne.s   .cf_loop
    rts

;---------------------------------------------------------------------------
; FillFrameBufferRows: strumento di misura, non codice di gioco.
;
; Riempie OGNI riga del framebuffer con un valore che ne codifica indice e
; parita', cosi' e' lo schermo a dire quale riga di framebuffer finisce su quale
; riga di display, invece di farcelo dedurre da una scala misurata a occhio.
;
;   riga PARI    -> 40  + ((riga>>1) & 63)   = grigi SCURI, rampa lenta
;   riga DISPARI -> 160 + ((riga>>1) & 63)   = grigi CHIARI, stessa rampa
;
; Come si legge il risultato:
;   scuro/chiaro alternati  -> l'OP pesca da entrambe le parita' di riga: i due
;                              campi finiscono davvero sullo schermo;
;   tutto scuro             -> disegnate solo le righe pari, un campo solo;
;   scuro + verde di BG     -> un campo disegnato e l'altro mai riempito;
; e la rampa da' il conto assoluto delle righe, che una distanza in pixel non
; puo' dare. E' la tecnica raccomandata a fine S25 e mai applicata.
;---------------------------------------------------------------------------
;---------------------------------------------------------------------------
; FillFrameBufferMarks: misura FINE del disallineamento fra i due campi.
;
; La rampa di FillFrameBufferRows cambia grigio ogni 16 righe, quindi non puo'
; misurare un offset con precisione migliore di +/-16 righe. Qui invece: fondo
; nero e quattro righe piene e luminose a righe di framebuffer NOTE, scelte a
; coppie di parita' opposta.
;
;   riga 100 (PARI, campo A) e riga 101 (DISPARI, campo B)
;   riga 300 (PARI, campo A) e riga 301 (DISPARI, campo B)
;
; Se i campi sono allineati le due righe di una coppia escono ADIACENTI sullo
; schermo (una banda unica). Se il campo B e' in ritardo di N righe di output,
; escono a 2*N pixel di distanza - e la distanza si legge con precisione di una
; riga. Due coppie a altezze diverse dicono anche se l'offset e' costante.
;---------------------------------------------------------------------------
FillFrameBufferMarks:
    movem.l d0-d2/a0, -(sp)
    movea.l #BG_FB, a0
    move.l  #(BG_W*BG_H)/4, d0
    moveq   #0, d1
.ffm_clear:
    move.l  d1, (a0)+
    subq.l  #1, d0
    bne.s   .ffm_clear

    moveq   #0, d2                          ; indice della coppia
.ffm_pair:
    move.l  #100, d0                        ; prima coppia a riga 100
    tst.w   d2
    beq.s   .ffm_have
    move.l  #300, d0                        ; seconda coppia a riga 300
.ffm_have:
    bsr     MarkRow                         ; riga PARI
    addq.l  #1, d0
    bsr     MarkRow                         ; riga DISPARI subito sotto
    addq.w  #1, d2
    cmpi.w  #2, d2
    blt.s   .ffm_pair
    movem.l (sp)+, d0-d2/a0
    rts

;---------------------------------------------------------------------------
; FillFrameBufferSteps: misura dell'allineamento fra campi LEGGIBILE A OCCHIO,
; pensata per l'hardware vero dove non si puo' contare i pixel con un programma.
;
; Due blocchi pieni AFFIANCATI, alti 100 righe:
;   sinistro, x 40..299  -> bordo superiore su riga PARI    (200) = campo A
;   destro,   x 340..599 -> bordo superiore su riga DISPARI (201) = campo B
;
; Se i campi sono allineati i due bordi superiori sono a filo (una riga di
; scarto, invisibile). Se il campo B e' spostato di N righe, il bordo del blocco
; destro si stacca da quello del sinistro di N righe: uno SCALINO fra due bordi
; adiacenti, che l'occhio giudica bene anche attraverso uno scaler.
;
; Un blocco pieno non alterna righe chiare e scure, quindi NON fa twitter: al
; contrario della rampa, qui quel che si vede e' geometria e non percezione.
;---------------------------------------------------------------------------
FillFrameBufferSteps:
    movem.l d0-d3/a0, -(sp)
    movea.l #BG_FB, a0
    move.l  #(BG_W*BG_H)/4, d0
    moveq   #0, d1
.ffs_clear:
    move.l  d1, (a0)+
    subq.l  #1, d0
    bne.s   .ffs_clear

    move.w  #200, d3                        ; blocco sinistro, bordo su riga PARI
.ffs_left:
    moveq   #0, d0
    move.w  d3, d0
    move.l  #40, d1
    move.w  #260, d2
    bsr     MarkSpan
    addq.w  #1, d3
    cmpi.w  #300, d3
    blt.s   .ffs_left

    move.w  #201, d3                        ; blocco destro, bordo su riga DISPARI
.ffs_right:
    moveq   #0, d0
    move.w  d3, d0
    move.l  #340, d1
    move.w  #260, d2
    bsr     MarkSpan
    addq.w  #1, d3
    cmpi.w  #301, d3
    blt.s   .ffs_right
    movem.l (sp)+, d0-d3/a0
    rts

; MarkSpan: riempie di $FF, sulla riga d0, d2 byte a partire dalla colonna d1.
MarkSpan:
    movem.l d0-d2/a0, -(sp)
    mulu.w  #BG_W, d0
    movea.l #BG_FB, a0
    adda.l  d0, a0
    adda.l  d1, a0
    subq.w  #1, d2
.ms_px:
    move.b  #$FF, (a0)+
    dbra    d2, .ms_px
    movem.l (sp)+, d0-d2/a0
    rts

; MarkRow: riempie di $FF la riga di framebuffer in d0.
MarkRow:
    movem.l d0-d1/a0, -(sp)
    mulu.w  #BG_W, d0
    movea.l #BG_FB, a0
    adda.l  d0, a0
    move.w  #(BG_W/4)-1, d1
.mr_px:
    move.l  #$FFFFFFFF, (a0)+
    dbra    d1, .mr_px
    movem.l (sp)+, d0-d1/a0
    rts

FillFrameBufferRows:
    movem.l d0-d4/d7/a0, -(sp)
    movea.l #BG_FB, a0
    moveq   #0, d3                          ; indice di riga
    move.w  #BG_H-1, d7
.ffr_row:
    move.w  d3, d2
    lsr.w   #1, d2
    and.w   #63, d2
    btst    #0, d3
    beq.s   .ffr_even
    add.w   #160, d2                        ; riga dispari: grigi chiari
    bra.s   .ffr_have
.ffr_even:
    add.w   #40, d2                         ; riga pari: grigi scuri
.ffr_have:
    moveq   #0, d1
    move.b  d2, d1                          ; 000000BB
    move.l  d1, d0
    lsl.l   #8, d1
    or.l    d0, d1                          ; 0000BBBB
    move.l  d1, d0
    swap    d1                              ; BBBB0000 (il 68000 shifta al massimo di 8)
    or.l    d0, d1                          ; BBBBBBBB

    move.w  #(BG_W/4)-1, d4
.ffr_px:
    move.l  d1, (a0)+
    dbra    d4, .ffr_px

    addq.w  #1, d3
    dbra    d7, .ffr_row
    movem.l (sp)+, d0-d4/d7/a0
    rts

;---------------------------------------------------------------------------
; InitOpList: la lista definitiva.
;   OP_BR_VDE -> se VC > VDE salta a STOP
;   OP_BR_VDB -> se VC < VDB salta a STOP
;   OP_BG     -> bitmap del fondo, LINK allo STOP finale
; Gli slot per gli attori (OP_ACTORS) sono gia' riservati dal master ma non
; ancora inseriti nella catena: entrano in M3.
;---------------------------------------------------------------------------
InitOpList:
; Prima si riempie TUTTA la regione di oggetti STOP. Al reset la DRAM contiene
; quello che ci ha lasciato il BIOS, e un solo campo di LINK sbagliato basta a
; mandare l'OP a spasso nella memoria altrui. Con la regione tutta STOP,
; qualunque errore di concatenamento si ferma invece di disegnare spazzatura.
    movea.l #OP_LIST, a0
    move.w  #(OP_LIST_SIZE/8)-1, d0
.iol_stop:
    move.l  #0, (a0)+
    move.l  #OBJ_TYPE_STOP, (a0)+
    dbra    d0, .iol_stop

    move.l  #0, OP_STOP
    move.l  #OBJ_TYPE_STOP, OP_STOP+4
    move.l  #0, OP_TAIL_STOP
    move.l  #OBJ_TYPE_STOP, OP_TAIL_STOP+4

    move.l  #LINK_HI_STOP, OP_BR_VDE
    moveq   #0, d0
    move.w  VDE_VAL, d0
    lsl.l   #3, d0                          ; YPOS in bit 3-13
    or.l    #(LINK_LO_STOP<<24)|(CC_YPOS_LT<<14)|OBJ_TYPE_BRANCH, d0
    move.l  d0, OP_BR_VDE+4

    move.l  #LINK_HI_STOP, OP_BR_VDB
    moveq   #0, d0
    move.w  VDB_VAL, d0
    lsl.l   #3, d0
    or.l    #(LINK_LO_STOP<<24)|(CC_YPOS_GT<<14)|OBJ_TYPE_BRANCH, d0
    move.l  d0, OP_BR_VDB+4

;---------------------------------------------------------------------------
; UN SOLO oggetto di fondo (S26).
;
; Fino a S25 ce n'erano DUE, con YPOS di parita' opposta, nella convinzione che
; l'hardware assegnasse ciascuno a un campo. Il TRM smentisce, campo YPOS della
; phrase 0: "The object will be active while the vertical counter >= YPOS and
; HEIGHT > 0". E' una SOGLIA, non un confronto di uguaglianza: nessuna parita'
; entra nella decisione. I due oggetti erano quindi attivi ENTRAMBI in ENTRAMBI
; i campi, disegnati in sequenza nello stesso line buffer, e vinceva il secondo.
;
; E' anche la spiegazione dell'esperimento di S25 che era sembrato assurdo: due
; oggetti con parita' di YPOS opposta davano lo stesso identico artefatto.
; Davano lo stesso artefatto perche' la parita' non ha mai fatto niente.
;
; Il campo lo sceglie il SOFTWARE: ViHandler sposta DATA di una riga a ogni
; interrupt. L'hardware, dal canto suo, l'interlace lo sa gia' fare - stesso
; campo HEIGHT: "the height is reduced by one for non-interlaced displays or by
; two for interlaced displays". Con HEIGHT = 480 l'oggetto si esaurisce in
; esattamente un campo (240 righe disegnate x 2), e DWIDTH = 160 phrase (due
; righe) fa saltare una riga a ogni linea disegnata.
;---------------------------------------------------------------------------
    moveq   #0, d0
    move.w  VDB_VAL, d0
    lsl.l   #3, d0                          ; YPOS in bit 3-13
    .if ACTOR_SPRITE=1
; Il fondo aggancia lo SPRITE invece del TAIL. Solo LINK_LO cambia: il
; LINK_HI di OP_ACTORS coincide con quello del TAIL (=1), quindi i long
; ALTI del fondo (qui e in ViHandler) restano quelli di sempre.
    or.l    #(LINK_LO_ACT0<<24)|(BG_HEIGHT_RAW<<14)|OBJ_TYPE_BMP, d0
    .else
    or.l    #(LINK_LO_TAIL<<24)|(BG_HEIGHT_RAW<<14)|OBJ_TYPE_BMP, d0
    .endif
    move.l  d0, OP_BG_PH0LO
    move.l  d0, OP_BG_F0+4
    move.l  #(BG_FB<<8)|LINK_HI_TAIL, OP_BG_F0
    move.l  #OP_BG_PH1_HI, OP_BG_F0+8
    move.l  #OP_BG_PH1_LO, OP_BG_F0+12

    .if ACTOR_SPRITE=1
; Lo SPRITE parte SPENTO: HEIGHT zero, LINK al TAIL. Le due phrase le
; riempie il C a ogni passata (Body_DrawAll sa dove sta l'attore e quanto
; e' alto), il gestore di interrupt le rimette a ogni campo. Spento e'
; anche lo stato giusto per tutto il caricamento, che dura secondi.
    moveq   #0, d0
    move.w  VDB_VAL, d0
    lsl.l   #3, d0
    or.l    #(LINK_LO_TAIL<<24)|OBJ_TYPE_BMP, d0     ; HEIGHT = 0
    move.l  d0, OP_SPR_PH0LO
    move.l  d0, OP_ACTORS+4
    move.l  #ACTOR_SPRITES, d0
    lsl.l   #8, d0
    or.l    #LINK_HI_TAIL, d0
    move.l  d0, OP_ACTORS
    move.l  #ACTOR_SPRITES, SPR_DATA_A
    move.l  #ACTOR_SPRITES, SPR_DATA_B
    move.l  #(BG_DEPTH_8BPP<<12)|(1<<15)|(SPR_DWIDTH<<18)|((SPR_IWIDTH&$F)<<28), d0
    move.l  d0, OP_SPR_PH1LO
    move.l  d0, OP_ACTORS+12
    move.l  #OP_SPR_PH1_HI, OP_ACTORS+8
; E le copie VIVE, che ViHandler adotta solo al campo A: se il primo interrupt
; fosse un campo B, armerebbe da memoria mai scritta. Si copiano DAI CAMPI e
; non da d0, che qui dentro ha gia' cambiato mestiere due volte.
    move.l  SPR_DATA_A, SPR_LIVE_DATA_A
    move.l  SPR_DATA_B, SPR_LIVE_DATA_B
    move.l  OP_SPR_PH0LO, SPR_LIVE_PH0LO
    move.l  OP_SPR_PH1LO, SPR_LIVE_PH1LO
    .endif

    move.l  #OP_BR_VDE, d0
    swap    d0
    move.l  d0, OLP
    rts

;---------------------------------------------------------------------------
; ViHandler: interrupt video, una volta per campo.
; Fa tre cose e basta: avanza il contatore, punta il fondo alla riga giusta per
; il campo corrente (interlace 480i), e chiude l'interrupt come si deve
; (C_VIDCLR + INT2, che la V4 ometteva).
; Niente logica di gioco qui dentro: quella sta nel C, chiamata da MainLoop.
;---------------------------------------------------------------------------
ViHandler:
    movem.l d0-d2, -(sp)
    addq.l  #1, VBL_TICK

;---------------------------------------------------------------------------
; S53: il profiler a campionamento. Il PC interrotto (nella cornice, sopra i
; tre registri e a0) cade in un cassetto da 64 byte di tutto il codice in
; cartuccia, ROM_CODE..fine di ROM_CBLOB; fuori (oggi niente, domani il codice
; caldo copiato in DRAM) si conta e basta. Lo accende il C (hal_log.c), che
; legge e azzera i cassetti. Campionare al VI e' agganciato alla fase del
; video: per i TEMPI fanno fede i cronometri in mezze righe, questo dice DOVE.
;---------------------------------------------------------------------------
PROF_SPAN       EQU     ROM_CBLOB+ROM_CBLOB_SIZE-ROM_CODE
; La parentesi NON e' cosmetica: RMAC legge "A = B*32" come "(A = B)*32"
; (il confronto lega piu' della moltiplicazione), e l'assert cade.
    .assert PROF_SPAN = (PROF_BINS_SIZE*32)
    tst.w   PROF_ON
    beq.s   .vh_noprof
    move.l  a0, -(sp)
    move.l  18(sp), d0                      ; PC: d0-d2 (12) + a0 (4) + SR (2)
    sub.l   #ROM_CODE, d0
    cmp.l   #PROF_SPAN, d0
    bcc.s   .vh_prof_out                    ; senza segno: anche sotto ROM_CODE
    lsr.l   #5, d0                          ; (PC>>6)*2, il cassetto e' una word
    and.w   #$FFFE, d0
    lea     PROF_BINS, a0
    addq.w  #1, 0(a0,d0.l)
    bra.s   .vh_prof_n
.vh_prof_out:
    addq.l  #1, PROF_OUT
.vh_prof_n:
    addq.l  #1, PROF_N
    move.l  (sp)+, a0
.vh_noprof:

    ; DIAGNOSTICA: fotografa l'oggetto del campo pari com'e' adesso, cioe' dopo
    ; che l'OP lo ha usato e prima che noi lo rimettiamo a posto.
    move.l  OP_BG_F0, DBG_F0_HI
    move.l  OP_BG_F0+4, DBG_F0_LO
    moveq   #0, d0
    move.w  VC, d0
    move.l  d0, DBG_VC
    btst    #11, d0
    beq.s   .vh_not_odd
    addq.l  #1, DBG_ODD_CNT
.vh_not_odd:

    ; --- Alternanza di campo guidata dal bit 11 di VC (S26) ---
    ; Il bit 11 di VC dice quale campo e' stato appena disegnato: 0 = campo
    ; alto, 1 = campo basso. Vale pero' solo perche' InitVideoTiming ha fatto
    ; ripartire HC e VC da uno stato noto prima di accendere l'interlace.
    ;
    ; Quando l'interrupt scatta il campo successivo non e' ancora cominciato,
    ; quindi tutto qui dentro riguarda il PROSSIMO campo, non quello appena
    ; finito. Servono due cose, e servono entrambe:
    ;
    ;   1. VI di parita' giusta per il prossimo campo, altrimenti l'interrupt
    ;      salta un campo su due (i "30 al secondo invece di 60" di S25).
    ;      Bit 11 alto (appena disegnato il campo basso) -> prossimo e' l'alto
    ;      -> VI pari. E viceversa.
    ;   2. DATA sulla riga giusta: il campo alto parte dalla riga 0, il basso
    ;      dalla riga 1. Con DWIDTH doppio l'OP salta una riga per volta, e
    ;      queste due partenze coprono il framebuffer intero.
    ; Il bit 11 di VC NON e' utilizzabile qui. Zerosquare lo ha verificato su
    ; hardware vero con un analizzatore logico, ma su BigPEmu non alterna: la
    ; sonda di S26 lo legge costantemente alto (vc=2048), e S25 era arrivata
    ; alla stessa conclusione per un'altra strada. Provato in S26 in ENTRAMBE
    ; le polarita': nessuna delle due recupera il campo mancante.
    ;
    ; Quindi la parita' del campo la contiamo noi. E' la stessa alternanza
    ; software di S25 - che era gia' misurata dare un interrupt PER CAMPO
    ; (63/s invece di 30/s) - ma ora sopra un HEIGHT corretto: allora l'oggetto
    ; era alto il doppio del campo e nessuna alternanza poteva funzionare.
    .if FIELD_SRC=0
    move.w  FIELD_PAR, d1
    eori.w  #1, d1
    move.w  d1, FIELD_PAR
    .endif

    .if FIELD_SRC=1
; Ricetta di Zerosquare, per HARDWARE VERO. Il bit 11 di VC si inverte a ogni
; campo per conto suo, quindi dice quale campo e' stato appena disegnato. Al
; momento dell'interrupt il campo successivo non e' ancora cominciato, percio'
; tutto qui dentro riguarda il PROSSIMO campo:
;   bit 11 ALTO  -> il prossimo campo vuole VI PARI    -> d1 = 0
;   bit 11 BASSO -> il prossimo campo vuole VI DISPARI -> d1 = 1
; Su BigPEmu questo non funziona (bit 11 misurato basso su 344 interrupt su 344),
; ma Zerosquare l'ha verificato su hardware con analizzatore logico.
; Se le righe escono scambiate, invertire FIELD_PHASE.
    moveq   #0, d1
    move.w  VC, d0
    btst    #11, d0
    bne.s   .vh_par_done
    moveq   #1, d1
.vh_par_done:
    .if FIELD_PHASE=1
    eori.w  #1, d1
    .endif
    move.w  d1, FIELD_PAR
    .endif

    .if VI_ALTERNATE=1
    move.w  VDE_VAL, d0                     ; VDE pari per costruzione (510)
    add.w   d1, d0
    move.w  d0, VI                          ; le due parita' agganciano i due campi
    .endif

    .if SECOND_FIELD_POLL=0
    tst.w   d1
    beq.s   .vh_row0
    move.l  #((BG_FB+BG_W)<<8)|LINK_HI_TAIL, OP_BG_F0
    bra.s   .vh_row_done
.vh_row0:
    move.l  #(FIELD_A_DATA<<8)|LINK_HI_TAIL, OP_BG_F0
.vh_row_done:
    move.l  OP_BG_PH0LO, d0                 ; YPOS + HEIGHT + LINK LO, ripristinati
    move.l  d0, OP_BG_F0+4
    move.l  #OP_BG_PH1_HI, OP_BG_F0+8
    move.l  #OP_BG_PH1_LO, OP_BG_F0+12

    .if ACTOR_SPRITE=1
; Lo SPRITE si riarma come il fondo: l'OP lo consuma disegnandolo
; (feedback_op_display_no_interlace), quindi phrase 0 e 1 si riscrivono a
; ogni campo, con DATA sulla riga del campo che sta per cominciare. I
; valori vengono dal C, che sa dove sta l'attore; qui non si calcola
; niente - dentro l'interrupt si copia e basta.
; IL CAMBIO DI BANCO SI ADOTTA SOLO AL CAMPO A. Il C scrive SPR_DATA_A/B e le
; due phrase quando ha finito di disegnare, cioe' in un istante qualsiasi
; rispetto alla scansione; se il campo B di una coppia leggesse gia' i valori
; nuovi mentre il campo A ha letto i vecchi, righe pari e dispari verrebbero da
; due pose diverse - ed e' esattamente cosi' che l'eroe si vede SFRANGIATO. Le
; copie vive rendono la coppia di campi coerente per costruzione.
    tst.w   d1
    bne.s   .vh_spr_live                    ; campo B: si arma con quel che c'e'
    move.l  SPR_DATA_A, SPR_LIVE_DATA_A
    move.l  SPR_DATA_B, SPR_LIVE_DATA_B
    move.l  OP_SPR_PH0LO, SPR_LIVE_PH0LO
    move.l  OP_SPR_PH1LO, SPR_LIVE_PH1LO
.vh_spr_live:
    tst.w   d1
    beq.s   .vh_spr0
    move.l  SPR_LIVE_DATA_B, d0
    bra.s   .vh_spr_done
.vh_spr0:
; LO STESSO BIAS DEL FONDO, che allo sprite mancava. Sul ferro il campo pari e'
; in ritardo di una riga di campo (misurato in S27), e il fondo lo compensa con
; FIELD_A_DATA = BG_FB + 2 righe. Senza, il campo B mostra la riga 1 SOPRA la
; riga 0 del campo A: le righe escono nell'ordine 1,0,3,2,5,4 - coppie
; adiacenti scambiate, cioe' un pettine fine che non dipende ne' dal movimento
; ne' dagli fps. Con il bias tornano consecutive: 1,2,3,4.
; A TARGET_HW=0 la costante e' zero e questa riga non fa niente, che e' giusto:
; su BigPEmu quel ritardo non c'e'.
    move.l  SPR_LIVE_DATA_A, d0
    add.l   #FIELD_A_ROW_BIAS*SPR_PITCH, d0
.vh_spr_done:
    lsl.l   #8, d0                          ; DATA in bit 8-31 della phrase 0
    or.l    #LINK_HI_TAIL, d0
    move.l  d0, OP_ACTORS
    move.l  SPR_LIVE_PH0LO, d0
    move.l  d0, OP_ACTORS+4
    move.l  #OP_SPR_PH1_HI, OP_ACTORS+8
    move.l  SPR_LIVE_PH1LO, d0
    move.l  d0, OP_ACTORS+12
    .endif
    .endif

    .if SECOND_FIELD_POLL=1
;---------------------------------------------------------------------------
; Esperimento S27: armare il secondo campo senza interrupt, chiedendolo all'OP.
;
; Misurato: 30,0 interrupt/s contro 60 campi/s, cioe' UNO PER QUADRO. Fra il
; campo A e il campo B non c'e' nessun interrupt, quindi nessuno schema che
; scambi DATA nel gestore puo' armare il secondo campo - ne' VI alternato ne'
; VI fisso, ed e' per questo che danno lo stesso fotogramma.
;
; Ma non serve un interrupt per sapere quando una passata e' finita: e' l'OP
; stesso a dirlo, azzerando HEIGHT nell'oggetto. Quindi:
;   1. arma il campo A (DATA = riga 0) e lascia disegnare;
;   2. sorveglia HEIGHT finche' non torna a zero = passata finita;
;   3. arma subito il campo B (DATA = riga 1).
;
; Esito 1, righe CHIARE e SCURE alternate: BigPEmu fa due passate per quadro e
;   il campo mancante e' chiuso senza dipendere dalla semantica di VC.
; Esito 2, solo righe SCURE: c'e' UNA passata per quadro, il 480i non e'
;   raggiungibile qui - e averle fatte diventare scure invece di chiare prova
;   comunque che il campo lo controlliamo noi.
; L'attesa ha un tetto: se la passata non arriva, il gestore esce comunque.
;---------------------------------------------------------------------------
    move.l  #(BG_FB<<8)|LINK_HI_TAIL, OP_BG_F0      ; campo A: riga 0
    move.l  OP_BG_PH0LO, d0
    move.l  d0, OP_BG_F0+4
    move.l  #OP_BG_PH1_HI, OP_BG_F0+8
    move.l  #OP_BG_PH1_LO, OP_BG_F0+12
    .if ACTOR_SPRITE=1
    move.l  SPR_DATA_A, d0                  ; sprite, campo A
    lsl.l   #8, d0
    or.l    #LINK_HI_TAIL, d0
    move.l  d0, OP_ACTORS
    move.l  OP_SPR_PH0LO, d0
    move.l  d0, OP_ACTORS+4
    move.l  #OP_SPR_PH1_HI, OP_ACTORS+8
    move.l  OP_SPR_PH1LO, d0
    move.l  d0, OP_ACTORS+12
    .endif

; Il ciclo e' il piu' corto possibile: un test di BYTE, non di long. Il byte a
; OP_BG_F0+5 contiene i bit 16-23 della phrase 0 bassa, cioe' i bit alti di
; HEIGHT: va a zero quando HEIGHT scende sotto 4, che e' entro 4 righe dalla fine
; della passata. Serve a distinguere due cause del disallineamento misurato di 22
; righe di output: se e' la LATENZA di questo ciclo l'offset cala stringendolo,
; se e' il weave dell'emulatore resta 22 identico.
; Il byte a OP_BG_F0+5 sono i bit 23-16 della phrase 0 bassa, cioe' HEIGHT>>2:
; confrontarlo con N vuol dire "mancano meno di 4*N righe alla fine".
;
; La relazione fra soglia e disallineamento e' stata misurata ed e' **1:1**:
; passando dal test di long (HEIGHT esattamente 0) al test di byte (HEIGHT < 4,
; cioe' ~3,5 righe di anticipo) il campo B si e' spostato in alto di 3 righe di
; campo, da 11 a 8 di ritardo. Quindi la soglia e' la manopola giusta.
; POLL_THRESH = 9 -> HEIGHT < 36 -> ~8 righe di campo di anticipo in piu'.
    move.w  #12000, d2                      ; tetto ~un quadro
.vh_poll:
    cmpi.b  #POLL_THRESH, OP_BG_F0+5
    bcs.s   .vh_consumed                    ; senza segno: byte < soglia
    subq.w  #1, d2
    bne.s   .vh_poll
.vh_consumed:
    move.l  #((BG_FB+BG_W)<<8)|LINK_HI_TAIL, OP_BG_F0   ; campo B: riga 1
    move.l  OP_BG_PH0LO, d0
    move.l  d0, OP_BG_F0+4
    .if ACTOR_SPRITE=1
    move.l  SPR_DATA_B, d0                  ; sprite, campo B
    lsl.l   #8, d0
    or.l    #LINK_HI_TAIL, d0
    move.l  d0, OP_ACTORS
    move.l  OP_SPR_PH0LO, d0
    move.l  d0, OP_ACTORS+4
    .endif
    .endif

    move.w  #C_VIDENA|C_VIDCLR, INT1
    move.w  #0, INT2

; S51: the C side's per-interrupt hook (engine_vbl, blob+12). The transplanted
; engine busy-waits on TimerRef, Key, Fire and Joy, so time and the pad have to
; move by themselves, as the DOS timer and keyboard IRQs moved them. engine_vbl
; does nothing until the fork takes over (a null pointer in C .bss, cleared
; before interrupts are ever enabled). d0-d1/a0-a1 are the registers the C ABI
; may clobber; the rest it saves itself.
    movem.l d1/a0-a1, -(sp)
    jsr     C_ENTRY_VBL
    movem.l (sp)+, d1/a0-a1
    movem.l (sp)+, d0-d2
    rte

;---------------------------------------------------------------------------
; CInitFail: engine_init non ha dato il magic. d0 contiene il codice: le tre
; sonde del blob C tornano $0BAD0001/2/3 per .data, .bss e .rodata.
; Qui il framebuffer e la CLUT funzionano di sicuro (li abbiamo appena
; inizializzati), quindi il guasto si disegna: N barre = codice. Statico e
; contabile con calma, meglio di un lampeggio da cronometrare.
;---------------------------------------------------------------------------
CInitFail:
    move.l  d0, d3
    and.l   #7, d3
    bne.s   .cf_have
    moveq   #7, d3                          ; codice fuori dai tre attesi
.cf_have:
    moveq   #0, d4
.cf_bars:
    move.l  d4, d1
    bsr     DrawBar
    addq.l  #1, d4
    cmp.l   d3, d4
    blt.s   .cf_bars
.cf_halt:
    bra.s   .cf_halt

;---------------------------------------------------------------------------
; DrawBar: barra piena 24x64 nel fondo, d1 = indice (0 = piu' a sinistra).
; Solo percorso di guasto: nessun vincolo di velocita'.
;---------------------------------------------------------------------------
DrawBar:
    movem.l d0-d2/a0-a1, -(sp)
    move.l  d1, d0
    mulu.w  #32, d0
    add.l   #16, d0                         ; x = 16 + idx*32
    add.l   #100*BG_W, d0                   ; y = 100
    movea.l #BG_FB, a0
    adda.l  d0, a0
    move.w  #63, d2                         ; 64 righe
.db_row:
    move.w  #23, d1                         ; 24 colonne
    movea.l a0, a1
.db_col:
    move.b  #IDX_BAR, (a1)+
    dbra    d1, .db_col
    adda.l  #BG_W, a0
    dbra    d2, .db_row
    movem.l (sp)+, d0-d2/a0-a1
    rts

;---------------------------------------------------------------------------
; Sonde di eccezione. Il numero di lampi = numero del vettore, cosi' non c'e'
; nessuna tabella da consultare: $0C / 4 = 3, e tre lampi vogliono dire
; Address Error. Tutto cio' che non ha una sonda propria ne fa 20.
;---------------------------------------------------------------------------
ExcBus:     moveq   #2, d1
            bra.s   ExcCommon
ExcAddr:    moveq   #3, d1
            bra.s   ExcCommon
ExcIllegal: moveq   #4, d1
            bra.s   ExcCommon
ExcDiv0:    moveq   #5, d1
            bra.s   ExcCommon
ExcChk:     moveq   #6, d1
            bra.s   ExcCommon
ExcTrapV:   moveq   #7, d1
            bra.s   ExcCommon
ExcPriv:    moveq   #8, d1
            bra.s   ExcCommon
ExcTrace:   moveq   #9, d1
            bra.s   ExcCommon
ExcLineA:   moveq   #10, d1
            bra.s   ExcCommon
ExcLineF:   moveq   #11, d1
            bra.s   ExcCommon
ExcOther:   moveq   #20, d1
            bra.s   ExcCommon

;---------------------------------------------------------------------------
; ExcCommon: non torna mai. Rimette in piedi lo stretto necessario (lo stack
; puo' essere distrutto), spegne la lista oggetti in modo che lo schermo intero
; sia il registro BG, e lampeggia.
;---------------------------------------------------------------------------
ExcCommon:
    move.w  #$2700, sr
    ; S52: la cornice dell'eccezione prima di azzerare lo stack. EXC_SAVE+0 =
    ; a7 al momento del guasto, +4.. = 32 byte da (a7): per Bus/Address Error
    ; funzione(w) indirizzo(l) istruzione(w) SR(w) PC(l), per gli altri SR(w)
    ; PC(l). Si legge da BigPEmu (emu_run -Watch 0x3400,...).
    move.l  a7, EXC_SAVE
    movea.l a7, a0
    lea     EXC_SAVE+4, a1
    moveq   #7, d0
.ex_copy:
    move.l  (a0)+, (a1)+
    dbra    d0, .ex_copy
    move.l  #STACK_TOP, a7
    move.w  d1, EXC_BLINKS
    bsr     StopOnlyOpList
    bsr     ExcReport                       ; S53: la stessa cornice su COM8
.ex_cycle:
    move.w  EXC_BLINKS, d2
.ex_blink:
    move.w  #COL_WHITE, BG
    bsr     ExcDelay
    move.w  #COL_BLACK, BG
    bsr     ExcDelay
    subq.w  #1, d2
    bne.s   .ex_blink
    moveq   #6, d3                          ; pausa lunga fra un conteggio e il successivo
.ex_pause:
    bsr     ExcDelay
    subq.w  #1, d3
    bne.s   .ex_pause
    bra.s   .ex_cycle

ExcDelay:
    move.l  #$18000, d0
.exd:
    subq.l  #1, d0
    bne.s   .exd
    rts

;---------------------------------------------------------------------------
; ExcReport (S53): la cornice dell'eccezione come riga su COM8,
;   EXC v=vv pc=pppppppp sr=ssss ad=aaaaaaaa sp=ssssssss
; v = numero del vettore (= i lampi; 14 = "tutti gli altri"), ad = indirizzo
; d'accesso per Bus/Address Error (0 per gli altri). Senza GameDrive non fa
; niente. Qui non c'e' piu' fretta: la risposta di una riga precedente e quella
; di questa si aspettano con un tetto lungo (~secondi), perche' un micro
; lasciato a meta' transazione ignora reboot e upload via USB fino a un ciclo
; di accensione (gamedrive_usb.md, regola 3). I lampi restano il canale che
; non dipende dalla GD (regola 7).
;---------------------------------------------------------------------------
EXC_LINE        EQU     SPARE_LO+$40        ; dopo EXC_SAVE (36 byte)

ExcReport:
    tst.l   GD_BIOS
    beq     .er_done
    tst.w   GD_PEND
    beq.s   .er_free
    bsr     ExcReply
.er_free:
    lea     EXC_LINE, a1
    lea     ExcTxtV, a0
    bsr     ExcPut
    moveq   #0, d0
    move.w  EXC_BLINKS, d0
    moveq   #2, d2
    bsr     ExcHex
    moveq   #0, d3                          ; ad
    moveq   #0, d4
    move.w  EXC_BLINKS, d0
    cmp.w   #2, d0
    blo.s   .er_short
    cmp.w   #3, d0
    bhi.s   .er_short
    move.l  EXC_SAVE+6, d3                  ; cornice lunga: indirizzo d'accesso
    move.w  EXC_SAVE+12, d4                 ; SR
    move.l  EXC_SAVE+14, d0                 ; PC
    bra.s   .er_pc
.er_short:
    move.w  EXC_SAVE+4, d4                  ; cornice corta: SR, PC
    move.l  EXC_SAVE+6, d0
.er_pc:
    lea     ExcTxtPc, a0
    bsr     ExcPut
    moveq   #8, d2
    bsr     ExcHex
    lea     ExcTxtSr, a0
    bsr     ExcPut
    move.l  d4, d0
    moveq   #4, d2
    bsr     ExcHex
    lea     ExcTxtAd, a0
    bsr     ExcPut
    move.l  d3, d0
    moveq   #8, d2
    bsr     ExcHex
    lea     ExcTxtSp, a0
    bsr     ExcPut
    move.l  EXC_SAVE, d0
    moveq   #8, d2
    bsr     ExcHex
    clr.b   (a1)
    pea     EXC_LINE
    movea.l GD_SEND_FN, a2                  ; la copia in DRAM, mai quella in ROM
    jsr     (a2)
    addq.l  #4, sp
    tst.w   GD_PEND
    beq.s   .er_done
    bsr     ExcReply
.er_done:
    rts

ExcReply:                                   ; gd_reply della copia in DRAM, tetto lungo
    lea     GD_CODE, a2
    adda.l  #gd_reply-GdBlock, a2
    move.l  #4000000, d1
    jsr     (a2)
    rts

ExcPut:                                     ; a0 stringa C -> a1, a1 sul NUL
    move.b  (a0)+, (a1)+
    bne.s   ExcPut
    subq.l  #1, a1
    rts

ExcHex:                                     ; d0 valore, d2 cifre (1..8) -> a1
    move.l  d2, d1
    lsl.w   #2, d1
    ror.l   d1, d0                          ; la cifra alta in cima (ror 32 = niente)
    subq.w  #1, d2
    lea     ExcHexDig, a0
.eh_dig:
    rol.l   #4, d0
    move.w  d0, d1
    and.w   #$F, d1
    move.b  0(a0,d1.w), (a1)+
    dbra    d2, .eh_dig
    rts

ExcHexDig:  dc.b    "0123456789ABCDEF"
ExcTxtV:    dc.b    "EXC v=",0
ExcTxtPc:   dc.b    " pc=",0
ExcTxtSr:   dc.b    " sr=",0
ExcTxtAd:   dc.b    " ad=",0
ExcTxtSp:   dc.b    " sp=",0
    .even

; Il canale COM8 (S53): GdInstall, GdSendC, GdCallC. Codice 68000 puro, quindi
; sta prima dei kernel GPU/DSP, che aprono le loro sezioni con una .org.
    .long
GdBlock:
    .include "src/gd.s"
GdBlockEnd:
    .assert (GdBlockEnd-GdBlock) <= GD_CODE_SIZE

; Il riempitore dei poligoni sulla GPU TOM. Sta in fondo perche' contiene una
; sezione .gpu con la sua .org: quel che segue non e' piu' codice 68000.
;
; NON si include src/gpu_raster.s, che e' l'archivio S9..S19 (ritaglio a 4
; passate, hline, scala 2x). Il ritaglio servira' a M3d e la scala e' morta col
; ritorno al rapporto 2:1 di S19; copiarli tutti vorrebbe dire riempire i 4 KB
; di RAM della GPU di codice che nessuno chiama.
    .include "src/gpu_body.s"

; Il secondo kernel della GPU: lo stadio dell'intensita' (S40). Va DOPO
; gpu_body.s e non prima, cosi' i blocchi restano nell'ordine in cui stanno
; nella RAM locale (la mappa e' GPU_SRAM_* in tools/memory_map.py) anche nel
; listato.
    .include "src/gpu_shade.s"

; Il terzo kernel: il front-end dei corpi (S45), fase V (posa dei vertici +
; proiezione + rettangolo) e fase C (faccia nascosta + chiave del pittore).
    .include "src/gpu_front.s"

; L'audio su Jerry. Stessa ragione della GPU per stare in fondo: contiene una
; sezione .dsp con la sua .org. E' indipendente dal riempitore - i due
; processori non si toccano - ma l'ordine conta per l'assemblatore.
    .include "src/dsp_audio.s"

    .end










