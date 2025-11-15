# TODO List - X1C Chamber Heater Controller

## 🐛 Active Bugs



---

## ✅ Resolved Bugs

### ✅ RESOLVED: UI Flickering with WebSocket Optimistic Updates
**Priority:** High
**Status:** Fixed (2025-11-09)
**Discovered:** 2025-11-08

**Symptoms:**
- Clicking START/STOP/PAUSE buttons caused flickering: buttons toggled enabled/disabled/enabled
- Clicking STOP button didn't immediately turn off heater/fans toggle indicators

**Root Cause:**
1. **JavaScript scoping bug:** `startBtn` was declared inside `if (!optimisticUpdateActive)` block but accessed outside it by fire alarm code
2. **Stale WebSocket messages:** Idle loop emitted status updates with old data before backend processed user action
3. **Timing issue:** 2-second optimistic lock expired before fresh WebSocket data arrived (~5 seconds)

**Solutions Implemented:**
1. ✅ **Fixed JavaScript scoping bug**
   - Moved button variable declarations to top of WebSocket handler
   - Prevents ReferenceError that was breaking WebSocket handler
2. ✅ **Implemented two-layer anti-flicker system**
   - Layer 1: Message sequence numbering - backend assigns incrementing IDs, frontend drops stale messages
   - Layer 2: Extended optimistic lock from 2s to 6s to cover backend processing time
3. ✅ **Added visual processing indicators**
   - Animated spinner on clicked button (START/STOP/PAUSE/EMERGENCY STOP)
   - Conflicting buttons temporarily dimmed and disabled during 6-second processing window
   - Prevents race conditions from rapid button clicks
4. ✅ **Added optimistic updates to all action buttons**
   - STOP and EMERGENCY STOP now immediately update heater/fans indicators
   - Consistent instant feedback across all buttons

**Files Modified:**
- `x1c_heater.py` - WebSocket handler, optimistic lock, processing indicators

**Verification:**
- No flickering when clicking buttons
- All buttons provide instant visual feedback
- Console logs show stale messages being dropped
- Processing spinners appear on button clicks
- Conflicting buttons properly disabled during processing

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

(No pending hardware tasks)

---

## ✅ Completed Hardware Tasks

### ✅ COMPLETED: GPIO-Based Lights Relay Implementation
**Priority:** Medium
**Status:** Completed (2025-11-15)

**Original Issue:**
- USB hub control via uhubctl was not working reliably
- Could not identify correct hub/port for USB lights

**Solution Implemented:**
- Added relay module on GPIO pin 22 (Physical Pin 15) for lights control
- Simple on/off GPIO control replaces unreliable uhubctl
- Lights operate independently of all program phases

**Hardware:**
- ✅ 5V relay module connected to GPIO 22
- ✅ Relay wiring completed (signal, VCC, GND)

**Code Changes Completed:**
- ✅ Added `LIGHTS_PIN = 22` to pin configuration
- ✅ Added `GPIO.setup(LIGHTS_PIN, GPIO.OUT)` in setup
- ✅ Created `set_lights()` function for GPIO control
- ✅ Removed all uhubctl dependencies and logic
- ✅ Removed `USB_HUB_LOCATION`, `USB_HUB_PORT`, `USB_CONTROL_ENABLED` constants
- ✅ Removed `get_usb_power_status()` function
- ✅ Added GPIO state detection on startup
- ✅ Implemented state persistence to heater_settings.json
- ✅ Lights maintain state during shutdown (not forced OFF)

**Features:**
- Independent operation (lights unaffected by print phases, emergency stop, or shutdown)
- User has full manual control via web interface toggle
- State persists across restarts and crashes
- GPIO state detection syncs hardware with saved preference

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

*All known issues resolved! See "Resolved Bugs" section above.*

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

## 📝 Flickering Fix & UI Polish Session (2025-11-09)

### Changes Implemented ✅

1. **Fixed JavaScript Scoping Bug**
   - Root cause: `startBtn`, `pauseBtn`, `stopBtn` declared inside optimistic lock check
   - Fire alarm code tried to access these variables, causing ReferenceError
   - Solution: Moved button declarations to top of WebSocket handler
   - Error was preventing entire WebSocket handler from completing

2. **Two-Layer Anti-Flicker System**
   - **Layer 1: Message Sequence Numbering**
     - Backend assigns incrementing sequence number to each WebSocket emission
     - Frontend tracks last received sequence number
     - Stale messages (older sequence) are immediately dropped
     - Prevents queued messages from overriding newer state
   - **Layer 2: Extended Optimistic Lock**
     - Increased from 2 seconds to 6 seconds
     - Covers backend processing time (idle loop checks every 1s, processing takes ~1-2s)
     - Lock prevents WebSocket from overriding user's optimistic updates
     - Automatically clears after 6 seconds

3. **Visual Processing Indicators**
   - Added animated spinner to clicked button (CSS keyframe animation)
   - Spinner appears on right side of button text
   - `.processing` class: shows spinner, prevents interaction
   - `.processing-blocked` class: dims conflicting buttons (50% opacity), disables interaction
   - Conflicting buttons blocked during 6-second processing window:
     - START clicked → STOP/PAUSE blocked
     - STOP clicked → START/PAUSE blocked
     - PAUSE clicked → START/STOP blocked
     - EMERGENCY STOP clicked → all others blocked

4. **Enhanced lockOptimisticUpdate() Function**
   - Now accepts `activeButton` and `blockedButtons` parameters
   - Automatically manages processing indicators and conflict prevention
   - Cleans up all classes when lock expires
   - Prevents race conditions from rapid button clicks

5. **Optimistic Updates for All Action Buttons**
   - START button: already had optimistic updates
   - STOP button: added heater/fans OFF optimistic updates
   - PAUSE button: already had text/class toggle
   - EMERGENCY STOP: added button states + heater/fans OFF updates
   - All buttons now provide consistent instant feedback

6. **Console Logging for Debugging**
   - Added sequence validation logging: "ACCEPTED" vs "DROPPED stale message"
   - Added lock status logging: "ENABLED" and "EXPIRED"
   - Added WebSocket update logging: "Updating indicators" vs "SKIPPED"
   - Helps verify anti-flicker system is working correctly

### Root Cause Summary

The flickering was caused by three issues working together:
1. JavaScript error breaking WebSocket handler
2. Stale WebSocket messages from idle loop arriving after user click
3. Optimistic lock expiring before fresh data arrived

### Files Modified
- `x1c_heater.py`:
  - Lines 91-93: Added sequence numbering globals
  - Lines 872-882: Updated `emit_status_update()` with sequence increment
  - Lines 1010-1013: Added spin animation keyframe
  - Lines 1060-1085: Added `.processing` and `.processing-blocked` CSS classes
  - Lines 1636-1651: Added sequence tracking and updated optimistic lock comments
  - Lines 2427-2465: Enhanced `lockOptimisticUpdate()` with visual indicators
  - Lines 2589-2592: Moved button declarations to top of WebSocket handler (scoping fix)
  - Lines 2604-2611: Added sequence validation and logging
  - Lines 2644-2657: Added indicator update logging
  - Lines 2075-2091: Updated `startPrint()` with processing indicators
  - Lines 2127-2163: Updated `stopPrint()` with processing indicators and optimistic updates
  - Lines 2166-2201: Updated `pausePrint()` with processing indicators
  - Lines 2222-2253: Updated `emergencyStop()` with processing indicators and optimistic updates
- `CLAUDE.md`: Updated version history to v2.5
- `TODO.md`: Moved flickering bug to "Resolved" section

### Testing Results
- ✅ No flickering when clicking any button
- ✅ Processing spinners appear on button clicks
- ✅ Conflicting buttons properly dimmed/disabled
- ✅ Stale messages dropped (visible in console logs)
- ✅ All buttons provide instant visual feedback
- ✅ No JavaScript errors in console
- ✅ Race conditions prevented

### Performance Impact
- Negligible - sequence validation is simple integer comparison
- Optimistic lock increased from 2s to 6s (intentional for reliability)
- Visual indicators use CSS animations (GPU accelerated)
- Message drops reduce unnecessary DOM updates

---

## 📝 Print State Persistence & Crash Recovery Session (2025-11-09)

### Changes Implemented ✅

1. **Print State Persistence System**
   - Created `save_print_state()` function to serialize current print state to JSON
   - Created `load_print_state()` function with intelligent staleness validation
   - Created `delete_print_state()` function for cleanup
   - State file: `print_state.json` (excluded from git via .gitignore)
   - Auto-saves every 10 seconds during heating/maintaining
   - Auto-saves every 5 minutes (per step) during cooldown

2. **State Schema**
   - Timestamp, phase, start_time, print_duration
   - Pause state (is_paused, pause_time_accumulated)
   - Target temp, fans_enabled, logging_enabled, time_adjustments
   - Manual override states (heater_manual_override, fans_manual_override)
   - Hardware states (heater_on, fans_on)

3. **Intelligent Staleness Validation**
   - Different validation logic for cooling vs heating/maintaining phases
   - Heating/maintaining: validates state hasn't exceeded remaining print time + 5min grace
   - Cooling: validates state is within max cooldown duration (12 hours)
   - Auto-aborts stale states that can't be meaningfully resumed
   - Backward compatible with old state files (defaults to Auto/OFF if fields missing)

4. **Resume UI**
   - Orange warning banner with "🔄 Print Interrupted" message
   - Displays immediately on page load if valid state found
   - Two buttons: "▶ RESUME PRINT" and "✖ ABORT"
   - Banner checks on page load (HTTP) and updates via WebSocket

5. **Resume Logic**
   - Calculates elapsed time since crash
   - Adjusts start_time to preserve correct remaining time
   - Restores pause state if print was paused during crash
   - Restores manual override flags (Auto/Manual mode)
   - Restores actual hardware GPIO states (heater/fans ON/OFF)
   - Skips warmup phase when resuming (no redundant heating)

6. **Cooling Phase Resume**
   - Fixed stale detection bug (was incorrectly deleting valid cooling states)
   - Calculates remaining cooldown time and continues from that point
   - Skips heating loop entirely when resuming from cooling
   - Shows correct cooldown time remaining in UI immediately

7. **API Routes**
   - `POST /resume_print` - Resume interrupted print cycle
   - `POST /abort_resume` - Discard saved state and return to idle

8. **UI Improvements**
   - Fixed probe name update delay (avoids slow sensor reads, emits immediate WebSocket)
   - Fixed processing spinner cleanup (new `clearAllProcessingStates()` function)
   - Added optimistic notifications for pause/resume (instant feedback)
   - Fixed heater UI sync during cooling (emits WebSocket when heater turns off)

### Files Modified
- `x1c_heater.py`:
  - Lines 22: Added `PRINT_STATE_FILE` constant
  - Lines 222-244: `save_print_state()` function (with manual override fields)
  - Lines 246-311: `load_print_state()` function (with staleness validation)
  - Lines 313-320: `delete_print_state()` function
  - Lines 255-263: Backward compatibility defaults
  - Lines 488-593: `slow_cool()` - state saving during cooldown
  - Lines 600-1150: `main_loop()` - resume logic, state loading, timing calculations
  - Lines 1563-1575: Resume banner HTML
  - Lines 2803-2817: `clearAllProcessingStates()` function
  - Lines 2515, 2521: Optimistic pause/resume notifications
  - Lines 564: Heater UI sync emit during cooling
  - Lines 3435-3455: `/resume_print` route
  - Lines 3457-3470: `/abort_resume` route
  - Lines 2280-2304: `checkResumeBanner()` function
- `.gitignore`: Already had `print_state.json` excluded
- `CLAUDE.md`: Updated version history to v2.6
- `TODO.md`: This file

### Testing Status
- ✅ Resume from heating phase works correctly
- ✅ Resume from maintaining phase works correctly
- ✅ Resume from cooling phase works correctly
- ✅ Pause state preserved across crashes
- ✅ Manual override states preserved across crashes
- ✅ Hardware states (heater/fans ON/OFF) preserved
- ✅ Cooldown time calculation correct when resuming
- ✅ Stale detection works for all phases
- ✅ Banner appears immediately on page load
- ✅ Probe name updates are instant
- ✅ Processing spinners clean up properly
- ✅ Pause/resume notifications appear instantly
- ✅ Heater UI syncs correctly during cooling

---

## 🎯 Future Enhancements

### 🎨 Rearrangeable Dashboard Cards (Planned for Next Session)
**Priority:** Medium
**Status:** Planning Phase

**Goal:**
Transform the current fixed-layout dashboard into a flexible, user-customizable tile-based system where users can rearrange, resize, and show/hide different information cards.

**Implementation Plan:**

1. **Technology Choice**
   - **Option A: GridStack.js** (Recommended)
     - Drag-and-drop grid layout library
     - Touch support for mobile/tablet
     - Responsive breakpoints
     - Save/restore layouts via JSON
     - ~40KB minified
     - MIT License
   - **Option B: Muuri**
     - More lightweight (~33KB)
     - Smooth animations
     - Less feature-rich than GridStack
   - **Option C: Custom CSS Grid + Drag API**
     - No dependencies
     - More development time
     - Full control

2. **Card Types to Implement**
   - **Temperature Card**: Current temp, setpoint, ETA (currently in top section)
   - **Control Panel Card**: START/STOP/PAUSE/EMERGENCY STOP buttons (currently center)
   - **Settings Card**: Target temp, print time, fans/logging toggles (currently center)
   - **Time Display Card**: Print time remaining, cooldown time (currently top)
   - **Phase Status Card**: Current phase, elapsed time (currently top)
   - **Indicators Card**: Heater/Fans/Lights toggles with indicators (currently right side)
   - **Presets Card**: Quick preset buttons (currently bottom)
   - **Sensor List Card**: Individual probe temperatures (currently bottom)
   - **Temperature Graph Card**: Real-time chart (currently bottom - already large)

3. **Layout System**
   - Default layout: Similar to current fixed layout
   - Grid-based: 12-column responsive grid
   - Breakpoints: Desktop (1200px+), Tablet (768-1199px), Mobile (<768px)
   - Each card has min/max size constraints
   - Cards snap to grid for clean alignment

4. **Persistence**
   - Save layout to `heater_settings.json` under `dashboard_layout` key
   - Store: card IDs, positions (x, y), sizes (w, h), visibility
   - Load layout on page load
   - "Reset to Default" button to restore original layout

5. **User Interface**
   - **Edit Mode Toggle**: Button in header to enter/exit edit mode
   - **In Edit Mode**:
     - Drag cards to reposition
     - Resize handles on card corners
     - "👁 Show/Hide" menu to toggle card visibility
     - Locked cards (optional): certain cards cannot be hidden (e.g., control panel)
   - **Visual Indicators**:
     - Dashed borders when in edit mode
     - Drag handles visible
     - Placeholder shown when dragging

6. **Mobile Considerations**
   - Touch-friendly drag/drop
   - Larger touch targets in edit mode
   - Single-column layout on mobile (stack cards vertically)
   - Swipe gestures for showing/hiding cards

7. **Implementation Steps**
   - [ ] Choose library (recommend GridStack.js)
   - [ ] Refactor HTML to create card components
   - [ ] Add GridStack.js CSS/JS to page
   - [ ] Create default layout configuration
   - [ ] Implement drag-and-drop functionality
   - [ ] Add edit mode toggle
   - [ ] Implement save/load layout to settings
   - [ ] Add show/hide card menu
   - [ ] Add "Reset to Default" button
   - [ ] Test responsive behavior on mobile/tablet
   - [ ] Add visual polish (animations, transitions)

8. **Backend Changes Required**
   - Minimal - only need to persist `dashboard_layout` in settings
   - Add new field to `heater_settings.json`
   - No new routes required (uses existing `/save_settings`)

9. **Estimated Complexity**
   - **Using GridStack.js**: 4-6 hours
     - 1-2 hours: HTML refactoring into cards
     - 1 hour: GridStack integration
     - 1 hour: Save/load layout logic
     - 1-2 hours: Polish and responsive testing
   - **Custom implementation**: 8-12 hours

10. **Benefits**
    - Users can prioritize information they care about most
    - Cleaner interface - hide cards not needed
    - Better use of screen space
    - Tablet-friendly layout customization
    - Future-proof: easy to add new card types

11. **Potential Challenges**
    - Maintaining WebSocket updates with rearranged cards
    - Ensuring cards update correctly regardless of position
    - Mobile layout constraints (limited screen space)
    - Accessibility considerations for drag-and-drop

12. **Nice-to-Have Features** (Future iterations)
    - Multiple saved layouts (e.g., "Monitoring", "Control", "Detailed")
    - Export/import layouts
    - Card color themes
    - Collapsible cards (minimize to title bar only)
    - Keyboard shortcuts for common layouts

**Files to Modify:**
- `x1c_heater.py`:
  - HTML section: Refactor into card components with GridStack classes
  - Add GridStack.js and CSS CDN links
  - JavaScript: Initialize GridStack, save/load layout functions
- `heater_settings.json`: Add `dashboard_layout` field (auto-created on first save)

**Dependencies:**
- GridStack.js: https://gridstackjs.com/
- CDN: https://cdn.jsdelivr.net/npm/gridstack@latest/dist/gridstack-all.min.js

**Testing Checklist:**
- [ ] Cards can be dragged and dropped
- [ ] Cards can be resized
- [ ] Layout persists across page reloads
- [ ] Layout persists across service restarts
- [ ] Reset to default works correctly
- [ ] Mobile layout is usable
- [ ] WebSocket updates work with rearranged cards
- [ ] All buttons and controls work in any position
- [ ] Edit mode toggle works
- [ ] Show/hide card menu works

---

**Last Updated:** 2025-11-15
