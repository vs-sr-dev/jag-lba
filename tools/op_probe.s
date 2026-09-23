;
; op_probe.s -- emit BMobj phrase pairs via RMAC's bitmap macro for several
; configurations we want to use:
;   1) 16bpp, 320 px wide, IWIDTH=40 phrases, sanity check vs SR (= $0140C000)
;   2)  8bpp, 640 px wide, IWIDTH=80 phrases, INDEX -- target for V3.4
;   3) 16bpp, 640 px wide, IWIDTH=160 phrases, NOTRANS -- sanity check vs JagLBA V3.3 (= $0280C00E)
;   4)  8bpp, 640 px wide, IWIDTH=80, PITCH=2 -- S19 Step 4 interlace 480i target
; Just dumps phrase pairs per case, read by a Python helper.
;
    .include "jaguar.inc"

OP_Data:
    .objproc
    bitmap  $100000, 0, 29, 40, 40, 240, 4, 0, NOTRANS RELEASE, 0, 1
    bitmap  $100000, 0, 29, 80, 80, 240, 3, 1, NOTRANS RELEASE, 0, 1
    bitmap  $100000, 0, 29, 160, 160, 240, 4, 0, NOTRANS RELEASE, 0, 1
    bitmap  $100000, 0, 29, 80, 80, 240, 3, 1, NOTRANS RELEASE, 0, 2
    .68000
OP_Data_End:

    END
