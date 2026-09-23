/* sprite_draw.c - M3e: gli SPRITE degli attori, cioe' le porte.
 *
 * PERCHE' ESISTE UN FILE APPOSTA
 * ------------------------------
 * Perche' non e' un caso particolare dei corpi: e' l'ALTRO ramo di AffScene.
 * Il gioco mette corpi e sprite nella STESSA lista ordinata e li distingue con
 * TYPE_OBJ_3D / TYPE_OBJ_SPRITE (OBJECT.C:2646); qui il ciclo unico resta in
 * body_draw.c - che e' quello che possiede l'ordine di disegno - e questo file
 * possiede solo il "come si disegna uno sprite". Due cicli separati vorrebbero
 * dire due ordini di disegno, cioe' una porta davanti a chi ci passa dietro.
 *
 * 348 attori su 1118 in tutto il gioco sono sprite, di cui 138 porte. Fino a
 * S35 non se ne disegnava NESSUNO e nelle stanze costruite mancavano le porte.
 *
 * LE QUATTRO COSE, NELL'ORDINE DEL GIOCO (OBJECT.C:3041-3070)
 * -----------------------------------------------------------
 *   1. proietta l'ancora dell'attore, la STESSA dei corpi;
 *   2. l'indice dello sprite. Upstream sta in `Body` (InitSprite lo ci copia,
 *      OBJECT.C:1120); qui sta in `Sprite` e `Body` resta -1, come nel parser
 *      di riferimento - un campo che vuol dire due cose diverse a seconda di
 *      una bandiera e' una mina, e non ne abbiamo bisogno;
 *   3. l'angolo alto-sinistro NON e' l'ancora: e' l'ancora piu' uno scostamento
 *      PER SPRITE preso da PtrZvExtra, cioe' RESS.HQR voce 3 (PERSO.C:1533).
 *      Senza quella tabella ogni sprite sta nel posto sbagliato di una quantita'
 *      diversa - cioe' un guasto che NON somiglia a un errore di proiezione;
 *   4. il ritaglio: per le porte (SPRITE_CLIP) un rettangolo FISSO da Info0..3
 *      piu' l'origine del mondo proiettata; per gli altri il riquadro dello
 *      sprite, che non taglia niente.
 *
 * E' il rettangolo fisso a far sembrare che la porta rientri nello stipite: la
 * porta scorre lungo il suo asse e il ritaglio la mangia. Non e' un'animazione,
 * ed e' il motivo per cui va portato ADESSO e non con il movimento.
 *
 * LA TRASPARENZA E' UN OPCODE, NON UN COLORE
 * ------------------------------------------
 * Nel flusso RLE solo il salto lascia il pixel intatto: un letterale che vale
 * zero e' un pixel NERO da scrivere. Percio' si percorrono gli opcode e si
 * scrivono le tratte, invece di decodificare in un rettangolo e saltare gli
 * zeri. Sbagliare qui non da' uno sprite mancante: da' uno sprite bucato dove
 * il disegno e' nero, che sembra un problema di palette.
 *
 * COME SI VERIFICA
 * ----------------
 * tools/sprite_ref.py fa le stesse cose sugli stessi byte e genera tre sonde
 * dentro body_expect.h: SPR (quanti passano il ritaglio), SPR_XY (dove finisce
 * il primo, cioe' PtrZvExtra e la proiezione) e SPR_PIX (quanti pixel escono,
 * cioe' l'RLE e il rettangolo). Tre diagnosi diverse invece di tre volte la
 * stessa.
 */
#include "hal.h"
#include "le.h"

#ifndef SPRITE_FB_BASE
#define SPRITE_FB_BASE JAG_BG_FB
#endif

#define RESS_GOODIES_GPC 3          /* COMMON.H:6 */

T_SPRITE_STAT SpriteStat;

/* PtrZvExtra: otto WORD per sprite. Le prime due sono lo scostamento di
 * schermo, le altre sei la scatola di collisione che InitSprite copia
 * nell'attore. Si rilegge dalla cartuccia a ogni scena e non si copia in DRAM:
 * sono 118 sprite x 16 byte di sola lettura, e una copia sarebbe una seconda
 * verita' da tenere allineata. */
static const le_byte *zv_table(void)
{
    return (const le_byte *)HQR_Get(HQR_RESS, RESS_GOODIES_GPC);
}

/* GetDxDyGraph, GRAPH_A.ASM:302: la voce dell'archivio e' un BANCO, e in testa
 * ha una tabella di scostamenti a 32 bit, uno per grafica. Gli attori usano
 * sempre la grafica 0. */
static const le_byte *graph_at(const le_byte *bank, int num,
                               int *dx, int *dy)
{
    unsigned long off = LE32u(bank + num * 4);
    const le_byte *g = bank + off;
    *dx = g[0];
    *dy = g[1];
    return g + 4;                   /* dx, dy, hotx, hoty: QUATTRO byte */
}

/* AffGraph con ritaglio rettangolare (i massimi sono INCLUSI, come nel gioco).
 * Torna i pixel scritti. */
static ULONG aff_graph(const le_byte *bank, int num, WORD x0, WORD y0,
                       WORD cx0, WORD cy0, WORD cx1, WORD cy1)
{
    int dx, dy, r, k;
    const le_byte *p;
    ULONG written = 0;

    /* Anche questo scrive nel framebuffer, e in una stanza di soli sprite e' la
     * PRIMA scrittura della passata: la cancellazione dei rettangoli del
     * disegno precedente scatta qui come scatta al lancio della GPU. */
    Bg_ClsPending();

    p = graph_at(bank, num, &dx, &dy);

    for (r = 0; r < dy; r++) {
        int nb = *p++;
        WORD y = (WORD)(y0 + r);
        int vis = (y >= cy0 && y <= cy1);
        UBYTE *row = (UBYTE *)SPRITE_FB_BASE + (LONG)y * JAG_BG_W;
        WORD col = x0;

        while (nb--) {
            int op = *p++;
            int count = (op & 0x3F) + 1;
            int mode = op & 0xC0;

            if (mode == 0x00) {             /* salto: niente da scrivere */
                col = (WORD)(col + count);
                continue;
            }
            if (mode == 0x80) {             /* tratta di un colore solo */
                UBYTE pix = *p++;
                if (vis) {
                    for (k = 0; k < count; k++) {
                        WORD x = (WORD)(col + k);
                        if (x >= cx0 && x <= cx1) {
                            row[x] = pix;
                            written++;
                        }
                    }
                }
                col = (WORD)(col + count);
                continue;
            }
            if (vis) {                      /* byte letterali */
                for (k = 0; k < count; k++) {
                    WORD x = (WORD)(col + k);
                    if (x >= cx0 && x <= cx1) {
                        row[x] = p[k];
                        written++;
                    }
                }
            }
            p += count;
            col = (WORD)(col + count);
        }
    }
    return written;
}

/* Dove stava ogni sprite l'ultima volta. Un attore, una voce: l'indice e'
 * quello di ListObjet, quindi non c'e' niente da tenere in sincrono. */
static T_SPRBOX sprbox[MAX_OBJETS];

/* Il cambio di stanza azzera la memoria: gli attori sono altri, e un rettangolo
 * della stanza precedente direbbe "fermo" a uno sprite che non e' nemmeno lo
 * stesso oggetto. */
void Sprite_ForgetBoxes(void)
{
    int i;

    for (i = 0; i < MAX_OBJETS; i++)
        sprbox[i].valid = 0;
}

void Sprite_StatReset(void)
{
    SpriteStat.drawn = 0;
    SpriteStat.clipped = 0;
    SpriteStat.still = 0;
    SpriteStat.pixels = 0;
    SpriteStat.firstx = 0;
    SpriteStat.firsty = 0;
    SpriteStat.havefirst = 0;
}

/* Un attore SPRITE_3D, con l'ancora GIA' proiettata da chi possiede il ciclo.
 * `vx, vy` sono lo scostamento degli attori: l'origine del mondo proiettata
 * (XpOrgw/YpOrgw, GRILLE.C:668) e' ProjettePoint(0,0,0), cioe' esattamente
 * (vx, vy) - non lo scostamento delle tessere, che ha un altro centro. */
void Sprite_DrawOne(const T_OBJET *o, WORD ax, WORD ay, LONG vx, LONG vy)
{
    const le_byte *zv = zv_table();
    const le_byte *bank;
    int num = o->Sprite;
    int dx, dy;
    WORD x0, y0;
    LONG cx0, cy0, cx1, cy1;
    ULONG px;

    if (num < 0 || zv == 0)
        return;
    bank = (const le_byte *)HQR_Get(HQR_SPRITES, num);
    if (bank == 0)
        return;

    x0 = (WORD)(ax + LE16(zv + (num * 8 + 0) * 2));
    y0 = (WORD)(ay + LE16(zv + (num * 8 + 1) * 2));
    (void)graph_at(bank, 0, &dx, &dy);

    if (o->Flags & SPRITE_CLIP) {
        cx0 = (LONG)o->Info  + vx;
        cy0 = (LONG)o->Info1 + vy;
        cx1 = (LONG)o->Info2 + vx;
        cy1 = (LONG)o->Info3 + vy;
    } else {
        cx0 = x0;          cy0 = y0;
        cx1 = x0 + dx;     cy1 = y0 + dy;
    }

    /* SetClip (INITSVGA.ASM:234): i minimi non scendono sotto zero, i massimi
     * non superano l'ultima riga/colonna del BUFFER. Su DOS quel limite era
     * 640x480; qui e' 640x592, e si copia la regola non il numero. */
    if (cx0 < 0) cx0 = 0;
    if (cy0 < 0) cy0 = 0;
    if (cx1 > JAG_BG_W - 1) cx1 = JAG_BG_W - 1;
    if (cy1 > JAG_BG_H - 1) cy1 = JAG_BG_H - 1;
    if (cx0 > cx1 || cy0 > cy1) {
        SpriteStat.clipped++;
        return;
    }

    /* -----------------------------------------------------------------------
     * QUEL CHE NON SI MUOVE NON SI CANCELLA (S42).
     *
     * IL SINTOMO: a pochi quadri al secondo si vedeva un lampo di fondo fra una
     * posa e l'altra, e si vedeva anche SULLE PORTE - che non si muovono. Con
     * un solo framebuffer la cancellazione e il ridisegno sono due momenti
     * distinti che l'Object Processor mostra entrambi; su una cosa ferma quei
     * due momenti non producono NIENTE di nuovo, sono lampeggio e basta.
     *
     * LA REGOLA, in tre stati:
     *
     *   - fermo e non sporcato da nessuno -> non si fa niente. Zero pixel, zero
     *     lampo, e nessun rettangolo accodato;
     *   - fermo ma qualcuno gli e' passato sopra (o il fondo e' cambiato) -> si
     *     ridisegna, ma NON si accoda il rettangolo. Accodarlo vorrebbe dire
     *     farsi cancellare al quadro dopo, quindi ridisegnare, quindi
     *     riaccodare: lampeggio perpetuo, e sarebbe stato il difetto peggiore
     *     dei due perche' si presenta come "il primo tentativo non e' servito";
     *   - MOSSO -> si ridisegna e si accoda, come sempre. E si accoda anche
     *     DOV'ERA, perche' se il quadro prima era fermo quel rettangolo non
     *     l'ha accodato nessuno e i suoi pixel resterebbero li' come una scia.
     *
     * Il confronto e' col rettangolo, non con una bandiera "e' una porta": il
     * giorno che una porta si apre davvero (M5, l'anta si sposta con AnimStep)
     * il suo rettangolo cambia e la regola la tratta da mobile senza che
     * nessuno debba ricordarsi di aggiornare un elenco di eccezioni.
     * --------------------------------------------------------------------- */
    {
        int n = (int)(o - ListObjet);
        T_SPRBOX *m = (n >= 0 && n < MAX_OBJETS) ? &sprbox[n] : 0;
        int same;

        if (m) {
            same = m->valid && m->x0 == (WORD)cx0 && m->y0 == (WORD)cy0
                            && m->x1 == (WORD)cx1 && m->y1 == (WORD)cy1;

            if (same && !Bg_BoxDamaged((WORD)cx0, (WORD)cy0,
                                       (WORD)cx1, (WORD)cy1)) {
                SpriteStat.still++;
                return;
            }

            if (!same) {
                if (m->valid)
                    Bg_AddBox(m->x0, m->y0, m->x1, m->y1);   /* dov'ERA */
                m->x0 = (WORD)cx0; m->y0 = (WORD)cy0;
                m->x1 = (WORD)cx1; m->y1 = (WORD)cy1;
                m->valid = 1;
                m->moved = 1;
            } else {
                m->moved = 0;
            }
        }

    px = aff_graph(bank, 0, x0, y0,
                   (WORD)cx0, (WORD)cy0, (WORD)cx1, (WORD)cy1);

    SpriteStat.drawn++;
    SpriteStat.pixels += px;
    if (!SpriteStat.havefirst) {
        SpriteStat.havefirst = 1;
        SpriteStat.firstx = x0;
        SpriteStat.firsty = y0;
    }

    /* M3d per gli sprite, OBJECT.C:3072-3090. Due differenze dai corpi, e la
     * prima e' quella che fa stare una porta davanti a chi ci passa dietro:
     *
     *  - una PORTA (SPRITE_CLIP) si posiziona con AnimStepX/Y/Z, cioe' dov'e'
     *    l'ANTA adesso, non lo stipite. Gli altri sprite usano PosObj piu' il
     *    loro Xmax/Zmax, cioe' lo spigolo lontano della loro ZV.
     *  - il `ym++` per la collisione lo fa solo il ramo senza SPRITE_CLIP: nel
     *    ramo della porta il gioco lo ha COMMENTATO. Si copia com'e'.
     *
     * E la variante e' DrawOverBrick3, che prova gx e gz separati invece della
     * loro somma. */
    {
        WORD xm, ym, zm;

        if (o->Flags & SPRITE_CLIP) {
            xm = (WORD)((o->AnimStepX + DEMI_BRICK_XZ) / SIZE_BRICK_XZ);
            ym = (WORD)(o->AnimStepY / SIZE_BRICK_Y);
            zm = (WORD)((o->AnimStepZ + DEMI_BRICK_XZ) / SIZE_BRICK_XZ);
        } else {
            /* Xmax e Zmax sono la ZV dello sprite, che InitSprite
             * (OBJECT.C:1122-1130) copia da PtrZvExtra[num*8 + 2..7]. Si
             * leggono dalla stessa tabella che sta gia' in mano qui sopra
             * invece di duplicarle dentro T_OBJET: un campo copiato e' un
             * campo che si puo' dimenticare di riempire. */
            WORD zxmax = LE16(zv + (num * 8 + 3) * 2);
            WORD zzmax = LE16(zv + (num * 8 + 7) * 2);

            xm = (WORD)((o->PosObjX + zxmax + DEMI_BRICK_XZ) / SIZE_BRICK_XZ);
            ym = (WORD)(o->PosObjY / SIZE_BRICK_Y);
            zm = (WORD)((o->PosObjZ + zzmax + DEMI_BRICK_XZ) / SIZE_BRICK_XZ);
            if (o->Col & 127)
                ym++;
        }
        Bg_DrawOverBrick3(xm, ym, zm,
                          (WORD)cx0, (WORD)cy0, (WORD)cx1, (WORD)cy1);
    }

    /* AddPhysBox anche qui, e non e' un di piu': un attore SPRITE sporca il
     * fondo come un corpo (OBJECT.C:3078 e 3089 chiamano le due funzioni una
     * accanto all'altra). Dimenticarlo lascerebbe una scia sulle sole porte -
     * cioe' il difetto piu' difficile da attribuire, perche' comparirebbe solo
     * nelle stanze che ne hanno una che si muove.
     *
     * MA SOLO SE SI E' MOSSO (S42): un rettangolo accodato e' una richiesta di
     * cancellazione per il quadro prossimo, e chi non si muove non ha niente da
     * far cancellare. Vedi il blocco a tre stati qui sopra. */
        if (!m || m->moved)
            Bg_AddBox((WORD)cx0, (WORD)cy0, (WORD)cx1, (WORD)cy1);
    }
}
