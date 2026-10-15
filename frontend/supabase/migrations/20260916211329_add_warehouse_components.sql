/*
# Add warehouse_components table for component inventory

## Overview
Creates a universal component database for the warehouse (Склад) section.
Components are added via smartphone camera OCR — the user photographs a component,
text is extracted and parsed into structured fields. The format is universal:
any electronic/mechanical component can be stored with its key parameters.

## New Tables

1. `warehouse_components` — Universal component inventory
   - id (uuid PK)
   - name (text) — component name/title
   - part_number (text, nullable) — manufacturer part number
   - manufacturer (text, nullable) — manufacturer name
   - category (text) — component category (resistor, capacitor, IC, connector, etc.)
   - package_type (text, nullable) — physical package (DIP, SMD, TQFP, etc.)
   - value (text, nullable) — nominal value (e.g. "10kΩ", "100µF", "3.3V")
   - tolerance (text, nullable) — tolerance rating
   - voltage_rating (text, nullable) — max voltage
   - power_rating (text, nullable) — power dissipation
   - temperature_range (text, nullable) — operating temp range
   - description (text, nullable) — free-form description
   - quantity (int) — stock count, default 0
   - location (text, nullable) — shelf/bin location code
   - raw_ocr_text (text, nullable) — original OCR text from camera
   - image_url (text, nullable) — stored image path (if uploaded)
   - datasheet_url (text, nullable) — link to datasheet
   - tags (text[]) — searchable tags
   - created_at, updated_at (timestamptz)

## Security
- RLS enabled, single-tenant no-auth pattern (TO anon, authenticated, USING(true)).
*/

CREATE TABLE IF NOT EXISTS warehouse_components (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  part_number text,
  manufacturer text,
  category text NOT NULL DEFAULT 'uncategorized',
  package_type text,
  value text,
  tolerance text,
  voltage_rating text,
  power_rating text,
  temperature_range text,
  description text,
  quantity int NOT NULL DEFAULT 0,
  location text,
  raw_ocr_text text,
  image_url text,
  datasheet_url text,
  tags text[] DEFAULT '{}',
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

ALTER TABLE warehouse_components ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_components" ON warehouse_components;
CREATE POLICY "anon_select_components" ON warehouse_components FOR SELECT
  TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "anon_insert_components" ON warehouse_components;
CREATE POLICY "anon_insert_components" ON warehouse_components FOR INSERT
  TO anon, authenticated WITH CHECK (true);

DROP POLICY IF EXISTS "anon_update_components" ON warehouse_components;
CREATE POLICY "anon_update_components" ON warehouse_components FOR UPDATE
  TO anon, authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "anon_delete_components" ON warehouse_components;
CREATE POLICY "anon_delete_components" ON warehouse_components FOR DELETE
  TO anon, authenticated USING (true);

CREATE INDEX IF NOT EXISTS idx_components_category ON warehouse_components(category);
CREATE INDEX IF NOT EXISTS idx_components_name ON warehouse_components(name);
CREATE INDEX IF NOT EXISTS idx_components_part_number ON warehouse_components(part_number);
CREATE INDEX IF NOT EXISTS idx_components_tags ON warehouse_components USING GIN(tags);
CREATE INDEX IF NOT EXISTS idx_components_created ON warehouse_components(created_at DESC);

-- SEED: sample components
INSERT INTO warehouse_components (name, part_number, manufacturer, category, package_type, value, tolerance, voltage_rating, power_rating, temperature_range, description, quantity, location, tags)
VALUES
('Resistor 10kΩ 1%', 'RC0603FR-0710KL', 'Yageo', 'resistor', '0603 SMD', '10kΩ', '±1%', '75V', '0.1W', '-55 to +155°C', 'Standard thick film chip resistor', 1240, 'A-01-03', ARRAY['resistor', 'smd', '0603', '10k']),
('Capacitor 100µF', 'EEU-FR1H101B', 'Panasonic', 'capacitor', 'Radial DIP', '100µF', '±20%', '50V', NULL, '-40 to +105°C', 'Aluminum electrolytic capacitor', 85, 'B-02-01', ARRAY['capacitor', 'electrolytic', '100uF']),
('STM32F407VGT6', 'STM32F407VGT6', 'STMicroelectronics', 'microcontroller', 'LQFP-100', '3.3V', NULL, '3.6V', NULL, '-40 to +85°C', 'ARM Cortex-M4 32-bit MCU, 168MHz, 1MB Flash, 192KB RAM', 47, 'C-03-05', ARRAY['mcu', 'stm32', 'arm', 'cortex-m4']),
('Connector USB-C', 'TYPE-C-31-M-12', 'HRO Electronics', 'connector', 'SMD', NULL, NULL, NULL, NULL, '-25 to +85°C', 'USB Type-C receptacle 16-pin SMD', 210, 'D-01-02', ARRAY['connector', 'usb-c', 'smd']),
('Transistor 2N3904', '2N3904', 'ON Semiconductor', 'transistor', 'TO-92', 'NPN', NULL, '60V', '0.625W', '-55 to +150°C', 'General purpose NPN bipolar junction transistor', 340, 'E-04-01', ARRAY['transistor', 'npn', 'to-92'])
ON CONFLICT DO NOTHING;