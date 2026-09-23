/* le.h - lettura dei dati LBA1 dalla cartuccia: little endian e senza
 * vincoli di allineamento.
 *
 * LA DECISIONE, E PERCHE' NON CE N'ERA UN'ALTRA
 * --------------------------------------------
 * M1 aveva lasciato aperta la scelta fra convertire i dati offline (swap
 * LE->BE nel packer) e leggerli con accessori a runtime. M2 la chiude, e non
 * per gusto: la struttura dei dati la decide.
 *
 * Le struct di LBA1 alternano campi da un byte e campi da due (in SCENE,
 * `CubeStartX` segue il singolo byte `CubeJingle`), percio' le word cadono a
 * offset DISPARI. Sul 386 del 1994 non costava niente. Sul 68000 una `move.w`
 * a indirizzo dispari e' un Address Error - la stessa eccezione che ha ucciso
 * la V4, che oggi darebbe tre lampi invece di uno schermo nero, ma resta un
 * arresto.
 *
 * Quindi il 68000 deve leggere quelle word **byte per byte comunque**, per
 * allineamento, qualunque sia l'ordine dei byte. E una volta che le legge byte
 * per byte, comporle in little endian costa ZERO in piu': sono gli stessi due
 * accessi, sommati nell'altro ordine.
 *
 * Lo swap offline invece avrebbe richiesto un parser per OGNI formato di
 * risorsa (scene, body, anim, sprite, testi...), e **non avrebbe risolto
 * l'allineamento**: le word sarebbero rimaste a offset dispari, solo con i byte
 * scambiati. Costo piu' alto e problema ancora aperto.
 *
 * Il precedente conferma: il porting 32X e' arrivato alla stessa conclusione per
 * le stesse due ragioni (vedi il commento in testa a
 * D:\Homebrew5\32XLBA\m10-engine\engine_src\diskfunc.c, punti 2 e 3), su una CPU
 * anch'essa big endian e anch'essa intollerante ai disallineamenti.
 *
 * Corollario che vale anche per i dati GIA' in RAM: dove upstream fa
 * `ListZone = (T_ZONE *)PtrSce`, cioe' punta una struct dentro il blob, noi
 * dobbiamo COPIARE i campi decodificandoli. Altrimenti ogni accesso successivo
 * a `ListZone[n].X0` eredita entrambi i problemi.
 */
#ifndef JAG_LE_H
#define JAG_LE_H

typedef unsigned char le_byte;

static inline unsigned short LE16u(const le_byte *p)
{
    return (unsigned short)(p[0] | ((unsigned short)p[1] << 8));
}

static inline short LE16(const le_byte *p)
{
    return (short)LE16u(p);
}

static inline unsigned long LE32u(const le_byte *p)
{
    return (unsigned long)p[0] | ((unsigned long)p[1] << 8)
           | ((unsigned long)p[2] << 16) | ((unsigned long)p[3] << 24);
}

/* Lettore sequenziale: le struct di LBA1 si leggono in avanti una volta sola,
 * ed e' esattamente quello che facevano le macro GET_BYTE / GET_WORD di
 * DISKFUNC.C. Tenere il cursore in una struct invece che in una variabile
 * globale evita il `PtrSce` globale di upstream, che e' una delle cose che
 * rendono quel codice difficile da seguire. */
typedef struct {
    const le_byte *p;
    const le_byte *end;
} le_reader;

static inline void LE_Init(le_reader *r, const void *base, unsigned long size)
{
    r->p = (const le_byte *)base;
    r->end = r->p + size;
}

static inline unsigned char LE_U8(le_reader *r)
{
    return *r->p++;
}

static inline short LE_S16(le_reader *r)
{
    short v = LE16(r->p);
    r->p += 2;
    return v;
}

static inline void LE_Skip(le_reader *r, long n)
{
    r->p += n;
}

/* Byte residui. Serve al controllo di coda: se il cammino della struct e'
 * corretto, alla fine restano ESATTAMENTE i T_TRACK e niente altro. */
static inline long LE_Left(const le_reader *r)
{
    return (long)(r->end - r->p);
}

#endif /* JAG_LE_H */
