import time
import threading
import sys
import json
import os
from datetime import datetime
from w1thermsensor import W1ThermSensor, SensorNotReadyError
from simple_pid import PID
import RPi.GPIO as GPIO
import subprocess
from flask import Flask, render_template_string, jsonify, request, send_file, Response
from flask_socketio import SocketIO, emit
import io
import csv
import paho.mqtt.client as mqtt
import ssl
import signal

# Configuration Constants
HYSTERESIS = 2.0  # Temperature hysteresis band in °C
TEMP_UPDATE_INTERVAL = 5  # Temperature reading interval in seconds (faster for graphing)
COOLDOWN_HOURS = 4  # Slow cooldown duration in hours
COOLDOWN_STEP_INTERVAL = 300  # Cooldown step interval in seconds (5 minutes)
SETTINGS_FILE = 'heater_settings.json'
PRINT_STATE_FILE = 'print_state.json'  # Persists print cycle state for crash recovery
LOG_FILE = 'temperature_log.csv'

# Pin Setup
RELAY_PIN = 17   # SSR control
FIRE_PIN = 18    # MQ-2 DO
LIGHTS_PIN = 22  # Lights relay
BUZZER_PIN = 27  # Buzzer
FAN1_PIN = 23    # Filtration fan 1
FAN2_PIN = 24    # Filtration fan 2

GPIO.setmode(GPIO.BCM)
GPIO.setup(RELAY_PIN, GPIO.OUT)
GPIO.setup(FIRE_PIN, GPIO.IN)
GPIO.setup(LIGHTS_PIN, GPIO.OUT)
GPIO.setup(BUZZER_PIN, GPIO.OUT)
GPIO.setup(FAN1_PIN, GPIO.OUT)
GPIO.setup(FAN2_PIN, GPIO.OUT)

# Read current GPIO states on startup to sync with hardware
# This handles cases where the service was restarted while outputs were on
startup_heater_state = GPIO.input(RELAY_PIN)
startup_fan1_state = GPIO.input(FAN1_PIN)
startup_fan2_state = GPIO.input(FAN2_PIN)
startup_lights_state = GPIO.input(LIGHTS_PIN)

# Global state flags and variables
emergency_stop = False
shutdown_requested = False
fans_on = bool(startup_fan1_state or startup_fan2_state)  # Set based on actual GPIO state
fans_manual_override = False
heater_on = bool(startup_heater_state)  # Set based on actual GPIO state
heater_manual_override = False
lights_on = bool(startup_lights_state)  # Set based on actual GPIO state

# Log detected startup states
if heater_on or fans_on or lights_on:
    print("="*50)
    print("GPIO State Detected on Startup:")
    if heater_on:
        print("  ⚠️  Heater was ON - syncing state")
    if fans_on:
        print("  ⚠️  Fans were ON - syncing state")
    if lights_on:
        print("  ⚠️  Lights were ON - syncing state")
    print("="*50)
print_active = False
print_paused = False
warmup_complete = False
awaiting_preheat_confirmation = False
start_requested = False
stop_requested = False
printer_finished = False  # Set when printer sends FINISH - triggers cooldown
emergency_stop_requested = False
pause_requested = False
preheat_confirmed = False
additional_seconds = 0
time_lock = threading.Lock()
state_lock = threading.Lock()
reset_requested = False

# Print state persistence for crash recovery
pending_resume = False  # Flag indicating there's a print to resume
resume_state = None  # Loaded print state data
resume_confirmed = False  # User confirmed resume
resume_aborted = False  # User aborted resume

# Temperature tracking
temp_history = []  # List of {time, temp, setpoint} dicts
history_lock = threading.Lock()
MAX_HISTORY = 1000  # Keep last 1000 data points

# Logging
logging_enabled = False
log_data = []

# WebSocket message sequencing - prevents stale messages from updating UI
status_sequence_number = 0
sequence_lock = threading.Lock()

status_data = {
    'current_temp': 0,
    'sensor_temps': [],
    'setpoint': 0,
    'time_remaining': 0,
    'heater_on': heater_on,  # Use actual GPIO state from startup
    'heater_manual': False,
    'fans_on': fans_on,  # Use actual GPIO state from startup
    'fans_manual': False,
    'lights_on': False,
    'emergency_stop': False,
    'print_active': False,
    'print_paused': False,
    'awaiting_preheat_confirmation': False,
    'pending_resume': False,  # Crash recovery available - waiting for user confirmation
    'phase': 'idle',  # idle, warming up, heating, maintaining, cooling
    'eta_to_target': 0,
    'print_time_remaining': 0,
    'cooldown_time_remaining': 0,
    'sequence': 0,  # Message sequence number to prevent stale updates
    # Printer status
    'printer_connected': False,
    'printer_phase': 'idle',  # idle, printing, paused, finish
    'printer_file': '',
    'printer_material': '',
    'printer_progress': 0,
    'printer_time_remaining': 0,
    'printer_nozzle_temp': 0,
    'printer_bed_temp': 0,
    'printer_chamber_temp': 0,
    'camera_streaming': False
}

# Printer MQTT state
printer_mqtt_client = None
printer_connected = False
printer_status = {
    'phase': 'idle',
    'file': '',
    'material': '',
    'progress': 0,
    'time_remaining': 0,
    'nozzle_temp': 0,
    'bed_temp': 0,
    'chamber_temp': 0
}
printer_lock = threading.Lock()
# Remember the last known mapping target to prevent flickering when mapping field is absent
last_mapping_target = -1
# Remember last known AMS data to prevent flickering when data is missing from message
last_ams_slots = ['', '', '', '']
last_external_spool = ''
last_tray_now = -1
# Remember last known print info to prevent text flickering
last_file_name = ''
last_progress = 0
last_time_remaining = 0
last_material = ''
mqtt_sequence_id = 0

# Camera streaming state (always-on when printer configured)
camera_process = None
camera_streaming = False
camera_lock = threading.Lock()
camera_frame = None  # Latest frame for serving to clients
camera_frame_lock = threading.Lock()

# DS18B20 Setup
try:
    sensors = W1ThermSensor.get_available_sensors()
    if not sensors:
        print("ERROR: No DS18B20 temperature sensors detected!")
        print("Please check your wiring and ensure 1-Wire interface is enabled.")
        print("Enable with: sudo raspi-config → Interface Options → 1-Wire")
        GPIO.cleanup()
        sys.exit(1)
    probe_locations = {s.id: f"Probe {i+1}" for i, s in enumerate(sensors)}
    print(f"Detected {len(sensors)} temperature sensor(s)")
except Exception as e:
    print(f"ERROR: Failed to initialize temperature sensors: {e}")
    GPIO.cleanup()
    sys.exit(1)

# Get average temperature from all sensors with error handling
def get_sensor_temps():
    """Returns list of tuples: (sensor_id, name, temperature)"""
    sensor_data = []

    for s in sensors:
        try:
            temp = s.get_temperature()
            name = probe_locations.get(s.id, s.id)
            sensor_data.append((s.id, name, temp))
        except (SensorNotReadyError, Exception) as e:
            name = probe_locations.get(s.id, s.id)
            sensor_data.append((s.id, name, None))

    return sensor_data

def get_average_temp():
    sensor_data = get_sensor_temps()
    temps = [temp for _, _, temp in sensor_data if temp is not None]

    if not temps:
        print("ERROR: All temperature sensors failed to read!")
        return None

    failed = [name for _, name, temp in sensor_data if temp is None]
    if failed:
        print(f"WARNING: Failed to read sensors: {', '.join(failed)}")

    return sum(temps) / len(temps)

# Get initial ambient temperature
ambient = get_average_temp()
if ambient is None:
    print("ERROR: Cannot read initial temperature. Exiting.")
    GPIO.cleanup()
    sys.exit(1)

print(f"Initial chamber temperature: {ambient:.1f}°C")

# Settings Management
def load_settings():
    """Load saved settings from file"""
    default_settings = {
        'desired_temp': 60.0,
        'print_hours': 8,
        'print_minutes': 0,
        'fans_enabled': True,
        'lights_enabled': True,
        'logging_enabled': False,
        'hysteresis': 2.0,
        'cooldown_hours': 4.0,
        'cooldown_target_temp': 21.0,  # Target temp for cooldown phase (21°C = 70°F)
        'temp_unit': 'C',
        'require_preheat_confirmation': False,
        'skip_preheat': False,  # Skip warming up phase and start timer immediately
        'probe_names': {},
        'presets': [
            {'name': 'ABS Standard', 'temp': 60, 'hours': 8, 'minutes': 0},
            {'name': 'ASA Standard', 'temp': 65, 'hours': 10, 'minutes': 0},
            {'name': 'Quick Test', 'temp': 40, 'hours': 0, 'minutes': 30}
        ],
        # Printer Integration Settings (user must configure via web interface)
        'printer_enabled': False,  # Enable/disable printer integration
        'printer_ip': '',  # User configures via settings
        'printer_access_code': '',  # User configures via settings
        'printer_serial': '',  # User configures via settings
        'auto_start_enabled': True,  # Automatically start heater when print detected
        'material_mappings': {
            'PC': {'temp': 60, 'fans': False},
            'ABS': {'temp': 60, 'fans': True},
            'ASA': {'temp': 65, 'fans': True},
            'PETG': {'temp': 40, 'fans': True},
            'PLA': {'temp': 0, 'fans': False},  # No heating for PLA
            'HIPS': {'temp': 60, 'fans': True},
            'TPU': {'temp': 40, 'fans': False},
            'NYLON': {'temp': 60, 'fans': False}
        },
        # AMS slot overrides - manually assign material if AMS can't read RFID
        # Empty string means use auto-detection from printer
        'ams_slot_overrides': {
            '0': '',  # Slot 1 (index 0)
            '1': '',  # Slot 2 (index 1)
            '2': '',  # Slot 3 (index 2)
            '3': ''   # Slot 4 (index 3)
        },
        # External spool material - what's loaded in the spool holder
        'external_spool_material': ''  # Empty means use auto-detection
    }

    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r') as f:
                saved = json.load(f)
                default_settings.update(saved)
        except Exception as e:
            print(f"WARNING: Could not load settings: {e}")

    return default_settings

def save_settings(settings):
    """Save settings to file"""
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings, f, indent=2)
    except Exception as e:
        print(f"WARNING: Could not save settings: {e}")

# Print State Persistence (for crash recovery)
def save_print_state(phase, start_time, print_duration, pause_time_accumulated, is_paused, target_temp, fans_enabled, logging_enabled, time_adjustments, heater_manual, fans_manual, heater_state, fans_state):
    """Save current print state for crash recovery"""
    try:
        state = {
            'timestamp': time.time(),
            'phase': phase,
            'start_time': start_time,
            'print_duration': print_duration,
            'pause_time_accumulated': pause_time_accumulated,
            'is_paused': is_paused,
            'target_temp': target_temp,
            'fans_enabled': fans_enabled,
            'logging_enabled': logging_enabled,
            'time_adjustments': time_adjustments,
            'heater_manual_override': heater_manual,
            'fans_manual_override': fans_manual,
            'heater_on': heater_state,
            'fans_on': fans_state
        }
        with open(PRINT_STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"WARNING: Could not save print state: {e}")

def load_print_state():
    """Load saved print state and validate if it's still resumable"""
    try:
        if not os.path.exists(PRINT_STATE_FILE):
            return None

        with open(PRINT_STATE_FILE, 'r') as f:
            state = json.load(f)

        # Add backward compatibility for old state files without manual override fields
        if 'heater_manual_override' not in state:
            state['heater_manual_override'] = False
        if 'fans_manual_override' not in state:
            state['fans_manual_override'] = False
        if 'heater_on' not in state:
            state['heater_on'] = False
        if 'fans_on' not in state:
            state['fans_on'] = False

        # Calculate elapsed time since last save
        elapsed_since_save = time.time() - state['timestamp']

        # Validate staleness differently based on phase
        if state['phase'] == 'cooling':
            # For cooling phase, check if restart happened within reasonable cooldown time
            # Max cooldown is configurable but usually 4-12 hours
            max_cooldown_seconds = 12 * 3600  # 12 hours max
            if elapsed_since_save > max_cooldown_seconds:
                print("Print state is stale (cooldown should have finished). Auto-aborting.")
                delete_print_state()
                return None

            print("="*50)
            print("PRINT STATE FOUND - Crash Recovery Available")
            print(f"  Phase: {state['phase'].upper()}")
            print(f"  Time elapsed since save: {int(elapsed_since_save)}s")
            print(f"  Cooldown in progress")
            print(f"  Paused: {state['is_paused']}")
            print(f"  Heater: {'Manual' if state['heater_manual_override'] else 'Auto'} ({'ON' if state['heater_on'] else 'OFF'})")
            print(f"  Fans: {'Manual' if state['fans_manual_override'] else 'Auto'} ({'ON' if state['fans_on'] else 'OFF'})")
            print("="*50)
        else:
            # For heating/maintaining phases, check against remaining print time
            elapsed_at_save = state['timestamp'] - state['start_time'] - state['pause_time_accumulated']
            remaining_at_save = state['print_duration'] + state['time_adjustments'] - elapsed_at_save

            # If more time has passed than was remaining, state is stale
            if elapsed_since_save > remaining_at_save + 300:  # Add 5min grace period
                print("Print state is stale (print should have finished). Auto-aborting.")
                delete_print_state()
                return None

            print("="*50)
            print("PRINT STATE FOUND - Crash Recovery Available")
            print(f"  Phase: {state['phase'].upper()}")
            print(f"  Time elapsed since save: {int(elapsed_since_save)}s")
            print(f"  Time remaining: ~{int(remaining_at_save)}s")
            print(f"  Paused: {state['is_paused']}")
            print(f"  Heater: {'Manual' if state['heater_manual_override'] else 'Auto'} ({'ON' if state['heater_on'] else 'OFF'})")
            print(f"  Fans: {'Manual' if state['fans_manual_override'] else 'Auto'} ({'ON' if state['fans_on'] else 'OFF'})")
            print("="*50)

        return state
    except Exception as e:
        print(f"WARNING: Could not load print state: {e}")
        return None

def delete_print_state():
    """Delete print state file"""
    try:
        if os.path.exists(PRINT_STATE_FILE):
            os.remove(PRINT_STATE_FILE)
            print("Print state file deleted")
    except Exception as e:
        print(f"WARNING: Could not delete print state: {e}")

# Load initial settings
current_settings = load_settings()

# Update probe names from settings
if current_settings.get('probe_names'):
    for sensor_id, custom_name in current_settings['probe_names'].items():
        if custom_name:  # Only update if custom name is not empty
            probe_locations[sensor_id] = custom_name

# Initialize status_data with current sensor readings (after probe names are loaded)
initial_sensor_data = get_sensor_temps()
status_data['current_temp'] = ambient
status_data['sensor_temps'] = [
    {'id': sid, 'name': name, 'temp': temp}
    for sid, name, temp in initial_sensor_data
]
print(f"Initialized {len(initial_sensor_data)} temperature probe(s) in status data")

# Check for existing print state (crash recovery)
resume_state = load_print_state()
if resume_state:
    pending_resume = True
    status_data['pending_resume'] = True
    print("Waiting for user to confirm resume or abort...")

# Lights Control
def set_lights(on_off):
    """Control lights relay via GPIO pin 22"""
    try:
        GPIO.output(LIGHTS_PIN, GPIO.HIGH if on_off else GPIO.LOW)
        print(f"Lights relay turned {'ON' if on_off else 'OFF'}")
        return True
    except Exception as e:
        print(f"WARNING: Lights relay control failed: {e}")
        return False

# Initialize lights based on saved settings
# (lights_on already set from GPIO state detection above)
saved_lights_state = current_settings.get('lights_enabled', True)
if saved_lights_state != lights_on:
    # Sync hardware with saved preference
    set_lights(saved_lights_state)
    lights_on = saved_lights_state
    print(f"Lights set to {'ON' if saved_lights_state else 'OFF'} (from saved settings)")
status_data['lights_on'] = lights_on

# Fire Monitor with Web Reset
def fire_monitor():
    global emergency_stop, heater_on, fans_on, reset_requested
    global printer_mqtt_client, printer_connected, mqtt_sequence_id

    while not shutdown_requested:
        if GPIO.input(FIRE_PIN) == GPIO.LOW:
            if not emergency_stop:
                print("\n🔥 FIRE DETECTED! Emergency shutdown!")
                with state_lock:
                    emergency_stop = True
                    heater_on = False
                    fans_on = False
                    GPIO.output(RELAY_PIN, GPIO.LOW)
                    GPIO.output(FAN1_PIN, GPIO.LOW)
                    GPIO.output(FAN2_PIN, GPIO.LOW)
                    GPIO.output(BUZZER_PIN, GPIO.HIGH)
                    status_data['emergency_stop'] = True
                    status_data['heater_on'] = False
                    status_data['fans_on'] = False

                # Also stop the printer if connected
                if printer_connected and printer_mqtt_client:
                    try:
                        serial = current_settings.get('printer_serial', '')
                        topic = f"device/{serial}/request"

                        with printer_lock:
                            mqtt_sequence_id += 1
                            command = {
                                "print": {
                                    "sequence_id": str(mqtt_sequence_id),
                                    "command": "stop"
                                }
                            }

                        printer_mqtt_client.publish(topic, json.dumps(command), qos=1)
                        print("🔥 Fire alarm: Sent STOP command to printer")
                    except Exception as e:
                        print(f"Error stopping printer during fire alarm: {e}")

                emit_status_update()  # Immediate WebSocket update for fire alarm
                print("Heater, fans, and printer stopped. Use web interface to RESET.")

        if emergency_stop and reset_requested:
            if GPIO.input(FIRE_PIN) == GPIO.HIGH:
                print("Reset acknowledged via web interface. Fire condition cleared.")
                with state_lock:
                    emergency_stop = False
                    status_data['emergency_stop'] = False
                    GPIO.output(BUZZER_PIN, GPIO.LOW)
                    reset_requested = False
                emit_status_update()  # Immediate WebSocket update for fire reset
            else:
                print("Cannot reset: Fire still detected!")
                reset_requested = False
                time.sleep(1)
        elif not emergency_stop:
            GPIO.output(BUZZER_PIN, GPIO.LOW)

        time.sleep(1)

# Calculate ETA to target temperature
def calculate_eta(current_temp, target_temp):
    """Calculate estimated time to reach target temperature"""
    if len(temp_history) < 10:
        return 0  # Not enough data

    # Get recent temperature change rate (last 2 minutes of data)
    with history_lock:
        recent = temp_history[-24:] if len(temp_history) >= 24 else temp_history

    if len(recent) < 2:
        return 0

    time_diff = recent[-1]['time'] - recent[0]['time']
    temp_diff = recent[-1]['temp'] - recent[0]['temp']

    if time_diff == 0 or temp_diff == 0:
        return 0

    rate = temp_diff / time_diff  # °C per second

    if rate <= 0:
        return 0  # Not heating or cooling in wrong direction

    remaining_temp = target_temp - current_temp
    if remaining_temp <= 0:
        return 0

    eta_seconds = remaining_temp / rate
    return max(0, int(eta_seconds))

# Slow Cooling Function
def slow_cool(pid, hours=COOLDOWN_HOURS, start_time=None, print_duration=0, pause_time_accumulated=0, target_temp=0, fans_enabled=True, logging_enabled=False, time_adjustments=0):
    global heater_on, fans_on, print_active, stop_requested, emergency_stop_requested, shutdown_requested, heater_manual_override, fans_manual_override

    # Get cooldown target temperature from settings (default to 21°C if not set)
    cooldown_target = current_settings.get('cooldown_target_temp', 21.0)

    current_set = pid.setpoint
    steps = int(hours * 12)  # Every 5 min (convert to int for range())
    delta = (current_set - cooldown_target) / steps

    print(f"Starting {hours}-hour cooldown from {current_set:.1f}°C to {cooldown_target:.1f}°C")
    status_data['phase'] = 'cooling'
    status_data['print_time_remaining'] = 0  # Clear print time when entering cooling phase

    # Emit immediate WebSocket update so UI shows cooldown phase right away
    emit_status_update()

    for step in range(steps):
        # Check for stop conditions at the start of each step
        if shutdown_requested or stop_requested or emergency_stop_requested:
            print("Cooldown interrupted by user")
            # Immediately turn off heater and fans and reset all state
            with state_lock:
                print_active = False
                heater_on = False
                if not fans_manual_override:
                    fans_on = False
                    GPIO.output(FAN1_PIN, GPIO.LOW)
                    GPIO.output(FAN2_PIN, GPIO.LOW)
                GPIO.output(RELAY_PIN, GPIO.LOW)
                status_data['print_active'] = False
                status_data['heater_on'] = False
                status_data['fans_on'] = fans_on
                status_data['phase'] = 'idle'
                status_data['print_time_remaining'] = 0
                status_data['cooldown_time_remaining'] = 0
            break

        pid.setpoint -= delta

        avg_temp = get_average_temp()
        if avg_temp is not None:
            cooldown_remaining = (steps - step) * COOLDOWN_STEP_INTERVAL
            status_data['cooldown_time_remaining'] = cooldown_remaining
            status_data['current_temp'] = avg_temp
            print(f"Cooldown step {step+1}/{steps}: Setpoint={pid.setpoint:.1f}°C | Current={avg_temp:.1f}°C")
        else:
            print(f"Cooldown step {step+1}/{steps}: Setpoint={pid.setpoint:.1f}°C | Temp sensor error")

        # Emit WebSocket update so UI shows cooldown progress
        emit_status_update()

        # Save print state for crash recovery during cooldown
        if start_time is not None:
            save_print_state(
                phase='cooling',
                start_time=start_time,
                print_duration=print_duration,
                pause_time_accumulated=pause_time_accumulated,
                is_paused=False,  # Can't pause during cooldown
                target_temp=target_temp,
                fans_enabled=fans_enabled,
                logging_enabled=logging_enabled,
                time_adjustments=time_adjustments,
                heater_manual=heater_manual_override,
                fans_manual=fans_manual_override,
                heater_state=heater_on,
                fans_state=fans_on
            )

        if heater_on and not heater_manual_override:
            heater_on = False
            GPIO.output(RELAY_PIN, GPIO.LOW)
            status_data['heater_on'] = False
            # Emit update so UI shows heater turned off during cooling
            emit_status_update()

        # Sleep in small increments to check for stop conditions more frequently
        # Instead of sleeping for 300 seconds straight, sleep in 5-second chunks
        for i in range(COOLDOWN_STEP_INTERVAL // 5):
            # Check every 5 seconds if stop was requested
            if shutdown_requested or stop_requested or emergency_stop_requested:
                print("Cooldown interrupted by user during sleep")
                # Immediately turn off heater and fans and reset all state
                with state_lock:
                    print_active = False
                    heater_on = False
                    if not fans_manual_override:
                        fans_on = False
                        GPIO.output(FAN1_PIN, GPIO.LOW)
                        GPIO.output(FAN2_PIN, GPIO.LOW)
                    GPIO.output(RELAY_PIN, GPIO.LOW)
                    status_data['print_active'] = False
                    status_data['heater_on'] = False
                    status_data['fans_on'] = fans_on
                    status_data['phase'] = 'idle'
                    status_data['print_time_remaining'] = 0
                    status_data['cooldown_time_remaining'] = 0
                break
            time.sleep(5)

        # If stop was requested during sleep, break from main loop too
        if shutdown_requested or stop_requested or emergency_stop_requested:
            break

    print("Cooldown complete.")
    status_data['phase'] = 'idle'
    status_data['cooldown_time_remaining'] = 0

    # Delete print state file after successful completion
    delete_print_state()

# Main PID Loop
def main_loop():
    global heater_on, fans_on, additional_seconds, print_active, start_requested
    global stop_requested, printer_finished, emergency_stop_requested, logging_enabled, log_data
    global heater_manual_override, fans_manual_override, print_paused, pause_requested
    global warmup_complete, awaiting_preheat_confirmation, preheat_confirmed
    global pending_resume, resume_confirmed, resume_aborted, resume_state

    while not shutdown_requested:
        # Wait for START command or RESUME confirmation from web interface
        # While idle, continuously update temperature readings
        while not start_requested and not resume_confirmed and not shutdown_requested:
            # Read and update temperatures even when idle
            avg_temp = get_average_temp()
            sensor_data = get_sensor_temps()

            if avg_temp is not None:
                status_data['current_temp'] = avg_temp
                status_data['sensor_temps'] = [
                    {'id': sid, 'name': name, 'temp': temp}
                    for sid, name, temp in sensor_data
                ]
                emit_status_update()  # Periodic WebSocket update while idle

            time.sleep(1)  # Check every 1 second for start request (faster response to START button)

        if shutdown_requested:
            break

        # Determine if we're resuming from crash or starting fresh
        is_resuming = resume_confirmed

        with state_lock:
            start_requested = False
            resume_confirmed = False  # Clear resume flag
            print_active = True
            stop_requested = False
            emergency_stop_requested = False
            status_data['print_active'] = True

            if not is_resuming:
                # Fresh start - initialize everything
                additional_seconds = 0
                # Clear manual override flags when starting a new print cycle
                heater_manual_override = False
                fans_manual_override = False
                status_data['heater_manual'] = False
                status_data['fans_manual'] = False
                # Clear pause state when starting
                print_paused = False
                pause_requested = False
                status_data['print_paused'] = False
                # Clear warmup state when starting
                warmup_complete = False
                awaiting_preheat_confirmation = False
                preheat_confirmed = False
                status_data['awaiting_preheat_confirmation'] = False

        # Get settings (either from current_settings or resume_state)
        if is_resuming:
            print("="*50)
            print("RESUMING PRINT FROM CRASH RECOVERY")
            desired_temp = resume_state['target_temp']
            print_duration_seconds = resume_state['print_duration']
            additional_seconds = resume_state['time_adjustments']

            # Calculate elapsed time since crash
            time_since_save = time.time() - resume_state['timestamp']

            # Calculate adjusted start time to resume from correct point
            # Original: start_time = time.time()
            # Resume: start_time = current_time - time_that_already_elapsed
            elapsed_at_save = resume_state['timestamp'] - resume_state['start_time'] - resume_state['pause_time_accumulated']
            total_elapsed_before_crash = elapsed_at_save + time_since_save
            start_time_adjusted = time.time() - total_elapsed_before_crash

            # Set start_time to adjusted value for proper time tracking
            start_time = start_time_adjusted

            # Restore pause state
            print_paused = resume_state['is_paused']
            status_data['print_paused'] = print_paused
            if print_paused:
                total_paused_time = resume_state['pause_time_accumulated']
                pause_start_time = time.time()  # Currently paused, track from now
            else:
                total_paused_time = resume_state['pause_time_accumulated']
                pause_start_time = 0

            # Skip preheat - we're resuming during heating/cooling
            skip_warmup = True
            warmup_complete = True
            require_confirmation = False  # Don't ask for preheat confirmation on resume

            logging_enabled = resume_state['logging_enabled']

            # Restore manual override states
            heater_manual_override = resume_state['heater_manual_override']
            fans_manual_override = resume_state['fans_manual_override']
            status_data['heater_manual'] = heater_manual_override
            status_data['fans_manual'] = fans_manual_override

            print(f"  Target: {desired_temp}°C")
            print(f"  Time elapsed: {int(total_elapsed_before_crash)}s")
            print(f"  Paused: {print_paused}")
            print(f"  Phase: {resume_state['phase']}")
            print(f"  Heater: {'Manual' if heater_manual_override else 'Auto'}")
            print(f"  Fans: {'Manual' if fans_manual_override else 'Auto'}")
            print("="*50)
        else:
            # Normal start
            desired_temp = current_settings['desired_temp']
            print_duration_seconds = (current_settings['print_hours'] * 3600) + \
                                    (current_settings['print_minutes'] * 60)
            require_confirmation = current_settings.get('require_preheat_confirmation', False)
            skip_warmup = False  # Will be determined later based on temperature
            # These will be set after warmup phase completes
            start_time = None  # Will be set after warmup
            total_paused_time = 0
            pause_start_time = 0

        # Initialize logging if enabled
        if logging_enabled:
            log_data = []
            log_data.append(['Timestamp', 'Elapsed (s)', 'Current Temp (°C)',
                           'Setpoint (°C)', 'Heater', 'Fans', 'Phase'])

        # Turn on fans if configured (or restore saved state if resuming)
        if is_resuming:
            # Restore hardware states from saved state
            heater_on = resume_state['heater_on']
            fans_on = resume_state['fans_on']
            if heater_on:
                GPIO.output(RELAY_PIN, GPIO.HIGH)
            if fans_on:
                GPIO.output(FAN1_PIN, GPIO.HIGH)
                GPIO.output(FAN2_PIN, GPIO.HIGH)
            status_data['heater_on'] = heater_on
            status_data['fans_on'] = fans_on
            print(f"Restored hardware states - Heater: {'ON' if heater_on else 'OFF'}, Fans: {'ON' if fans_on else 'OFF'}")
        else:
            # Normal start - turn on fans if configured
            if current_settings.get('fans_enabled', True) and not emergency_stop:
                fans_on = True
                GPIO.output(FAN1_PIN, GPIO.HIGH)
                GPIO.output(FAN2_PIN, GPIO.HIGH)
                status_data['fans_on'] = True

            # Turn on heater immediately if temperature is below target (for instant UI feedback)
            initial_temp = get_average_temp()
            if initial_temp is not None and initial_temp < desired_temp and not emergency_stop and not heater_manual_override:
                heater_on = True
                GPIO.output(RELAY_PIN, GPIO.HIGH)
                status_data['heater_on'] = True

        # Set initial values for UI display before first emit
        status_data['setpoint'] = desired_temp

        # Calculate correct print time remaining
        if is_resuming:
            # When resuming, show actual remaining time
            elapsed_at_resume = total_elapsed_before_crash
            remaining_at_resume = print_duration_seconds + additional_seconds - elapsed_at_resume
            status_data['print_time_remaining'] = max(0, remaining_at_resume)

            # If resuming from cooling, also set cooldown time remaining
            if resume_state['phase'] == 'cooling':
                cooldown_hours_setting = current_settings.get('cooldown_hours', COOLDOWN_HOURS)
                cooldown_start_time = resume_state['start_time'] + resume_state['print_duration'] + resume_state['pause_time_accumulated']
                cooldown_elapsed_seconds = resume_state['timestamp'] - cooldown_start_time
                cooldown_remaining_seconds = (cooldown_hours_setting * 3600) - cooldown_elapsed_seconds
                status_data['cooldown_time_remaining'] = max(0, int(cooldown_remaining_seconds))
                print(f"  Cooldown remaining: {cooldown_remaining_seconds/60:.1f} minutes")
            else:
                print(f"  Remaining time: {remaining_at_resume/60:.1f} minutes")
        else:
            # Normal start - show full duration
            status_data['print_time_remaining'] = print_duration_seconds

        # Emit immediate WebSocket update so UI responds instantly to START button
        emit_status_update()

        # PID Setup
        pid = PID(Kp=2.0, Ki=0.5, Kd=0.1, setpoint=desired_temp, output_limits=(-100, 100))

        # Check if we need warming up phase (only if not resuming)
        if not is_resuming:
            initial_temp = get_average_temp()
            skip_warmup = False

            # Check if user has skip_preheat enabled
            if current_settings.get('skip_preheat', False):
                skip_warmup = True
                warmup_complete = True
                print("\nSkipping preheat phase (user setting enabled)")
            elif initial_temp is not None and initial_temp >= desired_temp:
                # Already at or above target temperature, skip warming up phase
                skip_warmup = True
                warmup_complete = True
                print(f"\nChamber already at target temperature ({initial_temp:.1f}°C >= {desired_temp}°C), skipping warmup")
            else:
                # Warming up phase - reach target temp before starting timer
                status_data['phase'] = 'warming up'
                emit_status_update()  # WebSocket update for phase transition
                print(f"\nWarming up chamber to {desired_temp}°C...")

        while print_active and not shutdown_requested and not skip_warmup:
            # Check for stop conditions during warmup
            if stop_requested or emergency_stop_requested:
                print("Warmup stopped by user")
                break

            # Read temperatures
            avg_temp = get_average_temp()
            sensor_data = get_sensor_temps()

            if avg_temp is None:
                print("ERROR: Temperature sensor failure during warmup!")
                time.sleep(TEMP_UPDATE_INTERVAL)
                continue

            # Update temperature history
            current_time = time.time()
            with history_lock:
                temp_history.append({
                    'time': current_time,
                    'temp': avg_temp,
                    'setpoint': pid.setpoint
                })
                if len(temp_history) > MAX_HISTORY:
                    temp_history.pop(0)

            # Calculate ETA
            eta = calculate_eta(avg_temp, pid.setpoint)

            # Update status (show full print time since timer hasn't started)
            status_data['current_temp'] = avg_temp
            status_data['sensor_temps'] = [
                {'id': sid, 'name': name, 'temp': temp}
                for sid, name, temp in sensor_data
            ]
            status_data['setpoint'] = pid.setpoint
            status_data['print_time_remaining'] = print_duration_seconds
            status_data['eta_to_target'] = eta
            status_data['heater_on'] = heater_on
            status_data['fans_on'] = fans_on

            print(f"Warming up: {avg_temp:.1f}°C | Target: {pid.setpoint:.1f}°C | ETA: {eta}s")

            # Check if target temp reached (within 1°C)
            if abs(avg_temp - pid.setpoint) < 1.0:
                warmup_complete = True
                print(f"\nTarget temperature reached: {avg_temp:.1f}°C")

                if require_confirmation:
                    # Wait for user confirmation
                    print("Waiting for user confirmation to start print...")
                    with state_lock:
                        awaiting_preheat_confirmation = True
                        status_data['awaiting_preheat_confirmation'] = True

                    emit_status_update()  # Immediate WebSocket update for preheat confirmation modal
                    # Keep maintaining temperature while waiting for confirmation
                    while awaiting_preheat_confirmation and print_active and not shutdown_requested:
                        # Check for stop/emergency stop
                        if stop_requested or emergency_stop_requested:
                            print("Print stopped before confirmation")
                            break

                        # Check if user confirmed
                        if preheat_confirmed:
                            with state_lock:
                                awaiting_preheat_confirmation = False
                                status_data['awaiting_preheat_confirmation'] = False
                            emit_status_update()  # Immediate WebSocket update for preheat confirmed
                            print("Preheat confirmed by user. Starting print timer.")
                            break

                        # Continue PID control to maintain temp
                        avg_temp = get_average_temp()
                        if avg_temp is not None:
                            pid.setpoint = desired_temp
                            control = pid(avg_temp)

                            # Heater control
                            hysteresis = current_settings.get('hysteresis', HYSTERESIS) or HYSTERESIS
                            if not heater_manual_override and not emergency_stop:
                                if not heater_on and avg_temp < (pid.setpoint - hysteresis):
                                    heater_on = True
                                    GPIO.output(RELAY_PIN, GPIO.HIGH)
                                    status_data['heater_on'] = True
                                elif heater_on and avg_temp > (pid.setpoint + hysteresis):
                                    heater_on = False
                                    GPIO.output(RELAY_PIN, GPIO.LOW)
                                    status_data['heater_on'] = False

                            # Update status
                            status_data['current_temp'] = avg_temp
                            status_data['print_time_remaining'] = print_duration_seconds

                        time.sleep(TEMP_UPDATE_INTERVAL)

                # Break from warmup loop (either confirmation received or auto-start)
                break

            # PID control during warmup (if not emergency)
            if not emergency_stop:
                pid.setpoint = desired_temp
                control = pid(avg_temp)

                # Heater control (using configurable hysteresis)
                hysteresis = current_settings.get('hysteresis', HYSTERESIS) or HYSTERESIS
                if not heater_manual_override:
                    if not heater_on and avg_temp < (pid.setpoint - hysteresis):
                        heater_on = True
                        GPIO.output(RELAY_PIN, GPIO.HIGH)
                        status_data['heater_on'] = True
                    elif heater_on and avg_temp > (pid.setpoint + hysteresis):
                        heater_on = False
                        GPIO.output(RELAY_PIN, GPIO.LOW)
                        status_data['heater_on'] = False
            else:
                # Emergency stop - turn off heater
                if heater_on:
                    heater_on = False
                    GPIO.output(RELAY_PIN, GPIO.LOW)
                    status_data['heater_on'] = False

            # Emit WebSocket update for real-time UI updates during warmup
            emit_status_update()
            time.sleep(TEMP_UPDATE_INTERVAL)

        # If stopped during warmup, skip to cleanup
        if stop_requested or emergency_stop_requested or shutdown_requested:
            # Jump to cleanup section
            pass  # Will fall through to existing cleanup code after main loop
        else:
            # Warmup complete, now start the print timer (or continue from resume)
            if not is_resuming:
                # Only set these for fresh start, not when resuming
                start_time = time.time()
                total_paused_time = 0  # Track total time spent paused
                pause_start_time = 0   # Track when pause started

            # If resuming from cooling phase, skip heating loop and go straight to cooldown
            if is_resuming and resume_state['phase'] == 'cooling':
                print("Resuming in COOLING phase - skipping heating loop")
                status_data['phase'] = 'cooling'
                emit_status_update()
                # Delete old print state file (we're resuming now)
                delete_print_state()
                # Will jump to cooldown section below
            else:
                # Normal heating phase or resuming from heating/maintaining
                status_data['phase'] = 'heating'
                emit_status_update()  # WebSocket update for phase transition to heating

                print(f"Starting print timer: {print_duration_seconds/60:.1f} minutes")

                # Delete old print state file (we're starting/resuming now)
                delete_print_state()

                # Counter for periodic state saving (every 10 seconds = every 2 iterations)
                state_save_counter = 0

                # Main control loop (only runs if warmup completed successfully)
                while print_active and not shutdown_requested:
                    current_time = time.time()

                    # Handle pause state
                    if pause_requested:
                        with state_lock:
                            pause_requested = False
                            print_paused = not print_paused
                            status_data['print_paused'] = print_paused

                            if print_paused:
                                pause_start_time = current_time
                                print("Print PAUSED - timer stopped, temperature control active")
                            else:
                                # Resuming - add this pause duration to total
                                total_paused_time += (current_time - pause_start_time)
                                print("Print RESUMED - timer continuing")

                    # Calculate elapsed time (excluding paused time)
                    if print_paused:
                        # If currently paused, don't count time since pause started
                        elapsed = (pause_start_time - start_time) - total_paused_time
                    else:
                        elapsed = (current_time - start_time) - total_paused_time

                    with time_lock:
                        remaining = print_duration_seconds + additional_seconds - elapsed

                    # Check for stop conditions
                    if stop_requested or emergency_stop_requested:
                        print("Print stopped by user")
                        break

                    # Check if printer finished (FINISH state) - triggers cooldown
                    if printer_finished:
                        print("\nPrint finished on printer. Starting slow cool down.")
                        break

                    if remaining <= 0 and not print_paused:
                        print("\nPrint time expired. Starting slow cool down.")
                        break

                    # Read temperatures
                    avg_temp = get_average_temp()
                    sensor_data = get_sensor_temps()

                    if avg_temp is None:
                        print("ERROR: Temperature sensor failure during operation!")
                        time.sleep(TEMP_UPDATE_INTERVAL)
                        continue

                    # Update temperature history
                    with history_lock:
                        temp_history.append({
                            'time': current_time,
                            'temp': avg_temp,
                            'setpoint': pid.setpoint
                        })
                        if len(temp_history) > MAX_HISTORY:
                            temp_history.pop(0)

                    # Log data if enabled
                    if logging_enabled:
                        log_data.append([
                            datetime.now().isoformat(),
                            f"{elapsed:.1f}",
                            f"{avg_temp:.2f}",
                            f"{pid.setpoint:.2f}",
                            'ON' if heater_on else 'OFF',
                            'ON' if fans_on else 'OFF',
                            status_data['phase']
                        ])

                    # Calculate ETA and phase
                    eta = calculate_eta(avg_temp, pid.setpoint)
                    if abs(avg_temp - pid.setpoint) < 1.0:
                        status_data['phase'] = 'maintaining'
                    else:
                        status_data['phase'] = 'heating'

                    # Update status
                    status_data['current_temp'] = avg_temp
                    status_data['sensor_temps'] = [
                        {'id': sid, 'name': name, 'temp': temp}
                        for sid, name, temp in sensor_data
                    ]
                    status_data['setpoint'] = pid.setpoint
                    status_data['time_remaining'] = remaining
                    status_data['print_time_remaining'] = remaining
                    status_data['eta_to_target'] = eta
                    status_data['heater_on'] = heater_on
                    status_data['fans_on'] = fans_on
                    status_data['heater_manual'] = heater_manual_override
                    status_data['fans_manual'] = fans_manual_override

                    print(f"Temp: {avg_temp:.1f}°C | Target: {pid.setpoint:.1f}°C | Remaining: {remaining/60:.1f}min | ETA: {eta}s")

                    # PID control (unless manual override or emergency)
                    if not emergency_stop:
                        pid.setpoint = current_settings['desired_temp']  # Allow mid-print adjustment
                        control = pid(avg_temp)

                        # Heater control (using configurable hysteresis)
                        hysteresis = current_settings.get('hysteresis', HYSTERESIS) or HYSTERESIS
                        if not heater_manual_override:
                            if not heater_on and avg_temp < (pid.setpoint - hysteresis):
                                heater_on = True
                                GPIO.output(RELAY_PIN, GPIO.HIGH)
                                status_data['heater_on'] = True
                                print("Heater ON (PID)")
                            elif heater_on and avg_temp > (pid.setpoint + hysteresis):
                                heater_on = False
                                GPIO.output(RELAY_PIN, GPIO.LOW)
                                status_data['heater_on'] = False
                                print("Heater OFF (PID)")

                        # Fan control (if not manual override)
                        if not fans_manual_override:
                            if current_settings.get('fans_enabled', True) and not fans_on:
                                fans_on = True
                                GPIO.output(FAN1_PIN, GPIO.HIGH)
                                GPIO.output(FAN2_PIN, GPIO.HIGH)
                                status_data['fans_on'] = True
                    else:
                        # Emergency stop - turn off heater and fans
                        if heater_on:
                            heater_on = False
                            GPIO.output(RELAY_PIN, GPIO.LOW)
                            status_data['heater_on'] = False
                        if fans_on:
                            fans_on = False
                            GPIO.output(FAN1_PIN, GPIO.LOW)
                            GPIO.output(FAN2_PIN, GPIO.LOW)
                            status_data['fans_on'] = False

                    # Emit WebSocket update for real-time UI updates during print
                    emit_status_update()

                    # Periodically save print state for crash recovery (every 10 seconds)
                    state_save_counter += 1
                    if state_save_counter >= 2:  # Every 2 iterations × 5s = 10s
                        state_save_counter = 0
                        save_print_state(
                            phase=status_data['phase'],
                            start_time=start_time,
                            print_duration=print_duration_seconds,
                            pause_time_accumulated=total_paused_time,
                            is_paused=print_paused,
                            target_temp=desired_temp,
                            fans_enabled=current_settings.get('fans_enabled', True),
                            logging_enabled=logging_enabled,
                            time_adjustments=additional_seconds,
                            heater_manual=heater_manual_override,
                            fans_manual=fans_manual_override,
                            heater_state=heater_on,
                            fans_state=fans_on
                        )

                    time.sleep(TEMP_UPDATE_INTERVAL)

        # Print cycle ended - start cooldown (using configurable cooldown time)
        if not stop_requested and not emergency_stop_requested and not shutdown_requested:
            cooldown_hours = current_settings.get('cooldown_hours', COOLDOWN_HOURS)

            # If resuming from cooling phase, calculate remaining cooldown time
            if is_resuming and resume_state['phase'] == 'cooling':
                # Cooldown started after print completed
                cooldown_start_time = resume_state['start_time'] + resume_state['print_duration'] + resume_state['pause_time_accumulated']
                # Time that has already elapsed in cooldown
                cooldown_elapsed_seconds = resume_state['timestamp'] - cooldown_start_time
                cooldown_elapsed_hours = cooldown_elapsed_seconds / 3600.0
                # Calculate remaining cooldown time
                remaining_cooldown_hours = max(0, cooldown_hours - cooldown_elapsed_hours)
                print(f"Resuming cooldown: {cooldown_elapsed_hours:.2f}h elapsed, {remaining_cooldown_hours:.2f}h remaining")
                cooldown_hours = remaining_cooldown_hours

            slow_cool(
                pid,
                hours=cooldown_hours,
                start_time=start_time,
                print_duration=print_duration_seconds,
                pause_time_accumulated=total_paused_time,
                target_temp=desired_temp,
                fans_enabled=current_settings.get('fans_enabled', True),
                logging_enabled=logging_enabled,
                time_adjustments=additional_seconds
            )

        # Turn off everything
        with state_lock:
            print_active = False
            heater_on = False
            fans_on = False
            # Always turn off all outputs when print ends (ignore manual override for safety)
            GPIO.output(RELAY_PIN, GPIO.LOW)
            GPIO.output(FAN1_PIN, GPIO.LOW)
            GPIO.output(FAN2_PIN, GPIO.LOW)
            status_data['print_active'] = False
            status_data['heater_on'] = False
            status_data['fans_on'] = False
            status_data['phase'] = 'idle'
            status_data['print_time_remaining'] = 0
            stop_requested = False
            emergency_stop_requested = False
            printer_finished = False
            # Clear pause state
            print_paused = False
            pause_requested = False
            status_data['print_paused'] = False

        emit_status_update()  # WebSocket update for print cleanup/completion

        # Delete print state file (print is done or stopped)
        delete_print_state()

        print("Print cycle complete.")

# Printer MQTT Monitor Thread
def printer_monitor():
    """Monitor Bambu Lab X1C printer via MQTT"""
    global printer_mqtt_client, printer_connected, printer_status, start_requested, shutdown_requested
    global desired_temp, print_hours, print_minutes, fans_enabled, current_settings

    # Check if printer integration is enabled
    if not current_settings.get('printer_enabled', False):
        print("Printer integration disabled in settings")
        return

    printer_ip = current_settings.get('printer_ip', '')
    access_code = current_settings.get('printer_access_code', '')
    serial = current_settings.get('printer_serial', '')

    if not all([printer_ip, access_code, serial]):
        print("ERROR: Printer credentials not configured")
        return

    print(f"Starting printer monitor for {serial} at {printer_ip}")

    # Track previous print phase to detect transitions
    previous_phase = 'idle'
    previous_raw_state = 'IDLE'  # Track raw gcode_state for stop detection
    auto_start_triggered = False  # Track if we've already auto-started for this print
    last_trigger_time = 0  # Debounce: prevent rapid re-triggers
    heater_start_time = 0  # Track when heater was auto-started

    def on_connect(client, userdata, flags, rc, *args):
        global printer_connected
        if rc == 0:
            print("✓ Connected to printer MQTT broker")
            with printer_lock:
                printer_connected = True
                status_data['printer_connected'] = True

            # Subscribe to printer status reports
            topic = f"device/{serial}/report"
            client.subscribe(topic)
            print(f"  Subscribed to {topic}")
            emit_status_update()
        else:
            print(f"✗ MQTT connection failed with code {rc}")
            with printer_lock:
                printer_connected = False
                status_data['printer_connected'] = False

    def on_disconnect(client, userdata, rc, *args):
        global printer_connected
        print(f"Disconnected from printer MQTT (code: {rc})")
        with printer_lock:
            printer_connected = False
            status_data['printer_connected'] = False
        emit_status_update()

    def on_message(client, userdata, msg):
        nonlocal previous_phase, previous_raw_state, auto_start_triggered, last_trigger_time, heater_start_time
        global printer_status, start_requested, stop_requested, printer_finished, print_active
        global desired_temp, print_hours, print_minutes, fans_enabled

        try:
            payload = json.loads(msg.payload.decode('utf-8'))

            # Extract print status from MQTT message
            if 'print' in payload:
                print_data = payload['print']

                # Get print phase (idle, printing, paused, finish, failed)
                # IMPORTANT: Only update phase if gcode_state is actually present in message
                # Some MQTT messages don't include gcode_state, keep previous phase in that case
                gcode_state = print_data.get('gcode_state', None)

                # Map gcode_state to our simplified phase
                phase_map = {
                    'IDLE': 'idle',
                    'PREPARE': 'printing',
                    'RUNNING': 'printing',
                    'PAUSE': 'paused',
                    'FINISH': 'finish',
                    'FAILED': 'idle'
                }

                if gcode_state is not None:
                    phase = phase_map.get(gcode_state, previous_phase)
                else:
                    phase = previous_phase  # Keep previous phase if gcode_state not in message

                # Get file info - use sticky values to prevent flickering
                global last_file_name, last_progress, last_time_remaining
                subtask_name = print_data.get('subtask_name', '')
                gcode_file = print_data.get('gcode_file', '')
                new_file_name = subtask_name or gcode_file
                if new_file_name:
                    file_name = new_file_name
                    last_file_name = new_file_name
                else:
                    file_name = last_file_name  # Keep previous value

                # Get progress (0-100) - use sticky value
                new_progress = print_data.get('mc_percent', None)
                if new_progress is not None:
                    progress = new_progress
                    last_progress = new_progress
                else:
                    progress = last_progress  # Keep previous value

                # Get time remaining (in MINUTES from printer) - try multiple field names
                time_remaining_minutes = print_data.get('mc_remaining_time', None)
                if time_remaining_minutes is None:
                    time_remaining_minutes = print_data.get('remain_time', None)
                # Convert to seconds for internal use, use sticky value
                if time_remaining_minutes is not None:
                    time_remaining = time_remaining_minutes * 60
                    last_time_remaining = time_remaining
                else:
                    time_remaining = last_time_remaining  # Keep previous value


                # Try to detect material from AMS or external spool (inside print_data)
                # Priority: mapping (slicer assignment) > vir_slot > tray_tar > tray_now
                raw_material = ''
                tray_idx = -1  # Track which slot is being used
                is_external_spool = False

                # BEST SOURCE: 'mapping' field contains the slicer's filament assignment
                # e.g., [1] means the print uses slot 1
                mapping = print_data.get('mapping', None)
                if mapping and isinstance(mapping, list) and len(mapping) > 0:
                    try:
                        # Use the first mapped slot (for single-material prints)
                        mapped_slot = int(mapping[0])
                        if mapped_slot == 255:
                            # External spool
                            is_external_spool = True
                            # Check vir_slot for material info
                            vir_slot = print_data.get('vir_slot', [])
                            if vir_slot and isinstance(vir_slot, list) and len(vir_slot) > 0:
                                raw_material = vir_slot[0].get('tray_type', '')
                            # Check user override
                            external_override = current_settings.get('external_spool_material', '')
                            if external_override:
                                raw_material = external_override
                        elif 0 <= mapped_slot < 4:
                            tray_idx = mapped_slot
                            # Check user override for this AMS slot first
                            ams_overrides = current_settings.get('ams_slot_overrides', {})
                            slot_override = ams_overrides.get(str(tray_idx), '')
                            if slot_override:
                                raw_material = slot_override
                            elif 'ams' in print_data:
                                ams_data = print_data['ams']
                                if isinstance(ams_data, dict) and 'ams' in ams_data and len(ams_data['ams']) > 0:
                                    ams_unit = ams_data['ams'][0]
                                    if 'tray' in ams_unit and len(ams_unit['tray']) > tray_idx:
                                        tray = ams_unit['tray'][tray_idx]
                                        raw_material = tray.get('tray_type', '')
                    except (ValueError, TypeError, IndexError):
                        pass

                # FALLBACK: If mapping not available, use tray_tar/tray_now
                if not raw_material and 'ams' in print_data:
                    ams_data = print_data['ams']
                    if isinstance(ams_data, dict):
                        tray_tar = ams_data.get('tray_tar', None)
                        tray_now = ams_data.get('tray_now', None)
                        active_tray = tray_tar if tray_tar is not None else tray_now

                        if active_tray is not None:
                            try:
                                active_tray = int(active_tray)
                            except (ValueError, TypeError):
                                active_tray = None

                        if active_tray == 255:
                            is_external_spool = True
                            external_override = current_settings.get('external_spool_material', '')
                            if external_override:
                                raw_material = external_override
                            elif 'vt_tray' in print_data:
                                vt_tray = print_data['vt_tray']
                                if isinstance(vt_tray, dict):
                                    raw_material = vt_tray.get('tray_type', '')
                        elif active_tray is not None and 0 <= active_tray < 4:
                            if 'ams' in ams_data and len(ams_data['ams']) > 0:
                                try:
                                    tray_idx = active_tray
                                    ams_overrides = current_settings.get('ams_slot_overrides', {})
                                    slot_override = ams_overrides.get(str(tray_idx), '')
                                    if slot_override:
                                        raw_material = slot_override
                                    else:
                                        ams_unit = ams_data['ams'][0]
                                        if 'tray' in ams_unit and len(ams_unit['tray']) > tray_idx:
                                            tray = ams_unit['tray'][tray_idx]
                                            raw_material = tray.get('tray_type', '')
                                except (ValueError, IndexError, KeyError, TypeError):
                                    pass

                # Normalize material to match mapping keys (e.g., "PLA Basic" -> "PLA")
                # Check longer names first to avoid "PC" matching before "PLA" or "PETG"
                material = ''
                known_materials = ['PETG', 'PLA', 'ABS', 'ASA', 'NYLON', 'TPU', 'PC', 'HIPS']
                if raw_material:
                    raw_upper = raw_material.upper()
                    # First check for exact match (for user overrides)
                    if raw_upper in known_materials:
                        material = raw_upper
                    else:
                        # Then check for substring match
                        for mat in known_materials:
                            if mat in raw_upper:
                                material = mat
                                break

                # Fallback: try to extract from subtask_name or gcode_file using word boundaries
                if not material and file_name:
                    import re
                    # Match material as a word boundary (not inside other words)
                    # Check longer/more specific names first (PLA before PC)
                    for mat in known_materials:
                        # Use word boundary or underscore/dash separators
                        pattern = r'(?:^|[_\-\s])' + mat + r'(?:$|[_\-\s\.])'
                        if re.search(pattern, file_name.upper()):
                            material = mat
                            break

                # Apply stickiness for material - use last known value if current is empty
                global last_material
                if material:
                    last_material = material
                elif last_material and phase != 'idle':
                    material = last_material  # Keep previous value during print

                # Update printer status
                with printer_lock:
                    printer_status['phase'] = phase
                    printer_status['file'] = file_name
                    printer_status['material'] = material
                    printer_status['progress'] = progress
                    printer_status['time_remaining'] = time_remaining

                    status_data['printer_phase'] = phase
                    status_data['printer_file'] = file_name
                    status_data['printer_material'] = material
                    status_data['printer_progress'] = progress
                    status_data['printer_time_remaining'] = time_remaining

                # Get temperatures
                if 'nozzle_temper' in print_data:
                    nozzle_temp = print_data['nozzle_temper']
                    with printer_lock:
                        printer_status['nozzle_temp'] = nozzle_temp
                        status_data['printer_nozzle_temp'] = nozzle_temp

                if 'bed_temper' in print_data:
                    bed_temp = print_data['bed_temper']
                    with printer_lock:
                        printer_status['bed_temp'] = bed_temp
                        status_data['printer_bed_temp'] = bed_temp

                if 'chamber_temper' in print_data:
                    chamber_temp = print_data['chamber_temper']
                    with printer_lock:
                        printer_status['chamber_temp'] = chamber_temp
                        status_data['printer_chamber_temp'] = chamber_temp

                # Extract AMS slot data for UI display
                # Use remembered values as defaults to prevent flickering when data is missing
                global last_mapping_target, last_ams_slots, last_external_spool, last_tray_now
                ams_slots_data = last_ams_slots[:]  # Start with last known values
                external_spool_data = last_external_spool
                current_tray = last_tray_now  # Start with last known value
                target_tray = -1   # Target tray for print job (from mapping field)

                # Get target tray from 'mapping' field (slicer's filament assignment)
                # This is more reliable than tray_tar which only updates after filament change starts
                mapping = print_data.get('mapping', None)
                mapping_present = mapping and isinstance(mapping, list) and len(mapping) > 0
                if mapping_present:
                    try:
                        new_target = int(mapping[0])
                        # Update and remember the mapping target
                        target_tray = new_target
                        last_mapping_target = new_target
                    except (ValueError, TypeError):
                        pass

                # Only show target when print is active
                # Clear remembered target when printer is idle
                if phase == 'idle':
                    if last_mapping_target != -1:
                        last_mapping_target = -1
                    target_tray = -1  # Don't show target when idle
                elif target_tray == -1 and last_mapping_target != -1:
                    # If mapping not in this message but print is active, use remembered value
                    target_tray = last_mapping_target

                if 'ams' in print_data:
                    ams_data = print_data['ams']
                    if isinstance(ams_data, dict):
                        # Get current tray (which slot is loaded in toolhead)
                        tray_now_value = ams_data.get('tray_now', None)
                        if tray_now_value is not None:
                            if isinstance(tray_now_value, str):
                                try:
                                    current_tray = int(tray_now_value)
                                except ValueError:
                                    pass  # Keep previous value
                            else:
                                current_tray = tray_now_value
                            # Save to global
                            last_tray_now = current_tray

                        # Note: We no longer fall back to tray_tar as it causes flickering
                        # The remembered mapping target is used instead (set above)

                        if 'ams' in ams_data and len(ams_data['ams']) > 0:
                            try:
                                ams_unit = ams_data['ams'][0]
                                if 'tray' in ams_unit:
                                    for i, tray in enumerate(ams_unit['tray'][:4]):
                                        slot_type = tray.get('tray_type', '')
                                        if slot_type:  # Only update if we got actual data
                                            ams_slots_data[i] = slot_type
                                    # Save to global
                                    last_ams_slots = ams_slots_data[:]
                            except (IndexError, KeyError, TypeError):
                                pass
                if 'vt_tray' in print_data:
                    vt_tray = print_data['vt_tray']
                    if isinstance(vt_tray, dict):
                        ext_type = vt_tray.get('tray_type', '')
                        if ext_type:  # Only update if we got actual data
                            external_spool_data = ext_type
                            last_external_spool = ext_type

                with printer_lock:
                    status_data['ams_slots'] = ams_slots_data
                    status_data['external_spool'] = external_spool_data
                    status_data['tray_now'] = current_tray
                    status_data['tray_tar'] = target_tray

                # DETECT PRINT START AND AUTO-START HEATER
                auto_start_enabled = current_settings.get('auto_start_enabled', True)

                # Check if print just started (transition from idle to printing)
                # Use state_lock to prevent race conditions with multiple MQTT messages
                # Also add 30-second debounce to prevent rapid re-triggers
                current_time = time.time()
                with state_lock:
                    time_since_last = current_time - last_trigger_time
                    should_trigger = (
                        phase == 'printing' and
                        previous_phase != 'printing' and
                        not auto_start_triggered and
                        time_since_last > 30  # 30-second debounce
                    )
                    if should_trigger:
                        auto_start_triggered = True
                        last_trigger_time = current_time

                if should_trigger:
                    print(f"📄 Print detected: {file_name} ({material or 'unknown material'})")

                    # Auto-start heater if enabled and material is mapped
                    if auto_start_enabled and material and not print_active:
                        material_mappings = current_settings.get('material_mappings', {})

                        if material in material_mappings:
                            mapping = material_mappings[material]
                            target_temp = mapping['temp']
                            fans = mapping['fans']

                            # Convert time_remaining to hours/minutes
                            hours = time_remaining // 3600
                            minutes = (time_remaining % 3600) // 60

                            print(f"🤖 Auto-starting heater:")
                            print(f"   Target: {target_temp}°C")
                            print(f"   Fans: {'ON' if fans else 'OFF'}")
                            print(f"   Print time: {hours}h {minutes}m")

                            # Emit processing lock FIRST to lock UI buttons
                            emit_processing_lock('start')

                            # Update settings
                            with state_lock:
                                desired_temp = target_temp
                                print_hours = hours
                                print_minutes = minutes
                                fans_enabled = fans

                                current_settings['desired_temp'] = target_temp
                                current_settings['print_hours'] = hours
                                current_settings['print_minutes'] = minutes
                                current_settings['fans_enabled'] = fans

                                # Trigger heater start
                                start_requested = True
                                heater_start_time = current_time

                            # Emit notification
                            emit_notification(
                                f"{material} Print Detected",
                                f"Starting chamber heater: {target_temp}°C, Fans {'ON' if fans else 'OFF'}, {hours}h {minutes}m"
                            )
                        else:
                            print(f"⚠️  No material mapping found for '{material}' - skipping auto-start")
                    elif not auto_start_enabled:
                        print("   (Auto-start disabled)")

                # Detect print completion or cancellation and stop heater
                # Use raw gcode_state to differentiate:
                # - FINISH = print completed normally
                # - FAILED = user cancelled in Bambu Studio
                # - IDLE/UNKNOWN = ignore (normal state transitions during printing)
                heater_run_time = current_time - heater_start_time if heater_start_time > 0 else 0
                raw_gcode_state = print_data.get('gcode_state', None)  # None if not present

                # Only process stop detection if we have a valid gcode_state
                if raw_gcode_state:
                    # Log terminal states for monitoring
                    if raw_gcode_state in ('FINISH', 'FAILED'):
                        print(f"📡 Printer state: {raw_gcode_state} (prev={previous_raw_state}, active={print_active}, triggered={auto_start_triggered})", flush=True)

                    # Only stop heater on FINISH or FAILED states when previously RUNNING or PREPARE
                    if raw_gcode_state in ('FINISH', 'FAILED') and previous_raw_state in ('RUNNING', 'PREPARE', 'PAUSE'):
                        if print_active and auto_start_triggered:
                            # Emit processing lock FIRST to lock UI buttons before notification
                            emit_processing_lock('stop')
                            if raw_gcode_state == 'FINISH':
                                # Print completed normally - trigger cooldown
                                print(f"✅ Print finished on printer, starting cooldown (ran {heater_run_time:.0f}s)")
                                emit_notification("Print Finished", "Printer finished - starting cooldown")
                                with state_lock:
                                    printer_finished = True  # This triggers cooldown
                            else:  # FAILED
                                # User cancelled - stop immediately, no cooldown
                                print(f"🛑 Print cancelled in Bambu Studio, stopping heater (ran {heater_run_time:.0f}s)")
                                emit_notification("Print Cancelled", "Print stopped - heater off")
                                with state_lock:
                                    stop_requested = True  # This skips cooldown
                            # Reset flags for next print
                            auto_start_triggered = False
                            heater_start_time = 0

                    # Update previous raw state (only when we have a valid state)
                    previous_raw_state = raw_gcode_state

                # Update previous phase
                previous_phase = phase

                # Emit WebSocket update
                emit_status_update()

        except json.JSONDecodeError as e:
            print(f"Error parsing MQTT message: {e}")
        except Exception as e:
            print(f"Error processing MQTT message: {e}")

    # Create MQTT client (handle both paho-mqtt 1.x and 2.x APIs)
    try:
        # Try paho-mqtt 2.0+ API with VERSION2 (recommended)
        printer_mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"heater_controller_{int(time.time())}")
        print("Using paho-mqtt 2.x API (VERSION2)")
    except (AttributeError, TypeError):
        # Fall back to paho-mqtt 1.x API
        printer_mqtt_client = mqtt.Client(client_id=f"heater_controller_{int(time.time())}")
        print("Using paho-mqtt 1.x API")

    try:
        printer_mqtt_client.username_pw_set("bblp", access_code)
        printer_mqtt_client.tls_set(cert_reqs=ssl.CERT_NONE)
        printer_mqtt_client.tls_insecure_set(True)
        printer_mqtt_client.on_connect = on_connect
        printer_mqtt_client.on_disconnect = on_disconnect
        printer_mqtt_client.on_message = on_message
    except Exception as e:
        print(f"ERROR: Failed to configure MQTT client: {e}")
        return

    # Initial connection
    try:
        print(f"Connecting to printer at {printer_ip}:8883...", flush=True)
        printer_mqtt_client.connect(printer_ip, 8883, 60)
        printer_mqtt_client.loop_start()
        print("MQTT loop started", flush=True)
    except Exception as e:
        print(f"Initial MQTT connection failed: {e}", flush=True)

    # Connection monitoring loop with auto-reconnect
    while not shutdown_requested:
        try:
            if not printer_connected:
                print(f"Reconnecting to printer at {printer_ip}:8883...")
                printer_mqtt_client.reconnect()

            time.sleep(5)

        except Exception as e:
            print(f"Printer MQTT error: {e}")
            with printer_lock:
                printer_connected = False
                status_data['printer_connected'] = False
            time.sleep(10)  # Wait before retry

    # Cleanup on shutdown
    if printer_mqtt_client:
        printer_mqtt_client.loop_stop()
        printer_mqtt_client.disconnect()
    print("Printer monitor stopped")

# Flask Web Interface
app = Flask(__name__)

# Initialize SocketIO for real-time WebSocket communication
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# WebSocket event emitters - for real-time updates to connected clients
def emit_status_update():
    """Emit current status to all connected clients with sequence number"""
    global status_sequence_number

    with state_lock:
        # Increment sequence number to identify this message
        with sequence_lock:
            status_sequence_number += 1
            status_data['sequence'] = status_sequence_number

        socketio.emit('status_update', status_data, namespace='/')

def emit_notification(title, message):
    """Emit notification to all connected clients"""
    socketio.emit('notification', {'title': title, 'message': message}, namespace='/')

def emit_processing_lock(action):
    """Emit processing lock to frontend when backend initiates an action (e.g., printer-triggered stop)"""
    socketio.emit('processing_lock', {'action': action}, namespace='/')

def emit_history_update(data_point):
    """Emit new history data point to all connected clients"""
    socketio.emit('history_update', data_point, namespace='/')

# HTML template (will be quite large with all features)
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>X1C Chamber Heater Controller</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        :root {
            --bg-color: #f0f0f0;
            --card-bg: white;
            --text-color: #333;
            --border-color: #e0e0e0;
            --primary-color: #4CAF50;
            --danger-color: #f44336;
            --warning-color: #ff9800;
        }

        [data-theme="dark"] {
            --bg-color: #1a1a1a;
            --card-bg: #2d2d2d;
            --text-color: #e0e0e0;
            --border-color: #404040;
            --primary-color: #66BB6A;
            --danger-color: #EF5350;
            --warning-color: #FFA726;
        }

        * {
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: var(--bg-color);
            color: var(--text-color);
            transition: background-color 0.3s, color 0.3s;
        }

        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }

        h1 {
            margin: 0;
            font-size: 28px;
        }

        .theme-toggle {
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            padding: 8px 16px;
            border-radius: 5px;
            cursor: pointer;
            font-size: 14px;
            color: var(--text-color);
        }

        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            max-width: 1400px;
            margin: 0 auto;
        }

        .card {
            background: var(--card-bg);
            border-radius: 10px;
            padding: 20px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }

        .card h2 {
            margin: 0 0 15px 0;
            font-size: 18px;
            border-bottom: 2px solid var(--primary-color);
            padding-bottom: 8px;
        }

        .status-value {
            font-size: 36px;
            font-weight: bold;
            margin: 10px 0;
        }

        .status-label {
            font-size: 12px;
            color: #666;
            text-transform: uppercase;
        }

        .indicator {
            display: inline-block;
            width: 12px;
            height: 12px;
            border-radius: 50%;
            margin-right: 8px;
        }

        .indicator.on { background-color: var(--primary-color); }
        .indicator.off { background-color: #ccc; }
        .indicator.alarm {
            background-color: var(--danger-color);
            animation: blink 1s infinite;
        }

        @keyframes blink {
            0%, 50% { opacity: 1; }
            51%, 100% { opacity: 0.3; }
        }

        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }

        .input-group {
            margin: 15px 0;
        }

        .input-group label {
            display: block;
            margin-bottom: 5px;
            font-size: 14px;
            font-weight: 500;
        }

        .input-group input, .input-group select {
            width: 100%;
            padding: 10px;
            border: 1px solid var(--border-color);
            border-radius: 5px;
            font-size: 14px;
            background: var(--card-bg);
            color: var(--text-color);
        }

        .button {
            padding: 12px 24px;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 600;
            transition: opacity 0.2s;
            margin: 5px;
        }

        .button:hover:not(:disabled) { opacity: 0.8; }
        .button:disabled {
            opacity: 0.4;
            cursor: not-allowed;
            background: #666 !important;
            color: #999 !important;
        }

        .button.primary { background: var(--primary-color); color: white; }
        .button.danger { background: var(--danger-color); color: white; }
        .button.warning { background: var(--warning-color); color: white; }
        .button.secondary { background: #757575; color: white; }

        /* Processing state - button is actively processing user action */
        .button.processing {
            position: relative;
            pointer-events: none;
            padding-right: 45px !important; /* Make room for spinner */
        }

        .button.processing::after {
            content: "";
            position: absolute;
            width: 16px;
            height: 16px;
            top: 50%;
            right: 15px;
            margin-top: -8px;
            border: 2px solid rgba(255, 255, 255, 0.3);
            border-radius: 50%;
            border-top-color: white;
            animation: spin 0.8s linear infinite;
        }

        /* Processing-blocked state - button interaction temporarily disabled during optimistic lock */
        .button.processing-blocked {
            opacity: 0.5;
            pointer-events: none;
            cursor: not-allowed;
        }

        .button-group {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin: 15px 0;
        }

        .toggle-switch {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 10px 0;
        }

        .switch {
            position: relative;
            width: 50px;
            height: 24px;
        }

        .switch input {
            opacity: 0;
            width: 0;
            height: 0;
        }

        .slider {
            position: absolute;
            cursor: pointer;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background-color: #ccc;
            transition: .4s;
            border-radius: 24px;
        }

        .slider:before {
            position: absolute;
            content: "";
            height: 18px;
            width: 18px;
            left: 3px;
            bottom: 3px;
            background-color: white;
            transition: .4s;
            border-radius: 50%;
        }

        input:checked + .slider {
            background-color: var(--primary-color);
        }

        input:checked + .slider:before {
            transform: translateX(26px);
        }

        .alert {
            padding: 15px;
            border-radius: 5px;
            margin: 15px 0;
            font-weight: 600;
        }

        .alert.danger {
            background: var(--danger-color);
            color: white;
        }

        .alert.warning {
            background: var(--warning-color);
            color: white;
        }

        .sensor-list {
            list-style: none;
            padding: 0;
            margin: 10px 0;
        }

        .sensor-list li {
            padding: 8px 0;
            border-bottom: 1px solid var(--border-color);
            display: flex;
            justify-content: space-between;
        }

        .chart-container {
            position: relative;
            height: 300px;
            margin: 20px 0;
        }

        .preset-item {
            padding: 10px;
            margin: 5px 0;
            border: 1px solid var(--border-color);
            border-radius: 5px;
            cursor: pointer;
            transition: background 0.2s;
        }

        .preset-item:hover {
            background: var(--border-color);
        }

        .time-adjust {
            display: flex;
            gap: 10px;
            align-items: center;
        }

        .time-adjust input {
            width: 80px;
        }

        .full-width {
            grid-column: 1 / -1;
        }

        @media (max-width: 768px) {
            .grid {
                grid-template-columns: 1fr;
            }
        }

        /* Modal Styles */
        .modal {
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0,0,0,0.5);
        }

        .modal-content {
            background-color: var(--card-bg);
            margin: 5% auto;
            padding: 30px;
            border: 1px solid var(--border-color);
            border-radius: 10px;
            width: 90%;
            max-width: 600px;
            max-height: 80vh;
            overflow-y: auto;
        }

        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            border-bottom: 2px solid var(--primary-color);
            padding-bottom: 10px;
        }

        .modal-header h2 {
            margin: 0;
            font-size: 24px;
        }

        .close {
            color: var(--text-color);
            font-size: 32px;
            font-weight: bold;
            cursor: pointer;
            background: none;
            border: none;
        }

        .close:hover {
            color: var(--danger-color);
        }

        .settings-section {
            margin: 20px 0;
            padding: 15px;
            border: 1px solid var(--border-color);
            border-radius: 5px;
        }

        .settings-section h3 {
            margin: 0 0 15px 0;
            font-size: 16px;
        }

        /* In-page notification toast */
        .notification-toast {
            display: none;
            position: fixed;
            z-index: 2000;
            left: 50%;
            top: 20%;
            transform: translate(-50%, -50%);
            background-color: var(--card-bg);
            border: 2px solid var(--primary-color);
            border-radius: 10px;
            padding: 20px 30px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.3);
            min-width: 300px;
            max-width: 500px;
            text-align: center;
            cursor: pointer;
            animation: slideIn 0.3s ease-out;
        }

        @keyframes slideIn {
            from {
                opacity: 0;
                transform: translate(-50%, -60%);
            }
            to {
                opacity: 1;
                transform: translate(-50%, -50%);
            }
        }

        @keyframes slideOut {
            from {
                opacity: 1;
                transform: translate(-50%, -50%);
            }
            to {
                opacity: 0;
                transform: translate(-50%, -40%);
            }
        }

        .notification-toast.hiding {
            animation: slideOut 0.3s ease-out forwards;
        }

        .notification-toast h3 {
            margin: 0 0 10px 0;
            font-size: 20px;
            color: var(--primary-color);
        }

        .notification-toast p {
            margin: 0;
            font-size: 16px;
            color: var(--text-color);
        }

        .notification-overlay {
            display: none;
            position: fixed;
            z-index: 1999;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0,0,0,0.3);
            backdrop-filter: blur(3px);
        }

        .probe-rename {
            display: flex;
            align-items: center;
            margin: 10px 0;
            gap: 10px;
        }

        .probe-rename label {
            flex: 0 0 100px;
            font-size: 14px;
        }

        .probe-rename input {
            flex: 1;
            padding: 8px;
            border: 1px solid var(--border-color);
            border-radius: 5px;
            background: var(--bg-color);
            color: var(--text-color);
        }

        .radio-group {
            display: flex;
            gap: 20px;
            margin: 10px 0;
        }

        .radio-group label {
            display: flex;
            align-items: center;
            gap: 8px;
            cursor: pointer;
        }

        .radio-group input[type="radio"] {
            width: auto;
            cursor: pointer;
        }

        /* Connection status dot */
        .connection-dot {
            display: inline-block;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            margin-right: 6px;
        }
        .connection-dot.connected { background: #4CAF50; }
        .connection-dot.disconnected { background: #f44336; }

        /* AMS Slot Buttons */
        .ams-slot-btn {
            padding: 10px 14px;
            border: 2px solid var(--border-color, #ccc);
            border-radius: 8px;
            background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
            cursor: pointer;
            font-size: 12px;
            font-weight: bold;
            transition: all 0.2s;
            min-width: 75px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        body.dark .ams-slot-btn {
            background: linear-gradient(135deg, #3a3a3a 0%, #2a2a2a 100%);
            border-color: #555;
        }
        .ams-slot-btn:hover {
            border-color: var(--primary-color);
            background: var(--primary-color);
            color: white;
            transform: translateY(-1px);
            box-shadow: 0 3px 6px rgba(0,0,0,0.15);
        }
        .ams-slot-btn.external {
            min-width: 130px;
        }
        .ams-slot-btn.has-override {
            border-color: #ff9800;
            background: linear-gradient(135deg, #fff8e1 0%, #ffecb3 100%);
        }
        body.dark .ams-slot-btn.has-override {
            background: linear-gradient(135deg, #4a3d20 0%, #3d3020 100%);
        }
        /* Active slot - currently loaded in toolhead (green) */
        .ams-slot-btn.loaded {
            border-color: #4CAF50;
            background: linear-gradient(135deg, #e8f5e9 0%, #c8e6c9 100%);
            box-shadow: 0 0 0 2px rgba(76, 175, 80, 0.3), 0 2px 4px rgba(0,0,0,0.1);
        }
        body.dark .ams-slot-btn.loaded {
            background: linear-gradient(135deg, #1b3d1c 0%, #2e5830 100%);
            box-shadow: 0 0 0 2px rgba(76, 175, 80, 0.4), 0 2px 4px rgba(0,0,0,0.2);
        }
        .ams-slot-btn.loaded::after {
            content: " ●";
            color: #4CAF50;
        }
        /* Target slot - what the print job will use (blue) */
        .ams-slot-btn.target {
            border-color: #2196F3;
            background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%);
            box-shadow: 0 0 0 3px rgba(33, 150, 243, 0.3), 0 2px 4px rgba(0,0,0,0.1);
        }
        body.dark .ams-slot-btn.target {
            background: linear-gradient(135deg, #1a3a52 0%, #1e4976 100%);
            box-shadow: 0 0 0 3px rgba(33, 150, 243, 0.4), 0 2px 4px rgba(0,0,0,0.2);
        }
        .ams-slot-btn.target::after {
            content: " ▶";
            color: #2196F3;
        }
        /* When both loaded AND target (same slot) */
        .ams-slot-btn.loaded.target {
            border-color: #4CAF50;
            background: linear-gradient(135deg, #e8f5e9 0%, #c8e6c9 100%);
            box-shadow: 0 0 0 3px rgba(76, 175, 80, 0.4), 0 2px 4px rgba(0,0,0,0.1);
        }
        body.dark .ams-slot-btn.loaded.target {
            background: linear-gradient(135deg, #1b3d1c 0%, #2e5830 100%);
        }
        .ams-slot-btn.loaded.target::after {
            content: " ●▶";
            color: #4CAF50;
        }

        /* Floating Camera PiP Window */
        .camera-pip {
            position: fixed;
            bottom: 20px;
            right: 20px;
            width: 320px;
            background: var(--card-bg, white);
            border-radius: 10px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.3);
            z-index: 1500;
            overflow: hidden;
            resize: both;
            min-width: 200px;
            min-height: 150px;
        }
        .camera-pip.hidden {
            display: none;
        }
        .camera-pip.minimized {
            width: 150px !important;
            height: auto !important;
            resize: none;
        }
        .camera-pip.minimized .camera-pip-content {
            display: none;
        }
        .camera-pip.large {
            width: 640px;
        }
        .camera-pip-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 8px 12px;
            background: var(--primary-color);
            color: white;
            cursor: move;
            font-size: 14px;
            font-weight: bold;
        }
        .camera-pip-controls {
            display: flex;
            gap: 5px;
        }
        .pip-btn {
            background: rgba(255,255,255,0.2);
            border: none;
            color: white;
            width: 24px;
            height: 24px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .pip-btn:hover {
            background: rgba(255,255,255,0.4);
        }
        .pip-btn.close:hover {
            background: #f44336;
        }
        .camera-pip-content {
            background: #000;
        }
        .camera-pip-content img {
            display: block;
        }

        /* Button danger style */
        .button.danger {
            background: #f44336;
            color: white;
        }
        .button.danger:hover {
            background: #d32f2f;
        }
        .button.danger:disabled {
            background: #999;
        }
    </style>

    <!-- Socket.IO Client Library for WebSocket communication -->
    <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
</head>
<body>
    <div class="header">
        <h1>🔥 X1C Chamber Heater Controller</h1>
        <button class="theme-toggle" id="settings-btn" onclick="openSettings()">⚙️ Settings</button>
    </div>

    <div id="fire-alert" class="alert danger" style="display: none;">
        🔥 FIRE DETECTED! Emergency Shutdown Active
    </div>

    <!-- Resume Print Banner (Crash Recovery) -->
    <div id="resume-banner" class="alert warning" style="display: none;">
        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
            <div>
                <strong>🔄 Print Interrupted</strong>
                <p style="margin: 5px 0 0 0;">A print cycle was in progress before restart. Would you like to resume?</p>
            </div>
            <div style="display: flex; gap: 10px;">
                <button class="button primary" onclick="resumePrint()">▶ RESUME PRINT</button>
                <button class="button danger" onclick="abortResume()">✖ ABORT</button>
            </div>
        </div>
    </div>

    <!-- Settings Modal -->
    <div id="settings-modal" class="modal">
        <div class="modal-content">
            <div class="modal-header">
                <h2>⚙️ Settings</h2>
                <button class="close" onclick="closeSettings()">&times;</button>
            </div>

            <div class="settings-section">
                <h3>Display</h3>
                <div class="toggle-switch">
                    <span>Dark Mode</span>
                    <label class="switch">
                        <input type="checkbox" id="dark-mode-toggle" onchange="toggleTheme()">
                        <span class="slider"></span>
                    </label>
                </div>

                <div class="input-group">
                    <label>Temperature Units</label>
                    <div class="radio-group">
                        <label>
                            <input type="radio" name="temp-unit" value="C" checked onchange="changeTempUnit()">
                            Celsius (°C)
                        </label>
                        <label>
                            <input type="radio" name="temp-unit" value="F" onchange="changeTempUnit()">
                            Fahrenheit (°F)
                        </label>
                    </div>
                </div>
            </div>

            <div class="settings-section">
                <h3>Control Parameters</h3>
                <div class="input-group">
                    <label>Hysteresis (°C)</label>
                    <input type="number" id="hysteresis" value="2.0" min="0.5" max="10" step="0.5">
                    <small style="display: block; margin-top: 5px; color: #666;">Temperature band for heater control (±value from setpoint)</small>
                </div>

                <div class="input-group">
                    <label>Cooldown Time (hours)</label>
                    <input type="number" id="cooldown-hours" value="4" min="0" max="12" step="0.5">
                    <small style="display: block; margin-top: 5px; color: #666;">Duration for slow cooldown phase after print</small>
                </div>

                <div class="input-group">
                    <label>Cooldown Target Temperature (<span id="cooldown-temp-unit">°C</span>)</label>
                    <input type="number" id="cooldown-target-temp" value="21" min="0" max="50" step="0.5">
                    <small style="display: block; margin-top: 5px; color: #666;">Target temperature for cooldown phase (default: 21°C / 70°F)</small>
                </div>

                <div class="toggle-switch" style="margin-top: 15px;">
                    <span>Require Preheat Confirmation</span>
                    <label class="switch">
                        <input type="checkbox" id="require-preheat-confirmation">
                        <span class="slider"></span>
                    </label>
                </div>
                <small style="display: block; margin-top: 5px; color: #666;">Wait for user confirmation after reaching target temperature before starting print timer</small>

                <div class="toggle-switch" style="margin-top: 15px;">
                    <span>Skip Preheat Phase</span>
                    <label class="switch">
                        <input type="checkbox" id="skip-preheat">
                        <span class="slider"></span>
                    </label>
                </div>
                <small style="display: block; margin-top: 5px; color: #666;">Skip warming up phase and start print timer immediately</small>
            </div>

            <div class="settings-section">
                <h3>Probe Names</h3>
                <div id="probe-names-list">
                    <!-- Will be populated dynamically -->
                </div>
            </div>

            <div class="settings-section">
                <h3>🖨️ Printer Integration</h3>
                <div class="settings-row">
                    <label>Enable Printer Integration</label>
                    <input type="checkbox" id="printer-enabled-toggle">
                </div>
                <div class="settings-row">
                    <label>Printer IP Address</label>
                    <input type="text" id="printer-ip" placeholder="192.168.1.253" style="width: 150px;">
                </div>
                <div class="settings-row">
                    <label>LAN Access Code</label>
                    <input type="password" id="printer-access-code" placeholder="From printer settings" style="width: 150px;">
                </div>
                <div class="settings-row">
                    <label>Printer Serial Number</label>
                    <input type="text" id="printer-serial" placeholder="00M00A340600040" style="width: 180px;">
                </div>
                <div class="settings-row">
                    <label>Auto-Start Heater on Print</label>
                    <input type="checkbox" id="auto-start-toggle" checked>
                </div>
                <small style="display: block; margin-top: 5px; color: #666;">Automatically start chamber heater when a print begins on the printer</small>

                <button class="button secondary" onclick="testPrinterConnection()" id="test-connection-btn" style="margin-top: 15px; font-size: 12px; width: 100%;">
                    🔌 Test Connection
                </button>
                <div id="test-connection-result" style="margin-top: 8px; font-size: 12px; display: none;"></div>
            </div>

            <div class="settings-section">
                <h3>🎨 Material Temperature Settings</h3>
                <small style="display: block; margin-bottom: 10px; color: #666;">Set chamber temperature and fan settings for each material type</small>
                <div id="material-mappings-list">
                    <!-- Will be populated dynamically -->
                </div>
                <button class="button secondary" onclick="addMaterialMapping()" style="margin-top: 10px; font-size: 12px;">
                    ➕ Add Material
                </button>
            </div>

            <div class="settings-section">
                <h3>📦 AMS Slot Overrides</h3>
                <small style="display: block; margin-bottom: 10px; color: #666;">Manually assign materials to AMS slots if RFID detection fails. Leave blank to auto-detect.</small>
                <div id="ams-slot-overrides-list">
                    <!-- Will be populated dynamically -->
                </div>
            </div>

            <div class="settings-section">
                <h3>🧵 External Spool</h3>
                <div class="settings-row">
                    <label>External Spool Material</label>
                    <select id="external-spool-material" style="width: 150px;">
                        <option value="">Auto-detect</option>
                    </select>
                </div>
                <small style="display: block; margin-top: 5px; color: #666;">Material loaded in the external spool holder (not AMS)</small>
            </div>

            <div style="margin-top: 20px; display: flex; gap: 10px;">
                <button class="button primary" onclick="saveAllSettings()" style="flex: 1;">
                    💾 Save All Settings
                </button>
                <button class="button secondary" onclick="closeSettings()" style="flex: 1;">
                    Cancel
                </button>
            </div>
        </div>
    </div>

    <!-- Preheat Confirmation Modal -->
    <div id="preheat-modal" class="modal">
        <div class="modal-content" style="max-width: 400px;">
            <div class="modal-header">
                <h2>🔥 Target Temperature Reached!</h2>
            </div>

            <p style="margin: 20px 0; font-size: 16px;">
                The chamber has reached the target temperature and is ready for printing.
            </p>

            <p style="margin: 20px 0; font-size: 14px; color: #666;">
                Click "START PRINT" to begin the print timer, or wait to maintain temperature.
            </p>

            <button class="button primary" onclick="confirmPreheat()" style="width: 100%; font-size: 18px; padding: 15px;">
                ▶ START PRINT
            </button>
        </div>
    </div>

    <!-- In-page Notification Toast -->
    <div id="notification-overlay" class="notification-overlay" onclick="dismissNotification()"></div>
    <div id="notification-toast" class="notification-toast" onclick="dismissNotification()">
        <h3 id="notification-title">Notification</h3>
        <p id="notification-body">Message here</p>
    </div>

    <!-- Floating Camera PiP Window -->
    <div id="camera-pip" class="camera-pip hidden">
        <div class="camera-pip-header">
            <span>📷 Camera</span>
            <div class="camera-pip-controls">
                <button onclick="resetCameraPosition()" title="Reset Position" class="pip-btn">⌂</button>
                <button onclick="resizeCamera()" title="Resize" class="pip-btn">⤢</button>
                <button onclick="minimizeCamera()" title="Minimize" class="pip-btn">─</button>
                <button onclick="closeCamera()" title="Close" class="pip-btn close">✕</button>
            </div>
        </div>
        <div class="camera-pip-content">
            <img id="camera-feed" src="" alt="Camera Feed" style="width: 100%; height: auto; display: none;">
            <div id="camera-placeholder" style="display: flex; align-items: center; justify-content: center; height: 180px; color: #666;">
                Loading camera...
            </div>
        </div>
    </div>

    <!-- AMS Slot Editor Modal -->
    <div id="ams-editor-modal" class="modal">
        <div class="modal-content" style="max-width: 350px;">
            <div class="modal-header">
                <h2 id="ams-editor-title">Edit AMS Slot</h2>
                <button class="close" onclick="closeAmsEditor()">&times;</button>
            </div>

            <div style="margin-bottom: 15px; padding: 10px; background: var(--card-bg, #f0f0f0); border-radius: 5px;">
                <div style="font-size: 12px; color: #666;">Auto-detected from printer:</div>
                <div id="ams-auto-detected" style="font-weight: bold;">--</div>
            </div>

            <div class="settings-row" style="margin-bottom: 15px;">
                <label>Material Override</label>
                <select id="ams-editor-material" style="width: 120px;">
                    <option value="">Auto-detect</option>
                </select>
            </div>

            <div id="ams-material-settings" style="padding: 10px; background: var(--card-bg, #f0f0f0); border-radius: 5px; margin-bottom: 15px;">
                <div style="font-size: 12px; color: #666; margin-bottom: 8px;">Chamber Settings for this Material</div>
                <div class="settings-row" style="margin-bottom: 8px;">
                    <label>Temperature</label>
                    <input type="number" id="ams-editor-temp" style="width: 70px;" min="0" max="100" value="0">°C
                </div>
                <div class="settings-row">
                    <label>Fans Enabled</label>
                    <input type="checkbox" id="ams-editor-fans">
                </div>
            </div>

            <input type="hidden" id="ams-editor-slot">
            <input type="hidden" id="ams-editor-is-external">

            <div style="display: flex; gap: 10px;">
                <button class="button primary" onclick="saveAmsSlot()" style="flex: 1;">💾 Save</button>
                <button class="button secondary" onclick="closeAmsEditor()" style="flex: 1;">Cancel</button>
            </div>
        </div>
    </div>

    <div class="grid">
        <!-- Status Card -->
        <div class="card">
            <h2>Status</h2>
            <div class="status-label">Current Temperature</div>
            <div class="status-value" id="temp">--°C</div>
            <div class="status-label">Target: <span id="setpoint">--°C</span></div>
            <div class="status-label">Phase: <span id="phase">IDLE</span></div>
            <div class="status-label">ETA to Target: <span id="eta">--</span></div>
        </div>

        <!-- Time Remaining Card -->
        <div class="card">
            <h2>Time Remaining</h2>
            <div class="status-label">Print Time</div>
            <div class="status-value" id="print-time">--</div>
            <div class="status-label">Cooldown Time</div>
            <div class="status-value" id="cooldown-time" style="font-size: 24px;">--</div>
        </div>

        <!-- Controls Card -->
        <div class="card">
            <h2>Print Controls</h2>
            <div class="button-group">
                <button class="button primary" id="start-btn" onclick="startPrint()">▶ START</button>
                <button class="button secondary" id="pause-btn" onclick="pausePrint()" disabled>⏸ PAUSE</button>
                <button class="button warning" id="stop-btn" onclick="stopPrint()" disabled>■ STOP</button>
                <button class="button danger" id="emergency-stop-btn" onclick="emergencyStop()">⚠ EMERGENCY STOP</button>
            </div>

            <div class="toggle-switch">
                <span>
                    <span class="indicator" id="heater-ind"></span>
                    Heater <span id="heater-status">(Auto)</span>
                </span>
                <label class="switch">
                    <input type="checkbox" id="heater-toggle" onchange="toggleHeater()">
                    <span class="slider"></span>
                </label>
            </div>

            <div class="toggle-switch">
                <span>
                    <span class="indicator" id="fans-ind"></span>
                    Fans <span id="fans-status">(Auto)</span>
                </span>
                <label class="switch">
                    <input type="checkbox" id="fans-toggle" onchange="toggleFans()">
                    <span class="slider"></span>
                </label>
            </div>

            <div class="toggle-switch">
                <span>
                    <span class="indicator" id="lights-ind"></span>
                    Lights
                </span>
                <label class="switch">
                    <input type="checkbox" id="lights-toggle" onchange="toggleLights()">
                    <span class="slider"></span>
                </label>
            </div>

            <button class="button danger" id="reset-btn" onclick="resetFire()" disabled style="width: 100%; margin-top: 15px;">
                RESET FIRE ALARM
            </button>
        </div>

        <!-- Configuration Card -->
        <div class="card">
            <h2>Configuration</h2>

            <div class="input-group">
                <label id="target-temp-label">Target Temperature (°C)</label>
                <input type="number" id="target-temp" value="60" min="0" max="212" step="0.5">
            </div>

            <div class="input-group">
                <label>Print Time</label>
                <div style="display: flex; gap: 10px;">
                    <input type="number" id="print-hours" value="8" min="0" max="24" style="width: 50%;">
                    <span style="margin: 10px 5px;">h</span>
                    <input type="number" id="print-minutes" value="0" min="0" max="59" style="width: 50%;">
                    <span style="margin: 10px 5px;">m</span>
                </div>
            </div>

            <div class="time-adjust">
                <button class="button secondary" onclick="adjustTime(-5)">-5m</button>
                <button class="button secondary" onclick="adjustTime(5)">+5m</button>
                <button class="button secondary" onclick="adjustTime(15)">+15m</button>
            </div>

            <div class="toggle-switch">
                <span>Enable Fans</span>
                <label class="switch">
                    <input type="checkbox" id="fans-enabled" checked>
                    <span class="slider"></span>
                </label>
            </div>

            <div class="toggle-switch">
                <span>Enable Logging</span>
                <label class="switch">
                    <input type="checkbox" id="logging-enabled">
                    <span class="slider"></span>
                </label>
            </div>

            <button class="button primary" onclick="saveSettings()" style="width: 100%; margin-top: 15px;">
                💾 Save Settings
            </button>

            <button class="button secondary" onclick="downloadLog()" style="width: 100%; margin-top: 10px;">
                📥 Download Log CSV
            </button>
        </div>

        <!-- Presets Card -->
        <div class="card">
            <h2>Presets</h2>
            <div id="presets-list"></div>

            <div class="input-group" style="margin-top: 15px;">
                <label>Preset Name</label>
                <input type="text" id="preset-name" placeholder="My Preset">
            </div>
            <button class="button primary" onclick="savePreset()" style="width: 100%;">
                ➕ Save Current as Preset
            </button>
        </div>

        <!-- Individual Sensors Card -->
        <div class="card">
            <h2>Individual Sensors</h2>
            <ul class="sensor-list" id="sensor-list"></ul>
        </div>

        <!-- Printer Status Card -->
        <div class="card" id="printer-card">
            <h2>🖨️ Printer Status</h2>
            <div id="printer-connection-status" style="margin-bottom: 10px;">
                <span class="connection-dot disconnected"></span>
                <span id="printer-connection-text">Not configured</span>
            </div>

            <div id="printer-print-info" style="margin-bottom: 15px; padding: 10px; background: var(--card-bg, #f0f0f0); border-radius: 5px;">
                <div style="font-size: 12px; color: #666;">Current Print</div>
                <div id="printer-file-name" style="font-weight: bold; word-break: break-all;">No active print</div>
                <div id="printer-progress-container" style="margin-top: 8px; display: none;">
                    <div style="display: flex; justify-content: space-between; font-size: 12px;">
                        <span id="printer-material-display">--</span>
                        <span id="printer-time-remaining">--</span>
                    </div>
                    <div style="background: #ddd; border-radius: 3px; height: 8px; margin-top: 4px;">
                        <div id="printer-progress-bar" style="background: #4CAF50; height: 100%; border-radius: 3px; width: 0%; transition: width 0.3s;"></div>
                    </div>
                    <div id="printer-progress-text" style="text-align: center; font-size: 11px; margin-top: 2px;">0%</div>
                </div>
            </div>

            <div style="margin-bottom: 10px;">
                <div style="font-size: 12px; color: #666; margin-bottom: 5px;">📦 AMS Slots (click to configure)</div>
                <div id="ams-slots-display" style="display: flex; gap: 5px; flex-wrap: wrap;">
                    <button class="ams-slot-btn" onclick="openAmsSlotEditor(0)">1: --</button>
                    <button class="ams-slot-btn" onclick="openAmsSlotEditor(1)">2: --</button>
                    <button class="ams-slot-btn" onclick="openAmsSlotEditor(2)">3: --</button>
                    <button class="ams-slot-btn" onclick="openAmsSlotEditor(3)">4: --</button>
                </div>
            </div>

            <div style="margin-bottom: 15px;">
                <div style="font-size: 12px; color: #666; margin-bottom: 5px;">🧵 External Spool</div>
                <button class="ams-slot-btn external" onclick="openExternalSpoolEditor()" id="external-spool-btn">External: --</button>
            </div>

            <div style="display: flex; gap: 8px; flex-wrap: wrap;">
                <button class="button secondary" onclick="toggleCamera()" id="camera-toggle-btn" style="font-size: 12px;">
                    📷 Camera
                </button>
            </div>
        </div>

        <!-- Temperature Graph -->
        <div class="card full-width">
            <h2>Temperature History</h2>
            <div class="chart-container">
                <canvas id="temp-chart"></canvas>
            </div>
        </div>
    </div>

    <script>
        let chart;
        let tempUnit = 'C'; // Global temperature unit
        let sensorIds = []; // Store sensor IDs for renaming
        let previousPauseState = false; // Track pause state for notifications
        let previousAwaitingPreheat = false; // Track preheat confirmation state
        let isFireAlarmActive = false; // Track fire alarm state to block controls

        /* Anti-Flicker System (Two Layers of Protection)
         *
         * Layer 1: Message Sequence Validation
         *   - Backend assigns incrementing sequence numbers to each WebSocket message
         *   - Frontend drops any message with older sequence number than last received
         *   - Prevents stale queued messages from overriding newer state (permanent protection)
         *
         * Layer 2: Optimistic Update Lock (2-second window)
         *   - When user clicks button, UI updates immediately (optimistic)
         *   - Lock prevents WebSocket from overriding optimistic changes for 2 seconds
         *   - Gives backend time to process action and send fresh data
         *   - Works with Layer 1 to provide instant feedback while preventing flicker
         */
        let lastReceivedSequence = 0;  // Layer 1: Track latest message sequence
        let optimisticUpdateActive = false;  // Layer 2: Temporary lock during user actions
        let optimisticUpdateTimer = null;

        // Temperature conversion functions
        function celsiusToFahrenheit(c) {
            return (c * 9/5) + 32;
        }

        function fahrenheitToCelsius(f) {
            return (f - 32) * 5/9;
        }

        function formatTemp(celsius) {
            if (tempUnit === 'F') {
                return celsiusToFahrenheit(celsius).toFixed(1) + '°F';
            }
            return celsius.toFixed(1) + '°C';
        }

        function getTempInputValue(inputId) {
            const value = parseFloat(document.getElementById(inputId).value);
            if (tempUnit === 'F') {
                return fahrenheitToCelsius(value);
            }
            return value;
        }

        function setTempInputValue(inputId, celsius) {
            const input = document.getElementById(inputId);
            if (tempUnit === 'F') {
                input.value = celsiusToFahrenheit(celsius).toFixed(1);
            } else {
                input.value = celsius.toFixed(1);
            }
        }

        // Settings Modal Functions
        function openSettings() {
            // Load current settings into modal
            const savedTempUnit = localStorage.getItem('tempUnit') || 'C';
            tempUnit = savedTempUnit;
            document.querySelector(`input[name="temp-unit"][value="${savedTempUnit}"]`).checked = true;

            const hysteresis = localStorage.getItem('hysteresis') || '2.0';
            document.getElementById('hysteresis').value = hysteresis;

            const cooldownHours = localStorage.getItem('cooldownHours') || '4';
            document.getElementById('cooldown-hours').value = cooldownHours;

            const cooldownTargetTemp = localStorage.getItem('cooldownTargetTemp') || '21';
            document.getElementById('cooldown-target-temp').value = cooldownTargetTemp;

            const requirePreheatConfirmation = localStorage.getItem('requirePreheatConfirmation') === 'true';
            document.getElementById('require-preheat-confirmation').checked = requirePreheatConfirmation;

            const skipPreheat = localStorage.getItem('skipPreheat') === 'true';
            document.getElementById('skip-preheat').checked = skipPreheat;

            const savedTheme = localStorage.getItem('theme') || 'light';
            document.getElementById('dark-mode-toggle').checked = (savedTheme === 'dark');

            // Populate probe names
            populateProbeNames();

            // Populate printer integration settings
            populatePrinterSettings();

            document.getElementById('settings-modal').style.display = 'block';
        }

        function closeSettings() {
            document.getElementById('settings-modal').style.display = 'none';
        }

        // Close modal if user clicks outside
        window.onclick = function(event) {
            const modal = document.getElementById('settings-modal');
            if (event.target === modal) {
                closeSettings();
            }
        }

        function populateProbeNames() {
            const container = document.getElementById('probe-names-list');
            container.innerHTML = '';

            fetch('/status')
                .then(response => response.json())
                .then(data => {
                    data.sensor_temps.forEach((sensor, index) => {
                        const div = document.createElement('div');
                        div.className = 'probe-rename';
                        div.innerHTML = `
                            <label>${sensor.id.substring(0, 8)}...</label>
                            <input type="text"
                                   id="probe-name-${sensor.id}"
                                   value="${sensor.name}"
                                   placeholder="Probe ${index + 1}">
                        `;
                        container.appendChild(div);

                        // Store sensor ID for later
                        if (!sensorIds.includes(sensor.id)) {
                            sensorIds.push(sensor.id);
                        }
                    });
                });
        }

        async function saveAdvancedSettings() {
            // Save display settings to localStorage
            tempUnit = document.querySelector('input[name="temp-unit"]:checked').value;
            localStorage.setItem('tempUnit', tempUnit);

            const hysteresis = document.getElementById('hysteresis').value;
            localStorage.setItem('hysteresis', hysteresis);

            const cooldownHours = document.getElementById('cooldown-hours').value;
            localStorage.setItem('cooldownHours', cooldownHours);

            const cooldownTargetTemp = document.getElementById('cooldown-target-temp').value;
            localStorage.setItem('cooldownTargetTemp', cooldownTargetTemp);

            const requirePreheatConfirmation = document.getElementById('require-preheat-confirmation').checked;
            localStorage.setItem('requirePreheatConfirmation', requirePreheatConfirmation);

            const skipPreheat = document.getElementById('skip-preheat').checked;
            localStorage.setItem('skipPreheat', skipPreheat);

            // Collect probe names
            const probeNames = {};
            sensorIds.forEach(id => {
                const input = document.getElementById(`probe-name-${id}`);
                if (input) {
                    probeNames[id] = input.value;
                }
            });

            // Save to backend
            try {
                const response = await fetch('/save_advanced_settings', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        hysteresis: parseFloat(hysteresis),
                        cooldown_hours: parseFloat(cooldownHours),
                        cooldown_target_temp: parseFloat(cooldownTargetTemp),
                        require_preheat_confirmation: requirePreheatConfirmation,
                        skip_preheat: skipPreheat,
                        probe_names: probeNames,
                        temp_unit: tempUnit
                    })
                });
                const result = await response.json();
                if (result.success) {
                    showNotification('Settings Saved', 'Advanced settings saved successfully');
                    closeSettings();
                    // Update temperature input labels
                    updateTempLabels();
                    // Reload page to apply changes
                    location.reload();
                }
            } catch (e) {
                console.error('Failed to save advanced settings:', e);
            }
        }

        // Known materials for dropdowns
        const knownMaterials = ['PLA', 'ABS', 'ASA', 'PETG', 'PC', 'TPU', 'NYLON', 'HIPS'];

        function populatePrinterSettings() {
            fetch('/get_settings')
                .then(response => response.json())
                .then(settings => {
                    // Printer connection settings
                    document.getElementById('printer-enabled-toggle').checked = settings.printer_enabled || false;
                    document.getElementById('printer-ip').value = settings.printer_ip || '';
                    document.getElementById('printer-access-code').value = settings.printer_access_code || '';
                    document.getElementById('printer-serial').value = settings.printer_serial || '';
                    document.getElementById('auto-start-toggle').checked = settings.auto_start_enabled !== false;

                    // Material mappings
                    populateMaterialMappings(settings.material_mappings || {});

                    // AMS slot overrides
                    populateAmsSlotOverrides(settings.ams_slot_overrides || {});

                    // External spool
                    populateExternalSpool(settings.external_spool_material || '');
                });
        }

        function populateMaterialMappings(mappings) {
            const container = document.getElementById('material-mappings-list');
            container.innerHTML = '';

            Object.entries(mappings).forEach(([material, config]) => {
                const div = document.createElement('div');
                div.className = 'material-mapping-row';
                div.style.cssText = 'display: flex; gap: 10px; align-items: center; margin-bottom: 8px; padding: 8px; background: var(--card-bg, #f5f5f5); border-radius: 4px;';
                div.innerHTML = `
                    <input type="text" class="material-name" value="${material}" style="width: 70px; font-weight: bold;" placeholder="Material">
                    <label style="font-size: 12px;">Temp:</label>
                    <input type="number" class="material-temp" value="${config.temp}" style="width: 60px;" min="0" max="100">°C
                    <label style="font-size: 12px; margin-left: 10px;">
                        <input type="checkbox" class="material-fans" ${config.fans ? 'checked' : ''}> Fans
                    </label>
                    <button onclick="this.parentElement.remove()" style="margin-left: auto; background: #ff4444; color: white; border: none; padding: 2px 8px; border-radius: 3px; cursor: pointer;">✕</button>
                `;
                container.appendChild(div);
            });
        }

        function addMaterialMapping() {
            const container = document.getElementById('material-mappings-list');
            const div = document.createElement('div');
            div.className = 'material-mapping-row';
            div.style.cssText = 'display: flex; gap: 10px; align-items: center; margin-bottom: 8px; padding: 8px; background: var(--card-bg, #f5f5f5); border-radius: 4px;';
            div.innerHTML = `
                <input type="text" class="material-name" value="" style="width: 70px; font-weight: bold;" placeholder="Material">
                <label style="font-size: 12px;">Temp:</label>
                <input type="number" class="material-temp" value="50" style="width: 60px;" min="0" max="100">°C
                <label style="font-size: 12px; margin-left: 10px;">
                    <input type="checkbox" class="material-fans" checked> Fans
                </label>
                <button onclick="this.parentElement.remove()" style="margin-left: auto; background: #ff4444; color: white; border: none; padding: 2px 8px; border-radius: 3px; cursor: pointer;">✕</button>
            `;
            container.appendChild(div);
        }

        function populateAmsSlotOverrides(overrides) {
            const container = document.getElementById('ams-slot-overrides-list');
            container.innerHTML = '';

            for (let slot = 0; slot < 4; slot++) {
                const currentValue = overrides[String(slot)] || '';
                const div = document.createElement('div');
                div.className = 'settings-row';
                div.style.marginBottom = '8px';

                let options = '<option value="">Auto-detect</option>';
                knownMaterials.forEach(mat => {
                    options += `<option value="${mat}" ${currentValue === mat ? 'selected' : ''}>${mat}</option>`;
                });

                div.innerHTML = `
                    <label>Slot ${slot + 1}</label>
                    <select id="ams-slot-${slot}" style="width: 120px;">
                        ${options}
                    </select>
                `;
                container.appendChild(div);
            }
        }

        function populateExternalSpool(currentMaterial) {
            const select = document.getElementById('external-spool-material');
            select.innerHTML = '<option value="">Auto-detect</option>';

            knownMaterials.forEach(mat => {
                const option = document.createElement('option');
                option.value = mat;
                option.textContent = mat;
                if (currentMaterial === mat) option.selected = true;
                select.appendChild(option);
            });
        }

        function collectMaterialMappings() {
            const mappings = {};
            const rows = document.querySelectorAll('#material-mappings-list .material-mapping-row');
            rows.forEach(row => {
                const name = row.querySelector('.material-name').value.trim().toUpperCase();
                const temp = parseInt(row.querySelector('.material-temp').value) || 0;
                const fans = row.querySelector('.material-fans').checked;
                if (name) {
                    mappings[name] = { temp, fans };
                }
            });
            return mappings;
        }

        function collectAmsSlotOverrides() {
            const overrides = {};
            for (let slot = 0; slot < 4; slot++) {
                const select = document.getElementById(`ams-slot-${slot}`);
                if (select) {
                    overrides[String(slot)] = select.value;
                }
            }
            return overrides;
        }

        async function saveAllSettings() {
            // First save advanced settings (display, temp unit, etc.)
            tempUnit = document.querySelector('input[name="temp-unit"]:checked').value;
            localStorage.setItem('tempUnit', tempUnit);

            const hysteresis = document.getElementById('hysteresis').value;
            localStorage.setItem('hysteresis', hysteresis);

            const cooldownHours = document.getElementById('cooldown-hours').value;
            localStorage.setItem('cooldownHours', cooldownHours);

            const cooldownTargetTemp = document.getElementById('cooldown-target-temp').value;
            localStorage.setItem('cooldownTargetTemp', cooldownTargetTemp);

            const requirePreheatConfirmation = document.getElementById('require-preheat-confirmation').checked;
            localStorage.setItem('requirePreheatConfirmation', requirePreheatConfirmation);

            const skipPreheat = document.getElementById('skip-preheat').checked;
            localStorage.setItem('skipPreheat', skipPreheat);

            // Collect probe names
            const probeNames = {};
            sensorIds.forEach(id => {
                const input = document.getElementById(`probe-name-${id}`);
                if (input) {
                    probeNames[id] = input.value;
                }
            });

            try {
                // Save advanced settings
                await fetch('/save_advanced_settings', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        hysteresis: parseFloat(hysteresis),
                        cooldown_hours: parseFloat(cooldownHours),
                        cooldown_target_temp: parseFloat(cooldownTargetTemp),
                        require_preheat_confirmation: requirePreheatConfirmation,
                        skip_preheat: skipPreheat,
                        probe_names: probeNames,
                        temp_unit: tempUnit
                    })
                });

                // Save printer settings
                const printerSettings = {
                    printer_enabled: document.getElementById('printer-enabled-toggle').checked,
                    printer_ip: document.getElementById('printer-ip').value,
                    printer_access_code: document.getElementById('printer-access-code').value,
                    printer_serial: document.getElementById('printer-serial').value,
                    auto_start_enabled: document.getElementById('auto-start-toggle').checked,
                    material_mappings: collectMaterialMappings(),
                    ams_slot_overrides: collectAmsSlotOverrides(),
                    external_spool_material: document.getElementById('external-spool-material').value
                };

                const response = await fetch('/save_printer_settings', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(printerSettings)
                });

                const result = await response.json();
                if (result.success) {
                    showNotification('Settings Saved', 'All settings saved successfully');
                    closeSettings();
                    updateTempLabels();
                    // Reload to apply printer settings changes
                    location.reload();
                }
            } catch (e) {
                console.error('Failed to save settings:', e);
                showNotification('Error', 'Failed to save settings');
            }
        }

        // ========== PRINTER STATUS CARD FUNCTIONS ==========

        // Store current settings for AMS editor
        let currentAmsSlots = ['', '', '', ''];
        let currentExternalSpool = '';
        let currentTrayNow = -1;  // -1 = unknown, 0-3 = AMS slot, 255 = external (currently loaded)
        let currentTrayTar = -1;  // Target tray for print job (what the print needs)
        let currentMaterialMappings = {};
        let currentAmsOverrides = {};

        function updatePrinterStatusCard(data) {
            // Update connection status
            const connDot = document.querySelector('#printer-connection-status .connection-dot');
            const connText = document.getElementById('printer-connection-text');

            if (data.printer_connected) {
                connDot.className = 'connection-dot connected';
                connText.textContent = 'Connected';
            } else {
                connDot.className = 'connection-dot disconnected';
                connText.textContent = 'Not connected';
            }

            // Update print info
            const fileName = document.getElementById('printer-file-name');
            const progressContainer = document.getElementById('printer-progress-container');
            const materialDisplay = document.getElementById('printer-material-display');
            const timeRemaining = document.getElementById('printer-time-remaining');
            const progressBar = document.getElementById('printer-progress-bar');
            const progressText = document.getElementById('printer-progress-text');

            if (data.printer_phase === 'printing' || data.printer_phase === 'paused') {
                fileName.textContent = data.printer_file || 'Unknown file';
                progressContainer.style.display = 'block';
                materialDisplay.textContent = data.printer_material || '--';
                const minutes = Math.floor((data.printer_time_remaining || 0) / 60);
                const hours = Math.floor(minutes / 60);
                const mins = minutes % 60;
                timeRemaining.textContent = hours > 0 ? `${hours}h ${mins}m left` : `${mins}m left`;
                progressBar.style.width = (data.printer_progress || 0) + '%';
                progressText.textContent = (data.printer_progress || 0) + '%';
            } else {
                fileName.textContent = 'No active print';
                progressContainer.style.display = 'none';
            }

            // Update tray_now (currently loaded slot) and tray_tar (target for print)
            if (data.tray_now !== undefined) {
                currentTrayNow = data.tray_now;
            }
            if (data.tray_tar !== undefined) {
                currentTrayTar = data.tray_tar;
            }

            // Update AMS slots display
            if (data.ams_slots) {
                currentAmsSlots = data.ams_slots;
                updateAmsSlotButtons();
            }

            // Update external spool display
            if (data.external_spool !== undefined) {
                currentExternalSpool = data.external_spool;
                updateExternalSpoolButton();
            }
        }

        // Cache for last known state to prevent unnecessary DOM updates
        let lastAmsState = { slots: [], trayNow: -1, trayTar: -1, overrides: {} };

        function updateAmsSlotButtons() {
            // Check if anything actually changed
            const stateChanged =
                JSON.stringify(currentAmsSlots) !== JSON.stringify(lastAmsState.slots) ||
                currentTrayNow !== lastAmsState.trayNow ||
                currentTrayTar !== lastAmsState.trayTar;

            if (!stateChanged) return; // Skip update if nothing changed

            // Update cached state
            lastAmsState.slots = [...currentAmsSlots];
            lastAmsState.trayNow = currentTrayNow;
            lastAmsState.trayTar = currentTrayTar;

            // Get existing buttons or create new ones
            const container = document.getElementById('ams-slots-display');
            let buttons = container.querySelectorAll('.ams-slot-btn:not(.external)');

            // Create buttons if they don't exist
            if (buttons.length === 0) {
                container.innerHTML = '';
                for (let i = 0; i < 4; i++) {
                    const btn = document.createElement('button');
                    btn.className = 'ams-slot-btn';
                    btn.dataset.slot = i;
                    btn.onclick = () => openAmsSlotEditor(i);
                    container.appendChild(btn);
                }
                buttons = container.querySelectorAll('.ams-slot-btn:not(.external)');
            }

            // Update each button in place
            buttons.forEach((btn, i) => {
                const override = currentAmsOverrides[String(i)] || '';
                const detected = currentAmsSlots[i] || '';
                const display = override || detected || 'Empty';

                btn.textContent = `${i + 1}: ${display}`;

                // Update classes
                btn.classList.remove('has-override', 'loaded', 'target');
                if (override) btn.classList.add('has-override');
                if (currentTrayNow >= 0 && currentTrayNow < 4 && currentTrayNow === i) {
                    btn.classList.add('loaded');
                }
                if (currentTrayTar >= 0 && currentTrayTar < 4 && currentTrayTar === i) {
                    btn.classList.add('target');
                }
            });
        }

        // Fetch overrides once on page load and when settings change
        function refreshAmsOverrides() {
            fetch('/get_settings').then(r => r.json()).then(settings => {
                currentMaterialMappings = settings.material_mappings || {};
                currentAmsOverrides = settings.ams_slot_overrides || {};
                currentAmsOverrides['external'] = settings.external_spool_material || '';
                lastAmsState.overrides = {...currentAmsOverrides};
                // Force redraw of both AMS slots and external spool
                lastAmsState.slots = [];
                lastExternalState.spool = '';
                updateAmsSlotButtons();
                updateExternalSpoolButton();
            });
        }

        let lastExternalState = { spool: '', trayNow: -1, trayTar: -1 };

        function updateExternalSpoolButton() {
            // Check if anything changed
            const stateChanged =
                currentExternalSpool !== lastExternalState.spool ||
                currentTrayNow !== lastExternalState.trayNow ||
                currentTrayTar !== lastExternalState.trayTar;

            if (!stateChanged) return;

            lastExternalState.spool = currentExternalSpool;
            lastExternalState.trayNow = currentTrayNow;
            lastExternalState.trayTar = currentTrayTar;

            const externalOverride = currentAmsOverrides['external'] || '';
            const display = externalOverride || currentExternalSpool || 'Auto';

            const btn = document.getElementById('external-spool-btn');
            btn.textContent = `External: ${display}`;

            // Clear previous states
            btn.classList.remove('has-override', 'loaded', 'target');

            if (externalOverride) {
                btn.classList.add('has-override');
            }

            // Highlight if external spool is currently loaded (green)
            if (currentTrayNow === 255) {
                btn.classList.add('loaded');
            }

            // Highlight if external spool is target for print (blue)
            if (currentTrayTar === 255) {
                btn.classList.add('target');
            }
        }

        // ========== AMS SLOT EDITOR FUNCTIONS ==========

        function openAmsSlotEditor(slotIndex) {
            fetch('/get_settings').then(r => r.json()).then(settings => {
                currentMaterialMappings = settings.material_mappings || {};
                currentAmsOverrides = settings.ams_slot_overrides || {};

                const detected = currentAmsSlots[slotIndex] || 'Unknown';
                const override = currentAmsOverrides[String(slotIndex)] || '';

                document.getElementById('ams-editor-title').textContent = `Edit AMS Slot ${slotIndex + 1}`;
                document.getElementById('ams-auto-detected').textContent = detected || 'Not detected';
                document.getElementById('ams-editor-slot').value = slotIndex;
                document.getElementById('ams-editor-is-external').value = 'false';

                // Populate material dropdown
                const select = document.getElementById('ams-editor-material');
                select.innerHTML = '<option value="">Auto-detect</option>';
                knownMaterials.forEach(mat => {
                    const option = document.createElement('option');
                    option.value = mat;
                    option.textContent = mat;
                    if (override === mat) option.selected = true;
                    select.appendChild(option);
                });

                // Show material settings for current material
                const currentMaterial = override || detected;
                updateAmsEditorMaterialSettings(currentMaterial);

                // Update settings when material changes
                select.onchange = () => {
                    const selected = select.value || detected;
                    updateAmsEditorMaterialSettings(selected);
                };

                document.getElementById('ams-editor-modal').style.display = 'block';
            });
        }

        function openExternalSpoolEditor() {
            fetch('/get_settings').then(r => r.json()).then(settings => {
                currentMaterialMappings = settings.material_mappings || {};
                const override = settings.external_spool_material || '';
                const detected = currentExternalSpool || 'Unknown';

                document.getElementById('ams-editor-title').textContent = 'Edit External Spool';
                document.getElementById('ams-auto-detected').textContent = detected || 'Not detected';
                document.getElementById('ams-editor-slot').value = '-1';
                document.getElementById('ams-editor-is-external').value = 'true';

                // Populate material dropdown
                const select = document.getElementById('ams-editor-material');
                select.innerHTML = '<option value="">Auto-detect</option>';
                knownMaterials.forEach(mat => {
                    const option = document.createElement('option');
                    option.value = mat;
                    option.textContent = mat;
                    if (override === mat) option.selected = true;
                    select.appendChild(option);
                });

                // Show material settings
                const currentMaterial = override || detected;
                updateAmsEditorMaterialSettings(currentMaterial);

                select.onchange = () => {
                    const selected = select.value || detected;
                    updateAmsEditorMaterialSettings(selected);
                };

                document.getElementById('ams-editor-modal').style.display = 'block';
            });
        }

        function updateAmsEditorMaterialSettings(material) {
            const materialUpper = (material || '').toUpperCase();
            const config = currentMaterialMappings[materialUpper] || { temp: 0, fans: false };

            document.getElementById('ams-editor-temp').value = config.temp || 0;
            document.getElementById('ams-editor-fans').checked = config.fans || false;
        }

        function closeAmsEditor() {
            document.getElementById('ams-editor-modal').style.display = 'none';
        }

        async function saveAmsSlot() {
            const isExternal = document.getElementById('ams-editor-is-external').value === 'true';
            const slotIndex = document.getElementById('ams-editor-slot').value;
            const materialOverride = document.getElementById('ams-editor-material').value;
            const temp = parseInt(document.getElementById('ams-editor-temp').value) || 0;
            const fans = document.getElementById('ams-editor-fans').checked;

            // Get current settings
            const settings = await fetch('/get_settings').then(r => r.json());

            // Update material mapping if a material is selected
            const material = materialOverride || (isExternal ? currentExternalSpool : currentAmsSlots[slotIndex]);
            if (material) {
                settings.material_mappings = settings.material_mappings || {};
                settings.material_mappings[material.toUpperCase()] = { temp, fans };
            }

            // Update override
            if (isExternal) {
                settings.external_spool_material = materialOverride;
            } else {
                settings.ams_slot_overrides = settings.ams_slot_overrides || {};
                settings.ams_slot_overrides[slotIndex] = materialOverride;
            }

            // Save settings
            try {
                await fetch('/save_printer_settings', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(settings)
                });

                showNotification('Settings Saved', isExternal ? 'External spool updated' : `AMS Slot ${parseInt(slotIndex) + 1} updated`);
                closeAmsEditor();
                // Refresh overrides from server and update buttons
                refreshAmsOverrides();
            } catch (e) {
                console.error('Failed to save:', e);
                showNotification('Error', 'Failed to save settings');
            }
        }

        // ========== CAMERA PIP FUNCTIONS ==========

        let cameraVisible = false;
        let cameraSize = 'normal'; // normal, large, minimized

        function toggleCamera() {
            const pip = document.getElementById('camera-pip');
            if (cameraVisible) {
                closeCamera();
            } else {
                pip.classList.remove('hidden');
                cameraVisible = true;
                loadCameraFeed();
            }
        }

        function loadCameraFeed() {
            const img = document.getElementById('camera-feed');
            const placeholder = document.getElementById('camera-placeholder');

            placeholder.textContent = 'Loading camera...';
            placeholder.style.display = 'flex';
            img.style.display = 'none';

            // Set camera feed URL
            img.src = '/printer/camera/feed?' + new Date().getTime();
            img.onload = () => {
                placeholder.style.display = 'none';
                img.style.display = 'block';
            };
            img.onerror = () => {
                placeholder.textContent = 'Camera unavailable';
                placeholder.style.display = 'flex';
                img.style.display = 'none';
            };
        }

        function closeCamera() {
            const pip = document.getElementById('camera-pip');
            pip.classList.add('hidden');
            cameraVisible = false;

            // Stop loading camera
            document.getElementById('camera-feed').src = '';
        }

        function minimizeCamera() {
            const pip = document.getElementById('camera-pip');
            if (pip.classList.contains('minimized')) {
                pip.classList.remove('minimized');
                loadCameraFeed();
            } else {
                pip.classList.add('minimized');
                document.getElementById('camera-feed').src = '';
            }
        }

        function resizeCamera() {
            const pip = document.getElementById('camera-pip');
            pip.classList.remove('minimized');
            if (pip.classList.contains('large')) {
                pip.classList.remove('large');
                cameraSize = 'normal';
            } else {
                pip.classList.add('large');
                cameraSize = 'large';
            }
        }

        // Make camera PiP draggable with bounds checking and touch support
        (function() {
            const pip = document.getElementById('camera-pip');
            if (!pip) return;

            const header = pip.querySelector('.camera-pip-header');
            let isDragging = false;
            let offsetX, offsetY;

            function startDrag(clientX, clientY) {
                isDragging = true;
                offsetX = clientX - pip.offsetLeft;
                offsetY = clientY - pip.offsetTop;
                pip.style.cursor = 'grabbing';
            }

            function doDrag(clientX, clientY) {
                if (!isDragging) return;

                // Calculate new position
                let newX = clientX - offsetX;
                let newY = clientY - offsetY;

                // Get window and pip dimensions
                const pipRect = pip.getBoundingClientRect();
                const minVisible = 50; // Minimum visible pixels on each edge

                // Bounds checking - keep at least minVisible pixels on screen
                const maxX = window.innerWidth - minVisible;
                const maxY = window.innerHeight - minVisible;
                const minX = minVisible - pipRect.width;
                const minY = 0; // Don't allow dragging above viewport

                newX = Math.max(minX, Math.min(newX, maxX));
                newY = Math.max(minY, Math.min(newY, maxY));

                pip.style.left = newX + 'px';
                pip.style.top = newY + 'px';
                pip.style.right = 'auto';
                pip.style.bottom = 'auto';
            }

            function endDrag() {
                isDragging = false;
                pip.style.cursor = '';
            }

            // Mouse events
            header.addEventListener('mousedown', (e) => {
                if (e.target.classList.contains('pip-btn')) return;
                startDrag(e.clientX, e.clientY);
            });

            document.addEventListener('mousemove', (e) => {
                doDrag(e.clientX, e.clientY);
            });

            document.addEventListener('mouseup', endDrag);

            // Touch events for mobile
            header.addEventListener('touchstart', (e) => {
                if (e.target.classList.contains('pip-btn')) return;
                e.preventDefault(); // Prevent scrolling while dragging
                const touch = e.touches[0];
                startDrag(touch.clientX, touch.clientY);
            }, { passive: false });

            document.addEventListener('touchmove', (e) => {
                if (!isDragging) return;
                e.preventDefault(); // Prevent scrolling while dragging
                const touch = e.touches[0];
                doDrag(touch.clientX, touch.clientY);
            }, { passive: false });

            document.addEventListener('touchend', endDrag);
            document.addEventListener('touchcancel', endDrag);
        })();

        // Reset PiP position if it gets lost off-screen
        function resetCameraPosition() {
            const pip = document.getElementById('camera-pip');
            if (pip) {
                pip.style.left = 'auto';
                pip.style.top = 'auto';
                pip.style.right = '20px';
                pip.style.bottom = '20px';
            }
        }

        // ========== PRINTER CONTROL FUNCTIONS ==========

        async function printerPause() {
            try {
                const response = await fetch('/printer/pause', { method: 'POST' });
                const result = await response.json();
                if (result.success) {
                    showNotification('Printer', 'Pause command sent');
                }
            } catch (e) {
                console.error('Failed to pause printer:', e);
            }
        }

        async function printerStop() {
            if (!confirm('Are you sure you want to stop the print on the printer?')) return;
            try {
                const response = await fetch('/printer/stop', { method: 'POST' });
                const result = await response.json();
                if (result.success) {
                    showNotification('Printer', 'Stop command sent');
                }
            } catch (e) {
                console.error('Failed to stop printer:', e);
            }
        }

        // Initialize printer card on page load
        document.addEventListener('DOMContentLoaded', () => {
            // Fetch settings and initialize AMS buttons
            refreshAmsOverrides();
        });

        // ========== TEST CONNECTION FUNCTION ==========

        async function testPrinterConnection() {
            const btn = document.getElementById('test-connection-btn');
            const resultDiv = document.getElementById('test-connection-result');

            const ip = document.getElementById('printer-ip').value;
            const accessCode = document.getElementById('printer-access-code').value;
            const serial = document.getElementById('printer-serial').value;

            if (!ip || !accessCode || !serial) {
                resultDiv.style.display = 'block';
                resultDiv.style.color = '#f44336';
                resultDiv.textContent = '⚠️ Please fill in all printer fields';
                return;
            }

            btn.disabled = true;
            btn.textContent = '🔄 Testing...';
            resultDiv.style.display = 'block';
            resultDiv.style.color = '#666';
            resultDiv.textContent = 'Connecting to printer...';

            try {
                const response = await fetch('/test_printer_connection', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ ip, access_code: accessCode, serial })
                });

                const result = await response.json();
                if (result.success) {
                    resultDiv.style.color = '#4CAF50';
                    resultDiv.textContent = '✅ ' + result.message;
                } else {
                    resultDiv.style.color = '#f44336';
                    resultDiv.textContent = '❌ ' + result.message;
                }
            } catch (e) {
                resultDiv.style.color = '#f44336';
                resultDiv.textContent = '❌ Failed to test connection';
            }

            btn.disabled = false;
            btn.textContent = '🔌 Test Connection';
        }

        function changeTempUnit() {
            const newUnit = document.querySelector('input[name="temp-unit"]:checked').value;
            const oldUnit = tempUnit;
            tempUnit = newUnit;

            // Convert displayed values in the main form
            const targetTemp = parseFloat(document.getElementById('target-temp').value);
            if (oldUnit === 'C' && newUnit === 'F') {
                document.getElementById('target-temp').value = celsiusToFahrenheit(targetTemp).toFixed(1);
            } else if (oldUnit === 'F' && newUnit === 'C') {
                document.getElementById('target-temp').value = fahrenheitToCelsius(targetTemp).toFixed(1);
            }

            updateTempLabels();
        }

        function updateTempLabels() {
            const unit = tempUnit === 'F' ? '°F' : '°C';
            const targetLabel = document.getElementById('target-temp-label');
            if (targetLabel) {
                targetLabel.textContent = `Target Temperature (${unit})`;
            }

            // Update cooldown target temp unit in settings modal
            const cooldownTempUnit = document.getElementById('cooldown-temp-unit');
            if (cooldownTempUnit) {
                cooldownTempUnit.textContent = unit;
            }

            // Update chart Y-axis label if chart exists
            if (chart) {
                chart.options.scales.y.title.text = `Temperature (${unit})`;
                chart.update('none');
            }
        }

        // In-page notifications are always enabled (no permission needed)

        // Theme Management
        function toggleTheme() {
            const html = document.documentElement;
            const currentTheme = html.getAttribute('data-theme');
            const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
            html.setAttribute('data-theme', newTheme);
            localStorage.setItem('theme', newTheme);

            // Update chart colors
            if (chart) {
                updateChartTheme();
            }
        }

        // Load saved theme
        const savedTheme = localStorage.getItem('theme') || 'light';
        document.documentElement.setAttribute('data-theme', savedTheme);

        // Initialize Chart
        function initChart() {
            const ctx = document.getElementById('temp-chart').getContext('2d');
            const unit = tempUnit === 'F' ? '°F' : '°C';
            chart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [{
                        label: 'Current Temp',
                        data: [],
                        borderColor: 'rgb(75, 192, 192)',
                        backgroundColor: 'rgba(75, 192, 192, 0.1)',
                        tension: 0.1
                    }, {
                        label: 'Setpoint',
                        data: [],
                        borderColor: 'rgb(255, 99, 132)',
                        backgroundColor: 'rgba(255, 99, 132, 0.1)',
                        borderDash: [5, 5],
                        tension: 0.1
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: {
                            display: true,
                            title: { display: true, text: 'Time' }
                        },
                        y: {
                            display: true,
                            title: { display: true, text: `Temperature (${unit})` }
                        }
                    },
                    plugins: {
                        legend: { display: true, position: 'top' }
                    }
                }
            });
        }

        function updateChartTheme() {
            const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
            const textColor = isDark ? '#e0e0e0' : '#333';
            const gridColor = isDark ? '#404040' : '#e0e0e0';

            chart.options.scales.x.ticks.color = textColor;
            chart.options.scales.y.ticks.color = textColor;
            chart.options.scales.x.grid.color = gridColor;
            chart.options.scales.y.grid.color = gridColor;
            chart.options.plugins.legend.labels.color = textColor;
            chart.update();
        }

        // Load settings from backend
        async function loadSettings() {
            try {
                const response = await fetch('/get_settings');
                const settings = await response.json();

                // Load temperature unit from localStorage or backend
                tempUnit = settings.temp_unit || localStorage.getItem('tempUnit') || 'C';
                localStorage.setItem('tempUnit', tempUnit);

                // Set temperature value with conversion if needed
                setTempInputValue('target-temp', settings.desired_temp);

                document.getElementById('print-hours').value = settings.print_hours;
                document.getElementById('print-minutes').value = settings.print_minutes;
                document.getElementById('fans-enabled').checked = settings.fans_enabled;
                document.getElementById('logging-enabled').checked = settings.logging_enabled;
                document.getElementById('lights-toggle').checked = settings.lights_enabled;

                // Update temperature unit labels
                updateTempLabels();

                loadPresets(settings.presets);
            } catch (e) {
                console.error('Failed to load settings:', e);
            }
        }

        function loadPresets(presets) {
            const list = document.getElementById('presets-list');
            list.innerHTML = '';

            presets.forEach((preset, index) => {
                const div = document.createElement('div');
                div.className = 'preset-item';
                div.innerHTML = `
                    <strong>${preset.name}</strong><br>
                    <small>${preset.temp}°C, ${preset.hours}h ${preset.minutes}m</small>
                `;
                div.onclick = () => loadPreset(index);
                list.appendChild(div);
            });
        }

        async function loadPreset(index) {
            try {
                const response = await fetch('/load_preset', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({index: index})
                });
                const result = await response.json();
                if (result.success) {
                    loadSettings();
                    showNotification('Preset Loaded', result.message);
                }
            } catch (e) {
                console.error('Failed to load preset:', e);
            }
        }

        async function savePreset() {
            const name = document.getElementById('preset-name').value;
            if (!name) {
                alert('Please enter a preset name');
                return;
            }

            const preset = {
                name: name,
                temp: parseFloat(document.getElementById('target-temp').value),
                hours: parseInt(document.getElementById('print-hours').value),
                minutes: parseInt(document.getElementById('print-minutes').value)
            };

            try {
                const response = await fetch('/save_preset', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(preset)
                });
                const result = await response.json();
                if (result.success) {
                    loadSettings();
                    document.getElementById('preset-name').value = '';
                    showNotification('Preset Saved', result.message);
                }
            } catch (e) {
                console.error('Failed to save preset:', e);
            }
        }

        async function saveSettings() {
            const settings = {
                desired_temp: getTempInputValue('target-temp'), // Convert to Celsius if needed
                print_hours: parseInt(document.getElementById('print-hours').value),
                print_minutes: parseInt(document.getElementById('print-minutes').value),
                fans_enabled: document.getElementById('fans-enabled').checked,
                logging_enabled: document.getElementById('logging-enabled').checked,
                lights_enabled: document.getElementById('lights-toggle').checked
            };

            try {
                const response = await fetch('/save_settings', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(settings)
                });
                const result = await response.json();
                if (result.success) {
                    showNotification('Settings Saved', 'Configuration saved successfully');
                }
            } catch (e) {
                console.error('Failed to save settings:', e);
            }
        }

        async function startPrint() {
            console.log(`[CLICK] START clicked - last seq=${lastReceivedSequence}`);

            // Get button references
            const startBtn = document.getElementById('start-btn');
            const pauseBtn = document.getElementById('pause-btn');
            const stopBtn = document.getElementById('stop-btn');

            // Lock optimistic updates with processing indicators
            // START is processing, STOP/PAUSE are blocked from interaction
            lockOptimisticUpdate(startBtn, [stopBtn, pauseBtn]);

            // Optimistic UI update - disable START button immediately for instant feedback

            startBtn.disabled = true;
            pauseBtn.disabled = false;
            stopBtn.disabled = false;

            // Optimistic UI updates for instant feedback
            // Backend will turn these on and confirm via WebSocket within ~1 second
            const fansEnabled = document.getElementById('fans-enabled').checked;
            if (fansEnabled) {
                document.getElementById('fans-ind').className = 'indicator on';
                document.getElementById('fans-toggle').checked = true;
            }

            // Heater will turn on if temp is below target
            const currentTemp = parseFloat(document.getElementById('temp').textContent);
            const targetTemp = parseFloat(document.getElementById('target-temp').value);
            if (!isNaN(currentTemp) && !isNaN(targetTemp) && currentTemp < targetTemp) {
                document.getElementById('heater-ind').className = 'indicator on';
                document.getElementById('heater-toggle').checked = true;
            }

            // Save current settings first
            await saveSettings();

            try {
                const response = await fetch('/start', {method: 'POST'});
                const result = await response.json();
                if (result.success) {
                    showNotification('Print Started', 'Heating chamber to target temperature');
                }
            } catch (e) {
                console.error('Failed to start:', e);
                // Revert optimistic update on error
                startBtn.disabled = false;
                pauseBtn.disabled = true;
                stopBtn.disabled = true;
            }
        }

        async function stopPrint() {
            console.log(`[CLICK] STOP clicked - last seq=${lastReceivedSequence}`);

            // Get button references
            const startBtn = document.getElementById('start-btn');
            const pauseBtn = document.getElementById('pause-btn');
            const stopBtn = document.getElementById('stop-btn');

            // Lock optimistic updates with processing indicators
            // STOP is processing, START/PAUSE are blocked from interaction
            lockOptimisticUpdate(stopBtn, [startBtn, pauseBtn]);

            // Optimistic UI update - enable START, disable STOP/PAUSE immediately

            startBtn.disabled = false;
            pauseBtn.disabled = true;
            stopBtn.disabled = true;

            // Optimistic UI updates - turn off heater and fans immediately for instant feedback
            document.getElementById('heater-ind').className = 'indicator off';
            document.getElementById('heater-toggle').checked = false;
            document.getElementById('fans-ind').className = 'indicator off';
            document.getElementById('fans-toggle').checked = false;

            try {
                const response = await fetch('/stop', {method: 'POST'});
                const result = await response.json();
                if (result.success) {
                    showNotification('Print Stopped', 'Print cycle stopped');
                }
            } catch (e) {
                console.error('Failed to stop:', e);
                // Revert optimistic update on error
                startBtn.disabled = true;
                pauseBtn.disabled = false;
                stopBtn.disabled = false;
            }
        }

        async function pausePrint() {
            // Get button references
            const pauseBtn = document.getElementById('pause-btn');
            const startBtn = document.getElementById('start-btn');
            const stopBtn = document.getElementById('stop-btn');

            // Lock optimistic updates with processing indicators
            // PAUSE is processing, START/STOP are blocked from interaction
            lockOptimisticUpdate(pauseBtn, [startBtn, stopBtn]);

            // Optimistic UI update - toggle pause button appearance immediately
            const wasPaused = pauseBtn.textContent.includes('RESUME');

            if (wasPaused) {
                // Currently paused, switching to resume
                pauseBtn.textContent = '⏸ PAUSE';
                pauseBtn.className = 'button secondary';
                // Show optimistic notification
                showNotification('Print Resumed', 'Timer continuing, temperature control active');
            } else {
                // Currently running, switching to pause
                pauseBtn.textContent = '▶ RESUME';
                pauseBtn.className = 'button primary';
                // Show optimistic notification
                showNotification('Print Paused', 'Timer stopped, temperature control continues');
            }

            try {
                const response = await fetch('/pause', {method: 'POST'});
                const result = await response.json();
                if (result.success) {
                    // Optimistic notification already shown above
                }
            } catch (e) {
                console.error('Failed to pause:', e);
                // Revert optimistic update on error
                if (wasPaused) {
                    pauseBtn.textContent = '▶ RESUME';
                    pauseBtn.className = 'button primary';
                } else {
                    pauseBtn.textContent = '⏸ PAUSE';
                    pauseBtn.className = 'button secondary';
                }
            }
        }

        async function confirmPreheat() {
            try {
                const response = await fetch('/confirm_preheat', {method: 'POST'});
                const result = await response.json();
                if (result.success) {
                    // Hide the preheat modal
                    document.getElementById('preheat-modal').style.display = 'none';
                    showNotification('Print Started', 'Print timer has begun');
                }
            } catch (e) {
                console.error('Failed to confirm preheat:', e);
            }
        }

        async function resumePrint() {
            try {
                const response = await fetch('/resume_print', {method: 'POST'});
                const result = await response.json();
                if (result.success) {
                    // Hide the resume banner
                    document.getElementById('resume-banner').style.display = 'none';
                    showNotification('Resuming Print', 'Print cycle resuming from crash recovery');
                }
            } catch (e) {
                console.error('Failed to resume print:', e);
            }
        }

        async function abortResume() {
            try {
                const response = await fetch('/abort_resume', {method: 'POST'});
                const result = await response.json();
                if (result.success) {
                    // Hide the resume banner
                    document.getElementById('resume-banner').style.display = 'none';
                    showNotification('Resume Aborted', 'Starting fresh - previous print state cleared');
                }
            } catch (e) {
                console.error('Failed to abort resume:', e);
            }
        }

        async function emergencyStop() {
            if (!confirm('Emergency stop will immediately halt heater and fans. Continue?')) {
                return;
            }

            console.log(`[CLICK] EMERGENCY STOP clicked - last seq=${lastReceivedSequence}`);

            // Get button references
            const emergencyStopBtn = document.getElementById('emergency-stop-btn');
            const startBtn = document.getElementById('start-btn');
            const pauseBtn = document.getElementById('pause-btn');
            const stopBtn = document.getElementById('stop-btn');

            // Lock optimistic updates with processing indicators
            // EMERGENCY STOP is processing, all other action buttons are blocked
            lockOptimisticUpdate(emergencyStopBtn, [startBtn, pauseBtn, stopBtn]);

            // Optimistic UI updates - update buttons immediately for instant feedback

            startBtn.disabled = false;
            pauseBtn.disabled = true;
            stopBtn.disabled = true;

            // Optimistic UI updates - turn off heater and fans immediately for instant feedback
            document.getElementById('heater-ind').className = 'indicator off';
            document.getElementById('heater-toggle').checked = false;
            document.getElementById('fans-ind').className = 'indicator off';
            document.getElementById('fans-toggle').checked = false;

            try {
                const response = await fetch('/emergency_stop', {method: 'POST'});
                const result = await response.json();
                if (result.success) {
                    showNotification('Emergency Stop', 'All systems halted', true);
                }
            } catch (e) {
                console.error('Failed to emergency stop:', e);
            }
        }

        async function toggleHeater() {
            // Block toggle during fire alarm
            if (isFireAlarmActive) {
                // Revert toggle to previous state
                const toggle = document.getElementById('heater-toggle');
                toggle.checked = !toggle.checked;
                return;
            }

            const state = document.getElementById('heater-toggle').checked;

            // Optimistic UI update - update indicator immediately
            const indicator = document.getElementById('heater-ind');
            indicator.className = 'indicator ' + (state ? 'on' : 'off');

            try {
                const response = await fetch('/toggle_heater', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({state: state})
                });
            } catch (e) {
                console.error('Failed to toggle heater:', e);
                // Revert on error
                indicator.className = 'indicator ' + (state ? 'off' : 'on');
                document.getElementById('heater-toggle').checked = !state;
            }
        }

        async function toggleFans() {
            // Block toggle during fire alarm
            if (isFireAlarmActive) {
                // Revert toggle to previous state
                const toggle = document.getElementById('fans-toggle');
                toggle.checked = !toggle.checked;
                return;
            }

            const state = document.getElementById('fans-toggle').checked;

            // Optimistic UI update - update indicator immediately
            const indicator = document.getElementById('fans-ind');
            indicator.className = 'indicator ' + (state ? 'on' : 'off');

            try {
                const response = await fetch('/toggle_fans', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({state: state})
                });
            } catch (e) {
                console.error('Failed to toggle fans:', e);
                // Revert on error
                indicator.className = 'indicator ' + (state ? 'off' : 'on');
                document.getElementById('fans-toggle').checked = !state;
            }
        }

        async function toggleLights() {
            // Block toggle during fire alarm
            if (isFireAlarmActive) {
                // Revert toggle to previous state
                const toggle = document.getElementById('lights-toggle');
                toggle.checked = !toggle.checked;
                return;
            }

            const state = document.getElementById('lights-toggle').checked;

            // Optimistic UI update - update indicator immediately
            const indicator = document.getElementById('lights-ind');
            indicator.className = 'indicator ' + (state ? 'on' : 'off');

            try {
                const response = await fetch('/toggle_lights', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({state: state})
                });
            } catch (e) {
                console.error('Failed to toggle lights:', e);
                // Revert on error
                indicator.className = 'indicator ' + (state ? 'off' : 'on');
                document.getElementById('lights-toggle').checked = !state;
            }
        }

        async function adjustTime(minutes) {
            try {
                const response = await fetch('/adjust_time', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({minutes: minutes})
                });
                const result = await response.json();
                if (result.success) {
                    showNotification('Time Adjusted', result.message);
                }
            } catch (e) {
                console.error('Failed to adjust time:', e);
            }
        }

        async function resetFire() {
            try {
                const response = await fetch('/reset', {method: 'POST'});
                const result = await response.json();
                alert(result.message);
            } catch (e) {
                console.error('Failed to reset:', e);
            }
        }

        async function downloadLog() {
            window.open('/download_log', '_blank');
        }

        let notificationTimeout = null;

        function showNotification(title, body, isUrgent = false) {
            // Clear any existing notification timeout
            if (notificationTimeout) {
                clearTimeout(notificationTimeout);
            }

            // Update notification content
            document.getElementById('notification-title').textContent = title;
            document.getElementById('notification-body').textContent = body;

            // Show notification and overlay
            const toast = document.getElementById('notification-toast');
            const overlay = document.getElementById('notification-overlay');

            toast.classList.remove('hiding');
            toast.style.display = 'block';
            overlay.style.display = 'block';

            // Auto-dismiss after delay (longer for urgent notifications)
            const dismissDelay = isUrgent ? 5000 : 3000;
            notificationTimeout = setTimeout(() => {
                dismissNotification();
            }, dismissDelay);
        }

        function dismissNotification() {
            const toast = document.getElementById('notification-toast');
            const overlay = document.getElementById('notification-overlay');

            // Add hiding animation
            toast.classList.add('hiding');

            // Hide after animation completes
            setTimeout(() => {
                toast.style.display = 'none';
                overlay.style.display = 'none';
                toast.classList.remove('hiding');
            }, 300); // Match animation duration

            // Clear timeout
            if (notificationTimeout) {
                clearTimeout(notificationTimeout);
                notificationTimeout = null;
            }
        }

        function formatTime(seconds) {
            if (seconds <= 0) return '--';
            const hours = Math.floor(seconds / 3600);
            const minutes = Math.floor((seconds % 3600) / 60);
            const secs = Math.floor(seconds % 60);

            if (hours > 0) {
                return `${hours}h ${minutes}m`;
            } else if (minutes > 0) {
                return `${minutes}m ${secs}s`;
            } else {
                return `${secs}s`;
            }
        }

        // Enable optimistic update lock to prevent WebSocket from overriding for 6 seconds
        // Also manages visual processing indicators and conflict prevention
        function clearAllProcessingStates() {
            // Clear processing indicators from all buttons
            const allButtons = [
                document.getElementById('start-btn'),
                document.getElementById('pause-btn'),
                document.getElementById('stop-btn'),
                document.getElementById('emergency-stop-btn')
            ];
            allButtons.forEach(btn => {
                if (btn) {
                    btn.classList.remove('processing');
                    btn.classList.remove('processing-blocked');
                }
            });
        }

        function lockOptimisticUpdate(activeButton, blockedButtons = []) {
            const lockTime = Date.now();
            optimisticUpdateActive = true;
            console.log(`[LOCK] Optimistic lock ENABLED at +0ms for ${activeButton.id}`);

            // Clear existing timer if any
            if (optimisticUpdateTimer) {
                clearTimeout(optimisticUpdateTimer);
            }

            // Clear all existing processing states before applying new ones
            clearAllProcessingStates();

            // Add processing spinner to the clicked button
            activeButton.classList.add('processing');

            // Block conflicting buttons from being clicked
            blockedButtons.forEach(btn => {
                if (btn && !btn.disabled) {
                    btn.classList.add('processing-blocked');
                }
            });

            // Auto-clear after 6 seconds (increased from 2s to ensure backend has processed)
            optimisticUpdateTimer = setTimeout(() => {
                const elapsed = Date.now() - lockTime;
                optimisticUpdateActive = false;
                optimisticUpdateTimer = null;

                // Clear all processing indicators
                clearAllProcessingStates();

                console.log(`[LOCK] Optimistic lock EXPIRED at +${elapsed}ms`);
            }, 6000);
        }

        function updateStatus() {
            fetch('/status')
                .then(response => response.json())
                .then(data => {
                    // Update displays with proper temperature formatting
                    document.getElementById('temp').textContent = formatTemp(data.current_temp);
                    document.getElementById('setpoint').textContent = formatTemp(data.setpoint);
                    document.getElementById('phase').textContent = data.phase.toUpperCase();
                    document.getElementById('eta').textContent = data.eta_to_target > 0 ?
                        formatTime(data.eta_to_target) : '--';

                    document.getElementById('print-time').textContent = formatTime(data.print_time_remaining);
                    document.getElementById('cooldown-time').textContent = formatTime(data.cooldown_time_remaining);

                    // Update indicators
                    document.getElementById('heater-ind').className = 'indicator ' + (data.heater_on ? 'on' : 'off');
                    document.getElementById('fans-ind').className = 'indicator ' + (data.fans_on ? 'on' : 'off');
                    document.getElementById('lights-ind').className = 'indicator ' + (data.lights_on ? 'on' : 'off');

                    // Update toggle states
                    document.getElementById('heater-toggle').checked = data.heater_on;
                    document.getElementById('fans-toggle').checked = data.fans_on;
                    document.getElementById('lights-toggle').checked = data.lights_on;

                    // Update status text
                    document.getElementById('heater-status').textContent = data.heater_manual ? '(Manual)' : '(Auto)';
                    document.getElementById('fans-status').textContent = data.fans_manual ? '(Manual)' : '(Auto)';

                    // Update buttons
                    const startBtn = document.getElementById('start-btn');
                    const pauseBtn = document.getElementById('pause-btn');
                    const stopBtn = document.getElementById('stop-btn');

                    if (data.print_active) {
                        startBtn.disabled = true;
                        pauseBtn.disabled = false;
                        stopBtn.disabled = false;

                        // Update pause button text based on state
                        if (data.print_paused) {
                            pauseBtn.textContent = '▶ RESUME';
                            pauseBtn.className = 'button primary';
                        } else {
                            pauseBtn.textContent = '⏸ PAUSE';
                            pauseBtn.className = 'button secondary';
                        }

                        // Show notification when pause state changes
                        if (data.print_paused !== previousPauseState) {
                            if (data.print_paused) {
                                showNotification('Print Paused', 'Timer stopped - temperature control active');
                            } else if (previousPauseState) {
                                showNotification('Print Resumed', 'Timer continuing');
                            }
                            previousPauseState = data.print_paused;
                        }
                    } else {
                        startBtn.disabled = false;
                        pauseBtn.disabled = true;
                        stopBtn.disabled = true;
                        pauseBtn.textContent = '⏸ PAUSE';
                        pauseBtn.className = 'button secondary';
                        previousPauseState = false; // Reset when not active
                    }

                    // Fire alert
                    const alert = document.getElementById('fire-alert');
                    const resetBtn = document.getElementById('reset-btn');
                    const emergencyStopBtn = document.getElementById('emergency-stop-btn');
                    const heaterToggle = document.getElementById('heater-toggle');
                    const fansToggle = document.getElementById('fans-toggle');
                    const lightsToggle = document.getElementById('lights-toggle');
                    const settingsBtn = document.getElementById('settings-btn');

                    if (data.emergency_stop) {
                        isFireAlarmActive = true; // Set global flag
                        alert.style.display = 'block';
                        resetBtn.disabled = false;

                        // Disable ALL controls except reset button during fire alarm
                        startBtn.disabled = true;
                        pauseBtn.disabled = true;
                        stopBtn.disabled = true;
                        emergencyStopBtn.disabled = true;
                        settingsBtn.disabled = true;
                        heaterToggle.disabled = true;
                        fansToggle.disabled = true;
                        lightsToggle.disabled = true;

                        // Disable configuration inputs
                        document.getElementById('target-temp').disabled = true;
                        document.getElementById('print-hours').disabled = true;
                        document.getElementById('print-minutes').disabled = true;
                        document.getElementById('fans-enabled').disabled = true;
                        document.getElementById('logging-enabled').disabled = true;

                        // Disable time adjustment buttons
                        const timeAdjustButtons = document.querySelectorAll('.time-adjust button');
                        timeAdjustButtons.forEach(btn => {
                            btn.disabled = true;
                            btn.style.opacity = '0.4';
                        });

                        // Disable preset buttons
                        const presetItems = document.querySelectorAll('.preset-item');
                        presetItems.forEach(item => {
                            item.style.pointerEvents = 'none';
                            item.style.opacity = '0.4';
                        });

                        // Add visual indication that controls are locked
                        startBtn.style.opacity = '0.4';
                        pauseBtn.style.opacity = '0.4';
                        stopBtn.style.opacity = '0.4';
                        emergencyStopBtn.style.opacity = '0.4';
                    } else {
                        isFireAlarmActive = false; // Clear global flag
                        alert.style.display = 'none';
                        resetBtn.disabled = true;

                        // Re-enable controls when fire alarm is cleared
                        emergencyStopBtn.disabled = false;
                        settingsBtn.disabled = false;
                        heaterToggle.disabled = false;
                        fansToggle.disabled = false;
                        lightsToggle.disabled = false;

                        // Re-enable configuration inputs
                        document.getElementById('target-temp').disabled = false;
                        document.getElementById('print-hours').disabled = false;
                        document.getElementById('print-minutes').disabled = false;
                        document.getElementById('fans-enabled').disabled = false;
                        document.getElementById('logging-enabled').disabled = false;

                        // Re-enable time adjustment buttons
                        const timeAdjustButtons = document.querySelectorAll('.time-adjust button');
                        timeAdjustButtons.forEach(btn => {
                            btn.disabled = false;
                            btn.style.opacity = '1';
                        });

                        // Re-enable preset buttons
                        const presetItems = document.querySelectorAll('.preset-item');
                        presetItems.forEach(item => {
                            item.style.pointerEvents = 'auto';
                            item.style.opacity = '1';
                        });

                        // Restore normal opacity
                        startBtn.style.opacity = '1';
                        pauseBtn.style.opacity = '1';
                        stopBtn.style.opacity = '1';
                        emergencyStopBtn.style.opacity = '1';

                        // START, PAUSE, STOP buttons already handled by print_active logic above
                    }

                    // Update sensor list with proper temperature formatting
                    const sensorList = document.getElementById('sensor-list');
                    sensorList.innerHTML = '';
                    data.sensor_temps.forEach(sensor => {
                        const li = document.createElement('li');
                        const tempText = sensor.temp !== null ?
                            formatTemp(sensor.temp) : 'ERROR';
                        li.innerHTML = `<span>${sensor.name}</span><span>${tempText}</span>`;
                        sensorList.appendChild(li);
                    });

                    // Handle preheat confirmation modal
                    const preheatModal = document.getElementById('preheat-modal');
                    if (data.awaiting_preheat_confirmation) {
                        preheatModal.style.display = 'block';

                        // Show notification when first entering preheat confirmation state
                        if (!previousAwaitingPreheat) {
                            showNotification('Target Temperature Reached!', 'Chamber is ready - confirm to start print timer');
                            previousAwaitingPreheat = true;
                        }
                    } else {
                        preheatModal.style.display = 'none';
                        if (previousAwaitingPreheat) {
                            previousAwaitingPreheat = false;
                        }
                    }
                });
        }

        function updateHistory() {
            fetch('/history')
                .then(response => response.json())
                .then(data => {
                    if (data.length === 0) return;

                    const labels = data.map(d => {
                        const date = new Date(d.time * 1000);
                        return date.toLocaleTimeString();
                    });

                    const temps = data.map(d => d.temp);
                    const setpoints = data.map(d => d.setpoint);

                    chart.data.labels = labels;
                    chart.data.datasets[0].data = temps;
                    chart.data.datasets[1].data = setpoints;
                    chart.update('none'); // Update without animation
                });
        }

        // Check for resume banner immediately on page load
        async function checkResumeBanner() {
            try {
                const response = await fetch('/status');
                const data = await response.json();
                const resumeBanner = document.getElementById('resume-banner');
                if (data.pending_resume) {
                    resumeBanner.style.display = 'block';
                } else {
                    resumeBanner.style.display = 'none';
                }
            } catch (e) {
                console.error('Failed to check resume status:', e);
            }
        }

        // Initialize
        // Load temperature unit first before initializing chart
        tempUnit = localStorage.getItem('tempUnit') || 'C';

        initChart();
        loadSettings();
        checkResumeBanner(); // Show resume banner immediately if needed

        // Initialize Socket.IO connection for real-time updates
        const socket = io();

        // Connection status handlers
        socket.on('connect', () => {
            console.log('WebSocket connected - real-time updates active');
            // Initial data fetch on connection
            updateStatus();
            updateHistory();
        });

        socket.on('disconnect', () => {
            console.log('WebSocket disconnected - attempting to reconnect...');
        });

        // Real-time status updates via WebSocket
        socket.on('status_update', (data) => {
            // Validate message sequence - ignore stale messages
            if (data.sequence <= lastReceivedSequence) {
                console.log(`[WS] DROPPED stale message: seq=${data.sequence}, last=${lastReceivedSequence}, heater=${data.heater_on}, fans=${data.fans_on}`);
                return; // Drop this stale message
            }
            console.log(`[WS] ACCEPTED message: seq=${data.sequence}, heater=${data.heater_on}, fans=${data.fans_on}, lock=${optimisticUpdateActive}`);
            lastReceivedSequence = data.sequence;

            // Get button references at top of handler (needed by multiple sections below)
            const startBtn = document.getElementById('start-btn');
            const pauseBtn = document.getElementById('pause-btn');
            const stopBtn = document.getElementById('stop-btn');
            const emergencyStopBtn = document.getElementById('emergency-stop-btn');

            // Always update temperature displays (not affected by optimistic lock)
            document.getElementById('temp').textContent = formatTemp(data.current_temp);
            document.getElementById('setpoint').textContent = formatTemp(data.setpoint);
            document.getElementById('phase').textContent = data.phase.toUpperCase();
            document.getElementById('eta').textContent = data.eta_to_target > 0 ?
                formatTime(data.eta_to_target) : '--';

            document.getElementById('print-time').textContent = formatTime(data.print_time_remaining);
            document.getElementById('cooldown-time').textContent = formatTime(data.cooldown_time_remaining);

            // Skip button/toggle updates if optimistic update is active (prevents flickering)
            if (!optimisticUpdateActive) {
                console.log(`[WS] Updating indicators: heater=${data.heater_on}, fans=${data.fans_on} (lock=${optimisticUpdateActive})`);
                // Update indicators
                document.getElementById('heater-ind').className = 'indicator ' + (data.heater_on ? 'on' : 'off');
                document.getElementById('fans-ind').className = 'indicator ' + (data.fans_on ? 'on' : 'off');
                document.getElementById('lights-ind').className = 'indicator ' + (data.lights_on ? 'on' : 'off');

                // Update toggle states
                document.getElementById('heater-toggle').checked = data.heater_on;
                document.getElementById('fans-toggle').checked = data.fans_on;
                document.getElementById('lights-toggle').checked = data.lights_on;
            } else {
                console.log(`[WS] SKIPPED indicator update due to optimistic lock (heater=${data.heater_on}, fans=${data.fans_on})`);
            }

            // Update status text
            document.getElementById('heater-status').textContent = data.heater_manual ? '(Manual)' : '(Auto)';
            document.getElementById('fans-status').textContent = data.fans_manual ? '(Manual)' : '(Auto)';

            // Skip button updates if optimistic update is active (prevents flickering)
            if (!optimisticUpdateActive) {
                // Update button states based on print status

                if (data.print_active) {
                    startBtn.disabled = true;
                    pauseBtn.disabled = false;
                    stopBtn.disabled = false;

                    // Update pause button text based on state
                    if (data.print_paused) {
                        pauseBtn.textContent = '▶ RESUME';
                        pauseBtn.className = 'button primary';
                    } else {
                        pauseBtn.textContent = '⏸ PAUSE';
                        pauseBtn.className = 'button secondary';
                    }

                    // Show notification when pause state changes
                    if (data.print_paused !== previousPauseState) {
                        if (data.print_paused) {
                            showNotification('Print Paused', 'Timer stopped - temperature control active');
                        } else if (previousPauseState) {
                            showNotification('Print Resumed', 'Timer continuing');
                        }
                        previousPauseState = data.print_paused;
                    }
                } else {
                    startBtn.disabled = false;
                    pauseBtn.disabled = true;
                    stopBtn.disabled = true;
                    pauseBtn.textContent = '⏸ PAUSE';
                    pauseBtn.className = 'button secondary';
                    previousPauseState = false; // Reset when not active
                }
            }

            // Fire alert
            const alert = document.getElementById('fire-alert');
            const resetBtn = document.getElementById('reset-btn');
            const heaterToggle = document.getElementById('heater-toggle');
            const fansToggle = document.getElementById('fans-toggle');
            const lightsToggle = document.getElementById('lights-toggle');
            const settingsBtn = document.getElementById('settings-btn');

            if (data.emergency_stop) {
                isFireAlarmActive = true; // Set global flag
                alert.style.display = 'block';
                resetBtn.disabled = false;

                // Disable ALL controls except reset button during fire alarm
                startBtn.disabled = true;
                pauseBtn.disabled = true;
                stopBtn.disabled = true;
                emergencyStopBtn.disabled = true;
                settingsBtn.disabled = true;
                heaterToggle.disabled = true;
                fansToggle.disabled = true;
                lightsToggle.disabled = true;

                // Disable configuration inputs
                document.getElementById('target-temp').disabled = true;
                document.getElementById('print-hours').disabled = true;
                document.getElementById('print-minutes').disabled = true;
                document.getElementById('fans-enabled').disabled = true;
                document.getElementById('logging-enabled').disabled = true;

                // Disable time adjustment buttons
                const timeAdjustButtons = document.querySelectorAll('.time-adjust button');
                timeAdjustButtons.forEach(btn => {
                    btn.disabled = true;
                    btn.style.opacity = '0.4';
                });

                // Disable preset buttons
                const presetItems = document.querySelectorAll('.preset-item');
                presetItems.forEach(item => {
                    item.style.pointerEvents = 'none';
                    item.style.opacity = '0.4';
                });

                // Add visual indication that controls are locked
                startBtn.style.opacity = '0.4';
                pauseBtn.style.opacity = '0.4';
                stopBtn.style.opacity = '0.4';
                emergencyStopBtn.style.opacity = '0.4';
            } else {
                isFireAlarmActive = false; // Clear global flag
                alert.style.display = 'none';
                resetBtn.disabled = true;

                // Re-enable controls when fire alarm is cleared
                emergencyStopBtn.disabled = false;
                settingsBtn.disabled = false;
                heaterToggle.disabled = false;
                fansToggle.disabled = false;
                lightsToggle.disabled = false;

                // Re-enable configuration inputs
                document.getElementById('target-temp').disabled = false;
                document.getElementById('print-hours').disabled = false;
                document.getElementById('print-minutes').disabled = false;
                document.getElementById('fans-enabled').disabled = false;
                document.getElementById('logging-enabled').disabled = false;

                // Re-enable time adjustment buttons
                const timeAdjustButtons = document.querySelectorAll('.time-adjust button');
                timeAdjustButtons.forEach(btn => {
                    btn.disabled = false;
                    btn.style.opacity = '1';
                });

                // Re-enable preset buttons
                const presetItems = document.querySelectorAll('.preset-item');
                presetItems.forEach(item => {
                    item.style.pointerEvents = 'auto';
                    item.style.opacity = '1';
                });

                // Restore normal opacity
                startBtn.style.opacity = '1';
                pauseBtn.style.opacity = '1';
                stopBtn.style.opacity = '1';
                emergencyStopBtn.style.opacity = '1';

                // START, PAUSE, STOP buttons already handled by print_active logic above
            }

            // Update sensor list with proper temperature formatting
            const sensorList = document.getElementById('sensor-list');
            sensorList.innerHTML = '';
            data.sensor_temps.forEach(sensor => {
                const li = document.createElement('li');
                const tempText = sensor.temp !== null ?
                    formatTemp(sensor.temp) : 'ERROR';
                li.innerHTML = `<span>${sensor.name}</span><span>${tempText}</span>`;
                sensorList.appendChild(li);
            });

            // Handle preheat confirmation modal
            const preheatModal = document.getElementById('preheat-modal');
            if (data.awaiting_preheat_confirmation) {
                preheatModal.style.display = 'block';

                // Show notification when first entering preheat confirmation state
                if (!previousAwaitingPreheat) {
                    showNotification('Target Temperature Reached!', 'Chamber is ready - confirm to start print timer');
                    previousAwaitingPreheat = true;
                }
            } else {
                preheatModal.style.display = 'none';
                if (previousAwaitingPreheat) {
                    previousAwaitingPreheat = false;
                }
            }

            // Handle resume print banner (crash recovery)
            const resumeBanner = document.getElementById('resume-banner');
            if (data.pending_resume) {
                resumeBanner.style.display = 'block';
            } else {
                resumeBanner.style.display = 'none';
            }

            // Update Printer Status Card
            updatePrinterStatusCard(data);
        });

        // Real-time notification events
        socket.on('notification', (data) => {
            showNotification(data.title, data.message);
        });

        // Backend-initiated processing lock (e.g., printer triggered a stop)
        socket.on('processing_lock', (data) => {
            console.log(`[BACKEND] Processing lock received for action: ${data.action}`);
            const startBtn = document.getElementById('start-btn');
            const pauseBtn = document.getElementById('pause-btn');
            const stopBtn = document.getElementById('stop-btn');
            const emergencyStopBtn = document.getElementById('emergency-stop-btn');

            // Lock UI based on the action
            if (data.action === 'stop') {
                // Same as user clicking STOP - lock stop button, block others
                lockOptimisticUpdate(stopBtn, [startBtn, pauseBtn, emergencyStopBtn]);
            } else if (data.action === 'start') {
                lockOptimisticUpdate(startBtn, [stopBtn, pauseBtn, emergencyStopBtn]);
            }
        });

        // Update temperature graph every 5 seconds (WebSocket doesn't handle history data)
        setInterval(updateHistory, 5000);

        // Fallback: Poll /status every 10 seconds in case WebSocket disconnects
        setInterval(() => {
            if (!socket.connected) {
                console.log('WebSocket disconnected - using fallback polling');
                updateStatus();
            }
        }, 10000);
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/status')
def status():
    return jsonify(status_data)

@app.route('/history')
def history():
    with history_lock:
        # Return last 100 points for graphing
        recent = temp_history[-100:] if len(temp_history) > 100 else temp_history
        return jsonify(recent)

@app.route('/get_settings')
def get_settings():
    return jsonify(current_settings)

@app.route('/save_settings', methods=['POST'])
def save_settings_route():
    global current_settings, logging_enabled

    data = request.json
    current_settings.update(data)
    save_settings(current_settings)

    # Update logging state
    logging_enabled = data.get('logging_enabled', False)

    return jsonify({'success': True, 'message': 'Settings saved'})

@app.route('/save_advanced_settings', methods=['POST'])
def save_advanced_settings():
    global current_settings, probe_locations

    data = request.json

    # Update settings
    if 'hysteresis' in data:
        current_settings['hysteresis'] = data['hysteresis']
    if 'cooldown_hours' in data:
        current_settings['cooldown_hours'] = data['cooldown_hours']
    if 'cooldown_target_temp' in data:
        current_settings['cooldown_target_temp'] = data['cooldown_target_temp']
    if 'temp_unit' in data:
        current_settings['temp_unit'] = data['temp_unit']
    if 'require_preheat_confirmation' in data:
        current_settings['require_preheat_confirmation'] = data['require_preheat_confirmation']
    if 'skip_preheat' in data:
        current_settings['skip_preheat'] = data['skip_preheat']
    if 'probe_names' in data:
        current_settings['probe_names'] = data['probe_names']
        # Update probe locations immediately
        for sensor_id, custom_name in data['probe_names'].items():
            if custom_name:
                probe_locations[sensor_id] = custom_name

    save_settings(current_settings)

    # Update probe names in existing sensor data without re-reading temperatures
    # This makes probe name changes appear instantly in UI instead of waiting 1-5 seconds
    if 'probe_names' in data:
        with state_lock:
            # Update names in existing sensor_temps without blocking on sensor reads
            for sensor in status_data['sensor_temps']:
                sensor_id = sensor['id']
                # Update name from probe_locations (which was already updated above)
                sensor['name'] = probe_locations.get(sensor_id, sensor_id)
        emit_status_update()  # Immediate WebSocket update for instant UI refresh

    return jsonify({'success': True, 'message': 'Advanced settings saved'})

@app.route('/save_printer_settings', methods=['POST'])
def save_printer_settings():
    """Save printer integration and material settings"""
    global current_settings

    data = request.json

    # Update printer connection settings
    if 'printer_enabled' in data:
        current_settings['printer_enabled'] = data['printer_enabled']
    if 'printer_ip' in data:
        current_settings['printer_ip'] = data['printer_ip']
    if 'printer_access_code' in data:
        current_settings['printer_access_code'] = data['printer_access_code']
    if 'printer_serial' in data:
        current_settings['printer_serial'] = data['printer_serial']
    if 'auto_start_enabled' in data:
        current_settings['auto_start_enabled'] = data['auto_start_enabled']

    # Update material mappings
    if 'material_mappings' in data:
        current_settings['material_mappings'] = data['material_mappings']

    # Update AMS slot overrides
    if 'ams_slot_overrides' in data:
        current_settings['ams_slot_overrides'] = data['ams_slot_overrides']

    # Update external spool material
    if 'external_spool_material' in data:
        current_settings['external_spool_material'] = data['external_spool_material']

    save_settings(current_settings)

    return jsonify({'success': True, 'message': 'Printer settings saved'})

@app.route('/test_printer_connection', methods=['POST'])
def test_printer_connection():
    """Test printer MQTT connection with provided credentials"""
    import socket
    import ssl

    data = request.json
    ip = data.get('ip', '')
    access_code = data.get('access_code', '')
    serial = data.get('serial', '')

    if not ip or not access_code or not serial:
        return jsonify({'success': False, 'message': 'Missing required fields'})

    # Test basic TCP connectivity first
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((ip, 8883))
        sock.close()

        if result != 0:
            return jsonify({'success': False, 'message': f'Cannot reach printer at {ip}:8883'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Network error: {str(e)}'})

    # Test MQTT connection
    try:
        import paho.mqtt.client as mqtt
        connected = {'status': None}

        def on_connect(client, userdata, flags, rc, *args):
            if rc == 0:
                connected['status'] = 'success'
            elif rc == 5:
                connected['status'] = 'auth_failed'
            else:
                connected['status'] = f'error_{rc}'

        # Create temporary client for testing
        try:
            # Try paho-mqtt 2.0+ API with VERSION2 (recommended)
            test_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"test_{int(time.time())}")
        except (AttributeError, TypeError):
            # Fall back to paho-mqtt 1.x API
            test_client = mqtt.Client(client_id=f"test_{int(time.time())}")

        test_client.username_pw_set("bblp", access_code)
        test_client.tls_set(cert_reqs=ssl.CERT_NONE)
        test_client.tls_insecure_set(True)
        test_client.on_connect = on_connect

        test_client.connect(ip, 8883, keepalive=10)
        test_client.loop_start()

        # Wait for connection result
        timeout = 5
        while connected['status'] is None and timeout > 0:
            time.sleep(0.5)
            timeout -= 0.5

        test_client.loop_stop()
        test_client.disconnect()

        if connected['status'] == 'success':
            return jsonify({'success': True, 'message': 'Connection successful!'})
        elif connected['status'] == 'auth_failed':
            return jsonify({'success': False, 'message': 'Authentication failed - check access code'})
        elif connected['status'] is None:
            return jsonify({'success': False, 'message': 'Connection timeout'})
        else:
            return jsonify({'success': False, 'message': f'Connection failed: {connected["status"]}'})

    except Exception as e:
        return jsonify({'success': False, 'message': f'MQTT error: {str(e)}'})

@app.route('/save_preset', methods=['POST'])
def save_preset():
    preset = request.json

    if 'presets' not in current_settings:
        current_settings['presets'] = []

    current_settings['presets'].append(preset)
    save_settings(current_settings)

    return jsonify({'success': True, 'message': f"Preset '{preset['name']}' saved"})

@app.route('/load_preset', methods=['POST'])
def load_preset():
    index = request.json['index']

    if 'presets' in current_settings and 0 <= index < len(current_settings['presets']):
        preset = current_settings['presets'][index]
        current_settings['desired_temp'] = preset['temp']
        current_settings['print_hours'] = preset['hours']
        current_settings['print_minutes'] = preset['minutes']
        save_settings(current_settings)

        return jsonify({'success': True, 'message': f"Loaded preset '{preset['name']}'"})

    return jsonify({'success': False, 'message': 'Preset not found'})

@app.route('/start', methods=['POST'])
def start():
    global start_requested, logging_enabled

    if not print_active:
        with state_lock:
            start_requested = True
            logging_enabled = current_settings.get('logging_enabled', False)
        return jsonify({'success': True, 'message': 'Print started'})

    return jsonify({'success': False, 'message': 'Print already active'})

@app.route('/stop', methods=['POST'])
def stop():
    global stop_requested, additional_seconds, printer_mqtt_client, printer_connected, mqtt_sequence_id

    if print_active:
        with state_lock:
            stop_requested = True
            additional_seconds = 0  # Reset time adjustments when stopping

        # Also stop the printer if connected
        if printer_connected and printer_mqtt_client:
            try:
                serial = current_settings.get('printer_serial', '')
                topic = f"device/{serial}/request"
                mqtt_sequence_id += 1
                command = {
                    "print": {
                        "sequence_id": str(mqtt_sequence_id),
                        "command": "stop"
                    }
                }
                printer_mqtt_client.publish(topic, json.dumps(command), qos=1)
                print(f"🛑 Sent STOP command to printer")
                return jsonify({'success': True, 'message': 'Print stopped - heater and printer'})
            except Exception as e:
                print(f"Error stopping printer: {e}")
                return jsonify({'success': True, 'message': 'Heater stopped (printer stop failed)'})

        return jsonify({'success': True, 'message': 'Print stopped'})

    return jsonify({'success': False, 'message': 'No print active'})

@app.route('/pause', methods=['POST'])
def pause():
    global pause_requested, printer_mqtt_client, printer_connected, mqtt_sequence_id

    if print_active:
        # Determine if we're pausing or resuming
        is_pausing = not print_paused

        with state_lock:
            pause_requested = True

        # Also pause/resume the printer if connected
        if printer_connected and printer_mqtt_client:
            try:
                serial = current_settings.get('printer_serial', '')
                topic = f"device/{serial}/request"
                mqtt_sequence_id += 1
                command = {
                    "print": {
                        "sequence_id": str(mqtt_sequence_id),
                        "command": "pause" if is_pausing else "resume"
                    }
                }
                printer_mqtt_client.publish(topic, json.dumps(command), qos=1)
                action = "PAUSE" if is_pausing else "RESUME"
                print(f"⏸ Sent {action} command to printer")
                message = f'Print {"paused" if is_pausing else "resumed"} - heater and printer'
                return jsonify({'success': True, 'message': message})
            except Exception as e:
                print(f"Error {'pausing' if is_pausing else 'resuming'} printer: {e}")
                message = f'Heater {"paused" if is_pausing else "resumed"} (printer command failed)'
                return jsonify({'success': True, 'message': message})

        message = 'Pause toggled' if is_pausing else 'Resume toggled'
        return jsonify({'success': True, 'message': message})

    return jsonify({'success': False, 'message': 'No print active'})

@app.route('/confirm_preheat', methods=['POST'])
def confirm_preheat():
    global preheat_confirmed

    if print_active and awaiting_preheat_confirmation:
        with state_lock:
            preheat_confirmed = True
        return jsonify({'success': True, 'message': 'Preheat confirmed, starting print timer'})

    return jsonify({'success': False, 'message': 'Not waiting for preheat confirmation'})

@app.route('/resume_print', methods=['POST'])
def resume_print():
    global resume_confirmed, pending_resume

    if pending_resume:
        with state_lock:
            resume_confirmed = True
            pending_resume = False
            status_data['pending_resume'] = False
        print("User confirmed print resume")
        return jsonify({'success': True, 'message': 'Resuming print from crash recovery'})

    return jsonify({'success': False, 'message': 'No print to resume'})

@app.route('/abort_resume', methods=['POST'])
def abort_resume():
    global resume_aborted, pending_resume, resume_state

    if pending_resume:
        with state_lock:
            resume_aborted = True
            pending_resume = False
            resume_state = None
            status_data['pending_resume'] = False

        # Delete the print state file
        delete_print_state()

        print("User aborted print resume")
        return jsonify({'success': True, 'message': 'Print resume aborted, starting fresh'})

    return jsonify({'success': False, 'message': 'No print to abort'})

@app.route('/emergency_stop', methods=['POST'])
def emergency_stop_route():
    global emergency_stop_requested, stop_requested, additional_seconds
    global printer_mqtt_client, printer_connected, mqtt_sequence_id

    with state_lock:
        emergency_stop_requested = True
        stop_requested = True
        additional_seconds = 0  # Reset time adjustments when emergency stopping

    # Also stop the printer if connected
    if printer_connected and printer_mqtt_client:
        try:
            serial = current_settings.get('printer_serial', '')
            topic = f"device/{serial}/request"

            with printer_lock:
                mqtt_sequence_id += 1
                command = {
                    "print": {
                        "sequence_id": str(mqtt_sequence_id),
                        "command": "stop"
                    }
                }

            printer_mqtt_client.publish(topic, json.dumps(command), qos=1)
            print("⚠️  Emergency stop: Sent STOP command to printer")
        except Exception as e:
            print(f"Error stopping printer during emergency: {e}")

    return jsonify({'success': True, 'message': 'Emergency stop activated - heater and printer stopped'})

@app.route('/toggle_heater', methods=['POST'])
def toggle_heater():
    global heater_on, heater_manual_override

    state = request.json['state']

    with state_lock:
        heater_manual_override = True
        heater_on = state
        GPIO.output(RELAY_PIN, GPIO.HIGH if state else GPIO.LOW)
        status_data['heater_on'] = state
        status_data['heater_manual'] = True

    emit_status_update()  # Immediate WebSocket update for manual heater control
    return jsonify({'success': True})

@app.route('/toggle_fans', methods=['POST'])
def toggle_fans():
    global fans_on, fans_manual_override

    state = request.json['state']

    with state_lock:
        fans_manual_override = True
        fans_on = state
        GPIO.output(FAN1_PIN, GPIO.HIGH if state else GPIO.LOW)
        GPIO.output(FAN2_PIN, GPIO.HIGH if state else GPIO.LOW)
        status_data['fans_on'] = state
        status_data['fans_manual'] = True

    emit_status_update()  # Immediate WebSocket update for manual fan control
    return jsonify({'success': True})

@app.route('/toggle_lights', methods=['POST'])
def toggle_lights():
    global lights_on

    state = request.json['state']

    with state_lock:
        lights_on = state
        set_lights(state)
        status_data['lights_on'] = state
        current_settings['lights_enabled'] = state
        save_settings(current_settings)

    emit_status_update()  # Immediate WebSocket update for lights control
    return jsonify({'success': True})

# Printer Control Endpoints
@app.route('/printer/pause', methods=['POST'])
def printer_pause():
    """Send pause command to printer via MQTT"""
    global printer_mqtt_client, printer_connected, mqtt_sequence_id

    if not printer_connected or not printer_mqtt_client:
        return jsonify({'success': False, 'message': 'Printer not connected'})

    try:
        serial = current_settings.get('printer_serial', '')
        topic = f"device/{serial}/request"

        with printer_lock:
            mqtt_sequence_id += 1
            command = {
                "print": {
                    "sequence_id": str(mqtt_sequence_id),
                    "command": "pause"
                }
            }

        printer_mqtt_client.publish(topic, json.dumps(command), qos=1)
        print(f"Sent PAUSE command to printer")
        return jsonify({'success': True})

    except Exception as e:
        print(f"Error sending pause command: {e}")
        return jsonify({'success': False, 'message': str(e)})

@app.route('/printer/resume', methods=['POST'])
def printer_resume():
    """Send resume command to printer via MQTT"""
    global printer_mqtt_client, printer_connected, mqtt_sequence_id

    if not printer_connected or not printer_mqtt_client:
        return jsonify({'success': False, 'message': 'Printer not connected'})

    try:
        serial = current_settings.get('printer_serial', '')
        topic = f"device/{serial}/request"

        with printer_lock:
            mqtt_sequence_id += 1
            command = {
                "print": {
                    "sequence_id": str(mqtt_sequence_id),
                    "command": "resume"
                }
            }

        printer_mqtt_client.publish(topic, json.dumps(command), qos=1)
        print(f"Sent RESUME command to printer")
        return jsonify({'success': True})

    except Exception as e:
        print(f"Error sending resume command: {e}")
        return jsonify({'success': False, 'message': str(e)})

@app.route('/printer/stop', methods=['POST'])
def printer_stop():
    """Send stop command to printer via MQTT"""
    global printer_mqtt_client, printer_connected, mqtt_sequence_id

    if not printer_connected or not printer_mqtt_client:
        return jsonify({'success': False, 'message': 'Printer not connected'})

    try:
        serial = current_settings.get('printer_serial', '')
        topic = f"device/{serial}/request"

        with printer_lock:
            mqtt_sequence_id += 1
            command = {
                "print": {
                    "sequence_id": str(mqtt_sequence_id),
                    "command": "stop"
                }
            }

        printer_mqtt_client.publish(topic, json.dumps(command), qos=1)
        print(f"Sent STOP command to printer")
        return jsonify({'success': True})

    except Exception as e:
        print(f"Error sending stop command: {e}")
        return jsonify({'success': False, 'message': str(e)})

# Camera Streaming Functions

def camera_monitor():
    """Background thread that keeps camera stream running when printer is configured.
    Stores latest frame in shared buffer for clients to read."""
    global camera_process, camera_streaming, camera_frame

    print("Camera monitor thread started")

    while not shutdown_requested:
        try:
            # Check if printer is configured
            printer_ip = current_settings.get('printer_ip', '')
            access_code = current_settings.get('printer_access_code', '')
            printer_enabled = current_settings.get('printer_enabled', False)

            if not printer_enabled or not printer_ip or not access_code:
                # Printer not configured, wait and retry
                time.sleep(5)
                continue

            # Start FFmpeg if not running
            with camera_lock:
                if camera_process is not None and camera_process.poll() is None:
                    # Already running, wait a bit
                    time.sleep(1)
                    continue

                # Start camera stream
                print(f"📷 Starting camera stream from {printer_ip}...")
                camera_streaming = True
                status_data['camera_streaming'] = True

                stream_url = f"rtsps://bblp:{access_code}@{printer_ip}:322/streaming/live/1"

                camera_process = subprocess.Popen([
                    'ffmpeg',
                    '-rtsp_transport', 'tcp',
                    '-i', stream_url,
                    '-f', 'image2pipe',
                    '-vcodec', 'mjpeg',
                    '-q:v', '5',  # Quality (2=best, 31=worst) - 5 is good balance
                    '-vf', 'scale=1280:-1,fps=10',  # 720p @ 10fps - ~35% CPU
                    '-'
                ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=10**6)

            # Give FFmpeg a moment to start
            time.sleep(2)

            if camera_process.poll() is not None:
                stderr_output = camera_process.stderr.read().decode('utf-8', errors='ignore')
                print(f"Camera stream failed to start: {stderr_output[:200]}")
                with camera_lock:
                    camera_streaming = False
                    status_data['camera_streaming'] = False
                    camera_process = None
                time.sleep(10)  # Wait before retry
                continue

            print("📷 Camera stream started successfully")
            emit_status_update()

            # Read frames continuously and store in shared buffer
            buffer = b''
            while not shutdown_requested and camera_process.poll() is None:
                chunk = camera_process.stdout.read(4096)
                if not chunk:
                    break

                buffer += chunk

                # Extract JPEG frames (start: FFD8, end: FFD9)
                while True:
                    start = buffer.find(b'\xff\xd8')
                    if start == -1:
                        buffer = b''
                        break

                    end = buffer.find(b'\xff\xd9', start + 2)
                    if end == -1:
                        buffer = buffer[start:]
                        break

                    # Store complete frame
                    frame = buffer[start:end + 2]
                    buffer = buffer[end + 2:]

                    with camera_frame_lock:
                        camera_frame = frame

            # FFmpeg stopped
            print("📷 Camera stream stopped, will restart...")
            with camera_lock:
                camera_streaming = False
                status_data['camera_streaming'] = False
                if camera_process:
                    camera_process.terminate()
                    camera_process.wait()
                    camera_process = None

            emit_status_update()
            time.sleep(5)  # Wait before restart

        except Exception as e:
            print(f"Camera monitor error: {e}", flush=True)
            with camera_lock:
                camera_streaming = False
                status_data['camera_streaming'] = False
                if camera_process:
                    try:
                        camera_process.terminate()
                        camera_process.wait()
                    except:
                        pass
                    camera_process = None
            time.sleep(10)

    # Cleanup on shutdown
    with camera_lock:
        if camera_process:
            camera_process.terminate()
            camera_process.wait()
            camera_process = None
        camera_streaming = False
    print("Camera monitor thread stopped")

def generate_sdp_file():
    """Generate SDP file for Bambu Lab camera stream"""
    printer_ip = current_settings.get('printer_ip', '')
    access_code = current_settings.get('printer_access_code', '')

    sdp_content = f"""v=0
o=- 0 0 IN IP4 {printer_ip}
s=No Name
c=IN IP4 {printer_ip}
t=0 0
a=tool:libavformat 58.76.100
m=video 0 RTP/AVP 96
a=rtpmap:96 H264/90000
a=fmtp:96 packetization-mode=1
a=control:rtsps://bblp:{access_code}@{printer_ip}:322/streaming/live/1
"""
    return sdp_content

def camera_frame_generator():
    """Generator that serves frames from the shared buffer (fed by camera_monitor thread)"""
    last_frame = None
    while True:
        with camera_frame_lock:
            current_frame = camera_frame

        if current_frame is None:
            # No frame yet, wait
            time.sleep(0.1)
            continue

        if current_frame != last_frame:
            last_frame = current_frame
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + current_frame + b'\r\n')
        else:
            # Same frame, small delay to prevent busy loop
            time.sleep(0.05)

@app.route('/printer/camera/status')
def camera_status():
    """Get camera streaming status"""
    return jsonify({
        'streaming': camera_streaming,
        'printer_configured': bool(current_settings.get('printer_ip', '')) and bool(current_settings.get('printer_access_code', '')),
        'printer_enabled': current_settings.get('printer_enabled', False)
    })

@app.route('/printer/camera/feed')
def camera_feed():
    """Stream camera feed as MJPEG (always-on when printer configured)"""
    if not camera_streaming:
        # Return placeholder or error
        return jsonify({'error': 'Camera not available', 'reason': 'Printer not configured or camera starting'}), 503

    return Response(camera_frame_generator(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/adjust_time', methods=['POST'])
def adjust_time():
    global additional_seconds

    minutes = request.json['minutes']

    with time_lock:
        additional_seconds += minutes * 60

    return jsonify({'success': True, 'message': f'Adjusted time by {minutes} minutes'})

@app.route('/reset', methods=['POST'])
def reset():
    global reset_requested

    if not emergency_stop:
        return jsonify({'success': False, 'message': 'No emergency condition to reset'})

    reset_requested = True
    return jsonify({'success': True, 'message': 'Reset requested. Checking if fire is cleared...'})

@app.route('/download_log')
def download_log():
    if not log_data or len(log_data) <= 1:
        return "No log data available", 404

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerows(log_data)

    # Create response
    mem = io.BytesIO()
    mem.write(output.getvalue().encode('utf-8'))
    mem.seek(0)
    output.close()

    return send_file(
        mem,
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'temperature_log_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    )

def run_flask():
    # Use socketio.run() instead of app.run() for WebSocket support
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)

# Start monitoring threads
fire_thread = threading.Thread(target=fire_monitor, daemon=True)
main_thread = threading.Thread(target=main_loop, daemon=True)
printer_thread = threading.Thread(target=printer_monitor, daemon=True)
camera_thread = threading.Thread(target=camera_monitor, daemon=True)
flask_thread = threading.Thread(target=run_flask, daemon=True)

fire_thread.start()
main_thread.start()
printer_thread.start()
camera_thread.start()
flask_thread.start()

print("\n" + "="*50)
print("X1C Chamber Heater Controller")
print("Web interface: http://<raspberry-pi-ip>:5000")
print("="*50 + "\n")

# Keep main thread alive
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\n\nKeyboard interrupt received. Shutting down safely...")
    shutdown_requested = True
finally:
    # Cleanup - turn off heater, fans, and buzzer
    # Note: Lights maintain their current state
    GPIO.output(RELAY_PIN, GPIO.LOW)
    GPIO.output(FAN1_PIN, GPIO.LOW)
    GPIO.output(FAN2_PIN, GPIO.LOW)
    GPIO.output(BUZZER_PIN, GPIO.LOW)
    GPIO.cleanup()
    print("System shutdown complete.")
