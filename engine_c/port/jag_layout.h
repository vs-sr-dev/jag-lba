/* jag_layout.h - what the two halves of the program must agree on.
 *
 * The fork's TUs see T_OBJET through fork/game/DEFINES.H; ours through
 * hal.h. Nothing makes the two definitions the same except care, and a
 * difference does not fail to compile: ListObjet[n] simply lands somewhere
 * else on one side. On a big-endian CPU a width mismatch is worse than an
 * offset - NbObjets as WORD against LONG reads the high half, i.e. zero
 * actors, in silence (found in S51).
 *
 * So the numbers are written down ONCE, here, and each side proves its own
 * definition against them with _Static_assert: port/jag_layout_fork.c (fork
 * flags, fork headers) and port/jag_layout_ours.c (hal.h). An invariant, not
 * a comparison: neither side can drift without the build stopping.
 * Measured on 2026-09-23 from hal.h with m68k-elf-gcc.
 */
#ifndef JAG_LAYOUT_H
#define JAG_LAYOUT_H

#define JAG_SIZEOF_T_OBJET        134
#define JAG_OFF_BODY               24
#define JAG_OFF_POSOBJX            26
#define JAG_OFF_INFO               78
#define JAG_OFF_CODEJEU           122
#define JAG_OFF_ANIMSRCFRAME      124
#define JAG_OFF_PTRPOSESRC        130

#define JAG_MAX_OBJETS            100
#define JAG_MAX_BODYS             200

#define JAG_LAYOUT_CHECK(T)                                                    \
    _Static_assert(sizeof(T) == JAG_SIZEOF_T_OBJET, "T_OBJET size");          \
    _Static_assert(offsetof(T, Body) == JAG_OFF_BODY, "T_OBJET.Body");        \
    _Static_assert(offsetof(T, PosObjX) == JAG_OFF_POSOBJX, "T_OBJET.PosObjX");\
    _Static_assert(offsetof(T, Info) == JAG_OFF_INFO, "T_OBJET.Info");        \
    _Static_assert(offsetof(T, CodeJeu) == JAG_OFF_CODEJEU, "T_OBJET.CodeJeu");\
    _Static_assert(offsetof(T, AnimSrcFrame) == JAG_OFF_ANIMSRCFRAME,         \
                   "T_OBJET.AnimSrcFrame");                                   \
    _Static_assert(offsetof(T, PtrPoseSrc) == JAG_OFF_PTRPOSESRC,             \
                   "T_OBJET.PtrPoseSrc");                                     \
    _Static_assert(MAX_OBJETS == JAG_MAX_OBJETS, "MAX_OBJETS");               \
    _Static_assert(MAX_BODYS == JAG_MAX_BODYS, "MAX_BODYS")

#endif
