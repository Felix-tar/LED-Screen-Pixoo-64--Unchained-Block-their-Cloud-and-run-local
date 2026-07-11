
# Pixoo-64 Unchained

Free your Divoom Pixoo-64 from its cloud dependency and control it entirely from your local network.

Pixoo-64 Unchained provides a local Divoom server emulator, a browser-based editor, MQTT integration, and a setup guide for redirecting the display’s cloud traffic to your own Raspberry Pi or home server.

The goal is to keep the Pixoo-64 usable even without access to Divoom’s public servers while also providing more possibilities than the official smartphone application.

> This is an independent open-source project and is not affiliated with, endorsed by, or supported by Divoom.

---

## Why This Project Exists

The Divoom Pixoo-64 supports a local HTTP API, but the display still tries to contact Divoom servers during startup.

The initial cloud connection appears to be part of the activation or initialization process required before the local API becomes fully available.

This means that simply blocking all internet access can prevent the display from operating correctly.

Pixoo-64 Unchained solves this problem by redirecting the relevant Divoom server request inside the local network.

Instead of reaching the real Divoom infrastructure, the Pixoo-64 connects to a local server that reproduces the expected startup handshake.

After the simulated handshake is completed, the local API can be used without the display needing to communicate with the real Divoom servers.

---

## Main Features

* Local replacement for the required Divoom startup server
* Simulated initialization handshake
* Local-only control of the Pixoo-64
* Docker-based deployment
* Browser-based Pixoo editor
* Tools for displaying custom data
* MQTT integration
* Support for server statistics and monitoring data
* Custom text, images, animations, dashboards, and status screens
* No official Divoom smartphone app required for normal operation
* Can run on a Raspberry Pi or another home server
* Designed for self-hosted and privacy-focused environments

---

## How It Works

When the Pixoo-64 boots, it attempts to connect to a Divoom server.

Normally, the request would leave the local network and reach Divoom’s cloud infrastructure.

With this project, the request is redirected to a local server:

```text
Pixoo-64
    |
    | Requests Divoom server hostname
    v
Local DNS server
    |
    | Returns the IP address of the local emulator
    v
Raspberry Pi / Home Server
    |
    | Docker container simulates the expected server response
    v
Pixoo-64 completes its startup handshake
    |
    v
Local Pixoo API becomes available
```

The Pixoo-64 still believes that it has contacted the expected server, but the entire process happens inside the local network.

The screen can then be controlled through the existing local API.

---

## Architecture

A typical installation contains the following components:

```text
Local Network
├── Router or DHCP server
│   └── Permanent IP reservation for the Pixoo-64
│
├── Local DNS server
│   └── Redirects the required Divoom hostname
│
├── Raspberry Pi or Home Server
│   ├── Docker
│   ├── Local Divoom server emulator
│   ├── Pixoo web editor
│   ├── API and toolbox
│   └── Optional MQTT client
│
└── Divoom Pixoo-64
    └── Connects only to services inside the local network
```

The local emulator should use a stable IP address so that the DNS redirection always points to the correct destination.

Depending on the network configuration, the Docker container may use its own dedicated IP address.

---

## Requirements

### Hardware

* Divoom Pixoo-64
* Raspberry Pi, mini PC, NAS, or home server
* A device capable of running Docker
* Local network access

A Raspberry Pi 4 or Raspberry Pi 5 is recommended, but the project should also work on most Linux-based home servers.

### Network Access

You need administrative access to:

* Your DHCP server or router
* Your local DNS server
* The device that will host the Docker container

DHCP access is required to give the Pixoo-64 a persistent IP address.

DNS access is required to redirect the relevant Divoom server hostname to the local server emulator.

### Software

* Linux
* Docker
* Docker Compose
* A local DNS solution such as Pi-hole, AdGuard Home, dnsmasq, Unbound, or router-based DNS overrides
* Optional MQTT broker such as Mosquitto

---

## Important Network Concept

The Pixoo-64 should receive the same IP address after every restart.

This can normally be configured using a DHCP reservation based on the device’s MAC address.

Example:

```text
Pixoo-64 MAC address: AA:BB:CC:DD:EE:FF
Reserved IP address: 192.168.1.64
```

The local server or container also needs a permanent address.

Example:

```text
Pixoo-64:             192.168.1.64
Pixoo local server:   192.168.1.160
Raspberry Pi host:    192.168.1.10
```

These addresses are only examples. Use addresses that match your own network.

---

## DNS Redirection

The required Divoom hostname must resolve to the local emulator instead of the real public server.

The exact configuration depends on the DNS software being used.

A simplified example could look like this:

```text
divoom-server.example.com -> 192.168.1.160
```

The Pixoo-64 sends its startup request to the expected hostname.

The local DNS server responds with the address of the local Docker container.

The request therefore never reaches the public Divoom server.

> The actual hostname and redirection settings used by this project are documented in the setup section and configuration files.

---

## Docker Deployment

Clone the repository:

```bash
git clone https://github.com/YOUR-USERNAME/pixoo-64-unchained.git
cd pixoo-64-unchained
```

Copy the example environment file:

```bash
cp .env.example .env
```

Edit the configuration:

```bash
nano .env
```

Example configuration:

```env
PIXOO_IP=192.168.1.64
PIXOO_ADVERTISE_IP=192.168.1.160
WEB_PORT=8080

MQTT_ENABLED=true
MQTT_HOST=192.168.1.20
MQTT_PORT=1883
MQTT_USERNAME=
MQTT_PASSWORD=
```

Start the containers:

```bash
docker compose up -d
```

Check the running services:

```bash
docker compose ps
```

View logs:

```bash
docker compose logs -f
```

Stop the project:

```bash
docker compose down
```

---

## Host and Container Networking

The server emulator may run with a dedicated IP address inside the local network.

This is useful because the DNS server can point directly to the container instead of the Raspberry Pi host.

Depending on the Docker configuration, a `macvlan` network may be used.

Example:

```yaml
networks:
  pixoo_network:
    driver: macvlan
    driver_opts:
      parent: eth0
    ipam:
      config:
        - subnet: 192.168.1.0/24
          gateway: 192.168.1.1
          ip_range: 192.168.1.160/32
```

A macvlan container may not be directly reachable from its Docker host by default.

If host-to-container communication is required, an additional host-side macvlan interface can be created.

The repository contains helper and diagnostic scripts for checking this configuration.

---

## Web Editor

The included web interface provides a simple way to control the Pixoo-64 from a browser.

Open the editor using the configured host and port:

```text
http://192.168.1.10:8080
```

Depending on the current project version, the editor can be used to:

* Send text to the display
* Draw pixels
* Create basic layouts
* Display images
* Show status information
* Test API commands
* Build simple dashboards
* Connect external data sources
* Manage scenes and display pages

The editor communicates with the Pixoo-64 through the local API.

---

## MQTT Integration

Pixoo-64 Unchained can subscribe to MQTT topics and display received data.

This makes it possible to connect the screen to home automation systems, servers, sensors, and custom applications.

Example MQTT topics:

```text
home/server/cpu
home/server/memory
home/server/temperature
home/network/peers
home/network/status
home/pixoo/message
```

Example payload:

```json
{
  "title": "Server Status",
  "cpu": 23,
  "memory": 61,
  "temperature": 48.5,
  "online_peers": 12
}
```

The data can be transformed into a layout and sent to the display.

---

## Example Use Cases

### Server Monitoring

Display information such as:

* CPU usage
* Memory usage
* Disk usage
* CPU temperature
* Docker container status
* Uptime
* Network traffic

### Network Monitoring

Display:

* Connected devices
* VPN peers
* Online services
* Ping status
* Internet connection state
* Local IP addresses

### Home Automation

Connect the Pixoo-64 to:

* Home Assistant
* Node-RED
* OpenHAB
* ioBroker
* MQTT sensors
* Smart-home events

### Notifications

Show:

* Incoming messages
* System warnings
* Calendar events
* Build status
* GitHub notifications
* Backup results
* Doorbell events

### Custom Dashboards

Create screens for:

* Weather information
* Energy usage
* Solar production
* Stock or cryptocurrency data
* Sports results
* Countdown timers
* Project status
* Personal reminders

---

## Example MQTT Publisher

A simple Python example:

```python
import json
import time

import psutil
import paho.mqtt.client as mqtt


MQTT_HOST = "192.168.1.20"
MQTT_PORT = 1883
MQTT_TOPIC = "home/server/status"


client = mqtt.Client()
client.connect(MQTT_HOST, MQTT_PORT, 60)

while True:
    payload = {
        "cpu": psutil.cpu_percent(),
        "memory": psutil.virtual_memory().percent,
        "disk": psutil.disk_usage("/").percent,
        "temperature": None,
    }

    client.publish(MQTT_TOPIC, json.dumps(payload))
    time.sleep(10)
```

The Pixoo software can subscribe to this topic and convert the values into a visual status screen.

---

## Diagnostics

Before starting the complete system, verify the following:

1. The Pixoo-64 receives its reserved IP address.
2. The local server has its expected IP address.
3. The DNS hostname resolves to the local server.
4. The Docker container is running.
5. The simulated handshake endpoint is reachable.
6. The Pixoo-64 completes its startup process.
7. The local Pixoo API responds.
8. The web editor can communicate with the display.

Example DNS test:

```bash
nslookup REQUIRED_DIVOOM_HOSTNAME
```

The returned address should be the local emulator IP.

Example connectivity test:

```bash
ping 192.168.1.160
```

Example Docker check:

```bash
docker ps
```

Example log check:

```bash
docker compose logs --tail=100
```

---

## Troubleshooting

### The Pixoo-64 Still Connects to the Internet

Check that the device is actually using your local DNS server.

Some routers distribute their own DNS address through DHCP and forward requests internally.

Verify the DNS server assigned to the Pixoo-64.

You may also need firewall rules to prevent the device from using alternative DNS servers.

### The Handshake Does Not Complete

Check:

* The redirected hostname
* The emulator logs
* The configured advertised IP
* Container port mappings
* Firewall rules
* Docker networking
* DNS cache

Restart the Pixoo-64 after changing DNS records.

### The Local API Is Not Available

The startup handshake may not have completed correctly.

Check whether the Pixoo-64 successfully reached the local emulator before attempting to use the local API.

### The Container Is Reachable From the Network but Not From the Host

This is common when using Docker macvlan networks.

Use the included host-access helper or create a host-side macvlan interface.

### MQTT Data Is Not Displayed

Check:

* MQTT broker address
* MQTT port
* Topic name
* Credentials
* JSON format
* Container network access
* Subscription logs

---

## Security Notes

This project changes the network path used by the Pixoo-64.

Only deploy it on networks you control.

Recommended precautions:

* Do not expose the emulator directly to the public internet.
* Keep the web editor inside the local network.
* Use firewall rules where appropriate.
* Use authentication for MQTT.
* Use strong passwords.
* Keep Docker and the host operating system updated.
* Back up configuration files before making changes.
* Document all DNS overrides.

---

## Privacy

The main objective of this project is to keep Pixoo-64 communication inside the local network.

After successful configuration:

* The Pixoo-64 contacts the local emulator.
* The expected startup request is answered locally.
* Display data stays inside the local network.
* Custom data sources can be self-hosted.
* The official smartphone application is not required for daily operation.

Additional firewall rules may be used to block any remaining outbound traffic from the Pixoo-64.

---

## Current Project Status

The project is under active development.

The current implementation focuses on:

* Reproducing the required startup handshake
* Running the emulator inside Docker
* Using a dedicated local container IP
* Redirecting the Divoom hostname through local DNS
* Controlling the display through its local API
* Providing a browser-based editor
* Receiving and displaying MQTT data
* Offering diagnostic scripts for network and container checks

Some parts may still require manual configuration depending on the router, DNS server, operating system, and network layout.

---

## Planned Features

Possible future improvements include:

* Easier installation script
* Automatic network checks
* Improved visual editor
* Drag-and-drop widgets
* Scene scheduling
* Multiple display profiles
* Home Assistant integration
* Node-RED examples
* Additional MQTT templates
* REST API integrations
* Custom animations
* Dashboard presets
* Backup and restore
* Authentication for the web interface
* Support for additional Divoom devices

---

## Contributing

Contributions are welcome.

You can help by:

* Testing the project on different networks
* Reporting compatibility issues
* Improving the documentation
* Adding new widgets
* Creating MQTT templates
* Improving the web editor
* Adding Home Assistant or Node-RED integrations
* Testing other Divoom devices
* Reviewing the server handshake implementation

Before submitting a pull request, please describe:

* What was changed
* Why the change is required
* Which hardware and software were used
* How the change was tested

---

## Reporting Issues

When reporting a problem, include:

```text
Pixoo model:
Host hardware:
Operating system:
Docker version:
DNS server:
Router or DHCP server:
Pixoo IP:
Server or container IP:
Relevant logs:
Steps to reproduce:
```

Do not publish passwords, private IP configurations you consider sensitive, API keys, or MQTT credentials.

---

## Legal Notice

This project is intended for interoperability, research, education, privacy, and local control of hardware owned by the user.

No proprietary Divoom software, firmware, credentials, certificates, or copyrighted application files are distributed by this repository.

Users are responsible for ensuring that their use of this project complies with applicable laws, license agreements, network policies, and device warranties.

The Divoom name, Pixoo name, and related trademarks belong to their respective owners.

---

## Disclaimer

This software is provided without warranty.

Network changes, DNS overrides, Docker networking, and firewall rules can interrupt access to devices or services when configured incorrectly.

Use the project at your own risk and keep backups of all existing network configurations.

---

## License

This project is released under the MIT License unless otherwise stated.

See the `LICENSE` file for details.

---

## Summary

Pixoo-64 Unchained replaces the cloud-dependent startup path of the Divoom Pixoo-64 with a local self-hosted service.

It allows the screen to initialize through a simulated local handshake and then exposes the existing local API for custom use.

Combined with the web editor and MQTT support, the Pixoo-64 can become a flexible local dashboard for server monitoring, home automation, network information, notifications, and almost any other data you want to display.
