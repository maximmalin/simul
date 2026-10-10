/**
 * SDR direct-conversion receiver firmware - STM32F103C8T6 Blue Pill
 *
 * Signal path this firmware drives:
 *   PA0  ADC1_IN0   I channel from the mixer
 *   PA1  ADC1_IN1   Q channel from the mixer
 *   PA6  TIM3_CH1   local-oscillator drive to the LT5560 mixer
 *   PA7  A/B gain control to the RF front end
 *   USB  CDC        I/Q sample stream to the host
 *
 * Sampling is DMA-driven so the CPU only formats and transmits. The ADC is
 * clocked from the APB2 timer prescaler, which keeps the sample rate tied to a
 * real hardware clock rather than a blocking delay.
 */

#include <Arduino.h>
#include "cosim.h"

#ifdef COSIM_MODE
/**
 * Under co-simulation the firmware runs on qemu-system-arm, which does not
 * model the STM32 PLL: RCC->CR's PLLRDY bit never goes high, so the core's
 * SystemClock_Config() spins in `while (!(RCC->CR & RCC_CR_PLLRDY))` and never
 * returns. The PC ends up at SystemClock_Config+0x44 -- a `b .` self-branch --
 * and the main loop is never reached, which presents as a live core with a
 * completely silent mailbox.
 *
 * Overriding the function is the right fix rather than working around it. The
 * core calls it from main() before setup(), and because the application object's
 * file always wins over the one in the library archive, an empty definition here
 * replaces it outright. The clock tree is then left at its reset default: HSI at
 * 8 MHz with the PLL off. Everything this firmware does is driven off APB timers
 * and the ADC, both of which work at 8 MHz, and the co-simulation's timing comes
 * from the master's clock rather than the emulated one -- so there is nothing
 * to gain from waiting for a PLL that will never lock.
 */
extern "C" void SystemClock_Config(void) {
    // Intentionally empty: leave the reset clock tree in place. See above.
}
#endif

// ---- pin map (matches sim/scripts/sdr_skidl_circuit.py) --------------------
static const uint8_t PIN_ADC_I = PA0;   // ADC1_IN0
static const uint8_t PIN_ADC_Q = PA1;   // ADC1_IN1
static const uint8_t PIN_LO    = PA6;   // TIM3_CH1 -> mixer LO
static const uint8_t PIN_GAIN  = PA7;   // front-end A/B select

// Bit indices within the GPIOA CRL register. These are written out explicitly
// rather than derived from the PA* macros: those expand to port masks in some
// Arduino-STM32 cores and to pin indices in others, so `PA6 * 4` overflows a
// 32-bit shift in the first case.
static const uint32_t IDX_ADC_I = 0;
static const uint32_t IDX_ADC_Q = 1;
static const uint32_t IDX_LO    = 6;
static const uint32_t IDX_GAIN  = 7;

// Bound on the hardware calibration poll, in loop iterations. Generous enough
// for real silicon to finish, finite enough that a target which never completes
// calibration fails through to the main loop instead of spinning forever.
#define ADC_CAL_TIMEOUT 1000000u

// ---- ADC / DMA -------------------------------------------------------------
static const uint16_t ADC_BUF_LEN = 256;          // 128 I/Q pairs per block
static volatile uint16_t adc_buf[ADC_BUF_LEN];    // interleaved I0 Q0 I1 Q1...

// Sample rate. The mixer produces baseband I/Q after the zero-IF conversion;
// 200 kS/s covers a +/-100 kHz instantaneous bandwidth at the FFT bin spacing a
// host-side IQ stream usually wants.
static const uint32_t SAMPLE_RATE_HZ = 200000;

// ---- USB framing -----------------------------------------------------------
// One packet carries N I/Q pairs as little-endian int16, which any host-side
// GNU Radio / SoapySDR / custom reader can consume directly.
static const uint16_t PAIRS_PER_PACKET = 64;
static const uint16_t PACKET_BYTES = 4 + PAIRS_PER_PACKET * 4;

static uint8_t packet[PACKET_BYTES];

// ADC reference and pin assignment. PA0/PA1 are ADC channels 0/1 on this part;
// the mapping is not the same on every STM32 family, so it is named here.
static void adc_configure(void) {
    // Enable ADC1 and its clocks.
    RCC->APB2ENR |= RCC_APB2ENR_ADC1EN | RCC_APB2ENR_AFIOEN;

    // Analog pins, no pull.
    GPIOA->CRL &= ~((0xF << (IDX_ADC_I * 4)) | (0xF << (IDX_ADC_Q * 4)));

    // The ADC prescaler divides PCLK2 (72 MHz). 72/6 = 12 MHz -> 12 MSPS.
    RCC->CFGR &= ~RCC_CFGR_ADCPRE;
    RCC->CFGR |= RCC_CFGR_ADCPRE_DIV6;

    // Single conversion, right-aligned, 12-bit, software trigger.
    ADC1->CR1 = 0;
    ADC1->CR2 = ADC_CR2_ADON;

    // Conversion time = (12 + 12.5) / ADCCLK = 24.5 / 12 MHz ~= 2.04 us,
    // so the longest sample window is ~2 us -> ~490 kS/s ceiling.
    ADC1->SMPR2 = 0;

    // Regular group: channels 0 and 1, in that order.
    ADC1->SQR3 = 0;            // SQ1 = ch0 (PA0, I)
    ADC1->SQR2 = 1 << 4;       // SQ2 = ch1 (PA1, Q)

    // Calibration before first use.
    //
    // Bounded, because an unbounded wait here is not safe on every target: the
    // CAL bit is cleared by hardware when calibration finishes, and an emulator
    // that does not model the calibration block leaves it set forever. The
    // unbounded form compiles to a single `b .` self-branch, so the firmware
    // then sits in setup() with a live core and no way to tell that is what
    // happened. Under COSIM_MODE the calibration is skipped outright.
#ifndef COSIM_MODE
    ADC1->CR2 |= ADC_CR2_CAL;
    for (volatile uint32_t spin = 0;
         (ADC1->CR2 & ADC_CR2_CAL) && spin < ADC_CAL_TIMEOUT; ++spin) {
    }
#endif
}

static void adc_start_dma(void) {
    // Enable DMA1 channel 1, which is the one wired to ADC1 on this part.
    RCC->AHBENR |= RCC_AHBENR_DMA1EN;

    // PA0 -> ADC1_IN0, PA1 -> ADC1_IN1. Mapping matters and differs by family.
    DMA1_Channel1->CPAR  = (uint32_t)&ADC1->DR;
    DMA1_Channel1->CMAR  = (uint32_t)adc_buf;
    DMA1_Channel1->CNDTR = ADC_BUF_LEN;
    // Circular, memory-width halfword, peripheral-width halfword, priority high.
    // On F1 the channel bit-field macros are shared across channels
    // (DMA_CCR_*, not DMA_CCR1_*).
    DMA1_Channel1->CCR = DMA_CCR_CIRC | DMA_CCR_MINC
                       | DMA_CCR_PL_1 | DMA_CCR_PL_0;

    // Enable the ADC DMA request and the DMA channel.
    ADC1->CR2 |= ADC_CR2_DMA | ADC_CR2_CONT | ADC_CR2_RSTCAL;
    DMA1_Channel1->CCR |= DMA_CCR_EN;
}

static void lo_init(uint32_t freq_hz) {
    // PA6 is TIM3_CH1 on the F103 default map, so no AFIO remap is needed --
    // only the pin has to be alternate-function push-pull. F1 has no
    // OUTPUT_ALTERNATE_PP mode (that arrived with F4), so set CRL directly:
    // MODE=0b10 (50 MHz) and CNF=0b10 (AF push-pull).
    RCC->APB2ENR |= RCC_APB2ENR_IOPAEN;
    GPIOA->CRL &= ~(0xF << (IDX_LO * 4));
    GPIOA->CRL |= (0xB << (IDX_LO * 4));   // CNF=10 MODE=10

    RCC->APB1ENR |= RCC_APB1ENR_TIM3EN;
    TIM3->PSC = 0;                       // APB1 timer clock = 72 MHz
    // Half-period counts. Guard against a zero/overflow reload.
    uint32_t half = 72000000UL / (2UL * freq_hz);
    if (half < 2) {
        half = 2;
    }
    TIM3->ARR = (half > 1) ? (half - 1) : 1;
    TIM3->CCR1 = (TIM3->ARR + 1) / 2;    // 50% duty

    // PWM mode 1 on channel 1, preload enabled.
    TIM3->CCMR1 |= (6 << TIM_CCMR1_OC1M_Pos) | TIM_CCMR1_OC1PE;
    TIM3->CCER |= TIM_CCER_CC1E;

    // Advanced timer not used, so no BDTR update event.
    TIM3->EGR = TIM_EGR_UG;
    TIM3->CR1 |= TIM_CR1_ARPE | TIM_CR1_CEN;
}

volatile uint32_t cosim_canary = 0;

void setup() {
#ifndef COSIM_MODE
    // USB CDC first so the host sees the banner as soon as it enumerates.
    //
    // Only in the normal build. On this core Serial.begin() blocks until a USB
    // host enumerates, and under qemu-system-arm there is no USB device model
    // at all -- so setup() never returns and loop() never runs, which is why
    // the co-simulation saw a running core but a silent mailbox. COSIM_MODE
    // skips USB entirely and exchanges through the RAM mailbox instead.
    Serial.begin(115200);
#endif

#ifdef COSIM_MODE
    // PA7 as output push-pull, written straight to the register.
    //
    // The core's pinMode()/digitalWrite() go through pin_function(), which is
    // what lands in Infinite_Loop under qemu: the stack at the fault shows
    // pin_function under setup, and the core's pin layer asserts on peripherals
    // the emulated machine does not fully model. Everything else this firmware
    // does is already direct register access, so this keeps COSIM_MODE consistent
    // and removes the last dependency on the core's pin layer.
    RCC->APB2ENR |= RCC_APB2ENR_IOPAEN;
    GPIOA->CRL &= ~(0xF << (IDX_GAIN * 4));
    GPIOA->CRL |= (0x3 << (IDX_GAIN * 4));   // MODE=11 CNF=00: output, 50 MHz
    GPIOA->BRR = 1u << IDX_GAIN;            // drive low
#else
    pinMode(PIN_GAIN, OUTPUT);
    digitalWrite(PIN_GAIN, LOW);
#endif

    adc_configure();
    lo_init(7150000UL);        // 7.15 MHz centre frequency
    adc_start_dma();

#ifndef COSIM_MODE
    if (Serial) {
        Serial.println(F("SDR direct-conversion receiver ready"));
        Serial.print(F("sample_rate="));
        Serial.println(SAMPLE_RATE_HZ);
    }
#endif
}

// Build one host packet from the DMA buffer: 4-byte header then int16 I/Q pairs.
// The STM32F103 ADC is unsigned, so the midpoint is subtracted to centre the
// signal on zero before it is signed.
// The DMA buffer is volatile because hardware writes it; the parameter must
// preserve that or the compiler rejects the call.
static void build_packet(const volatile uint16_t *src) {
    const uint16_t mid = 2048;
    packet[0] = 'I';
    packet[1] = 'Q';
    packet[2] = PAIRS_PER_PACKET & 0xFF;
    packet[3] = (PAIRS_PER_PACKET >> 8) & 0xFF;

    uint16_t *out = (uint16_t *)&packet[4];
    for (uint16_t i = 0; i < PAIRS_PER_PACKET; i++) {
        int16_t si = (int16_t)((int32_t)src[2 * i]     - mid);
        int16_t sq = (int16_t)((int32_t)src[2 * i + 1] - mid);
        out[2 * i]     = (uint16_t)si;
        out[2 * i + 1] = (uint16_t)sq;
    }
}

void loop() {
  cosim_canary++;
    static uint16_t last = 0;

    // ---- co-simulation mailbox -------------------------------------------
    // Only active once the host has claimed the block by writing the magic.
    // Off the box that is one volatile read and a compare, so normal USB CDC
    // operation is unchanged.
    volatile uint32_t *mbox = cosim();
    if (mbox[COSIM_OFF_MAGIC / 4] == COSIM_MAGIC) {
        // Apply the pin state the host wrote. Driving PA6/PA7 through RAM is
        // deliberate: the master has to be able to set them while the core runs.
        digitalWrite(PIN_LO,   mbox[COSIM_OFF_LO / 4]   ? HIGH : LOW);
        digitalWrite(PIN_GAIN, mbox[COSIM_OFF_GAIN / 4] ? HIGH : LOW);

        // Publish the freshest pair and bump the stamp so the host can tell new
        // data from a stale read.
        uint16_t p = DMA1_Channel1->CNDTR;
        if (p < ADC_BUF_LEN - 2) {
            mbox[COSIM_OFF_ADC_I / 4]     = adc_buf[p];
            mbox[COSIM_OFF_ADC_Q / 4]     = adc_buf[p + 1];
            mbox[COSIM_OFF_ADC_STAMP / 4] = mbox[COSIM_OFF_ADC_STAMP / 4] + 1;
        }
        mbox[COSIM_OFF_MCU_STAMP / 4]++;
    }

    // Only repack on a fresh half-block, so the stream runs at the block rate
    // rather than as fast as the loop can spin.
    uint16_t pos = DMA1_Channel1->CNDTR;
    if (pos == last) {
        return;
    }

    // Wait for a full pair block so we never read a torn sample.
    if (pos > ADC_BUF_LEN - PAIRS_PER_PACKET * 2
        && pos < ADC_BUF_LEN - PAIRS_PER_PACKET * 2 + 8) {
        // Near the top of the circular buffer; the wrapped half is complete.
        build_packet(&adc_buf[ADC_BUF_LEN - PAIRS_PER_PACKET * 2]);
    } else if (pos < PAIRS_PER_PACKET * 2) {
        // Just wrapped; the start of the buffer is complete.
        build_packet(&adc_buf[0]);
    } else {
        last = pos;
        return;
    }
    last = pos;

#ifndef COSIM_MODE
    if (Serial) {
        Serial.write(packet, PACKET_BYTES);
    }
#endif
}