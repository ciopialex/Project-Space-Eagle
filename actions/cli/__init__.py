"""Native interfaces, one per platform, behind one contract.

The eagle used to press fake keys: `volume_up()` was five
`pyautogui.press("volumeup")` calls that returned nothing, on a machine where
`wpctl set-volume` does it exactly and `wpctl get-volume` says what happened.
Measured: 12ms against a 250ms floor, 20x, and the CLI returns 0.95 while the
keypresses return silence.

Everything under here obeys the same three rules: speak the platform's real
interface, read the resulting state back, and report honestly when a backend
is missing so the capability ladder can drop a rung.
"""
