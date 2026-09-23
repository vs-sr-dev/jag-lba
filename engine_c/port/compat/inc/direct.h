/* direct.h - DOS/MinGW header, used by game/DISKFUNC.C for getcwd/chdir.
 * There is no working directory on a cartridge: jag_stubs.c answers with the
 * root and accepts any chdir. */
#ifndef JAG_COMPAT_DIRECT_H
#define JAG_COMPAT_DIRECT_H
char *getcwd(char *buf, unsigned int size);
int   chdir(const char *path);
#endif
