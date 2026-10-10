/* cosim.h -- shared memory mailbox between the firmware and the co-simulation host.
 *
 * The MCU has no usable link to ngspice on its own, so the exchange is a block
 * of RAM that both sides agree on. QEMU's human monitor reads and writes it
 * with `xp`/`wp` exactly as it does the vector table, so the host can drive the
 * pins and sample the ADC without any code on the host side that has to know
 * how a Cortex-M core works.
 *
 * Layout (little endian, naturally aligned):
 *
 *   offset  size  field
 *   0x00     4    magic        set by the host, checked by the firmware
 *   0x04     4    host_lo      host -> MCU, PA6 / TIM3_CH1 level, 0 or 1
 *   0x04     4    host_gain    host -> MCU, PA7 level (adjacent word)
 *   0x08     4    mcu_stamp    MCU -> host, incremented every DMA sample
 *   0x0C     4    adc_i        MCU -> host, PA0 raw
 *   0x10     4    adc_q        MCU -> host, PA1 raw
 *   0x14     4    adc_stamp    MCU -> host, incremented when a new pair lands
 *
 * The stamps are what make the exchange correct rather than merely connected:
 * the host must know whether the values it just read are new, and the firmware
 * must know whether the pin state it just applied is current. Counting rather
 * than flagging means a missed update is detectable instead of silent.
 */
#ifndef COSIM_H
#define COSIM_H

#include <stdint.h>

#define COSIM_MAGIC 0x43305349u /* "CS0S" */
#define COSIM_BASE 0x20004000u

/* host -> MCU */
#define COSIM_OFF_MAGIC 0x00
#define COSIM_OFF_LO 0x04
#define COSIM_OFF_GAIN 0x08
#define COSIM_OFF_HOST_REQ 0x0C

/* MCU -> host */
#define COSIM_OFF_MCU_STAMP 0x10
#define COSIM_OFF_ADC_I 0x14
#define COSIM_OFF_ADC_Q 0x18
#define COSIM_OFF_ADC_STAMP 0x1C

#define COSIM_WORDS 8

/* Volatile throughout: these are read and written by two agents, and without
 * volatile the compiler is free to cache a register in a core-local copy and
 * the exchange silently stops happening. */
static inline volatile uint32_t *cosim(void) {
  return (volatile uint32_t *)COSIM_BASE;
}

#endif /* COSIM_H */
