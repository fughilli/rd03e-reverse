/* From-source GD32E230K8 linker script — self-contained (no INCLUDE), so both
 * cargo (`-C link-arg=-Tlink.x`, linker cwd = this dir) and Bazel/rules_rust
 * (passed via the firmware's cc_library linkopts) find everything in one file.
 *
 * GD32E230K8: 64 KB flash @ 0x08000000, 8 KB SRAM @ 0x20000000 (from RE).
 * Pairs with src/rt.rs (vector table + reset handler).
 */
MEMORY
{
  FLASH : ORIGIN = 0x08000000, LENGTH = 64K
  RAM   : ORIGIN = 0x20000000, LENGTH = 8K
}

ENTRY(Reset);
EXTERN(RESET_VECTOR);
EXTERN(EXCEPTIONS);

/* Initial stack pointer = top of RAM (full-descending). */
_stack_start = ORIGIN(RAM) + LENGTH(RAM);

SECTIONS
{
  /* Vector table at the flash base: [0]=initial SP, [1]=Reset, [2..]=handlers. */
  .vector_table ORIGIN(FLASH) :
  {
    LONG(_stack_start);
    KEEP(*(.vector_table.reset_vector));
    KEEP(*(.vector_table.exceptions));
  } > FLASH

  .text :
  {
    *(.text .text.*);
    . = ALIGN(4);
  } > FLASH

  .rodata :
  {
    *(.rodata .rodata.*);
    . = ALIGN(4);
  } > FLASH

  /* Initialised data: VMA in RAM, LMA in flash; rt::Reset copies it. */
  .data : ALIGN(4)
  {
    _sdata = .;
    *(.data .data.*);
    . = ALIGN(4);
    _edata = .;
  } > RAM AT > FLASH
  _sidata = LOADADDR(.data);

  /* Zero-initialised data; rt::Reset clears it. NOLOAD = no flash footprint. */
  .bss (NOLOAD) : ALIGN(4)
  {
    _sbss = .;
    *(.bss .bss.*);
    *(COMMON);
    . = ALIGN(4);
    _ebss = .;
  } > RAM

  /* No unwinding on a no_std / panic=abort build. */
  /DISCARD/ :
  {
    *(.ARM.exidx .ARM.exidx.*);
    *(.ARM.extab .ARM.extab.*);
  }
}
