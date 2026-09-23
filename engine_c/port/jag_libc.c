/* jag_libc.c - the libc and LIB_SYS pieces the fork needs and newlib either
 * lacks or would bring in at too high a price.
 *
 * THE PRICE, measured on 2026-09-23 in the first test link: newlib alone was
 * 48 KB of the 235 KB of code, almost all of it printf with floating point
 * (vfprintf, dtoa, mprec) and stdio, pulled in by two callers - PERSO.C's
 * TheEnd (replaced by ours) and MSG_CUST.C looking for a community text file
 * with fopen. Defining the handful of entry points here keeps the linker from
 * ever pulling those archive members. What newlib does well and cheaply
 * (string.h, qsort, rand, itoa, malloc) stays newlib's.
 */
#include "hal.h"
#include <stddef.h>
#include <stdio.h>
#include <stdarg.h>

#undef Malloc
#undef feof             /* a macro in newlib's stdio.h */
#undef Free

/* ---- the fork's Malloc (LIB_SYS/MALLOC.C) -------------------------------- *
 * On DOS, Malloc(-1) answered "how much is free"; the fork sizes some pools
 * from it. Everything else it allocates with Malloc is for the whole game
 * (Screen, BufText, BufOrder...), so it goes to the persistent arena, whose
 * Free is LIFO - the pattern the fork's temporary allocations follow. */
void *Malloc(LONG size)
{
    if (size == -1)
        return (void *)Jag_Size_HQM_Memory();
    return Jag_Malloc((ULONG)size);
}

void *SmartMalloc(LONG size)
{
    return Malloc(size);
}

void Free(void *ptr)
{
    Jag_Free(ptr);
}

/* ---- newlib's malloc needs sbrk ------------------------------------------- *
 * Only for the small, really freed allocations: MSG_CUST.C returns strings
 * that its callers free(). A fixed region carved once from the persistent
 * arena, so the two allocators never compete for the same bytes. */
#define SBRK_SIZE (48UL * 1024UL)

void *sbrk(ptrdiff_t incr)
{
    static unsigned char *base, *brk;

    if (!base) {
        base = (unsigned char *)Jag_Malloc(SBRK_SIZE);
        brk = base;
    }
    if (incr < 0 || (ULONG)(brk - base) + (ULONG)incr > SBRK_SIZE)
        return (void *)-1;
    brk += incr;
    return brk - incr;
}

/* ---- stdio: no files ------------------------------------------------------- */

FILE *fopen(const char *path, const char *mode)
{
    (void)path;
    (void)mode;
    return 0;
}

int fclose(FILE *f)             { (void)f; return 0; }
char *fgets(char *s, int n, FILE *f) { (void)s; (void)n; (void)f; return 0; }
int feof(FILE *f)               { (void)f; return 1; }
int printf(const char *fmt, ...) { (void)fmt; return 0; }
int puts(const char *s)         { (void)s; return 0; }

/* What newlib's own internals reach for (abort, assert). There is no process
 * and no descriptor; anything that gets here is a fault. */
void _exit(int code)
{
    TheEnd((WORD)(0x200 | (code & 0xFF)), "_exit");
    for (;;)
        ;
}
int  kill(int pid, int sig)     { (void)pid; (void)sig; return -1; }
int  getpid(void)               { return 1; }
int  isatty(int fd)             { (void)fd; return 0; }
int  close(int fd)              { (void)fd; return -1; }
int  open(const char *p, int f, ...) { (void)p; (void)f; return -1; }
int  read(int fd, void *b, unsigned n)  { (void)fd; (void)b; (void)n; return -1; }
int  write(int fd, const void *b, unsigned n) { (void)fd; (void)b; (void)n; return -1; }
long lseek(int fd, long o, int w) { (void)fd; (void)o; (void)w; return -1; }
int  fstat(int fd, void *st)    { (void)fd; (void)st; return -1; }

/* ---- Watcom's number formatting ------------------------------------------- */

char *ultoa(unsigned long value, char *buf, int radix)
{
    char tmp[34];
    int i = 0, j = 0;

    if (radix < 2 || radix > 36)
        radix = 10;
    do {
        int d = (int)(value % (unsigned long)radix);
        tmp[i++] = (char)(d < 10 ? '0' + d : 'a' + d - 10);
        value /= (unsigned long)radix;
    } while (value && i < 33);
    while (i)
        buf[j++] = tmp[--i];
    buf[j] = 0;
    return buf;
}

char *ltoa(long value, char *buf, int radix)
{
    if (radix == 10 && value < 0) {
        buf[0] = '-';
        ultoa((unsigned long)(-value), buf + 1, 10);
        return buf;
    }
    return ultoa((unsigned long)value, buf, radix);
}

/* ---- PORT_Diag: a ring in DRAM, not a console ------------------------------ *
 * The other ports print. There is nowhere to print here, and a formatter
 * would bring back the 40 KB just removed. So each call records the format
 * POINTER and its first three arguments; the format strings are in the ELF,
 * which is enough for a BigPEmu script to print the ring after the fact.
 * Nobody has to read hex off the screen for it. */
#define DIAG_SLOTS 64

typedef struct {
    const char *fmt;
    ULONG a, b, c;
} T_DIAG;

volatile ULONG Jag_DiagCount;           /* total calls; slot = count % 64 */
T_DIAG Jag_DiagRing[DIAG_SLOTS];

/* Every argument the fork passes is an int, a long or a pointer: 4 bytes on
 * the 68000, so reading three ULONGs is exact for the first three, whatever
 * the format says. Arguments the format does not have are read as garbage and
 * ignored by whoever prints the ring. */
void PORT_Diag(const char *fmt, ...)
{
    T_DIAG *d = &Jag_DiagRing[Jag_DiagCount % DIAG_SLOTS];
    va_list ap;

    va_start(ap, fmt);
    d->fmt = fmt;
    d->a = va_arg(ap, ULONG);
    d->b = va_arg(ap, ULONG);
    d->c = va_arg(ap, ULONG);
    va_end(ap);
    Jag_DiagCount++;
}
