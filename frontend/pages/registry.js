// Central page registry — the single place that knows every route id, its
// sidebar label/icon, and how to lazily load its module. Adding a tenth page
// means adding one line here plus the page module itself; nothing else in
// the app needs to change.

// `key` is the second letter of the `g <key>` keyboard-shortcut chord
// (core/shortcuts.js) — picked explicitly so pages sharing a first letter
// (System/Services/Security) don't collide.
export const NAV = [
  // Dashboard: understand the current state before changing anything.
  { id: 'overview', label: 'Overview', icon: 'home', category: 'Dashboard', key: 'o', loader: () => import('./overview.js') },
  { id: 'monitoring', label: 'Monitoring', icon: 'activity', category: 'Dashboard', key: 'm', loader: () => import('./monitoring.js') },
  { id: 'telemetry', label: 'Telemetry', icon: 'signal', category: 'Dashboard', key: '4', loader: () => import('./telemetry.js') },
  { id: 'faults', label: 'Faults', icon: 'alerts', category: 'Dashboard', key: '2', loader: () => import('./faults.js') },
  { id: 'alerts', label: 'Alarms', icon: 'alerts', category: 'Dashboard', key: 'a', loader: () => import('./alerts.js') },
  { id: 'logs', label: 'Logs', icon: 'logs', category: 'Dashboard', key: 'l', loader: () => import('./logs.js') },

  // System: local identity and lifecycle controls.
  { id: 'system', label: 'System settings', icon: 'settings', category: 'System', key: 's', loader: () => import('./system.js') },
  { id: 'time', label: 'Clock', icon: 'clock', category: 'Time & Sync', key: 't', loader: () => import('./time.js') },
  { id: 'ntp', label: 'NTP', icon: 'clock', category: 'Time & Sync', key: '8', loader: () => import('./ntp.js') },
  { id: 'ptp', label: 'PTP', icon: 'activity', category: 'Time & Sync', key: '9', loader: () => import('./ptp.js') },
  { id: 'gps', label: 'GPS / GNSS', icon: 'signal', category: 'Time & Sync', key: '6', loader: () => import('./gps.js') },
  { id: 'power', label: 'Power', icon: 'power', category: 'System', key: 'p', loader: () => import('./power.js') },
  { id: 'system-control', label: 'System Control', icon: 'settings', category: 'System', key: '0', loader: () => import('./system-control.js'), adminOnly: true },

  // Connectivity: network links and connected services.
  { id: 'wired', label: 'Wired', icon: 'ethernet', category: 'Connectivity', key: 'n', loader: () => import('./wired.js') },
  { id: 'wireless', label: 'Wireless', icon: 'wifi', category: 'Connectivity', key: 'h', loader: () => import('./wireless.js') },
  { id: '5g', label: '5G', icon: 'signal', category: 'Connectivity', key: '5', loader: () => import('./fiveg.js') },
  { id: 'services', label: 'Services', icon: 'services', category: 'Connectivity', key: 'v', loader: () => import('./services.js') },
  { id: 'cloud', label: 'Cloud', icon: 'cloud', category: 'Connectivity', key: 'c', loader: () => import('./cloud.js') },

  // Maintenance: updates and configuration recovery.
  { id: 'updates', label: 'Updates', icon: 'updates', category: 'Maintenance', key: 'u', loader: () => import('./updates.js') },
  { id: 'remote-logs', label: 'Remote Logs', icon: 'logs', category: 'Maintenance', key: '6', loader: () => import('./remote-logs.js'), adminOnly: true },
  { id: 'remote-updates', label: 'Remote Updates', icon: 'cloud', category: 'Maintenance', key: '7', loader: () => import('./remote-updates.js'), adminOnly: true },
  { id: 'backup', label: 'Backup & Restore', icon: 'archive', category: 'Maintenance', key: 'b', loader: () => import('./backup.js'), adminOnly: true },

  // Security: access, exposure, trust, and user administration.
  { id: 'security', label: 'Security', icon: 'security', category: 'Security', key: 'e', loader: () => import('./security.js'), adminOnly: true },
  { id: 'ssh', label: 'SSH & Console', icon: 'terminal', category: 'Security', key: 'j', loader: () => import('./ssh.js'), adminOnly: true },
  { id: 'firewall', label: 'Firewall', icon: 'security', category: 'Security', key: 'f', loader: () => import('./firewall.js'), adminOnly: true },
  { id: 'secure-boot', label: 'Secure Boot', icon: 'lock', category: 'Security', key: 'r', loader: () => import('./secure-boot.js'), adminOnly: true },
  { id: 'users', label: 'Users & Groups', icon: 'users', category: 'Security', key: 'g', loader: () => import('./users.js'), adminOnly: true },

  // Hardware: individual peripheral controls.
  { id: 'display', label: 'Display', icon: 'monitor', category: 'Hardware', key: 'i', loader: () => import('./display.js') },
  { id: 'leds', label: 'Board LEDs', icon: 'lightbulb', category: 'Hardware', key: '1', loader: () => import('./leds.js'), adminOnly: true },
  { id: 'gpio', label: 'GPIO', icon: 'io', category: 'Hardware', key: 'd', loader: () => import('./gpio.js'), adminOnly: true },
  { id: 'serial', label: 'Serial Peripherals', icon: 'cable', category: 'Hardware', key: 'w', loader: () => import('./serial.js'), adminOnly: true },
  { id: 'can', label: 'CAN', icon: 'network', category: 'Hardware', key: 'z', loader: () => import('./can.js'), adminOnly: true },
  { id: 'usb', label: 'USB', icon: 'usb', category: 'Hardware', key: 'x', loader: () => import('./usb.js') },
  { id: 'pci', label: 'PCI', icon: 'io', category: 'Hardware', key: 'y', loader: () => import('./pci.js') },
  { id: 'mcu', label: 'MCU', icon: 'cpu', category: 'Hardware', key: 'q', loader: () => import('./mcu.js') },
  { id: 'camera', label: 'Camera', icon: 'camera', category: 'Hardware', key: 'k', loader: () => import('./camera.js') },
];

export const ROUTE_IDS = NAV.map(n => n.id);
export const routeMeta = (id) => NAV.find(n => n.id === id);
