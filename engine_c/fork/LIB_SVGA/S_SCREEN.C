#include "LIB_SYS/ADELINE.H"
#include "LIB_SYS/LIB_SYS.H"
#include "LIB_SVGA/LIB_SVGA.H"

extern UBYTE *Screen;

void Cls(void)
{
	memset(Log, 0, Screen_X * Screen_Y);
}

void CopyScreen(void *src, void *dst)
{
	if (src == dst)
		return;

#ifdef PORT_JAG
	/* PORT: `Screen` is 64 KB here and the full-screen snapshot it stands for
	 * (300 KB) has no DRAM yet: copying into it trampled the fork's heap
	 * (JAGUAR_PORT.md #11). Skipped and counted until it has a home. */
	if (src == (void *)Screen || dst == (void *)Screen)
	{
		Jag_ScreenSkips++;
		return;
	}
#endif

#ifdef PORT_PSX_BG_VRAM
	/* PORT: `Screen` is not a 640x480 buffer on this machine -- it is a
	 * 64 KB scratch area with the clean background living in VRAM instead
	 * (psx_video.c, docs/M7-NOTES.md). Every full-screen save and restore in
	 * the engine is one of these two, and both are one DMA. */
	if (dst == (void *)Screen)
	{
		PORT_BgStoreAll();
		return;
	}
	if (src == (void *)Screen)
	{
		PORT_BgFetchAll();
		return;
	}
#endif

	memcpy(dst, src, Screen_X * Screen_Y);
}
