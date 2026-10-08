"""KiCad Symbol Library Helper
Generator for KiCad symbols used in SDR receiver project
"""

import os
import sys
from pathlib import Path

class KiCadLIB:
    """Helper class for generating KiCad symbol libraries."""
    
    def __init__(self, lib_name="crystal_radio"):
        self.lib_name = lib_name
        self.lib_dir = Path(__file__).parent / "symbols"
        self.lib_dir.mkdir(parents=True, exist_ok=True)
        
    def generate_symbol(self, symbol_name, pins, properties=None):
        """Generate a KiCad symbol for a component."""
        if properties is None:
            properties = {}
            
        # Generate pin definitions
        pin_defs = []
        for i, pin in enumerate(pins):
            pin_num = i + 1
            pin_name = pin.get("name", f"Pin_{pin_num}")
            pin_type = pin.get("type", "input")
            pin_attr = pin.get("attr", "")
            pin_defs.append(f'  (pin {pin_type} {pin_num} "{pin_name}" {pin_attr})')
        
        pins_str = "\n".join(pin_defs)
        
        # Generate property definitions
        prop_defs = []
        for key, value in properties.items():
            prop_defs.append(f'  (property "{key}" "{value}")')
        props_str = "\n".join(prop_defs)
        
        symbol = f"""(symbol "{symbol_name}" (symbol_lib_id "{self.lib_name}:{symbol_name}")
  {props_str}
  {pins_str}
)"""
        
        return symbol
    
    def generate_2n7002_symbol(self):
        """Generate 2N7002 FET symbol."""
        pins = [
            {"name": "G", "type": "input"},
            {"name": "D", "type": "bidirectional"},
            {"name": "S", "type": "power_in"}
        ]
        properties = {
            "Reference": "Q",
            "Value": "2N7002",
            "Footprint": "Transistor_FET:2N7002"
        }
        return self.generate_symbol("2N7002", pins, properties)
    
    def generate_ams1117_symbol(self):
        """Generate AMS1117-3.3 LDO symbol."""
        pins = [
            {"name": "IN", "type": "power_in"},
            {"name": "GND", "type": "power_in"},
            {"name": "OUT", "type": "power_out"}
        ]
        properties = {
            "Reference": "U",
            "Value": "AMS1117-3.3",
            "Footprint": "Regulator_Linear:AMS1117-3.3",
            "Datasheet": "https://www.ams1117.com/ams1117-3.3.html"
        }
        return self.generate_symbol("AMS1117-3.3", pins, properties)
    
    def generate_stm32f103c8t6_symbol(self):
        """Generate STM32F103C8T6 symbol."""
        pins = [
            {"name": "PA0", "type": "bidirectional"},
            {"name": "PA1", "type": "bidirectional"},
            {"name": "PA6", "type": "input"},
            {"name": "PA7", "type": "input"},
            {"name": "VDD", "type": "power_in"},
            {"name": "VSS", "type": "power_in"},
            {"name": "GND", "type": "power_in"},
            {"name": "D+", "type": "input"},
            {"name": "D-", "type": "input"}
        ]
        properties = {
            "Reference": "U",
            "Value": "STM32F103C8T6",
            "Footprint": "Package_QFP:LQFP-48",
            "Datasheet": "https://www.st.com/en/microcontrollers-microprocessors/stm32f103c8t6.html"
        }
        return self.generate_symbol("STM32F103C8T6", pins, properties)
    
    def generate_opa2134_symbol(self):
        """Generate OPA2134 op-amp symbol."""
        pins = [
            {"name": "IN+", "type": "input"},
            {"name": "IN-", "type": "input"},
            {"name": "V+", "type": "power_in"},
            {"name": "V-", "type": "power_in"},
            {"name": "OUT", "type": "output"}
        ]
        properties = {
            "Reference": "U",
            "Value": "OPA2134",
            "Footprint": "Package_SO:SOIC-8_6.5x10.3mm_P1.27mm",
            "Datasheet": "https://www.ti.com/product/OPA2134"
        }
        return self.generate_symbol("OPA2134", pins, properties)
    
    def generate_lt5560_symbol(self):
        """Generate LT5560 mixer symbol."""
        pins = [
            {"name": "IN+", "type": "input"},
            {"name": "IN-", "type": "input"},
            {"name": "I_OUT", "type": "output"},
            {"name": "Q_OUT", "type": "output"},
            {"name": "VCC", "type": "power_in"},
            {"name": "GND", "type": "power_in"}
        ]
        properties = {
            "Reference": "U",
            "Value": "LT5560",
            "Footprint": "Package_SO:SOIC-8_6.5x10.3mm_P1.27mm",
            "Datasheet": "https://www.analog.com/en/products/lt5560.html"
        }
        return self.generate_symbol("LT5560", pins, properties)
    
    def generate_lpj1_symbol(self):
        """Generate LPJ-1 quarter-wave coupler symbol."""
        pins = [
            {"name": "IN+", "type": "input"},
            {"name": "IN-", "type": "input"},
            {"name": "OUT+", "type": "output"},
            {"name": "OUT-", "type": "output"}
        ]
        properties = {
            "Reference": "Q",
            "Value": "LPJ-1",
            "Footprint": "Connector:QuarterWaveCoupler",
            "Datasheet": "https://www.arrow.com/en/products/lpj-1/gvp-engineering"
        }
        return self.generate_symbol("LPJ-1", pins, properties)
    
    def save_library(self):
        """Save the symbol library file."""
        lib_file = self.lib_dir / f"{self.lib_name}.lib"
        
        with open(lib_file, 'w') as f:
            f.write("# SDR Direct-Conversion Receiver Symbol Library\n")
            f.write(f"# Generated by kicad_lib.py\n\n")
            f.write(f"{self.generate_2n7002_symbol()}\n\n")
            f.write(f"{self.generate_ams1117_symbol()}\n\n")
            f.write(f"{self.generate_stm32f103c8t6_symbol()}\n\n")
            f.write(f"{self.generate_opa2134_symbol()}\n\n")
            f.write(f"{self.generate_lt5560_symbol()}\n\n")
            f.write(f"{self.generate_lpj1_symbol()}\n")
        
        print(f"Generated KiCad library: {lib_file}")
        return lib_file

def main():
    """Generate the KiCad symbol library for the SDR receiver."""
    lib = KiCadLIB("crystal_radio")
    lib.save_library()
    print("KiCad library generation complete!")

if __name__ == "__main__":
    main()