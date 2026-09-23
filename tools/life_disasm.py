#!/usr/bin/env python3
"""JagLBA - disassembler degli script LIFE e TRACK di una scena LBA1.

A COSA SERVE
------------
Vedere DOVE il gioco fa partire un dialogo, testa una zona, apre una porta,
cambia stanza: la logica di scena non sta nel motore, sta nei byte degli script
di ogni attore, e questi byte non si leggono a occhio.

DA DOVE VIENE IL FORMATO
------------------------
Dal sorgente C del gioco in questo repo, non da TwinE ne' dalla memoria:
  - engine_c/fork/game/GERELIFE.C  DoLife (LM_*), DoFuncLife (LF_*), DoTest (LT_*)
  - engine_c/fork/game/GERETRAK.C  DoTrack (TM_*)
  - engine_c/fork/game/COMMON.H    i numeri degli opcode
Ogni opcode qui sotto consuma ESATTAMENTE i byte che il suo `case` fa avanzare
a PtrPrg / OffsetTrack. Due casi che contano:
  - DoTest legge il valore confrontato su 1 byte CON SEGNO (BYTE valuebyte),
    tranne dopo DISTANCE, DISTANCE_3D, CONE_VIEW, NB_GOLD_PIECES e CHOICE,
    che mettono TypeAnswer = RET_WORD -> 2 byte LE con segno;
  - SET_DIR / SET_DIR_OBJ leggono un byte in piu' SOLO se la direzione e'
    MOVE_FOLLOW (2).
Gli opcode senza `case` (LM_NOP, LM_ENDIF, TM_NOP) a runtime cadono nel default
e consumano solo se stessi. Un numero che COMMON.H non definisce e' un errore.

I SALTI sono offset in byte dall'inizio dello script (PtrLife + LE_R16):
si stampano come `->XXXX`, e il bersaglio deve cadere su un inizio di istruzione
(lo verifica --check). Quando si salta:
  IF, SWIF, ONEIF   se la condizione e' FALSA (il corpo e' fra qui e il bersaglio)
  OR_IF             se e' VERA (salta dentro il corpo dell'IF che segue)
  SNIF, NEVERIF     sempre (sono le forme "gia' scattato" di SWIF e ONEIF)
  ELSE, OFFSET      sempre
Il comportamento corrente (OffsetLife) si sposta con SET_COMPORTEMENT/SET_LIFE
(life@XXXX); il ciclo di un frame finisce a END_COMPORTEMENT/RETURN.

IL TESTO
--------
TEXT.HQR (CommonClassic), MESSAGE.C:InitDial: voce (lingua*14*2)+(file*2)+0 =
tabella degli id (UWORD LE), +1 = offset (MaxText+1 UWORD LE) e poi le stringhe.
Inglese = lingua 0; file = START_FILE_ISLAND (3) + isola della scena. LM_TEXT
gira dopo LM_PROJ_3D, che fa InitDial(1): il suo id si cerca nel file 1.

USO
    python tools/life_disasm.py 0                 # scena 0, tutti gli attori
    python tools/life_disasm.py 0 --actor 3       # solo l'attore 3
    python tools/life_disasm.py --check 0 1 2     # solo validazione, exit != 0 se fallisce
    python tools/life_disasm.py --messages 0      # solo i dialoghi, con le condizioni
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hqr_decompress as lzss
import hqr_pack
import scene_parse

START_FILE_ISLAND = 3      # COMMON.H:330
MAX_TEXT_LANG = 14         # MESSAGE.C:33
LANG_EN = 0                # ListLanguage[] = {"EN_", ...}
FILE_CREDITS = 1           # LM_PROJ_3D -> InitDial(1)
MOVE_FOLLOW = 2            # COMMON.H:357

MOVE_NAMES = {0: 'NO_MOVE', 1: 'MANUAL', 2: 'FOLLOW', 3: 'TRACK',
              4: 'FOLLOW_2', 5: 'TRACK_ATTACK', 6: 'SAME_XZ', 7: 'RANDOM'}
COMPORTEMENT = {0: 'NORMAL', 1: 'SPORTIF', 2: 'AGRESSIF', 3: 'DISCRET',
                4: 'PROTOPACK'}
FLAG_GAME_NAMES = {0: 'HOLOMAP', 1: 'BALLE_MAGIQUE', 2: 'SABRE_MAGIQUE',
                   3: 'TROMPE', 4: 'TUNIQUE', 5: 'LIVRE_BU', 6: 'MEDAILLON',
                   7: 'FLACON_EAU_CLAIRE', 12: 'PROTOPACK', 14: 'MECA_PINGOUIN',
                   15: 'CARBURANT', 27: 'CLOVER', 70: 'CONSIGNE'}
ZONE_TYPES = {0: 'CHANGE_CUBE', 1: 'CAMERA', 2: 'SCENARIO', 3: 'GRM',
              4: 'GIVER', 5: 'MESSAGE', 6: 'ECHELLE'}
# COMMON.H "Flags" (T_OBJET.Flags)
OBJ_FLAGS = [(1, 'OBJ_COL'), (2, 'BRICK_COL'), (4, 'ZONE'), (8, 'SPRITE_CLIP'),
             (16, 'PUSHABLE'), (32, 'COL_BASSE'), (64, 'CODE_JEU'),
             (512, 'INVISIBLE'), (1024, 'SPRITE_3D'), (2048, 'FALLABLE'),
             (4096, 'NO_SHADOW'), (8192, 'BACKGROUND'), (16384, 'CARRIER'),
             (32768, 'MINI_ZV')]

# ---------------------------------------------------------------------------
# Tabelle degli opcode. Codici operando:
#   b  byte senza segno          o  numero di oggetto (byte)
#   w  word LE con segno         j  salto nello script (word LE, offset)
#   t  id di testo (word LE)     s  stringa terminata da 0
#   L  offset nella LIFE dello STESSO attore (word)   (SET_LIFE/SET_COMPORTEMENT)
#   T  offset nella TRACK (word)  - dell'attore corrente o dell'oggetto `o`
#   X  offset nella LIFE dell'oggetto `o` (word)
#   c  condizione: funzione LF_* + suoi operandi + LT_* + valore
#   d  direzione (SET_DIR): byte, +1 byte se MOVE_FOLLOW
# ---------------------------------------------------------------------------
LIFE_OPS = {
    0: ('END', ''), 1: ('NOP', ''), 2: ('SNIF', 'cj'), 3: ('OFFSET', 'j'),
    4: ('NEVERIF', 'cj'),
    10: ('LABEL', 'b'), 11: ('RETURN', ''), 12: ('IF', 'cj'),
    13: ('SWIF', 'cj'), 14: ('ONEIF', 'cj'), 15: ('ELSE', 'j'),
    16: ('ENDIF', ''), 17: ('BODY', 'b'), 18: ('BODY_OBJ', 'ob'),
    19: ('ANIM', 'b'), 20: ('ANIM_OBJ', 'ob'), 21: ('SET_LIFE', 'L'),
    22: ('SET_LIFE_OBJ', 'oX'), 23: ('SET_TRACK', 'T'),
    24: ('SET_TRACK_OBJ', 'oT'), 25: ('MESSAGE', 't'), 26: ('FALLABLE', 'b'),
    27: ('SET_DIR', 'd'), 28: ('SET_DIR_OBJ', 'od'), 29: ('CAM_FOLLOW', 'o'),
    30: ('COMPORTEMENT_HERO', 'b'), 31: ('SET_FLAG_CUBE', 'bb'),
    32: ('COMPORTEMENT', 'b'), 33: ('SET_COMPORTEMENT', 'L'),
    34: ('SET_COMPORTEMENT_OBJ', 'oX'), 35: ('END_COMPORTEMENT', ''),
    36: ('SET_FLAG_GAME', 'bb'), 37: ('KILL_OBJ', 'o'), 38: ('SUICIDE', ''),
    39: ('USE_ONE_LITTLE_KEY', ''), 40: ('GIVE_GOLD_PIECES', 'w'),
    41: ('END_LIFE', ''), 42: ('STOP_L_TRACK', ''),
    43: ('RESTORE_L_TRACK', ''), 44: ('MESSAGE_OBJ', 'ot'),
    45: ('INC_CHAPTER', ''), 46: ('FOUND_OBJECT', 'b'),
    47: ('SET_DOOR_LEFT', 'w'), 48: ('SET_DOOR_RIGHT', 'w'),
    49: ('SET_DOOR_UP', 'w'), 50: ('SET_DOOR_DOWN', 'w'),
    51: ('GIVE_BONUS', 'b'), 52: ('CHANGE_CUBE', 'b'), 53: ('OBJ_COL', 'b'),
    54: ('BRICK_COL', 'b'), 55: ('OR_IF', 'cj'), 56: ('INVISIBLE', 'b'),
    57: ('ZOOM', 'b'), 58: ('POS_POINT', 'b'), 59: ('SET_MAGIC_LEVEL', 'b'),
    60: ('SUB_MAGIC_POINT', 'b'), 61: ('SET_LIFE_POINT_OBJ', 'ob'),
    62: ('SUB_LIFE_POINT_OBJ', 'ob'), 63: ('HIT_OBJ', 'ob'),
    64: ('PLAY_FLA', 's'), 65: ('PLAY_MIDI', 'b'), 66: ('INC_CLOVER_BOX', ''),
    67: ('SET_USED_INVENTORY', 'b'), 68: ('ADD_CHOICE', 't'),
    69: ('ASK_CHOICE', 't'), 70: ('BIG_MESSAGE', 't'),
    71: ('INIT_PINGOUIN', 'o'), 72: ('SET_HOLO_POS', 'b'),
    73: ('CLR_HOLO_POS', 'b'), 74: ('ADD_FUEL', 'b'), 75: ('SUB_FUEL', 'b'),
    76: ('SET_GRM', 'b'), 77: ('SAY_MESSAGE', 't'),
    78: ('SAY_MESSAGE_OBJ', 'ot'), 79: ('FULL_POINT', ''), 80: ('BETA', 'w'),
    81: ('GRM_OFF', ''), 82: ('FADE_PAL_RED', ''), 83: ('FADE_ALARM_RED', ''),
    84: ('FADE_ALARM_PAL', ''), 85: ('FADE_RED_PAL', ''),
    86: ('FADE_RED_ALARM', ''), 87: ('FADE_PAL_ALARM', ''),
    88: ('EXPLODE_OBJ', 'o'), 89: ('BULLE_ON', ''), 90: ('BULLE_OFF', ''),
    91: ('ASK_CHOICE_OBJ', 'ot'), 92: ('SET_DARK_PAL', ''),
    93: ('SET_NORMAL_PAL', ''), 94: ('MESSAGE_SENDELL', ''),
    95: ('ANIM_SET', 'b'), 96: ('HOLOMAP_TRAJ', 'b'), 97: ('GAME_OVER', ''),
    98: ('THE_END', ''), 99: ('MIDI_OFF', ''), 100: ('PLAY_CD_TRACK', 'b'),
    101: ('PROJ_ISO', ''), 102: ('PROJ_3D', ''), 103: ('TEXT', 't'),
    104: ('CLEAR_TEXT', ''), 105: ('BRUTAL_EXIT', ''),
}

# Gli opcode che mostrano un testo (per il riepilogo dei dialoghi).
MESSAGE_OPS = {'MESSAGE', 'MESSAGE_OBJ', 'SAY_MESSAGE', 'SAY_MESSAGE_OBJ',
               'BIG_MESSAGE', 'ASK_CHOICE', 'ASK_CHOICE_OBJ', 'ADD_CHOICE',
               'MESSAGE_SENDELL', 'TEXT'}
COND_OPS = {'IF', 'SWIF', 'SNIF', 'ONEIF', 'NEVERIF', 'OR_IF'}

# DoFuncLife: (nome, ha un operando oggetto/indice, risposta a word)
LIFE_FUNCS = {
    0: ('COL', '', False), 1: ('COL_OBJ', 'o', False),
    2: ('DISTANCE', 'o', True), 3: ('ZONE', '', False),
    4: ('ZONE_OBJ', 'o', False), 5: ('BODY', '', False),
    6: ('BODY_OBJ', 'o', False), 7: ('ANIM', '', False),
    8: ('ANIM_OBJ', 'o', False), 9: ('L_TRACK', '', False),
    10: ('L_TRACK_OBJ', 'o', False), 11: ('FLAG_CUBE', 'b', False),
    12: ('CONE_VIEW', 'o', True), 13: ('HIT_BY', '', False),
    14: ('ACTION', '', False), 15: ('FLAG_GAME', 'b', False),
    16: ('LIFE_POINT', '', False), 17: ('LIFE_POINT_OBJ', 'o', False),
    18: ('NB_LITTLE_KEYS', '', False), 19: ('NB_GOLD_PIECES', '', True),
    20: ('COMPORTEMENT_HERO', '', False), 21: ('CHAPTER', '', False),
    22: ('DISTANCE_3D', 'o', True), 23: ('MAGIC_LEVEL', '', False),
    24: ('MAGIC_POINT', '', False), 25: ('USE_INVENTORY', 'b', False),
    26: ('CHOICE', '', True), 27: ('FUEL', '', False),
    28: ('CARRY_BY', '', False), 29: ('CDROM', '', False),
}
TESTS = {0: '==', 1: '>', 2: '<', 3: '>=', 4: '<=', 5: '!='}

# DoTrack
TRACK_OPS = {
    0: ('END', ''), 1: ('NOP', ''), 2: ('BODY', 'b'), 3: ('ANIM', 'b'),
    4: ('GOTO_POINT', 'b'), 5: ('WAIT_ANIM', ''), 6: ('LOOP', ''),
    7: ('ANGLE', 'w'), 8: ('POS_POINT', 'b'), 9: ('LABEL', 'b'),
    10: ('GOTO', 'j'), 11: ('STOP', ''), 12: ('GOTO_SYM_POINT', 'b'),
    13: ('WAIT_NB_ANIM', 'bb'), 14: ('SAMPLE', 'w'),
    15: ('GOTO_POINT_3D', 'b'), 16: ('SPEED', 'w'), 17: ('BACKGROUND', 'b'),
    18: ('WAIT_NB_SECOND', 'b4'), 19: ('NO_BODY', ''), 20: ('BETA', 'w'),
    21: ('OPEN_LEFT', 'w'), 22: ('OPEN_RIGHT', 'w'), 23: ('OPEN_UP', 'w'),
    24: ('OPEN_DOWN', 'w'), 25: ('CLOSE', ''), 26: ('WAIT_DOOR', ''),
    27: ('SAMPLE_RND', 'w'), 28: ('SAMPLE_ALWAYS', 'w'),
    29: ('SAMPLE_STOP', 'w'), 30: ('PLAY_FLA', 's'),
    31: ('REPEAT_SAMPLE', 'w'), 32: ('SIMPLE_SAMPLE', 'w'),
    33: ('FACE_TWINKEL', 'w'), 34: ('ANGLE_RND', 'ww'),
}


class DisasmError(Exception):
    pass


# ---------------------------------------------------------------------------
# Dati: scena e testo, dalla GOG
# ---------------------------------------------------------------------------
_hqr_cache = {}


def _hqr(path):
    if path not in _hqr_cache:
        with open(path, 'rb') as f:
            d = f.read()
        _hqr_cache[path] = (d, lzss.parse_offsets(d))
    return _hqr_cache[path]


def load_scene(num):
    """La scena `num` di SCENE.HQR, con gli script di OGNI attore (eroe in 0).

    Il layout e' quello di scene_parse.parse (LoadScene, DISKFUNC.C); qui si
    rilegge solo l'intestazione fino agli script dell'eroe, che parse() salta
    senza annotarli. Gli attori 1.. usano track_off/life_off di parse()."""
    d, offs = _hqr(os.path.join(hqr_pack.GOG_COMMON, 'SCENE.HQR'))
    if not 0 <= num < len(offs):
        raise DisasmError('scena %d: SCENE.HQR ha %d voci' % (num, len(offs)))
    payload = lzss.extract_entry(d, num, offs)
    if payload is None:
        return None
    s = scene_parse.parse(payload)
    r = scene_parse.Reader(payload)
    r.u8(); r.u8()                       # Island, GameOverCube
    for _ in range(4 + 12 + 2):          # 2 scartati, luce, 4 ambience x3, second
        r.s16()
    r.u8()                               # CubeJingle
    r.s16(); r.s16(); r.s16()            # CubeStartX/Y/Z
    tl = r.s16(); toff = r.p; r.skip(tl)
    ll = r.s16(); loff = r.p; r.skip(ll)
    if r.s16() != s.nb_objets:
        raise DisasmError('scena %d: rilettura dell\'eroe sfasata' % num)
    s.hero_track = payload[toff:toff + tl]
    s.hero_life = payload[loff:loff + ll]
    for a in s.actors:
        a.track = payload[a.track_off:a.track_off + a.track_len]
        a.life_bytes = payload[a.life_off:a.life_off + a.life_len]
    return s


class Texts:
    """Una lingua, i file caricati a richiesta come fa InitDial."""

    def __init__(self, lang=LANG_EN):
        self.lang = lang
        self.files = {}

    def _file(self, f):
        if f not in self.files:
            d, offs = _hqr(os.path.join(hqr_pack.GOG_CLASSIC, 'TEXT.HQR'))
            base = self.lang * MAX_TEXT_LANG * 2 + f * 2
            table = {}
            try:
                order = lzss.extract_entry(d, base, offs)
                blob = lzss.extract_entry(d, base + 1, offs)
            except IndexError:
                order = blob = None
            if order and blob:
                n = len(order) // 2                      # MaxText
                ids = struct.unpack_from('<%dH' % n, order)
                tab = struct.unpack_from('<%dH' % (n + 1), blob)
                for i, tid in enumerate(ids):            # FindText + GetText
                    raw = blob[tab[i]:tab[i + 1]]
                    table.setdefault(tid, raw.split(b'\0')[0]
                                     .decode('cp437', 'replace'))
            self.files[f] = table
        return self.files[f]

    def get(self, f, tid):
        return self._file(f).get(tid)


# ---------------------------------------------------------------------------
# Disassembler
# ---------------------------------------------------------------------------
class Cur:
    def __init__(self, b, what):
        self.b = b
        self.p = 0
        self.what = what

    def need(self, n):
        if self.p + n > len(self.b):
            raise DisasmError('%s: operando oltre la fine dello script '
                              '(offset %04X, servono %d byte)'
                              % (self.what, self.p, n))

    def u8(self):
        self.need(1)
        v = self.b[self.p]
        self.p += 1
        return v

    def s8(self):
        v = self.u8()
        return v - 256 if v >= 128 else v

    def s16(self):
        self.need(2)
        v = self.b[self.p] | (self.b[self.p + 1] << 8)
        self.p += 2
        return v - 0x10000 if v >= 0x8000 else v

    def u16(self):
        return self.s16() & 0xFFFF

    def string(self):
        end = self.b.find(b'\0', self.p)
        if end < 0:
            raise DisasmError('%s: stringa senza terminatore a %04X'
                              % (self.what, self.p))
        v = self.b[self.p:end].decode('cp437', 'replace')
        self.p = end + 1
        return v


def _quote(t, n=70):
    if t is None:
        return '<testo assente>'
    t = t.replace('\n', ' ').replace('\r', ' ')
    return '"%s"' % (t if len(t) <= n else t[:n - 3] + '...')


def decode_cond(c, ctx):
    """DoFuncLife + DoTest. Torna (testo, commento)."""
    fo = c.p
    f = c.u8()
    if f not in LIFE_FUNCS:
        raise DisasmError('%s: funzione LF sconosciuta %d a %04X'
                          % (c.what, f, fo))
    name, arg, is_word = LIFE_FUNCS[f]
    comment = []
    s = name
    if arg:
        a = c.u8()
        s += '(%d)' % a
        if name in ('FLAG_GAME', 'USE_INVENTORY') and a in FLAG_GAME_NAMES:
            comment.append(FLAG_GAME_NAMES[a])
    to = c.p
    t = c.u8()
    if t not in TESTS:
        raise DisasmError('%s: test LT sconosciuto %d a %04X' % (c.what, t, to))
    v = c.s16() if is_word else c.s8()
    s += ' %s %d' % (TESTS[t], v)
    if name == 'CHOICE':
        comment.append(_quote(ctx['texts'].get(ctx['file'], v), 50))
    elif name == 'COMPORTEMENT_HERO' and v in COMPORTEMENT:
        comment.append(COMPORTEMENT[v])
    elif name == 'ZONE' or name == 'ZONE_OBJ':
        zs = [z for z in ctx['scene'].zones if z.type == 2 and z.num == v]
        if zs:
            comment.append('zona scenario %d: %d box' % (v, len(zs)))
    return s, comment


def disasm_life(b, ctx, what):
    """Lista di (offset, testo, commenti, info) fino alla fine esatta."""
    c = Cur(b, what)
    out = []
    while c.p < len(b):
        off = c.p
        op = c.u8()
        if op not in LIFE_OPS:
            raise DisasmError('%s: opcode LIFE sconosciuto %d a %04X'
                              % (what, op, off))
        name, spec = LIFE_OPS[op]
        ops, com = [], []
        info = {'name': name}
        obj = None
        for k in spec:
            if k == 'b':
                ops.append(str(c.u8()))
            elif k == 'o':
                obj = c.u8()
                ops.append('obj%d' % obj)
                info['obj'] = obj
            elif k == 'w':
                ops.append(str(c.s16()))
            elif k == 'j':
                tgt = c.u16()
                ops.append('->%04X' % tgt)
                info.setdefault('jumps', []).append(tgt)
            elif k == 'L':
                tgt = c.u16()
                ops.append('life@%04X' % tgt)
                if tgt == 0xFFFF:
                    com.append('-1 = life finita, come END')
                else:
                    info.setdefault('jumps', []).append(tgt)
                    info['life_set'] = (ctx['self'], tgt)
            elif k == 'X':
                tgt = c.u16()
                ops.append('life@%04X' % tgt)
                info['life_set'] = (obj, tgt)
            elif k == 'T':
                tgt = c.u16()
                ops.append('track@%04X' % tgt)
                if tgt == 0xFFFF:
                    com.append('-1 = nessuna track')
                who = obj if obj is not None else ctx['self']
                lab = ctx['track_labels'].get(who, {}).get(tgt)
                if lab is not None:
                    com.append('TRACK LABEL %d' % lab)
            elif k == 't':
                tid = c.u16()
                ops.append('#%d' % tid)
                f = FILE_CREDITS if name == 'TEXT' else ctx['file']
                info['text'] = tid
                com.append(_quote(ctx['texts'].get(f, tid)))
            elif k == 's':
                ops.append('"%s"' % c.string())
            elif k == 'd':
                mv = c.u8()
                ops.append(MOVE_NAMES.get(mv, str(mv)))
                if mv == MOVE_FOLLOW:
                    ops.append('obj%d' % c.u8())
            elif k == 'c':
                cs, cc = decode_cond(c, ctx)
                ops.append(cs)
                com += cc
                info['cond'] = cs
        if name in ('COMPORTEMENT_HERO',) and ops and int(ops[0]) in COMPORTEMENT:
            com.append(COMPORTEMENT[int(ops[0])])
        if name == 'SET_FLAG_GAME' and int(ops[0]) in FLAG_GAME_NAMES:
            com.append(FLAG_GAME_NAMES[int(ops[0])])
        if name == 'MESSAGE_SENDELL':
            com.append('Dial(6): ' + _quote(ctx['texts'].get(ctx['file'], 6)))
            info['text'] = 6
        if name == 'SWIF':
            com.append('se vera: diventa SNIF (scatta sul fronte)')
        elif name == 'ONEIF':
            com.append('una volta sola: diventa NEVERIF')
        elif name == 'OR_IF':
            com.append('salta se VERA')
        elif name == 'TEXT':
            com.append('file 1, non avanza se TextLig >= 440')
        if name in COND_OPS:
            txt = '%-12s %s, %s' % (name, ops[0], ops[1])
        else:
            txt = '%-12s %s' % (name, ' '.join(ops)) if ops else name
        out.append((off, txt, com, info))
    return out


def disasm_track(b, what):
    c = Cur(b, what)
    out = []
    while c.p < len(b):
        off = c.p
        op = c.u8()
        if op not in TRACK_OPS:
            raise DisasmError('%s: opcode TRACK sconosciuto %d a %04X'
                              % (what, op, off))
        name, spec = TRACK_OPS[op]
        ops, com = [], []
        info = {'name': name}
        for k in spec:
            if k == 'b':
                v = c.u8()
                ops.append(str(v))
                if name == 'LABEL':
                    info['label'] = v
            elif k == 'w':
                ops.append(str(c.s16()))
            elif k == 'j':
                tgt = c.u16()
                ops.append('->%04X' % tgt)
                if tgt == 0xFFFF:
                    # OffsetTrack = -1: la track si ferma, come TM_STOP
                    com.append('-1 = track ferma')
                else:
                    info.setdefault('jumps', []).append(tgt)
            elif k == '4':
                c.need(4)
                c.p += 4                    # timer di lavoro (LE_W32), non codice
            elif k == 's':
                ops.append('"%s"' % c.string())
        if name == 'WAIT_NB_ANIM':
            com.append('2o byte = contatore di lavoro')
        elif name == 'WAIT_NB_SECOND':
            com.append('+4 byte timer di lavoro')
        elif name in ('FACE_TWINKEL', 'ANGLE_RND'):
            com.append('ultima word = scratch (-1 a riposo)')
        txt = '%-12s %s' % (name, ' '.join(ops)) if ops else name
        out.append((off, txt, com, info))
    return out


def check_jumps(lines, length, what):
    starts = {off for off, _t, _c, _i in lines}
    for off, _t, _c, info in lines:
        for tgt in info.get('jumps', ()):
            if tgt not in starts and tgt != length:
                raise DisasmError('%s: salto a %04X (da %04X) non cade su '
                                  'un\'istruzione' % (what, tgt, off))


def actor_list(s):
    """[(indice, track, life, attore o None per l'eroe)]"""
    out = [(0, s.hero_track, s.hero_life, None)]
    for i, a in enumerate(s.actors, 1):
        out.append((i, a.track, a.life_bytes, a))
    return out


def disasm_scene(num, texts):
    """Tutto quel che serve per stampare o controllare la scena `num`."""
    s = load_scene(num)
    if s is None:
        return None
    ctx = {'scene': s, 'texts': texts, 'file': START_FILE_ISLAND + s.island,
           'track_labels': {}}
    res = []
    tracks = {}
    for idx, tr, _li, _a in actor_list(s):
        what = 'scena %d attore %d TRACK' % (num, idx)
        tl = disasm_track(tr, what)
        check_jumps(tl, len(tr), what)
        tracks[idx] = tl
        ctx['track_labels'][idx] = {off: i['label'] for off, _t, _c, i in tl
                                    if 'label' in i}
    for idx, tr, li, a in actor_list(s):
        what = 'scena %d attore %d LIFE' % (num, idx)
        ctx['self'] = idx
        ll = disasm_life(li, ctx, what)
        check_jumps(ll, len(li), what)
        res.append((idx, a, tracks[idx], ll, len(tr), len(li)))
    return s, ctx, res


def flag_names(fl):
    return '|'.join(n for bit, n in OBJ_FLAGS if fl & bit) or '0'


def print_scene(num, only=None, texts=None):
    texts = texts or Texts()
    s, ctx, res = disasm_scene(num, texts)
    print('=== scena %d  isola %d (testi: file %d)  %d attori  %d zone ==='
          % (num, s.island, ctx['file'], s.nb_objets, s.nb_zones))
    if only is None:
        for i, z in enumerate(s.zones):
            extra = ''
            if z.type == 0:
                extra = '-> scena %d' % z.num
            elif z.type == 2:
                extra = 'ZONE == %d' % z.num
            elif z.type == 5:
                extra = '#%d %s' % (z.num, _quote(texts.get(ctx['file'], z.num), 60))
            else:
                extra = 'num %d' % z.num
            print('  zona %2d %-11s x %d..%d y %d..%d z %d..%d  %s'
                  % (i, ZONE_TYPES.get(z.type, str(z.type)),
                     z.x0, z.x1, z.y0, z.y1, z.z0, z.z1, extra))
    for idx, a, tl, ll, tlen, llen in res:
        if only is not None and idx != only:
            continue
        print()
        if a is None:
            print('--- attore 0 (eroe)  pos (%d, %d, %d)'
                  % (s.hero_x, s.hero_y, s.hero_z))
        else:
            print('--- attore %d  fiche %d body %d anim %d sprite %d  '
                  'pos (%d, %d, %d)  move %s  flags %s'
                  % (idx, a.index_file3d, a.gen_body, a.gen_anim, a.sprite,
                     a.x, a.y, a.z, MOVE_NAMES.get(a.move, a.move),
                     flag_names(a.flags & 0xFFFF)))
        for title, lines, n in (('TRACK', tl, tlen), ('LIFE', ll, llen)):
            print('  %s (%d byte)' % (title, n))
            for off, txt, com, _i in lines:
                line = '    %04X: %s' % (off, txt)
                if com:
                    line = '%-52s ; %s' % (line, '; '.join(com))
                print(line)


def message_guards(lines, starts):
    """Per ogni opcode di testo: le condizioni che lo racchiudono.

    Struttura, non esecuzione: un IF/SWIF/ONEIF a `c` che salta a `t` racchiude
    [c, t); un ELSE a `e` che salta a `t` racchiude (e, t) con la condizione
    NEGATA dell'IF che saltava a e+3. Un OR_IF subito prima si aggiunge in OR.
    `starts` = inizi di comportamento (life@ scritti da SET_COMPORTEMENT*)."""
    neg = {' == ': ' != ', ' != ': ' == ', ' < ': ' >= ', ' >= ': ' < ',
           ' > ': ' <= ', ' <= ': ' > '}

    def ored(j):
        """La condizione dell'IF all'indice j con gli OR_IF che lo precedono."""
        cs = [lines[j][3]['cond']]
        jj = j - 1
        while jj >= 0 and lines[jj][3]['name'] == 'OR_IF':
            cs.insert(0, lines[jj][3]['cond'])
            jj -= 1
        return cs

    def negate(cs):
        if len(cs) == 1:
            for a, b in neg.items():
                if a in cs[0]:
                    return cs[0].replace(a, b, 1)
        return 'NOT(%s)' % ' OR '.join(cs)

    out = []
    for k, (off, txt, com, info) in enumerate(lines):
        if info['name'] not in MESSAGE_OPS:
            continue
        guards = []
        for j, (c, _t2, _c2, ci) in enumerate(lines[:k]):
            n = ci['name']
            if n in ('IF', 'SWIF', 'ONEIF') and ci['jumps'][0] > off:
                cs = ored(j)
                g = cs[0] if len(cs) == 1 else '(%s)' % ' OR '.join(cs)
                guards.append(g if n == 'IF' else '%s[%s]' % (g, n))
            elif n == 'ELSE' and ci['jumps'][0] > off:
                src = [jj for jj, x in enumerate(lines[:j])
                       if x[3]['name'] in ('IF', 'SWIF', 'ONEIF')
                       and x[3]['jumps'][0] == c + 3]
                guards.append(negate(ored(src[-1])) if src else 'NOT(?)')
        seg = max([s for s in starts if s <= off] or [0])
        out.append((off, txt, com, seg, guards))
    return out


def print_messages(num, texts):
    s, ctx, res = disasm_scene(num, texts)
    starts = {}
    for idx, _a, _tl, ll, _n1, _n2 in res:
        for _o, _t, _c, info in ll:
            if 'life_set' in info:
                who, tgt = info['life_set']
                starts.setdefault(who, set()).add(tgt)
    print('=== scena %d: testi negli script LIFE (file %d) ==='
          % (num, ctx['file']))
    for idx, a, _tl, ll, _n1, _n2 in res:
        msgs = message_guards(ll, starts.get(idx, set()) | {0})
        if not msgs:
            continue
        print()
        print('--- attore %d%s' % (idx, ' (eroe)' if a is None else
                                   '  body %d' % a.gen_body))
        for off, txt, com, seg, guards in msgs:
            print('    %04X: %s  %s' % (off, txt, '; '.join(com)))
            print('          comportamento @%04X, se: %s'
                  % (seg, ' AND '.join(guards) if guards else '(sempre)'))
    for i, z in enumerate(s.zones):
        if z.type == 5:
            print()
            print('--- zona %d MESSAGE (eroe dentro + ACTION): #%d %s'
                  % (i, z.num, _quote(texts.get(ctx['file'], z.num))))


def main(argv):
    check = '--check' in argv
    msgs = '--messages' in argv
    only = None
    args = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ('--check', '--messages'):
            pass
        elif a == '--actor':
            i += 1
            only = int(argv[i])
        else:
            args.append(int(a))
        i += 1
    if not args:
        print(__doc__)
        return 2
    texts = Texts()
    if check:
        bad = 0
        for num in args:
            try:
                r = disasm_scene(num, texts)
            except DisasmError as e:
                print('ERRORE %s' % e)
                bad += 1
                continue
            if r is None:
                print('scena %d: voce vuota' % num)
                continue
            _s, _ctx, res = r
            ni = sum(len(tl) + len(ll) for _i, _a, tl, ll, _t, _l in res)
            print('scena %3d: %2d attori, %5d istruzioni, fine esatta OK'
                  % (num, len(res), ni))
        print('life_disasm --check: %s' % ('OK' if not bad else
                                           '%d scene FALLITE' % bad))
        return 1 if bad else 0
    for num in args:
        try:
            if msgs:
                print_messages(num, texts)
                continue
            print_scene(num, only, texts)
        except DisasmError as e:
            print('ERRORE %s' % e)
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
