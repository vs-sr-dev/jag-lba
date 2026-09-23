/* jag_config.c - LBA.CFG and SETUP.LST, built in (LIB_SYS/DEF_FILE.C).
 *
 * The fork reads its configuration from two text files through
 * Def_ReadString/Def_ReadValue. There are no files here, and DEF_FILE.C's
 * failure paths are not kind: PERSO.C strcpy()s the result of
 * Def_ReadString without a NULL check, and a missing driver key makes the
 * Adeline layer exit (DS-LBA hit both, PORTING_NOTES.md). So the answers are
 * a table: every key the fork asks for, verified by grepping the fork on
 * 2026-09-23, with the value from the floppy release's own LBA.CFG except
 * where the Jaguar has a reason to differ (marked).
 *
 * A key that is not in the table answers "" or -1, the way DEF_FILE.C
 * answers for an identifier it cannot find; nothing exits.
 */
#include "hal.h"

typedef struct {
    const char *key;
    const char *value;
} T_CFG;

static const T_CFG cfg[] = {
    /* JAG: English. The floppy's own file says Francais, which is what the
     * copy on disk happened to be installed with, not a property of the game. */
    { "Language",              "English"   },
    { "LanguageCD",            "None"      },   /* the floppy has no voices  */
    { "FlagKeepVoice",         "OFF"       },
    { "FlagDisplayText",       "ON"        },
    { "WindowsFilenameSaving", "OFF"       },   /* absent from the floppy's
                                                   file; PERSO.C wants it   */
    /* JAG: no DLLs to load. "NoMidi"/"NoWave"/"NoMixer" are the values the
     * setup program wrote for a machine without the card. */
    { "SvgaDriver",            "VESA"      },
    { "MidiDriver",            "NoMidi"    },
    { "MidiExec",              "NoExec"    },
    { "MidiType",              "Midi"      },
    { "WaveDriver",            "NoWave"    },
    { "WaveExec",              "NoExec"    },
    { "WaveRate",              "22000"     },
    { "MixerDriver",           "NoMixer"   },
    { "WaveVolume",            "255"       },
    { "MusicVolume",           "195"       },
    { "CDVolume",              "191"       },
    { "LineVolume",            "191"       },
    { "MasterVolume",          "255"       },
    /* SETUP.LST. 0 on this floppy: the European bumper, and GERELIFE's
     * LM_TEXT substitution for text 0. */
    { "Version_US",            "0"         },
};
#define N_CFG ((int)(sizeof(cfg) / sizeof(cfg[0])))

static int lower(int c)
{
    return (c >= 'A' && c <= 'Z') ? c + ('a' - 'A') : c;
}

/* DEF_FILE.C matches identifiers without regard to case. */
static const char *lookup(const UBYTE *key)
{
    int i;

    for (i = 0; i < N_CFG; i++) {
        const char *a = cfg[i].key;
        const UBYTE *b = key;

        while (*a && *b && lower((UBYTE)*a) == lower(*b)) {
            a++;
            b++;
        }
        if (*a == 0 && *b == 0)
            return cfg[i].value;
    }
    return 0;
}

static LONG to_long(const char *s)
{
    LONG v = 0;
    int neg = 0;

    if (*s == '-') {
        neg = 1;
        s++;
    }
    while (*s >= '0' && *s <= '9')
        v = v * 10 + (*s++ - '0');
    return neg ? -v : v;
}

/* The fork copies from the returned pointer and never writes through it. */
UBYTE *Def_ReadString(UBYTE *deffic, UBYTE *identificateur)
{
    static UBYTE empty[1] = { 0 };
    const char *v = lookup(identificateur);

    (void)deffic;
    return v ? (UBYTE *)v : empty;
}

LONG Def_ReadValue(UBYTE *deffic, UBYTE *identificateur)
{
    const char *v = lookup(identificateur);

    (void)deffic;
    return v ? to_long(v) : -1;
}

/* Returns TRUE and stores the value if the key exists; leaves *result alone
 * otherwise, which is how the volume code keeps its defaults. */
LONG Def_ReadValue2(UBYTE *deffic, UBYTE *identificateur, LONG *result)
{
    const char *v = lookup(identificateur);

    (void)deffic;
    if (!v)
        return 0;
    *result = to_long(v);
    return 1;
}

/* The volume menu writes the file back. Nowhere to write to, and the values
 * live in the engine's own variables for the session. */
LONG Def_WriteValue(UBYTE *deffic, UBYTE *identificateur, LONG value)
{
    (void)deffic; (void)identificateur; (void)value;
    return 1;
}

LONG Def_WriteString(UBYTE *deffic, UBYTE *identificateur, UBYTE *string)
{
    (void)deffic; (void)identificateur; (void)string;
    return 1;
}
