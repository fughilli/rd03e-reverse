# Ghidra headless PRE-script for GD32E230 (Cortex-M23) firmware images.
#
# The Intel-Hex / raw-bin loaders give you only the flash block. This adds the
# SRAM, peripheral and system-memory regions from the Rd-03E handoff so that
# fixed-address MMIO references resolve (and become name-able) during analysis,
# marks the peripheral banks volatile so the decompiler won't fold their reads,
# and sets the reset vector as an entry point so auto-analysis starts from real
# code instead of guessing.
#
# Addresses are from the GD32E23x family map; confirm exact peripheral offsets
# against the GD32E230 SVD / user manual (see docs/re/09-handoff.md).
#
# Run via analyzeHeadless:  ... -preScript SetupMemoryMap.py [base_hex]
#
# @category ReverseEngineering
# @runtime Jython

args = getScriptArgs()
base = int(args[0], 16) if args else 0x08000000

mem = currentProgram.getMemory()
space = currentProgram.getAddressFactory().getDefaultAddressSpace()


def A(off):
    return space.getAddress(off)


# name, start, length, volatile, execute
BLOCKS = [
    ("SRAM",       0x20000000, 0x2000,  False, True),   # 8 KB; initial SP at top
    ("PERIPH_APB", 0x40000000, 0x30000, True,  False),  # RCU/USART/SPI/I2C/TIM/ADC/DMA
    ("GPIO_AHB2",  0x48000000, 0x2000,  True,  False),  # GPIOA/B/C/F
    ("SYSMEM",     0x1FFFEC00, 0x1400,  False, True),   # boot ROM + option bytes
]

tx = currentProgram.startTransaction("SetupMemoryMap")
ok = True
try:
    for name, start, length, vol, execu in BLOCKS:
        a = A(start)
        if mem.getBlock(a) is not None:
            print("[SetupMemoryMap] block already covers 0x%08x; skipping %s" % (start, name))
            continue
        try:
            blk = mem.createUninitializedBlock(name, a, length, False)
            blk.setRead(True)
            blk.setWrite(True)
            blk.setExecute(execu)
            blk.setVolatile(vol)
            print("[SetupMemoryMap] added %-11s 0x%08x +0x%05x (volatile=%s)"
                  % (name, start, length, vol))
        except Exception as e:
            print("[SetupMemoryMap] WARN could not add %s: %s" % (name, e))

    # Vector table: word 0 = initial SP, word 1 = Reset_Handler (Thumb bit set).
    try:
        sp = getInt(A(base)) & 0xffffffff
        reset = getInt(A(base + 4)) & 0xffffffff
        entry = A(reset & ~1)          # clear the Thumb bit for the address
        createFunction(entry, None)
        addEntryPoint(entry)
        print("[SetupMemoryMap] vector table: SP=0x%08x  Reset_Handler=0x%08x"
              % (sp, reset & ~1))
    except Exception as e:
        print("[SetupMemoryMap] WARN reset-vector setup skipped: %s" % e)
finally:
    currentProgram.endTransaction(tx, ok)
