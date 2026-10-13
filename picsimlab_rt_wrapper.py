#!/usr/bin/env python3
"""
picsimlab_rt_wrapper.py - Real-time picsimlab pin/ADC reader with minimal delay

Optimized for real-time operation with picsimlab ESP32.
Uses picsimlab's Remote Control interface via serial port.

Key optimizations:
- Higher baud rate for faster communication (921600)
- Non-blocking reads with select()
- Batch command processing
- Direct value extraction without parsing overhead
"""

import sys
import argparse
import time
import select
import json


# Constants for picsimlab Remote Control protocol
CMD_DIGITAL_READ = "D"
CMD_ADC_READ = "A"
CMD_PIN_MODE = "M"
CMD_LIST = "L"
CMD_QUIT = "Q"

# picsimlab serial response prefixes
PREFIX_DIGITAL = "DS:"
PREFIX_ADC = "AD:"
PREFIX_OK = "OK"
PREFIX_ERROR = "ERR"


class PicsimlabRTWrapper:
    """Real-time wrapper for picsimlab ESP32 pin reading.

    Optimized for minimal delay in reading GPIO and ADC values.
    Communicates via picsimlab's Remote Control serial interface.
    """

    def __init__(self, port, baudrate=921600, timeout=0.01):
        """Initialize the wrapper.

        Args:
            port: Serial port (e.g., /dev/tnt2)
            baudrate: Baud rate (921600 for fastest picsimlab support)
            timeout: Read timeout in seconds (low for real-time)
        """
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.ser = None
        self.running = False

        # Cached pin states for efficiency
        self._digital_cache = {}  # pin -> 0/1
        self._adc_cache = {}      # pin -> recent values list
        self._pin_modes = {}      # pin -> 'input'/'output'

        # Performance tracking
        self._min_delay = float('inf')
        self._max_delay = 0
        self._total_requests = 0
        self._total_time = 0.0

    def connect(self):
        """Connect to picsimlab via serial Remote Control interface."""
        import serial
        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=self.timeout,
                write_timeout=self.timeout
            )
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            self.running = True
            print(f"Connected to picsimlab on {self.port} @ {self.baudrate}bps")
            return True
        except Exception as e:
            print(f"Connection failed: {e}")
            return False

    def _send(self, command_str):
        """Send a command to picsimlab. Optimized: single write, minimal flush."""
        if not self.ser or not self.ser.is_open:
            return None
        try:
            self.ser.write((command_str + '\n').encode('utf-8'))
            self.ser.flush()
            return True
        except Exception as e:
            print(f"Send error: {e}")
            return False

    def _recv_nonblocking(self, timeout=None):
        """Non-blocking receive using select(). Returns data or None."""
        if not self.ser or not self.ser.is_open:
            return None
        timeout = timeout or self.timeout
        ready, _, _ = select.select([self.ser], [], [], timeout)
        if ready:
            try:
                data = self.ser.read(self.ser.in_waiting or 1)
                if data:
                    return data.decode('utf-8', errors='replace')
            except Exception:
                pass
        return None

    def digital_read(self, pin):
        """Read digital state from a GPIO pin. Optimized for minimal delay."""
        start = time.time()

        # Send command
        self._send(f"{CMD_DIGITAL_READ} {pin}")

        # Non-blocking read with short timeout
        ready = select.select([self.ser], [], [], self.timeout)

        if ready:
            try:
                self.ser.read(self.ser.in_waiting or 1)
            except Exception:
                pass

        # Calculate delay for performance tracking
        delay = time.time() - start
        self._total_requests += 1
        self._total_time += delay
        if delay < self._min_delay:
            self._min_delay = delay
        if delay > self._max_delay:
            self._max_delay = delay

        # Fast non-blocking response read
        ready2, _, _ = select.select([self.ser], [], [], self.timeout * 2)
        if ready2:
            try:
                resp = self.ser.read(self.ser.in_waiting or 1)
                resp_str = resp.decode('utf-8', errors='replace').strip()
                # Parse DS:n format or just number
                if resp_str.startswith(PREFIX_DIGITAL):
                    val = int(resp_str.split(':')[1])
                    self._digital_cache[pin] = val
                    return val
                elif resp_str.isdigit() or (resp_str.startswith('-') and resp_str[1:].isdigit()):
                    val = int(resp_str)
                    self._digital_cache[pin] = val
                    return val
            except (ValueError, IndexError):
                pass

        # Return cached value if available, otherwise None
        return self._digital_cache.get(pin)

    def adc_read(self, pin, samples=1):
        """Read analog value from ADC-capable GPIO pin. Optimized for real-time."""
        start = time.time()

        # For real-time, take minimal samples
        if samples < 1:
            samples = 1

        values = []
        for i in range(samples):
            # Send ADC read command
            self._send(f"{CMD_ADC_READ} {pin}")

            # Non-blocking read
            ready = select.select([self.ser], [], [], self.timeout)
            if ready:
                try:
                    resp = self.ser.read(self.ser.in_waiting or 1)
                    resp_str = resp.decode('utf-8', errors='replace').strip()

                    # Parse AD:n format or numeric value
                    if resp_str.startswith(PREFIX_ADC):
                        val = float(resp_str.split(':')[1])
                    elif resp_str.replace('.', '', 1).isdigit() or \
                         (resp_str.startswith('-') and resp_str[1:].replace('.', '', 1).isdigit()):
                        val = float(resp_str)
                    else:
                        continue

                    values.append(val)
                except ValueError:
                    continue

            # Minimal delay between samples
            if i < samples - 1:
                time.sleep(max(0, self.timeout * 0.1))

        if values:
            # Return average
            avg_val = sum(values) / len(values)
            self._adc_cache[pin] = values
            return avg_val

        return self._adc_cache.get(pin)

    def pin_mode(self, pin, mode):
        """Set pin mode (INPUT or OUTPUT)."""
        if mode not in ('INPUT', 'OUTPUT'):
            return False
        self._send(f"{CMD_PIN_MODE} {pin} {mode}")
        # Quick readback
        ready = select.select([self.ser], [], [], self.timeout)
        if ready:
            try:
                self.ser.read(self.ser.in_waiting or 1)
            except Exception:
                pass
        self._pin_modes[pin] = mode
        return True

    def batch_read_digital(self, pins):
        """Read multiple digital pins in single round-trip for efficiency."""
        if not pins:
            return {}

        # Build command with all pins
        cmd = f"{CMD_DIGITAL_READ} " + " ".join(str(p) for p in pins)
        self._send(cmd)

        # Read response
        ready = select.select([self.ser], [], [], self.timeout * 2)
        if ready:
            try:
                resp = self.ser.read(self.ser.in_waiting or 1)
                resp_str = resp.decode('utf-8', errors='replace').strip()
                # Parse format: DS:p0 v0 DS:p1 v1 ...
                import re
                matches = re.findall(r'DS:(\d+)\s+(\d+)', resp_str)
                for pin_str, val_str in matches:
                    pin = int(pin_str)
                    val = int(val_str)
                    self._digital_cache[pin] = val
                    result[pin] = val
            except Exception:
                pass

        # Fill in cached values for any missed pins
        result = {}
        for p in pins:
            result[p] = self._digital_cache.get(p)
        return result

    def get_stats(self):
        """Get communication performance statistics."""
        if self._total_requests == 0:
            return {}
        avg_delay = self._total_time / self._total_requests
        return {
            'min_delay': f"{self._min_delay * 1000:.2f} ms" if self._min_delay != float('inf') else "N/A",
            'max_delay': f"{self._max_delay * 1000:.2f} ms",
            'avg_delay': f"{avg_delay * 1000:.2f} ms",
            'total_requests': self._total_requests
        }

    def close(self):
        """Close the connection."""
        self.running = False
        if self.ser and self.ser.is_open:
            self.ser.close()
        print("Connection closed")

    def continuous_monitor(self, pins, interval=0.1, callback=None):
        """Continuously monitor pins with specified interval.

        Args:
            pins: List of pin numbers to monitor
            interval: Monitoring interval in seconds (default 0.1 = 10Hz)
            callback: Function(pin, value) called with each reading
        """
        print(f"Starting continuous monitoring @ {1 / interval:.1f}Hz for pins {pins}")
        print("Press Ctrl+C to stop\n")

        try:
            next_time = time.time()
            while self.running:
                # Hit next interval
                next_time += interval

                # Read all pins in batch
                values = self.batch_read_digital(pins)

                # Call callback if provided
                if callback:
                    for pin, val in values.items():
                        try:
                            callback(pin, val)
                        except Exception:
                            pass
                else:
                    # Default output - overwrite line
                    outputs = []
                    for pin in pins:
                        v = values.get(pin, self._digital_cache.get(pin, '?'))
                        outputs.append(f"GPIO{pin}:{v}")
                    sys.stdout.write('\r  ' + '  '.join(outputs))
                    sys.stdout.flush()

                # Sleep until next interval
                now = time.time()
                sleep_time = next_time - now
                if sleep_time > 0:
                    time.sleep(sleep_time)

        except KeyboardInterrupt:
            print("\nMonitoring stopped")


def main():
    parser = argparse.ArgumentParser(
        description='picsimlab Real-time Pin/ADC Reader',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument('port', help='Serial port (e.g., /dev/tnt2)')
    parser.add_argument('-b', '--baud', type=int, default=921600,
                       help='Baud rate (921600 max for picsimlab)')
    parser.add_argument('-i', '--interval', type=float, default=0.1,
                       help='Monitoring interval in seconds')
    parser.add_argument('-p', '--adc-pins', type=int, nargs='+', default=[34, 35, 36],
                       help='ADC GPIO pin numbers')
    parser.add_argument('-d', '--digital-pins', type=int, nargs='+', default=[0, 2, 5, 18, 19],
                       help='Digital GPIO pin numbers')
    parser.add_argument('-c', '--callback', action='store_true',
                       help='Use callback mode (suppress console output)')
    parser.add_argument('-s', '--samples', type=int, default=1,
                       help='ADC samples per read (1=real-time, >1=averaged)')
    parser.add_argument('--stats', action='store_true',
                       help='Show performance statistics at end')

    args = parser.parse_args()

    # Initialize wrapper
    wrapper = PicsimlabRTWrapper(args.port, args.baud, timeout=0.005)

    # Connect
    if not wrapper.connect():
        sys.exit(1)

    # Set pin modes
    for pin in args.digital_pins:
        # Estimate: ESP32 GPIO0,2,4,5,18,19 can be output
        mode = 'OUTPUT' if pin in [0, 2, 4, 5, 18, 19] else 'INPUT'
        wrapper.pin_mode(pin, mode)
    for pin in args.adc_pins:
        wrapper.pin_mode(pin, 'input')  # ADC pins are input

    # Setup callback container
    pin_values = {'digital': {}, 'adc': {}}

    def default_callback(pin, value):
        pin_values['adc'][pin] = value

    # Choose mode based on arguments
    if args.digital_pins and not args.adc_pins:
        # Digital-only mode
        print(f"Digital mode: reading pins {args.digital_pins} @ {1 / args.interval:.1f}Hz")
        wrapper.continuous_monitor(
            pins=args.digital_pins,
            interval=args.interval,
            callback=default_callback if args.callback else None
        )

    elif args.adc_pins and not args.digital_pins:
        # ADC-only mode
        print(f"ADC mode: reading pins {args.adc_pins} @ {1 / args.interval:.1f}Hz, "
              f"{args.samples} sample(s)")
        wrapper.continuous_monitor(
            pins=args.adc_pins,
            interval=args.interval,
            callback=default_callback if args.callback else None
        )

    else:
        # Mixed mode - digital + ADC
        print(f"Mixed mode: digital pins {args.digital_pins} + ADC pins {args.adc_pins}")
        print(f"Interval: {args.interval}s ({1 / args.interval:.1f}Hz)")
        print(f"ADC samples: {args.samples}")
        print()

        # Read once and show
        print("=== Initial Read ===")

        # Read digital
        print("Digital pins:")
        for pin in args.digital_pins:
            val = wrapper.digital_read(pin)
            v_str = f"{val}" if val is not None else "?"
            print(f"  GPIO {pin}: {v_str}")
            pin_values['digital'][pin] = val

        # Read ADC
        print("ADC pins:")
        for pin in args.adc_pins:
            val = wrapper.adc_read(pin, samples=args.samples)
            if val is not None:
                voltage = val * 3.3 / 4095.0
                print(f"  GPIO {pin}: {val:.1f} (~{voltage:.3f} V)")
                pin_values['adc'][pin] = (val, voltage)
            else:
                print(f"  GPIO {pin}: no reading")
                pin_values['adc'][pin] = (None, None)

    # Show stats if requested
    if args.stats:
        stats = wrapper.get_stats()
        if stats:
            print(f"\n=== Performance Stats ===")
            for k, v in stats.items():
                print(f"  {k}: {v}")

    # Close
    wrapper.close()


if __name__ == '__main__':
    main()