#!/usr/bin/env python3
"""Flask web GUI for SdrNgspiceFMU.fmu co-simulation.

Provides:
- Real-time I/Q plotting (adc_i, adc_q)
- Parameter tuning (stim_level, lo_frequency)
- Live simulation control (start/stop/reset)
- Web access from any browser at http://localhost:5000
"""
import os
import json
import time
import threading
from flask import Flask, render_template, jsonify, request
from pythonfmi import load_fmu, simulate_fmu

app = Flask(__name__)

# --- FMU Configuration ---
FMU_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "sim", "fmu", "SdrNgspiceFMU.fmu"
)

# Load FMU once at startup
try:
    fmu = load_fmu(FMU_PATH)
    print(f"✓ FMU loaded: {FMU_PATH}")
except Exception as e:
    print(f"⚠ FMU load warning: {e}")
    fmu = None

# --- Simulation State ---
sim_lock = threading.Lock()
simulation_active = False
sim_thread = None

# Default simulation parameters (matching firmware config)
DEFAULT_PARAMS = {
    "stim_level": 0.0,       # 0=LO off, 1=LO on (from firmware mailbox)
    "lo_frequency": 7150000.0,  # 7.15 MHz LO (from lo_init())
    "duration": 0.01,        # 10ms simulation window
    "step_size": 1e-6,       # 1us timesteps
}

# Initial simulation state
sim_state = {
    "last_update": time.time(),
    "adc_i": 2048,           # Mid-scale ADC I
    "adc_q": 2048,           # Mid-scale ADC Q
    "model_status": "initialized",
    "stim_level": 0.0,
}


def run_simulation_background():
    """Run FMU simulation in background thread."""
    global simulation_active, sim_thread, sim_state

    with sim_lock:
        if simulation_active:
            return  # Already running

        simulation_active = True

    try:
        # Run FMU simulation using fmpy
        result = simulate_fmu(
            fmu,
            start_time=0,
            stop_time=DEFAULT_PARAMS["duration"],
            n_timesteps=100,
            # Input parameters from web UI
            parameters=[
                DEFAULT_PARAMS["stim_level"],
                DEFAULT_PARAMS["lo_frequency"],
                DEFAULT_PARAMS["step_size"],
            ],
            # Requested output variables
            output=["adc_i", "adc_q", "model_status"]
        )

        # Update global state with results
        if isinstance(result, dict):
            sim_state["adc_i"] = result.get("adc_i", sim_state["adc_i"])
            sim_state["adc_q"] = result.get("adc_q", sim_state["adc_q"])
            sim_state["model_status"] = result.get("model_status", sim_state["model_status"])
        else:
            # result may be dict-like or structured
            for key in ["adc_i", "adc_q", "model_status"]:
                if key in result:
                    sim_state[key] = result[key]

    except Exception as e:
        sim_state["model_status"] = f"error: {str(e)[:50]}"
        print(f"FMU simulation error: {e}")
    finally:
        with sim_lock:
            simulation_active = False


@app.route("/")
def index():
    """Main web interface."""
    return render_template("index.html", 
                           state=sim_state,
                           params=DEFAULT_PARAMS)


@app.route("/api/status")
def api_status():
    """Return current simulation state as JSON."""
    with sim_lock:
        state = {
            "adc_i": sim_state["adc_i"],
            "adc_q": sim_state["adc_q"],
            "model_status": sim_state["model_status"],
            "stim_level": sim_state["stim_level"],
            "simulation_active": simulation_active,
        }
    return jsonify(state)


@app.route("/api/start", methods=["POST"])
def api_start():
    """Start background simulation thread."""
    global simulation_active, sim_thread

    with sim_lock:
        if simulation_active:
            return jsonify({"status": "already_running"}), 200

        simulation_active = True
        sim_thread = threading.Thread(target=run_simulation_background, daemon=True)
        sim_thread.start()

    return jsonify({"status": "started", "message": "FMU simulation started"}), 202


@app.route("/api/stop", methods=["POST"])
def api_stop():
    """Stop simulation."""
    global simulation_active
    with sim_lock:
        simulation_active = False
    return jsonify({"status": "stopped"}), 200


@app.route("/api/parameters", methods=["POST"])
def api_parameters():
    """Update simulation parameters from web UI."""
    global DEFAULT_PARAMS
    data = request.get_json()
    if data and "stim_level" in data:
        DEFAULT_PARAMS["stim_level"] = float(data["stim_level"])
    if data and "lo_frequency" in data:
        DEFAULT_PARAMS["lo_frequency"] = float(data["lo_frequency"])
    if data and "duration" in data:
        DEFAULT_PARAMS["duration"] = float(data["duration"])

    return jsonify({"status": "updated", "parameters": DEFAULT_PARAMS}), 200


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """Reset simulation state."""
    global sim_state
    with sim_lock:
        sim_state = {
            "adc_i": 2048,
            "adc_q": 2048,
            "model_status": "initialized",
            "stim_level": 0.0,
            "last_update": time.time(),
        }
    return jsonify({"status": "reset"}), 200


if __name__ == "__main__":
    print("=" * 60)
    print("🌐 PICSimLab Web GUI for SdrNgspiceFMU.fmu")
    print("=" * 60)
    print(f"FMU Path: {FMU_PATH}")
    print(f"Web UI:   http://localhost:5000")
    print("=" * 60)
    print("Controls:")
    print("  • stim_level: 0.0 (LO off) to 1.0 (LO on)")
    print("  • lo_frequency: 7150000.0 (7.15 MHz)")
    print("  • Real-time I/Q plotting")
    print("=" * 60)

    # Run Flask app
    # Use 0.0.0.0 to allow access from Docker network/other containers
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)