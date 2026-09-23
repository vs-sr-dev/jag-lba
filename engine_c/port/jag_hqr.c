/* jag_hqr.c - the fork's HQR and HQM API (LIB_SYS/HQ_RESS.C, HQ_R_M.C,
 * HQ_MEM.C, LOADMALL.C) on top of the cartridge.
 *
 * The fork opens archives BY NAME, keeps LRU pools of entries and a block
 * heap. None of that exists here, on purpose: every archive is already
 * indexed in the cartridge, and hal_hqr.c hands out an entry either in place
 * (BRICK) or expanded once into the per-room arena and cached until the room
 * changes. So each call below maps a file name to an hqr_id_t and asks the
 * HAL. There is no eviction, hence none of the pool compaction bugs the other
 * ports had to fix (HQM_Free rebasing, WaveMove) - JAGUAR_PORT.md, section 2.
 *
 * LIFETIMES, the one rule to keep in mind:
 *   HQR_Get / HQRM_Load  the entry lives until the next HQM_Free_All, i.e.
 *                        the next ChangeCube. That is also what the fork's
 *                        pools guaranteed at best.
 *   LoadMalloc_HQR       a copy in the persistent arena: for what the fork
 *                        loads once at boot (palette, font, shadow sprites).
 *   Load_HQR             a copy into the caller's buffer.
 */
#include "hal.h"

/* Our HAL's names are macros for Jag_* (hal.h); the fork's are the real
 * symbols defined in this file. Undo the macros that collide with a function
 * defined here, and call ours by their Jag_ name. */
#undef HQR_Get
#undef HQM_Free_All
#undef Size_HQM_Memory

/* ---- the name of an archive, as the fork spells it ----------------------- */

static int lower(int c)
{
    return (c >= 'A' && c <= 'Z') ? c + ('a' - 'A') : c;
}

/* Compares the file name part of `path` (after the last '/' or '\') with
 * `name`, ignoring case: the fork writes "RESS.HQR", "ress.hqr", or prefixes
 * PATH_RESSOURCE. */
static int same_file(const char *path, const char *name)
{
    const char *p = path, *s;

    for (s = path; *s; s++)
        if (*s == '/' || *s == '\\' || *s == ':')
            p = s + 1;
    while (*p && *name) {
        if (lower((UBYTE)*p) != lower((UBYTE)*name))
            return 0;
        p++;
        name++;
    }
    return *p == 0 && *name == 0;
}

static const struct {
    const char *file;
    hqr_id_t    id;
} archives[] = {
    { "scene.hqr",   HQR_SCENE   },
    { "body.hqr",    HQR_BODY    },
    { "anim.hqr",    HQR_ANIMS   },
    { "ress.hqr",    HQR_RESS    },
    { "file3d.hqr",  HQR_FILE3D  },
    { "text.hqr",    HQR_TEXT    },
    { "samples.hqr", HQR_SAMPLES },
    { "midi_mi.hqr", HQR_MIDI_MI },
    { "midi_sb.hqr", HQR_MIDI_SB },
    { "invobj.hqr",  HQR_INVOBJ  },
    { "sprites.hqr", HQR_SPRITES },
    { "lba_gri.hqr", HQR_GRID    },
    { "lba_bll.hqr", HQR_BLL     },
    { "lba_brk.hqr", HQR_BRICK   },
};
#define N_ARCHIVES ((int)(sizeof(archives) / sizeof(archives[0])))

/* -1 if the cartridge has no such archive (fla_gif.hqr, flasamp.hqr...).
 * Not an error by itself: the fork checks what it gets back. */
static int archive_of(const char *path)
{
    int i;

    for (i = 0; i < N_ARCHIVES; i++)
        if (same_file(path, archives[i].file))
            return HQR_Slots(archives[i].id) ? (int)archives[i].id : -1;
    return -1;
}

/* ---- the resource "pools": a descriptor per archive ---------------------- *
 * The fork keeps a T_HQR_HEADER* per pool and passes it back to HQR_Get. It
 * never reads the fields (checked: only HQR_Flag is read outside HQ_RESS.C),
 * so the header is opaque to it and ours only needs the archive. */
typedef struct {
    hqr_id_t id;
} T_JAG_POOL;

static T_JAG_POOL pools[HQR_LAST];

WORD HQR_Flag = 0;

void *HQR_Init_Ressource(char *hqrname, ULONG maxsize, WORD maxindex)
{
    int id = archive_of(hqrname);

    (void)maxsize;
    (void)maxindex;
    if (id < 0)
        return 0;               /* the fork checks: "not enough memory" path */
    pools[id].id = (hqr_id_t)id;
    return &pools[id];
}

void *HQR_Get(void *header, WORD index)
{
    const T_JAG_POOL *p = (const T_JAG_POOL *)header;
    const void *e;

    HQR_Flag = 0;
    if (!p || index < 0)
        return 0;
    e = Jag_HQR_Get(p->id, index);
    HQR_Flag = e ? (WORD)HQR_WasFresh() : 0;
    return (void *)e;
}

/* The samples pool. No sound effects in this build (M7), and every caller
 * treats a null sample as "nothing to play". */
void *HQR_GetSample(void *header, WORD index)
{
    (void)header;
    (void)index;
    HQR_Flag = 0;
    return 0;
}

/* Called by ChangeCube to empty the sprite and anim pools. The per-room arena
 * already dies in HQM_Free_All, a few lines earlier in the same function. */
void HQR_Reset_Ressource(void *header)
{
    (void)header;
}

LONG HQR_Change_Ressource(void *header, char *newhqrname)
{
    int id = archive_of(newhqrname);
    T_JAG_POOL *p = (T_JAG_POOL *)header;

    if (!p || id < 0)
        return 0;
    p->id = (hqr_id_t)id;
    return 1;
}

/* ---- whole-entry loads ---------------------------------------------------- */

ULONG Size_HQR(char *name, UWORD index)
{
    int id = archive_of(name);

    return (id < 0) ? 0 : HQR_Size((hqr_id_t)id, index);
}

/* Into the caller's buffer. The fork passes no capacity: its buffers are
 * sized for the biggest entry plus the 500-byte LZS margin, and the entry
 * size is what we copy, so the capacity check is the entry's own size. */
ULONG Load_HQR(UBYTE *name, void *ptrdest, UWORD index)
{
    int id = archive_of((const char *)name);
    ULONG size;

    if (id < 0 || !ptrdest)
        return 0;
    size = HQR_Size((hqr_id_t)id, index);
    if (size == 0)
        return 0;
    return HQR_Expand((hqr_id_t)id, index, ptrdest, size);
}

/* A persistent copy: what the fork loads once at boot and keeps for the whole
 * game (PERSO.C: the palette, the shadow sprites, the font, the goodies). */
void *LoadMalloc_HQR(UBYTE *name, UWORD index)
{
    int id = archive_of((const char *)name);
    ULONG size;
    void *dst;

    if (id < 0)
        return 0;
    size = HQR_Size((hqr_id_t)id, index);
    if (size == 0)
        return 0;
    dst = Jag_Malloc(size);
    if (HQR_Expand((hqr_id_t)id, index, dst, size) != size)
        return 0;
    return dst;
}

/* Into "HQM memory", i.e. until the next ChangeCube: the scene, the fiches.
 * Returns the size, 0 on failure, like HQ_R_M.C. */
LONG HQRM_Load(UBYTE *name, UWORD index, void **ptrptr)
{
    int id = archive_of((const char *)name);
    const void *e;

    *ptrptr = 0;
    if (id < 0)
        return 0;
    e = Jag_HQR_Get((hqr_id_t)id, index);
    if (!e)
        return 0;
    *ptrptr = (void *)e;
    return (LONG)HQR_Size((hqr_id_t)id, index);
}

/* ---- HQM, the fork's block heap ------------------------------------------- *
 * Only GRILLE.C (not compiled: scene_bg.c owns the bricks) and PLAYFLA.C (not
 * yet) allocate from it. What remains is the lifetime: HQM_Free_All is the
 * fork's "the room is over", and it has to end OUR per-room arena too, or
 * every ChangeCube would leak a room's worth of entries. */
ULONG Size_HQM_Memory = 0;
ULONG Size_HQM_Free = 0;

LONG HQM_Init_Memory(ULONG size)
{
    Size_HQM_Memory = size;
    Size_HQM_Free = size;
    return 1;
}

void HQM_Free_All(void)
{
    Jag_HQM_Free_All();
}

void HQM_Free(void *ptr)
{
    (void)ptr;
}
