#!/usr/bin/env python3

"""Проверка готовности проекта к запуску."""

import os
import sys
import json

def check():
    base = "/home/mirrage/Desktop/brig-receiver"
    checks = {}
    errors = []
    warnings = []

    # 1. Проверка ФУЗЕЙ (антенный блок)
    fuziy_dir = os.path.join(base, "hardware", "fuziy")
    checks["fuziy_exists"] = os.path.isdir(fuziy_dir)

    fu...[truncated]