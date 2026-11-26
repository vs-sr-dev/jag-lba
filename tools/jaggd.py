# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Samuele Voltan.  See LICENSE.
"""The Jaguar GameDrive over its side USB port: a ROM onto the real Jaguar without the SD card.

    python tools/jaggd.py reboot [menu|debug|rom]
    python tools/jaggd.py run ROM.j64           upload to $800000 and boot it from the BIOS
    python tools/jaggd.py upload FILE --base A [--exec X]   (from the debug stub)
    python tools/jaggd.py listen [--port COMn] [--secs N]

The protocol is the one cubanismo/open_jaggd reverse engineered from SainT's jaggd.exe:
USB 03EB:800E, product "RetroHQ Jaguar GameDrive".  Interface 0 (vendor, WinUSB on Windows
with no setup) takes the commands as vendor control transfers (bRequest 1, index 0) and the
data on bulk OUT EP2 in 16 KB pieces; it also has a bulk IN EP1 that jaggd.exe never reads.
Interfaces 1+2 are a CDC serial port (usbser, COM8 here): the channel back from the Jaguar,
fed by the GD BIOS's GD_DebugString (jaguar/gd_hello.s, jaguar_kb/05), not by JERRY's UART.

pyusb needs libusb-1.0.dll: from PATH, else MSYS2's mingw64 copy.

While a command goes, the serial port is read and thrown away (Drain): a ROM that logs holds the
micro with a line nobody has read, and then the micro ignores reboot and upload (20/11: with a
reader the reboot went through at once, without one never).
"""
import argparse
import os
import struct
import sys
import time

VID, PID = 0x03EB, 0x800E
CHUNK = 16 * 1024
REBOOT = {'menu': 0x00, 'debug': 0x01, 'rom': 0x06}
MEM_LO, MEM_HI = 0x2000, 0xE00000
LIBUSB_DLLS = (r'C:\msys64\mingw64\bin\libusb-1.0.dll', r'C:\msys64\ucrt64\bin\libusb-1.0.dll')


class Drain:
    """The GameDrive's serial port read in a thread, the bytes dropped, from PRE seconds before
    the commands (the line the micro holds is taken) until stop()."""
    PRE = 1.2

    def __init__(self):
        import threading
        self.ser, self.stop_ev = None, threading.Event()
        port = find_port()
        if not port:
            return
        import serial
        try:
            self.ser = serial.Serial(port, 115200, timeout=0.1)
        except serial.SerialException:      # (someone else reads it: as good)
            return
        self.th = threading.Thread(target=self._run, daemon=True)
        self.th.start()
        time.sleep(self.PRE)

    def _run(self):
        while not self.stop_ev.is_set():
            try:
                self.ser.read(4096)
            except Exception:
                return

    def stop(self):
        if self.ser:
            self.stop_ev.set()
            self.th.join(1)
            self.ser.close()


def open_gd():
    import usb.core
    import usb.util
    import usb.backend.libusb1 as libusb1
    drain = Drain()
    backend = libusb1.get_backend()
    for dll in LIBUSB_DLLS:
        if backend is None and os.path.exists(dll):
            backend = libusb1.get_backend(find_library=lambda _n, dll=dll: dll)
    dev = usb.core.find(idVendor=VID, idProduct=PID, backend=backend)
    if dev is None:
        drain.stop()
        sys.exit('Jaguar GameDrive not found (USB cable, Jaguar powered on?)')
    usb.util.claim_interface(dev, 0)
    dev.prowl_drain = drain
    return dev


def close_gd(dev):
    import usb.util
    usb.util.release_interface(dev, 0)
    usb.util.dispose_resources(dev)
    dev.prowl_drain.stop()


def command(dev, payload):
    n = dev.ctrl_transfer(0x41, 1, 0, 0, bytes(payload), 2000)   # VENDOR | INTERFACE, OUT
    if n != len(payload):
        sys.exit(f'GameDrive took {n} of {len(payload)} command bytes')


def reboot(dev, target):
    command(dev, [0x02, REBOOT[target]])
    time.sleep(1.5)                                     # jaggd.exe waits this long


def upload(dev, data, base, exec_addr):
    """exec_addr 0 = upload only, 0xFFFFFFFF = boot the cartridge from the BIOS."""
    size = len(data)
    pkt = bytes([0x14, 0x02]) + struct.pack('<I', size) + bytes([0x0E, 0x04]) \
        + struct.pack('>III', base, size, exec_addr)
    command(dev, pkt)
    t0, done = time.time(), 0
    while done < size:
        done += dev.write(0x02, data[done:done + CHUNK], 120000)
        print(f'\r  {done * 100 // size:3d}%', end='', flush=True)
    dt = time.time() - t0
    print(f'\r  {size} bytes to ${base:06X} in {dt:.1f} s ({size / 1024 / max(dt, 1e-3):.0f} KB/s)')


def rom_entry(data):
    """The ROM header open_jaggd accepts: the same long 4 times at $400, the entry at $404."""
    if len(data) > 0x2000:
        w = data[0x400:0x404]
        start = struct.unpack('>I', data[0x404:0x408])[0]
        if w == bytes([w[0]]) * 4 and 0x800000 <= start < 0xE00000:
            return start
    return None


def number(s):
    return int(s[1:], 16) if s.startswith('$') else int(s, 0)


def find_port():
    """The GameDrive's CDC interface (MI_01) as a COM port name, or None."""
    from serial.tools import list_ports
    for p in list_ports.comports():
        if p.vid == VID and p.pid == PID:
            return p.device
    return None


def lines(secs, port=None):
    """(seconds since the start, line) from the GameDrive's serial port for secs seconds;
    GD_DebugString ends each line with a newline of its own."""
    import serial
    port = port or find_port()
    if not port:
        sys.exit("the GameDrive's serial port is not there")
    buf, t0 = b'', time.time()
    with serial.Serial(port, 115200, timeout=0.2) as ser:
        while not secs or time.time() - t0 < secs:      # (0: until Ctrl+C)
            buf += ser.read(4096)
            while b'\n' in buf:
                ln, buf = buf.split(b'\n', 1)
                ln = ln.decode('latin-1').strip()
                if ln:
                    yield time.time() - t0, ln


def run_rom(path):
    """A cartridge ROM onto the Jaguar and booted (the GameDrive's debug stub first)."""
    data = open(path, 'rb').read()
    if rom_entry(data) is None:
        sys.exit(f'{path}: no cartridge header at $400')
    dev = open_gd()
    try:
        reboot(dev, 'debug')                            # uploads are taken from the stub
        print(f'{os.path.basename(path)} -> $800000, boot from the BIOS')
        upload(dev, data, 0x800000, 0xFFFFFFFF)
    finally:
        close_gd(dev)


def listen(port, baud, secs):
    import serial
    port = port or find_port()
    with serial.Serial(port, baud, timeout=0.2) as ser:
        print(f'{port} @ {baud}, Ctrl+C to stop')
        end = time.time() + secs if secs else None
        try:
            while end is None or time.time() < end:
                b = ser.read(256)
                if b:
                    print(b.hex(' '), '|', b.decode('latin-1').translate(
                        {i: '.' for i in range(32)}), flush=True)
        except KeyboardInterrupt:
            pass


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('reboot')
    p.add_argument('target', nargs='?', default='menu', choices=REBOOT)
    p = sub.add_parser('run')
    p.add_argument('rom')
    p = sub.add_parser('upload')
    p.add_argument('file')
    p.add_argument('--base', type=number, required=True)
    p.add_argument('--exec', type=number, default=0, dest='exec_addr')
    p = sub.add_parser('listen')
    p.add_argument('--port', default=None, help="default: the GameDrive's own")
    p.add_argument('--baud', type=int, default=115200)
    p.add_argument('--secs', type=float, default=0)
    a = ap.parse_args(argv)

    if a.cmd == 'listen':
        return listen(a.port, a.baud, a.secs)
    if a.cmd == 'run':
        return run_rom(a.rom)
    dev = open_gd()
    try:
        if a.cmd == 'reboot':
            reboot(dev, a.target)
        else:
            data = open(a.file, 'rb').read()
            for name, v in (('base', a.base), ('exec', a.exec_addr)):
                if v and not MEM_LO <= v < MEM_HI:
                    sys.exit(f'{name} ${v:X} outside [${MEM_LO:X}, ${MEM_HI:X})')
            upload(dev, data, a.base, a.exec_addr)
    finally:
        close_gd(dev)


if __name__ == '__main__':
    main()
