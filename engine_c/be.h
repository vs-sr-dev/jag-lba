/* be.h - lettura dei dati PRE-DIGERITI dal packer: big endian, offset pari.
 *
 * IL COMPLEMENTO DI le.h, NON IL SUO SOSTITUTO
 * --------------------------------------------
 * La decisione di M2 (le.h: little endian letto byte per byte) resta giusta
 * per i dati che arrivano da LBA1 COME SONO: li' le word cadono a offset
 * dispari e il 68000 le deve comporre a byte comunque. Questi accessori sono
 * per i dati che il packer RICUOCE - da S43 il corpo (tools/body_digest.py,
 * che e' il padrone del formato) - dove le due premesse cadono insieme:
 * l'ordine dei byte lo scegliamo noi, e ogni campo sta a offset PARI per
 * costruzione.
 *
 * Su quel dato, BE16u e' una `move.w` nuda. Sul banco di prova (x86, little
 * endian) la stessa macro compone i byte: stessi valori, un sorgente solo,
 * ed e' il banco a garantire che i due rami non divergano - le 24 sonde
 * leggono lo stesso corpo da tutte e due le strade.
 *
 * JAG_BE_NATIVE distingue anche i punti dove il dato big-endian si puo'
 * usare IN PLACE sul Jaguar ma va convertito sull'host (le normali dei
 * corpi: vedi norm_native in body_draw.c).
 */
#ifndef JAG_BE_H
#define JAG_BE_H

typedef unsigned char be_byte;

#if defined(__mc68000__)
#define JAG_BE_NATIVE 1
static inline unsigned short BE16u(const be_byte *p)
{
    return *(const unsigned short *)(const void *)p;
}
#else
#define JAG_BE_NATIVE 0
static inline unsigned short BE16u(const be_byte *p)
{
    return (unsigned short)(((unsigned short)p[0] << 8) | p[1]);
}
#endif

static inline short BE16(const be_byte *p)
{
    return (short)BE16u(p);
}

#endif /* JAG_BE_H */
