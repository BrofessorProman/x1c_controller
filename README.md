# X1C Chamber Heater Controller

A comprehensive Raspberry Pi-based temperature controller for 3D printer chamber heating with a modern web interface.

![Status](https://img.shields.io/badge/status-production-brightgreen)
![Platform](https://img.shields.io/badge/platform-Raspberry%20Pi-red)
![Python](https://img.shields.io/badge/python-3.7+-blue)

## Features

- 🌡️ **PID Temperature Control** - Precise chamber temperature management
- 🌐 **Web Interface** - Full control via browser (desktop/mobile)
- ⚡ **WebSocket Real-Time Updates** - Instant UI response (<50ms latency) with optimistic updates
- 📊 **Real-time Graphing** - Temperature history with Chart.js
- 🔥 **Fire Safety** - MQ-2 sensor with automatic shutdown
- 💾 **Data Logging** - Export temperature logs as CSV
- 🎨 **Dark Mode** - Eye-friendly interface with settings modal
- 🌡️ **Temperature Units** - Switch between Celsius and Fahrenheit
- ⚙️ **Advanced Settings** - Configurable hysteresis, cooldown time, cooldown target temp, probe naming, and skip preheat
- 🔔 **In-Page Notifications** - Custom modal notifications (works on HTTP)
- 📱 **Mobile Friendly** - Responsive design for phones/tablets
- 🔒 **Remote Access** - WireGuard VPN support
- 💾 **Preset Configs** - Save and load common settings
- 🔄 **Auto-Start** - Runs as systemd service
- ⏸️ **Pause/Resume** - Pause print timer while maintaining temperature
- 🔥 **Preheat Phase** - Reaches target temp before starting print timer (optional skip)
- ✅ **Preheat Confirmation** - Optional wait for user approval to start print
- 🔐 **Fire Alarm Lockdown** - All controls disabled during fire emergency
- 🔄 **GPIO State Sync** - Detects hardware state on service restart

## Quick Start

### 1. Hardware Setup

**Required Components:**
- Raspberry Pi (3/4/Zero 2W)
- DS18B20 temperature sensors (1 or more)
- SSR relay (for heater control)
- MQ-2 fire/smoke sensor
- Buzzer (for alarm)
- 5V relay modules (for fans, optional for lights)

**GPIO Connections:**
```
GPIO 17 → SSR Relay (Heater)
GPIO 18 → MQ-2 Sensor (Fire Detection)
GPIO 22 → Lights Relay (optional)
GPIO 23 → Fan 1 Relay
GPIO 24 → Fan 2 Relay
GPIO 27 → Buzzer
```

### 2. Software Installation

```bash
# Clone repository
cd ~
git clone git@github.com:BrofessorProman/x1c_controller.git
# Or use HTTPS: git clone https://github.com/BrofessorProman/x1c_controller.git
cd x1c_controller

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Enable 1-Wire for temperature sensors
sudo raspi-config
# Interface Options → 1-Wire → Enable → Reboot
```

### 3. Configuration

Edit `x1c_heater.py` to configure:
- USB hub settings (if using uhubctl for lights)
- PID tuning parameters
- Default temperatures

### 4. Test Run

```bash
# Activate virtual environment
source venv/bin/activate

# Run the controller
python3 x1c_heater.py

# Access web interface
# Open browser: http://<pi-ip-address>:5000
```

### 5. Setup as Service (Recommended)

```bash
# Create service file
sudo nano /etc/systemd/system/x1c-heater.service
```

Paste this configuration (update paths as needed):
```ini
[Unit]
Description=X1C Chamber Heater Controller
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/x1c_controller
ExecStart=/home/pi/x1c_controller/venv/bin/python3 /home/pi/x1c_controller/x1c_heater.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SupplementaryGroups=gpio

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable x1c-heater
sudo systemctl start x1c-heater

# Check status
sudo systemctl status x1c-heater
```

## Usage

### Web Interface

Access at `http://<raspberry-pi-ip>:5000`

**Control Panel:**
- START button - begins warming up phase
- PAUSE/RESUME button - pause print timer while maintaining temperature
- STOP button - gracefully stop print cycle
- EMERGENCY STOP - immediate halt of all systems
- Manual toggles for heater, fans, and lights (auto-cleared on START)
- Real-time status display

**Configuration:**
- Set target temperature (adjustable mid-print)
- Configure print duration
- Quick time adjustments (±5min, +15min)
- Enable/disable logging
- Optional preheat confirmation (Settings menu)

**Monitoring:**
- Current vs target temperature
- Phase indicator (IDLE/WARMING UP/HEATING/MAINTAINING/COOLING)
- ETA to target temperature
- Print time remaining (frozen when paused)
- Individual sensor readings
- Real-time temperature graph

**Presets:**
- ABS Standard (60°C, 8h)
- ASA Standard (65°C, 10h)
- Quick Test (40°C, 30min)
- Save custom presets

### Service Management

```bash
# Restart service (after code changes)
sudo systemctl restart x1c-heater

# View logs
sudo journalctl -u x1c-heater.service -f

# Stop service
sudo systemctl stop x1c-heater

# Check status
sudo systemctl status x1c-heater
```

See `SERVICE_MANAGEMENT.md` for complete service documentation.

## Remote Access

For secure remote access from outside your home network, set up WireGuard VPN:

1. Install WireGuard on spare Raspberry Pi
2. Configure router port forwarding (UDP 51820)
3. Install WireGuard app on phone/tablet
4. Scan QR code to connect
5. Access controller at local IP while connected to VPN

**Alternative Options:**
- Tailscale (easiest, zero-config)
- Cloudflare Tunnel (custom domain support)

Complete setup guides are available in project documentation.

## Safety Features

- **Fire Detection**: Continuous MQ-2 monitoring with automatic shutdown
- **Emergency Stop**: Immediate halt of all heating/cooling
- **Manual Overrides**: Force heater/fans off if needed
- **Sensor Redundancy**: Continues with partial sensor failures
- **Gradual Cooldown**: Prevents thermal shock to printer/chamber
- **Auto-Recovery**: Service restarts automatically if crashed

## Temperature Logging

Enable logging before starting a print cycle to capture:
- Timestamp
- Elapsed time
- Current temperature
- Setpoint
- Heater state
- Fan state
- Phase

Download logs as CSV from the web interface.

## Troubleshooting

### Service Won't Start

```bash
# Check logs for errors
sudo journalctl -u x1c-heater.service -n 50

# Common fixes:
# - Verify venv path in service file
# - Check GPIO permissions: sudo usermod -a -G gpio pi
# - Ensure 1-Wire is enabled
# - Check sensor connections
```

### Can't Access Web Interface

```bash
# Verify service is running
sudo systemctl status x1c-heater

# Check if Flask is listening
sudo netstat -tulpn | grep 5000

# Test locally
curl http://localhost:5000
```

### Sensors Not Detected

```bash
# Check 1-Wire devices
ls /sys/bus/w1/devices/
# Should show 28-* directories

# Enable 1-Wire
sudo raspi-config
```

See `SERVICE_MANAGEMENT.md` and `CLAUDE.md` for detailed troubleshooting.

## Documentation

- **CLAUDE.md** - Complete technical documentation
- **SERVICE_MANAGEMENT.md** - Service commands and deployment
- **TODO.md** - Planned improvements and hardware tasks

## Configuration

Edit these constants in `x1c_heater.py`:

```python
HYSTERESIS = 2.0              # Temperature band (°C)
TEMP_UPDATE_INTERVAL = 5      # Update frequency (seconds)
COOLDOWN_HOURS = 4            # Cooldown duration (hours)
USB_CONTROL_ENABLED = True    # Enable/disable USB light control
```

## Default Presets

- **ABS Standard**: 60°C for 8 hours
- **ASA Standard**: 65°C for 10 hours
- **Quick Test**: 40°C for 30 minutes

Add custom presets via web interface.

## Development

### Making Changes

```bash
# Stop service
sudo systemctl stop x1c-heater

# Make changes
nano x1c_heater.py

# Test manually
source venv/bin/activate
python3 x1c_heater.py

# Deploy changes
sudo systemctl start x1c-heater
```

### Adding Features

1. Edit `x1c_heater.py`
2. Update `requirements.txt` if adding packages
3. Test manually first
4. Update documentation
5. Restart service

## File Structure

```
x1c_controller/
├── x1c_heater.py           # Main application
├── requirements.txt         # Python dependencies
├── heater_settings.json    # Auto-generated settings
├── README.md               # This file
├── CLAUDE.md               # Technical documentation
├── SERVICE_MANAGEMENT.md   # Service management guide
├── TODO.md                 # Future improvements
└── venv/                   # Virtual environment
```

## Requirements

- Raspberry Pi 3/4/Zero 2W
- Python 3.7+
- 1-Wire interface enabled
- GPIO access
- Network connection

## License

This project is for personal use. Modify and distribute as needed.

## Contributing

Contributions welcome! Please:
1. Test changes thoroughly
2. Update documentation
3. Follow existing code style
4. Add comments for complex logic

## Support

- Check logs first: `sudo journalctl -u x1c-heater.service -n 100`
- Review documentation in CLAUDE.md
- Test manually: `python3 x1c_heater.py`

## Acknowledgments

- Built for X1C 3D printer chamber heating
- Uses simple-pid for temperature control
- Flask for web interface
- Chart.js for temperature graphing

---

**Version**: 2.4 (WebSocket Implementation)
**Status**: In Testing ⚠️ (Known UI flickering issue - see TODO.md)
**Last Updated**: 2025-11-08
