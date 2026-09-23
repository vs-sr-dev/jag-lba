| JagLBA V6 - startup del blob C dell'engine.
|
| Il blob gira IN PLACE dalla cartuccia (docs/skeleton_design.md 3.1): .text e
| .rodata restano in ROM, solo .data e .bss stanno in DRAM. Questo file fa le
| due cose che il C non puo' fare da solo prima di partire:
|   1. copia .data dalla sua LMA in ROM alla VMA in DRAM
|   2. azzera .bss
|
| ABI verso RMAC - tabella di entry a offset FISSI (jmp abs.l = 6 byte):
|   blob+0 : _c_init()      -> d0 = JAG_ENGINE_MAGIC, oppure codice d'errore
|   blob+6 : engine_frame()
|   blob+12: engine_vbl()     once per video interrupt, from ViHandler (S51)
| Gli offset sono dichiarati in include/cart_map.inc (C_ENTRY_INIT/C_ENTRY_FRAME),
| generato dallo stesso master Python del linker script.
|
| Indirizzi assoluti via `move.l #sym,%aN`: i simboli stanno in DRAM ($147000)
| mentre il codice sta in ROM ($842000), quindi il pc-relative di `lea` sarebbe
| fuori portata.

	.section .entry,"ax"
	.global entry_table
entry_table:
	jmp	_c_init
	jmp	engine_frame
	jmp	engine_vbl

	.text
	.global _c_init
_c_init:
	move.l	#__data_lma, %a0
	move.l	#__data_start, %a1
	move.l	#__data_end, %a2
1:	cmpa.l	%a1, %a2
	bls.s	2f
	move.b	(%a0)+, (%a1)+
	bra.s	1b

2:	move.l	#__bss_start, %a1
	move.l	#__bss_end, %a2
3:	cmpa.l	%a1, %a2
	bls.s	4f
	clr.b	(%a1)+
	bra.s	3b

4:	jmp	engine_init		| tail call: d0 di engine_init torna a RMAC
