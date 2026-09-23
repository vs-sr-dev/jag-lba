"""JagLBA V6 - packer della cartuccia.

Il layout della cartuccia lo decide QUESTO script, non RMAC
(docs/skeleton_design.md 3.2). In V4 il tutto era concatenato con `.incbin`,
quindi l'indirizzo di ogni blob dipendeva dalla dimensione del codice
assembly: era la ragione per cui il blob C non poteva avere un indirizzo fisso
e finiva copiato in DRAM, dove non c'era spazio.

Qui ogni blob va al suo offset dichiarato in tools/memory_map.py, e sforare la
propria regione e' un errore di build invece che una corruzione silenziosa.

Uso: python tools/cart_build.py [--out build/jaglba_v6.j64]
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import memory_map as mm
from cart_header import write_boot_header

REPO = mm.REPO

# (regione, file sorgente, obbligatorio)
BLOBS = [
    ("ROM_CODE",  os.path.join("build", "main_v6_raw.abs"), True),
    ("ROM_CBLOB", os.path.join("engine_c", "engine_blob.bin"), True),
    # cart/scene_all.bin e' l'artefatto V4 (5,80 MB) e NON si riusa: era pensato
    # per una cartuccia in cui il codice stava in coda ai dati. Il packer V6 lo
    # rigenerera' a M2 con il layout nuovo, in cart/data_v6.bin.
    ("ROM_DATA",  os.path.join("cart", "data_v6.bin"), False),
]


def read_target_hw():
    """Il valore di TARGET_HW da src/main_v6.s, che ne resta l'unico padrone.

    Serve a dare due NOMI diversi alle due ROM. Non e' cosmesi: le due build si
    vedono giuste solo sul rispettivo bersaglio, sono indistinguibili a occhio
    dal nome del file, e provare quella sbagliata produce un guasto che sembra
    un difetto del programma. In S33 e' costato un giro di caccia a un fantasma.

    Se la riga non si trova non si indovina: si alza le mani. Un nome sbagliato
    e' peggio di nessun nome, perche' verrebbe creduto.
    """
    src = os.path.join(REPO, "src", "main_v6.s")
    with open(src, encoding="utf-8") as f:
        m = re.search(r"^TARGET_HW\s+EQU\s+(\d+)", f.read(), re.M)
    if not m:
        raise SystemExit("cart_build: TARGET_HW non trovato in src/main_v6.s")
    return int(m.group(1))


def main():
    hw = read_target_hw()
    # S51: build_v6.bat assembla anche l'ALTRO bersaglio, da una copia del
    # sorgente con TARGET_HW rovesciato, e lo impacchetta con --hw e --code.
    # Il sorgente resta l'unico padrone del proprio valore: qui si dice solo
    # quale ROM si sta scrivendo, perche' il nome del file lo deve dire.
    if "--hw" in sys.argv:
        hw = int(sys.argv[sys.argv.index("--hw") + 1])
    blobs = list(BLOBS)
    if "--code" in sys.argv:
        code = sys.argv[sys.argv.index("--code") + 1]
        blobs = [(n, code if n == "ROM_CODE" else r, q) for n, r, q in blobs]
    out = os.path.join(REPO, "build",
                       "jaglba_v6_%s.j64" % ("ferro" if hw else "emu"))
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]

    mm.check()

    cart = bytearray(b"\xFF" * mm.CART_SIZE)
    write_boot_header(cart, mm.cart_region("ROM_CODE")[0])

    print("cart_build: layout V6")
    for name, rel, required in blobs:
        addr, off, size = mm.cart_region(name)
        path = os.path.join(REPO, rel)
        if not os.path.isfile(path):
            if required:
                raise SystemExit("manca %s (regione %s)" % (rel, name))
            print("  %-10s $%06X  ---  (assente, riservata %d KB)"
                  % (name, addr, size // 1024))
            continue
        with open(path, "rb") as f:
            data = f.read()
        if len(data) > size:
            raise SystemExit(
                "%s: %d byte non entrano nella regione %s (%d byte). "
                "Allargare la regione in tools/memory_map.py."
                % (rel, len(data), name, size))
        cart[off:off + len(data)] = data
        print("  %-10s $%06X  %8d byte  (%d%% di %d KB)  <- %s"
              % (name, addr, len(data), 100 * len(data) // size,
                 size // 1024, rel))

    d = os.path.dirname(out)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(out, "wb") as f:
        f.write(bytes(cart))
    print("cart_build: scritto %s (%d MB, entry $%06X)"
          % (out, len(cart) // 1024 // 1024, mm.cart_region("ROM_CODE")[0]))
    print("cart_build: bersaglio TARGET_HW=%d -> %s"
          % (hw, "JAGUAR REALE" if hw else "BigPEmu"))


if __name__ == "__main__":
    main()
