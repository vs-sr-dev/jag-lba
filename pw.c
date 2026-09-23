#include <stdio.h>
typedef unsigned short UWORD; typedef unsigned long ULONG;
static UWORD pad_width(int dx, ULONG *flags)
{
    int e, m, bestw = 0, bestf = 0;
    if (dx < 2) dx = 2;
    for (e = 0; e < 12; e++)
        for (m = 0; m < 4; m++) {
            int num = (4 + m) << e; int w;
            if (num & 3) continue;
            w = num >> 2;
            if (w >= dx && !(w & 1) && (bestw == 0 || w < bestw)) { bestw = w; bestf = (e << 2) | m; }
        }
    *flags = 0x00010018UL | ((ULONG)bestf << 9);
    return (UWORD)bestw;
}
int main(void){ ULONG f; for(int dx=1;dx<=64;dx++){ UWORD w=pad_width(dx,&f); printf("%d %u %08lX\n",dx,w,f);} return 0; }
