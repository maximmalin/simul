/**
 * SDR direct-conversion receiver firmware - STM32F103C8T6 Blue Pill
 * Minimal COSIM_MODE version that reaches loop() and exchanges via mailbox.
 */

#include <Arduino.h>
#include "cosim.h"

#ifdef COSIM_MODE
// Override SystemClock_Config to avoid PLL wait (QEMU doesn't model PLLRDY)
extern "C" void SystemClock_Config(void) {
}
#endif

// ---- pin map ----
static const uint8_t PIN_ADC_I = PA0;   // ADC1_IN0
static const uint8_t PIN_ADC_Q = PA1;   // ADC1_IN1
static const uint8_t PIN_LO    = PA6;   // TIM3_CH1 -> mixer LO
static const uint8_t PIN_GAIN  = PA7;   // front-end A/B select

static const uint32_t IDX_ADC_I = 0;
static const uint32_t IDX_ADC_Q = 1;
static const uint32_t IDX_LO    = 6;
static const uint32_t IDX_GAIN  = 7;

// Bound on hardware calibration poll
#define ADC_CAL_TIMEOUT 1000000u

// Simple ADC buffer (no DMA for now - QEMU ADC modeling is limited)
static volatile uint16_t adc_i = 2048;
static volatile uint16_t adc_q = 2048;

static void adc_configure(void) {
    RCC->APB2ENR |= RCC_APB2ENR_ADC1EN | RCC_APB2ENR_AFIOEN;

    // Analog pins PA0/PA1
    GPIOA->CRL &= ~((0xF << (IDX_ADC_I * 4)) | (0xF << (IDX_ADC_Q * 4)));

    // ADC prescaler: PCLK2/6 = 12 MHz
    RCC->CFGR &= ~RCC_CFGR_ADCPRE;
    RCC->CFGR |= RCC_CFGR_ADCPRE_DIV6;

    ADC1->CR1 = 0;
    ADC1->CR2 = ADC_CR2_ADON;

    // Sample time
    ADC1->SMPR2 = 0;

    // Regular group: ch0 (PA0), ch1 (PA1)
    ADC1->SQR3 = 0;
    ADC1->SQR2 = 1 << 4;

#ifndef COSIM_MODE
    ADC1->CR2 |= ADC_CR2_CAL;
    for (volatile uint32_t spin = 0;
         (ADC1->CR2 & ADC_CR2_CAL) && spin < ADC_CAL_TIMEOUT; ++spin) {}
#endif
}

// Simple ADC read (polling, no DMA)
static inline uint16_t adc_read_single(uint8_t ch) {
    ADC1->SQR3 = ch;  // select channel
    ADC1->CR2 |= ADC_CR2_SWSTART;
    while (!(ADC1->SR & ADC_SR_EOC)) {}
    return ADC1->DR & 0xFFF;
}

static void lo_init(uint32_t freq_hz) {
    RCC->APB2ENR |= RCC_APB2ENR_IOPAEN;
    GPIOA->CRL &= ~(0xF << (IDX_LO * 4));
    GPIOA->CRL |= (0xB << (IDX_LO * 4));   // CNF=10 MODE=10: AF push-pull

    RCC->APB1ENR |= RCC_APB1ENR_TIM3EN;
    TIM3->PSC = 0;
    uint32_t half = 72000000UL / (2UL * freq_hz);
    if (half < 2) half = 2;
    TIM3->ARR = (half > 1) ? (half - 1) : 1;
    TIM3->CCR1 = (TIM3->ARR + 1) / 2;

    TIM3->CCMR1 |= (6 << TIM_CCMR1_OC1M_Pos) | TIM_CCMR1_OC1PE;
    TIM3->CCER |= TIM_CCER_CC1E;
    TIM3->EGR = TIM_EGR_UG;
    TIM3->CR1 |= TIM_CR1_ARPE | TIM_CR1_CEN;
}

volatile uint32_t cosim_canary = 0;

void setup() {
#ifndef COSIM_MODE
    Serial.begin(115200);
#endif

#ifdef COSIM_MODE
    // PA7 output push-pull, direct register
    RCC->APB2ENR |= RCC_APB2ENR_IOPAEN;
    GPIOA->CRL &= ~(0xF << (IDX_GAIN * 4));
    GPIOA->CRL |= (0x3 << (IDX_GAIN * 4));
    GPIOA->BRR = 1u << IDX_GAIN;
#else
    pinMode(PIN_GAIN, OUTPUT);
    digitalWrite(PIN_GAIN, LOW);
#endif

    adc_configure();
    lo_init(7150000UL);

#ifndef COSIM_MODE
    if (Serial) {
        Serial.println(F("SDR ready"));
        Serial.print(F("sample_rate="));
        Serial.println(200000);
    }
#endif
}

void loop() {
    cosim_canary++;

    // Simple ADC polling
    adc_i = adc_read_single(0);  // PA0 = I
    adc_q = adc_read_single(1);  // PA1 = Q

    // ---- co-simulation mailbox ----
    volatile uint32_t *mbox = cosim();
    if (mbox[COSIM_OFF_MAGIC / 4] == COSIM_MAGIC) {
        // Apply host pin states
        digitalWrite(PIN_LO,   mbox[COSIM_OFF_LO / 4]   ? HIGH : LOW);
        digitalWrite(PIN_GAIN, mbox[COSIM_OFF_GAIN / 4] ? HIGH : LOW);

        // Publish ADC samples
        mbox[COSIM_OFF_ADC_I / 4]     = adc_i;
        mbox[COSIM_OFF_ADC_Q / 4]     = adc_q;
        mbox[COSIM_OFF_ADC_STAMP / 4]++;
        mbox[COSIM_OFF_MCU_STAMP / 4]++;
    }
}