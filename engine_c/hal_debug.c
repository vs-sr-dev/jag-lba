/* hal_debug.c - font esadecimale e righe di diagnostica sul fondo.
 *
 * PERCHE' UN FONT E NON I SOLITI QUADRATINI
 * -----------------------------------------
 * In M0 un valore a 32 bit si leggeva come 32 quadratini, uno per bit. Ha
 * funzionato, ma male: ogni lettura andava allineata a mano contro una riga di
 * taratura, e un disallineamento si scambiava per un dato (sta scritto nel
 * commento di DEBUG_READOUT che si e' portato via). Da M1 in poi i valori da
 * guardare diventano molti e non piu' uno alla volta, quindi il font si paga
 * da se': 512 byte di ROM contro un intero strumento di misura.
 *
 * Resta valida la lezione di S24 ([[feedback_debug_border_milestones]]): non
 * fidarsi dei colori. Qui il dato e' una FORMA, non una tinta.
 *
 * PERCHE' 2x IN VERTICALE
 * -----------------------
 * Il display e' 480i: le righe pari e le dispari stanno in campi diversi, a 30
 * Hz ciascuno. Un glifo alto 8 righe di framebuffer avrebbe metа' delle sue
 * righe in un campo e metа' nell'altro, cioe' sfarfallerebbe a 30 Hz (twitter).
 * Raddoppiando in verticale ogni riga del glifo finisce in ENTRAMBI i campi con
 * lo stesso contenuto, e il glifo sta fermo. Il 2x orizzontale invece serve
 * perche' a PWIDTH2 i pixel sono larghi mezzo pixel quadrato: senza, i glifi
 * verrebbero schiacciati.
 */
#include "hal.h"
#include "bg_expect.h"

static unsigned char *const fb = (unsigned char *)JAG_BG_FB;

/* Indici nella CLUT.
 *
 * In M1 erano 255 e 160 sulla rampa di grigi del lato RMAC. Da M2 la CLUT e'
 * la palette di LBA1 e quei due indici sono COLORI DELLA SCENA: 255 e' un
 * bianco che i brick usano davvero, quindi la diagnostica scriverebbe con un
 * colore che appartiene al disegno.
 *
 * La diagnostica si prende invece due dei quattordici slot che LBA1 riserva
 * alla UI riempiendoli di bianco. Che siano liberi non e' un'ipotesi: e'
 * misurato brick per brick da `bg_ref.py --check-clut`, e i due valori
 * arrivano da la' insieme alle altre attese, cosi' non possono divergere. */
#define COL_FG    BG_DBG_IDX_FG
#define COL_BG      0
#define COL_LABEL BG_DBG_IDX_LBL

/* Font esadecimale 8x8, bit 7 = colonna di sinistra. Sedici glifi, uno per
 * cifra: e' tutto quello che serve per leggere valori, e restare a 16 glifi
 * significa poterli scrivere e ricontrollare a occhio uno per uno. */
static const unsigned char hexfont[16][8] = {
    { 0x3C, 0x66, 0x66, 0x66, 0x66, 0x66, 0x3C, 0x00 },  /* 0 */
    { 0x18, 0x38, 0x18, 0x18, 0x18, 0x18, 0x3C, 0x00 },  /* 1 */
    { 0x3C, 0x66, 0x06, 0x0C, 0x30, 0x60, 0x7E, 0x00 },  /* 2 */
    { 0x3C, 0x66, 0x06, 0x1C, 0x06, 0x66, 0x3C, 0x00 },  /* 3 */
    { 0x0C, 0x1C, 0x3C, 0x6C, 0x7E, 0x0C, 0x0C, 0x00 },  /* 4 */
    { 0x7E, 0x60, 0x7C, 0x06, 0x06, 0x66, 0x3C, 0x00 },  /* 5 */
    { 0x1C, 0x30, 0x60, 0x7C, 0x66, 0x66, 0x3C, 0x00 },  /* 6 */
    { 0x7E, 0x06, 0x0C, 0x18, 0x18, 0x18, 0x18, 0x00 },  /* 7 */
    { 0x3C, 0x66, 0x66, 0x3C, 0x66, 0x66, 0x3C, 0x00 },  /* 8 */
    { 0x3C, 0x66, 0x66, 0x3E, 0x06, 0x0C, 0x38, 0x00 },  /* 9 */
    { 0x18, 0x3C, 0x66, 0x66, 0x7E, 0x66, 0x66, 0x00 },  /* A */
    { 0x7C, 0x66, 0x66, 0x7C, 0x66, 0x66, 0x7C, 0x00 },  /* B */
    { 0x3C, 0x66, 0x60, 0x60, 0x60, 0x66, 0x3C, 0x00 },  /* C */
    { 0x78, 0x6C, 0x66, 0x66, 0x66, 0x6C, 0x78, 0x00 },  /* D */
    { 0x7E, 0x60, 0x60, 0x7C, 0x60, 0x60, 0x7E, 0x00 },  /* E */
    { 0x7E, 0x60, 0x60, 0x7C, 0x60, 0x60, 0x60, 0x00 },  /* F */
};

/* Geometria delle righe di diagnostica. Sotto il marcatore di vita (y 8..64),
 * che resta dov'era in M0. */
#define GLYPH_W    16               /* 8 pixel di font a 2x */
#define GLYPH_H    16
#define DBG_Y0     80
#define DBG_STEP   20
#define DBG_LBL_X   8               /* cifra dell'indice di riga */
#define DBG_VAL_X  40               /* le otto cifre del valore  */

/* TRE COLONNE da 16 righe. Una riga occupa l'etichetta piu' 8 cifre a 16 px =
 * 168 px, quindi tre colonne da 210 finiscono a 588 su 640.
 *
 * Non e' un vezzo: le righe del fondo servono ACCANTO a quelle della scena e a
 * quelle di HQR, non al loro posto - una sonda del fondo che cade mentre le
 * altre tengono dice una cosa diversa da tutte che cadono, ed e' precisamente
 * la distinzione che serve. Allungare in verticale invece sfonderebbe le 480
 * righe visibili (16 righe vanno da y 80 a 380; 48 arriverebbero a 1020).
 *
 * L'etichetta resta una cifra sola perche' e' `riga & 15`: la colonna la dice
 * la posizione. */
#define DBG_PER_COL 16
#define DBG_COL_W  210

void Dbg_Fill(int x, int y, int w, int h, UBYTE colour)
{
    unsigned char *row = fb + (ULONG)y * JAG_BG_W + x;
    int j;

    for (j = 0; j < h; j++) {
        int i;
        for (i = 0; i < w; i++)
            row[i] = colour;
        row += JAG_BG_W;
    }
}

/* Un glifo a 2x. Ogni riga del font produce due righe di framebuffer (una per
 * campo) e ogni bit due byte. */
static void glyph(int x, int y, int digit, UBYTE colour)
{
    const unsigned char *src = hexfont[digit & 15];
    unsigned char *row = fb + (ULONG)y * JAG_BG_W + x;
    int r;

    for (r = 0; r < 8; r++) {
        unsigned char bits = src[r];
        int c;
        for (c = 0; c < 8; c++) {
            UBYTE v = (bits & (0x80 >> c)) ? colour : COL_BG;
            row[c * 2]                 = v;
            row[c * 2 + 1]             = v;
            row[JAG_BG_W + c * 2]      = v;
            row[JAG_BG_W + c * 2 + 1]  = v;
        }
        row += JAG_BG_W * 2;
    }
}

/* Ultimo valore disegnato per riga, per non ridisegnare quel che non cambia.
 * Il flag separato serve perche' 0 e' un valore legittimo: senza, una riga che
 * vale zero verrebbe ridisegnata a ogni quadro. */
static ULONG shown[DBG_ROWS];
static UBYTE shown_valid[DBG_ROWS];

/* Invalida la cache, NON pulisce lo schermo.
 *
 * In M1 riempiva di nero la fascia delle righe. Da M2 sotto c'e' la scena, e
 * una fascia nera larga 640 le passerebbe attraverso. Non serve comunque: ogni
 * glifo disegna anche i propri pixel spenti, quindi una cifra e' un rettangolo
 * opaco 16x16 e resta leggibile sopra qualunque fondo; fra una cifra e l'altra
 * si vede la scena, che e' un difetto minore dell'aver cancellato la scena.
 *
 * Il mestiere che resta - dimenticare quel che e' gia' a schermo - e' proprio
 * quello che serve dopo Bg_Redraw: il fondo ridisegnato ha cancellato le
 * righe, e senza invalidare la cache Dbg_Value crederebbe di averle gia'
 * scritte e non le riscriverebbe mai piu'. */
void Dbg_Clear(void)
{
    int i;

    for (i = 0; i < DBG_ROWS; i++)
        shown_valid[i] = 0;
}

void Dbg_Value(int row, ULONG value)
{
    int x0, y, i;

    if (row < 0 || row >= DBG_ROWS)
        return;
    if (shown_valid[row] && shown[row] == value)
        return;
    shown[row] = value;
    shown_valid[row] = 1;

    x0 = (row / DBG_PER_COL) * DBG_COL_W;
    y  = DBG_Y0 + (row % DBG_PER_COL) * DBG_STEP;
    /* L'indice della riga in grigio medio: identifica la riga anche quando le
     * altre sono vuote, che e' il caso in cui serve di piu'. */
    glyph(x0 + DBG_LBL_X, y, row & 15, COL_LABEL);
    for (i = 0; i < 8; i++)
        glyph(x0 + DBG_VAL_X + i * GLYPH_W, y,
              (int)((value >> ((7 - i) * 4)) & 15), COL_FG);
}
