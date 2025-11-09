# TODO List - X1C Chamber Heater Controller

## 🐛 Active Bugs

### ✅ RESOLVED: Temperature Probes Not Displaying on Main Interface
**Priority:** High
**Status:** Fixed (2025-11-08)
**Discovered:** 2025-11-08

**Root Cause:**
- Emergency Stop button was missing `id="emergency-stop-btn"` attribute
- JavaScript error: `Cannot set properties of null (setting 'disabled')`
- This error prevented `updateStatus()` function from completing execution
- Sensor list rendering code (line 2347) never executed due to early error

**Solution:**
- Added `id="emergency-stop-btn"` to EMERGENCY STOP button in HTML
- JavaScript can now properly reference and control the button
- `updateStatus()` completes successfully and renders sensor list

**Files Modified:**
- `x1c_heater.py` line 1444: Added missing ID attribute

**Verification:**
- Browser console no longer shows JavaScript errors
- Individual sensor temperatures display correctly on main interface
- Settings menu continues to work properly

---

## Hardware To-Do

### ⚡ Add Relay for USB Lights Control
**Priority:** Medium
**Status:** Not Started

**Current Issue:**
- USB hub control via uhubctl is not working reliably
- Cannot identify correct hub/port for USB lights

**Solution:**
- Add a relay module to control USB lights power
- Connect relay to available GPIO pin (suggestion: GPIO 22)
- Similar to heater relay setup (SSR on GPIO 17)

**Parts Needed:**
- [ ] 5V relay module (or reuse existing relay if available)
- [ ] Jumper wires
- [ ] USB cable with power wire accessible for relay switching

**Code Changes Required:**
- [ ] Add `LIGHTS_RELAY_PIN = 22` to pin configuration
- [ ] Update `GPIO.setup(LIGHTS_RELAY_PIN, GPIO.OUT)` in setup
- [ ] Replace `set_usb_power()` calls with `GPIO.output(LIGHTS_RELAY_PIN, HIGH/LOW)`
- [ ] Remove uhubctl dependency

**Wiring:**
```
GPIO 22 → Relay Signal Pin
Relay VCC → 5V Pin
Relay GND → Ground Pin
Relay NO/COM → USB Power Wire (cut and insert relay)
```

**Estimated Time:** 30 minutes

---

## Network Access

### 🔧 WireGuard VPN Setup
**Priority:** Medium
**Status:** Pending Setup

**Purpose:**
- Access heater controller from anywhere
- Secure access to entire home network
- Phone/tablet friendly (no command line needed)

**Setup Required:**
1. [ ] Install WireGuard on spare Raspberry Pi server
2. [ ] Configure router port forwarding (UDP port 51820)
3. [ ] Generate server and client keys
4. [ ] Create client configs for phone, tablet, laptop
5. [ ] Test connection from outside network

**Documentation:**
Complete WireGuard setup guide was provided in session. Key steps:
- Server setup on spare Pi
- Router configuration
- Client setup (scan QR code on phone)
- Access via: `http://192.168.1.x:5000` after connecting to VPN

**Estimated Time:** 1-2 hours

**Alternative Options (if WireGuard is too complex):**
- Tailscale (easiest, 5 minutes setup, free)
- Cloudflare Tunnel (free, custom domain support)

---

## Future Enhancements

Ideas for future development:
- [ ] Add email/SMS notifications for fire alerts
- [ ] Integration with 3D printer API (if available)
- [ ] Historical data persistence (SQLite database)
- [ ] Mobile-optimized UI improvements
- [ ] Multiple chamber support
- [ ] Automated preset selection based on filament type

---

## 📝 Recent Session Notes (2025-11-08)

### Changes Implemented ✅

1. **Skip Preheat Feature**
   - Added `skip_preheat` setting to bypass warming up phase
   - Located in Settings menu under "Control Parameters"
   - Useful for quick tests or when chamber is already hot

2. **Cooldown Target Temperature**
   - Added `cooldown_target_temp` setting (default: 21°C / 70°F)
   - Replaces unreliable startup-based ambient temperature
   - User can set desired cooldown target in Settings menu
   - Label shows current temperature unit (°C or °F)

3. **GPIO State Detection on Restart**
   - Service now reads GPIO states on startup
   - Syncs software variables with actual hardware state
   - Logs warning if heater/fans are ON during startup
   - Prevents UI mismatch after service restart

4. **Fire Alarm UI Lockdown**
   - ALL controls disabled during fire alarm except RESET button
   - Disabled: START, PAUSE, STOP, EMERGENCY STOP, Settings, toggles, inputs, presets
   - Visual dimming (40% opacity) shows controls are locked
   - JavaScript enforcement prevents toggle switches from working
   - Critical safety feature

5. **In-Page Modal Notifications**
   - Replaced browser notifications (didn't work on HTTP/IP addresses)
   - Custom toast notifications with blur overlay
   - Auto-dismiss after 3-5 seconds
   - Click to dismiss immediately

6. **Bug Fixes**
   - Fixed cooldown phase crash (TypeError: float in range())
   - Fixed hysteresis returning None (added safety checks)
   - Fixed fans not shutting off in manual mode when STOP clicked
   - Fixed print time not clearing when entering cooldown
   - Fixed phase display showing "WARMING_UP" → "WARMING UP"
   - Fixed emergency stop message wording

### Files Modified
- `x1c_heater.py` - Main application (multiple sections)
- `CLAUDE.md` - Updated version history and features
- `TODO.md` - This file

### Settings File Changes
Users will need to save settings once to populate new fields:
- `skip_preheat: false`
- `cooldown_target_temp: 21.0`

### Testing Checklist for Next Session
- [X] Verify temperature probes display on main interface
- [X] Test skip preheat setting
- [ ] Test cooldown target temperature setting
- [X] Verify GPIO state detection after service restart
- [X] Test fire alarm UI lockdown (all controls blocked)
- [X] Verify continuous temperature reading while idle
- [ ] Test all new features with actual hardware

---

**Last Updated:** 2025-11-08
