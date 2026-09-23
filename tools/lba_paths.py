"""Where YOUR copy of LBA1 lives - the only place the tools ask.

This repository is BYOA (bring your own assets): not one byte of the game is
in it, and every tool that reads an .HQR finds it through this module.

The folder is LBA1_DIR, the game's install folder: the one that holds
`Common` (all the .HQR archives) and, in the GOG re-release, `CommonClassic`
(where that edition keeps TEXT.HQR). It comes from, in order:

  1. the environment variable LBA1_DIR;
  2. `local.cfg` at the repository root, a line `LBA1_DIR=...` - the same
     untracked file build_v6.bat and tools/emu_run.ps1 read (copy
     local.cfg.example and edit it).

Nothing is guessed: a tool that needs the game and cannot find it stops and
says which file to write.
"""
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_CFG = os.path.join(REPO, 'local.cfg')


def local_cfg():
    """local.cfg as a dict: KEY=VALUE lines, '#' comments, blanks ignored."""
    out = {}
    if not os.path.isfile(LOCAL_CFG):
        return out
    with open(LOCAL_CFG, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def setting(key):
    return os.environ.get(key) or local_cfg().get(key)


def _lba1_dir():
    d = setting('LBA1_DIR')
    if not d:
        raise SystemExit(
            'LBA1_DIR is not set: copy local.cfg.example to local.cfg and '
            'write the folder of your LBA1 install (the one containing '
            '"Common"), or set the environment variable LBA1_DIR.')
    if not os.path.isdir(os.path.join(d, 'Common')):
        raise SystemExit('LBA1_DIR=%s has no "Common" folder' % d)
    return d


def _classic(d):
    # TEXT.HQR: the GOG edition keeps it in CommonClassic; other copies have
    # it next to the rest, in Common.
    c = os.path.join(d, 'CommonClassic')
    if os.path.isfile(os.path.join(c, 'TEXT.HQR')):
        return c
    return os.path.join(d, 'Common')


def __getattr__(name):
    # Lazy (PEP 562): importing this module costs nothing and asks nothing;
    # only a tool that touches LBA1_DIR, COMMON or CLASSIC needs the game.
    if name == 'LBA1_DIR':
        return _lba1_dir()
    if name == 'COMMON':
        return os.path.join(_lba1_dir(), 'Common')
    if name == 'CLASSIC':
        return _classic(_lba1_dir())
    raise AttributeError(name)


def common(name):
    """Path of an archive in Common, e.g. common('BODY.HQR')."""
    return os.path.join(_lba1_dir(), 'Common', name)


def repo(*parts):
    """A path inside this repository (extracted/, cart/, build/...)."""
    return os.path.join(REPO, *parts)
