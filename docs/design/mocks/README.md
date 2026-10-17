# Design mocks: #65, #66, #67 (Wren)

800x480 PNGs rendered with headless Chrome from the HTML here. `kiosk-style.css` is a verbatim copy of
`static/style.css` from origin/main @555a195; `_shutdown.css` / `_menu.css` and inline `<style>` hold only the
proposed changes. Mocks only. Not app code.

- `65-shutdown-A.png` (recommended): spinner + "Shutting down" + "Unplug when the spinner stops"
- `65-shutdown-B.png`: same, with "Wait 10 seconds, then unplug"
- `66-menu.png`: menu with bottom row Close (outline, left) / Shut down (#C62828, right)
- `66-shutdown-confirm.png`: "Shut down Memory Frame?" with Cancel (default) / Shut down (red)
- `67-dev-mode-confirm.png`: "Switch to dev mode?" with Cancel (default) / Switch (amber #FFB000)
