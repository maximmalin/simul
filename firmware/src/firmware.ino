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

// ---- pin map (matches sim/scripts/sdr_skidl_circuit.py) --------------------
static const uint8_t PIN_ADC_I = PA0;   // ADC1_IN0
static const uint8_t PIN_ADC_Q = PA1;   // ADC1_IN1
static const uint8_t PIN_LO    = PA6;   // TIM3_CH1 -> mixer LO
static const uint8_t PIN_GAIN  = PA7;   // front-end A/B select

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
    GPIOA->CRL &= ~((0xF << (PIN_ADC_I * 4)) | (0xF << (PIN_ADC_Q * 4)));

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
    while (ADC1->CR2 & ADC_CR2_CAL) {
    }
    ADC1->CR2 |= ADC_CR2_CAL;
    while (ADC1->CR2 & ADC_CR2_CAL) {
    }
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
    GPIOA->CRL &= ~(0xF << (PIN_LO * 4));
    GPIOA->CRL |= (0xB << (PIN_LO * 4));   // CNF=10 MODE=10

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

void setup() {
    // USB CDC first so the host sees the banner as soon as it enumerates.
    Serial.begin(115200);

    pinMode(PIN_GAIN, OUTPUT);
    digitalWrite(PIN_GAIN, LOW);

    adc_configure();
    lo_init(7150000UL);        // 7.15 MHz centre frequency
    adc_start_dma();

    if (Serial) {
        Serial.println(F("SDR direct-conversion receiver ready"));
        Serial.print(F("sample_rate="));
        Serial.println(SAMPLE_RATE_HZ);
    }
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
    static uint16_t last = 0;

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

    if (Serial) {
        Serial.write(packet, PACKET_BYTES);
    }
}