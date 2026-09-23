/* The Jaguar side's half of port/jag_layout.h. Compiles to nothing. */
#include <stddef.h>
#include "hal.h"
#include "jag_layout.h"

JAG_LAYOUT_CHECK(T_OBJET);
/* The globals the two halves share, by width (hal.h vs fork/game/GLOBAL.C). */
_Static_assert(sizeof(NbObjets) == 4, "NbObjets is LONG in GLOBAL.C");
_Static_assert(sizeof(NbBodys) == 4, "NbBodys is LONG in GLOBAL.C");
_Static_assert(sizeof(XMap) == 4, "XMap is LONG in GRILLE.C");
