"""JagLBA V6 - MASTER della mappa di memoria (DRAM + cartuccia).

Questo file e' l'UNICO padrone del layout. Tutto il resto e' generato:

    include/dram_map.inc     (RMAC)   - regioni DRAM
    include/cart_map.inc     (RMAC)   - layout cartuccia
    engine_c/jag_engine.ld   (GNU ld) - split ROM/RAM del blob C
    engine_c/mem_map.h       (C)      - stesse costanti per l'engine

Motivazione (docs/skeleton_design.md 3.2 e 3.5): in V4 la mappa era implicita e
sparsa su piu' sorgenti, e questo ha prodotto due collisioni silenziose in S24
(blob C dentro la griglia, VBL_TICK sopra GPU_ARG_OUT_COUNT). Qui le regioni si
dichiarano una volta sola e la sovrapposizione e' un errore di build.

Uso:  python tools/memory_map.py          (genera + verifica)
      python tools/memory_map.py --check  (solo verifica, non scrive)
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DRAM_SIZE = 0x200000            # 2 MB
CART_BASE = 0x800000
CART_SIZE = 6 * 1024 * 1024

# ---------------------------------------------------------------------------
# Regioni DRAM. (nome, base, size, commento)
# Elencate in ordine di indirizzo; il controllo sotto pretende che siano
# contigue e senza buchi non dichiarati.
# ---------------------------------------------------------------------------
DRAM = [
    ("VECTORS",        0x000000, 0x000400, "tabella vettori 68000 (256 long)"),
    ("BOOT_PAD",       0x000400, 0x000400, "riservato, non usato"),
    ("OP_LIST",        0x000800, 0x000800, "Object Processor list"),
    ("HAL_VARS",       0x001000, 0x001000, "variabili lato RMAC (HAL)"),
    ("GPU_VARS",       0x002000, 0x001000, "argomenti e bordi per src/gpu_body.s"),
    ("PALETTE",        0x003000, 0x000400, "copia RAM della CLUT (256 x RGB16)"),
    ("SPARE_LO",       0x003400, 0x000C00, "margine basso"),
    ("BRICK_HANDLES",  0x004000, 0x009000, "handle di brick (long), uno per slot LBA_BRK"),
    ("BRICK_USED_BMP", 0x00D000, 0x003000, "bitmap dei brick referenziati"),
    ("BRICK_WORK",     0x010000, 0x090000, "brick decodificati 8bpp (per scena)"),
    ("BG_FB",          0x0A0000, 0x060000, "fondo UNICO 640x592 8bpp (379 KB usati)"),
    ("CUBE_DRAM",      0x100000, 0x032000, "griglia cubo 64x25x64 (misurata S24)"),
    ("BODY_WORK",      0x132000, 0x003000, "lavoro su UN corpo: proiezione, intensita', ordine"),
    # DUE banchi da $F000 (S49). Il secondo non e' un lusso: con un banco solo
    # l'Object Processor legge il buffer MENTRE lo riscriviamo, e l'eroe sparisce
    # per tutta la durata del disegno - misurato sul ferro, i tre quarti del
    # quadro, perche' la finestra va da spr_clear fino a quando le tessere
    # davanti sono cotte. Le phrase possono puntare solo a un'immagine completa,
    # quindi con un banco solo lo stato intermedio si VEDE per costruzione.
    ("ACTOR_SPRITES",  0x135000, 0x01E000, "sprite attore OP (S49: due banchi, righe da 640 come il fondo)"),
    # I 40 KB del secondo banco vengono da qui, ed e' il donatore giusto:
    # misurato sul ferro, l'arena per-cubo della scena 0 ne usa 66624 su oltre
    # 480 KB (pagina 1, etichetta A). Il margine alto invece resta intatto:
    # e' prenotato dall'audio del DSP e prenderglielo adesso vorrebbe dire
    # scoprirlo a M7, quando rimetterlo a posto costa molto di piu'.
    ("C_ARENA",        0x153000, 0x076000, ".data/.bss engine C + heap Malloc"),
    # 10240 celle x 8 byte = $14000. Vedi CELL_SLOTS piu' sotto: la cella e'
    # cresciuta di due byte in S37 per portarsi dietro le due coordinate che
    # DrawOverBrick confronta.
    ("CELL_LIST",      0x1C9000, 0x014000, "celle risolte del cubo, in ordine di pittore"),
    # 32 KB e non 16: da S37 nel flusso non ci sono solo i poligoni ma anche le
    # TRATTE di linee e sfere, una per riga. MISURATO sul banco di prova:
    # l'attore piu' grosso delle tre stanze e' passato da 12704 a 19600 byte, e
    # a 16 KB l'eroe si faceva due lanci invece di uno - cioe' si perdeva
    # proprio la proprieta' che S34 aveva comprato (335 strette di mano ridotte
    # a 4). Non e' un margine di sicurezza inventato: e' il numero misurato piu'
    # dodici KB, presi dai sessantotto di SPARE_HI.
    ("JOB_STREAM",     0x1DD000, 0x008000, "flusso di poligoni e tratte per il lancio singolo (S34/S37)"),
    ("SPARE_HI",       0x1E5000, 0x00B000, "margine alto (audio DSP, M7)"),
    ("STACK",          0x1F0000, 0x010000, "stack 68000, top = fine DRAM"),
]

# Slot dentro OP_LIST. Ogni oggetto OP e' allineato a phrase (8 byte).
# L'ordine di esecuzione e': BR_VDE -> BR_VDB -> BG -> [attori] -> TAIL_STOP.
# I due branch saltano a STOP quando VC e' fuori dalla finestra visibile.
OP_SLOTS = [
    ("OP_STOP",      0x000800, 0x0008, "STOP: target dei branch e OP list di panico"),
    ("OP_PAD",       0x000808, 0x0008, "allineamento"),
    ("OP_BR_VDE",    0x000810, 0x0008, "BRANCH: VC > VDE -> STOP"),
    ("OP_BR_VDB",    0x000818, 0x0008, "BRANCH: VC < VDB -> STOP"),
    # Un BMobj per campo, entrambi sempre presenti in lista: la parita' di YPOS
    # decide da sola quale dei due l'OP disegna nel campo corrente, quindi il
    # gestore non deve piu' indovinare in che campo si trova.
    ("OP_BG_F0",     0x000820, 0x0010, "BMobj del fondo, campo pari (2 phrase)"),
    ("OP_BG_F1",     0x000830, 0x0010, "BMobj del fondo, campo dispari (2 phrase)"),
    ("OP_ACTORS",    0x000840, 0x0200, "32 BMobj attore x 16 byte (M3)"),
    ("OP_TAIL_STOP", 0x000A40, 0x0008, "STOP finale della lista"),
]

# ---------------------------------------------------------------------------
# Campi dentro HAL_VARS. Sono la superficie di contatto fra il lato RMAC (che
# li scrive) e il blob C (che li legge), quindi hanno lo STESSO problema che la
# mappa DRAM aveva in V4: due sorgenti che dichiarano gli stessi indirizzi a
# mano. In M0 erano duplicati fra src/main_v6.s e engine_main.c, e sarebbero
# divergiti al primo campo aggiunto. Da M1 li dichiara questo master.
#
# (nome, offset, size, commento)
HAL_FIELDS = [
    ("VDB_VAL",        0x00, 2, "word: Vertical Display Begin"),
    ("VDE_VAL",        0x02, 2, "word: Vertical Display End"),
    ("OP_BG_PH0LO",    0x04, 4, "long: PH0 LO del fondo (YPOS+HEIGHT+LINK)"),
    ("VBL_TICK",       0x08, 4, "long: contatore interrupt video, +1 per campo"),
    ("LAST_TICK",      0x0C, 4, "long: ultimo tick consumato da MainLoop"),
    ("EXC_BLINKS",     0x10, 2, "word: lampi da emettere (percorso di panico)"),
    ("TICK_HZ",        0x12, 2, "word: interrupt video al secondo (M1: base di TimerRef)"),
    ("DBG_VC",         0x14, 4, "long: VC campionato nel gestore"),
    ("DBG_ODD_CNT",    0x18, 4, "long: interrupt con VC bit 11 alto"),
    ("DBG_CAL",        0x1C, 4, "long: costante nota, taratura della lettura"),
    ("DBG_F0_HI",      0x20, 4, "long: OP_BG_F0 phrase 0 HI prima del ripristino"),
    ("DBG_F0_LO",      0x24, 4, "long: OP_BG_F0 phrase 0 LO prima del ripristino"),
    ("VP_VAL",         0x28, 2, "word: Vertical Period"),
    ("FIELD_PAR",      0x2A, 2, "word: parita' del campo che si sta per disegnare"),
    # I due contatori del DSP audio (S35). Non li scrive il lato RMAC ma JERRY,
    # ed e' il motivo per cui stanno qui invece che in una regione loro: sono
    # una superficie di contatto fra un processore che conta e uno che legge,
    # esattamente come VBL_TICK. Il DSP li aggiorna dal ciclo di riposo, mai
    # dall'ISR: una scrittura in DRAM per campione a 20 kHz sarebbe traffico sul
    # bus dentro l'interruzione piu' fitta della macchina.
    ("DSP_IRQ_CNT",    0x2C, 4, "long: interruzioni I2S contate dal DSP"),
    ("DSP_IDLE_CNT",   0x30, 4, "long: blocchi di ciclo di riposo del DSP"),
    ("DSP_STARVE",     0x34, 4, "long: volte che l'ISR ha trovato il buffer vuoto"),
    # Va nell'altro verso: qui il 68000 SCRIVE e il DSP legge. Serve a poter
    # spazzare il numero di operatori a caldo col joypad invece di ricostruire
    # e rimasterizzare la cartuccia per ogni valore.
    ("DSP_OPS",        0x38, 4, "long: operatori OPL2 per campione (68000 -> DSP)"),
    ("DSP_ENV_MASK",   0x3C, 4, "long: maschera di cadenza dell'inviluppo (68000 -> DSP)"),
    ("DSP_GAIN",       0x40, 4, "long: scalatura dell'uscita audio, in bit (68000 -> DSP)"),
    # S47, attore-sprite OP. Stanno qui per la stessa ragione di OP_BG_PH0LO:
    # l'OP CONSUMA l'oggetto mentre lo disegna e ViHandler lo riarma a ogni
    # campo, quindi le due phrase vanno tenute da qualche parte. Le scrive il
    # C (che sa dove sta l'attore), le legge il gestore di interrupt.
    # A HEIGHT zero l'oggetto e' spento: e' cosi' che si nasconde lo sprite
    # quando l'attore e' fuori campo, senza toccare la catena della lista.
    ("OP_SPR_PH0LO",   0x44, 4, "long: PH0 LO dello sprite (YPOS+HEIGHT+LINK)"),
    ("OP_SPR_PH1LO",   0x48, 4, "long: PH1 LO dello sprite (XPOS+depth+DWIDTH+IWIDTH)"),
    ("SPR_DATA_A",     0x4C, 4, "long: indirizzo dei dati, campo pari"),
    ("SPR_DATA_B",     0x50, 4, "long: indirizzo dei dati, campo dispari"),
    # LE QUATTRO COPIE VIVE (S49). Il C scrive le quattro sopra quando vuole;
    # ViHandler le ADOTTA solo all'inizio del campo A e arma sempre da queste.
    # Senza, il cambio di banco cade a meta' di una coppia di campi: le righe
    # pari vengono da una posa e le dispari da quella dopo, e l'eroe si vede
    # sfrangiato lungo le righe dell'interlacciamento. A 20 fps capita a quasi
    # ogni quadro, perche' il quadro dura tre campi e il cambio e' uno.
    ("SPR_LIVE_DATA_A", 0x54, 4, "long: copia viva di SPR_DATA_A, adottata al campo A"),
    ("SPR_LIVE_DATA_B", 0x58, 4, "long: copia viva di SPR_DATA_B"),
    ("SPR_LIVE_PH0LO",  0x5C, 4, "long: copia viva di OP_SPR_PH0LO"),
    ("SPR_LIVE_PH1LO",  0x60, 4, "long: copia viva di OP_SPR_PH1LO"),
    # S51: il canale d'ingresso dell'EMULATORE. Uno script di BigPEmu ci scrive
    # i pulsanti (word bassa, bit PAD_*) e il tastierino (word alta, bit di
    # PadKeys), e Hal_ReadPad li somma a quelli veri. Serve a guidare il gioco
    # da uno script - logo, menu, un giro a piedi - senza mandare tasti al
    # desktop dell'utente. engine_init la azzera: sul ferro nessuno la scrive.
    ("EMU_PAD",         0x64, 4, "long: pulsanti dello script BigPEmu, OR sul pad (0 sul ferro)"),
]
# Il joypad NON e' qui. Leggerlo e' superficie HAL, e il blob C puo' arrivare a
# $F14000 come a qualunque altro indirizzo: farlo in C evita due campi in piu'
# da tenere sincronizzati e soprattutto evita di rimettere le mani in ViHandler,
# che e' la parte piu' delicata del progetto (S27). Vedi engine_c/hal_input.c.

# ---------------------------------------------------------------------------
# Layout cartuccia. Offset nel file .j64; indirizzo = CART_BASE + offset.
# Il packer (tools/cart_build.py) piazza ogni blob al suo offset fisso, quindi
# l'indirizzo del blob C NON dipende piu' dalla dimensione del codice RMAC.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Regioni della cartuccia.
#
# S30: ROM_CODE e ROM_CBLOB riservavano 768 KB e ne usavano 11. Non era
# generosita' innocua: erano LORO a decidere che il gioco completo non entrava
# nei 6 MB, non i dati. Ridotti a 256 KB in tutto, il contenitore con TUTTE e
# 120 le scene (5901532 byte) entra con 116 KB di margine.
#
# Il numero da sorvegliare adesso e' il blob C, non ROM_DATA. Quando il motore
# vero lo fara' crescere oltre i 192 KB, la riserva da tirare e' comprimere
# anche BRICK: vale 285 KB (vedi STORE in tools/hqr_pack.py), costa un LZSS sul
# percorso di caricamento e richiede una via che decomprima senza passare dalla
# cache, perche' i brick di una scena sono 526 e la cache ne tiene 64.
# ---------------------------------------------------------------------------
CART = [
    ("ROM_HEADER", 0x000000, 0x002000, "stub fastboot $F6 + config (pad_rom)"),
    ("ROM_CODE",   0x002000, 0x010000, "output RMAC: main + HAL + gpu_raster"),
    ("ROM_CBLOB",  0x012000, 0x030000, "engine C: .text/.rodata + LMA di .data"),
    ("ROM_DATA",   0x042000, 0x5BE000, "dati impacchettati (scene, brick, body)"),
]

# Numero di sprite attore preallocati (deve combaciare con OP_ACTORS/16).
ACTOR_SPRITE_COUNT = 20
ACTOR_SPRITE_SIZE = 0x1000

# Slot di LBA_BRK. Servono un handle da 4 byte e un bit di bitmap ciascuno; il
# numero vero e' 8715 e va tenuto qui, non nel C, perche' e' la capienza di due
# regioni DRAM. In V4 stava scritto 8619 in un commento e nessuno lo
# controllava: sforare BRICK_HANDLES vuol dire scrivere dentro BRICK_USED_BMP,
# cioe' cancellare i bit che dicono quali brick decodificare.
BRICK_SLOTS = 8715

# Capienza della lista compatta delle celle (S30). Ogni voce e' 6 byte:
# lo scostamento di schermo px/py della cella, gia' calcolato e SENZA camera,
# piu' l'indice del brick. Sostituisce la seconda passata sulle 102400 celle
# del cubo, di cui il 91% non nomina niente.
#
# Il numero NON e' scelto a occhio: contate le celle risolte di tutte e 133 le
# griglie del gioco, la piu' carica e' la scena 8 con 9852.
#
# S37 / M3d: la cella porta anche DUE coordinate di cubo, e con quelle sono 8
# byte esatti invece di 6 - senza riempimento, perche' px e py vogliono
# indirizzo pari.
#
#   gy    il livello, cioe' l'altezza in celle
#   gxz   gx+gz, cioe' la profondita' in celle
#
# Sono le DUE sole grandezze che `DrawOverBrick` confronta (GRILLE.C:518-524:
# `Ym >= ym` e `(Xm+Zm) > (xm+zm)`), e non si possono ricavare da px e py: quelle
# due equazioni hanno tre incognite. Portarle nella cella costa due byte;
# ricalcolarle costerebbe due divisioni per cella a ogni attore.
#
# I 12288 posti di prima avevano il 25% di margine sulla scena piu' carica
# (9852). A 8 byte quel margine costerebbe 24 KB di DRAM, quindi si stringe a
# 10240 - il 4% - che basta perche' chi sfora lo scopre come ERRORE DI BUILD e
# non come guasto a runtime: tools/bg_ref.py confronta ogni scena che impacchetta
# contro questo numero.
CELL_SLOTS = 10240
CELL_BYTES = 8

BG_W = 640
BG_H = 592

# Capienza del lavoro su UN corpo (M3c). Dimensionano BODY_WORK, e come
# CELL_SLOTS sono MISURATI e non scelti: percorse tutte e 132 le voci di
# BODY.HQR, il massimo e' 257 vertici, 261 normali, 304 poligoni, 15 angoli per
# poligono. Qui stanno arrotondati verso l'alto.
#
# Un corpo che li sforasse deve fermare il programma, non scrivere oltre la
# regione: lo controlla engine_c/body_draw.c a ogni parse. E che i numeri
# restino veri lo ricontrolla `python tools/body_ref.py --limits` sull'archivio,
# cosi' non e' una misura fatta una volta e poi creduta per sempre.
BODY_MAX_VERTS   = 320
BODY_MAX_NORMALS = 320
BODY_MAX_POLYS   = 384
BODY_MAX_CORNERS = 16

# M4: i GRUPPI dell'animazione. Misurato su tutto l'archivio, il massimo e' 25 -
# e lo stesso numero esce da ANIM.HQR, che non puo' pilotare piu' gruppi di
# quanti il corpo ne abbia. Arrotondato a 32.
#
# Un gruppo si porta due cose dentro BODY_WORK:
#   la sua MATRICE      9 long (TabMat, P_OB_ISO.ASM:1188) - la posa di un
#                       gruppo e' quella del padre composta con la propria
#                       rotazione, quindi la matrice del padre deve esistere
#                       ancora quando si calcola quella del figlio;
#   la sua POSA         tipo + tre valori, 4 word (i byte +8..+15 del record da
#                       38 che nel DOS stanno DENTRO il corpo - vedi
#                       body_draw.c, che qui non puo' scriverli perche' il
#                       corpo e' in cartuccia).
BODY_MAX_GROUPS  = 32
BODY_GROUP_BYTES = 9 * 4 + 4 * 2

# ---------------------------------------------------------------------------
# S47: LO SPRITE DELL'ATTORE (OP layered).
#
# Il buffer tiene righe LARGHE QUANTO IL FONDO, e non e' spreco: e' la scelta
# che lascia il kernel GPU intatto. Il riempitore calcola `fb + y*640 + x` col
# 640 cotto dentro, quindi dichiarando la base spostata indietro di y0 righe
# atterra da solo dentro il buffer, senza traslare una sola coordinata (vedi
# BODY_ACTOR_SPRITE in engine_c/body_draw.c). Il BMobj ne mostra SPR_VIS_W
# perche' IWIDTH e DWIDTH sono campi indipendenti.
#
# L'altezza e' il riquadro di Twinsen (79 righe, uniforme su tutte le varianti
# 0..8) piu' margine: un corpo che non ci sta si disegna nel fondo come prima,
# e il C se ne accorge dal riquadro che la fase V ha appena calcolato.
SPR_PITCH  = 640         # come BG_W: il kernel non lo sa nemmeno
SPR_ROWS   = 96          # 79 di Twinsen + margine
SPR_VIS_W  = 128         # pixel mostrati dal BMobj (IWIDTH = 16 phrase)
SPR_BYTES  = SPR_PITCH * SPR_ROWS      # 60 KB per banco
# DUE BANCHI, e il secondo non si ottimizza via. Il disegno di una posa dura
# quasi un quadro intero; l'Object Processor legge il buffer a ogni campo. Con
# un banco solo i due si sovrappongono sempre, e non c'e' ordinamento delle
# scritture che lo eviti: le phrase possono puntare a un'immagine COMPLETA o a
# niente. Si disegna nel banco dietro e si scambiano SPR_DATA_A/B alla fine,
# che e' un'operazione che l'attore-sprite rende possibile e che il fondo non
# poteva fare senza un secondo framebuffer intero.
SPR_BANKS  = 2

# Byte per vertice / per normale / per poligono dentro BODY_WORK. Sono qui e non
# nel C perche' e' questo file a garantire che ci stiano.
#   vertice   posa x,y,z,pad (4 word = una PHRASE) + record di schermo wscr
#             (2 long: (sx<<16|sy) impacchettato + profondita')
#   normale   intensita' (1 word)
#   poligono  una voce di wlist: chiave (long) + (indice<<16|scostamento) (long)
#
# S45: la forma e' quella che la GPU legge e scrive. Il vertice di posa e' una
# phrase (il pad porta l'allineamento, LOADP/STORE senza spezzare); lo schermo
# e' UN long impacchettato - che e' anche ESATTAMENTE il long che il flusso dei
# lavori vuole a +0 del vertice, quindi l'emissione lo copia senza ricomporlo.
# La coppia (chiave, indice) sta in un record da 8 byte: l'ordinamento scambia
# due long alla stessa base invece di tre array.
#
# La profondita' e' un LONG e non una word, ed e' una divergenza DICHIARATA dal
# gioco: X+Y+Z arriva a 56644 sulle nostre tre stanze, mentre il DOS la tiene in
# una word (`mov word ptr[edi+4], bp`) e la confronta con una `cmp` a 16 bit. Un
# attore i cui poligoni cadono a cavallo di 32767 li vedrebbe quindi ordinati a
# rovescio - un difetto del gioco, non una convenzione da riprodurre.
BODY_VERT_BYTES   = 16
BODY_NORMAL_BYTES = 2
BODY_POLY_BYTES   = 8

# S45: il DESCRITTORE DI POSA - il contratto del kernel front-end (gpu_front.s).
# Una voce per gruppo (o una sola per un corpo senza gruppi), QUATTRO long:
#
#     +0   moff<<16 | nbv        moff = scostamento della matrice in LONG (g*9)
#                                nbv  = vertici del gruppo, GIA' limitati
#     +4   vstart<<16 | anchor   anchor = indice del vertice di ancoraggio,
#                                $FFFF = nessuno (gruppo 0 / corpo senza gruppi)
#     +8   dx<<16 | dy           lo spostamento TRANSLATE, word firmate
#     +12  dz<<16 | 0            (zero per i gruppi ROTATE)
#
# Vive dentro GPU_VARS, DOPO gli argomenti: e' scritto dal 68000 per attore e
# letto dalla GPU, quindi e' superficie di contatto esattamente come gli
# argomenti. 33 voci = MAXG (32) piu' una di scorta per il caso senza gruppi.
POSE_DESC_OFF   = 0x200          # scostamento dentro GPU_VARS
POSE_DESC_MAX   = 33
POSE_DESC_LONGS = 4

# Superficie di contatto con la GPU TOM (M3c mossa 2). Sta dentro GPU_VARS e la
# dichiara QUESTO file, non src/gpu_body.s: e' la lezione di S24, dove due
# sorgenti che si dichiaravano gli indirizzi da soli hanno prodotto VBL_TICK
# sopra un argomento della GPU. Da qui esce sia l'EQU per RMAC sia il #define
# per il C, quindi i due lati non possono divergere.
#
# Un BORDO e' 8 long, e la forma e' esattamente quella di T_EDGE in
# engine_c/body_draw.c. Da S44 i bordi NON viaggiano piu' nel flusso: li
# costruisce la GPU nella sua SRAM, a partire dai vertici, con la stessa forma
# a 8 long - GPU_EDGE_LONGS resta la taglia di QUEL record, non di un record
# del flusso.
GPU_EDGE_LONGS = 8
# S46: 15 e non piu' BODY_MAX_CORNERS (16). Il massimo VERO su tutte le voci
# di BODY.HQR e' 15 angoli (body_ref.py --limits lo riverifica), e il
# sedicesimo record costava 32 byte di SRAM che i quattro kernel residenti
# non hanno piu'. Un corpo futuro con 16 angoli si vedrebbe TAGLIARE un
# lato (clamp in jobs_emit e nella fase E), non corrompere la memoria.
GPU_EDGE_MAX   = 15

# S44: nel flusso viaggiano i VERTICI, non i bordi risolti. Un vertice e' due
# long: (x<<16)|y con i due segni dentro le due word, e l'intensita' 0..255
# nuda. Le quattro divisioni per bordo passano cosi' dal __udivsi3 del 68000
# (~300 cicli l'una) al divisore hardware della GPU (16 tick, interbloccato -
# S40), e i byte scritti in DRAM per poligono scendono a un terzo.
GPU_VTX_LONGS = 2

# LA MAPPA DELLA SRAM DELLA GPU (4 KB, $F03000-$F03FFF). E' QUI e non nei
# sorgenti dei kernel per la stessa lezione di S24 che vale per la DRAM: in
# S44 i due kernel si dichiaravano l'ingresso DA SOLI, il body e' cresciuto
# (fase A dei bordi) fino a $F035B6 sopra lo shade a $F03400, e al boot le due
# copie si sono sovrapposte - meta' del riempitore era in realta' codice dello
# shade, e la GPU "finiva" scrivendo il DONE SBAGLIATO. Ogni kernel chiude con
# una .assert contro il confine che gli tocca: se cresce oltre, la build si
# ferma invece di consegnare una ROM che impazzisce.
#
# S45: entra il TERZO kernel, il front-end dei corpi (gpu_front.s: posa dei
# vertici + proiezione + faccia nascosta). Lo shade SCENDE subito dopo il body
# - i suoi 426 byte non meritavano 1,5 KB - e il front-end prende la meta' alta.
# I tre kernel sono residenti INSIEME: nessuna ricarica per lancio.
#
# S46: il body cresce della fase E (il walker di wlist), SHADE e FRONT
# scivolano in su e la SRAM e' PIENA: 2134+372+1020 di codice misurato, le
# celle del walker, la tabella dei bordi a 15 record. I margini sono
# distribuiti a chi si tocca ancora (il body: 18 byte + 16 di riserva prima
# di ETAB); shade e front sono congelati dal ferro di S40/S45. Le tre code
# identiche di barriera+DONE+arresto sono diventate UNA (GpuCommon_Done nel
# body) ed e' il grosso di come tutto questo entra nei 4 KB.
#
#   BODY     $F03000  kernel dei corpi: walker (E) + bordi (A) + riempitore (B)
#   SHADE    $F03868  kernel intensita' (S40; misurato 372 byte)
#   FRONT    $F039E0  kernel front-end (S45): vertici (V) + faccia nascosta (C)
#   FRDXYZ   $F03DE0  3 celle fisse: dx,dy,dz TRANSLATE del gruppo corrente
#                     (+1 long di scorta) - i registri del kernel sono finiti
#   EMNEXT   $F03DF0  cella fissa: dove JobNext prosegue (modo lista, S46)
#   EMST     $F03DF4  stato del walker S46, 5 long:
#                     LISTP, CNT, RUNP, NPOLY, EMDONE (la porta del
#                     terminatore del ciclo dei lavori)
#   HDRSLOT  $F03E08  cella fissa: intestazione del lavoro corrente
#   MARK     $F03E0C  cella fissa: marcatore di progresso (debug, si tiene)
#   ETAB     $F03E20  tabella dei bordi, GPU_EDGE_MAX record da 8 long
#                     (fino a $F04000 esatto; i 16 byte fra MARK e ETAB sono
#                     la riserva del body)
GPU_SRAM = [
    ("GPU_SRAM_BODY",    0xF03000, "kernel corpi: walker (E) + bordi (A) + riempitore (B)"),
    ("GPU_SRAM_SHADE",   0xF03868, "kernel intensita' (S40)"),
    ("GPU_SRAM_FRONT",   0xF039E0, "kernel front-end (S45): vertici + cull"),
    ("GPU_SRAM_FRDXYZ",  0xF03DE0, "celle fisse: dx,dy,dz del gruppo corrente"),
    ("GPU_SRAM_EMNEXT",  0xF03DF0, "cella fissa: dove JobNext prosegue (S46)"),
    ("GPU_SRAM_EMST",    0xF03DF4, "stato del walker S46, 5 long"),
    ("GPU_SRAM_HDRSLOT", 0xF03E08, "cella fissa: intestazione lavoro corrente"),
    ("GPU_SRAM_MARK",    0xF03E0C, "cella fissa: marcatore di progresso"),
    ("GPU_SRAM_ETAB",    0xF03E20, "tabella bordi, GPU_EDGE_MAX record da 8 long"),
]

# IL FLUSSO DEI LAVORI (S34). Fino a S33 si lanciava la GPU una volta per
# POLIGONO: ~320 strette di mano per stanza, cioe' 320 occasioni di corsa fra i
# due processori. Tapparne quattro VISTE non dice niente sulle non viste, quindi
# la soluzione non e' tappare: e' non correre. Il C prepara un FLUSSO di
# poligoni e lancia la GPU una volta sola.
#
# Un lavoro e' un'intestazione da 4 long seguita dai suoi VERTICI (S44; fino a
# S43 il flusso portava i bordi gia' risolti dal 68000):
#
#     +0   quanti vertici (0 = fine del flusso; 2 = tratta di linea/sfera,
#          una riga sola a colore piatto; >=3 = poligono)
#     +4   ymin, gia' limitata allo schermo dal C
#     +8   ymax, compresa
#     +12  la GPU ci scrive il TOTALE CORRENTE dei pixel a fine lavoro
#     +16  i vertici, GPU_VTX_LONGS long l'uno
#
# Il long +12 non e' diagnostica: e' quel che permette al 68000 di ricostruire
# quali poligoni hanno prodotto pixel senza chiedere alla GPU un contatore in
# piu' (la differenza fra due totali consecutivi). Serve perche' BodyStat.polys
# conta esattamente quelli, e la sonda DRAWN non deve cambiare significato.
#
# 16 KB: l'attore piu' grosso delle tre stanze del riferimento ne occupa 12704
# (MISURATO sul banco di prova, non stimato), quindi in pratica e' un lancio per
# attore. Non e' una garanzia e non deve esserlo - il limite teorico di un corpo
# e' 384 poligoni x 528 byte = 202 KB - percio' il C svuota il flusso quando e'
# pieno e riprende. La taglia decide QUANTI lanci, non se il disegno e' giusto.
GPU_JOB_HDR_LONGS = 4

GPU_ARGS = [
    ("DONE",  "il 68000 lo azzera prima di RISCGO, la GPU ci scrive 1 uscendo"),
    ("FB",    "base del framebuffer di destinazione"),
    ("JOBS",  "base del flusso dei lavori (= JOB_STREAM)"),
    ("NJOB",  "quanti lavori ci sono nel flusso, terminatore escluso"),
    ("PIXELS", "la GPU ci scrive quanti pixel ha davvero disegnato"),
    ("SAWJ",  "la GPU ci riscrive la base del flusso che ha LETTO (diagnostica)"),
    ("SAWC",  "la GPU ci riscrive il numero di lavori che ha LETTO"),
    # ---------------------------------------------------------------------
    # S40: il SECONDO kernel, lo stadio dell'intensita' (src/gpu_shade.s).
    #
    # Argomenti suoi e non riusati: i due kernel non girano mai insieme, ma un
    # long condiviso vorrebbe dire che chi legge un dump non sa quale dei due
    # l'ha scritto. Un long costa niente, una diagnosi ambigua costa un giro
    # sul ferro.
    #
    # Sono ESATTAMENTE gli argomenti di shade_stage() in body_draw.c, che e' il
    # modello che il kernel trascrive - stessa idea di T_EDGE per il riempitore.
    # ---------------------------------------------------------------------
    ("SH_DONE",  "intensita': la GPU ci scrive 1 uscendo"),
    ("SH_NORM",  "intensita': normali in forma nativa, 4 word BE per normale"),
    ("SH_NN",    "intensita': quante normali ha il corpo"),
    ("SH_OUT",   "intensita': dove scrivere, una word per normale (= wint)"),
    ("SH_SPANS", "intensita': descrittore, un long per voce (moff<<16 | count)"),
    ("SH_NS",    "intensita': quante voci ha il descrittore"),
    ("SH_MATS",  "intensita': base delle matrici di gruppo, 9 long l'una (= wmat)"),
    ("SH_LX",    "intensita': luce X, gia' ruotata (SetLightVector)"),
    ("SH_LY",    "intensita': luce Y"),
    ("SH_LZ",    "intensita': luce Z"),
    ("SH_SAWN",  "intensita': quante normali la GPU ha LETTO (diagnostica)"),
    ("SH_SAWS",  "intensita': quante voci la GPU ha LETTO (diagnostica)"),
    ("SH_LEFT",  "intensita': normali NON coperte dal descrittore, atteso 0"),
    # ---------------------------------------------------------------------
    # S45: il TERZO kernel, il front-end dei corpi (src/gpu_front.s).
    #
    # Fase V: posa dei vertici (matrice del gruppo, gia' calcolata dal 68000
    # in wmat) + proiezione isometrica + rettangolo dell'attore. Fase C: la
    # faccia nascosta e la chiave del pittore. Sono gli argomenti ESATTI di
    # vert_stage() e cull_stage() in body_draw.c, che sono i modelli.
    # ---------------------------------------------------------------------
    ("FR_DONE",  "front: la GPU ci scrive 1 uscendo"),
    ("FR_STAGE", "front: 1 = solo vertici, 2 = vertici + faccia nascosta"),
    ("FR_DESC",  "front: base del descrittore di posa (= GPU_POSE_DESC)"),
    ("FR_ND",    "front: quante voci ha il descrittore"),
    ("FR_VERTS", "front: vertici del corpo JBD (x,y,z,pad BE, una phrase)"),
    ("FR_NPTS",  "front: nb_points del corpo (guardia degli indici)"),
    ("FR_MATS",  "front: base delle matrici di gruppo (= wmat), 9 long l'una"),
    ("FR_WV",    "front: dove scrivere la posa (x,y,z,pad word = wv)"),
    ("FR_WSCR",  "front: dove scrivere schermo+profondita' (2 long = wscr)"),
    ("FR_POSX",  "front: PosObjX dell'attore"),
    ("FR_POSY",  "front: PosObjY"),
    ("FR_POSZ",  "front: PosObjZ"),
    ("FR_VX",    "front: scostamento di schermo X (camera attori)"),
    ("FR_VY",    "front: scostamento di schermo Y"),
    ("FR_BBX",   "front: la GPU ci scrive minx<<16 | maxx (word firmate)"),
    ("FR_BBY",   "front: la GPU ci scrive miny<<16 | maxy"),
    ("FR_POLYS", "front: intestazioni dei poligoni JBD, 8 byte l'una"),
    ("FR_NPOLY", "front: quanti poligoni ha il corpo"),
    ("FR_CORNERS", "front: angoli JBD, un long l'uno (normale<<16|vertice)"),
    ("FR_WLIST", "front: dove scrivere le coppie (chiave, indice) = wlist"),
    ("FR_LIVE",  "front: la GPU ci scrive quanti poligoni sopravvivono"),
    ("FR_SAWD",  "front: voci del descrittore LETTE (diagnostica)"),
    ("FR_SAWN",  "front: nb_points LETTO (diagnostica)"),
    # ---------------------------------------------------------------------
    # S46: il MODO LISTA del riempitore (fase E di gpu_body.s). La GPU
    # cammina wlist ordinata e legge il corpo DA SOLA: intestazioni e angoli
    # JBD dalla cartuccia (come gpu_front.s fa gia' da S45), schermo da wscr,
    # intensita' da wint. Il flusso resta SOLO per le tratte di linee e
    # sfere, come BLOCCHI [conteggio][tratte] in ordine di wlist: quando il
    # walker trova una voce entita' consuma il blocco successivo, e l'ordine
    # del pittore si conserva per costruzione.
    #
    # Sono gli argomenti ESATTI di list_run_c() / gather_poly() in
    # body_draw.c, che sono i modelli. EM_MODE e' a runtime (0 = flusso S34,
    # 1 = lista): la bisezione BODY_EMIT_ON_GPU=0 rimette il percorso
    # vecchio sulla stessa cartuccia senza toccare il kernel.
    # ---------------------------------------------------------------------
    ("EM_MODE",  "emissione: 0 = flusso (S34), 1 = la GPU cammina wlist (S46)"),
    ("EM_LIST",  "emissione: base di wlist (coppie chiave, idx<<16|off)"),
    ("EM_LIVE",  "emissione: quante voci vive ha wlist"),
    ("EM_POLYS", "emissione: intestazioni dei poligoni JBD, 8 byte l'una"),
    ("EM_NPOLY", "emissione: quanti poligoni ha il corpo (confine entita')"),
    ("EM_CORNERS", "emissione: angoli JBD, un long l'uno (normale<<16|vertice)"),
    ("EM_WSCR",  "emissione: record di schermo, 2 long per vertice (= wscr)"),
    ("EM_WINT",  "emissione: intensita', una word per normale (= wint)"),
    ("EM_NPTS",  "emissione: nb_points del corpo (guardia degli indici)"),
    ("EM_NNORM", "emissione: nb_normals (guardia delle normali)"),
    # I poligoni DISEGNATI non hanno un argomento: il kernel lascia il totale
    # corrente dei pixel nella CHIAVE MORTA di ogni voce di wlist (la chiave
    # del pittore e' dato consumato dopo il sort) e il 68000 li conta
    # confrontando totali consecutivi, come faceva coi totali del flusso.
    ("EM_SAWL",  "emissione: modo<<16|live LETTI dalla GPU (diagnostica)"),
]

# S46: l'AREA DI REGIA del modo lista - un lavoro alla volta, nella stessa
# forma del flusso (4 long di intestazione + 16 vertici da 2 long). La fase E
# la riempie leggendo corpo/wscr/wint e poi salta nella fase A esistente con
# il puntatore qui: le fasi A e B, provate sul ferro da S34/S44, non cambiano
# di una riga. Sta dentro GPU_VARS, DOPO il descrittore di posa: e' DRAM
# apposta - nella SRAM i tre kernel non lasciano 144 byte, e la fase A legge
# i vertici dalla DRAM da sempre (fino a S45 leggeva il flusso).
EM_STAGE_OFF   = 0x420
EM_STAGE_LONGS = GPU_JOB_HDR_LONGS + GPU_EDGE_MAX * GPU_VTX_LONGS


def check():
    """Contiguita', allineamento, capienza. Alza AssertionError al primo guaio."""
    prev_end = 0
    for name, base, size, _ in DRAM:
        assert base == prev_end, (
            "DRAM: buco o sovrapposizione prima di %s "
            "(atteso $%06X, dichiarato $%06X)" % (name, prev_end, base))
        assert size > 0 and base % 8 == 0, "DRAM: %s non allineata a phrase" % name
        prev_end = base + size
    assert prev_end == DRAM_SIZE, (
        "DRAM: totale $%06X != $%06X" % (prev_end, DRAM_SIZE))

    op_base, op_size = region("OP_LIST")
    prev_end = op_base
    for name, base, size, _ in OP_SLOTS:
        assert base == prev_end, "OP_LIST: buco prima di %s" % name
        assert base % 8 == 0, "OP_LIST: %s non allineato a phrase" % name
        prev_end = base + size
    assert prev_end <= op_base + op_size, "OP_LIST: slot oltre la regione"

    # Il fondo deve starci per intero nella sua regione.
    _, bg_size = region("BG_FB")
    assert BG_W * BG_H <= bg_size, "BG_FB troppo piccola per %dx%d" % (BG_W, BG_H)

    _, hnd_size = region("BRICK_HANDLES")
    assert BRICK_SLOTS * 4 <= hnd_size, (
        "BRICK_HANDLES: %d slot x 4 byte non entrano in $%X" % (BRICK_SLOTS, hnd_size))
    _, bmp_size = region("BRICK_USED_BMP")
    assert (BRICK_SLOTS + 7) // 8 <= bmp_size, "BRICK_USED_BMP piccola"

    _, cell_size = region("CELL_LIST")
    assert CELL_SLOTS * CELL_BYTES <= cell_size, (
        "CELL_LIST: %d celle x %d byte non entrano in $%X"
        % (CELL_SLOTS, CELL_BYTES, cell_size))

    _, body_size = region("BODY_WORK")
    body_need = (BODY_MAX_VERTS * BODY_VERT_BYTES
                 + BODY_MAX_NORMALS * BODY_NORMAL_BYTES
                 + BODY_MAX_POLYS * BODY_POLY_BYTES
                 + BODY_MAX_GROUPS * BODY_GROUP_BYTES)
    assert body_need <= body_size, (
        "BODY_WORK: servono %d byte, la regione ne ha $%X" % (body_need, body_size))

    gpu_base, gpu_size = region("GPU_VARS")
    args_end = gpu_slots()[-1][1] + 4
    assert args_end <= gpu_base + POSE_DESC_OFF, (
        "GPU_VARS: gli argomenti arrivano a $%06X, il descrittore di posa "
        "comincia a $%06X" % (args_end, gpu_base + POSE_DESC_OFF))
    desc_end = gpu_base + POSE_DESC_OFF + POSE_DESC_MAX * POSE_DESC_LONGS * 4
    assert desc_end <= gpu_base + EM_STAGE_OFF, (
        "GPU_VARS: il descrittore di posa arriva a $%06X, l'area di regia "
        "comincia a $%06X" % (desc_end, gpu_base + EM_STAGE_OFF))
    stage_end = gpu_base + EM_STAGE_OFF + EM_STAGE_LONGS * 4
    assert stage_end <= gpu_base + gpu_size, (
        "GPU_VARS: l'area di regia arriva a $%06X, la regione finisce "
        "a $%06X" % (stage_end, gpu_base + gpu_size))
    assert POSE_DESC_MAX >= BODY_MAX_GROUPS + 1, (
        "POSE_DESC_MAX deve coprire MAXG gruppi piu' il caso senza gruppi")

    # La SRAM della GPU: indirizzi crescenti, dentro i 4 KB. La tabella dei
    # bordi e' l'ultima regione e la sua fine e' calcolata, non dichiarata.
    prev = 0xF03000
    for name, addr, _ in GPU_SRAM:
        assert addr >= prev, "GPU_SRAM: %s a $%06X torna indietro" % (name, addr)
        prev = addr
    etab = dict((n, a) for n, a, _ in GPU_SRAM)["GPU_SRAM_ETAB"]
    assert etab + GPU_EDGE_MAX * GPU_EDGE_LONGS * 4 <= 0xF04000, (
        "GPU_SRAM: la tabella dei bordi sfora i 4 KB")

    # Il flusso deve contenere ALMENO il poligono piu' grosso possibile piu' il
    # terminatore, altrimenti lo svuotamento a pezzi non converge: un poligono
    # che non entra nemmeno in un flusso vuoto non verrebbe mai disegnato.
    _, job_size = region("JOB_STREAM")
    job_min = ((GPU_JOB_HDR_LONGS + GPU_EDGE_MAX * GPU_VTX_LONGS) * 4
               + GPU_JOB_HDR_LONGS * 4)
    assert job_min <= job_size, (
        "JOB_STREAM: il poligono piu' grosso piu' il terminatore sono %d byte, "
        "la regione ne ha $%X" % (job_min, job_size))

    _, spr_size = region("ACTOR_SPRITES")
    assert ACTOR_SPRITE_COUNT * ACTOR_SPRITE_SIZE <= spr_size, "ACTOR_SPRITES piccola"
    # I banchi devono starci TUTTI: e' l'unico posto in cui il numero di banchi
    # e l'altezza del buffer si incontrano, quindi e' qui che un aumento di
    # SPR_ROWS deve fermarsi invece di scoprirsi come un attore che scrive
    # dentro l'arena del C.
    assert SPR_BANKS * SPR_BYTES <= spr_size, (
        "ACTOR_SPRITES: %d banchi da %d byte non entrano in $%X"
        % (SPR_BANKS, SPR_BYTES, spr_size))
    assert ACTOR_SPRITE_COUNT * 16 <= dict(
        (n, s) for n, _, s, _ in OP_SLOTS)["OP_ACTORS"], "OP_ACTORS piccola"

    prev_end = 0
    for name, off, size, _ in CART:
        assert off == prev_end, "CART: buco o sovrapposizione prima di %s" % name
        prev_end = off + size
    assert prev_end == CART_SIZE, "CART: totale %d != %d" % (prev_end, CART_SIZE)

    # HAL_FIELDS: nessuna sovrapposizione, allineamento naturale, dentro la
    # regione. I campi possono avere buchi (l'allineamento e' esplicito), quindi
    # qui NON si pretende contiguita': si pretende che non si pestino i piedi.
    _, hal_size = region("HAL_VARS")
    occupied = {}
    for name, off, size, _ in HAL_FIELDS:
        assert size in (1, 2, 4), "HAL_VARS: %s ha size %d" % (name, size)
        assert off % size == 0, (
            "HAL_VARS: %s a $%02X non e' allineato a %d byte" % (name, off, size))
        assert off + size <= hal_size, "HAL_VARS: %s oltre la regione" % name
        for b in range(off, off + size):
            assert b not in occupied, (
                "HAL_VARS: %s si sovrappone a %s al byte $%02X"
                % (name, occupied[b], b))
            occupied[b] = name


def region(name):
    for n, base, size, _ in DRAM:
        if n == name:
            return base, size
    for n, base, size, _ in OP_SLOTS:
        if n == name:
            return base, size
    raise KeyError(name)


def cart_region(name):
    for n, off, size, _ in CART:
        if n == name:
            return CART_BASE + off, off, size
    raise KeyError(name)


BANNER_ASM = (";" + "-" * 74 + "\n"
              "; GENERATO DA tools/memory_map.py - NON MODIFICARE A MANO.\n"
              "; Ogni modifica va fatta nel master Python e rigenerata.\n"
              ";" + "-" * 74 + "\n")


def gpu_slots():
    """Gli argomenti dentro GPU_VARS. I BORDI non stanno piu' qui: da S34 sono
    dentro il flusso in JOB_STREAM, che e' quattro volte questa regione."""
    base, _ = region("GPU_VARS")
    return [(name, base + i * 4, comment)
            for i, (name, comment) in enumerate(GPU_ARGS)]


def gen_dram_inc():
    out = [BANNER_ASM, "; Mappa DRAM JagLBA V6 (2 MB)\n\n"]
    for name, base, size, comment in DRAM:
        out.append("%-20s EQU $%06X        ; %s\n" % (name, base, comment))
        out.append("%-20s EQU $%06X\n" % (name + "_SIZE", size))
        out.append("%-20s EQU $%06X\n\n" % (name + "_END", base + size))
    out.append("; Cima dello stack = fine della DRAM (il 68000 pre-decrementa).\n")
    out.append("%-20s EQU $%06X\n\n" % ("STACK_TOP", DRAM_SIZE))
    out.append("; Slot dentro la OP list\n")
    for name, base, size, comment in OP_SLOTS:
        out.append("%-20s EQU $%06X        ; %s\n" % (name, base, comment))
    out.append("\n; Campi dentro HAL_VARS: superficie di contatto RMAC <-> blob C.\n")
    out.append("; Gli stessi offset escono in engine_c/mem_map.h come JAG_HAL_*.\n")
    for name, off, size, comment in HAL_FIELDS:
        out.append("%-20s EQU HAL_VARS+$%02X    ; %s\n" % (name, off, comment))
    out.append("\n; Argomenti della GPU TOM: superficie di contatto blob C <-> src/gpu_body.s.\n")
    out.append("; Gli stessi indirizzi escono in engine_c/mem_map.h come JAG_GPU_*.\n")
    for name, off, comment in gpu_slots():
        out.append("%-20s EQU $%06X        ; %s\n" % ("GPU_" + name, off, comment))
    out.append("\n; S47: lo sprite dell'attore (OP layered). Righe larghe come il\n"
               "; fondo, cosi' il kernel GPU resta quello provato sul ferro.\n")
    out.append("%-20s EQU %d\n" % ("SPR_PITCH", SPR_PITCH))
    out.append("%-20s EQU %d\n" % ("SPR_ROWS", SPR_ROWS))
    out.append("%-20s EQU %d\n" % ("SPR_VIS_W", SPR_VIS_W))
    out.append("%-20s EQU $%X\n" % ("SPR_BANK_BYTES", SPR_BYTES))
    out.append("%-20s EQU %d\n\n" % ("SPR_BANKS", SPR_BANKS))
    out.append("%-20s EQU %d\n" % ("GPU_EDGE_LONGS", GPU_EDGE_LONGS))
    out.append("%-20s EQU %d\n" % ("GPU_VTX_LONGS", GPU_VTX_LONGS))
    out.append("%-20s EQU %d\n" % ("GPU_EDGE_MAX", GPU_EDGE_MAX))
    out.append("%-20s EQU %d\n" % ("GPU_JOB_HDR_LONGS", GPU_JOB_HDR_LONGS))
    gpu_base, _ = region("GPU_VARS")
    out.append("%-20s EQU $%06X        ; descrittore di posa (S45)\n"
               % ("GPU_POSE_DESC", gpu_base + POSE_DESC_OFF))
    out.append("%-20s EQU %d\n" % ("GPU_POSE_DESC_MAX", POSE_DESC_MAX))
    out.append("%-20s EQU %d\n" % ("GPU_POSE_DESC_LONGS", POSE_DESC_LONGS))
    out.append("%-20s EQU $%06X        ; area di regia del modo lista (S46)\n"
               % ("GPU_EM_STAGE", gpu_base + EM_STAGE_OFF))
    out.append("%-20s EQU %d\n" % ("GPU_EM_STAGE_LONGS", EM_STAGE_LONGS))
    out.append("\n; La SRAM della GPU: ingressi dei kernel e celle fisse (S44).\n")
    out.append("; Ogni kernel chiude con una .assert contro il proprio confine.\n")
    for name, addr, comment in GPU_SRAM:
        out.append("%-20s EQU $%06X        ; %s\n" % (name, addr, comment))
    out.append("\n; Geometria del fondo\n")
    out.append("%-20s EQU %d\n" % ("BG_W", BG_W))
    out.append("%-20s EQU %d\n" % ("BG_H", BG_H))
    out.append("\n; Sprite attore\n")
    out.append("%-20s EQU %d\n" % ("ACTOR_SPRITE_COUNT", ACTOR_SPRITE_COUNT))
    out.append("%-20s EQU $%X\n" % ("ACTOR_SPRITE_SIZE", ACTOR_SPRITE_SIZE))
    return "".join(out)


def gen_cart_inc():
    out = [BANNER_ASM, "; Layout della cartuccia JagLBA V6 (6 MB)\n"
           "; Gli indirizzi sono FISSI: li assegna il packer Python, non RMAC.\n\n"]
    out.append("%-20s EQU $%06X\n\n" % ("CART_BASE", CART_BASE))
    for name, off, size, comment in CART:
        out.append("%-20s EQU $%06X        ; %s\n" % (name, CART_BASE + off, comment))
        out.append("%-20s EQU $%06X\n" % (name + "_OFF", off))
        out.append("%-20s EQU $%06X\n\n" % (name + "_SIZE", size))
    cb, _, _ = cart_region("ROM_CBLOB")
    out.append("; Tabella di entry del blob C: jmp abs.l = 6 byte per voce.\n")
    out.append("%-20s EQU $%06X        ; engine_init() -> d0 = magic\n" % ("C_ENTRY_INIT", cb))
    out.append("%-20s EQU $%06X        ; engine_frame()\n" % ("C_ENTRY_FRAME", cb + 6))
    out.append("%-20s EQU $%06X        ; engine_vbl(), from ViHandler (S51)\n" % ("C_ENTRY_VBL", cb + 12))
    out.append("%-20s EQU $900DF00D\n" % "C_ENGINE_MAGIC")
    return "".join(out)


def gen_linker():
    rom_addr, _, rom_size = cart_region("ROM_CBLOB")
    ram_base, ram_size = region("C_ARENA")
    return """/* GENERATO DA tools/memory_map.py - NON MODIFICARE A MANO.
 *
 * Split ROM/RAM del blob C dell'engine (docs/skeleton_design.md 3.1):
 * il 68000 esegue dalla cartuccia in place, quindi .text e .rodata restano
 * in ROM. Solo .data (copiata al boot da crt0) e .bss stanno in DRAM.
 */

OUTPUT_ARCH(m68k)
ENTRY(entry_table)

MEMORY
{
    ROM (rx)  : ORIGIN = 0x%06X, LENGTH = 0x%06X
    RAM (rw)  : ORIGIN = 0x%06X, LENGTH = 0x%06X
}

SECTIONS
{
    .text : {
        *(.entry)
        *(.text*)
        *(.rodata*)
        . = ALIGN(2);
    } > ROM

    /* .data: VMA in DRAM, LMA in ROM. crt0 copia da __data_lma a __data_start. */
    .data : AT (ADDR(.text) + SIZEOF(.text)) {
        . = ALIGN(2);
        __data_start = .;
        *(.data*)
        . = ALIGN(2);
        __data_end = .;
    } > RAM
    __data_lma = LOADADDR(.data);

    .bss (NOLOAD) : {
        . = ALIGN(2);
        __bss_start = .;
        *(.bss*)
        *(COMMON)
        . = ALIGN(2);
        __bss_end = .;
    } > RAM

    __heap_start = __bss_end;
    __heap_end   = 0x%06X;

    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
""" % (rom_addr, rom_size, ram_base, ram_size, ram_base + ram_size)


def gen_header():
    out = ["/* GENERATO DA tools/memory_map.py - NON MODIFICARE A MANO. */\n",
           "#ifndef JAG_MEM_MAP_H\n#define JAG_MEM_MAP_H\n\n"]
    for name, base, size, comment in DRAM:
        out.append("#define JAG_%-18s 0x%08XUL  /* %s */\n" % (name, base, comment))
        out.append("#define JAG_%-18s 0x%08XUL\n" % (name + "_SIZE", size))
    for name, base, size, _ in OP_SLOTS:
        out.append("#define JAG_%-18s 0x%08XUL\n" % (name, base))
    out.append("\n/* Regioni della cartuccia. Il 68000 le legge come memoria\n"
               " * normale: il blob C gira da ROM_CBLOB e i dati stanno in\n"
               " * ROM_DATA, quindi HQR_Get torna puntatori qui dentro. */\n")
    for name, off, size, comment in CART:
        out.append("#define JAG_%-18s 0x%08XUL  /* %s */\n"
                   % (name, CART_BASE + off, comment))
        out.append("#define JAG_%-18s 0x%08XUL\n" % (name + "_SIZE", size))
    out.append("\n/* Campi dentro HAL_VARS, scritti dal lato RMAC.\n"
               " * Stessi offset delle EQU di include/dram_map.inc. */\n")
    for name, off, size, comment in HAL_FIELDS:
        ctype = {1: "unsigned char", 2: "unsigned short", 4: "unsigned long"}[size]
        out.append("#define JAG_HAL_%-14s (*(volatile %s *)(JAG_HAL_VARS + 0x%02XUL))"
                   "  /* %s */\n" % (name, ctype, off, comment))
    out.append("\n#define JAG_BG_W %d\n#define JAG_BG_H %d\n" % (BG_W, BG_H))
    out.append("#define JAG_BRICK_SLOTS %d\n" % BRICK_SLOTS)
    out.append("#define JAG_CELL_SLOTS %d\n" % CELL_SLOTS)
    out.append("\n/* Capienza di BODY_WORK, misurata su tutte le voci di BODY.HQR. */\n")
    out.append("#define JAG_BODY_MAX_VERTS   %d\n" % BODY_MAX_VERTS)
    out.append("#define JAG_BODY_MAX_NORMALS %d\n" % BODY_MAX_NORMALS)
    out.append("#define JAG_BODY_MAX_POLYS   %d\n" % BODY_MAX_POLYS)
    out.append("#define JAG_BODY_MAX_CORNERS %d\n" % BODY_MAX_CORNERS)
    out.append("#define JAG_BODY_MAX_GROUPS  %d\n" % BODY_MAX_GROUPS)
    out.append("\n/* S47: lo sprite dell'attore (OP layered). Righe larghe come\n"
               " * il fondo: e' quel che lascia intatto il kernel GPU. */\n")
    out.append("#define JAG_SPR_PITCH %d\n" % SPR_PITCH)
    out.append("#define JAG_SPR_ROWS  %d\n" % SPR_ROWS)
    out.append("#define JAG_SPR_VIS_W %d\n" % SPR_VIS_W)
    out.append("/* S49: due banchi. Si disegna in quello dietro e si scambiano\n"
               " * SPR_DATA_A/B quando l'immagine e' finita, cosi' l'OP non\n"
               " * legge mai un buffer a meta'. */\n")
    out.append("#define JAG_SPR_BANK_BYTES %dUL\n" % SPR_BYTES)
    out.append("#define JAG_SPR_BANKS %d\n" % SPR_BANKS)
    out.append("/* Gli slot della lista OP: il C compone le due phrase dello\n"
               " * sprite (dove sta l'attore lo sa lui), il gestore di\n"
               " * interrupt le ricopia a ogni campo. */\n")
    for name, off, _size, _comment in OP_SLOTS:
        out.append("#define JAG_%-13s 0x%08XUL\n" % (name, off))
    out.append("\n/* Argomenti della GPU TOM (M3c mossa 2). Stessi indirizzi che\n"
               " * include/dram_map.inc da' a RMAC come GPU_*. */\n")
    for name, off, comment in gpu_slots():
        out.append("#define JAG_GPU_%-9s 0x%08XUL  /* %s */\n"
                   % (name, off, comment))
    out.append("#define JAG_GPU_EDGE_LONGS %d\n" % GPU_EDGE_LONGS)
    out.append("#define JAG_GPU_VTX_LONGS  %d\n" % GPU_VTX_LONGS)
    out.append("#define JAG_GPU_EDGE_MAX   %d\n" % GPU_EDGE_MAX)
    out.append("#define JAG_GPU_JOB_HDR_LONGS %d\n" % GPU_JOB_HDR_LONGS)
    gpu_base, _ = region("GPU_VARS")
    out.append("\n/* Il descrittore di posa (S45): contratto di gpu_front.s. */\n")
    out.append("#define JAG_GPU_POSE_DESC 0x%08XUL\n" % (gpu_base + POSE_DESC_OFF))
    out.append("#define JAG_GPU_POSE_DESC_MAX   %d\n" % POSE_DESC_MAX)
    out.append("#define JAG_GPU_POSE_DESC_LONGS %d\n" % POSE_DESC_LONGS)
    out.append("\n/* L'area di regia del modo lista (S46): contratto della fase E. */\n")
    out.append("#define JAG_GPU_EM_STAGE 0x%08XUL\n" % (gpu_base + EM_STAGE_OFF))
    out.append("#define JAG_GPU_EM_STAGE_LONGS %d\n" % EM_STAGE_LONGS)
    out.append("\n/* La SRAM della GPU: ingressi dei kernel e celle fisse (S44). */\n")
    for name, addr, comment in GPU_SRAM:
        out.append("#define JAG_%-16s 0x%08XUL  /* %s */\n" % (name, addr, comment))
    out.append("#define JAG_ENGINE_MAGIC 0x900DF00DUL\n")
    out.append("\n#endif\n")
    return "".join(out)


TARGETS = [
    ("include/dram_map.inc", gen_dram_inc),
    ("include/cart_map.inc", gen_cart_inc),
    ("engine_c/jag_engine.ld", gen_linker),
    ("engine_c/mem_map.h", gen_header),
]


def main():
    check()
    if "--check" in sys.argv:
        print("memory_map: layout coerente")
        return
    for rel, fn in TARGETS:
        path = os.path.join(REPO, rel)
        d = os.path.dirname(path)
        if not os.path.isdir(d):
            os.makedirs(d)
        text = fn()
        with open(path, "w", newline="\n") as f:
            f.write(text)
        print("  scritto %s (%d byte)" % (rel, len(text)))
    spare = (region("SPARE_LO")[1] + region("SPARE_HI")[1] + region("BOOT_PAD")[1])
    print("memory_map: DRAM 2 MB, margine dichiarato libero %d KB" % (spare // 1024))


if __name__ == "__main__":
    main()
