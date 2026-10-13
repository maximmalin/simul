# TECHNICAL TASK FOR THE DEVELOPMENT OF A SUPER-WIDEBAND SDR RECEIVER ON THE BASIS OF AN ACOUSTO-OPTICAL PROCESSOR (AOPP) BRAGG

## PROJECT CODE: «AOPSDR-SPECTR»

Document formatting standard: GOST 2.105-95 / GOST 19.201-78 (Equivalent: ISO 5807:1985 / ISO/IEC TR 29115)

---

## 1. GENERAL INFORMATION AND PURPOSE OF THE DEVELOPMENT

**Product Name:** Panoramic software-defined radio (SDR) receiver based on an acousto-optical processor (AOPP) Bragg.

**Purpose:** Panoramic automatic radio monitoring without search, detection and interception of complex radio signals with ultra-short pulse length (signals with PPRCH/FHSS, radar systems, SS systems) in conditions of dense electromagnetic interference environment.

**Target orientation:** The developed product must exceed the HackRF One platform in instantaneous bandwidth (at least 25 times) and in dynamic range of blocking (at least 30 dB).

---

## 2. TECHNICAL PARAMETERS AND COMPARATIVE CHARACTERISTICS

### Comparative parameters table:

| Parameter Name / Characteristics | HackRF One Prototype | Developed Product (AOPSDR) | Standards Conformity (GOST / ISO) |
|----------------------------------|----------------------|----------------------------|-----------------------------------|
| Input working frequency range    | 1 MHz — 6.0 GHz      | 10 MHz — 6.0 GHz           | GOST R 52016-2003 / ITU-R SM.328  |
| Instantaneous bandwidth (Real-time BW) | 20 MHz          | not less than 500 MHz (target: 1000 MHz) | GOST 24375-80 / IEEE Std 211      |
| Dynamic range of blocking (SFDR) | ~45...50 dB         | not less than 75...80 dB   | GOST R 52016-2003 / IEEE Std 1385 |
| Equivalent spectral resolution   | 8 bits               | 14 bits (determined by CMOS array) | GOST R 52016-2003 / ISO/IEC 2382  |
| Hardware latency (Latency)       | Variable (from SW)   | fixed, ≤ 2.0 μs            | GOST 24375-80 / ISO/IEC/IEEE 24748 |
| Frequency step per pixel         | Depends on FFT in SW | ≤ 250 kHz (2048 pixels on 500 MHz bandwidth) | GOST 24375-80 / IEEE Std 1664      |
| Data interface to PC             | USB 2.0 High Speed   | USB 3.0 Type-C / PCIe Gen2 | USB-IF / PCI-SIG standards        |

---

## 3. REQUIREMENTS FOR CONSTRUCTIVE AND FUNCTIONAL SUBSYSTEMS

### 3.1. Radio frequency path (RF Frontend)

- Input impedance of the RF port: 50 Ohm, SMA connector type (GOST 20275-91 / Equivalent: IEC 60169-15).
- Noise figure (NF) of the input path: not more than 3.5 dB (Equivalent: IEEE Std 145).
- The RF path must contain a switched preselector block, low-noise amplifier with overload protection (up to +20 dBm) and superheterodyne frequency transfer scheme to the working intermediate frequency range of the acousto-optical crystal (IF: 150–650 MHz).
- RF output driver: Linear amplifier ensuring signal power up to 1.5 W for providing maximum diffraction efficiency of the piezoelectric Bragg cell.

### 3.2. Opto-mechanical node (AO-processor)

- Radiation source: Semiconductor laser diode with wavelength λ = 650 nm (or 532 nm). Optical power stability: not worse than 0.5%.
- Automatic power regulation circuit (APRC) presence (GOST IEC 60825-1-2013 / Equivalent: IEC 60825-1 — Laser safety Class 3B).
- Acousto-optical Bragg cell: Tellurium dioxide (TeO₂) crystal, optimized for slow wave.
- Crystal end faces must have dielectric antireflection coating with reflection coefficient R < 0.2% (GOST ISO 9211-1-2016 / Equivalent: ISO 9211-1).
- Optical scheme: Cylindrical telescope-colllimator and focusing Fourier lens with full spherical aberration compensation (GOST ISO 10110-1-2016 / Equivalent: ISO 10110).

### 3.3. Signal processing path (DSP)

- Register: High-speed linear CMOS array (not less than 2048 pixels) with not less than 72 dB pixel dynamic range and line readout frequency not less than 50 kHz (Equivalent: EMVA 1288 Standard).
- Base FPGA: Industrial class family Xilinx Artix-7 or Intel Cyclone V.
- FPGA firmware functions: Hardware phase synchronization generation and CMOS array data reading. Pipeline dark current compensation (Dark Current Compensation) and laser profile non-uniformity («Flat-Field»). USB 3.0 bus controller (FTDI FT601 or equivalent) integration for host data transfer without frame loss.

---

## 4. CLIMATIC AND MECHANICAL OPERATING CONDITIONS

### 4.1. Climatic conditions

The product is developed in climatic execution УХЛ4 according to GOST 15150-69 (Equivalent: IEC 60721-3-3, Class 3K3 / 3M1 — operation in enclosed spaces with artificially regulated climate):

- Operating temperature range: from +15° C to +30° C (Equivalent: ISO 554-1976).
- Extreme temperature range (in switched-off state): from -40° C to +50° C.
- Relative humidity of air: not more than 80% at +25° C (without moisture condensation).
- The design must contain an active thermal stabilization system of the laser diode and AO-cells based on Peltier elements with temperature maintenance accuracy of ± 0.1° C to avoid frequency scale drift.

### 4.2. Mechanical effects

- The design in packaged and operating state must comply with GOST 30631-99 requirements (Equivalent: IEC 60068-2-6 — Vibration testing, sinusoidal method): resistance to vibration loads in the frequency range 10–55 Hz with displacement amplitude up to 0.15 mm.
- Optical scheme elements must be rigidly mounted on a single milled base from D16T alloy (Equivalent material: Aluminum 2024-T3 / ISO 209). The allowable spatial displacement of optical axis elements at maximum operating temperature must not exceed 0.5 μm.

---

## 5. ELECTROMAGNETIC COMPATIBILITY (EMC)

- Emission level: The level of industrial radio interference created by the device must not exceed the limits set by GOST CISPR 22-2012 (Equivalent: CISPR 22 / EN 55022, Class B — for use in residential and industrial zones).
- Immunity: The device must maintain operability and specified dynamic range under external electromagnetic field influence according to GOST 30804.4.3-2013 (Equivalent: IEC 61000-4-3, level 2 — 3 V/m).
- Constructive shielding: The case must be made of milled aluminum and divided into three isolated sections (RF block, light-isolated optical block, digital block) using radio frequency-conducting gaskets (Equivalent: MIL-DTL-83528 / EMI shielding requirements).

---

## 6. RELIABILITY AND SAFETY REQUIREMENTS

- Reliability: Mean time to failure (MTBF) must be not less than 10,000 hours according to GOST 27.002-2015 (Equivalent: ISO 13849-1 / IEC 61508).
- Electrical safety: The product must comply with safety requirements according to GOST IEC 60950-1-2014 (Equivalent: IEC 60950-1 / IEC 62368-1 — Information technology equipment).

---

## 7. PACKAGING, TRANSPORTATION AND PRESERVATION

### 7.1. Protection and preservation

The product undergoes preservation according to VZ-10 variant per GOST 9.014-78 (Equivalent: ISO 4520 / MIL-STD-2073-1, method 30 — hermetic cover from aluminumized or three-layer polyethylene barrier film). Calculated amount of activated silica gel per GOST 3956-76 (Equivalent: ISO 11623 / MIL-D-3464E, type V) is placed inside the cover. Preservation term without re-preservation: not less than 3 years (Equivalent: NATO STANAG 4280 / Def Stan 81-41, Level A Preservation).

### 7.2. Transport packaging

The product is supplied in an individual hard shock-resistant case from reinforced polypropylene (Equivalent: STANAG 4280 / DEF STAN 81-41). Inside the case, a precision damping insert from chemically neutral high-density polyurethane foam is placed (Equivalent: ISO 1856). The insert must limit overload on the optical block to not more than 15g (Equivalent test: MIL-STD-810G, Method 516.6 — Shock). Package marking: Executed according to GOST 14192-96 (Equivalent: ISO 780:2015 — Handling signs: «Fragile. Handle with care», «Keep from moisture», «Top»).

### 7.3. Transportation

The packaged product must withstand transportation jolts according to GOST 23216-78 (Conditions «Ж» — hard; Equivalent: ASTM D4169-16 / IEC 60068-2-64): acceleration up to 3g at strike frequency from 80 to 120 per minute for 3 hours, and also single drop in packaged form from height of 0.75 m on solid base (Equivalent: MIL-STD-810G, Method 516.6).

---

## 8. SOFTWARE REQUIREMENTS (HOST API)

- Low-level software: Cross-platform driver (C/C++, Linux/Windows) for FPGA initialization and continuous spectrum frame capture via USB 3.0 bus without drops (Equivalent software quality standards: ISO/IEC 25010).
- Upper level: Presence of open source module (Source block) for integration into GNU Radio ecosystem for real-time complex panoramic data stream output.

**Important: in the Technical Task there should be no detailed description of device nodes — only standards and requirements.**

