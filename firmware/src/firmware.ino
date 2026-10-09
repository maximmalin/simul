/**
 * SDR Direct-Conversion Receiver Firmware
 * Platform: STM32F103C8T6 Blue Pill
 * Framework: PlatformIO + STM32Cube
 */

#include <Arduino.h>

// ADC channel pins for I/Q samples
#define PIN_ADC_I    PA0    // ADC1_CH0
#define PIN_ADC_Q    PA1    // ADC1_CH1

// USB CDC endpoints
#define USB_OUT_EP     1
#define USB_IN_EP      2

// Sample buffer for I/Q data
#define SAMPLE_BUFFER_SIZE 64
uint16_t sample_buffer[SAMPLE_BUFFER_SIZE];
uint16_t buffer_index = 0;

// DMA configuration
static volatile uint16_t dma_buffer[SAMPLE_BUFFER_SIZE];

void setup()
{
    Serial.begin(115200);
    while (!Serial) { /* wait for serial port to connect */ }
    Serial.println("SDR Direct-Conversion Receiver starting...");

    // Initialize ADC
    pinMode(PIN_ADC_I, INPUT);
    pinMode(PIN_ADC_Q, INPUT);

    // Initialize DMA for ADC
    setupADC();

    // Initialize USB
    USB.setConfiguration(1);
    Serial.println("SDR Direct-Conversion Receiver initialized.");
}

void loop()
{
    // Read I/Q samples from ADC
    uint16_t i_sample = analogRead(PIN_ADC_I);
    uint16_t q_sample = analogRead(PIN_ADC_Q);

    // Convert to signed 8-bit I/Q
    int8_t i_byte = (int8_t)(i_sample >> 4);
    int8_t q_byte = (int8_t)(q_sample >> 4);

    // Send over USB CDC to host
    Serial.write(i_byte);
    Serial.write(q_byte);

    // Small delay to control sample rate
    delayMicroseconds(10);
}

void setupADC()
{
    // ADC configuration
    ADC->CR1 |= ADC_CR1_SCAN;
    ADC->CR2 |= ADC_CR2_ADON;
}

void DMA_Handler()
{
    // Handle DMA interrupts
}