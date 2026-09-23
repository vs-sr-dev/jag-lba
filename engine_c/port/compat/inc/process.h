/* process.h - DOS header. ADELINE.C only uses spawnl() to launch the external
 * DOS driver setup programs. Nothing to launch here: report failure and the
 * engine skips the exec. */
#ifndef JAG_COMPAT_PROCESS_H
#define JAG_COMPAT_PROCESS_H
#define P_WAIT 0
#define spawnl(...) (-1)
#endif
