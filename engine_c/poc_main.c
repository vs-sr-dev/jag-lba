/*
 * S24 PoC blob: prova che C compilato con m68k-elf-gcc (Docker marsdev-32x,
 * stessa famiglia GCC dell'engine 32X) gira sul 68000 Jaguar chiamato dal
 * lato RMAC. E' il fondamento per il riuso di engine_src/ (32XLBA m10).
 *
 * ABI verso RMAC (via crt_entry.s):
 *   blob+0 : poc_init()    -> d0 = 0x900DF00D
 *   blob+6 : poc_frame(fb) -> d0 = frame_count; arg long sullo stack
 *
 * Freestanding: niente libc, niente static constructor. .bss azzerata da
 * poc_init stesso via simboli del linker script.
 */

extern char __bss_start[], __bss_end[];

#define POC_MAGIC 0x900DF00DUL

/* FB Jaguar 8bpp: 640 byte per riga (layout S20 interlace: righe contigue,
 * i field le campionano alternate). */
#define FB_STRIDE 640u

static unsigned long frame_count;

unsigned long poc_init(void)
{
    char *p;

    for (p = __bss_start; p < __bss_end; p++)
        *p = 0;
    frame_count = 0;
    return POC_MAGIC;
}

/* Quadrato 8x8 top-left con indice palette ciclico: se lampeggia, il C
 * sta girando a ogni tick VBL. fb = base del buffer attivo (BG_A/BG_B). */
unsigned long poc_frame(unsigned char *fb)
{
    unsigned int x, y;
    unsigned char c = (unsigned char)(frame_count >> 2);

    for (y = 0; y < 8; y++)
        for (x = 0; x < 8; x++)
            fb[y * FB_STRIDE + x] = c;

    frame_count++;
    return frame_count;
}
