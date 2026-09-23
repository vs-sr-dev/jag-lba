# JagLBA V6 — progetto dello scheletro

Documento di architettura scritto in S24 (2026-07-29), dopo la diagnosi che ha
chiuso la V4/V5. Sostituisce l'impianto "scene viewer" con uno scheletro
progettato attorno all'engine LBA1 derivato.

---

## 1. Perche' rifare lo scheletro

Tre fatti accertati in S24, non opinioni:

1. **Il motore dati/rendering e' sano.** I marcatori di boot hanno certificato
   una per una: decodifica griglia, decodifica RLE dei brick, rasterizzatore
   GPU TOM (clip + scanline + gouraud + sfere), proiezione isometrica, camera,
   blit. Tutta questa roba funziona e va **conservata**.
2. **Lo strato di plumbing e' rotto e non diagnosticabile a costo ragionevole.**
   Address Error da fetch a indirizzo dispari entrando in `OPSetup`, con stack
   sano. E' esattamente la parte che una ricostruzione riscrive comunque.
3. **Non esisteva un punto di verifica del boot.** Il progetto e' rimasto rotto
   tre mesi senza che nessuno lo notasse. Questo e' un difetto di
   *architettura*, non di codice, ed e' il primo da correggere.

A cui si aggiunge il vincolo dimostrato: con la mappa attuale restano **8 KB
liberi** in DRAM, insufficienti per attori, animazioni e stato di gioco.

---

## 2. Calibrazione hardware (Jaguar vs 32X)

Va messa a fuoco perche' guida le scelte, e un punto dell'intuizione iniziale
va corretto.

| | Jaguar | 32X |
|---|---|---|
| CPU che ospita la logica | **68000 @ 13,3 MHz**, CISC, no cache, **ultimo in priorita' sul bus** | **SH-2 @ 23 MHz**, RISC, cache 4 KB |
| Coprocessori | Tom (GPU RISC 26,6 MHz, 4 KB locali) + Jerry (DSP 26,6 MHz, 8 KB locali) + Blitter + Object Processor | secondo SH-2 |
| Bus verso DRAM | **64 bit** | SDRAM 16 bit |
| RAM | 2 MB | 256 KB |
| Risoluzione target | 640x480i | 320x224 |

**Correzione importante:** il 68000 del Jaguar e' effettivamente ~1,7x quello
del Genesis, ma sul 32X la logica di gioco **non gira sul 68000** — gira sugli
SH-2. Come *ospite dell'engine* il 68000 del Jaguar e' quindi
**significativamente piu' debole** dell'SH-2: RISC contro CISC, 23 contro 13,3
MHz, con cache contro senza, e per giunta ultimo in priorita' sul bus.

Dove il Jaguar vince davvero e' nell'**aggregato**: banda a 64 bit, e soprattutto
composizione e riempimento in hardware (OP, Blitter, GPU) che sul 32X sono a
carico della CPU. Questo non e' un dettaglio: e' proprio la parte che costa piu'
caro in un gioco isometrico.

**Implicazione di progetto, non negoziabile:** sul 68000 va **solo la logica**
(interpreti LIFE/TRACK, attori, collisioni a griglia) — cioe' esattamente il
carico che girava su un 386. Ogni ciclo per-pixel o per-vertice va su
GPU/Blitter/OP. Nessun loop su pixel sul 68000, mai.

**Sul costo del 640x480i:** sono 4x i pixel del 32X, ma **non 4x il costo per
frame**. Il rendering di LBA e' fondo statico (disegnato una volta per stanza) +
attori in movimento. Il costo a regime e' proporzionale all'**area degli attori**,
non a quella dello schermo. Il fattore 4 pesa sul caricamento scena e sui blit
a pieno schermo, non sul regime. Questa e' la ragione per cui l'architettura a
sprite via OP (scoperta in S23) e' quella giusta e va generalizzata.

---

## 3. Decisioni architetturali

### 3.1 Il C dell'engine gira dal cart, non da DRAM

Il 68000 esegue dal cart nativamente (il codice asm attuale sta a `$802000`).
Quindi il blob C **non si copia in DRAM**: si esegue in place, e in DRAM
finiscono solo `.data` e `.bss`. Questo elimina alla radice il problema dei
soli 8 KB liberi che aveva bloccato il PoC S24.

Linker script con split ROM/RAM: `.text`+`.rodata` a un indirizzo fisso del
cart, `.data`/`.bss` in DRAM, e uno startup che copia `.data` e azzera `.bss`.

### 3.2 Il layout del cart lo decide Python, non RMAC

Oggi RMAC concatena tutto con `.incbin`, quindi gli indirizzi dipendono dalla
dimensione del codice — ed e' per questo che il blob C non poteva avere un
indirizzo fisso. Si inverte: **il packer Python possiede il layout** e piazza a
indirizzi fissi il blob asm/HAL, il blob C dell'engine e i dati. RMAC produce
solo il proprio pezzo.

Cart **V6 con bank switching dall'inizio**: ANIM.HQR sta arrivando e i 6 MB sono
gia' pieni.

### 3.3 I dati vengono dal cart, non bakati in array C

Sul 32X `engine_src` contiene ~31.000 righe di dati compilati (grid, anims,
body, testi). Su Jaguar **non servono**: abbiamo gia' `cart_pack_all.py` e
`HQR_Get` puo' restituire direttamente puntatori nel cart.

Il codice logico vero da portare e' circa **5.000 righe**:
`object.c` (1557), `p_anim.c` (639), `gerelife.c` (606), `geretrak.c` (413),
`hqr_scene.c` (408), `p_func.c` (272), `globals.c` (192), `diskfunc.c` (191),
`message.c` (191), `fiche.c` (189), `grille_a.c` (176), `mainloop.c` (107),
piu' header. `p_sintab.c` (1028) e' tabella, va in ROM.

### 3.4 Un solo framebuffer di fondo + attori come oggetti OP

E' la semplificazione piu' importante, ed e' abilitata proprio dalla scoperta
di S23. Se gli attori sono sprite composti dall'Object Processor **sopra** il
fondo, allora il fondo non va mai ridisegnato per frame — e **il doppio buffer
non serve piu'**. Aggiornare la posizione di un attore e' scrivere qualche long
nella OP list, operazione atomica se fatta in VBLANK.

Questo libera ~757 KB (via `BG_PRISTINE` e `BG_B`) e cancella tutta la
complessita' di `PENDING_SWAP`, sync dei due buffer, e le routine `Sync*` (che
tra l'altro erano gia' dead code). Elimina anche la contesa Blitter/OP a
regime, perche' a regime il Blitter non tocca il fondo.

### 3.5 Mappa DRAM: un solo file, un solo padrone

Le due collisioni di S24 (blob dentro la griglia, `VBL_TICK` sopra
`GPU_ARG_OUT_COUNT`) sono nate perche' la mappa e' implicita e sparsa su due
sorgenti. **Un unico `include/dram_map.inc`**, incluso da tutti, con ogni
regione dichiarata con base e dimensione, e nessun indirizzo hardcoded altrove.

Budget indicativo (2 MB):

| Regione | Dimensione |
|---|---|
| Vettori, OP list, scratch | ~64 KB |
| Fondo 640x592 8bpp (singolo) | 379 KB |
| Buffer brick decodificati (caricamento scena) | 576 KB |
| Griglia cubo | 200 KB |
| Bitmap sprite attori (~20 x 4 KB) | 80 KB |
| Arene engine (`.data`/`.bss`, copie RAM dei body per anim, `ListObjet`) | ~200 KB |
| Stack | 64 KB |
| **Totale** | **~1,56 MB** — restano ~450 KB di margine |

### 3.6 Il boot deve dire di essere arrivato in fondo

Requisito di architettura. Alla fine dell'init: segnale di vita **inequivocabile
e non dipendente dai colori** (lezione CRY16 di S24 — vedi
`feedback_debug_border_milestones`). Un indicatore che pulsa a frame rate, o un
contatore visibile: qualcosa che se si ferma si nota subito.

In piu': tabella vettori **completamente inizializzata** con un handler di
eccezione che segnala invece di saltare nel vuoto. In V4 solo `$100` e i sette
autovettori erano scritti; tutto il resto era spazzatura da cold boot, ed e'
per questo che un Address Error si e' presentato come schermo nero muto.

### 3.7 Cosa si salva e cosa si riscrive

**Si salva (diventa libreria HAL in RMAC):** rasterizzatore GPU TOM completo,
decodifica brick RLE e griglia, proiezione isometrica e camera, interlace 480i,
architettura sprite OP, tutta la pipeline Python di estrazione e packing,
strobe joypad.

**Si riscrive:** boot e init, mappa DRAM, `OPSetup` e gestione OP list,
`ViHandler`, il main loop (passa al C dell'engine), il layout del cart.

**Si importa dal 32X:** `engine_src` logico (~5.000 righe, gia' derivato **e
gia' debuggato** — le mine di S17 non vanno riscoperte) e la forma del
contratto `hal.h` (322 righe, gia' scritto).

---

## 4. Milestone

Ognuna con un criterio di verifica esplicito. Nessuna si dichiara chiusa senza.

| # | Obiettivo | Verifica |
|---|---|---|
| **M0** | Scheletro: boot RMAC + blob C dal cart in place + `dram_map.inc` + vettori completi + segnale di vita | Indicatore che pulsa; una eccezione forzata a mano produce un segnale, non nero |
| **M1** | HAL core: `TimerRef` 50 Hz da VBL, input joypad, `HQR_Get` sul cart, arene `Malloc`, `TheEnd` | Il C legge un record HQR dal cart e ne mostra un valore noto — **chiuso S28** |
| **M2** | Fondo: `LoadScene` reale + **bonifica placeholder** (zone ChangeCube vere da SCENE.HQR, camera init dalla regola del gioco) | Scena 0 identica al PC DOS; transizione verso la stanza *giusta*, non N+1 — **CHIUSO S30**, 13 sonde su 13 su entrambi i passi |
| **M3** | Attori come sprite OP: `AffScene` come iteratore su `ListObjet`, body renderizzato nella bitmap dello sprite via GPU | Twinsen + il prigioniero visibili insieme, posizionati dai dati di scena |
| **M4** | Animazioni: estrazione ANIM.HQR + `p_anim.c` | Twinsen esegue un'animazione di idle reale |
| **M5** | Script: `object.c` + `gerelife.c` + `geretrak.c` | Un NPC si muove e parla secondo il suo script, non secondo un trigger hardcoded |
| **M6** | Eroe: `DoDir` + `grille_a.c` | Twinsen cammina e i muri lo fermano |
| **M7** | Audio: soft-synth sul DSP Jerry | Musica della Citadel |

M0-M2 sono il vero contenuto di "scheletro". Da M3 in poi e' porting che segue
il tracciato del 32X.

### Stato M0: CHIUSO (criteri in S25, strato video in S27)

I due criteri di M0 erano soddisfatti in S25; il difetto video non era un criterio
di M0 ma era annotato **"da chiudere prima di M1"**, ed e' chiuso in S27 — su
BigPEmu *e* su Jaguar reale (vedi sotto).

Costruito e verificato su BigPEmu:

- `tools/memory_map.py` e' l'unico padrone della mappa e genera
  `include/dram_map.inc`, `include/cart_map.inc`, `engine_c/jag_engine.ld` e
  `engine_c/mem_map.h`. Buchi e sovrapposizioni sono errori di build.
- `src/main_v6.s` inizializza **tutta** la tabella vettori; ogni eccezione
  lampeggia il proprio numero di vettore invece di sparire nel nero.
- Il blob C gira **in place dalla cartuccia** a `$842000`, con `.data` copiata
  e `.bss` azzerata in DRAM. Tre sonde di sezione fanno fallire il boot in modo
  parlante se il linker script e' sbagliato.
- Il layout della cartuccia lo decide `tools/cart_build.py` a offset fissi.
- **Entrambi i criteri soddisfatti**: il marcatore avanza di uno slot per tick,
  cioe' il C viene eseguito dal cart a ogni frame; e un'eccezione forzata a
  mano (Address Error, la stessa che uccise la V4) produce **3 lampi**, cioe'
  il numero del suo vettore, invece di uno schermo nero muto.

**Verificato anche su hardware (S27):** sia il segnale di vita sia il percorso di
**guasto**. Provate due eccezioni diverse con conteggi attesi diversi —
istruzione illegale = **4 lampi**, Address Error (quella che uccise la V4) = **3
lampi** — ed entrambe confermate sul Jaguar reale. Due test invece di uno perche'
un conteggio solo non distingue "il meccanismo legge il numero del vettore" da
"qualcosa lampeggia": se avessero dato lo stesso numero, il contatore sarebbe
rotto e non lo sapremmo.

**M0 e' quindi chiuso su entrambe le piattaforme**, criteri e strato video.

### Difetto video: CHIUSO in S27, su emulatore E su hardware vero

Il difetto che dalla S23 alla S26 era descritto come "un campo su due non
disegnato" e' risolto. La diagnosi che stava scritta qui (mancato sincronismo fra
la parita' scritta in `VI` e il campo disegnato) era **sbagliata**, come le altre
tre che l'avevano preceduta.

**La causa vera, misurata:** su BigPEmu non esiste un interrupt per campo — 30,0
al secondo contro 60 campi/s, cioe' uno per **quadro**, con istogramma dei delta
`0:730 1:499 2:0 3:0 4+:0` (ritmo regolare, nessun interrupt perso). Fra il campo
A e il campo B non c'e' quindi **nessun istante** in cui ripristinare l'oggetto, e
nessuno schema che scambi `DATA` nel gestore puo' funzionare: ne' `VI` alternato
ne' `VI` fisso. Era il motivo per cui davano lo stesso identico fotogramma.

**Soluzione su BigPEmu:** non serve un interrupt per sapere quando una passata
dell'OP e' finita, perche' **l'OP lo dichiara azzerando `HEIGHT`**. Il gestore
arma il campo A, sorveglia quel campo e arma il campo B appena va a zero. La
soglia di rilevamento taglia il disallineamento con relazione **1:1** in righe di
campo (tarata su quattro punti; `HEIGHT < 12` lo annulla).

**Su hardware vero valgono premesse opposte**, e la ricetta di Zerosquare (parita'
del campo dal bit 11 di `VC`, `VI` riarmato per il campo successivo) **funziona** —
era il percorso che su BigPEmu era impossibile validare, perche' la' il bit 11 non
alterna (misurato basso su 344 interrupt su 344).

**Validazione su Jaguar reale** (RGB SCART -> RetroTink 4K), tre misure
indipendenti:
- RetroTink: `1716x525i (1440x480)`, 27.0024 MHz, H = 15.74 kHz, V = 59.95 Hz,
  modo rilevato `CP 480i` — 480i NTSC **canonico**, non un timing tollerato per
  caso;
- geometria a schermo: marcatore di riga 300 al 62,5% dell'altezza (su un display
  da 240 righe non esisterebbe);
- rampa per riga: 3,75 bande = 480/128.

Nessuna striscia verde: entrambi i campi disegnati.

Corretto anche un **off-by-one del campo pari** presente **solo su hardware** (il
campo pari mostrava la riga `L-2` alla riga di output `L`), isolato con due blocchi
di parita' di bordo opposta e chiuso con un bias di 2 righe. Ha eliminato anche
l'oscillazione verticale del marcatore.

**Un solo interruttore, `TARGET_HW`**, deriva i sei valori delle due
configurazioni: mescolarle riproduce in silenzio il difetto di S23-S26.

|  | `TARGET_HW=0` (BigPEmu) | `TARGET_HW=1` (Jaguar reale) |
|---|---|---|
| `FIELD_SRC` | 0 (contatore software) | 1 (bit 11 di `VC`) |
| `VI_ALTERNATE` | 0 | 1 |
| `SECOND_FIELD_POLL` | 1 | 0 |
| `POLL_THRESH` | 3 | irrilevante |
| `BG_RELEASE` | 1 | 0 |
| `FIELD_A_ROW_BIAS` | 0 | 2 |

Vicoli ciechi da non ripercorrere, con la ragione per cui lo sono:
- **due oggetti con `YPOS` di parita' opposta**: `YPOS` e' una soglia (`>=`), non
  un confronto di uguaglianza, quindi entrambi sono attivi in entrambi i campi — e
  comunque il campo A consuma `HEIGHT` di tutti e due;
- **tutti e 18 i registri di timing**: provati, nessun cambiamento (tenuti perche'
  sono igiene, e i valori di Zerosquare sono verificati con analizzatore logico);
- **`DWIDTH` a 80**: fa sparire l'artefatto solo perche' dimezza la verticale a
  240 linee. Aggiramento travestito da correzione;
- **`RELEASE = 1` come cura della latenza**: non lo e' (contribuisce ~1,5 righe,
  non le 8 cercate).

Cose da riverificare in seguito: `POLL_THRESH` e `BG_RELEASE = 1` sono tarature
**specifiche di BigPEmu**; quando il Blitter torna in M2/M3, `BG_RELEASE = 1` va
riverificato contro la contesa che in S22 produceva spazzatura sulle scanline.

### Stato M1: CHIUSO (S28)

Tutti e cinque i servizi in piedi, criterio soddisfatto al primo avvio.

**Il criterio, e perche' e' stato irrigidito.** Preso alla lettera ("il C legge
un record HQR dal cart e ne mostra un valore noto") si accontenterebbe di cifre
a schermo, e cifre a schermo non provano niente: senza un riferimento non si
distingue una lettura giusta da una plausibile. E' la stessa trappola delle
diagnosi di S23-S26, dove uno strumento senza taratura ha prodotto quattro
diagnosi sbagliate di fila. Quindi il confronto **non lo fa l'occhio**:
`tools/hqr_pack.py` calcola i valori attesi quando impacchetta i dati e li
scrive in `engine_c/hqr_expect.h`; il blob C rilegge la cartuccia, confronta e
mostra una maschera con un bit per sonda. `riga 0 = 000000FF` = otto sonde su
otto. Le righe 1..8 mostrano comunque i valori **letti**, cosi' una sonda caduta
si diagnostica dallo stesso screenshot invece di richiedere un altro giro.

Misurato su BigPEmu:

| | letto | atteso |
|---|---|---|
| maschera delle sonde | `000000FF` | `000000FF` |
| magic del contenitore | `4A485152` | `'JHQR'` |
| versione \| archivi | `0001000B` | 11 archivi |
| dimensione | `0046B9B8` | 4.635.064 byte |
| somma di controllo | `1D543102` | idem |
| `HQR_Size(SCENE,0)` | `000018E5` | 6373 byte |
| `SCENE[0]` byte 4..7 | `00008003` | idem |
| `HQR_Size(BODY,0)` | `00001A04` | 6660 byte |
| `BODY[0]` byte 0..3 | `0300EFFE` | idem |
| interrupt/s \| `NbFramePerSecond` | `001E001E` | 30 \| 30 |
| arene usate | `01000200` | `$100` \| `$200` |

Le due righe vive (contatore degli interrupt e `TimerRef`) avanzano con rapporto
5 a 3, cioe' 50/30: e' il rapporto atteso, e si verifica **senza cronometro**
confrontando due catture qualsiasi. Tutte le altre righe stanno ferme, che e'
altrettanto informativo: una sonda che si muovesse sarebbe una sonda che legge
la cosa sbagliata.

**Cosa e' stato costruito.**

- **`tools/hqr_pack.py`** decomprime gli .HQR originali e li impacchetta in un
  contenitore "JHQR" a due livelli di indice, tutto big endian (nativo 68000) e
  con offset **relativi alla base** — cosi' il contenitore si puo' spostare
  senza rifarlo, che e' quel che servira' al bank switching di M2. Sette
  archivi, 4,42 MB, l'84% di `ROM_DATA`.
- **`HQR_Get` torna un puntatore dentro la cartuccia**: nessuna copia in DRAM,
  nessuna decompressione a runtime. E' la decisione 3.3, e da sola cancella il
  problema che aveva bloccato il PoC di S24 (i dati non entravano negli 8 KB di
  DRAM che restavano).
- **`TimerRef` a 50 Hz derivato, non assunto.** Il porting 32X poteva scrivere
  `vblank * 5 / 6` perche' li' il vblank e' 60 Hz e basta. Qui gli interrupt
  arrivano a **quattro** cadenze (60/50 su hardware con VI alternato, 30/25 su
  BigPEmu che ne da' uno per quadro), NTSC/PAL si sa solo a runtime e la riga
  della tabella la sceglie `TARGET_HW`. Il lato RMAC risolve entrambe e pubblica
  `TICK_HZ`; il C consuma la **differenza** dei tick con un accumulatore —
  niente divisione, niente sfondamento a 32 bit, e un quadro perso non fa
  perdere tempo di gioco.
- **Due arene** (persistente dal basso, per-cubo dall'alto, `HQM_Free_All` che
  azzera la seconda) collaudate scrivendo e rileggendo: un'arena e' l'unica
  risorsa del progetto che si esaurisce in silenzio, quindi l'esaurimento e' un
  `TheEnd`, non un `NULL` a un chiamante che non lo guarderebbe.
- **Font esadecimale 8x8 disegnato a 2x** (`engine_c/hal_debug.c`). Sostituisce
  i 32 quadratini per valore di M0, che andavano allineati a mano contro una
  riga di taratura e dove un disallineamento si scambiava per un dato. Il 2x
  **verticale** non e' estetica: in 480i un glifo alto 8 righe avrebbe metа'
  delle righe in un campo e metа' nell'altro, cioe' sfarfallerebbe a 30 Hz.
  Raddoppiando, ogni riga finisce in entrambi i campi e il glifo sta fermo.
  Costo 512 byte di ROM, e da M2 in poi e' lo strumento con cui si legge tutto.
- **`HAL_VARS` ha adesso un padrone solo.** In M0 i suoi campi erano scritti a
  mano in `src/main_v6.s` **e** in `engine_main.c`: ha funzionato finche' non si
  toccavano, ma e' la stessa forma di guasto che in S24 ha messo `VBL_TICK`
  sopra `GPU_ARG_OUT_COUNT`. Ora li dichiara `tools/memory_map.py`, che li
  riemette da entrambi i lati e rifiuta sovrapposizioni e disallineamenti.

**Input: come si verifica, e cosa ha dato.** Le righe 12, 13 e 14 **accumulano**
i bit mai visti premuti e non tornano piu' a zero, quindi si prova il controller
a mano e si guarda lo schermo dopo, senza far coincidere la pressione con lo
scatto di uno screenshot. Tre righe e non una perche' provano tre strati che si
rompono separatamente:

| riga | strato | letto |
|---|---|---|
| 12 | matrice di strobe, tutte e 4 le righe | `PadSeen = 01FF` = tutti e nove i pulsanti |
| 13 | mappatura su `MyJoy`/`MyFire` | `0F` \| `27` = `J_*` tutti e quattro, `F_SPACE\|F_RETURN\|F_CTRL\|F_SHIFT` |
| 14 | tastierino numerico | `PadKeysSeen = 03FF` = tutte e dieci le cifre 0-9 |

`MyFireSeen = 27` non e' un numero qualsiasi: e' esattamente
`F_SPACE|F_RETURN|F_CTRL|F_SHIFT` = 1+2+4+32, cioe' i quattro bit su cui A,
Option, B e C sono mappati e nessun altro. La mappatura e' verificata bit per
bit, non "sembra rispondere".

`*` e `#` non sono stati premuti, quindi restano gli unici due tasti del
controller mai esercitati.

Gli accumulatori hanno anche un secondo uso: **provano l'assenza**. Venti secondi
di funzionamento senza toccare niente lasciano tutte e tre le righe a zero, cioe'
in ~600 letture del pad non e' entrato un solo bit spurio. Un valore istantaneo
non lo potrebbe dire: mostrerebbe zero anche se un quadro su cento fosse
sbagliato.

La lettura usa un **accesso long** a `$F14000` (JOYSTICK nella word alta,
JOYBUTS nella bassa), che e' la sequenza verificata su Jaguar reale
(`move.l JOYSTICK, d0`). Due letture word separate funzionano anch'esse, ma
prendono i due registri in istanti diversi rispetto allo strobe: non c'e' ragione
di discostarsi da una sequenza verificata sul ferro.

Vicolo cieco annotato: automatizzare la pressione con `keybd_event` sulle frecce
(`emu_shot.ps1 -Hold`) **non verifica l'input** — le frecce non sono la
mappatura predefinita di BigPEmu, quindi il gioco non vede niente e lo schermo
mostra zero, che si scambia per "il pad non si legge".

Aperto e rinviato: la mappatura pulsante -> tasto PC (`A`->`F_SPACE`,
`B`->`F_CTRL`, `C`->`F_SHIFT`, `Option`->`F_RETURN`) e' **provvisoria**. Va
confrontata con quel che `DoDir` e i suoi chiamanti testano davvero in
`object.c`, e quello succede a M6 quando l'eroe comincia a camminare.

**Ordine dei byte dei payload: decisione rinviata a M2, consapevolmente.** I
payload restano verbatim little endian come li scriveva il Watcom del 1994. Il
contenitore e' un contenitore: se convertisse, servirebbe un parser per ogni
formato di risorsa e un errore di conversione diventerebbe indistinguibile da un
errore del motore. A M2 si scegliera' fra accessori `le16`/`le32` lato C (costo a
ogni lettura, zero rischio) e swap offline per-formato (costo zero a runtime,
serve un parser per formato); il porting 32X ha usato lo swap offline per SCENE
e TEXT, quindi il precedente esiste per entrambe le strade.

### Stato M2: META' FATTA (S28) — il percorso dati, non ancora il fondo

Diviso in due meta' che si verificano in modo diverso: leggere la scena vera, e
disegnarla. La prima e' chiusa, la seconda no.

**Fatto: il percorso dati, verificato al primo avvio.** Il C percorre la struct
SCENE vera dalla cartuccia e la colonna destra della diagnostica da' 8 sonde su
8: eroe `(26880, 768, 27626)`, camera `(53, 54)`, 23 attori, 31 zone, 2
change-cube verso la scena 1, musica 18, e cammino arrivato in fondo esatto.

**Il formato non e' stato indovinato.** `tools/scene_parse.py` e
`engine_c/scene_walk.c` sono la trascrizione di `LoadScene()` da
`lba1-classic-community-main/SOURCES/DISKFUNC.C:43-241`, cioe' del codice che ha
**scritto** quei byte, piu' `T_ZONE` (COMMON.H, 12 WORD) e `T_TRACK` (DEFINES.H,
3 WORD). Gli offset sono una lettura, non un'ipotesi da confermare.

**Il controllo che rende credibile il cammino.** La struct e' a lunghezza
variabile: ogni attore porta due script preceduti dalla loro dimensione, quindi
un errore di UN byte sfasa tutto il resto. In coda restano solo i `T_TRACK`, che
nessuno salta, percio' i byte residui devono valere **esattamente**
`NbBrickTrack * 6`. Preteso su **tutte e 120 le scene: 120 su 120**. Un layout
sbagliato non lo supera per caso, e la stessa condizione e' la sonda 7 lato
Jaguar — se cade, gli altri sette valori non sono "un po' sbagliati", sono letti
dal posto sbagliato.

**Bonifica dei due placeholder, con una sorpresa.**

- *Transizioni:* le zone di **tipo 0** (`OBJECT.C:3436`, "change cube perso")
  danno la scena di destinazione in `Num` e la posizione d'ingresso in
  `Info0/1/2`. Sostituiscono la mappa ciclica `N -> N+1`. Nota: per la scena 0
  quella mappa **azzeccava per caso** (le sue due zone puntano entrambe alla
  scena 1), quindi non si poteva scoprire guardando la scena 0.
- *Camera:* il piano diceva "camera init dai 120 valori del CSV". **Sbagliato**,
  e per un off-by-one su tutte e 120 le scene. La regola vera del gioco
  (`OBJECT.C:577`, `PERSO.C:773`) e'
  `StartXCube = (PosObjX + DEMI_BRICK_XZ) / SIZE_BRICK_XZ`, cioe' un
  arrotondamento al **piu' vicino**; il CSV di S20 ha le celle **troncate**
  (`world >> 9`). Per la scena 0: cella `(52, 53)` ma camera `(53, 54)`. La
  regola batte la tabella.

**Chiusa la decisione rinviata da M1 sull'ordine dei byte: accessori a runtime**
(`engine_c/le.h`), e non per gusto. Le struct di LBA1 alternano campi da uno e
due byte, quindi le word cadono a offset **dispari**: `CubeStartX` segue il
singolo byte `CubeJingle`. Sul 386 non costava niente, sul 68000 e' un Address
Error. Il 68000 deve percio' leggerle byte per byte **comunque**, per
allineamento, e a quel punto comporle little endian costa **zero in piu'** —
sono gli stessi due accessi. Lo swap offline invece avrebbe richiesto un parser
per ogni formato **e non avrebbe risolto l'allineamento**. Il porting 32X e'
arrivato alla stessa conclusione per le stesse due ragioni (`diskfunc.c`, punti
2 e 3), su una CPU anch'essa big endian e intollerante ai disallineamenti.

Corollario che vale anche altrove: dove upstream fa `ListZone = (T_ZONE *)PtrSce`
i campi vanno **copiati decodificandoli**, non puntati, altrimenti ogni accesso
successivo eredita entrambi i problemi.

### Stato M2b (S29): il fondo — DISEGNATO, 12 sonde su 12 su BigPEmu

La seconda meta' di M2. **La scena 0 si vede**, ed e' verificata contro il
riferimento offline: riga 32 = `00000FFF`, cioe' tutte e dodici le sonde,
Fletcher compresi. Il framebuffer del Jaguar e' `build/bg_ref_scene000.png`
byte per byte.

Resta aperta la **durata del caricamento**, misurata e non piccola: vedi in
fondo a questa sezione.

**I dati del fondo entrano nel contenitore JHQR, e il formato parallelo muore.**
`LBA_GRI`, `LBA_BLL` e `LBA_BRK` sono archivi HQR come tutti gli altri: adesso
sono gli archivi 11, 12 e 13, con gli **indici originali** di LBA1. Con loro
sparisce `tools/cart_pack_all.py` e il suo formato "JAGLBA03", che aveva un
direttorio proprio, una rimappatura dei brick e i due placeholder inventati.
Tenere gli indici originali non e' pigrizia: fa si' che un numero letto sul
Jaguar sia confrontabile con lo stesso numero stampato dagli strumenti Python.

**Il sottoinsieme, e perche' viene prima del bank switching.** Con tutte e 134
le scene il contenitore chiede ~10,8 MB contro i 6 della finestra. Ma il fondo
della scena 0 usa 526 brick su 8715 = 250 KB, e in `ROM_DATA` ce n'erano 841
liberi: `hqr_pack.py --scenes 0` produce 4,73 MB, il 90%. Non e' rimandare un
problema, e' separarne due: se il fondo non si vedesse su una cartuccia
bankizzata appena scritta, non si saprebbe se il difetto sta nel renderizzatore
o nei banchi. Il bank switching sara' esattamente quel che trasforma
`--scenes 0` in `--scenes tutte`, e a quel punto avra' un riferimento
funzionante contro cui essere verificato.

**Niente RMAC nuovo.** Griglia, brick e composizione stanno tutti in
`engine_c/scene_bg.c`. La regola "nessun ciclo su pixel sul 68000" vale a
regime, e comunque il 68000 qui non tocca un pixel di schermo: decodifica RLE
(~460 KB di sorgenti che il Blitter poi usa 756 volte) e programma il Blitter,
che sono nove scritture long per cella. Programmare il Blitter non e' un ciclo
su pixel: il ciclo lo fa il Blitter.

**Correzione trovata misurando, non a schermo: la finestra di celle non
esisteva piu'.** V4 percorreva `camera +- (9,12,12,14)` celle. Il riferimento
Python dice che per la scena 0 le celle che producono pixel visibili arrivano a
`gz-15`: quella finestra ne perdeva **tre colonne, 2307 pixel**, e non ai bordi
ma dentro l'immagine, dove un buco si scambia per una tessera mancante. Non era
una taratura da correggere. La regione visibile in `(gx,gz)` e' un **rombo**,
perche' le coordinate di schermo sono `gx-gz` e `gx+gz`, e nessuna scatola
allineata agli assi lo contiene senza tagliare o sprecare
(`feedback_iso_bounds_pitfall`). Ritarare i quattro numeri li avrebbe fatti
tornare qui e cadere in un'altra stanza. Adesso si percorre tutto il cubo con un
rifiuto **per colonna** sulla X, che e' il taglio esatto: il riferimento
pretende che il risultato sia identico byte per byte a una forza bruta senza
alcun rifiuto, e lo e'.

**La tabella delle larghezze del Blitter e' derivata, non copiata.** V4 aveva 64
valori scritti a mano. La codifica del TRM Rev.8 e' `1.mm x 2^e`, cioe'
`larghezza = (4+m) << (e-2)` con campo `eeee mm`; controprova sui due esempi del
manuale (20 -> `17`, 640 -> `37`, e `37` e' proprio il WID che V4 usa per il
clear a schermo intero). La funzione riproduce la tabella di V4 **64 su 64**, e
la versione C e quella Python sono confrontate valore per valore.

**Come si verifica.** `tools/bg_ref.py` fa due mestieri: renderizza il fondo con
le stesse tre decodifiche e ne scrive un PNG, e ne calcola le attese in
`engine_c/bg_expect.h`. "Si vede la scena" non e' un criterio — si vede *una*
scena anche con la camera sbagliata di una cella, con una tessera su dieci
mancante, con l'ordine del pittore invertito. Le dodici sonde si dividono il
lavoro di dire **cosa** e' andato storto: riquadro diverso = immagine traslata;
riquadro uguale e conteggio diverso = tessere mancanti; tutto uguale e somme di
Fletcher diverse = pixel giusti nei posti sbagliati. Il tasto **C** toglie e
rimette la diagnostica, cosi' lo stesso avvio produce sia la maschera sia lo
screenshot pulito per il confronto a occhio.

Valori attesi della scena 0: camera `(53,54)`, `vx=347 vy=-1033`, 526 tessere,
447 KB di buffer di lavoro, 756 celle, 238.901 pixel non trasparenti,
Fletcher `86950838` / `C722F284`.

**Aperto: 24 pixel di framatura.** La camera dalla regola del gioco e' `(53,54)`;
la costante di centratura `CAM_CY = 191` e' stata tarata in S20 contro il PC DOS
**con la camera troncata `(52,53)`**. Le due scelte danno la stessa immagine
traslata di 24 righe, e una delle due assorbe l'off-by-one che S28 ha corretto.
Si decide con una schermata vera del DOS, non ragionandoci: `bg_ref.py --cam
52,53` produce l'altra variante per il confronto.

**Due difetti trovati al primo avvio, e li hanno trovati le sonde.** La
maschera e' uscita `000000FF` invece di `00000FFF`, cioe' 8 su 12, e le tre
sonde cadute hanno detto da sole di che si trattava:

- `NONZERO` **esattamente 256 in piu'** = 16x16 = il quadratino dell'indicatore
  di fase, disegnato dopo `render()` e prima di `measure()`. `SUM1` differiva di
  esattamente 64, che sono 64 long da `FFFFFFFF`: gli stessi pixel. Lezione
  generale: uno strumento che scrive nel framebuffer va spento **prima** di
  misurare il framebuffer.
- `CLUT` differiva di `FFFF-4210`, cioe' del solo indice 3: la CLUT a schermo
  era giusta, era la *somma* a essere calcolata prima del forzamento. Una somma
  che non descrive quel che c'e' in memoria e' peggio di nessuna somma.

Nessuno dei due si sarebbe visto guardando lo schermo, ed e' il motivo per cui
la maschera esiste.

**Il difetto vero, e la sua diagnosi.** Il primo avvio ha dato uno **schermo
verde uniforme** - il colore di BG, cioe' l'OP che non disegnava niente - pur
essendo la scena disegnata benissimo sotto. La causa e' la scoperta di S27
applicata a un caso nuovo: **l'OP consuma l'oggetto azzerandone `HEIGHT`**, ed e'
`ViHandler` a ripristinarlo a ogni interrupt
(`feedback_second_field_poll_height`). `engine_init` gira con gli interrupt
mascherati: finche' e' durata millisecondi non si e' visto niente, ma adesso
dura secondi, quindi dopo il primo campo il fondo spariva per tutto il
caricamento. **Il fondo si costruisce percio' dal primo giro di `MainLoop`, non
da `engine_init`** - e li' e' anche l'unico posto da cui la durata si puo'
misurare, contando gli interrupt.

**La durata, misurata per fase** (BigPEmu, 30 interrupt/s):

| fase | interrupt | secondi |
|---|---|---|
| `grid_decode` | 50 | 1,7 |
| `mark_bricks` | 71 | 2,4 |
| `decode_bricks` | **213** | **7,1** |
| `render` (756 blit) | 70 | 2,3 |
| `measure` (solo diagnostica) | 191 | 6,4 |
| **totale** | **595** | **19,8** |

Tolta `measure`, che nel gioco non esistera', restano **13,5 s per stanza**: non
spedibile. Il grosso e' l'espansione RLE, 458 KB scritti byte per byte dal
68000. `mark_bricks` e `render` percorrono ciascuno 102.400 celle, e sono
fondibili in una passata sola con una tabella dei blocchi BLL precalcolata.

### Hardware vero (S29): 12 sonde su 12, e la 3.1 resta in piedi

Provato su Jaguar reale con `TARGET_HW=1` (`build/jaglba_v6_M2b_HW.j64`), uscita
RGB SCART -> RetroTink 4K. **Maschera `00000FFF`**: il fondo sul ferro e'
identico bit per bit al riferimento calcolato in Python, Fletcher compresi. E'
anche la prima volta che nella pipeline 480i validata in S27 passa un'immagine
di gioco invece di rampe e marcatori di taratura.

Tempi, a parita' di secondi (hardware 60 interrupt/s contro i 30 di BigPEmu):

| fase | BigPEmu | hardware | rapporto |
|---|---|---|---|
| `grid_decode` | 50 -> 1,67 s | 120 -> 2,00 s | 1,20 |
| `mark_bricks` | 71 -> 2,37 s | 148 -> 2,47 s | 1,04 |
| `decode_bricks` | 213 -> 7,10 s | 529 -> 8,82 s | 1,24 |
| `render` (756 blit) | 70 -> 2,33 s | 110 -> **1,83 s** | **0,79** |
| `measure` (diagnostica) | 191 -> 6,37 s | 446 -> 7,43 s | 1,17 |
| **totale** | 595 -> 19,8 s | 1353 -> 22,6 s | 1,14 |

**Un'ipotesi smentita dalla misura, e va scritto.** Durante la sessione era
stato ipotizzato che il grosso del costo fosse il *fetch delle istruzioni dalla
cartuccia*, cioe' la decisione 3.1. Se fosse vero, l'hardware con 10 clock di
attesa - che BigPEmu ignora del tutto - dovrebbe essere molto piu' lento del
modello. E' piu' lento del **14%**. Le attese della cartuccia costano al massimo
un quinto del tempo, non un ordine di grandezza: **la 3.1 non e' il problema e
copiare il blob C in DRAM non e' la cura.** Il costo e' il lavoro in se', 458 KB
di RLE espansa un byte alla volta da un 68000.

**Dato collaterale che conferma l'impianto:** `render` e' il 21% piu' VELOCE sul
ferro che nel modello. E' l'unica fase dominata dal Blitter, e il Blitter vero
batte la sua emulazione — cioe' la regola "il 68000 non tocca i pixel" viene
premiata proprio dove conta.

**Dove attaccare** (tolto `measure`, che nel gioco non esistera': restano 907
tick = 15,1 s): `decode_bricks` **58%**, `mark_bricks`+`render` **28%**,
`grid_decode` **13%**. In ordine di leva: cicli interni dell'espansione RLE
(clamp del conteggio fuori dal ciclo, puntatore invece di indice) per un 2-3x;
espansione sulla **GPU Tom**, che e' il salto vero e ha gia' l'infrastruttura
dalla V4; fusione di `mark_bricks` e `render` in una passata sola con una
tabella dei blocchi BLL precalcolata, invece di una `LE32u` dalla ROM piu' una
moltiplicazione a tre fattori per cella.

Espandere i brick **offline** azzererebbe la fase ma li porta da 4,04 a ~8 MB, e
va a sbattere frontalmente contro la finestra da 6 MB.

**Velocita' della cartuccia: capitolo CHIUSO su tre lati.** Portare
`CART_CONFIG` da `$04` (32 bit / 10 clock) a `$1C` (32 bit / 5 clock):

- su **BigPEmu** non cambia un solo tick: quella configurazione la ignora;
- su **Jaguar reale** la cartuccia **non parte proprio** - schermo rosso al boot
  del BIOS, prima ancora di arrivare al nostro codice. Le due immagini
  differiscono in **esattamente 4 byte** (`$400-$403`), quindi l'attribuzione e'
  inequivocabile: non e' il codice, e' la temporizzazione. La cartuccia non
  regge accessi a quella velocita';
- e anche se li reggesse, il tetto era ~20%, contesa di bus compresa.

Ne segue che **la decisione 3.1 resta in piedi** (il C gira in place dalla
cartuccia) e che il lavoro sul caricamento va tutto sul RIDURRE IL LAVORO, non
sul velocizzare la memoria. Gli 8 e i 6 clock non sono stati provati: guadagno
atteso nell'ordine del 5% su un numero che l'ottimizzazione dell'RLE cambiera'
di 3-5x.

**Non fatto, e non va spacciato per fatto.** Il criterio di M2 ("scena 0
identica al PC DOS; transizione verso la stanza giusta") **non e' soddisfatto**:
il confronto contro il DOS aspetta una schermata vera (i 24 pixel qui sopra), le
transizioni restano lette e non usate, e il caricamento dura tredici secondi.

Quel che segue e' lo stato al termine di S28 e resta valido tranne dove il
paragrafo qui sopra lo supera.

- il **fondo** non e' disegnato: griglia, decodifica dei brick e blit non sono
  ancora agganciati (il codice esiste dalla V4 in `src/gpu_raster.s` e
  `tools/cart_pack_all.py`, va riportato sotto l'architettura nuova);
- il **bank switching** non c'e'. *(S30: misurato, e NON serve — vedi la
  sezione in fondo a questa milestone.)*
- le transizioni sono **lette, non usate**: nessuno cambia ancora scena;
- `scene_walk.c` e' una duplicazione **dichiarata** del cammino di
  `diskfunc.c`, con condizione di rimozione scritta: serve perche' le zone
  stanno dopo gli attori e M2 ne ha bisogno prima che il sistema degli attori
  esista. Quando a M3 arrivano `T_OBJET` e `ListObjet[]`, si importa
  `diskfunc.c` del porting 32X (decisione 3.7) e di questo file resta solo
  `Scene_Derive`.

**Resta aperto da S27, non e' una regressione di M1:** il disegno concorrente al
display. Il 68000 scrive nel framebuffer mentre l'OP lo sta leggendo, quindi le
cifre che cambiano da un quadro all'altro escono spezzate fra i due campi. Si
vede solo sulle righe vive (10 e 11) perche' `Dbg_Value` ridisegna soltanto quel
che cambia. E' contenuto e diagnostico adesso, ma va chiuso prima che M3 metta
attori in movimento.

Nota sul budget: `cart/scene_all.bin` della V4 (5,80 MB) non e' riusabile.
*(S30: e i 768 KB riservati a codice e blob sono la vera leva, non i dati —
vedi «Il bank switching: misurato, e NON serve» in fondo alla milestone.)*

---

### Stato M2 (S30): CHIUSO — tutti e tre i criteri, 13 sonde su 13

Il criterio di M2 chiedeva due cose, e la seconda ne conteneva una terza:
*«Scena 0 identica al PC DOS; transizione verso la stanza giusta, non N+1»*.

**1. Identica al PC DOS: chiusa dalla regola, non da una schermata.** I 24 px di
framatura lasciati aperti in S29 aspettavano un confronto a occhio con una
cattura del DOS. Non e' servita: la mappatura sta in `Map2Screen`
(`SOURCES/GRILLE_A.ASM:446`, identica in `GRILLE_A.C:209`) ed e' esatta.

```
XScreen = (xm - zm)*24            + 320-8-23-1   = ... + 288
YScreen = (xm + zm)*12 - ym*15    + 240-25       = ... + 215
```

Le costanti erano `CAM_CX = 323` e `CAM_CY = 191`, tarate a occhio in S20.

- **La Y si spiega per intero, ed e' la conferma piu' bella della sessione:**
  191 era *giusta* per la cella troncata `(52,53)`; con la cella che da' la
  regola del gioco, `(53,54)`, diventa `191 + 24 = 215`, cioe' esattamente il
  `240-25` di `Map2Screen`. I due errori si annullavano — ed e' il motivo per
  cui una taratura a occhio poteva sembrare perfetta pur poggiando su una
  camera sbagliata.
- **La X invece era sbagliata di 35 px** senza niente che la compensasse:
  partiva da «320 = mezzo schermo», che e' un'assunzione e non una misura. Con
  288 la stanza si centra, la parete di sinistra rientra e il pavimento non e'
  piu' tagliato.

`StartYCube` smette di essere `CAM_GY_FIXED = 4` scritto a mano e diventa la
regola di `OBJECT.C:578`, che ha un'**asimmetria che sembra un refuso e non lo
e'**: la Y somma un brick INTERO dove X e Z ne sommano mezzo.

**Aggiunto il rifiuto per tessera del gioco** (`AffBrickBlock`, `GRILLE.C:631`:
`XScreen >= -24 AND < 640 AND YScreen >= -38 AND < 480`) al posto del
`CELL_MARGIN = 128`, che era un margine di sicurezza inventato. Misurato: su
799 celle ne rifiuta 4 e cambia **zero pixel** dentro le 480 righe visibili —
i 1409 pixel di differenza stanno tutti sotto il bordo. Non e' un cambiamento
d'immagine, e' una regola al posto di un numero.

**2. La transizione: chiusa col GRILLETTO premuto a mano, non con la
destinazione.** Nel gioco vero quella change-cube sta in fondo alla stanza e ci
si arriva dopo i primi combattimenti, cioe' dopo M3, M5 e M6. Il tasto A la
attraversa. Quel che si scavalca e' il *modo* di entrarci: scena di
destinazione e posizione d'ingresso continuano a venire dalla zona di tipo 0
letta dalla cartuccia — se fossero hardcodate, il criterio non proverebbe piu'
niente.

**Il punto piu' facile da sbagliare, e la ragione per cui e' stato letto invece
che dedotto:** entrando da una porta la camera NON e' quella di partenza della
stanza di destinazione. `ChangeCube` (`OBJECT.C:518-537`) fa

```
NewPos = posEroe - zona.X0 + zona.Info0        (per asse)
ListObjet[0].PosObj = NewPos
...e SOLO DOPO Start[XYZ]Cube da quella posizione
```

cioe' conserva lo scostamento dell'eroe *dentro* la zona. Senza un eroe (arriva
a M3) si entra dall'angolo, `posEroe = zona.X0`, che da' esattamente
`Info0/1/2`. `Bg_EnterAt` lo applica **fra** `Scene_Walk` e `Scene_Derive`, che
e' lo stesso ordine dell'originale: applicarlo dopo darebbe la stanza giusta
inquadrata dal posto sbagliato.

**Verificato**: scena 1 (il cortile della Citadel con gli stendardi di
FunFrock) con camera `(37,13)`, 531 brick, 8290 celle risolte, 1190 disegnate,
Fletcher `5C482A3A`/`7FE34F42` — **13 sonde su 13**, identiche al riferimento
calcolato offline.

`engine_c/bg_expect.h` non e' piu' una manciata di define ma un **array di
passi**: passo 0 = la scena iniziale, passo 1 = la stanza in cui si entra. Una
maschera piena della stanza sbagliata e una maschera piena non sarebbero
distinguibili senza; la riga 29 mostra il passo accanto al risultato.

**3. Il caricamento: 404 → 218 tick, 1,85x.** Misurato sulla STESSA macchina
(BigPEmu) rilanciando la build di S29, non confrontando piattaforme diverse.

| stadio | S29 | S30 | |
|---|---|---|---|
| `grid_decode` | 50 | 31 | 1,6x |
| cubo → celle | 71 | 45 | 1,6x |
| `decode_bricks` (RLE) | 213 | 100 | 2,1x |
| `render` | 70 | 42 | 1,7x |
| **totale senza `measure`** | **404** | **218** | **1,85x** |

Tre interventi, tutti decisi da una misura fatta prima:

- **La lista compatta delle celle.** Il cubo ha 102400 celle di cui 8781
  nominano un brick: il 91% delle visite non produce niente, e fino a S29 quel
  91% si pagava *due volte*, in `mark_bricks` e in `render`. Adesso il cubo si
  percorre una volta e produce una lista in ordine di pittore di 6 byte per
  cella (`px`, `py` gia' calcolati **senza camera**, piu' l'indice del brick).
  `render` non tocca piu' il cubo ne' la BLL. E' anche quel che rendera'
  economico lo scatto di camera di M6, dove il fondo si ridisegna ma la griglia
  non cambia.
  Dimensionata su una misura di **tutte e 133 le griglie**: la piu' carica e'
  la scena 8 (il Tempio di Bu) con 9852 celle; `CELL_SLOTS = 12288` le tiene
  col 25% di margine, e `bg_ref.py` fa fallire la **build** di chi sfora invece
  di lasciarlo scoprire con un `TheEnd`.
- **La tabella BLL precalcolata.** Ogni cella pagava una `LE32u` dentro la
  cartuccia piu' due moltiplicazioni a 32 bit — che sul 68000 sono chiamate a
  `__mulsi3`, non istruzioni — per un valore che dipende solo dal blocco.
  Adesso si calcola una volta per scena, al massimo 255 volte.
- **I cicli interni dell'RLE a puntatore, e l'azzeramento al Blitter.** La
  guardia era per pixel ed e' diventata per *corsa*. I salti sono il 45,4% dei
  pixel (200.041 byte in 18.043 corse da 11 pixel medi) e su un buffer gia' a
  zero sono una somma di puntatore. **Il primo tentativo azzerava con un ciclo
  di long sul 68000: funzionava ma spostava il lavoro invece di toglierlo**
  (458 KB restano 458 KB, scritti dalla CPU piu' lenta della macchina). Col
  Blitter, `decode_bricks` scende ancora da 115 a 100 — cioe' l'azzeramento non
  era il collo di bottiglia, lo e' la *lettura* dell'RLE dalla cartuccia.

**La striscia verde e' chiusa**, e non nascondendola: `BG` va a nero quando
`Bg_Build` riesce, quindi verde = sto caricando o l'OP e' morto, nero = sto
girando.

### Su Jaguar reale (S30): 8,0 s misurati, e la transizione verificata sul ferro

`build/jaglba_v6_M2_HW.j64`. Il caricamento **senza `measure`**, che nel gioco
non ci sara':

| stadio | S29 hw | S30 hw |
|---|---|---|
| `grid_decode` | 120 | 72 |
| cubo -> celle | 148 | 111 |
| `decode_bricks` | 529 | 249 |
| `render` | 110 | 48 |
| **totale** | **907** = 15,1 s | **480** = **8,0 s** (1,89x) |

Dopo il tasto A: passo 1, cortile caricato in 499 tick = 8,3 s, e **riga 32 =
`00001FFF`**, cioe' tredici sonde su tredici *su hardware*. E' la prova piu'
forte delle due: il passo 1 fa tutto quel che fa il passo 0 piu' la
transizione, su una scena che riempie il buffer di lavoro al 92% (529 KB su
576) e disegna 1190 celle invece di 795. Primo cambio di stanza mai verificato
su un Jaguar vero.

**Taratura BigPEmu <-> hardware**, che rende predittive le misure future e non
solo comparative. I tick NON valgono uguale (30/s sull'emulatore, 60/s sul
ferro), quindi il confronto si fa in secondi:

| | BigPEmu | Jaguar |
|---|---|---|
| lavoro 68000 (grid + celle + RLE) | 5,9 s | 7,2 s |
| `render` (Blitter) | 1,40 s | **0,80 s** |

Sul 68000 il ferro e' piu' lento del ~20%; sul Blitter e' quasi il **doppio
piu' veloce** di come lo modella BigPEmu. Vale anche sui dati di S29 (0,79
contro 1,24), quindi non e' un artefatto della singola misura. Vedi
[[reference_bigpemu_vs_hw_timing]].

Controprova che lo strumento si comporta come deve: `measure` costa 568 tick
sul cortile contro ~464 sull'ospedale, ed e' esattamente il rapporto dei pixel
non trasparenti (316.444 contro 254.388: 1,24 contro 1,22).

**Cosa resta aperto dopo M2**

- **Il caricamento e' 8,0 s sul ferro**, misurato (vedi sotto). Il pezzo grosso
  e' l'espansione RLE, 4,15 s da sola, e la leva vera non e' piu' il C: e' la
  **GPU Tom**, con l'infrastruttura gia' pronta dalla V4. Cantiere a se'.
- **`measure()` costa quanto tutto il resto** (203 tick contro 218) ed e' pura
  strumentazione: nel gioco non ci sara'. E' la ragione per cui il caricamento
  *percepito* e' peggiore di quello vero.
- **Il bank switching: MISURATO E NON SERVE.** Vedi la sezione qui sotto.
- Il disegno concorrente al display, aperto da S27.

### Il bank switching (S30): misurato, e NON serve — la cartuccia le ha TUTTE

**Stato: fatto, non pianificato.** `build_v6.bat` impacchetta `--scenes tutte`:
5.901.532 byte su 6.021.120, e sul Jaguar le sonde tengono tutte (riga 0
`000000FF`, riga 32 `00001FFF`, riga 3 `005A0CDC` = il contenitore intero). Il
caricamento e' identico a quello della cartuccia con due scene: 120 scene in
ROM non costano niente, perche' si tocca solo quel che serve.

Lato C e' bastato un decompressore LZSS in `hal_hqr.c` (~50 righe) piu' una
cache di 64 voci nell'arena per-cubo, invalidata da `HQM_Free_All`.

**Verificato su Jaguar reale**, in entrambe le stanze. La maschera di M1
(`000000FF`) smette di essere una formalita': quelle otto sonde leggono
`SCENE[0]` e `BODY[0]` attraverso `HQR_Get`, quindi sono la prova del
decompressore. Costo sul ferro: la costruzione della stanza e' **invariata**
(480 -> 481 tick sull'ospedale, 499 -> 500 sulla cittadella) perche' la
decompressione cade fuori dagli stadi cronometrati; il conto sta nel totale, ~40
tick = **circa mezzo secondo per stanza**.

Dato per obbligatorio dal M0 («i 6 MB sono stretti», 3.2). E' stato misurato
invece che assunto, e il risultato ribalta la decisione: **il gioco completo sta
in 6 MB senza mapper**. `tools/hqr_pack.py --scenes tutte --compress` non lo
stima, lo costruisce: dispone i byte per davvero e scrive
`cart/data_v6_compresso.bin`.

**Da 11061 KB a 5763 KB, in tre mosse — tutte regole, nessun elenco a mano.**

*1. Il gonfiore era il contenitore, non i dati.* JHQR ha sempre depositato i
payload DECOMPRESSI, perche' `HQR_Get` torna un puntatore dentro la cartuccia e
il dato si usa in place. Misurato per archivio, il costo non e' distribuito:

| | compresso | decompresso | gonfia |
|---|---|---|---|
| BRICK | 3883 KB | 4138 KB | **1,07x** |
| RESS | 932 KB | 2872 KB | 3,08x |
| GRID | 587 KB | 1645 KB | 2,80x |
| BLL | 54 KB | 771 KB | 14,16x |

Da qui la politica **per archivio** e non globale (`STORE` nel packer). Per
'lzss' si ricopiano i byte originali di LBA1 **cosi' come sono**, intestazione
da 10 byte compresa: non serve un compressore e il dato e' identico bit per bit
a quel che il gioco ha spedito.

*2. La deduplica, 442 KB, e l'ha rivelata un'incoerenza.* `LBA_BLL.HQR` e' 54 KB
su disco ma la somma delle sue 120 voci fa 493 KB: le scene **condividono** le
librerie di blocchi, e gli offset dell'HQR lo dicevano puntando allo stesso
posto. Il contenitore le ricopiava una per una. Deduplicare e' gratis e
**invisibile al Jaguar**: due slot puntano allo stesso payload, e `HQR_Get`
torna due puntatori uguali che sono entrambi validi. Nessuna riga di C.

*3. La potatura a regola.* RESS ha nove voci da **esattamente** 640x480 byte —
le schermate intere (intro, menu, holomap, game over): 727 KB su 931, e nessuna
serve per stare dentro una stanza. Si riconoscono dalla dimensione, che e' una
regola verificabile e non una lista da tenere aggiornata. Vanno rimesse per
l'intro, ma non in questa forma. Ed elencare le scene invece di dire "tieni
tutto" pota da se' i 247 brick che nessuna scena nomina e i 14 slot GRID/BLL di
troppo.

**Il verdetto, e il knob che resta**

| BRICK | contenitore | codice+blob 512 KB | 384 KB | 256 KB |
|---|---|---|---|---|
| non compresso | 5,63 MB | -140 KB | -12 KB | **+116 KB** |
| compresso | 5,35 MB | **+145 KB** | +273 KB | +401 KB |

Si tiene **BRICK non compresso**: guadagnerebbe 285 KB ma aggiungerebbe un LZSS
da ~230 KB per stanza sul percorso di caricamento, cioe' pagherebbe proprio gli
8,0 s appena guadagnati. Resta come **riserva documentata**, da tirare se
l'audio chiede spazio.

Il numero da tenere d'occhio non e' piu' `ROM_DATA` ma **codice+blob**: oggi
riservano 768 KB e ne usano 11, e sono loro a decidere se i dati ci stanno. A
384 KB si sfora di 12 KB.

**Cosa questo prova e cosa no.** Prova che i byte, disposti davvero, ci stanno.
NON prova che il 68000 li sappia leggere: serve un decompressore LZSS lato C e
la decompressione per stanza dentro l'arena del cubo (che e' esattamente quel
che l'arena e'; il set decompresso per stanza e' di pochi KB).

**E l'audio non ci sta.** Misurato nello stesso giro, perche' era l'incognita
rimasta:

| | |
|---|---|
| `SAMPLES.HQR` (effetti) | 230 voci, **2371 KB** compressi, 3483 decompressi |
| `Vox/` (parlato) | 99 MB, tutte le lingue |
| musica | solo mp3 (audio CD GOG); gli XMIDI stanno nel floppy |

Il parlato e' fuori discussione e non serve: LBA1 ha i sottotitoli. Ma i soli
effetti sonori chiedono 2371 KB contro i 116 di margine. Quindi il quadro e'
netto: **senza audio il gioco completo entra in 6 MB; con gli effetti interi
servono ~8 MB.**

Tre vie, nessuna da decidere adesso perche' l'audio e' M7 e non tocca niente da
M3 a M6: rifare i campioni per il Jaguar (il DSP suona PCM 8 bit diretto, e un
ADPCM 4:1 porterebbe 3483 KB a ~870 — che comunque non basta), tenerne un
sottoinsieme, oppure il mapper. Si costruisce sulla base a 6 MB senza mapper,
che e' provata per tutto il resto, e la cartuccia la decide l'audio a M7 con il
fabbisogno gia' in mano invece che scoperto allora.

**Se il mapper servisse, la granularita' e' l'ISOLA.** Misurato: partizionare i
brick per isola costerebbe il **114% in piu'** (8054 KB contro 3757), perche'
sono condivisi pesantemente — quindi come modo per far entrare i dati le isole
peggiorano le cose. Ma l'isola piu' affamata vuole solo **1311 KB** di brick
(isola 0, 18 scene, 3036 brick), quindi un banco da 2 MB ne copre una
qualsiasi, e il cambio cadrebbe ai passaggi in barca: raro, con un momento di
caricamento naturale, mai dentro una stanza. E' un contratto molto piu' semplice
del bancare per scena.

---

## 5. Cantiere genuinamente nuovo

L'unico sottosistema che non erediteremo da nessuna parte e' l'**audio**: il
Jaguar non ha sintesi hardware, quindi la musica XMIDI richiede un soft-synth
wavetable su Jerry (26,6 MHz, 8 KB locali — fattibile) oppure conversione
offline a stream. Il 32X qui non fa da apripista perche' ha lo YM2612 in dote.
Rimandato a M7, ma la DRAM va riservata da subito.

---

## 6. Riferimenti

- Contratto HAL da soddisfare: `D:\Homebrew5\32XLBA\m10-engine\hal\hal.h`
- Engine derivato da importare: `D:\Homebrew5\32XLBA\m10-engine\engine_src\`
- Metodo di derivazione: `D:\Homebrew5\32XLBA\docs\engine_port_arch.md`
- Audit funzione-per-funzione: `D:\Homebrew5\32XLBA\docs\derivation_audit_s17.md`
- Sorgente originale: `lba1-classic-community-main/SOURCES/`
- Codice da recuperare: `src/gpu_raster.s`, `src/scene_viewer.s`, `tools/*.py`
- Toolchain C m68k: immagine Docker `marsdev-32x`, `/marsdev/mars/m68k-elf/bin`
