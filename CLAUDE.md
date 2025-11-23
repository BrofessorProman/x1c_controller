# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Raspberry Pi-based temperature controller for a 3D printer chamber heater with comprehensive Flask web interface and Bambu Lab X1C printer integration. It uses PID control to maintain target temperature using multiple DS18B20 sensors, controls a heater via SSR relay, monitors for fire with an MQ-2 sensor, manages USB-powered lights and filtration fans, and integrates with Bambu Lab X1C printer via MQTT for automated print workflows.

**Key Features:**
- ✅ Fully web-controlled - no command-line interaction required during operation
- ✅ Runs as systemd service - persistent, auto-starts on boot
- ✅ Remote access via WireGuard VPN
- ✅ Real-time temperature graphing with dark mode
- ✅ Comprehensive settings menu with temperature units (C/F), adjustable hysteresis, probe renaming, skip preheat, and cooldown target
- ✅ In-page modal notifications and CSV logging
- ✅ Preset configurations and settings persistence
- ✅ Git version control with GitHub integration
- ✅ Pause/Resume functionality - pause print timer while maintaining temperature
- ✅ Preheat phase - reaches target temperature before starting print timer (optional skip)
- ✅ Optional preheat confirmation - wait for user confirmation before starting print
- ✅ Configurable cooldown target temperature - reliable cooldown to user-set temp
- ✅ GPIO state detection on restart - syncs software/hardware state
- ✅ Fire alarm UI lockdown - comprehensive safety controls during emergency
- ✅ Print state persistence and crash recovery - resume interrupted prints after crashes or restarts
- ✅ **Bambu Lab X1C Integration** - MQTT monitoring, control, and camera streaming (v2.8-alpha)
- ✅ **Material-Based Auto-Start** - Automatically configures heater when print starts based on detected material
- ✅ **Printer Control** - Pause/Resume/Stop prints via web interface
- ✅ **Live Camera Feed** - On-demand streaming from X1C camera

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run manually for testing
python3 x1c_heater.py

# Access web interface
http://<raspberry-pi-ip>:5000

# For production: Set up as systemd service (see SERVICE_MANAGEMENT.md)
```

## Hardware Dependencies

The system requires:
- **DS18B20 temperature sensors**: Multiple probes for averaging chamber temperature (1-Wire interface required)
- **SSR relay**: GPIO pin 17 controls heater power
- **MQ-2 fire sensor**: GPIO pin 18 for fire detection (active LOW - triggers when pin goes LOW)
- **Lights relay**: GPIO pin 22 for independent lights control
- **Buzzer**: GPIO pin 27 for fire alarm
- **Filtration fans**: GPIO pins 23 and 24

## Software Dependencies

Install Python packages:
```bash
pip install -r requirements.txt
```

Or manually:
```bash
pip install w1thermsensor simple-pid RPi.GPIO Flask flask-socketio paho-mqtt
```

Install system packages:
```bash
# Enable 1-Wire interface for temperature sensors
sudo raspi-config
# Navigate to: Interface Options → 1-Wire → Enable

# Install FFmpeg for camera streaming (optional, only needed if using printer camera)
sudo apt-get install -y ffmpeg
```

## Running the Controller

### Development/Testing
```bash
python3 x1c_heater.py
```

### Production (Recommended)
Set up as systemd service for persistence and auto-start. See `SERVICE_MANAGEMENT.md` for complete instructions.

Quick setup:
```bash
# Create service file
sudo nano /etc/systemd/system/x1c-heater.service
# (Copy contents from SERVICE_MANAGEMENT.md)

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable x1c-heater
sudo systemctl start x1c-heater

# Check status
sudo systemctl status x1c-heater
```

The script will:
1. Auto-detect DS18B20 sensors and calculate ambient temperature
2. Load last saved settings from `heater_settings.json`
3. Start Flask web server on port 5000
4. Initialize lights based on saved preference
5. Wait for START command from web interface

## Remote Access

For secure remote access from outside your home network, WireGuard VPN is recommended. This allows access to your entire home network including the heater controller and any future devices.

### WireGuard VPN Setup Guide

**Operating System Recommendation:**
For Raspberry Pi 4 with 8GB RAM, use **Raspberry Pi OS Lite (64-bit)**:
- Required for utilizing all 8GB RAM (32-bit limited to ~3GB)
- No desktop environment - more resources for heater controller
- Smaller attack surface for security
- Full WireGuard support built-in
- Perfect for headless server operation (SSH + web interface access)

**Installation & Configuration:**

Most WireGuard installers provide interactive setup. Key configuration choices:

1. **IPv6 Routing** - Select "Yes" to force routing IPv6 to block leakage
   - Prevents traffic from bypassing VPN tunnel
   - Safe even if you don't use IPv6 devices
   - Future-proof for ISP IPv6 rollout

2. **DNS Entry vs Public IP** - Select "DNS Entry" (recommended)
   - Works with Dynamic DNS (DDNS) services
   - Handles dynamic IP changes automatically
   - Client configs never need updating
   - Only use Public IP if you have a static IP from ISP

3. **Client IP Configuration**
   - Enter client IP without CIDR notation (e.g., `10.180.200.2`)
   - Most setup wizards add `/24` automatically
   - Each client needs unique IP in VPN subnet (`.2`, `.3`, `.4`, etc.)
   - Check server config for subnet: `sudo cat /etc/wireguard/wg0.conf`

**Dynamic DNS Setup (DuckDNS):**

If using DNS Entry option, set up DuckDNS for automatic IP updates:

```bash
# 1. Create directory
mkdir ~/duckdns
cd ~/duckdns

# 2. Create update script
nano duck.sh
```

Add this content (replace YOUR_DOMAIN and YOUR_TOKEN from duckdns.org):
```bash
#!/bin/bash
echo url="https://www.duckdns.org/update?domains=YOUR_DOMAIN&token=YOUR_TOKEN&ip=" | curl -k -o ~/duckdns/duck.log -K -
```

```bash
# 3. Make executable and test
chmod 700 duck.sh
./duck.sh
cat duck.log  # Should show "OK"

# 4. Set up auto-update (runs every 5 minutes)
crontab -e
# Add this line:
*/5 * * * * ~/duckdns/duck.sh >/dev/null 2>&1
```

**Router Port Forwarding:**

Configure port forwarding on your router (example for Asus RT-AC5300):

1. Navigate to: **Advanced Settings → WAN → Virtual Server / Port Forwarding**
2. Configure:
   - **Service Name:** WireGuard
   - **Port Range:** 51820 (or your WireGuard port)
   - **Local IP:** Raspberry Pi's IP (e.g., `192.168.1.50`)
   - **Local Port:** 51820
   - **Protocol:** **UDP** (important!)
   - **Source Target:** Leave blank or `0.0.0.0` (allow from anywhere)

**Important:** Set a static IP or DHCP reservation for your Pi so port forwarding doesn't break.

**Testing the Connection:**

Best method is actual WireGuard connection from outside your network:

1. Use mobile device with cellular data (not WiFi)
2. Install WireGuard app
3. Import client config
4. Attempt connection
5. Try accessing heater interface: `http://<pi-local-ip>:5000`

**Note:** WireGuard is "stealthy" - it won't respond to port scans without valid keys, so online port checkers may report it as closed even when working.

**What WireGuard Encrypts (Important Clarification):**

This is a **"remote access VPN"** - it provides:
- ✅ Encrypted connection from remote device to home network
- ✅ Secure access to heater controller from anywhere
- ✅ Protection when using public WiFi to connect home
- ✅ Access to all home network devices remotely

This is NOT a **"privacy VPN"** - it does NOT:
- ❌ Encrypt general internet browsing from home devices
- ❌ Hide home network's internet activity from ISP
- ❌ Route all internet traffic through another server
- ❌ Provide anonymity/privacy like commercial VPNs

**Alternative Remote Access Options:**
- **Tailscale** (easiest, zero-config, mesh VPN)
- **Cloudflare Tunnel** (free, custom domain, no port forwarding needed)
- **Port forwarding** (not recommended - exposes services directly to internet)

## Web Interface

Access at: `http://<raspberry-pi-ip>:5000`

### Main Features

#### Control Panel
- **START button**: Begin print cycle with current settings
  - Automatically clears any manual overrides and switches heater/fans to Auto mode
  - Begins warming up phase to reach target temperature
- **PAUSE button**: Pause/Resume print timer
  - Pauses the print time countdown
  - Temperature control remains active (maintains setpoint)
  - Fans stay on if configured
  - Button changes to "RESUME" when paused
- **STOP button**: Gracefully stop print cycle (skips cooldown)
  - Resets print time adjustments
  - Clears pause state
- **EMERGENCY STOP**: Immediately halt all heating/cooling
  - Resets print time adjustments
  - Turns off heater and fans immediately
- **Manual toggles**: Override PID control for heater, fans, and lights
  - Heater/Fan toggles show (Auto) or (Manual) status
  - Manual override allows forcing components on/off during print
  - Useful for safety or troubleshooting
  - Manual overrides are automatically cleared when START is clicked

#### Configuration
- **Target Temperature**: Adjustable mid-print (0-100°C or 32-212°F, 0.5° increments)
- **Print Time**: Hours and minutes
- **Quick Time Adjustment**: ±5min, +15min buttons
- **Enable Fans**: Toggle fan operation during print
- **Enable Logging**: Toggle CSV logging (configure before starting)
- **Settings Persistence**: Automatically saves to JSON file

#### Settings Menu
Access via ⚙️ Settings button in the header.
- **Display Settings**:
  - **Dark Mode**: Toggle between light and dark themes
  - **Temperature Units**: Switch between Celsius (°C) and Fahrenheit (°F)
    - All displays update automatically (current temp, setpoint, sensors, chart)
    - Preference persists across sessions
- **Control Parameters**:
  - **Hysteresis**: Adjustable temperature band (default: 2.0°C, range: 0.5-10°C)
    - Controls heater on/off cycling to prevent relay wear
  - **Cooldown Time**: Adjustable duration (default: 4 hours, range: 0-12 hours)
    - Sets length of gradual cooldown phase after print
  - **Require Preheat Confirmation**: Wait for user confirmation before starting print timer (default: OFF)
    - When enabled: System reaches target temp, shows confirmation modal, waits for user to click "START PRINT"
    - When disabled: System automatically starts print timer after reaching target temp
    - Temperature is maintained while waiting for confirmation
- **Probe Names**:
  - **Rename Temperature Sensors**: Customize display name for each DS18B20 probe
    - Names persist and appear throughout the interface
    - Helps identify sensor locations (e.g., "Top Left", "Bottom Center")

#### Presets
- **Default Presets**:
  - ABS Standard: 60°C, 8h
  - ASA Standard: 65°C, 10h
  - Quick Test: 40°C, 30min
- **Save Custom Presets**: Save current configuration with custom name
- **Load Presets**: Click to load temperature and time settings

#### Status Display
- **Current Temperature**: Average of all working sensors
- **Target Temperature**: Current setpoint
- **Phase**: IDLE, WARMING UP, HEATING, MAINTAINING, or COOLING
- **ETA to Target**: Estimated time to reach target temp (based on heating rate)
- **Print Time Remaining**: Countdown for print duration
  - Shows full configured time during WARMING UP phase (timer hasn't started yet)
  - Counts down during HEATING/MAINTAINING phases
  - Freezes when paused, resumes when unpaused
  - Resets to 0 when print cycle ends
- **Cooldown Time Remaining**: Separate display during cooldown phase
- **Individual Sensors**: List showing each sensor reading

#### Temperature Graph
- Real-time line chart showing temperature vs setpoint
- Updates every 5 seconds
- Displays last 100 data points
- Y-axis label adapts to selected temperature unit (°C or °F)
- Colors automatically adapt for dark mode

#### Browser Notifications
- Requests permission on first load
- Notifies on:
  - Print started
  - Print paused / resumed
  - Target temperature reached (preheat complete)
  - Preheat confirmation required
  - Print timer started (after preheat confirmation)
  - Print stopped
  - Emergency stop
  - Settings saved
  - Preset loaded/saved
  - Time adjustments

#### Temperature Logging
- Enable before starting print
- Logs: Timestamp, Elapsed Time, Current Temp, Setpoint, Heater State, Fan State, Phase
- Download as CSV file with timestamp in filename
- Format: `temperature_log_YYYYMMDD_HHMMSS.csv`

### Fire Safety
When fire detected:
1. Heater and fans immediately shut down
2. Buzzer sounds alarm
3. Red alert banner displays on web interface
4. "RESET FIRE ALARM" button enabled
5. User must press reset button
6. Reset only works if fire sensor clears (pin returns to HIGH)

## Architecture

### Threading Model
Five concurrent threads:
1. **fire_monitor()**: Polls MQ-2 sensor (1s interval), manages emergency shutdown and reset
2. **main_loop()**: Waits for web START command, manages print cycle, PID control, and cooldown
3. **printer_monitor()**: MQTT client for Bambu Lab X1C integration (optional, enabled via settings)
4. **run_flask()**: Web server on port 5000 with WebSocket support
5. **Main thread**: Keeps process alive, handles Ctrl+C cleanup

### State Management
- **Settings persistence**: `heater_settings.json` stores configuration and presets
- **Auto-load on startup**: Loads last settings, turns on lights if previously enabled
- **Thread-safe operations**: Uses `state_lock` and `time_lock` for thread-safe updates
- **Global state variables**: `print_active`, `heater_on`, `fans_on`, `emergency_stop`, etc.

### Temperature Control
- **Multi-sensor averaging**: All working sensors averaged for chamber temperature
- **Custom probe naming**: Each sensor can be renamed for easy identification
- **Individual sensor failure handling**: Operation continues if at least one sensor works
- **PID controller**: Kp=2.0, Ki=0.5, Kd=0.1, output_limits=(-100, 100)
- **Configurable hysteresis**: User-adjustable temperature band (default: 2.0°C, range: 0.5-10°C) prevents relay cycling
- **Mid-print adjustment**: Web interface can change target temperature during operation
- **Manual override**: User can force heater/fans on/off bypassing PID
  - Manual overrides are automatically cleared when START button is clicked
  - System switches back to Auto mode for new print cycle
  - Ensures consistent behavior at start of each print
- **Temperature units**: Display in Celsius or Fahrenheit (all calculations done in Celsius internally)
- **ETA calculation**: Uses last 2 minutes of temperature data to estimate time to target

### Print Cycle Phases
1. **IDLE**: Waiting for START command
2. **WARMING UP**: Initial heating to target temperature before print timer starts
   - PID control active, heater and fans operating
   - Print time displays full configured duration (not counting down)
   - ETA to target temperature shown
   - When temperature reaches within 1°C of setpoint:
     - If "Require Preheat Confirmation" is disabled: Automatically transitions to HEATING phase and starts print timer
     - If "Require Preheat Confirmation" is enabled: Shows confirmation modal, maintains temperature, waits for user to click "START PRINT"
   - Browser notification sent when target temperature reached
3. **HEATING**: Print timer counting down, temperature rising toward or at setpoint
   - Print timer started and actively counting down
   - Temperature control maintains setpoint
   - Can be paused (timer stops, temperature control continues)
4. **MAINTAINING**: Print timer counting down, within 1°C of setpoint
   - Same as HEATING but indicates stable temperature
   - Can be paused (timer stops, temperature control continues)
5. **COOLING**: Configurable gradual cooldown to ambient (default: 4 hours, 5-minute steps)
   - Only runs if print completes normally (not stopped early)
   - Gradual temperature reduction prevents thermal shock

After cooldown, system returns to IDLE and waits for next START command.

### Pause/Resume Functionality
- **PAUSE button** available during HEATING and MAINTAINING phases
- When paused:
  - Print time countdown stops (frozen at current value)
  - Temperature control remains fully active (PID continues)
  - Heater and fans continue operating to maintain setpoint
  - Button changes to "RESUME" (green color)
  - Browser notification sent
- When resumed:
  - Print time countdown continues from where it stopped
  - All other functions continue normally
  - Button changes back to "PAUSE" (gray color)
  - Browser notification sent
- Pause state is cleared when print ends or is stopped

### Bambu Lab X1C Printer Integration (v2.8-alpha)

**Status:** Backend complete, UI pending

The system integrates with Bambu Lab X1C printer via MQTT for automated print workflows.

#### Connection & Configuration
- **Protocol**: MQTT over TLS (port 8883)
- **Authentication**: Username `bblp`, Password = LAN Access Code
- **Enable**: Set `printer_enabled: true` in `heater_settings.json`
- **Required Settings**:
  - `printer_ip`: Printer's local IP address
  - `printer_access_code`: LAN Access Code from printer settings
  - `printer_serial`: Printer serial number/device ID
- **Prerequisites**:
  - Developer Mode enabled on X1C
  - LAN Mode Liveview enabled for camera access

#### Material-Based Auto-Start
When a print starts on the X1C, the system automatically:
1. Detects print start (MQTT gcode_state transition to RUNNING)
2. Identifies material (from AMS tray data or filename pattern matching)
3. Looks up material in `material_mappings` configuration
4. Configures heater with material-specific settings:
   - Target temperature
   - Fan enable/disable
   - Print duration (from printer's estimated time)
5. Triggers heater START automatically

**Material Detection Methods:**
- **Primary**: AMS tray data (`ams.ams[0].tray[X].tray_type`)
- **Fallback**: Filename pattern matching (e.g., `part_PC.gcode` → PC)

**Default Material Mappings:**
- **PC** (Polycarbonate): 60°C, Fans OFF
- **ABS**: 60°C, Fans ON
- **ASA**: 65°C, Fans ON
- **PETG**: 40°C, Fans ON
- **PLA**: 0°C (no heating), Fans OFF

Material mappings are fully customizable via `heater_settings.json`.

**Auto-Start Behavior:**
- Only triggers on idle → printing transition
- Skips if heater already running
- Skips if material not in mappings (logs warning)
- Can be disabled: `auto_start_enabled: false`
- Emits notification when triggered

#### Printer Monitoring
The `printer_monitor()` thread continuously receives MQTT reports containing:
- **Print Status**: Phase (idle/printing/paused/finish), file name, progress (0-100%)
- **Temperatures**: Nozzle, bed, chamber (from printer's sensors)
- **Time Remaining**: Estimated time left in seconds
- **Material Info**: From AMS or slicer metadata
- **Connection Status**: MQTT broker connectivity

All status data is:
- Updated in real-time via WebSocket (`status_update` event)
- Thread-safe (protected by `printer_lock`)
- Available via `/status` API endpoint

#### Printer Control
Three control commands available via API:
- **`POST /printer/pause`**: Pause current print
- **`POST /printer/resume`**: Resume paused print
- **`POST /printer/stop`**: Stop/cancel print

Commands use MQTT publish to `device/{SERIAL}/request` with format:
```json
{
  "print": {
    "sequence_id": "123",
    "command": "pause|resume|stop"
  }
}
```

**UI Integration:** Control buttons planned for dashboard (pending UI implementation)

#### Camera Streaming
On-demand live video feed from X1C camera.

**Technical Approach:**
- **Input**: RTSPS (RTSP over SSL) on port 322
- **SDP File**: Generated dynamically with printer IP and access code
- **Transcoding**: FFmpeg converts RTSPS → MJPEG for browser compatibility
- **Output**: MJPEG stream via `/printer/camera/feed` endpoint
- **Resolution**: 640px width (scaled for Raspberry Pi performance)
- **Control**: User-initiated start/stop (not continuous)

**API Endpoints:**
- **`POST /printer/camera/start`**: Start FFmpeg transcoding process
- **`POST /printer/camera/stop`**: Terminate FFmpeg, stop streaming
- **`GET /printer/camera/feed`**: MJPEG stream (multipart/x-mixed-replace)

**Performance Notes:**
- Camera transcoding uses ~20-40% CPU on Raspberry Pi 4
- On-demand design reduces idle resource usage
- Recommended to stop when not actively monitoring

#### Emergency Integration
**Fire Alarm:** When MQ-2 sensor detects fire, system:
1. Shuts down heater and fans (existing behavior)
2. **Sends MQTT stop command to printer** (new)
3. Sounds buzzer alarm
4. Locks UI controls

**Emergency Stop Button:** User-triggered emergency stop:
1. Halts heater immediately
2. **Sends MQTT stop command to printer** (new)
3. Resets all state flags
4. Returns message: "Emergency stop activated - heater and printer stopped"

This ensures both systems shut down during emergencies.

#### Settings Structure
Printer configuration in `heater_settings.json`:
```json
{
  "printer_enabled": false,
  "printer_ip": "192.168.1.253",
  "printer_access_code": "216e7b9b",
  "printer_serial": "00M00A340600040",
  "auto_start_enabled": true,
  "material_mappings": {
    "PC": {"temp": 60, "fans": false},
    "ABS": {"temp": 60, "fans": true},
    "ASA": {"temp": 65, "fans": true},
    "PETG": {"temp": 40, "fans": true},
    "PLA": {"temp": 0, "fans": false}
  }
}
```

#### State Variables
New global state for printer integration:
- `printer_mqtt_client`: MQTT client instance
- `printer_connected`: Boolean connection status
- `printer_status`: Dict with phase, file, material, progress, temps, time
- `printer_lock`: Thread safety lock
- `mqtt_sequence_id`: Command sequence counter
- `camera_process`: FFmpeg subprocess handle
- `camera_streaming`: Boolean camera state
- `camera_lock`: Camera thread safety

Added to `status_data` dict for WebSocket updates:
- `printer_connected`, `printer_phase`, `printer_file`, `printer_material`
- `printer_progress`, `printer_time_remaining`
- `printer_nozzle_temp`, `printer_bed_temp`, `printer_chamber_temp`
- `camera_streaming`

#### Testing & Documentation
**See:**
- `PRINTER_TESTING_GUIDE.md` - Comprehensive backend testing procedures
- `IMPLEMENTATION_STATUS.md` - Technical details and code locations

**Current Status:**
- ✅ Backend implementation complete
- ⏳ User testing in progress
- ⏳ UI integration pending
- ⏳ Documentation updates ongoing

### Data Tracking
- **Temperature history**: Last 1000 data points stored in memory
- **Update interval**: 5 seconds (faster than original for better graphing)
- **CSV logging**: Optional, enabled per print cycle
- **History endpoint**: Returns last 100 points for charting

## GPIO Pin Mapping

```
GPIO 17 → SSR relay (heater control)
GPIO 18 → MQ-2 digital output (fire detection, active LOW)
GPIO 22 → Lights relay
GPIO 27 → Buzzer (alarm)
GPIO 23 → Filtration fan 1
GPIO 24 → Filtration fan 2
```

Uses BCM pin numbering.

## Configuration Constants

Located at top of `x1c_heater.py`:
- `HYSTERESIS = 2.0` - Default temperature band in °C (user-configurable via settings menu)
- `TEMP_UPDATE_INTERVAL = 5` - Update interval in seconds (faster for graphing)
- `COOLDOWN_HOURS = 4` - Default slow cooldown duration in hours (user-configurable via settings menu)
- `COOLDOWN_STEP_INTERVAL = 300` - Cooldown step interval (5 minutes)
- `SETTINGS_FILE = 'heater_settings.json'` - Settings persistence file
- `PRINT_STATE_FILE = 'print_state.json'` - Print state persistence for crash recovery
- `MAX_HISTORY = 1000` - Maximum temperature history data points

## API Endpoints

### Status & Data
- `GET /` - Main web interface (HTML)
- `GET /status` - Current system status (JSON)
- `GET /history` - Temperature history for graphing (JSON)
- `GET /get_settings` - Current settings and presets (JSON)

### Control Commands
- `POST /start` - Start print cycle (begins WARMING UP phase)
- `POST /pause` - Toggle pause/resume for print timer
- `POST /confirm_preheat` - Confirm preheat complete and start print timer
- `POST /stop` - Stop print cycle
- `POST /emergency_stop` - Emergency halt
- `POST /reset` - Reset fire alarm
- `POST /toggle_heater` - Manual heater control
- `POST /toggle_fans` - Manual fan control
- `POST /toggle_lights` - Toggle USB lights

### Configuration
- `POST /save_settings` - Save current configuration
- `POST /save_advanced_settings` - Save advanced settings (hysteresis, cooldown, temp unit, preheat confirmation, probe names)
- `POST /save_preset` - Save new preset
- `POST /load_preset` - Load existing preset
- `POST /adjust_time` - Add/subtract print time

### Logging
- `GET /download_log` - Download CSV temperature log

## File Structure

```
x1c_controller/
├── .git/                    # Git repository (version control)
├── .gitignore              # Git ignore rules
├── x1c_heater.py           # Main application
├── requirements.txt         # Python dependencies
├── README.md               # Project overview and quick start
├── CLAUDE.md               # This file - AI assistant guidance
├── SERVICE_MANAGEMENT.md   # Systemd service commands & deployment
├── TODO.md                 # Hardware tasks and future features
├── heater_settings.json    # Auto-generated settings (not in git)
└── venv/                   # Virtual environment (not in git)
```

## Systemd Service Management

The application runs as a systemd service for persistence and reliability. See `SERVICE_MANAGEMENT.md` for:
- Complete service setup instructions
- Service control commands (start, stop, restart, status)
- Viewing logs
- Deploying code updates
- Troubleshooting guide

**Quick commands:**
```bash
# Restart after code changes
sudo systemctl restart x1c-heater

# Check status
sudo systemctl status x1c-heater

# View logs
sudo journalctl -u x1c-heater.service -n 50

# Watch live logs
sudo journalctl -u x1c-heater.service -f
```

## Safety Features

- **Fire detection with web reset**: Continuous monitoring, immediate shutdown, alarm, reset only when fire cleared
- **Emergency stop button**: Immediately halts all operations
- **Manual overrides**: Force heater/fans off during operation
- **Sensor failure tolerance**: Continues with partial sensors
- **Thread-safe operations**: Prevents race conditions
- **Automatic cooldown**: Gradual temperature reduction prevents thermal shock
- **Settings validation**: Input validation on web interface
- **Graceful shutdown**: Ctrl+C properly cleans up GPIO and turns off all outputs

## Development Notes

### Fire Sensor (MQ-2)
**Active LOW** configuration:
- Normal (no fire): GPIO reads HIGH
- Fire detected: GPIO reads LOW

Verify this matches your specific MQ-2 module.

### Manual Override Behavior
When user toggles heater/fans via web interface:
- Sets `heater_manual_override` or `fans_manual_override` flag
- PID control is bypassed for that component
- Component state follows toggle switch
- Remains in manual mode until print cycle ends
- Use case: Force heater off if temperature overshoots, or turn off fans if too noisy

### Settings Persistence
- Saved to `heater_settings.json` in working directory
- Updated on: Save Settings button, Save Advanced Settings, light toggle, preset save/load
- Loaded on startup
- Contains:
  - Basic settings: desired_temp, print_hours, print_minutes, fans_enabled, lights_enabled, logging_enabled
  - Advanced settings: hysteresis, cooldown_hours, temp_unit, require_preheat_confirmation, probe_names
  - Presets array
- Note: `heater_settings.json` is excluded from git (contains user-specific configuration)

### Lights Control

The system uses a relay on GPIO pin 22 for independent lights control:
- **Simple toggle**: User can turn lights on/off at any time via web interface
- **State persistence**: Lights state saved to `heater_settings.json`
- **Crash recovery**: Lights restore to last saved state after restart
- **Independent operation**: Lights remain on/off regardless of program phase (heating, cooling, emergency stop, etc.)
- **GPIO state detection**: On startup, syncs hardware state with saved preference

### Browser Compatibility
- Tested on modern Chrome, Firefox, Safari, Edge
- Requires JavaScript enabled
- Notification API support optional but recommended
- localStorage used for:
  - Dark mode preference
  - Temperature unit preference (C/F)
  - Hysteresis and cooldown settings (synced with backend)
- Chart.js loaded from CDN

### Performance
- Temperature updates: Every 5 seconds
- WebSocket status updates: Real-time push (replaces HTTP polling)
- History updates: Every 5 seconds (web interface)
- Fire monitoring: Every 1 second
- Cooldown steps: Every 5 minutes
- Optimistic UI lock duration: 6 seconds (prevents stale WebSocket data from overriding user actions)

## Version Control

The project uses Git for version control and is hosted on GitHub.

**Repository**: https://github.com/BrofessorProman/x1c_controller

### Git Workflow
```bash
# Check status
git status

# Stage changes
git add x1c_heater.py  # or git add -A for all changes

# Commit changes
git commit -m "Description of changes"

# Push to GitHub (SSH configured)
git push

# View commit history
git log --oneline

# See what changed
git diff
```

### What's Tracked in Git
- ✅ Source code (x1c_heater.py)
- ✅ Documentation (*.md files)
- ✅ Dependencies (requirements.txt)
- ✅ Git configuration (.gitignore)

### What's Excluded (.gitignore)
- ❌ User settings (heater_settings.json)
- ❌ Log files (temperature_log*.csv)
- ❌ Virtual environment (venv/)
- ❌ Python cache (__pycache__/)

## Deployment Workflow

### Initial Setup
1. Clone from GitHub: `git clone git@github.com:BrofessorProman/x1c_controller.git`
2. Navigate to directory: `cd x1c_controller`
3. Create virtual environment: `python3 -m venv venv`
4. Install dependencies: `venv/bin/pip install -r requirements.txt`
5. Configure GPIO permissions: `sudo usermod -a -G gpio pi`
6. Enable 1-Wire interface: `sudo raspi-config`
7. Set up systemd service (see SERVICE_MANAGEMENT.md)
8. Configure WireGuard VPN for remote access (optional)

### Making Code Changes
1. Edit code locally or on Pi
2. If using virtual environment, ensure packages are installed in venv
3. Test manually first: `venv/bin/python3 x1c_heater.py`
4. Commit changes: `git add -A && git commit -m "Description"`
5. Push to GitHub: `git push`
6. Deploy to service: `sudo systemctl restart x1c-heater`
7. Monitor logs: `sudo journalctl -u x1c-heater.service -f`

### Updating Dependencies
1. Update requirements.txt
2. Activate venv: `source venv/bin/activate`
3. Install: `pip install -r requirements.txt`
4. Deactivate: `deactivate`
5. Restart service: `sudo systemctl restart x1c-heater`

## Troubleshooting

### Service Won't Start
```bash
# Check detailed logs
sudo journalctl -u x1c-heater.service -n 100

# Common issues:
# - Wrong Python path in service file (use venv path)
# - Missing dependencies in venv
# - GPIO permission denied (add user to gpio group)
# - Sensors not connected
# - 1-Wire not enabled
```

### Web Interface Not Accessible
```bash
# Check if service is running
sudo systemctl status x1c-heater

# Check if Flask is listening
sudo netstat -tulpn | grep 5000

# Test locally first
curl http://localhost:5000

# Check firewall
sudo ufw status
```

### Temperature Sensors Not Found
```bash
# Check 1-Wire is enabled
ls /sys/bus/w1/devices/
# Should show 28-* directories for each sensor

# Enable in raspi-config
sudo raspi-config
# Interface Options → 1-Wire → Enable
```

### Lights Relay Not Working
```bash
# Check if relay is connected to GPIO 22 (Physical Pin 15)
# Verify wiring:
# - Relay signal pin → GPIO 22
# - Relay VCC → 5V or 3.3V (depending on relay)
# - Relay GND → Ground

# Test GPIO output manually
gpio -g write 22 1  # Turn on
gpio -g write 22 0  # Turn off

# Check logs for errors
sudo journalctl -u x1c-heater.service -n 50 | grep -i light
```

## Remote Access Security

When setting up remote access:
- ✅ Use WireGuard VPN (recommended) or Tailscale
- ✅ Keep Raspberry Pi updated: `sudo apt update && sudo apt upgrade`
- ✅ Use strong passwords or SSH keys
- ✅ Don't expose Flask port directly to internet
- ✅ Monitor logs regularly
- ❌ Don't use simple port forwarding without VPN
- ❌ Don't share VPN private keys
- ❌ Don't use default passwords

## Testing

### Manual Testing Checklist
- [ ] All sensors detected on startup
- [ ] Web interface loads
- [ ] Can set temperature and start cycle
- [ ] Heater turns on when below setpoint
- [ ] Heater turns off when above setpoint
- [ ] Manual toggles work
- [ ] Temperature graph updates
- [ ] Preset loading works
- [ ] Settings persistence works
- [ ] Settings menu opens and closes
- [ ] Dark mode toggle works
- [ ] Temperature unit conversion (C/F) works
- [ ] Probe renaming persists
- [ ] Custom hysteresis value works
- [ ] Custom cooldown time works
- [ ] Fire alarm triggers (test safely!)
- [ ] Emergency stop works
- [ ] Cooldown completes
- [ ] CSV logging works

### Service Testing
- [ ] Service starts on boot
- [ ] Service restarts after crash (simulate with `kill`)
- [ ] Logs are captured in journald
- [ ] GPIO permissions work without sudo

## Future Enhancements

See `TODO.md` for planned improvements including:
- Relay-based USB light control
- Email/SMS notifications
- Historical data persistence
- Multiple chamber support
- Additional safety features

## Support & Documentation

- **Service Management**: See SERVICE_MANAGEMENT.md
- **Hardware TODO**: See TODO.md
- **Code Issues**: Check logs first: `sudo journalctl -u x1c-heater.service -n 100`
- **Remote Access**: WireGuard setup guide provided in session
- **Web Interface**: Built-in help tooltips (planned)

## Version History

**Current Version**: 2.9-alpha (Bambu Lab X1C Integration - Backend Complete, UI Pending)

**⚠️ WORK IN PROGRESS**: Printer integration features are functional but the web UI has not been implemented yet. Users must configure printer settings manually in `heater_settings.json`. UI implementation planned for next release.

- **NEW**: Bambu Lab X1C printer integration via MQTT
  - MQTT client thread connects to printer broker (port 8883, TLS)
  - Real-time monitoring of print status, progress, temperatures
  - Material detection from AMS tray data or filename
  - Configurable material mappings (temp + fan settings per material)
- **NEW**: Material-based auto-start automation
  - Detects when print starts on X1C (MQTT state transition)
  - Identifies material (PC, ABS, ASA, PETG, PLA)
  - Auto-configures heater: target temp, fans, print duration
  - Triggers heater START automatically (user-configurable)
  - Default mappings: PC (60°C, fans off), ABS/ASA (60-65°C, fans on)
- **NEW**: Auto-stop when print ends or is cancelled
  - Detects `FINISH` state (print completed) → triggers cooldown phase
  - Detects `FAILED` state (user cancelled in Bambu Studio) → immediate stop, no cooldown
  - Uses raw `gcode_state` tracking to avoid false triggers from normal state transitions
  - UI buttons lock with processing indicator when backend triggers stop
- **NEW**: Printer control API endpoints
  - `/printer/pause` - Pause current print via MQTT
  - `/printer/resume` - Resume paused print
  - `/printer/stop` - Stop/cancel print
  - MQTT command publishing with sequence ID tracking
- **NEW**: Always-on camera streaming from X1C
  - Camera starts automatically when printer is configured and enabled
  - Background `camera_monitor` thread manages FFmpeg lifecycle
  - Auto-restarts if FFmpeg dies or stream disconnects
  - Resolution: 720p @ 10fps (~35% CPU on Raspberry Pi 4)
  - Quality setting: q:v 5 (good balance of quality and performance)
  - API: `/printer/camera/feed` (always available), `/printer/camera/status`
  - Removed manual start/stop - camera runs continuously when printer configured
- **NEW**: Emergency integration with printer
  - Fire alarm now stops both heater AND printer via MQTT
  - Emergency stop button stops both systems
  - Enhanced safety: dual-system shutdown during emergencies
- **NEW**: Printer state variables and WebSocket updates
  - Real-time printer status via WebSocket (phase, file, material, progress, temps)
  - Thread-safe state management with `printer_lock`
  - Camera streaming status tracking
  - Processing lock event for UI button coordination
- **IMPROVED**: Security - removed hardcoded printer credentials
  - Default settings now have empty strings for printer_ip, printer_access_code, printer_serial
  - Users must configure via settings file (UI configuration planned)
  - Safe for public GitHub repository
- **NEW**: Documentation for testing and implementation
  - `PRINTER_TESTING_GUIDE.md` - Comprehensive testing procedures
  - `IMPLEMENTATION_STATUS.md` - Technical implementation details
- **STATUS**: Backend complete and tested, UI implementation pending
- All features from version 2.7 and earlier

**Version 2.7**: (Lights Relay Implementation)
- **NEW**: GPIO-based lights relay on pin 22 (Physical Pin 15)
  - Direct GPIO control replaces unreliable uhubctl USB hub control
  - Simple on/off relay operation for lights
  - More reliable and consistent than USB hub power switching
- **NEW**: Independent lights operation
  - Lights remain on/off regardless of program phase (heating, cooling, emergency stop, shutdown)
  - User has full manual control via web interface toggle
  - Lights maintain state during emergency stop and system shutdown
- **IMPROVED**: Lights state persistence and recovery
  - State saved to `heater_settings.json` (lights_enabled field)
  - GPIO state detection on startup syncs hardware with saved preference
  - Lights restore to last saved state after service restart or crash
- **REMOVED**: All uhubctl USB hub control logic
  - Deleted `USB_HUB_LOCATION`, `USB_HUB_PORT`, `USB_CONTROL_ENABLED` constants
  - Removed `get_usb_power_status()` function
  - Renamed `set_usb_power()` to `set_lights()` for clarity
  - Removed uhubctl from software dependencies
- All features from version 2.6 and earlier

**Version 2.6**: (Print State Persistence & Crash Recovery)
- **NEW**: Comprehensive print state persistence system
  - Automatically saves print state every 10 seconds during heating/maintaining phases
  - Saves state every 5 minutes during cooldown phase
  - State includes: phase, timing info, pause state, target temp, manual overrides, hardware states
  - Resume banner appears on page load if crash detected
  - User can choose to resume or abort interrupted print
- **NEW**: Intelligent crash recovery with staleness validation
  - Different validation for heating vs cooling phases
  - Heating/maintaining: validates against remaining print time (+ 5min grace period)
  - Cooling: validates state is within max cooldown duration (12 hours)
  - Auto-aborts stale states that can't be meaningfully resumed
- **NEW**: Resume logic preserves exact timing and state
  - Calculates elapsed time since crash and adjusts start time accordingly
  - Shows correct remaining time when resuming
  - Preserves pause state if print was paused during crash
  - Restores manual override flags (heater/fans manual/auto mode)
  - Restores actual hardware states (heater/fans on/off)
- **NEW**: Cooling phase crash recovery fully working
  - Skips warmup phase when resuming (no redundant heating)
  - Calculates remaining cooldown time and continues from that point
  - Shows correct cooldown time remaining in UI immediately
  - Fixed stale detection bug that was deleting valid cooling states
- **NEW**: API routes for crash recovery
  - `/resume_print` - Resume interrupted print cycle
  - `/abort_resume` - Discard saved state and return to idle
- **IMPROVED**: Probe name updates are instant
  - Avoids slow sensor reads when saving settings
  - Updates names in existing sensor data structure
  - Emits immediate WebSocket update for instant UI refresh
- **IMPROVED**: Processing spinner cleanup
  - Added `clearAllProcessingStates()` function
  - Clears spinners from all buttons before applying new ones
  - Prevents leftover spinners when clicking buttons rapidly
  - Ensures only one button shows spinner at a time
- **IMPROVED**: Optimistic notifications for pause/resume
  - "Print Paused" notification appears immediately when clicking PAUSE
  - "Print Resumed" notification appears immediately when clicking RESUME
  - No delay waiting for backend response
- **FIXED**: Heater UI sync during cooling phase
  - Cooling loop now emits WebSocket update when turning off heater
  - UI shows correct heater state in real-time
  - Fixes issue where UI showed stale "ON" state until something else triggered an emit
- **FIXED**: Backward compatibility for old state files
  - Gracefully handles state files without manual override fields
  - Defaults to Auto mode and OFF state if fields missing
- All features from version 2.5 and earlier

**Version 2.5**: (WebSocket Real-Time Communication & UI Polish)
- **NEW**: Real-time WebSocket communication replaces HTTP polling
  - Migrated from 2-second polling to instant WebSocket push updates
  - Server-to-browser latency reduced from 0-2000ms to <50ms
  - Uses Flask-SocketIO and Socket.IO client library
  - Auto-reconnect with fallback polling if WebSocket fails
- **NEW**: Advanced anti-flicker system with two-layer protection
  - Layer 1: Message sequence numbering - backend assigns incrementing IDs to each WebSocket message
  - Layer 2: Extended optimistic lock (6 seconds) prevents stale data from overriding user actions
  - Frontend validates sequence numbers and drops stale messages permanently
  - Eliminates UI flickering when clicking buttons or toggling controls
- **NEW**: Visual processing indicators for all action buttons
  - Animated spinner appears on button being clicked (START/STOP/PAUSE/EMERGENCY STOP)
  - Conflicting buttons temporarily dimmed and disabled during processing (6-second window)
  - Prevents race conditions - user cannot click conflicting actions too quickly
  - Clear visual feedback that action is being processed
- **NEW**: Optimistic UI updates for instant feedback
  - START/STOP/PAUSE/EMERGENCY STOP buttons update immediately on click
  - Heater/Fans/Lights indicators update instantly
  - All buttons have optimistic updates for consistent UX
  - Error handling reverts changes on failure
- **IMPROVED**: Faster idle loop response (1 second vs 5 seconds)
  - Reduces max delay from clicking START to processing from 5s to 1s
  - More responsive to user actions
- **IMPROVED**: Temperature graph updates restored (5-second polling)
  - Fixed after WebSocket migration removed original polling interval
- **IMPROVED**: Enhanced disabled button styling
  - 40% opacity with gray background (#666) and text (#999)
  - Much clearer visual indication when buttons are disabled
- **IMPROVED**: Heater activates immediately when START clicked
  - Backend turns heater on before first WebSocket emit
  - Reduces perceived delay in UI feedback
- **FIXED**: JavaScript scoping bug in WebSocket handler
  - Button references were scoped incorrectly, causing ReferenceError
  - Error prevented entire WebSocket handler from completing
  - Fixed by moving element references to top of handler function
- **FIXED**: UI flickering completely eliminated
  - Root cause: stale WebSocket messages from idle loop arriving after user clicks
  - Solution: message sequence validation drops any out-of-order updates
  - Optimistic lock extended to 6 seconds to cover backend processing time
- All features from version 2.3.1 and earlier

**Version 2.3.1**: (Bug Fix - Temperature Sensor Display)
- **NEW**: Skip Preheat setting - option to bypass warming up phase and start timer immediately
- **NEW**: Cooldown Target Temperature setting - user-configurable target temp for cooldown phase (default 21°C/70°F)
  - Replaces unreliable startup-based ambient temperature detection
  - Ensures consistent and predictable cooldown behavior
- **NEW**: GPIO state detection on service restart - syncs software state with hardware state on startup
  - Prevents mismatch between UI and actual heater/fan states after service restart
  - Logs warning when outputs are detected as ON during startup
- **NEW**: Comprehensive fire alarm UI lockdown - all controls disabled except RESET button during fire alarm
  - Blocks START, PAUSE, STOP, EMERGENCY STOP buttons
  - Disables heater, fans, and lights toggles (with JavaScript enforcement)
  - Disables all configuration inputs, time adjustments, presets, and Settings button
  - Visual dimming (40% opacity) to indicate locked state
  - Critical safety feature preventing user from restarting heater during fire
- **NEW**: Continuous temperature reading while idle - probes update every 5 seconds even before START is clicked
  - Allows user to monitor chamber temperature before starting print
- **NEW**: In-page modal notifications - replaced browser notifications with custom toast notifications
  - Works on HTTP/IP addresses (browser notifications require HTTPS)
  - Auto-dismiss after 3-5 seconds with blur overlay effect
- **FIXED**: Cooldown phase crash - TypeError with float object in range() function
  - Added int() conversion to cooldown step calculation
- **FIXED**: Hysteresis returning None - added safety checks to use default value if setting is null
- **FIXED**: Fans not shutting off in manual mode - STOP/EMERGENCY STOP now force all outputs off regardless of manual override
- **IMPROVED**: Print time clears to 0 when entering cooldown phase
- **IMPROVED**: Phase display shows "WARMING UP" instead of "WARMING_UP" (cosmetic fix)
- **IMPROVED**: Emergency stop message refers to "fans" instead of "heating and cooling fans"
- **IMPROVED**: Warming up phase skipped if already at or above target temperature
- **FIXED**: Temperature probes not displaying on main interface
  - Emergency Stop button was missing id="emergency-stop-btn"
  - JavaScript error prevented updateStatus() from completing
  - Sensor list now displays correctly with proper temperature readings
- All features from version 2.2 and earlier

**Version 2.2**: (Pause, Preheat & Control Improvements)
- Pause/Resume functionality - pause print timer while maintaining temperature control
- Warming up phase - reaches target temperature before starting print timer
- Optional preheat confirmation - wait for user confirmation before starting print timer
- Preheat confirmation modal with browser notification
- Manual overrides automatically clear when START is clicked
- Print time resets properly when STOP or EMERGENCY STOP is clicked
- Print time displays correctly reset to 0 when print cycle ends
- All features from version 2.1

**Version 2.1**: (Advanced Settings & Version Control)
- Comprehensive settings modal with dark mode, temp units, hysteresis, cooldown, and probe renaming
- Temperature unit switching (Celsius/Fahrenheit) with automatic conversion
- User-configurable hysteresis (0.5-10°C) for heater control
- User-configurable cooldown time (0-12 hours)
- Custom probe naming for easy sensor identification
- Git version control with GitHub integration (SSH)
- Fully web-controlled interface
- Systemd service integration
- Remote access support
- Real-time temperature graphing
- Browser notifications
- CSV logging
- Preset management
- Settings persistence

**Version 2.0**: (Web-based with systemd service)
- Fully web-controlled interface
- Systemd service integration
- Remote access support
- Temperature graphing
- Dark mode
- Browser notifications
- CSV logging
- Preset management
- Settings persistence

**Version 1.0**: (Command-line based)
- Terminal-only interface
- Manual start/stop
- Basic temperature control
