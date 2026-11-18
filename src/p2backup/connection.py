"""Who has the data partition: the Pi, or the PC. Plan §3.3.

Modes:
    idle        nothing connected; data area mounted on the Pi
    camera      a USB device on the host port; data area mounted on the Pi
    pc          a PC has the data partition (Pi has it unmounted)
    pc_ejected  the PC ejected it and the Pi has flushed: "Safe to unplug"
    error       the data area could not be checked/mounted/released

Order of operations is the safety rule: the data area is unmounted (and the
loop device detached) before the partition is handed to the PC, and the PC
loses it before the Pi checks and mounts it again.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from .storage import StorageError
from .usb import UsbStatus

log = logging.getLogger("p2backup.connection")

# The UDC can briefly leave "configured" while a PC resets or re-enumerates
# the device; only treat the PC as gone after this long.
PC_GONE_AFTER_S = 2.0


class Connection:
    def __init__(self, gadget, storage, busy: Callable[[str], None] = lambda text: None,
                 clock: Callable[[], float] = time.monotonic):
        self.gadget = gadget
        self.storage = storage
        self.busy = busy                    # shows a "please wait" message during slow steps
        self.clock = clock
        self.mode = "idle"
        self.error: str | None = None
        self.repaired = False               # fsck repaired the data area on the last mount
        self._pc_failed = False             # don't retry entering PC mode until replugged
        self._pc_gone_since: float | None = None

    @property
    def pc_has_drive(self) -> bool:
        return self.mode == "pc"

    # ---- start-up ----------------------------------------------------
    def start(self, st: UsbStatus) -> None:
        """Plan's startup rule: look at the gadget before mounting anything."""
        if st.medium:
            # The app restarted while a PC had the drive: leave it with the PC.
            log.info("start: PC already has the drive")
            self.storage.pc_writeback(True)
            self.mode = "pc"
            return
        if st.pc:
            # Booted from a PC: hand it the drive straight away (no local mount first).
            self._enter_pc()
            return
        self._mount_local(st)
        self.update(st)

    # ---- changes -----------------------------------------------------
    def update(self, st: UsbStatus) -> bool:
        """React to a new USB status. Returns True if the mode changed."""
        before = (self.mode, self.error)
        if not st.pc:
            self._pc_failed = False

        if self.mode in ("pc", "pc_ejected"):
            self._update_pc(st)
        elif st.pc and not st.medium and not self._pc_failed:
            self._enter_pc()
        elif self.mode != "error":
            self.mode = "camera" if st.camera else "idle"
        return (self.mode, self.error) != before

    def _update_pc(self, st: UsbStatus) -> None:
        if st.pc:
            self._pc_gone_since = None
            if self.mode == "pc" and not st.medium:
                # The PC ejected the drive. Its last writes may still be in RAM
                # (an eject does not fsync), so flush before saying it's safe.
                self.busy("Saving...")
                self.storage.flush()
                log.info("PC ejected the drive; flushed")
                self.mode = "pc_ejected"
            return
        now = self.clock()
        if self._pc_gone_since is None:
            self._pc_gone_since = now
            return
        if now - self._pc_gone_since < PC_GONE_AFTER_S:
            return
        # The PC is gone (unplugged with the UPS on, or asleep).
        self._pc_gone_since = None
        log.info("PC disconnected (mode was %s)", self.mode)
        if self.gadget.medium():
            self.gadget.detach()
        self.storage.flush()
        self._mount_local(st)
        if self.mode != "error":
            self.mode = "camera" if st.camera else "idle"

    def _enter_pc(self) -> None:
        self.busy("Connecting to PC...")
        try:
            self.storage.unmount()
        except StorageError as e:
            log.error("cannot hand the drive to the PC: %s", e)
            self._pc_failed = True
            self.mode, self.error = "error", f"Could not {e}"
            return
        self.storage.pc_writeback(True)
        self.gadget.attach(self.storage.data_dev)
        self.mode, self.error = "pc", None
        log.info("PC mode: drive handed to the PC")

    def _mount_local(self, st: UsbStatus) -> None:
        self.storage.pc_writeback(False)
        self.busy("Checking drive...")
        try:
            self.repaired = self.storage.mount().repaired
            self.error = None
            if self.mode == "error":
                self.mode = "idle"
        except StorageError as e:
            log.error("data area not available: %s", e)
            self.mode, self.error = "error", str(e).capitalize()

    def retry(self, st: UsbStatus) -> None:
        """Try again after an error (e.g. a key press on the error screen)."""
        self.mode, self.error = "idle", None
        self._pc_failed = False
        self._mount_local(st)
        self.update(st)
