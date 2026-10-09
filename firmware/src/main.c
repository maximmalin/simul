/**
 * SDR Direct-Conversion Receiver Firmware
 * STM32F103C8T6 Blue Pill
 * Zero-IF architecture with USB CDC output
 */

#include <stm32f1xx.h>
#include <string.h>
#include <stdio.h>

// ADC channels for I/Q samples
#define ADC_I_CHANNEL    ADC_Channel_0  // PA0
#define ADC_Q_CHANNEL    ADC_Channel_1  // PA1

// USB CDC configuration
#define CDC_BUFFER_SIZE  64

// Sample buffer for I/Q data
#define SAMPLE_SIZE     16
static uint16_t adc_samples[SAMPLE_SIZE * 2];  // I and Q interleaved

// DMA buffer for ADC
static uint16_t adc_buffer[64];

// USB CDC handle
static uint8_t cdc_buffer[CDC_BUFFER_SIZE];

// Function prototypes
void SystemClock_Config(void);
void GPIO_Init(void);
void ADC_Init(void);
void DMA_Init(void);
void USB_CDC_Init(void);
void USB_CDC_Send(const uint8_t *data, uint16_t len);
void SDR_ProcessSamples(void);

int main(void)
{
    SystemClock_Config();
    GPIO_Init();
    ADC_Init();
    DMA_Init();
    USB_CDC_Init();

    while (1)
    {
        SDR_ProcessSamples();
    }
}

void SystemClock_Config(void)
{
    RCC->CFGR = 0;
    RCC->CR |= RCC_CR_HSION;
    while (!(RCC->CR & RCC_CR_HSIRDY));

    // Enable HSI for 72 MHz
    RCC->CFGR = RCC_CFGR_PPRE1_2 | RCC_CFGR_PPRE2_2;
    FLASH->ACR = FLASH_ACR_PRFTEN | FLASH_ACR_LATENCY_2;
    RCC->CR |= RCC_CR_HSEON;
    while (!(RCC->CR & RCC_CR_HSERDY));

    RCC->CFGR |= RCC_CFGR_SW;
    while ((RCC->CFGR & RCC_CFGR_SWS) != RCC_CFGR_SW);
}

void GPIO_Init(void)
{
    RCC->APB2ENR |= RCC_APB2ENR_IOPAEN | RCC_APB2ENR_IOPBEN | RCC_APB2ENR_IOPCEN;

    // PA0 - ADC1_CH0 (I channel)
    // PA1 - ADC1_CH1 (Q channel)
    GPIOA->CRL &= ~(GPIO_CRL_MODE0 | GPIO_CRL_CNF0 | GPIO_CRL_MODE1 | GPIO_CRL_CNF1);
    GPIOA->CRL |= GPIO_CRL_CNF0_1 | GPIO_CRL_CNF1_1;  // Analog input

    // PA6, PA7 - MCU outputs
    GPIOA->CRL &= ~(GPIO_CRL_MODE6 | GPIO_CRL_CNF6 | GPIO_CRL_MODE7 | GPIO_CRL_CNF7);
    GPIOA->CRL |= GPIO_CRL_MODE6 | GPIO_CRL_MODE7;  // Output push-pull
}

void ADC_Init(void)
{
    RCC->APB2ENR |= RCC_APB2ENR_ADC1EN;
    ADC1->CR1 = ADC_CR1_EOCIE | ADC_CR1_SCAN;
    ADC1->CR2 = ADC_CR2_DMA | ADC_CR2_ADON;
    ADC1->SMPR1 = 0;
    ADC1->SMPR2 = 0;
    ADC1->SQR1 = 0;
    ADC1->SQR2 = 0;
    ADC1->SQR3 = 0x01 | (0x01 << 3);  // Channel 0 and 1
    ADC1->CR2 |= ADC_CR2_CAL;
    while (ADC1->CR2 & ADC_CR2_CAL);
}

void DMA_Init(void)
{
    RCC->AHBENR |= RCC_AHBENR_DMA1EN;
    DMA1->CCR3 = 0;
    DMA1->CNDTR3 = sizeof(adc_buffer) / 2;
    DMA1->CPAR3 = (uint32_t)(&ADC1->DR);
    DMA1->CMAR3 = (uint32_t)(&adc_buffer);
    DMA1->CCR3 = DMA_CCR3_MINC | DMA_CCR3_CIRC | DMA_CCR3_TEIE | DMA_CCR3_HTIE;
    DMA1->CNDTR3 = sizeof(adc_buffer) / 2;
    DMA1->CCR3 |= DMA_CCR3_EN;
    ADC1->CR2 |= ADC_CR2_DMA;
}

void USB_CDC_Init(void)
{
    RCC->APB1ENR |= RCC_APB1ENR_USBEN;
    // USB CDC initialization would go here
}

void USB_CDC_Send(const uint8_t *data, uint16_t len)
{
    // Send data over USB CDC
    // Implementation depends on USB stack
}

void SDR_ProcessSamples(void)
{
    // Read I and Q samples from ADC
    uint16_t i_sample = adc_buffer[0];
    uint16_t q_sample = adc_buffer[1];

    // Convert to signed 8-bit for transmission
    int8_t i_byte = (int8_t)(i_sample >> 4);
    int8_t q_byte = (int8_t)(q_sample >> 4);

    // Prepare CDC buffer
    cdc_buffer[0] = (uint8_t)i_byte;
    cdc_buffer[1] = (uint8_t)q_byte;

    // Send over USB
    USB_CDC_Send(cdc_buffer, 2);
}

#ifdef USE_FULL_ASSERT
void assert_failed(uint8_t *file, uint32_t line)
{
    while (1);
}
#endif