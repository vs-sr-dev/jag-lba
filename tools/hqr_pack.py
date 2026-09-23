#!/usr/bin/env python3
"""JagLBA V6 / M1 - costruisce il contenitore HQR della cartuccia.

Il motore LBA1 chiede le sue risorse con `HQR_Get(id, indice)`. Su PC quello
apriva un file .HQR e ne decomprimeva una voce; qui le risorse stanno nella
cartuccia e `HQR_Get` deve poter tornare un PUNTATORE DIRETTO alla ROM, senza
copiare niente in DRAM e senza decomprimere a runtime (decisione 3.3 di
docs/skeleton_design.md).

Quindi questo script fa offline tutto il lavoro:
  1. legge gli .HQR originali;
  2. decomprime ogni voce (LZSS, tools/hqr_decompress.py);
  3. le impacchetta in un unico contenitore con due livelli di indice.

FORMATO DEL CONTENITORE ("JHQR", tutto BIG ENDIAN = nativo 68000)
-----------------------------------------------------------------
Gli offset sono SEMPRE relativi alla base del contenitore, non assoluti: cosi'
il blob non sa a che indirizzo verra' piazzato e il packer della cartuccia
resta libero di spostarlo (e, quando arrivera' il bank switching, di
mapparlo a finestre diverse).

  intestazione (16 byte)
    +$00  4   magic 'JHQR'
    +$04  2   versione del formato (1)
    +$06  2   numero di archivi
    +$08  4   dimensione totale del contenitore
    +$0C  4   somma di controllo dei payload (32 bit, con riporto perso)

  direttorio degli archivi: n_archivi x 12 byte, indicizzato da hqr_id_t
    +$00  4   offset della tabella delle voci
    +$04  4   numero di voci
    +$08  4   flag (0 = payload verbatim)

  tabella delle voci di un archivio: n_voci x 8 byte
    +$00  4   offset del payload
    +$04  4   dimensione in byte (0 = slot assente, HQR_Get torna NULL)

  payload, allineati a 4 byte.

ORDINE DEI BYTE DEI PAYLOAD
---------------------------
I payload restano VERBATIM come li produce la decompressione, cioe' little
endian come li scriveva il Watcom del 1994. Questo contenitore e' un
contenitore: non interpreta e non converte niente, altrimenti servirebbe un
parser per ogni formato di risorsa e un errore di conversione diventerebbe
indistinguibile da un errore del motore.

La conversione LE->BE e' una decisione di M2, quando si sapra' quali campi
vengono letti davvero e quanto spesso: si scegliera' fra accessori le16/le32
lato C (costo a ogni lettura, zero rischio) e swap offline per-formato (costo
zero a runtime, serve un parser per formato). Il porting 32X ha usato lo swap
offline per SCENE e TEXT, quindi il precedente esiste per entrambe le strade.

USO
    python tools/hqr_pack.py                 # ricostruisce se serve
    python tools/hqr_pack.py --force         # ricostruisce sempre
    python tools/hqr_pack.py --manifest      # stampa i valori noti attesi

La decompressione di ~4,6 MB in Python costa una decina di secondi, percio' il
risultato viene riusato se e' piu' recente di tutti i suoi ingressi.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hqr_decompress as lzss

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOG_COMMON = r'I:\GOG\Little Big Adventure\Common'
# TEXT.HQR non sta in Common: la ristampa 2:21 lo tiene in CommonClassic (e in
# Speedrun\Windows, che e' la stessa cosa). E' l'unico archivio con una casa
# diversa, quindi la deroga si dichiara qui invece di generalizzare il percorso.
GOG_CLASSIC = r'I:\GOG\Little Big Adventure\CommonClassic'
ARCHIVE_DIR = {'TEXT': GOG_CLASSIC}
OUT = os.path.join(REPO, 'cart', 'data_v6.bin')

MAGIC = b'JHQR'
VERSION = 1
HDR_SIZE = 16
ARCH_DIR_ENTRY = 12
ENTRY_SIZE = 8

# L'indice di ogni archivio nel direttorio E' il valore di hqr_id_t di
# engine_c/hal.h, e i primi dieci combaciano con l'enum del porting 32X
# (hal/hal.h) per non divergere inutilmente. Un archivio con file None resta
# dichiarato ma vuoto: HQR_Get torna NULL e il motore prende la sua strada di
# ripiego, invece di leggere spazzatura.
#
# Segnaposto: dice a build() che le voci di questo archivio le genera un modulo
# invece di leggerle da un HQR di LBA1. Non e' None, che vuol dire "archivio
# dichiarato e vuoto".
FRONT_SYNTH = '<generato>'

# (id, nome, file sorgente, None, o FRONT_SYNTH)
ARCHIVES = [
    (0,  'SCENE',   'SCENE.HQR'),
    (1,  'BODY',    'BODY.HQR'),
    (2,  'ANIMS',   'ANIM.HQR'),
    (3,  'RESS',    'RESS.HQR'),
    (4,  'FILE3D',  'FILE3D.HQR'),
    (5,  'TEXT',    'TEXT.HQR'),    # da CommonClassic; solo il dialogo 0 (S48)
    (6,  'SAMPLES', None),          # audio: M7
    (7,  'MIDI_MI', None),          # audio: M7
    (8,  'MIDI_SB', None),          # audio: M7
    (9,  'INVOBJ',  'INVOBJ.HQR'),
    (10, 'SPRITES', 'SPRITES.HQR'),
    # M2b: i tre archivi del fondo. In V4 avevano una pipeline separata
    # (tools/cart_pack_all.py) con un formato proprio, una tabella di scene
    # inventata e una rimappatura degli indici dei brick. Sono HQR come tutti
    # gli altri: qui entrano nel contenitore, con gli indici ORIGINALI di
    # LBA_BRK, e quel formato parallelo - placeholder compresi - muore.
    (11, 'GRID',    'LBA_GRI.HQR'),
    (12, 'BLL',     'LBA_BLL.HQR'),
    (13, 'BRICK',   'LBA_BRK.HQR'),
    # S48: l'unico archivio che NON viene da un file di LBA1. Contiene le
    # schermate del front-end preparate da tools/front_screen.py - oggi il solo
    # logo Adeline, ritagliato del suo bordo bianco. Il formato di voce e'
    # quello di tutti gli altri (intestazione LZSS da 10 byte), quindi sul
    # Jaguar non c'e' un percorso nuovo: HQR_Get lo espande come qualunque
    # altra risorsa.
    (14, 'FRONT',   FRONT_SYNTH),
]

# Archivi indicizzati per scena: il filtro --scenes tiene solo gli slot chiesti.
PER_SCENE = ('GRID', 'BLL')

# ---------------------------------------------------------------------------
# TEXT: due slot su 140 (S48)
# ---------------------------------------------------------------------------
# TEXT.HQR intero pesa 220 KB compressi e il margine della cartuccia, dopo le
# due schermate del front-end, e' di una quindicina. Ma il menu chiede QUATTRO
# stringhe, e stanno tutte nel dialogo 0 - gli slot 0 (la tabella degli
# identificatori) e 1 (le stringhe), 730 byte in due.
#
# Non e' una potatura arbitraria: il dialogo 0 e' la sezione di SISTEMA di
# LBA1 (menu, opzioni, comportamenti), e quando serviranno i dialoghi di gioco
# saranno altri slot con la stessa identica struttura. Gli indici restano
# quelli originali, come per tutti gli altri archivi.
#
# S52: entrano i dialoghi che la cella chiede. Una coppia (ordine, stringhe)
# per file di testo, file = voce/2 (MESSAGE.C:ListFileText, inglese = lingua
# 0): 2 GAM (inventario, comportamenti, messaggi di gioco), 3 l'isola 0
# (Citadel, START_FILE_ISLAND + Island). Adeline le ha salvate NON compresse
# (ctype 0, 22 KB in due): ricompresse col nostro LZSS pesano 12 KB, e il
# margine della cartuccia e' 18,7. Vedi recompress_text.
TEXT_KEEP = {0, 1, 4, 5, 6, 7}


def recompress_text(pairs):
    """TEXT: ogni voce depositata nella forma PIU' CORTA fra l'originale e il
    nostro LZSS (tipo 1 o 2, il migliore). Il testo inglese di LBA1 e' quasi
    tutto salvato in chiaro, e compresso si dimezza. Il roundtrip col
    decompressore vero - lo stesso trascritto in engine_c/hal_hqr.c - e' la
    prova, voce per voce; il payload LOGICO non cambia."""
    out = []
    for stored, lg in pairs:
        if lg is None:
            out.append((stored, lg))
            continue
        best = stored
        for ct in (1, 2):
            c = lzss.compress_lzss(lg, ct)
            if lzss.decompress_lzss(c, len(lg), ct) != lg:
                raise SystemExit('hqr_pack: roundtrip LZSS ROTTO sul testo')
            e = struct.pack('<IIH', len(lg), len(c), ct) + c
            if len(e) < len(best):
                best = e
        out.append((best, lg))
    return out

# ---------------------------------------------------------------------------
# COME OGNI ARCHIVIO STA NEL CONTENITORE (S30, modo --compress)
# ---------------------------------------------------------------------------
# Il contenitore ha sempre messo i payload DECOMPRESSI, perche' HQR_Get torna
# un puntatore dentro la cartuccia e il dato si usa in place. Costa: il gioco
# completo chiede 11061 KB dove ce ne stanno 5368.
#
# Misurato archivio per archivio, il gonfiore non e' distribuito:
#
#     BRICK   3883 -> 4138 KB    1,07x     <- gia' RLE: comprimerlo non rende
#     RESS     932 -> 2872 KB    3,08x
#     GRID     587 -> 1645 KB    2,80x
#     BLL       54 ->  771 KB   14,16x
#
# Quindi la politica non e' "tutto compresso", e' per archivio:
#
#   'lzss'  i byte originali di LBA1 ricopiati COSI' COME SONO, intestazione
#           di 10 byte compresa. Non serve un compressore e il dato e'
#           identico bit per bit a quel che il gioco ha spedito; il costo e'
#           una decompressione al caricamento, che per questi archivi vale
#           pochi KB per stanza.
#   'raw'   depositato decompresso, come prima.
#
# BRICK resta 'raw' e le due ragioni tirano dalla stessa parte: guadagnerebbe
# 255 KB su 4138 (il 6%), e sarebbe il 98% del lavoro di decompressione per
# stanza - cioe' pagherebbe il percorso caldo per non guadagnare quasi niente.
# Vedi [[feedback_spostare_non_e_togliere]].
STORE = {
    'BRICK': 'raw',
}
STORE_DEFAULT = 'lzss'

# Bit del campo flag nella directory di archivio (finora sempre 0).
ARCH_FLAG_LZSS = 1

# ---------------------------------------------------------------------------
# POTATURA A REGOLA, NON A MANO
# ---------------------------------------------------------------------------
# RESS contiene nove voci da ESATTAMENTE 640x480 byte: sono le schermate intere
# (intro, menu, holomap, game over). Valgono 727 KB su 931 e nessuna serve per
# stare dentro una stanza. Si riconoscono dalla dimensione decompressa, che e'
# una regola verificabile e non un elenco scritto a mano da tenere aggiornato.
#
# Vanno rimesse per l'intro e i menu, ma non in questa forma e non a questo
# prezzo: e' una decisione rimandata, non un pezzo di gioco buttato.
SLIDE_BYTES = 640 * 480

# S48: DUE tornano, e sono nominate una per una.
#
#   27  RESS_LOGO_PCR   il logo Adeline, con la sua palette allo slot 28
#   14  RESS_MENU_PCR   il fondo del menu principale, che usa la palette
#                       GLOBALE (slot 0) - quindi il passaggio menu -> stanza
#                       non cambia CLUT
#
# Le altre sette restano fuori: 12 e 15 sono i titoli RELENTLESS/LBA, 52 il
# marchio Electronic Arts, e la progressione decisa per questa cartuccia e'
# boot -> Adeline -> menu -> gioco, senza filmato FLA. 17/19/25/49 sono le
# schermate dell'intro e di Sendell, che tornano quando ci sara' un posto dove
# metterle.
#
# La regola sulla dimensione RESTA, ed e' il punto: una schermata nuova non
# entra per sbaglio, entra perche' qualcuno ha scritto il suo numero qui. Le
# due tengono 101 KB dei 116 di margine - vedi cart_build.py, che si lamenta
# da solo se si sfora.
#
# Il logo Adeline (27) NON e' qui, e non perche' sia stato scartato: passa
# dall'archivio FRONT ritagliato del suo bordo bianco (tools/front_screen.py),
# che sugli stessi pixel costa 17,7 KB invece di 41,6.
KEEP_SLIDES = {14}


def drop_fullscreen_slides(idx, orig_size):
    return orig_size == SLIDE_BYTES and idx not in KEEP_SLIDES

# ---------------------------------------------------------------------------
# IL SOTTOINSIEME, E PERCHE' NON E' UN RIPIEGO
# ---------------------------------------------------------------------------
# Con tutti e 134 gli slot il contenitore chiede ~10,8 MB e la finestra della
# cartuccia ne ha 6: senza bank switching non entra. Ma il fondo della scena 0
# chiede 526 brick su 8715, cioe' 250 KB, e nella regione ROM_DATA ce ne sono
# 841 liberi.
#
# Costruire prima il sottoinsieme non e' rimandare il problema, e' separare due
# problemi che altrimenti si mascherano a vicenda: se il fondo non si vedesse su
# una cartuccia bankizzata appena scritta, non si saprebbe se il difetto sta nel
# renderizzatore o nei banchi. Il bank switching e' esattamente quel che
# trasformera' `--scenes 0` in `--scenes tutte`, e a quel punto avra' un
# riferimento funzionante contro cui essere verificato.


def align4(n):
    return (n + 3) & ~3


def load_archive(path, keep=None, store='raw', drop_if=None):
    """I payload da depositare (None = slot assente, escluso o potato).

    `keep` e' un insieme di indici: gli altri slot restano DICHIARATI ma vuoti,
    quindi `HQR_Get` torna NULL invece di un puntatore a un vicino. Gli indici
    restano quelli originali di LBA1 - una rimappatura farebbe risparmiare la
    tabella ma renderebbe i numeri sul Jaguar incomparabili con quelli degli
    strumenti Python, che e' il contrario di quel che serve.

    `store='lzss'` deposita i byte ORIGINALI, intestazione di 10 byte compresa,
    senza toccarli. `drop_if(indice, dimensione_decompressa)` pota a regola.

    Torna coppie (depositato, logico). Il LOGICO e' quel che il 68000 vede dopo
    HQR_Get, e sono le sonde di M1 a chiederlo: se le attese descrivessero i
    byte depositati, con un archivio compresso pretenderebbero dal Jaguar dei
    byte che il Jaguar non vedra' mai. Per gli archivi 'raw' le due facce sono
    lo stesso oggetto e non costano niente.
    """
    with open(path, 'rb') as f:
        data = f.read()
    offsets = lzss.parse_offsets(data)
    out = []
    dropped = 0
    for i in range(len(offsets)):
        if offsets[i] == 0 or (keep is not None and i not in keep):
            out.append((None, None))
            continue
        try:
            orig, comp, _ctype = lzss.read_entry_header(data, offsets[i])
        except Exception as exc:
            print('    slot %d: intestazione ILLEGGIBILE (%s)' % (i, exc))
            out.append((None, None))
            continue
        if drop_if is not None and drop_if(i, orig):
            out.append((None, None))
            dropped += 1
            continue
        try:
            logical = lzss.extract_entry(data, i, offsets)
        except Exception as exc:
            # Uno slot rotto si dichiara assente e si stampa: meglio di un
            # payload troncato che poi sembra un errore del motore.
            print('    slot %d: ILLEGGIBILE (%s) -> dichiarato assente' % (i, exc))
            out.append((None, None))
            continue
        if store == 'lzss':
            out.append((data[offsets[i]:offsets[i] + 10 + comp], logical))
        else:
            out.append((logical, logical))
    if dropped:
        print('    potati %d slot a regola' % dropped)
    return out


def scene_filter(scenes):
    """Quali slot tenere per GRID, BLL e BRICK, dato l'insieme delle scene.

    L'insieme dei brick lo calcola tools/bg_ref.py percorrendo griglia e BLL:
    la STESSA funzione che usa il renderizzatore di riferimento. Se il packer
    avesse una copia propria di quella logica, un giorno divergerebbero e la
    cartuccia conterrebbe un insieme di brick diverso da quello con cui sono
    state calcolate le attese - un guasto che si presenterebbe come "un brick
    manca" senza dire perche'.
    """
    if scenes is None:
        return {}
    import bg_ref
    common = bg_ref.load_common(GOG_COMMON)
    bricks = set()
    for s in scenes:
        bricks |= bg_ref.used_bricks(s, common)
    return {'GRID': set(scenes), 'BLL': set(scenes), 'BRICK': bricks}


def build(scenes=None, compress=False):
    keep_by_name = scene_filter(scenes)
    archives = []
    flags = []
    logical = []
    for aid, name, fn in ARCHIVES:
        if fn is None:
            archives.append((aid, name, []))
            flags.append(0)
            logical.append([])
            continue
        if fn is FRONT_SYNTH:
            # Le voci le genera front_screen a partire da RESS, quindi il
            # sorgente e' lo stesso file di LBA1 e non un asset nostro tenuto
            # in repo: se un giorno cambia RESS, cambia anche questo archivio.
            import front_screen
            ress_path = os.path.join(GOG_COMMON, 'RESS.HQR')
            with open(ress_path, 'rb') as fh:
                ress = fh.read()
            store = 'lzss' if compress else 'raw'
            entries = front_screen.build_entries(
                ress, lzss.parse_offsets(ress), store_lzss=(store == 'lzss'))
            print('  %-8s %-12s %4d slot (%d pieni)  %8d byte %-11s'
                  % (name, '(front_screen)', len(entries),
                     sum(1 for e in entries if e),
                     sum(len(e) for e in entries if e),
                     'compressi' if store == 'lzss' else 'decompressi'))
            archives.append((aid, name, entries))
            flags.append(ARCH_FLAG_LZSS if store == 'lzss' else 0)
            # Il payload LOGICO e' il blob FSCR: e' quel che il 68000 trova
            # dopo HQR_Get, ed e' su quello che menu_ref.py calcola le attese.
            logical.append([front_screen.unpack_raw(e, store) for e in entries])
            continue
        path = os.path.join(ARCHIVE_DIR.get(name, GOG_COMMON), fn)
        if not os.path.isfile(path):
            raise SystemExit('manca %s' % path)
        keep = keep_by_name.get(name)
        if name == 'TEXT':
            keep = TEXT_KEEP
        store = (STORE.get(name, STORE_DEFAULT) if compress else 'raw')
        # --compress-bricks: mette un numero anche sull'altra faccia del knob.
        # Comprimere i brick guadagna ROM e paga al caricamento; senza sapere
        # quanto vale ciascuna delle due cose la scelta sarebbe un'opinione.
        if compress and name == 'BRICK' and '--compress-bricks' in sys.argv:
            store = 'lzss'
        drop = drop_fullscreen_slides if (compress and name == 'RESS') else None
        pairs = load_archive(path, keep, store, drop)
        if name == 'TEXT' and store == 'lzss':
            pairs = recompress_text(pairs)
        # S43: i corpi si PRE-DIGERISCONO (tools/body_digest.py, che documenta
        # formato e ragioni). Il payload LOGICO diventa il blob JBD - ed e' su
        # quello che si calcolano le attese di M1, quindi il 68000 e' misurato
        # sui byte che vedra' davvero. Lo STORED e' il blob ricompresso col
        # nostro LZSS, roundtrip-verificato contro lo stesso decompressore che
        # gira in hal_hqr.c: la build si controlla da sola.
        if name == 'BODY':
            import body_digest
            digested = []
            for _stored, lg in pairs:
                if lg is None:
                    digested.append((None, None))
                    continue
                dig = body_digest.digest(lg)
                digested.append((body_digest.hqr_entry(dig, lzss)
                                 if store == 'lzss' else dig, dig))
            pairs = digested
        entries = [p[0] for p in pairs]
        logical.append([p[1] for p in pairs])
        n_ok = sum(1 for e in entries if e)
        tot = sum(len(e) for e in entries if e)
        print('  %-8s %-12s %4d slot (%d pieni)  %8d byte %-11s%s'
              % (name, fn, len(entries), n_ok, tot,
                 'compressi' if store == 'lzss' else 'decompressi',
                 '   [sottoinsieme]' if keep is not None else ''))
        archives.append((aid, name, entries))
        flags.append(ARCH_FLAG_LZSS if store == 'lzss' else 0)

    n_arch = len(archives)

    # Prima passata: solo le dimensioni, per sapere dove cominciano i payload.
    cursor = HDR_SIZE + n_arch * ARCH_DIR_ENTRY
    table_off = []
    for _, _, entries in archives:
        table_off.append(cursor)
        cursor += len(entries) * ENTRY_SIZE
    payload_base = align4(cursor)

    # Seconda passata: posizioni dei payload, CON DEDUPLICA.
    #
    # Due slot con lo stesso contenuto puntano allo stesso payload. E' gratis e
    # invisibile al Jaguar: HQR_Get torna un puntatore, e due puntatori uguali
    # sono due puntatori validi.
    #
    # Non e' una micro-ottimizzazione, e lo dice LBA_BLL.HQR: il file su disco e'
    # 54 KB, ma la somma delle sue 120 voci fa 493 KB. Le scene CONDIVIDONO le
    # librerie di blocchi, e gli offset dell'HQR originale lo dicono chiaramente
    # puntando allo stesso posto. Ricopiarle e' pagare nove volte lo stesso dato.
    cursor = payload_base
    placed = []          # per archivio: lista di (offset, size)
    seen = {}            # contenuto -> offset
    saved = 0
    for _, _, entries in archives:
        slots = []
        for e in entries:
            if not e:
                slots.append((0, 0))
                continue
            key = bytes(e)
            if key in seen:
                slots.append((seen[key], len(e)))
                saved += len(e)
                continue
            seen[key] = cursor
            slots.append((cursor, len(e)))
            cursor = align4(cursor + len(e))
        placed.append(slots)
    total = cursor
    if saved:
        print('  deduplica: %d KB risparmiati (%d payload distinti)'
              % (saved // 1024, len(seen)))

    buf = bytearray(total)
    checksum = 0
    for ai, (_, _, entries) in enumerate(archives):
        # Il terzo campo della directory era sempre 0 e adesso porta i flag:
        # bit 0 = i payload di questo archivio sono LZSS con la loro
        # intestazione da 10 byte, cioe' vanno decompressi prima dell'uso.
        struct.pack_into('>III', buf, HDR_SIZE + ai * ARCH_DIR_ENTRY,
                         table_off[ai], len(entries), flags[ai])
        for si, e in enumerate(entries):
            off, size = placed[ai][si]
            struct.pack_into('>II', buf, table_off[ai] + si * ENTRY_SIZE, off, size)
            if e:
                buf[off:off + size] = e
                checksum = (checksum + sum(e)) & 0xFFFFFFFF

    struct.pack_into('>4sHHII', buf, 0, MAGIC, VERSION, n_arch, total, checksum)
    return bytes(buf), archives, placed, logical


def probes(buf, placed, logical=None):
    """I valori noti che il Jaguar deve rileggere dalla cartuccia.

    E' questo che rende verificabile il criterio di M1. Senza un valore atteso
    calcolato QUI, "il C legge un record HQR" non e' distinguibile da "il C
    legge qualcosa": un readout di cifre esadecimali senza un riferimento e'
    solo una decorazione.

    Il confronto non lo fa l'occhio, lo fa il C: questa tabella diventa
    engine_c/hqr_expect.h e il blob mostra a schermo una maschera di bit, uno
    per sonda. Tutti i bit alti = M1 verificato.

    Torna una lista di (nome_C, etichetta, valore atteso).
    """
    magic, ver, n_arch, total, csum = struct.unpack_from('>IHHII', buf, 0)

    # Le sonde descrivono quel che il 68000 VEDE dopo HQR_Get. Per un archivio
    # 'raw' e' il payload depositato; per uno compresso e' la sua espansione,
    # che il packer ha gia' in mano. Senza questa distinzione le attese
    # pretenderebbero dal Jaguar byte che il Jaguar non vede mai.
    def view(aid, slot):
        if logical is not None:
            return logical[aid][slot]
        off, size = placed[aid][slot]
        return buf[off:off + size]

    def word_at(aid, slot, byte_off):
        p = view(aid, slot)
        if p is None or len(p) < byte_off + 4:
            return 0
        return struct.unpack_from('>I', p, byte_off)[0]

    def size_of(aid, slot):
        p = view(aid, slot)
        return len(p) if p else 0

    # Sonde scelte per essere DIVERSE DA ZERO e caratteristiche: uno zero
    # atteso e' indistinguibile da una lettura fallita, e proverebbe il
    # contrario di quel che serve. SCENE[0] comincia con quattro byte nulli
    # (campi di ambiente inutilizzati), percio' la sonda prende i byte 4-7.
    return [
        ('MAGIC',       'magic del contenitore',        magic),
        ('VERARCH',     'versione | numero archivi',    (ver << 16) | n_arch),
        ('TOTAL',       'dimensione del contenitore',   total),
        ('CHECKSUM',    'somma di controllo payload',   csum),
        ('SCENE0_SIZE', 'HQR_Size(SCENE, 0)',           size_of(0, 0)),
        ('SCENE0_W4',   'SCENE[0] byte 4..7',           word_at(0, 0, 4)),
        ('BODY0_SIZE',  'HQR_Size(BODY, 0)',            size_of(1, 0)),
        ('BODY0_W0',    'BODY[0] byte 0..3',            word_at(1, 0, 0)),
    ]


def gen_expect_header(pr):
    out = ['/* GENERATO DA tools/hqr_pack.py - NON MODIFICARE A MANO.\n',
           ' *\n',
           ' * Valori attesi delle sonde HQR di M1. Il blob C li confronta con\n',
           ' * quello che rilegge dalla cartuccia e mostra a schermo la maschera\n',
           ' * dei bit corrispondenti: tutti alti = il contenitore e\' quello che\n',
           ' * questo packer ha scritto, alla posizione dove il packer della\n',
           ' * cartuccia lo ha piazzato.\n',
           ' *\n',
           ' * Se una sonda cade, il valore letto e\' comunque a schermo accanto\n',
           ' * alla maschera, quindi si vede COSA e\' stato letto invece che solo\n',
           ' * "non torna".\n',
           ' */\n',
           '#ifndef JAG_HQR_EXPECT_H\n#define JAG_HQR_EXPECT_H\n\n']
    for name, label, value in pr:
        out.append('#define HQR_EXPECT_%-12s 0x%08XUL  /* %s */\n' % (name, value, label))
    out.append('\n#define HQR_EXPECT_COUNT %d\n' % len(pr))
    out.append('#define HQR_EXPECT_ALL   0x%02XUL\n' % ((1 << len(pr)) - 1))
    out.append('\n#endif\n')
    return ''.join(out)


def manifest(pr, archives):
    lines = ['Sonde di M1 (il Jaguar le mostra in esadecimale, una per riga):']
    for i, (name, label, value) in enumerate(pr):
        lines.append('  sonda %d  %-28s %08X' % (i, label, value))
    lines.append('')
    lines.append('  maschera attesa: %02X  (tutte le sonde superate)'
                 % ((1 << len(pr)) - 1))
    lines.append('')
    lines.append('Riepilogo archivi:')
    for aid, name, entries in archives:
        n_ok = sum(1 for e in entries if e)
        lines.append('  %2d %-8s %4d slot, %4d pieni' % (aid, name, len(entries), n_ok))
    return '\n'.join(lines)


SPEC = os.path.join(REPO, 'cart', 'data_v6_spec.txt')


def parse_scenes():
    """--scenes 0 | 0,3,7 | tutte. Il valore predefinito e' la sola scena 0."""
    if '--scenes' not in sys.argv:
        return [0], '0+c'
    raw = sys.argv[sys.argv.index('--scenes') + 1]
    if raw in ('tutte', 'all'):
        # ELENCARE le scene invece di dire "tieni tutto" non e' la stessa cosa,
        # e la differenza si vede nei brick: LBA_BRK ha 8715 slot ma le 120
        # scene ne nominano 8468, e GRID/BLL hanno 134 slot per 120 scene.
        # Passando l'elenco, gli avanzi li pota il filtro che c'e' gia'.
        return list(range(n_scenes())), 'tutte+c'
    return [int(v) for v in raw.split(',')], raw + '+c'


def n_scenes():
    """Quante scene ha davvero il gioco, contate in SCENE.HQR."""
    with open(os.path.join(GOG_COMMON, 'SCENE.HQR'), 'rb') as f:
        data = f.read()
    return sum(1 for o in lzss.parse_offsets(data) if o)


def stale(spec):
    """Rifare il contenitore?

    Oltre alle date c'e' lo SPEC: il sottoinsieme non e' deducibile dai file
    d'ingresso, quindi senza registrarlo un `--scenes 0,3` dopo un `--scenes 0`
    riuserebbe in silenzio il contenitore sbagliato, e le attese sarebbero
    calcolate su dati che nella cartuccia non ci sono.
    """
    if '--force' in sys.argv or not os.path.isfile(OUT):
        return True
    if not os.path.isfile(SPEC) or open(SPEC).read().strip() != spec:
        return True
    out_t = os.path.getmtime(OUT)
    deps = [os.path.abspath(__file__),
            os.path.join(REPO, 'tools', 'hqr_decompress.py'),
            os.path.join(REPO, 'tools', 'body_digest.py'),
            os.path.join(REPO, 'tools', 'bg_ref.py')]
    deps += [os.path.join(GOG_COMMON, fn) for _, _, fn in ARCHIVES if fn]
    for d in deps:
        if os.path.isfile(d) and os.path.getmtime(d) > out_t:
            return True
    return False


def logical_view(buf, placed):
    """Quel che il 68000 vede, ricavato DAL CONTENITORE GIA' SCRITTO.

    Serve al percorso "niente da fare", dove non si ripaga la decompressione
    degli archivi originali ma le attese vanno comunque calcolate sulla faccia
    giusta. Legge i flag e, dove c'e' il bit LZSS, espande - con lo stesso
    decompressore che usa il resto degli strumenti, cioe' la stessa lettura del
    formato che sta anche in engine_c/hal_hqr.c.
    """
    n_arch = struct.unpack_from('>H', buf, 6)[0]
    out = []
    for ai in range(n_arch):
        _toff, _n, fl = struct.unpack_from('>III', buf,
                                           HDR_SIZE + ai * ARCH_DIR_ENTRY)
        slots = []
        for off, size in placed[ai]:
            if size == 0:
                slots.append(None)
            elif fl & ARCH_FLAG_LZSS:
                orig, comp, ctype = lzss.read_entry_header(buf, off)
                body = buf[off + 10:off + 10 + comp]
                slots.append(body[:orig] if ctype == 0
                             else lzss.decompress_lzss(body, orig, ctype))
            else:
                slots.append(buf[off:off + size])
        out.append(slots)
    return out


def emit(buf, archives, placed, logical):
    """Scrive contenitore, header delle attese e manifest leggibile."""
    d = os.path.dirname(OUT)
    if not os.path.isdir(d):
        os.makedirs(d)
    with open(OUT, 'wb') as f:
        f.write(buf)
    print('hqr_pack: scritto %s (%d byte, %.2f MB)'
          % (os.path.relpath(OUT, REPO), len(buf), len(buf) / 1048576.0))

    pr = probes(buf, placed, logical)
    hdr = os.path.join(REPO, 'engine_c', 'hqr_expect.h')
    with open(hdr, 'w', newline='\n') as f:
        f.write(gen_expect_header(pr))
    print('hqr_pack: scritto engine_c/hqr_expect.h (%d sonde)' % len(pr))

    text = manifest(pr, archives)
    with open(os.path.join(REPO, 'cart', 'data_v6_manifest.txt'), 'w',
              newline='\n') as f:
        f.write(text + '\n')
    print()
    print(text)


def budget(scenes, spec):
    """--compress: DIMOSTRA il bilancio invece di stimarlo.

    Costruisce il contenitore per davvero, con la politica per archivio e la
    potatura a regola, e lo scrive in un file A PARTE. Non tocca ne' il
    contenitore in uso ne' le attese, perche' il lato Jaguar ancora non sa
    decomprimere e la build di M2 e' verificata: la misura non deve costare la
    cosa che funziona.

    Quel che questo modo prova e quel che non prova: prova che i byte, disposti
    davvero, ci stanno. NON prova che il 68000 li sappia leggere - quello e' il
    lavoro dopo, e va fatto solo se questo numero e' buono.
    """
    print('hqr_pack: BILANCIO (scene: %s), politica per archivio' % spec)
    buf, archives, placed, _logical = build(scenes, compress=True)

    import memory_map as mm
    _addr, _off, size = mm.cart_region('ROM_DATA')
    out = os.path.join(os.path.dirname(OUT), 'data_v6_compresso.bin')
    with open(out, 'wb') as f:
        f.write(buf)

    print()
    print('  contenitore %d byte (%.2f MB), scritto %s'
          % (len(buf), len(buf) / 1048576.0, os.path.relpath(out, REPO)))
    print('  ROM_DATA di adesso: %d byte -> %.0f%%'
          % (size, 100.0 * len(buf) / size))

    # Il confronto utile non e' contro ROM_DATA com'e' oggi: ROM_CODE e
    # ROM_CBLOB si riservano 768 KB e ne usano 11. La domanda vera e' quanto
    # bisogna lasciare a codice e blob perche' i dati ci stiano.
    _a, hdr_off, hdr_size = mm.cart_region('ROM_HEADER')
    cart = mm.CART_SIZE
    for reserve in (768, 512, 384, 256, 128):
        avail = cart - hdr_size - reserve * 1024
        verdict = 'ENTRA, %5d KB di margine' % ((avail - len(buf)) // 1024)
        if len(buf) > avail:
            verdict = 'sfora di %d KB' % ((len(buf) - avail + 1023) // 1024)
        print('  codice+blob %4d KB -> dati %5d KB : %s'
              % (reserve, avail // 1024, verdict))
    print()
    print('  (le attese e il contenitore in uso NON sono stati toccati)')


def main():
    scenes, spec = parse_scenes()
    if '--compress' in sys.argv:
        budget(scenes, spec)
        return
    if not stale(spec):
        # Il contenitore e' aggiornato, ma l'header delle attese deve esistere
        # comunque: la build del C lo include. Lo si rigenera dal file gia'
        # scritto, senza ridecomprimere niente.
        with open(OUT, 'rb') as f:
            buf = f.read()
        archives, placed = reread(buf)
        print('hqr_pack: %s e\' aggiornato (--force per rifarlo)'
              % os.path.relpath(OUT, REPO))
        pr = probes(buf, placed, logical_view(buf, placed))
        with open(os.path.join(REPO, 'engine_c', 'hqr_expect.h'), 'w',
                  newline='\n') as f:
            f.write(gen_expect_header(pr))
        if '--manifest' in sys.argv:
            print()
            print(manifest(pr, archives))
        return

    print('hqr_pack: decompressione degli archivi (scene: %s)' % spec)
    buf, archives, placed, logical = build(scenes, compress=True)
    emit(buf, archives, placed, logical)
    with open(SPEC, 'w', newline='\n') as f:
        f.write(spec + '\n')

    # La capienza si controlla QUI e non al packing della cartuccia: qui si sa
    # anche quanto si potrebbe risparmiare, e "non ci sta" senza un numero e'
    # un messaggio che non aiuta.
    import memory_map as mm
    _addr, _off, size = mm.cart_region('ROM_DATA')
    print()
    print('ROM_DATA: %d / %d byte usati (%.0f%%), %d KB liberi'
          % (len(buf), size, 100.0 * len(buf) / size, (size - len(buf)) // 1024))
    if len(buf) > size:
        raise SystemExit(
            'il contenitore sfora ROM_DATA di %d KB. Con --scenes tutte serve\n'
            'il bank switching; con un sottoinsieme piu\' piccolo no.'
            % ((len(buf) - size + 1023) // 1024))


def reread(buf):
    """Ricostruisce direttorio e posizioni da un contenitore gia' scritto.

    Serve al percorso "niente da fare": rigenerare hqr_expect.h senza pagare
    di nuovo la decompressione. Legge il contenitore con le stesse regole che
    usera' il 68000, quindi e' anche un controllo del formato.
    """
    magic, ver, n_arch = struct.unpack_from('>4sHH', buf, 0)
    if magic != MAGIC or ver != VERSION:
        raise SystemExit('%s: contenitore non riconosciuto, rifarlo con --force'
                         % os.path.relpath(OUT, REPO))
    archives = []
    placed = []
    for ai in range(n_arch):
        toff, n_entries, _ = struct.unpack_from('>III', buf,
                                               HDR_SIZE + ai * ARCH_DIR_ENTRY)
        slots = [struct.unpack_from('>II', buf, toff + si * ENTRY_SIZE)
                 for si in range(n_entries)]
        placed.append(slots)
        name = ARCHIVES[ai][1] if ai < len(ARCHIVES) else '?'
        archives.append((ai, name, [b'x' * s if s else None for _, s in slots]))
    return archives, placed


if __name__ == '__main__':
    main()
