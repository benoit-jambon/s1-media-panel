# Document seat-scoped USB access for the LCD

The FalledCan rule assigns the LCD USB vendor/product pair and a separate CH340 serial device to plugdev with mode 0660. This project uses a narrower template for the LCD with USB device type and revision checks. A static group can work without an active graphical session. For headless operation, use a dedicated administrator-managed group with mode 0660 and the same device match, rather than granting access to all USB or HID endpoints.

For interactive Linux sessions managed by logind, install a rule before seat ACL processing, for example as 70-s1-lcd.rules. Match usb_device, VID 04d9, PID fd01 and bcdDevice 0110. Use MODE="0600" and TAG+="uaccess". Optionally add the site's USB topology selector if there are multiple matching devices. Run the panel as a systemd user service. A user manager or lingering alone does not grant the seat ACL. Confirm the active session and inspect the usbfs ACL after applying the rule. The ACL covers the composite device; the PyUSB driver claims interface 01 only. Leave LED and unrelated devices outside the rule.

Expected: the authorized session can open the LCD, while unrelated devices keep their permissions. Previously observed: access denied before applying the local seat rule, and successful idle and playback views after access was granted and transport and renderer fixes were applied. No generic rule was applied during this review. Confirm ACL revocation on seat changes and access in the documented headless setup before claiming support.

## Apply or remove the template

Inspect `contrib/udev/70-s1-media-panel.rules` and adjust its selector before copying it to the system udev directory. Check for an existing rule with this name before copying. Install the template only if that destination is free or after reviewing the differences and backing up the existing file. The following commands require administrative privileges:

```sh
sudo install -m 644 contrib/udev/70-s1-media-panel.rules /etc/udev/rules.d/70-s1-media-panel.rules
sudo udevadm control --reload-rules
```

Use a new device event or a targeted `udevadm trigger` against the selected USB device's sysfs path. Check the dynamic bus and device numbers and use `getfacl` on the corresponding usbfs node. Remove only this installed rule during uninstallation. Reload rules, then recreate the device node or have an administrator verify and revoke any residual ACL. Replacing the rule does not necessarily reset ACLs on already open devices.
