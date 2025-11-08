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
from flask import Flask, render_template_string, jsonify, request, send_file
import io
import csv

# Configuration Constants
HYSTERESIS = 2.0  # Temperature hysteresis band in °C
TEMP_UPDATE_INTERVAL = 5  # Temperature reading interval in seconds (faster for graphing)
COOLDOWN_HOURS = 4  # Slow cooldown duration in hours
COOLDOWN_STEP_INTERVAL = 300  # Cooldown step interval in seconds (5 minutes)
SETTINGS_FILE = 'heater_settings.json'
LOG_FILE = 'temperature_log.csv'

# USB Hub Configuration (adjust for your Raspberry Pi)
# Run 'sudo uhubctl' to find your hub location and port number
USB_HUB_LOCATION = '1-1'  # Common for RPi 4, may be '1-1.1' or different on other models
USB_HUB_PORT = '2'        # Port number where USB lights are connected
USB_CONTROL_ENABLED = True  # Set to False to disable USB control if not needed/supported

# Pin Setup
RELAY_PIN = 17   # SSR control
FIRE_PIN = 18    # MQ-2 DO
BUZZER_PIN = 27  # Buzzer
FAN1_PIN = 23    # Filtration fan 1
FAN2_PIN = 24    # Filtration fan 2

GPIO.setmode(GPIO.BCM)
GPIO.setup(RELAY_PIN, GPIO.OUT)
GPIO.setup(FIRE_PIN, GPIO.IN)
GPIO.setup(BUZZER_PIN, GPIO.OUT)
GPIO.setup(FAN1_PIN, GPIO.OUT)
GPIO.setup(FAN2_PIN, GPIO.OUT)

# Global state flags and variables
emergency_stop = False
shutdown_requested = False
fans_on = False
fans_manual_override = False
heater_on = False
heater_manual_override = False
lights_on = False
print_active = False
print_paused = False
warmup_complete = False
awaiting_preheat_confirmation = False
start_requested = False
stop_requested = False
emergency_stop_requested = False
pause_requested = False
preheat_confirmed = False
additional_seconds = 0
time_lock = threading.Lock()
state_lock = threading.Lock()
reset_requested = False

# Temperature tracking
temp_history = []  # List of {time, temp, setpoint} dicts
history_lock = threading.Lock()
MAX_HISTORY = 1000  # Keep last 1000 data points

# Logging
logging_enabled = False
log_data = []

status_data = {
    'current_temp': 0,
    'sensor_temps': [],
    'setpoint': 0,
    'time_remaining': 0,
    'heater_on': False,
    'heater_manual': False,
    'fans_on': False,
    'fans_manual': False,
    'lights_on': False,
    'emergency_stop': False,
    'print_active': False,
    'print_paused': False,
    'awaiting_preheat_confirmation': False,
    'phase': 'idle',  # idle, warming_up, heating, maintaining, cooling
    'eta_to_target': 0,
    'print_time_remaining': 0,
    'cooldown_time_remaining': 0
}

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

print(f"Detected ambient temperature: {ambient:.1f}°C")

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
        'temp_unit': 'C',
        'require_preheat_confirmation': False,
        'probe_names': {},
        'presets': [
            {'name': 'ABS Standard', 'temp': 60, 'hours': 8, 'minutes': 0},
            {'name': 'ASA Standard', 'temp': 65, 'hours': 10, 'minutes': 0},
            {'name': 'Quick Test', 'temp': 40, 'hours': 0, 'minutes': 30}
        ]
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

# Load initial settings
current_settings = load_settings()

# Update probe names from settings
if current_settings.get('probe_names'):
    for sensor_id, custom_name in current_settings['probe_names'].items():
        if custom_name:  # Only update if custom name is not empty
            probe_locations[sensor_id] = custom_name

# USB Lights Control
def get_usb_power_status():
    """Check current USB power status"""
    if not USB_CONTROL_ENABLED:
        return False

    try:
        result = subprocess.run(['uhubctl'], capture_output=True, text=True, timeout=5)
        # Look for the configured hub location
        hub_line = f'Current status for hub {USB_HUB_LOCATION}'
        if hub_line in result.stdout:
            # Parse the output to check port status
            lines = result.stdout.split('\n')
            for line in lines:
                if f'Port {USB_HUB_PORT}:' in line and 'power' in line.lower():
                    # Port is powered if status contains '0x0503' or similar power-on code
                    return '0503' in line or '0x0503' in line
        return False
    except FileNotFoundError:
        print("WARNING: uhubctl not found. Install with: sudo apt install uhubctl")
        print("USB light control will be disabled.")
        return False
    except (subprocess.TimeoutExpired, Exception) as e:
        print(f"WARNING: USB power status check failed: {e}")
        return False

def set_usb_power(on_off):
    """Control USB hub power"""
    if not USB_CONTROL_ENABLED:
        print("USB control is disabled in configuration")
        return False

    try:
        action = '1' if on_off else '0'
        result = subprocess.run(
            ['uhubctl', '-l', USB_HUB_LOCATION, '-p', USB_HUB_PORT, '-a', action],
            capture_output=True,
            text=True,
            timeout=5,
            check=False  # Don't raise exception, we'll check manually
        )

        if result.returncode != 0:
            print(f"WARNING: uhubctl command failed (exit code {result.returncode})")
            print(f"stdout: {result.stdout}")
            print(f"stderr: {result.stderr}")
            print(f"\nTroubleshooting:")
            print(f"1. Run 'sudo uhubctl' on your Raspberry Pi to find correct hub/port")
            print(f"2. Update USB_HUB_LOCATION (currently '{USB_HUB_LOCATION}') and USB_HUB_PORT (currently '{USB_HUB_PORT}')")
            print(f"3. Try running with sudo if permission denied")
            print(f"4. Set USB_CONTROL_ENABLED = False if USB control not needed/supported")
            return False

        return True

    except FileNotFoundError:
        print("ERROR: uhubctl not found. Install with: sudo apt install uhubctl")
        print("Or set USB_CONTROL_ENABLED = False to disable USB control")
        return False
    except (subprocess.TimeoutExpired, Exception) as e:
        print(f"WARNING: USB power control failed: {e}")
        return False

# Initialize lights based on saved settings
lights_on = current_settings.get('lights_enabled', True)
if lights_on:
    set_usb_power(True)
    print("USB lights turned on (from saved settings)")
status_data['lights_on'] = lights_on

# Fire Monitor with Web Reset
def fire_monitor():
    global emergency_stop, heater_on, fans_on, reset_requested

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
                print("Heater and fans turned off. Use web interface to RESET.")

        if emergency_stop and reset_requested:
            if GPIO.input(FIRE_PIN) == GPIO.HIGH:
                print("Reset acknowledged via web interface. Fire condition cleared.")
                with state_lock:
                    emergency_stop = False
                    status_data['emergency_stop'] = False
                    GPIO.output(BUZZER_PIN, GPIO.LOW)
                    reset_requested = False
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
def slow_cool(pid, hours=COOLDOWN_HOURS):
    global heater_on, print_active

    current_set = pid.setpoint
    steps = hours * 12  # Every 5 min
    delta = (current_set - ambient) / steps

    print(f"Starting {hours}-hour cooldown from {current_set:.1f}°C to {ambient:.1f}°C")
    status_data['phase'] = 'cooling'

    for step in range(steps):
        if shutdown_requested or stop_requested or emergency_stop_requested:
            break

        pid.setpoint -= delta

        avg_temp = get_average_temp()
        if avg_temp is not None:
            cooldown_remaining = (steps - step) * COOLDOWN_STEP_INTERVAL
            status_data['cooldown_time_remaining'] = cooldown_remaining
            print(f"Cooldown step {step+1}/{steps}: Setpoint={pid.setpoint:.1f}°C | Current={avg_temp:.1f}°C")
        else:
            print(f"Cooldown step {step+1}/{steps}: Setpoint={pid.setpoint:.1f}°C | Temp sensor error")

        if heater_on and not heater_manual_override:
            heater_on = False
            GPIO.output(RELAY_PIN, GPIO.LOW)
            status_data['heater_on'] = False

        time.sleep(COOLDOWN_STEP_INTERVAL)

    print("Cooldown complete.")
    status_data['phase'] = 'idle'
    status_data['cooldown_time_remaining'] = 0

# Main PID Loop
def main_loop():
    global heater_on, fans_on, additional_seconds, print_active, start_requested
    global stop_requested, emergency_stop_requested, logging_enabled, log_data
    global heater_manual_override, fans_manual_override, print_paused, pause_requested
    global warmup_complete, awaiting_preheat_confirmation, preheat_confirmed

    while not shutdown_requested:
        # Wait for START command from web interface
        while not start_requested and not shutdown_requested:
            time.sleep(0.5)

        if shutdown_requested:
            break

        with state_lock:
            start_requested = False
            print_active = True
            stop_requested = False
            emergency_stop_requested = False
            status_data['print_active'] = True
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

        # Get settings
        desired_temp = current_settings['desired_temp']
        print_duration_seconds = (current_settings['print_hours'] * 3600) + \
                                (current_settings['print_minutes'] * 60)
        require_confirmation = current_settings.get('require_preheat_confirmation', False)

        # Initialize logging if enabled
        if logging_enabled:
            log_data = []
            log_data.append(['Timestamp', 'Elapsed (s)', 'Current Temp (°C)',
                           'Setpoint (°C)', 'Heater', 'Fans', 'Phase'])

        # Turn on fans if configured
        if current_settings.get('fans_enabled', True) and not emergency_stop:
            fans_on = True
            GPIO.output(FAN1_PIN, GPIO.HIGH)
            GPIO.output(FAN2_PIN, GPIO.HIGH)
            status_data['fans_on'] = True

        # PID Setup
        pid = PID(Kp=2.0, Ki=0.5, Kd=0.1, setpoint=desired_temp, output_limits=(-100, 100))

        # Warming up phase - reach target temp before starting timer
        status_data['phase'] = 'warming_up'
        print(f"\nWarming up chamber to {desired_temp}°C...")

        while print_active and not shutdown_requested:
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
                            print("Preheat confirmed by user. Starting print timer.")
                            break

                        # Continue PID control to maintain temp
                        avg_temp = get_average_temp()
                        if avg_temp is not None:
                            pid.setpoint = desired_temp
                            control = pid(avg_temp)

                            # Heater control
                            hysteresis = current_settings.get('hysteresis', HYSTERESIS)
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
                hysteresis = current_settings.get('hysteresis', HYSTERESIS)
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

            time.sleep(TEMP_UPDATE_INTERVAL)

        # If stopped during warmup, skip to cleanup
        if stop_requested or emergency_stop_requested or shutdown_requested:
            # Jump to cleanup section
            pass  # Will fall through to existing cleanup code after main loop
        else:
            # Warmup complete, now start the print timer
            start_time = time.time()
            total_paused_time = 0  # Track total time spent paused
            pause_start_time = 0   # Track when pause started
            status_data['phase'] = 'heating'

            print(f"Starting print timer: {print_duration_seconds/60:.1f} minutes")

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
                    hysteresis = current_settings.get('hysteresis', HYSTERESIS)
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

                time.sleep(TEMP_UPDATE_INTERVAL)

        # Print cycle ended - start cooldown (using configurable cooldown time)
        if not stop_requested and not emergency_stop_requested and not shutdown_requested:
            cooldown_hours = current_settings.get('cooldown_hours', COOLDOWN_HOURS)
            slow_cool(pid, hours=cooldown_hours)

        # Turn off everything
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
            stop_requested = False
            emergency_stop_requested = False
            # Clear pause state
            print_paused = False
            pause_requested = False
            status_data['print_paused'] = False

        print("Print cycle complete.")

# Flask Web Interface
app = Flask(__name__)

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
        .button:disabled { opacity: 0.5; cursor: not-allowed; }

        .button.primary { background: var(--primary-color); color: white; }
        .button.danger { background: var(--danger-color); color: white; }
        .button.warning { background: var(--warning-color); color: white; }
        .button.secondary { background: #757575; color: white; }

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
            color: var(--primary-color);
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
    </style>
</head>
<body>
    <div class="header">
        <h1>🔥 X1C Chamber Heater Controller</h1>
        <button class="theme-toggle" onclick="openSettings()">⚙️ Settings</button>
    </div>

    <div id="fire-alert" class="alert danger" style="display: none;">
        🔥 FIRE DETECTED! Emergency Shutdown Active
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

                <div class="toggle-switch" style="margin-top: 15px;">
                    <span>Require Preheat Confirmation</span>
                    <label class="switch">
                        <input type="checkbox" id="require-preheat-confirmation">
                        <span class="slider"></span>
                    </label>
                </div>
                <small style="display: block; margin-top: 5px; color: #666;">Wait for user confirmation after reaching target temperature before starting print timer</small>
            </div>

            <div class="settings-section">
                <h3>Probe Names</h3>
                <div id="probe-names-list">
                    <!-- Will be populated dynamically -->
                </div>
            </div>

            <div style="margin-top: 20px; display: flex; gap: 10px;">
                <button class="button primary" onclick="saveAdvancedSettings()" style="flex: 1;">
                    💾 Save Settings
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
                <button class="button danger" onclick="emergencyStop()">⚠ EMERGENCY STOP</button>
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
        let notificationsEnabled = false;
        let tempUnit = 'C'; // Global temperature unit
        let sensorIds = []; // Store sensor IDs for renaming
        let previousPauseState = false; // Track pause state for notifications
        let previousAwaitingPreheat = false; // Track preheat confirmation state

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

            const requirePreheatConfirmation = localStorage.getItem('requirePreheatConfirmation') === 'true';
            document.getElementById('require-preheat-confirmation').checked = requirePreheatConfirmation;

            const savedTheme = localStorage.getItem('theme') || 'light';
            document.getElementById('dark-mode-toggle').checked = (savedTheme === 'dark');

            // Populate probe names
            populateProbeNames();

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

            const requirePreheatConfirmation = document.getElementById('require-preheat-confirmation').checked;
            localStorage.setItem('requirePreheatConfirmation', requirePreheatConfirmation);

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
                        require_preheat_confirmation: requirePreheatConfirmation,
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

            // Update chart Y-axis label if chart exists
            if (chart) {
                chart.options.scales.y.title.text = `Temperature (${unit})`;
                chart.update('none');
            }
        }

        // Request notification permission
        if ('Notification' in window && Notification.permission === 'default') {
            Notification.requestPermission().then(permission => {
                notificationsEnabled = (permission === 'granted');
            });
        } else if ('Notification' in window && Notification.permission === 'granted') {
            notificationsEnabled = true;
        }

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
            }
        }

        async function stopPrint() {
            try {
                const response = await fetch('/stop', {method: 'POST'});
                const result = await response.json();
                if (result.success) {
                    showNotification('Print Stopped', 'Print cycle stopped');
                }
            } catch (e) {
                console.error('Failed to stop:', e);
            }
        }

        async function pausePrint() {
            try {
                const response = await fetch('/pause', {method: 'POST'});
                const result = await response.json();
                if (result.success) {
                    // Notification will be updated based on status
                }
            } catch (e) {
                console.error('Failed to pause:', e);
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

        async function emergencyStop() {
            if (!confirm('Emergency stop will immediately halt all heating and cooling. Continue?')) {
                return;
            }

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
            const state = document.getElementById('heater-toggle').checked;
            try {
                const response = await fetch('/toggle_heater', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({state: state})
                });
            } catch (e) {
                console.error('Failed to toggle heater:', e);
            }
        }

        async function toggleFans() {
            const state = document.getElementById('fans-toggle').checked;
            try {
                const response = await fetch('/toggle_fans', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({state: state})
                });
            } catch (e) {
                console.error('Failed to toggle fans:', e);
            }
        }

        async function toggleLights() {
            const state = document.getElementById('lights-toggle').checked;
            try {
                const response = await fetch('/toggle_lights', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({state: state})
                });
            } catch (e) {
                console.error('Failed to toggle lights:', e);
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

        function showNotification(title, body, isUrgent = false) {
            if (notificationsEnabled) {
                new Notification(title, {
                    body: body,
                    icon: '🔥',
                    requireInteraction: isUrgent
                });
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
                    if (data.emergency_stop) {
                        alert.style.display = 'block';
                        resetBtn.disabled = false;
                    } else {
                        alert.style.display = 'none';
                        resetBtn.disabled = true;
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

        // Initialize
        // Load temperature unit first before initializing chart
        tempUnit = localStorage.getItem('tempUnit') || 'C';

        initChart();
        loadSettings();
        updateStatus();
        updateHistory();

        // Update every 2 seconds
        setInterval(updateStatus, 2000);
        setInterval(updateHistory, 5000);
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
    if 'temp_unit' in data:
        current_settings['temp_unit'] = data['temp_unit']
    if 'require_preheat_confirmation' in data:
        current_settings['require_preheat_confirmation'] = data['require_preheat_confirmation']
    if 'probe_names' in data:
        current_settings['probe_names'] = data['probe_names']
        # Update probe locations immediately
        for sensor_id, custom_name in data['probe_names'].items():
            if custom_name:
                probe_locations[sensor_id] = custom_name

    save_settings(current_settings)

    return jsonify({'success': True, 'message': 'Advanced settings saved'})

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
    global stop_requested, additional_seconds

    if print_active:
        with state_lock:
            stop_requested = True
            additional_seconds = 0  # Reset time adjustments when stopping
        return jsonify({'success': True, 'message': 'Print stopped'})

    return jsonify({'success': False, 'message': 'No print active'})

@app.route('/pause', methods=['POST'])
def pause():
    global pause_requested

    if print_active:
        with state_lock:
            pause_requested = True
        message = 'Pause toggled' if not print_paused else 'Resume toggled'
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

@app.route('/emergency_stop', methods=['POST'])
def emergency_stop_route():
    global emergency_stop_requested, stop_requested, additional_seconds

    with state_lock:
        emergency_stop_requested = True
        stop_requested = True
        additional_seconds = 0  # Reset time adjustments when emergency stopping

    return jsonify({'success': True, 'message': 'Emergency stop activated'})

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

    return jsonify({'success': True})

@app.route('/toggle_lights', methods=['POST'])
def toggle_lights():
    global lights_on

    state = request.json['state']

    with state_lock:
        lights_on = state
        set_usb_power(state)
        status_data['lights_on'] = state
        current_settings['lights_enabled'] = state
        save_settings(current_settings)

    return jsonify({'success': True})

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
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

# Start monitoring threads
fire_thread = threading.Thread(target=fire_monitor, daemon=True)
main_thread = threading.Thread(target=main_loop, daemon=True)
flask_thread = threading.Thread(target=run_flask, daemon=True)

fire_thread.start()
main_thread.start()
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
    # Cleanup
    GPIO.output(RELAY_PIN, GPIO.LOW)
    GPIO.output(FAN1_PIN, GPIO.LOW)
    GPIO.output(FAN2_PIN, GPIO.LOW)
    GPIO.output(BUZZER_PIN, GPIO.LOW)
    GPIO.cleanup()
    set_usb_power(False)
    print("System shutdown complete.")
