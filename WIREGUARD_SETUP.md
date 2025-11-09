# WireGuard VPN Setup Guide - Educational Walkthrough

Complete guide for setting up WireGuard VPN to access your home network remotely.

---

## Table of Contents

1. [Understanding the Basics](#part-1-understanding-the-basics)
2. [Pre-Setup Checklist](#part-2-pre-setup-checklist)
3. [Network Planning](#part-3-network-planning)
4. [Installing WireGuard](#part-4-installing-wireguard-using-pivpn)
5. [PiVPN Installation Wizard](#part-5-pivpn-installation-wizard)
6. [Router Configuration](#part-6-router-configuration-port-forwarding)
7. [Creating Client Configs](#part-7-creating-client-configs)
8. [Install Client on Phone](#part-8-install-client-on-your-phone)
9. [Understanding What Just Happened](#part-9-understanding-what-just-happened)
10. [Testing & Troubleshooting](#part-10-testing--troubleshooting)
11. [Advanced Topics](#part-11-advanced-topics-optional)
12. [Maintenance & Updates](#part-12-maintenance--updates)

---

## Part 1: Understanding the Basics

### What is a VPN and How Does WireGuard Work?

**Traditional Internet Connection:**
```
Your Phone → Cell Network → Internet → Your Home Router (blocks incoming) → ❌ Can't reach Pi
```

**With WireGuard VPN:**
```
Your Phone → Cell Network → Internet → Your Home Router (port forwarded) → Spare Pi (VPN gateway) → Local Network → Heater Pi ✅
```

### Key Concepts

1. **VPN Gateway** - Your spare Pi acts as a "door" into your home network
2. **Encryption Tunnel** - All traffic between phone and gateway is encrypted (even your cell carrier can't see it)
3. **Private Network** - Your devices get "virtual" IP addresses (like 10.0.0.x) that work across the internet
4. **Port Forwarding** - Opens a specific "door" (port 51820) on your router to let VPN traffic in

### WireGuard vs Other VPNs

**Why WireGuard is better:**
- **Fast**: Only ~4,000 lines of code (OpenVPN has 600,000+)
- **Modern crypto**: Uses ChaCha20, Curve25519 (same as Signal app)
- **Simple**: No complex certificates, just public/private key pairs
- **Battery friendly**: Stays connected without draining phone battery

---

## Part 2: Pre-Setup Checklist

### 1. Check Your Internet Setup

**Find your public IP address:**
```bash
# From any Pi or computer on your network
curl ifconfig.me
```

**Example output:** `123.45.67.89`

**Understanding IPs:**
- **Public IP** (e.g., 123.45.67.89): Your home's address on the internet (assigned by ISP)
- **Private IP** (e.g., 192.168.1.x): Addresses inside your home network (assigned by router)

**Check if your IP changes:**
- Most home internet has a "dynamic IP" that changes occasionally
- We'll use DDNS (Dynamic DNS) to handle this

### 2. Check for CGNAT (Carrier Grade NAT)

Some ISPs put customers behind an extra layer of NAT, which breaks port forwarding.

**Test:**
1. Find your public IP: `curl ifconfig.me`
2. Log into your router's web interface (usually http://192.168.1.1)
3. Look for "WAN IP" or "Internet IP" in router settings

**If they match:** ✅ You're good! (Normal setup)
**If they DON'T match:** ❌ You might be behind CGNAT (call ISP or use Tailscale instead)

### 3. Identify Your Spare Pi

**What Pi model do you have for the VPN gateway?**
- Pi 3/4/5: Great performance
- Pi Zero 2 W: Works, but slower

**Check its IP:**
```bash
# SSH into spare Pi
hostname -I
```

**Example output:** `192.168.1.100` (this will be your VPN gateway)

---

## Part 3: Network Planning

### IP Address Scheme

**Your current network (example):**
```
Router:          192.168.1.1
Heater Pi:       192.168.1.150  (or whatever it is now)
Spare Pi:        192.168.1.100  (VPN gateway)
Your Laptop:     192.168.1.50
etc...
```

**WireGuard VPN network (we'll create this):**
```
VPN Gateway (spare Pi):  10.6.0.1
Your Phone (client):     10.6.0.2
Your Laptop (client):    10.6.0.3
Future devices:          10.6.0.4, 10.6.0.5, etc.
```

### Why a different subnet?

- VPN uses `10.6.0.0/24` - a separate "virtual" network
- Your home uses `192.168.1.0/24` - physical network
- VPN gateway routes between them (acts as a bridge)

### Understanding CIDR Notation

- `10.6.0.0/24` means:
  - Network: 10.6.0.0
  - Subnet mask: 255.255.255.0
  - Available IPs: 10.6.0.1 through 10.6.0.254 (254 devices)
  - `/24` = first 24 bits are the network (last 8 bits for devices)

---

## Part 4: Installing WireGuard (Using PiVPN)

We'll use **PiVPN** - it's a helper script that makes WireGuard setup much easier while still giving you control.

### Step 1: Prepare the Spare Pi

**SSH into your spare Pi:**
```bash
ssh pi@192.168.1.100  # (use your spare Pi's actual IP)
```

**Update system:**
```bash
sudo apt update && sudo apt upgrade -y
```

**Why update first?**
- Gets latest security patches
- Ensures compatibility with WireGuard kernel module
- Prevents installation errors

### Step 2: Run PiVPN Installer

```bash
curl -L https://install.pivpn.io | bash
```

**What this does:**
- Downloads the official PiVPN installer script
- The `|` (pipe) sends it directly to bash to execute
- `-L` follows redirects (if the URL has moved)

**Security note:** Only run scripts from trusted sources! PiVPN is open source and widely used.

---

## Part 5: PiVPN Installation Wizard

The installer will show a series of screens. Here's what to choose and WHY:

### Screen 1: Welcome
- Read it, press **Enter**

### Screen 2: Static IP Warning
**It says:** "Your Pi needs a static IP"

**What this means:**
- By default, your router assigns IPs automatically (DHCP)
- If spare Pi's IP changes, port forwarding breaks
- We need to "reserve" its IP

**Choose:** "Yes" to configure static IP

**Next screen - Static IP configuration:**
- **Current IP:** Shows 192.168.1.100 (or whatever yours is)
- **Choose:** "Yes" to use this IP as static

**Understanding DHCP vs Static:**
```
DHCP (Dynamic):
Router: "Here's IP 192.168.1.100, but it might change tomorrow"

Static:
Router: "192.168.1.100 is YOURS forever"
```

### Screen 3: Choose Local User
**Select:** Your username (probably `pi`)

**Why this matters:**
- PiVPN stores configs in this user's home directory
- Client config files will be in `/home/pi/configs`

### Screen 4: Choose VPN Protocol
**Select:** WireGuard

**WireGuard vs OpenVPN:**
- WireGuard: Modern, fast, simple (4,000 lines of code)
- OpenVPN: Older, slower, complex (600,000+ lines of code)

### Screen 5: Default WireGuard Port
**Shows:** 51820 (default)
**Choose:** Keep default (press Enter)

**Understanding ports:**
- A "port" is like an apartment number for your router's IP address
- `123.45.67.89:51820` = "Go to address 123.45.67.89, knock on door 51820"
- WireGuard uses **UDP** (not TCP):
  - **UDP**: "Fire and forget" - faster, used for real-time stuff (VPN, video calls)
  - **TCP**: "Confirm delivery" - slower, used for files, web pages

**Why 51820?**
- WireGuard's standard port
- Can change if you want (doesn't affect security)
- Some people use random ports to avoid port scans

### Screen 6: DNS Provider
**Select:** Custom (we'll use your router)

**Next screen - Enter DNS:**
```
192.168.1.1
```
(Your router's IP - it already knows your local network)

**What is DNS?**
```
You type:     heater-pi.local  or  192.168.1.150
DNS says:     "That's 192.168.1.150"
Browser:      Connects to 192.168.1.150:5000
```

**Why use your router for DNS?**
- It knows local hostnames (like `heater-pi.local`)
- You can access devices by name, not just IP
- External DNS (like 1.1.1.1) doesn't know your local network

**Alternative:** You could use `1.1.1.1` (Cloudflare) or `8.8.8.8` (Google), but then you'd need to use IP addresses for local devices.

### Screen 7: Public IP or DNS
**This is important!** You have two choices:

**Option 1: Use current public IP** (simpler, but IP might change)
**Option 2: Use a DNS entry** (better, handles IP changes)

**Recommended:** Option 2 - DNS Entry

**Why?** Most home IPs change occasionally. If you use DNS, we can update it automatically.

### Screen 8: DNS Provider (for your public IP)
**If you chose DNS entry above:**

**Select:** DuckDNS (free, easy, reliable)

**What is DDNS (Dynamic DNS)?**
```
Normal DNS:
google.com → 142.250.180.46 (never changes)

DDNS:
yourname.duckdns.org → 123.45.67.89 (updates when your home IP changes)
```

**How it works:**
1. You create `yourname.duckdns.org`
2. Your Pi runs a script every 5 minutes
3. Script checks: "Did my public IP change?"
4. If yes: Updates DuckDNS automatically
5. Your phone always connects to `yourname.duckdns.org` (which points to current IP)

**Next screen - DuckDNS Setup:**
- Follow instructions to create account at https://www.duckdns.org
- Choose a subdomain (e.g., `homelab.duckdns.org`)
- Copy your token (long string of letters/numbers)
- Paste token into PiVPN installer

### Screen 9: Unattended Upgrades
**Select:** Yes (enable automatic security updates)

**What this does:**
- Automatically installs security patches
- Only applies updates marked "security" (won't break things)
- Keeps your VPN gateway secure without manual intervention

### Screen 10: Installation Complete!
**It will say:** "WireGuard is now installed"
**Choose:** Reboot now

---

## Part 6: Router Configuration (Port Forwarding)

After the Pi reboots, you need to tell your router to send VPN traffic to it.

### Understanding Port Forwarding

**Without port forwarding:**
```
Internet → Your Router → "I don't know what to do with port 51820" → ❌ Drops packet
```

**With port forwarding:**
```
Internet → Your Router → "Port 51820 goes to 192.168.1.100" → Spare Pi → ✅ VPN works
```

### Finding Your Router's Admin Panel

**Common router addresses:**
- `http://192.168.1.1`
- `http://192.168.0.1`
- `http://10.0.0.1`

**Or find it:**
```bash
ip route | grep default
```
Output: `default via 192.168.1.1` ← That's your router

**Login credentials:**
- Check sticker on router
- Common defaults: admin/admin, admin/password
- ISP routers: Often printed on device

### Configure Port Forwarding

**Every router is different, but look for:**
- "Port Forwarding"
- "Virtual Servers"
- "NAT Forwarding"
- "Gaming" or "Applications"

**Settings to configure:**
```
Service Name:       WireGuard VPN
Protocol:           UDP
External Port:      51820
Internal IP:        192.168.1.100  (your spare Pi)
Internal Port:      51820
```

**Some routers ask for:**
- **Port Range:** 51820-51820 (just one port)
- **Both TCP and UDP:** Choose UDP only

### Example (common router brands)

**TP-Link:**
Advanced → NAT Forwarding → Virtual Servers → Add

**Netgear:**
Advanced → Advanced Setup → Port Forwarding/Port Triggering

**Asus:**
WAN → Virtual Server/Port Forwarding

**Save/Apply** the settings.

### Test Port Forwarding

From a device ON your network:
```bash
# Test if port is open locally
nc -zvu 192.168.1.100 51820
```

Should say: "Connection to 192.168.1.100 51820 port [udp/*] succeeded!"

**From outside your network** (use your phone's cellular data):
- Visit: https://www.yougetsignal.com/tools/open-ports/
- Enter your public IP and port 51820
- It should show "closed" for now (WireGuard doesn't respond to scans for security)
- This is normal! We'll test properly later

---

## Part 7: Creating Client Configs

Now we create configs for your phone, laptop, etc.

### SSH back into spare Pi:
```bash
ssh pi@192.168.1.100
```

### Create a client config:
```bash
pivpn add
```

**It will ask:**
```
Enter a Name for the Client: phone
```

**Naming convention:**
- Use descriptive names: phone, laptop, tablet, etc.
- No spaces (use `johns-phone` not `john's phone`)
- This becomes the config filename

**What happens:**
1. Generates a public/private key pair for this device
2. Assigns it IP `10.6.0.2` (next available in VPN subnet)
3. Creates config file: `/home/pi/configs/phone.conf`
4. Updates server to allow this client

### See the config:
```bash
cat ~/configs/phone.conf
```

**Example output:**
```ini
[Interface]
PrivateKey = <long random string>
Address = 10.6.0.2/24
DNS = 192.168.1.1

[Peer]
PublicKey = <server's public key>
PresharedKey = <extra security key>
Endpoint = yourname.duckdns.org:51820
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
```

### Understanding the config:

**[Interface] section** (this device):
- `PrivateKey`: Secret key (never share this!)
- `Address`: VPN IP for this device (10.6.0.2)
- `DNS`: What DNS server to use when connected

**[Peer] section** (the VPN server):
- `PublicKey`: Server's public key (safe to share)
- `PresharedKey`: Extra encryption layer (optional, but PiVPN adds it)
- `Endpoint`: Where to connect (your home)
- `AllowedIPs = 0.0.0.0/0`: Route ALL traffic through VPN
- `PersistentKeepalive = 25`: Send packet every 25 seconds to keep connection alive

**AllowedIPs explained:**
- `0.0.0.0/0` = ALL IPv4 traffic
- `::/0` = ALL IPv6 traffic
- Alternative: `192.168.1.0/24` = Only route home network traffic (split tunnel)

---

## Part 8: Install Client on Your Phone

### iOS:
1. Install "WireGuard" app from App Store
2. On your Pi, generate QR code:
   ```bash
   pivpn -qr phone
   ```
3. Scan QR code with WireGuard app
4. Tap toggle to connect!

### Android:
1. Install "WireGuard" app from Play Store
2. Same as iOS - scan QR code
3. Enable connection

### Understanding the connection:

**When you toggle ON:**
```
1. Phone generates random UDP port (e.g., 51234)
2. Sends encrypted handshake to yourname.duckdns.org:51820
3. Router forwards port 51820 to spare Pi
4. Spare Pi receives handshake, verifies public key
5. Both sides derive encryption keys
6. Phone gets VPN IP 10.6.0.2
7. Traffic flows: Phone → Internet → Your Router → Spare Pi → Home Network
```

**To verify connection:**
- Toggle VPN on
- Phone should show "Active" and data transfer
- Try: `http://192.168.1.150:5000` (heater Pi's local IP)
- Should load heater controller! 🎉

---

## Part 9: Understanding What Just Happened

### Network Topology

```
                    INTERNET
                       |
    [Your Public IP: 123.45.67.89:51820]
                       |
              +--------+--------+
              |   Your Router   |  (Port forward: 51820 → 192.168.1.100)
              +--------+--------+
                       |
        192.168.1.0/24 network
                       |
        +--------------+--------------+
        |                             |
[Spare Pi: 192.168.1.100]    [Heater Pi: 192.168.1.150]
 WireGuard Gateway                Chamber Controller
 VPN IP: 10.6.0.1
        |
   [Virtual Tunnel]
        |
[Phone via VPN: 10.6.0.2]
```

### Packet Journey Example

**You browse to http://192.168.1.150:5000 from your phone:**

```
1. Phone (10.6.0.2):
   "I want to reach 192.168.1.150:5000"

2. WireGuard app:
   "This IP is in AllowedIPs, I'll encrypt it"
   Encrypts packet with ChaCha20

3. Phone sends to:
   yourname.duckdns.org:51820 (over cellular)

4. Router receives on port 51820:
   "Port forwarding rule says: send to 192.168.1.100"

5. Spare Pi (WireGuard):
   "I recognize this key, it's the phone!"
   Decrypts packet: "Oh, you want 192.168.1.150:5000"
   Forwards to heater Pi on local network

6. Heater Pi:
   "HTTP request for port 5000, here's the web page"
   Sends response back to 10.6.0.2 (via spare Pi)

7. Spare Pi encrypts response, sends to phone

8. Phone decrypts, shows web page
```

**This all happens in ~50ms!**

---

## Part 10: Testing & Troubleshooting

### Basic Tests

**1. From phone (VPN connected):**
```
Ping test: Download "Network Analyzer" app
Ping: 192.168.1.150
Should reply: "Reply from 192.168.1.150 time=20ms"
```

**2. Check VPN status on server:**
```bash
# SSH to spare Pi
sudo wg show
```

**Example output:**
```
interface: wg0
  public key: <server public key>
  private key: (hidden)
  listening port: 51820

peer: <phone public key>
  preshared key: (hidden)
  endpoint: 23.45.67.89:51234  ← Your phone's public IP
  allowed ips: 10.6.0.2/32
  latest handshake: 30 seconds ago  ← Connection is active!
  transfer: 1.2 KiB received, 8.5 KiB sent
```

**Understanding the output:**
- `endpoint`: Where phone connected from (cell carrier's IP)
- `latest handshake`: Last time keys were exchanged (should be < 3 minutes)
- `transfer`: Data sent/received
- If `latest handshake` is old → connection is down

### Common Issues

#### ❌ "Connection timeout"

**Cause:** Port forwarding not working

**Fix:**
1. Double-check router port forward: UDP 51820 → spare Pi IP
2. Check spare Pi firewall:
   ```bash
   sudo ufw status
   ```
   If enabled, allow WireGuard:
   ```bash
   sudo ufw allow 51820/udp
   ```

#### ❌ "Can reach VPN IP (10.6.0.x) but not home IPs (192.168.1.x)"

**Cause:** IP forwarding not enabled

**Fix:**
```bash
# On spare Pi, check:
cat /proc/sys/net/ipv4/ip_forward
# Should be: 1

# If it's 0, enable:
echo "net.ipv4.ip_forward=1" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

**What is IP forwarding?**
- Allows Pi to route packets between networks (VPN ↔ local)
- Without it, spare Pi receives packets but doesn't forward them
- PiVPN should enable this automatically

#### ❌ "Works sometimes, not others"

**Cause:** Dynamic IP changed, DDNS didn't update

**Fix:**
```bash
# Check DDNS update status:
cat /var/log/syslog | grep duck
# Should show recent updates

# Manually update:
echo url="https://www.duckdns.org/update?domains=yourname&token=YOUR-TOKEN&ip=" | curl -k -o /home/pi/duck.log -K -
```

#### ❌ "High latency (slow)"

**Cause:** Routing all traffic through VPN

**Fix:** Use split-tunnel (only route home network)
- Edit phone's WireGuard config
- Change `AllowedIPs` from `0.0.0.0/0` to `192.168.1.0/24`
- This means: "Only send home network traffic through VPN, everything else goes direct"

---

## Part 11: Advanced Topics (Optional)

### Adding More Clients

**Laptop, tablet, etc.:**
```bash
pivpn add
# Enter name: laptop

# Show QR code:
pivpn -qr laptop

# Or copy config file:
cat ~/configs/laptop.conf
# Copy/paste into WireGuard client
```

### Removing Clients

```bash
pivpn remove
# Select client to remove
```

### Viewing All Clients

```bash
pivpn -c
```

Shows: Name, IP, public key, last seen

### Monitoring VPN Traffic

**Real-time bandwidth:**
```bash
sudo apt install vnstat
vnstat -l -i wg0
```

**Connection logs:**
```bash
sudo journalctl -u wg-quick@wg0 -f
```

### Security Hardening (Optional)

**1. Change SSH port on spare Pi:**
```bash
sudo nano /etc/ssh/sshd_config
# Change: Port 22 → Port 2222
sudo systemctl restart ssh
```

**2. Disable password auth (use SSH keys only):**
```bash
# First, set up SSH key authentication
# Then:
sudo nano /etc/ssh/sshd_config
# Change: PasswordAuthentication yes → no
```

**3. Set up fail2ban (blocks brute force):**
```bash
sudo apt install fail2ban
sudo systemctl enable fail2ban
```

---

## Part 12: Maintenance & Updates

### Updating WireGuard

```bash
sudo apt update
sudo apt upgrade wireguard
```

### Checking VPN Status

```bash
pivpn -c  # Show clients
sudo wg   # Show WireGuard status
```

### Backup Configs

```bash
# Backup important files:
cd ~
tar -czf wireguard-backup.tar.gz /etc/wireguard/ ~/configs/
```

Store this somewhere safe! If spare Pi dies, you can restore configs to new Pi.

---

## Quick Reference Commands

### Common PiVPN Commands

```bash
pivpn add              # Add new client
pivpn remove           # Remove client
pivpn -c               # List all clients
pivpn -qr <name>       # Show QR code for client
pivpn -l               # Show latest handshake for clients
pivpn uninstall        # Remove PiVPN (careful!)
```

### Common WireGuard Commands

```bash
sudo wg                # Show WireGuard status
sudo wg show wg0       # Show specific interface
sudo systemctl status wg-quick@wg0  # Check service status
sudo systemctl restart wg-quick@wg0 # Restart WireGuard
```

### Useful Network Commands

```bash
ip route               # Show routing table
ip addr                # Show IP addresses
netstat -tupln         # Show listening ports
ping 10.6.0.1          # Ping VPN gateway
ping 192.168.1.150     # Ping heater Pi
```

---

## Summary of What You Learned

Congratulations! You now understand:

✅ **VPN fundamentals** - Encrypted tunnels, gateways, routing
✅ **IP addressing** - Public vs private, CIDR notation, subnets
✅ **Port forwarding** - Opening specific ports, UDP vs TCP
✅ **DDNS** - Handling dynamic IPs with DNS
✅ **WireGuard** - Modern VPN with public/private keys
✅ **Routing** - IP forwarding, split-tunnel vs full-tunnel
✅ **Network troubleshooting** - Checking connections, logs, status

**You've built a production-grade VPN from scratch!** 🎉

---

## Troubleshooting Checklist

When things don't work, check in this order:

1. ☑️ Is spare Pi online? (`ping 192.168.1.100`)
2. ☑️ Is WireGuard running? (`sudo systemctl status wg-quick@wg0`)
3. ☑️ Is port forwarding set up? (Check router: UDP 51820 → spare Pi)
4. ☑️ Is IP forwarding enabled? (`cat /proc/sys/net/ipv4/ip_forward` should be 1)
5. ☑️ Is DDNS working? (`cat /var/log/syslog | grep duck`)
6. ☑️ Can phone reach VPN? (`sudo wg show` - check "latest handshake")
7. ☑️ Can you ping VPN gateway? (Phone → 10.6.0.1)
8. ☑️ Can you ping local devices? (Phone → 192.168.1.150)

---

## Security Best Practices

✅ **DO:**
- Keep spare Pi updated (`sudo apt update && sudo apt upgrade`)
- Use strong passwords or SSH keys
- Enable automatic security updates
- Monitor connection logs occasionally
- Backup your WireGuard configs

❌ **DON'T:**
- Share your WireGuard private keys
- Expose SSH (port 22) to the internet
- Use default passwords on any device
- Disable firewall on VPN gateway
- Give out VPN access to untrusted people

---

## Additional Resources

- **WireGuard Official Site**: https://www.wireguard.com/
- **PiVPN Documentation**: https://www.pivpn.io/
- **DuckDNS**: https://www.duckdns.org/
- **WireGuard Quick Start**: https://www.wireguard.com/quickstart/

---

**Document Version**: 1.0
**Last Updated**: 2025-11-08
**For**: X1C Chamber Heater Controller Remote Access
