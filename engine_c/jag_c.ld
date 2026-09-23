/* S24: linker script per il blob C m68k del Jaguar.
 * Il blob viene copiato dal cart a C_BLOB_BASE dal lato RMAC (InitCBlob in
 * scene_viewer.s).
 *
 * FIX S24: la prima stesura usava 0x1E0000, che cade DENTRO la griglia.
 * CUBE_DRAM parte a 0x1C0000 e la griglia e' (gz*64+gx)*50 byte = 0x32000,
 * quindi arriva a 0x1F2000. Slot libero reale = 0x1F2000..0x1F4000 (8 KB,
 * fra fine griglia e VERT_LIST).
 *
 * Deve restare allineato a C_BLOB_BASE/C_POC_* in src/scene_viewer.s.
 */

OUTPUT_ARCH(m68k)
ENTRY(entry_table)

MEMORY
{
    CBLOB (rwx) : ORIGIN = 0x1F2000, LENGTH = 0x2000
}

SECTIONS
{
    .text :
    {
        *(.entry)
        *(.text*)
        *(.rodata*)
    } > CBLOB

    .data :
    {
        *(.data*)
    } > CBLOB

    .bss :
    {
        . = ALIGN(2);
        __bss_start = .;
        *(.bss*)
        *(COMMON)
        . = ALIGN(2);
        __bss_end = .;
    } > CBLOB
}
