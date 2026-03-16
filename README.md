<p align="center">
  <img src="media/GlyphKit.png" alt="GlyphKit" width="200">
</p>

<h1 align="center">GlyphKit</h1>

<p align="center">
  A tiny, always-on-top Unicode palette for Windows.<br>
  Click a glyph. It's in your clipboard. Or already pasted.
</p>

<p align="center">
  <a href="https://github.com/Lunyxium/glyphkit/releases/latest"><img src="https://img.shields.io/github/v/release/Lunyxium/glyphkit?style=flat-square&color=2dd4bf" alt="Release"></a>
  <img src="https://img.shields.io/badge/platform-Windows%2010%20%2F%2011-0078D4?style=flat-square" alt="Windows 10/11">
  <img src="https://img.shields.io/github/license/Lunyxium/glyphkit?style=flat-square&_t=1" alt="License">
  <img src="https://img.shields.io/badge/dependencies-zero-2dd4bf?style=flat-square" alt="Zero dependencies">
</p>

<p align="center">
  Part of the <a href="https://github.com/stars/Lunyxium/lists/tools">Lunyxium Tools</a> collection.
</p>

---

<p align="center">
  <img src="media/GlyphKit.gif" alt="GlyphKit — click a glyph, it's already in your text" width="720">
</p>

## Why

Writing a formula that needs `∑` or `∂`? A doc where `½` looks cleaner than `1/2`, or `→` beats `-->`?
Greek letters for a proof, `≈` instead of `~=`, currency symbols, arrows, fractions?

You know the drill: open the Windows character map. Lose focus. Scroll. Squint. Click. Copy. Alt-tab back. Paste. Repeat. For every single glyph.

Or you google it. Three tabs later you're reading the Wikipedia article on the Greek alphabet.

The floating touch keyboard and other tools exist, sure. Great for emoji. Finding `∑` or `⊕`? Thin coverage, cluttered layout, scrolling for days, it still steals focus or closes every time. Built for touchscreens, not your workflow.

*GlyphKit exists because that gap shouldn't be there.* It floats, never steals your cursor, puts every symbol one click away. In AUTO mode it even pastes for you – click `∞` and it lands right in your text field – while the tool stays available to you on top of your window for as long as you need it.

## Features

### Always there, never in the way

GlyphKit stays on top of all your windows but never takes focus. Click a character and your cursor stays exactly where it was. It doesn't appear in your taskbar, doesn't flash in the alt-tab list, and fades to near-transparent when you're not using it. It's there when you need it and invisible when you don't.

Toggle visibility from anywhere with **Ctrl + Alt + G**. Close with **Esc**.

### 433 characters across 13 categories

Math, arrows, Greek, logic, sets, comparison, geometry, subscripts, superscripts, box-drawing, fractions, currency, and miscellaneous symbols — all searchable by name.

### 4 copy modes

Cycle through them in the title bar:

| Mode     | What it does                                               |
| -------- | ---------------------------------------------------------- |
| **COPY** | Copies the character to your clipboard                     |
| **AUTO** | Copies and instantly pastes into whatever you're typing in |
| **HTML** | Copies the HTML entity, e.g. `&#x00BD;`                   |
| **U+**   | Copies the Unicode codepoint, e.g. `U+00BD`                |

<p align="center">
  <img src="media/glyphkit_modes.png" alt="The four copy modes — COPY, AUTO, HTML, and U+" width="720">
</p>

### Favorites & Recently Used

Right-click any character to pin it to your Favorites. Your last 24 used characters are tracked automatically in the Recent tab. Both persist between sessions.

<p align="center">
  <img src="media/glyphkit_favorites.png" alt="Favorites — your personal shortlist of frequently used glyphs" width="720">
</p>

<p align="center">
  <img src="media/glyphkit_recent.png" alt="Recently Used — the last 24 characters you clicked" width="720">
</p>

### Idle Opacity

When you're not hovering over GlyphKit, it fades out so it doesn't cover your work. Five levels from fully opaque to nearly invisible — with a configurable delay before the fade kicks in. The fade is gradual, not instant.

<p align="center">
  <img src="media/glyphkit_opacity.png" alt="Opacity comparison — fully opaque vs. low opacity over a text document" width="720">
</p>

### Window Snapping

Drag GlyphKit near the top or bottom edge of another window and it snaps cleanly into place — no overlap, no gap. Dock it right above or below the app you're working in. The snapping uses actual visible window bounds (not the invisible drop shadow Windows adds), so alignment is pixel-perfect. Works against any window and the taskbar.

<p align="center">
  <img src="media/glyphkit_snapping.png" alt="GlyphKit snapped to the bottom edge of a Notepad window" width="720">
</p>

### Settings

Click the gear icon in the title bar to open the settings flyout. Changes are applied instantly via the **Apply** button.

| Setting            | Options                                | Description                                                        |
| ------------------ | -------------------------------------- | ------------------------------------------------------------------ |
| **Scale**          | 80% / 90% / 100% / 110% / 125%        | Adjusts the overall UI size. Auto-scaled based on your display DPI |
| **Idle Opacity**   | Off / High / Mid / Low / Very          | How transparent the window becomes when idle (100% → 35%)          |
| **Fade Delay**     | 50ms – 1000ms                          | How long after the mouse leaves before the fade begins             |
| **Glyph Size**     | S / M / L                              | Character size in the grid — fewer columns at larger sizes         |
| **Window Snapping** | On / Off                              | Snap to nearby window edges while dragging                         |

<p align="center">
  <img src="media/glyphkit_settings.png" alt="The settings flyout with scale, opacity, and snapping controls" width="720">
</p>

### Auto-scaling

GlyphKit was designed at 150% display scaling (144 DPI). On screens with different scaling — a 4K monitor at 100%, a laptop at 125%, an ultra-wide at 175% — the UI automatically adjusts so buttons, text, and spacing stay proportional. The Scale setting lets you fine-tune on top of the auto-detection.

<p align="center">
  <img src="media/glyphkit_sizes.png" alt="GlyphKit at every scale setting — 80% through 125%" width="720">
</p>

### Custom hotkey

The default toggle hotkey is **Ctrl + Alt + G**. To change it, edit the `hotkey` field in `.glyphkit/config.json`:

```json
{
  "hotkey": "ctrl+alt+g"
}
```

Supported modifiers: `ctrl`, `alt`, `shift`. The key must be a single letter. Examples: `ctrl+shift+u`, `alt+g`, `ctrl+alt+k`.

### Remembers your setup

Window position, copy mode, favorites, recents, and all settings survive restarts. Config is stored in a `.glyphkit` folder next to the executable — clean, portable, and easy to back up.

## Compatibility

| Display          | Works? | Notes                                        |
| ---------------- | ------ | -------------------------------------------- |
| 1080p @ 100%     | ✓      | Auto-scales down, or use Scale setting       |
| 1440p @ 100%     | ✓      | Tested — auto-scale + manual adjustment      |
| 1600p @ 150%     | ✓      | Design baseline — looks exactly as intended  |
| 4K @ 150–200%    | ✓      | Auto-scales up                               |
| Multi-monitor    | ✓      | Remembers position per session               |

Requires **Windows 10 or 11**. Uses Win32 API directly — no Electron, no web runtime, no framework overhead.

## Installation

### Portable (recommended)

1. Download **GlyphKit.exe** from the [latest release](https://github.com/Lunyxium/glyphkit/releases/latest)
2. Put it anywhere you like
   *(note: if you put it in a read-only folder like C:\Program Files\ it won't save your settings or history)*
3. Run it

Windows might prompt a security pop-up, code & everything is right here if you want to check before allowing it.

Single file. No installer. No Python required. No admin rights. Just works.
A `.glyphkit` folder is created next to the exe to store your config.

<details>
<summary><strong>Run from source</strong></summary>

Requires Python 3.10+ on Windows.

```powershell
git clone https://github.com/Lunyxium/glyphkit.git
cd glyphkit
python run.pyw
```

Or just double-click `run.pyw`.

</details>

<details>
<summary><strong>Build the exe yourself</strong></summary>

```powershell
pip install pyinstaller
pyinstaller --onefile --noconsole --icon=glyphkit.ico --add-data "glyphkit.ico;." --name=GlyphKit run.pyw
```

Output: `dist/GlyphKit.exe` (~11 MB)

</details>

## Built with

Python, tkinter, and the Win32 API via ctypes. Zero external dependencies, runs entirely on Python's standard library.

~2600 lines of code across 3 files. Lightweight by design.

## License

GPL-3.0 &mdash; [Matt Baeumli](https://github.com/Lunyxium) &mdash; 2026

This means you can use, modify, and distribute GlyphKit freely — but any derivative work must also be open source under the same license, and must credit the original author.
