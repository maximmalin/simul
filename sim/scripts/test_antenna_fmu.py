#!/usr/bin/env python3
"""Regression tests for the antenna interface stub, not antenna physics.

Run: .venv/bin/python sim/scripts/test_antenna_fmu.py
"""
import math
from pathlib import Path
import tempfile
import unittest
import zipfile

import fmpy
from fmpy.validation import validate_fmu

from antenna_fmu import AntennaBlock

FMU = Path(__file__).resolve().parents[1] / "fmu" / "AntennaBlock.fmu"


class AntennaTests(unittest.TestCase):
    def make_block(self, **values):
        block = AntennaBlock(instance_name="test")
        for name, value in values.items():
            setattr(block, name, value)
        block.exit_initialization_mode()
        return block

    def test_defaults_and_interface(self):
        block = self.make_block()
        self.assertEqual(len(block.vars), 10)
        self.assertEqual(block.s11_db, -10.0)
        self.assertEqual(block.efficiency, 0.65)
        self.assertEqual(block.status, 1)
        self.assertIn("UNVALIDATED", block.status_message)

    def test_modes_and_upper_band(self):
        block = self.make_block()
        for mode, frequency, s11 in ((0, 400e6, -10), (1, 2.45e9, -15), (2, 7e9, -12)):
            block.antenna_mode = mode
            block.frequency = frequency
            self.assertTrue(block.do_step(0, 0.1))
            self.assertEqual(block.s11_db, s11)
            self.assertNotIn("outside", block.status_message)
        block.frequency = 3.5e9
        self.assertTrue(block.do_step(0, 0.1))
        self.assertIn("outside", block.status_message)

    def test_drift_independent_of_step_partition(self):
        a = self.make_block(b3=1e6)
        b = self.make_block(b3=1e6)
        self.assertTrue(a.do_step(0, 1))
        for i in range(10):
            self.assertTrue(b.do_step(i / 10, 0.1))
        self.assertAlmostEqual(a.frequency, b.frequency)
        self.assertEqual(a.frequency, 401e6)

    def test_invalid_input_and_recovery(self):
        for name, value in (("antenna_mode", 3), ("b1", -1), ("b2", 1),
                            ("b3", math.nan), ("frequency", 0), ("frequency", 8e9)):
            with self.subTest(name=name, value=value):
                block = self.make_block()
                setattr(block, name, value)
                self.assertFalse(block.do_step(0, 0.1))
                self.assertEqual(block.status, 2)
        block = self.make_block()
        self.assertFalse(block.do_step(0, 0))
        self.assertTrue(block.do_step(0, 0.1))
        self.assertEqual(block.status, 1)
        with self.assertRaises(ValueError):
            self.make_block(initial_frequency=math.inf)

    def test_archive_validation_and_source_parity(self):
        self.assertEqual(validate_fmu(str(FMU)), [])
        with zipfile.ZipFile(FMU) as archive:
            self.assertEqual(archive.read("resources/antenna_fmu.py"),
                             Path(__file__).with_name("antenna_fmu.py").read_bytes())

    def test_actual_fmi_runtime(self):
        md = fmpy.read_model_description(str(FMU))
        vr = {v.name: v.valueReference for v in md.modelVariables}
        with tempfile.TemporaryDirectory(prefix="antenna_test_", dir="/tmp/opencode") as directory:
            fmpy.extract(str(FMU), directory)
            slave = fmpy.instantiate_fmu(directory, md)
            initialized = False
            try:
                slave.setupExperiment(startTime=0.0)
                slave.enterInitializationMode()
                slave.setReal([vr["initial_frequency"]], [2.45e9])
                slave.setInteger([vr["antenna_mode"]], [1])
                slave.exitInitializationMode()
                initialized = True
                self.assertEqual(slave.getReal([vr["s11_db"]]), [-15.0])
                slave.setInteger([vr["antenna_mode"]], [2])
                slave.setReal([vr["b1"], vr["b2"], vr["b3"]], [0.5, 0.25, 1e6])
                slave.doStep(currentCommunicationPoint=0.0, communicationStepSize=0.1)
                s11, eff, frequency = slave.getReal([vr[n] for n in ("s11_db", "efficiency", "frequency")])
                self.assertEqual(s11, -6.0)
                self.assertAlmostEqual(eff, 0.51)
                self.assertEqual(frequency, 2.4501e9)
                self.assertEqual(slave.getInteger([vr["status"]]), [1])
                self.assertIn(b"UNVALIDATED", slave.getString([vr["status_message"]])[0])
            finally:
                if initialized:
                    slave.terminate()
                slave.freeInstance()


if __name__ == "__main__":
    unittest.main(verbosity=2)
