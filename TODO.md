# TODO List - X1C Chamber Heater Controller

## 🐛 Active Bugs

### UI Flickering with WebSocket Optimistic Updates
**Priority:** High
**Status:** In Progress (2025-11-08)
**Discovered:** 2025-11-08

**Symptoms:**
- Clicking START/STOP/PAUSE buttons causes flickering: buttons toggle enabled/disabled/enabled
- The delay between state changes is ~2 seconds (longer than before optimistic lock)
- Clicking STOP button doesn't immediately turn off heater/fans toggle indicators
- Temperature display and graph work correctly

**Root Cause Analysis:**
- Optimistic UI updates occur instantly when user clicks button
- Flask route sets flag (`start_requested = True`) but doesn't update `status_data`
- Idle loop (sleeping for 1 second) emits WebSocket with OLD state during optimistic lock period
- WebSocket bypasses optimistic lock for some reason, overriding UI
- 2 seconds later, optimistic lock expires and WebSocket corrects state again
- Result: Multiple state changes create flickering effect

**Attempted Solutions:**
1. ✅ Added `lockOptimisticUpdate()` function with 2-second timer
2. ✅ WebSocket handler checks `optimisticUpdateActive` flag before updating buttons/toggles
3. ✅ Reduced idle loop sleep from 5 seconds to 1 second
4. ✅ Backend turns heater on immediately when START is processed
5. ❌ Still experiencing flickering despite lock mechanism

**Potential Solutions to Try:**
1. Increase optimistic lock duration from 2 seconds to 3-4 seconds
2. Stop idle loop from emitting during optimistic lock period
3. Have Flask routes immediately update `status_data` AND emit WebSocket (instead of just setting flag)
4. Add debouncing to WebSocket updates
5. Investigate why optimistic lock check isn't preventing all WebSocket updates

**Code Locations:**
- Optimistic lock: `x1c_heater.py` lines 1624-1626, 2331-2345
- Lock usage in buttons: lines 2020, 2067, 2095
- WebSocket handler with lock check: lines 2598-2609, 2615-2653
- Idle loop emissions: line 471

**Related Issue:**
- STOP button should immediately turn off heater/fans indicators (add optimistic update to stopPrint())

---

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

## 📝 WebSocket Implementation Session (2025-11-08)

### Changes Implemented ✅

1. **Real-Time WebSocket Communication**
   - Migrated from 2-second HTTP polling to instant WebSocket updates
   - Added Flask-SocketIO and Socket.IO client library
   - Server pushes updates to browser instantly when state changes
   - Latency reduced from 0-2000ms to <50ms

2. **Backend WebSocket Infrastructure**
   - Added `socketio = SocketIO(app)` initialization
   - Created `emit_status_update()`, `emit_notification()`, `emit_history_update()` helper functions
   - Added 11+ strategic WebSocket emits at critical state changes:
     - Fire detection & reset
     - Manual toggle controls (heater, fans, lights)
     - Phase transitions (warming up, heating, cooling)
     - Print start/stop/pause
     - Idle temperature updates (every 1 second, reduced from 5)
     - Main warmup/heating loops (every 5 seconds)

3. **Frontend WebSocket Integration**
   - Replaced polling intervals with Socket.IO event listeners
   - Added `socket.on('status_update')` for real-time UI updates
   - Added `socket.on('notification')` for push notifications
   - Connection/disconnection handling with auto-reconnect
   - Fallback polling (10 seconds) if WebSocket fails

4. **Optimistic UI Updates**
   - Instant visual feedback when user clicks buttons/toggles
   - START/STOP/PAUSE buttons update immediately
   - Heater/Fans/Lights indicators update immediately
   - Added `lockOptimisticUpdate()` mechanism to prevent WebSocket override (2-second lock)
   - Error handling reverts optimistic updates on failure

5. **Performance Improvements**
   - Idle loop sleep reduced from 5 seconds to 1 second for faster START response
   - Backend turns heater ON immediately when START processed (before first emit)
   - Temperature graph updates every 5 seconds (restored after migration)
   - Disabled button styling improved (40% opacity, gray background)

6. **Bug Fixes**
   - Fixed Emergency Stop button missing ID (v2.3.1)
   - Fixed temperature sensors not displaying
   - Added WebSocket emits to warmup and heating loops
   - Added initial heater activation in START sequence

### Known Issues ⚠️

1. **UI Flickering** (High Priority)
   - Buttons and toggles flicker when clicked despite optimistic lock
   - See "Active Bugs" section above for details
   - Needs investigation in next session

2. **STOP Button Toggles**
   - STOP button doesn't immediately turn off heater/fans indicators
   - Should add optimistic update to stopPrint() function

### Files Modified
- `requirements.txt` - Added flask-socketio and python-socketio
- `x1c_heater.py` - Extensive changes:
  - Lines 12: Added Flask-SocketIO imports
  - Lines 838-852: SocketIO initialization and emit helpers
  - Lines 315, 326: Fire monitor emits
  - Lines 471, 672, 821: Main loop emits
  - Lines 520-524: Early heater activation
  - Lines 2592-2610: Toggle route emits
  - Lines 1316: Socket.IO client library CDN
  - Lines 1624-1626: Optimistic lock variables
  - Lines 2331-2345: Optimistic lock function
  - Lines 2018-2127: Optimistic updates in button handlers
  - Lines 2586-2770: WebSocket event handlers
  - Lines 2739: Temperature graph polling restored
- `TODO.md` - This file (documented issues)

### Dependencies to Install
```bash
source venv/bin/activate
pip install flask-socketio python-socketio
deactivate
sudo systemctl restart x1c-heater
```

### Testing Checklist for Next Session
- [ ] Investigate optimistic lock flickering issue
- [ ] Debug why WebSocket bypasses optimistic lock
- [ ] Add optimistic update to STOP button for heater/fans
- [ ] Test with actual hardware (heater, fans, temperature changes)
- [ ] Verify no performance degradation with WebSocket
- [ ] Test connection stability over extended period
- [ ] Verify fallback polling works when WebSocket disconnects

### Performance Metrics
- **Before:** 2-second polling, 0-2000ms latency
- **After:** WebSocket push, <50ms latency (when working)
- **Known Issue:** Flickering adds ~2 second delay (needs fix)

---

**Last Updated:** 2025-11-08
