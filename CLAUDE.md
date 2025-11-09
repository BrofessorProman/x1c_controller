# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Raspberry Pi-based temperature controller for a 3D printer chamber heater with a comprehensive Flask web interface. It uses PID control to maintain target temperature using multiple DS18B20 sensors, controls a heater via SSR relay, monitors for fire with an MQ-2 sensor, and manages USB-powered lights and filtration fans.

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
- **Buzzer**: GPIO pin 27 for fire alarm
- **Filtration fans**: GPIO pins 23 and 24
- **USB lights control**: Either via uhubctl (GPIO pin 22 if using relay - see TODO.md)

## Software Dependencies

Install Python packages:
```bash
pip install -r requirements.txt
```

Or manually:
```bash
pip install w1thermsensor simple-pid RPi.GPIO Flask
```

System dependency for USB control (optional):
```bash
sudo apt install uhubctl
```

Enable 1-Wire interface:
```bash
sudo raspi-config
# Navigate to: Interface Options → 1-Wire → Enable
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

For secure remote access from outside your home network, WireGuard VPN is recommended. A complete setup guide for WireGuard is provided in this documentation, allowing access to your entire home network including the heater controller and any future devices.

**Alternative options:**
- Tailscale (easiest, zero-config)
- Cloudflare Tunnel (free, custom domain)
- Port forwarding (not recommended for security)

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
Four concurrent threads:
1. **fire_monitor()**: Polls MQ-2 sensor (1s interval), manages emergency shutdown and reset
2. **main_loop()**: Waits for web START command, manages print cycle, PID control, and cooldown
3. **run_flask()**: Web server on port 5000
4. **Main thread**: Keeps process alive, handles Ctrl+C cleanup

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

### Data Tracking
- **Temperature history**: Last 1000 data points stored in memory
- **Update interval**: 5 seconds (faster than original for better graphing)
- **CSV logging**: Optional, enabled per print cycle
- **History endpoint**: Returns last 100 points for charting

## GPIO Pin Mapping

```
GPIO 17 → SSR relay (heater control)
GPIO 18 → MQ-2 digital output (fire detection, active LOW)
GPIO 22 → Optional: Lights relay (if not using uhubctl)
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
- `MAX_HISTORY = 1000` - Maximum temperature history data points
- `USB_HUB_LOCATION = '1-1'` - USB hub location for uhubctl
- `USB_HUB_PORT = '2'` - USB port number for lights
- `USB_CONTROL_ENABLED = True` - Enable/disable USB control

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

### USB Light Control Options

**Option 1: uhubctl (Current default)**
- Configure `USB_HUB_LOCATION` and `USB_HUB_PORT` in x1c_heater.py
- Run `sudo uhubctl` to find your hub/port configuration
- May require sudo or udev rules for permissions
- Set `USB_CONTROL_ENABLED = False` to disable if not needed

**Option 2: Relay (Recommended - See TODO.md)**
- Add relay on GPIO pin 22
- More reliable than uhubctl
- No USB hub configuration needed
- Simple on/off control like heater relay

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
- Status polling: Every 2 seconds (web interface)
- History updates: Every 5 seconds (web interface)
- Fire monitoring: Every 1 second
- Cooldown steps: Every 5 minutes

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

### USB Light Control Fails
```bash
# Option 1: Configure uhubctl
sudo uhubctl  # Find your hub/port
# Update USB_HUB_LOCATION and USB_HUB_PORT in code

# Option 2: Disable USB control
# Set USB_CONTROL_ENABLED = False in x1c_heater.py

# Option 3: Use relay instead (see TODO.md)
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

**Current Version**: 2.4 (WebSocket Real-Time Communication - In Progress)
- **NEW**: Real-time WebSocket communication replaces HTTP polling
  - Migrated from 2-second polling to instant WebSocket push updates
  - Server-to-browser latency reduced from 0-2000ms to <50ms
  - Uses Flask-SocketIO and Socket.IO client library
  - Auto-reconnect with fallback polling if WebSocket fails
- **NEW**: Optimistic UI updates for instant button/toggle feedback
  - START/STOP/PAUSE buttons update immediately on click
  - Heater/Fans/Lights indicators update instantly
  - 2-second optimistic lock prevents WebSocket from overriding user actions
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
- **KNOWN ISSUE**: UI flickering on button clicks (High Priority)
  - Buttons and toggles flicker when clicked despite optimistic lock
  - Optimistic updates being overridden by stale WebSocket data
  - Investigating root cause for next session (see TODO.md)
- **KNOWN ISSUE**: STOP button doesn't immediately update heater/fans indicators
  - Missing optimistic update in stopPrint() function
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
