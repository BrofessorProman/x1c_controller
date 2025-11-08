# TODO List - X1C Chamber Heater Controller

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

**Last Updated:** 2025-11-07
