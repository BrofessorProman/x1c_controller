# X1C Heater Service Management Guide

This guide covers managing the x1c-heater systemd service and deploying code updates.

---

## Service Control Commands

### Basic Operations

```bash
# Start the service
sudo systemctl start x1c-heater

# Stop the service
sudo systemctl stop x1c-heater

# Restart the service (use after code updates)
sudo systemctl restart x1c-heater

# Check service status
sudo systemctl status x1c-heater

# Enable service (auto-start on boot)
sudo systemctl enable x1c-heater

# Disable service (prevent auto-start on boot)
sudo systemctl disable x1c-heater
```

### Viewing Logs

```bash
# View live logs (real-time, press Ctrl+C to exit)
sudo journalctl -u x1c-heater.service -f

# View last 50 log entries
sudo journalctl -u x1c-heater.service -n 50

# View last 100 log entries
sudo journalctl -u x1c-heater.service -n 100

# View all logs since last boot
sudo journalctl -u x1c-heater.service -b

# View logs from specific time
sudo journalctl -u x1c-heater.service --since "2024-11-07 14:00:00"

# View logs from last hour
sudo journalctl -u x1c-heater.service --since "1 hour ago"

# Follow logs and show last 20 lines
sudo journalctl -u x1c-heater.service -n 20 -f
```

### Service Configuration

```bash
# Reload systemd (required after editing .service file)
sudo systemctl daemon-reload

# Edit the service file
sudo nano /etc/systemd/system/x1c-heater.service

# View the service file
sudo cat /etc/systemd/system/x1c-heater.service
```

---

## Deploying Code Updates

### Method 1: Simple Update (Recommended for Small Changes)

Use this when you've made changes to `x1c_heater.py` or other Python files.

```bash
# 1. Navigate to project directory
cd /home/pi/x1c_controller

# 2. If using git, pull latest changes:
git pull

# OR if manually editing/uploading files, just make your changes

# 3. Restart the service to apply changes
sudo systemctl restart x1c-heater

# 4. Check logs to verify it started correctly
sudo journalctl -u x1c-heater.service -n 20 -f
```

**That's it!** The service will reload with your new code.

---

### Method 2: Safe Update with Backup

Use this for major changes or when you want to be extra careful.

```bash
# 1. Stop the service
sudo systemctl stop x1c-heater

# 2. Backup current code (optional but recommended)
cd /home/pi/x1c_controller
cp x1c_heater.py x1c_heater.py.backup.$(date +%Y%m%d_%H%M%S)

# 3. Update your code
# - Edit files, OR
# - Upload new files, OR
# - Pull from git
nano x1c_heater.py  # or however you update

# 4. Test the code manually (optional)
/home/pi/x1c_controller/venv/bin/python3 x1c_heater.py
# Press Ctrl+C to stop after verifying it starts

# 5. Start the service
sudo systemctl start x1c-heater

# 6. Verify it's running
sudo systemctl status x1c-heater

# 7. Watch logs for any errors
sudo journalctl -u x1c-heater.service -n 30 -f
```

---

### Method 3: Update Python Dependencies

Use this when you've added new Python packages to `requirements.txt`.

```bash
# 1. Stop the service
sudo systemctl stop x1c-heater

# 2. Activate virtual environment
cd /home/pi/x1c_controller
source venv/bin/activate

# 3. Update dependencies
pip install -r requirements.txt

# OR install a specific package
pip install package-name

# 4. Deactivate virtual environment
deactivate

# 5. Restart service
sudo systemctl start x1c-heater

# 6. Check status
sudo systemctl status x1c-heater
```

---

## Common Workflows

### Daily: Check if Service is Running

```bash
sudo systemctl status x1c-heater
```

Look for:
- `Active: active (running)` in green = ✅ Good!
- `Active: failed` or `Active: inactive` = ❌ Problem

---

### After Editing Code

```bash
sudo systemctl restart x1c-heater && sudo journalctl -u x1c-heater.service -n 20 -f
```

This restarts the service and immediately shows logs so you can verify it started correctly.

---

### Quick Health Check

```bash
# Check service status
sudo systemctl status x1c-heater

# Check recent logs for errors
sudo journalctl -u x1c-heater.service -n 50 | grep -i error

# Test web interface
curl http://localhost:5000
# Should return HTML
```

---

### Reboot the Pi

```bash
# Service will auto-start on boot (if enabled)
sudo reboot

# After reboot, verify service started
sudo systemctl status x1c-heater
```

---

## Troubleshooting

### Service Won't Start

**Step 1: Check logs for errors**
```bash
sudo journalctl -u x1c-heater.service -n 50
```

**Step 2: Try running manually to see detailed errors**
```bash
# Stop service first
sudo systemctl stop x1c-heater

# Run manually
cd /home/pi/x1c_controller
source venv/bin/activate
python3 x1c_heater.py

# Look for error messages
# Press Ctrl+C to stop
deactivate
```

**Step 3: Check service file syntax**
```bash
sudo systemctl status x1c-heater.service
# Look for configuration errors
```

---

### Service Keeps Restarting

```bash
# Watch logs in real-time to see why it crashes
sudo journalctl -u x1c-heater.service -f

# Common causes:
# - Sensor not connected
# - 1-Wire interface not enabled
# - Missing Python packages
# - Syntax error in Python code
```

---

### Can't Access Web Interface

**Check if service is running:**
```bash
sudo systemctl status x1c-heater
```

**Check if Flask is listening:**
```bash
sudo netstat -tulpn | grep 5000
# Should show Python listening on port 5000
```

**Try accessing locally first:**
```bash
curl http://localhost:5000
# Should return HTML
```

**Check firewall:**
```bash
sudo ufw status
# If enabled, you may need to allow port 5000
sudo ufw allow 5000
```

---

### Permission Errors (GPIO)

```bash
# Add user to gpio group
sudo usermod -a -G gpio pi

# Check group membership
groups pi

# Log out and back in for changes to take effect
```

---

## Editing the Service File

If you need to change the service configuration:

```bash
# 1. Edit the service file
sudo nano /etc/systemd/system/x1c-heater.service

# 2. Save and exit (Ctrl+X, Y, Enter)

# 3. Reload systemd (IMPORTANT!)
sudo systemctl daemon-reload

# 4. Restart service
sudo systemctl restart x1c-heater

# 5. Verify
sudo systemctl status x1c-heater
```

**Common things to edit:**
- Python path (if venv location changes)
- Working directory
- User
- Restart behavior

---

## Quick Reference Card

### Most Common Commands

| Task | Command |
|------|---------|
| Restart after code change | `sudo systemctl restart x1c-heater` |
| Check if running | `sudo systemctl status x1c-heater` |
| View recent logs | `sudo journalctl -u x1c-heater.service -n 50` |
| Watch live logs | `sudo journalctl -u x1c-heater.service -f` |
| Stop service | `sudo systemctl stop x1c-heater` |
| Start service | `sudo systemctl start x1c-heater` |

### File Locations

| Item | Path |
|------|------|
| Service file | `/etc/systemd/system/x1c-heater.service` |
| Python code | `/home/pi/x1c_controller/x1c_heater.py` |
| Virtual environment | `/home/pi/x1c_controller/venv/` |
| Settings file | `/home/pi/x1c_controller/heater_settings.json` |
| Logs | `sudo journalctl -u x1c-heater.service` |

---

## Development Workflow Example

Here's a typical workflow when developing new features:

```bash
# 1. Stop the service so you can run manually
sudo systemctl stop x1c-heater

# 2. Make your code changes
cd /home/pi/x1c_controller
nano x1c_heater.py

# 3. Test manually
source venv/bin/activate
python3 x1c_heater.py
# Test in browser: http://<pi-ip>:5000
# Press Ctrl+C when done testing
deactivate

# 4. If everything works, restart service
sudo systemctl start x1c-heater

# 5. Verify service is running
sudo systemctl status x1c-heater

# 6. Check logs for any issues
sudo journalctl -u x1c-heater.service -n 30 -f
```

---

## Auto-Start on Boot

### Enable Auto-Start
```bash
sudo systemctl enable x1c-heater
```

### Disable Auto-Start
```bash
sudo systemctl disable x1c-heater
```

### Check if Auto-Start is Enabled
```bash
systemctl is-enabled x1c-heater
# Returns: enabled or disabled
```

---

## Performance Monitoring

### Check Resource Usage
```bash
# CPU and memory usage
systemctl status x1c-heater

# Detailed resource info
sudo systemd-cgtop
# Look for x1c-heater.service
```

### Check How Long Service Has Been Running
```bash
systemctl status x1c-heater | grep "Active:"
# Shows how long it's been running
```

---

## Emergency Recovery

If something goes very wrong:

```bash
# 1. Stop the service
sudo systemctl stop x1c-heater

# 2. Disable auto-start
sudo systemctl disable x1c-heater

# 3. Restore from backup
cd /home/pi/x1c_controller
ls x1c_heater.py.backup.*  # List backups
cp x1c_heater.py.backup.YYYYMMDD_HHMMSS x1c_heater.py

# 4. Test manually
source venv/bin/activate
python3 x1c_heater.py
# Verify it works, then Ctrl+C
deactivate

# 5. Re-enable and start service
sudo systemctl enable x1c-heater
sudo systemctl start x1c-heater
```

---

## Tips & Best Practices

1. **Always check logs after restart:**
   ```bash
   sudo systemctl restart x1c-heater && sudo journalctl -u x1c-heater.service -f
   ```

2. **Create backups before major changes:**
   ```bash
   cp x1c_heater.py x1c_heater.py.backup.$(date +%Y%m%d_%H%M%S)
   ```

3. **Test manually before enabling service:**
   Run the Python script manually first to catch errors early.

4. **Use git for version control:**
   ```bash
   git add .
   git commit -m "Description of changes"
   git push
   ```

5. **Monitor logs during first run:**
   After deploying changes, watch logs for at least 5 minutes to catch any issues.

---

**Need help?** Check the logs first:
```bash
sudo journalctl -u x1c-heater.service -n 100
```

Most issues will show up in the logs with helpful error messages!
