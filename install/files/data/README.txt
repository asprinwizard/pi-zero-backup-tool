P2 BACKUP DRIVE
===============

This drive holds backups of Panasonic P2 cards made by the P2 Backup device.

Layout
------
  P2-01/, P2-02/, ...   one folder per physical P2 card (number your cards with stickers)
    <date>_<id>/        one folder per format of that card. Each is a complete
                        card layout (CONTENTS/, LASTCLIP.TXT) that P2 Viewer and
                        editing software can open directly.
      _p2backup/        backup records: manifest (checksums), logs, and any
                        files that were moved aside instead of overwritten.
  UNSORTED/             backups where no card number was chosen.
  backups.csv           one line per backup run.

Before formatting a P2 card
---------------------------
The device only says a card "can be formatted" once every file has been
copied and verified. That backup is still a single copy on one SD card.
Copy it to a PC (or other storage) before you format the P2 card.

Unplugging
----------
Eject this drive on your PC, then wait until the device's screen shows
"Safe to unplug" before pulling the cable. Make sure the UPS switch is OFF
whenever the device is connected to a PC.

macOS may create hidden files here (._*, .Trashes, .fseventsd). They are
harmless and ignored by the device.
