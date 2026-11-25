# ESP32-S3 configuration and pin mappings — SigCap REV A

This is a bring-up reference for U6, **ESP32-S3-MINI-1-N4R2**, based on the released schematic and PCB. It describes the intended firmware setup; no capture firmware or sustained sample-rate result is supplied here.

The schematic sheet is still named `ADS8363 and ESP32-S3`, but the fitted ADCs are **ADS8681W**, U7 and U8. Do not use ADS8363 commands or timings.

## ESP-IDF setup

Use an ESP-IDF release supporting ESP32-S3 and record its exact version with the firmware. Start with:

```sh
idf.py set-target esp32s3
idf.py menuconfig
```

| Setting | Initial choice |
| --- | --- |
| Target | ESP32-S3 |
| Flash | 4 MB; conservative 40 MHz DIO for initial bring-up |
| PSRAM | Enable external SPI RAM, Quad mode; start at 40 MHz and verify 2 MB detected |
| Console | USB Serial/JTAG (`CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG`), or UART0 on J5 |
| Wireless / sleep | Leave Wi-Fi, Bluetooth and sleep disabled during initial timing tests |
| SPI hosts | Suggested assignment: current on `SPI2_HOST`, voltage on `SPI3_HOST` |

The host assignment is a firmware choice; GPIO routing is fixed by the PCB. Keep SPI0/SPI1 for module memory. GPIO26 is used internally by PSRAM on the N4R2 module, despite appearing unconnected externally in the schematic. The KiCad footprint is named `ESP32-S2-MINI-1`; the actual processor target is ESP32-S3. [Module datasheet](https://documentation.espressif.com/esp32-s3-mini-1_mini-1u_datasheet_en.pdf).

Use internal DMA-capable buffers initially, copying completed blocks into PSRAM if needed. Do not assume an arbitrary PSRAM allocation is suitable for SPI DMA. [External RAM guidance](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-guides/external-ram.html).

## GPIO mapping

Directions are relative to the ESP. GPIO numbers are **not module pad numbers**.

| Signal | Current ADC U7: GPIO / U6 pad | Voltage ADC U8: GPIO / U6 pad | Direction | ADC pad |
| --- | --- | --- | --- | --- |
| SCLK | 4 / 8 | 12 / 16 | Output | 10 |
| SDI | 7 / 11 | 14 / 18 | Output | 8 |
| SDO-0 | 5 / 9 | 11 / 15 | Input | 11 |
| SDO-1 | 6 / 10 | 13 / 17 | Input | 12 |
| CONVST/CS | 17 / 21 | 10 / 14 | Output | 9 |
| RVS | 16 / 20 | 15 / 19 | Input | 13 |

| Other signal | GPIO | U6 pad | Use |
| --- | --- | --- | --- |
| Power mux ST | 1 | 5 | Input; high = VIN1 / IRM supply selected, low = VIN2 / USB selected, while powered |
| BOOT | 0 | 4 | SW2 pulls low; boot strap at reset |
| USB D− | 19 | 23 | Native USB through R32 |
| USB D+ | 20 | 24 | Native USB through R31 |
| UART0 TX | 43 | 39 | Output to J5 pin 1 |
| UART0 RX | 44 | 40 | Input from J5 pin 3 |
| EN | — | 45 | SW1 reset; not a GPIO |

R35 pulls mux status up to 3.3 V. [TPS2116 status definition](https://www.ti.com/lit/ds/symlink/tps2116.pdf).

ADC reset pins are pulled up to 3.3 V and **are not connected to ESP GPIOs**. An ESP reset does not reset the ADC configuration: initialise explicitly, using the ADC software reset when needed. The isolated-amplifier diagnostic nets and TP1 are also not connected to ESP GPIOs. TP1 can rise to 5 V; do not attach it directly to an ESP input.

## Programming and recovery

Connect a USB data cable to J4 with mains disconnected for first bring-up. Native USB Serial/JTAG supports flashing and a console; Linux normally exposes `/dev/ttyACM*`.

```sh
idf.py -p /dev/ttyACM0 flash monitor
```

If automatic download fails, hold **SW2 BOOT**, press and release **SW1 RST**, then release BOOT. Flash again; reset with BOOT released to run. Preserve GPIO19/20 for USB. J5 is the fallback UART connection and requires the same manual boot sequence; there is no USB-UART bridge or DTR/RTS auto-reset circuit. [USB flashing and console guidance](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-guides/usb-serial-jtag-console.html).

## ADC bring-up configuration

Start with single-SDO, MSB-first, SPI mode 0 at 1 MHz. Set both CS pins high and both SDI pins low before capture. Allow reference startup (use 100 ms initially). Read back register writes; responses are pipelined into the following frame.

| Register | Address | Initial value / purpose |
| --- | --- | --- |
| `SDI_CTL_REG` | `0x08` | `0x0000`: mode 0 |
| `SDO_CTL_REG` | `0x0C` | `0x0000`: single-SDO; later `0x0300`: dual-SDO, same SPI protocol |
| `DATAOUT_CTL_REG` | `0x10` | `0x0000`: conversion data without appended flags |
| `RANGE_SEL_REG` | `0x14` | `0x0004`: ±2.56 V, internal reference enabled |

For normal acquisition, CS rising starts conversion; wait for completion before reading. Read out the preceding result, then the next rising edge starts another conversion. Discard initial/settling results. Separate CS lines mean channel sampling skew must be measured and managed in firmware. [ADS8681W datasheet, sections 6.4–7](https://www.ti.com/lit/ds/symlink/ads8681w.pdf).

### Dual-SDO reception

Both outputs carry interleaved bits of **one** sample. SDO-1 supplies bits 15,13,…,1; SDO-0 supplies bits 14,12,…,0. Eight clocks convey the 16-bit result; command writes still use 32 serial SDI clocks. Verify lane packing using the ADC's test patterns.

ESP dual reception uses its D and Q lanes. Suggested capture mapping:

| Host lane | Current | Voltage |
| --- | --- | --- |
| D / `mosi_io_num` | GPIO5 ← SDO-0 | GPIO11 ← SDO-0 |
| Q / `miso_io_num` | GPIO6 ← SDO-1 | GPIO13 ← SDO-1 |
| Separate SDI GPIO | GPIO7, hold low | GPIO14, hold low |

Use receive-only half-duplex DIO transfers (`SPI_DEVICE_HALFDUPLEX`, `SPI_TRANS_MODE_DIO`), with no command/address/dummy phase during sample reads. **Do not transmit on the D/Q pins**: they are connected to ADC outputs. Configure the ADC first by bit-banging SCLK/CS and the separate SDI GPIO, with both SDO GPIOs as inputs; then hand clock/CS/D/Q to the SPI peripheral. To reconfigure, stop capture and safely return to GPIO control. [ESP-IDF SPI driver](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/api-reference/peripherals/spi_master.html).

### Scaling and capture limits

At ±2.56 V, conversion codes run from negative full scale through `0x8000` at zero to positive full scale. Use a signed intermediate:

```text
signal_V = (int32(code) - 32768) * 5.12 / 65536
current_A ≈ signal_V / 0.041
line_V    ≈ signal_V / (2.0 / 481.0)
```

Calibrate each channel's offset, gain and polarity. The two ADC references are independent.

Begin with slow captures and check SCLK, CS, RVS and both SDO lanes on a logic analyser. A continuous SPI DMA transaction does not create the required per-sample CS transitions. Raising SCLK alone therefore does not establish a 1 MSPS capture rate; validate conversion/acquisition intervals, jitter, dropped samples and channel skew.

At 1 MSPS on each channel, packed 16-bit samples require **4 MB/s** before framing. Full-speed USB has a 12 Mbit/s raw line rate, so it cannot stream that payload continuously. Start with finite buffered captures and slower export; measure wireless throughput separately. The 2 MB PSRAM is a finite capture buffer, not a guarantee of sustained streaming.

## Bring-up checks

1. Verify boot log, 4 MB flash and 2 MB PSRAM; check reset and BOOT recovery.
2. Read GPIO1 on USB power; expect low. Test the alternate supply only under a suitable mains test setup.
3. Confirm both ADCs respond independently and retain explicit startup settings after an ESP-only reset.
4. Verify zero-signal readings, test patterns, lane order and scaling before increasing capture rate.
5. Compare captured waveforms with the BNC outputs using known safe test signals; record achieved sample rate, skew and calibration.
