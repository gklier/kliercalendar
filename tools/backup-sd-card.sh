#!/usr/bin/env bash
# Make a compressed image of the TV's SD card, on a Mac.
#
#   ./tools/backup-sd-card.sh [--dry-run] [folder to save into]
#
# Put the card in a reader (ideally after shutting the Pi down), run this, and pick the card.
# It only READS from the card, and refuses anything that isn't an external disk. To use the
# image later: Raspberry Pi Imager -> Choose OS -> Use custom -> pick the .img.gz -> write it.
# --dry-run lists the disks and shows what it would do without reading anything.
set -euo pipefail

DRY=0
if [[ "${1:-}" == "--dry-run" ]]; then DRY=1; shift; fi
OUT_DIR="${1:-$HOME/Documents/tv-card-images}"

[[ "$(uname)" == Darwin ]] || { echo "This script is for macOS. On Linux, use: sudo dd if=/dev/sdX bs=4M status=progress | gzip > card.img.gz" >&2; exit 1; }
command -v diskutil >/dev/null || { echo "diskutil not found." >&2; exit 1; }

echo "External disks:"
diskutil list external physical || true
echo
read -r -p "Which disk is the SD card? (for example disk4, or q to quit): " DISK
[[ "$DISK" =~ ^disk[0-9]+$ ]] || { echo "Nothing chosen. Nothing was read."; exit 0; }

info="$(diskutil info "/dev/$DISK" 2>/dev/null)" || { echo "There is no disk called $DISK." >&2; exit 1; }
location="$(printf '%s\n' "$info" | awk -F': +' '/Device Location/ {print $2}')"
if [[ "$location" != External ]]; then
  echo "$DISK is not an external disk (it says: ${location:-unknown}). Refusing: this tool is only for the SD card." >&2
  exit 1
fi
name="$(printf '%s\n' "$info" | awk -F': +' '/Media Name|Device \/ Media Name/ {print $2; exit}')"
size="$(printf '%s\n' "$info" | awk -F': +' '/Disk Size/ {print $2; exit}')"
echo
echo "  Disk:  /dev/$DISK"
echo "  Name:  ${name:-unknown}"
echo "  Size:  ${size:-unknown}"
echo
read -r -p "Is that the SD card? Type its disk name again ($DISK) to continue: " CONFIRM
[[ "$CONFIRM" == "$DISK" ]] || { echo "Not confirmed. Nothing was read."; exit 0; }

mkdir -p "$OUT_DIR"
OUT="$OUT_DIR/tv-card-$(date +%Y%m%d-%H%M).img.gz"

if [[ $DRY == 1 ]]; then
  echo "Dry run. It would unmount /dev/$DISK, then read /dev/r$DISK into $OUT"
  exit 0
fi

echo "Unmounting the card (it is not erased or changed)..."
diskutil unmountDisk "/dev/$DISK" >/dev/null
echo "Reading the card into:"
echo "  $OUT"
echo "This takes a while for a big card. You may be asked for your Mac password. Press Ctrl+T to see progress."
if command -v pv >/dev/null; then
  sudo dd if="/dev/r$DISK" bs=4m 2>/dev/null | pv | gzip -c > "$OUT"
else
  sudo dd if="/dev/r$DISK" bs=4m | gzip -c > "$OUT"
fi

echo "Checking the file..."
gzip -t "$OUT"
shasum -a 256 "$OUT" | tee "$OUT.sha256"
echo
echo "Done. Eject the card, and keep $OUT somewhere safe."
echo "To restore: Raspberry Pi Imager -> Choose OS -> Use custom -> pick this file -> write it to a new card."
diskutil eject "/dev/$DISK" >/dev/null 2>&1 || true
