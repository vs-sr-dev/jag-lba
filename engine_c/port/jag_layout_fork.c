/* The fork's half of port/jag_layout.h, compiled with the fork's flags and
 * headers. Compiles to nothing. */
#include "c_extern.h"
#include "jag_layout.h"

JAG_LAYOUT_CHECK(T_OBJET);
_Static_assert(sizeof(NbObjets) == 4, "NbObjets");
_Static_assert(sizeof(NbBodys) == 4, "NbBodys");
