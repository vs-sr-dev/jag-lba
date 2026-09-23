| S24 PoC: tabella di entry a offset fissi, chiamata dal lato RMAC 68k.
| Ogni jmp e' abs.l = 6 byte, quindi:
|   blob+0 -> poc_init
|   blob+6 -> poc_frame
| Il linker script (jag_c.ld) piazza .entry come prima sezione del blob.

	.section .entry,"ax"
	.global entry_table
entry_table:
	jmp	poc_init
	jmp	poc_frame
