/* jag_hw.h - i registri del Jaguar che il blob C tocca direttamente.
 *
 * PERCHE' IL C E NON UNA PRIMITIVA IN RMAC
 * ----------------------------------------
 * La regola del progetto e' "nessun ciclo su pixel sul 68000, mai" (3.2 di
 * docs/skeleton_design.md), e vale. Ma programmare il Blitter NON e' un ciclo
 * su pixel: sono nove scritture long e un'attesa. Il ciclo su pixel lo fa il
 * Blitter, che e' il punto.
 *
 * Metterlo in RMAC avrebbe aggiunto una superficie di contatto in piu' da
 * tenere sincronizzata (la forma dei record dei brick, gli indirizzi del
 * buffer di lavoro) proprio nel file piu' delicato del progetto, quello che in
 * S27 ha richiesto quattro diagnosi per un difetto. Gli indirizzi dei registri
 * sono presi dalle EQU di jaguar.inc via src/main_v6.prn, non da memoria.
 */
#ifndef JAG_HW_H
#define JAG_HW_H

#define HW_L(addr) (*(volatile unsigned long *)(addr))
#define HW_W(addr) (*(volatile unsigned short *)(addr))

/* CLUT: 256 voci RGB16 Jaguar, formato R5 B5 G6 (NON RGB565: il verde sta in
 * basso). Sbagliarlo da' un'immagine con rosso e blu plausibili e i verdi
 * altrove, che e' il modo in cui questo errore si nasconde. */
#define JAG_CLUT      0x00F00400UL

/* BG: il colore che TOM mostra dove nessun oggetto disegna, cioe' i pixel oltre
 * i 640 dell'oggetto di fondo. Il lato RMAC ci mette COL_BOOT (verde scuro) e
 * lo lascia li', percio' fino a S29 restava una striscia verde a destra della
 * scena - su BigPEmu come su hardware, quindi non era specifica del ferro.
 *
 * Portarlo a nero quando il fondo e' costruito non nasconde la striscia: la
 * rende INFORMATIVA. Verde = sto ancora caricando, oppure l'OP e' morto e non
 * disegna piu' niente ([[feedback_op_dies_when_masked]]); nero = sto girando.
 * Un colore che vuol dire due cose diverse in due momenti diversi vale piu' di
 * uno che ne vuol dire una sola per sempre. */
#define JAG_BG_REG    0x00F00058UL

/* Blitter */
#define A1_BASE       0x00F02200UL
#define A1_FLAGS      0x00F02204UL
#define A1_CLIP       0x00F02208UL
#define A1_PIXEL      0x00F0220CUL
#define A1_STEP       0x00F02210UL
#define A2_BASE       0x00F02224UL
#define A2_FLAGS      0x00F02228UL
#define A2_MASK       0x00F0222CUL
#define A2_PIXEL      0x00F02230UL
#define A2_STEP       0x00F02234UL
#define B_CMD         0x00F02238UL
#define B_COUNT       0x00F0223CUL
#define B_PATD        0x00F02268UL

/* Configurazione di A1 sul fondo: PITCH1 | PIXEL8 | WID640 | XADDPIX.
 * WID640 = campo $25 (1.01 x 2^9), la stessa codifica derivata in
 * tools/bg_ref.py e verificata sui due esempi del TRM. */
#define A1_FLAGS_BG   0x00014A18UL

/* Configurazione di A1 per azzerare il BUFFER DI LAVORO dei brick, che non e'
 * un'immagine ma va trattato come tale per darlo in pasto al Blitter: WID2048
 * (campo $2C = 1.00 x 2^11) x le righe che servono a coprire la regione.
 *
 * 2048 e' scelto perche' e' rappresentabile ed e' una potenza di due: la
 * regione, che e' un multiplo di 2048, viene coperta esattamente da un numero
 * intero di righe, senza resto da rifinire a mano. */
#define A1_FLAGS_WORK 0x00015818UL
#define WORK_FILL_W   2048

/* Riempimento a motivo per azzerare il fondo, e blit di sprite con l'indice 0
 * trasparente. Sono i due comandi che V4 usava a schermo dalla V3.4: si
 * riprendono verbatim invece di ricomporli bit per bit, perche' un bit
 * sbagliato in B_CMD non da' un errore ma un'immagine quasi giusta. */
#define B_CMD_FILL    0x01810200UL
#define B_CMD_SPRITE  0x09800601UL

/* Copia OPACA, cioe' senza l'indice 0 trasparente: la destinazione viene
 * sostituita comunque. Serve allo scorrimento del fondo (S42), dove una copia
 * trasparente lascerebbe in piedi il vecchio contenuto dove il nuovo e' vuoto.
 *
 * Preso verbatim da src/scene_viewer.s:227, dove e' in uso e verificato SUL
 * FERRO da S22: differisce da B_CMD_SPRITE per il solo DCOMPEN, ma quel bit
 * ricavato a mano dal TRM e' esattamente il genere di cosa che non da' un
 * errore, da' un'immagine quasi giusta. */
#define B_CMD_OPAQUE  0x01800601UL

static inline void Blit_Wait(void)
{
    while (!(HW_L(B_CMD) & 1))
        ;
}

/* GPU TOM. Stesso ragionamento del Blitter: avviare la GPU non e' un ciclo su
 * pixel, sono cinque scritture long e un'attesa - il ciclo sui pixel lo fa la
 * GPU, che e' il punto. Il CODICE della GPU sta in src/gpu_body.s perche' RMAC
 * e' l'unico che sappia assemblare il set di istruzioni del RISC; la sua RAM
 * locale la riempie InitGpuBody al boot. Indirizzi dalle EQU di jaguar.inc. */
#define G_FLAGS       0x00F02100UL
#define G_END         0x00F0210CUL
#define G_PC          0x00F02110UL
#define G_CTRL        0x00F02114UL
#define GPU_RISCGO    0x00000001UL

/* Ingressi dei due kernel nella RAM locale della GPU. Da S44 il numero vive
 * in tools/memory_map.py (JAG_GPU_SRAM_*, via mem_map.h): "l'unico numero che
 * i due file si scambiano senza passare dalla mappa" e' esattamente quello
 * che in S44 li ha fatti sovrapporre quando il kernel dei corpi e' cresciuto.
 * I due programmi sono residenti insieme nei 4 KB: niente ricariche per
 * lancio, si riprogramma solo il PC. */
#define JAG_GPU_BODY_ENTRY  JAG_GPU_SRAM_BODY
#define JAG_GPU_SHADE_ENTRY JAG_GPU_SRAM_SHADE
#define JAG_GPU_FRONT_ENTRY JAG_GPU_SRAM_FRONT

/* Il divisore del clock seriale di Jerry, che fissa la frequenza di
 * campionamento: Fs = SysClk / (2*(N+1)*32) in mode16 (TRM pag. 97). Con N=19
 * escono i 20.795 Hz MISURATI in S35.
 *
 * E' scrivibile a caldo, ed e' il motivo per cui sta qui: il budget del
 * sintetizzatore dipende da Fs in un modo che NON si ricava a tavolino,
 * perche' il costo dell'interruzione e' fisso per interruzione e quindi pesa
 * di piu' quando le interruzioni sono di piu'. Potendolo spazzare col joypad,
 * quella curva si misura invece di stimarla.
 *
 * REGISTRO DI SOLA SCRITTURA: il valore corrente lo tiene una copia in RAM,
 * non si puo' rileggere dall'hardware. */
#define JAG_SCLK      0x00F1A150UL
#define JAG_SCLK_MIN  11
#define JAG_SCLK_MAX  31

/* $00070007 = organizzazione dei dati big-endian su tutte e due le meta'. E'
 * il valore che V1..V5 hanno usato per ogni lancio della GPU; si riprende
 * verbatim perche' un bit sbagliato qui non da' un errore ma dati letti al
 * contrario. */
#define G_END_BE      0x00070007UL

/* Torna 1 se la GPU ha alzato DONE, 0 se e' scaduto il guardiano.
 *
 * IL GUARDIANO NON E' PRUDENZA, E' UNO STRUMENTO DI MISURA. Un'attesa senza
 * uscita trasforma qualsiasi difetto della GPU nello stesso identico sintomo -
 * la macchina ferma - e cancella tutte le altre sonde insieme. Con l'uscita, il
 * disegno prosegue saltando quel poligono: si vede quanti si sono piantati,
 * quale per primo e con che dati, e le sonde a monte restano leggibili.
 *
 * IL CONTEGGIO SI TARA SUL LAVORO MISURATO, NON "GRANDE PER SICUREZZA".
 * Un milione di giri sembrava prudenza e invece rendeva il guardiano inutile:
 * mentre la GPU e' in fuga satura il bus, ogni lettura del 68000 costa un
 * centinaio di cicli, e un milione di giri diventano ~8 secondi. Per 110
 * poligoni sono quindici minuti prima di vedere un numero - cioe' la stessa
 * macchina ferma che il guardiano doveva evitare.
 *
 * Il lavoro vero: al massimo 38 righe per ~360 istruzioni GPU, circa 40000
 * cicli, che il 68000 vede come un migliaio di giri di attesa.
 *
 * DA S34 IL LAVORO NON E' PIU' COSTANTE, quindi nemmeno il guardiano lo e'.
 * Un lancio non copre piu' UN poligono ma un flusso intero - fino a ~113 sul
 * piu' grosso attore misurato - cioe' cento volte il lavoro. Un guardiano fisso
 * tarato sul poligono scatterebbe su ogni lancio buono; uno tarato sul flusso
 * peggiore sarebbe cento volte troppo largo quando il flusso e' corto, ed e'
 * l'errore del milione di S33 in un'altra forma.
 *
 * Percio' lo decide il chiamante, sul lavoro che sta per consegnare: una base
 * per la stretta di mano piu' un tanto per lavoro. Il fattore quattro di
 * margine sul migliaio di giri misurato basta: piu' del margine conta che, se
 * scatta, si veda un numero invece di una macchina ferma. */
#define GPU_RUN_GUARD_BASE     20000UL
#define GPU_RUN_GUARD_PER_JOB   4000UL
/* E per lo stadio dell'intensita', dove l'unita' di lavoro e' la NORMALE:
 * ~60 tick di kernel piu' i sedici del divisore, contro i ~40 tick di GPU che
 * costa un giro dell'attesa del 68000. Quaranta e' quindi un fattore sei di
 * margine sul lavoro misurato, non un numero grande per prudenza. */
#define GPU_RUN_GUARD_PER_NORM    40UL
/* E per il front-end (S45), dove l'unita' e' il VERTICE o il POLIGONO: la fase
 * V costa ~120 tick a vertice (due load dalla DRAM, nove moltiplicazioni, due
 * store), la fase C fino a ~500 a poligono sopravvissuto (il minimo della
 * chiave rilegge gli angoli). Il chiamante passa vertici+poligoni: 40 giri di
 * attesa per unita' e' un fattore >3 sul caso peggiore, non un numero grande
 * per prudenza. */
#define GPU_RUN_GUARD_PER_VERT    40UL

static inline int Gpu_Run(unsigned long entry, unsigned long done_addr,
                          unsigned long guard)
{

    HW_L(done_addr) = 0;

    /* BARRIERA PRIMA DI PARTIRE, e non e' simmetria estetica con quella che la
     * GPU fa in uscita.
     *
     * Il chiamante ha appena scritto in DRAM i bordi e gli argomenti. Le
     * scritture del 68000 sul Jaguar sono POSTICIPATE: possono essere ancora in
     * volo quando RISCGO parte, e allora la GPU legge i valori del giro
     * PRECEDENTE. Un emulatore le completa nell'istante, quindi questo difetto
     * non esiste su BigPEmu ed esiste sul ferro - e si manifesta come un
     * poligono ogni tanto disegnato con i dati di quello prima, cioe' come un
     * guasto intermittente e non riproducibile.
     *
     * Una lettura qualsiasi forza l'ordine: la rilettura non torna finche' le
     * scritture in coda non sono passate. */
    (void)HW_L(done_addr);

    /* FERMARE PRIMA DI RIPROGRAMMARE, E ACCERTARSENE.
     *
     * Alla fine del lavoro la GPU non si spegne: gira a vuoto nel suo salto di
     * arresto, ed e' il 68000 a fermarla scrivendo zero qui. Riscrivere G_PC
     * mentre e' ancora in moto vuol dire perdere la riprogrammazione: la GPU
     * riparte da dove stava, cioe' NON rilegge gli argomenti nuovi.
     *
     * Su BigPEmu lo stop e' istantaneo e ordinato, quindi il difetto non esiste.
     * Sul ferro si e' visto cosi': ymin/ymax scritti dal 68000 diversi da quelli
     * riletti dalla GPU, che erano quelli del lancio PRECEDENTE. Il sintomo
     * sembrava un difetto di disegno e invece era un avvio perso.
     *
     * La rilettura non serve al valore: serve a non proseguire finche' la
     * scrittura non e' passata davvero. */
    HW_L(G_CTRL) = 0;
    (void)HW_L(G_CTRL);

    HW_L(G_FLAGS) = 0;
    HW_L(G_END) = G_END_BE;
    HW_L(G_PC) = entry;
    (void)HW_L(G_PC);           /* il PC nuovo e' atterrato prima del via */

    HW_L(G_CTRL) = GPU_RISCGO;
    while (!HW_L(done_addr)) {
        if (!--guard) {
            HW_L(G_CTRL) = 0;
            (void)HW_L(G_CTRL);
            return 0;
        }
    }
    HW_L(G_CTRL) = 0;
    (void)HW_L(G_CTRL);
    return 1;
}

#endif /* JAG_HW_H */
