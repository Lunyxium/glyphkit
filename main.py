"""GlyphKit — Unicode character palette for Windows 11.

A compact, always-on-top character picker that doesn't steal focus
from the target application. Click a character to copy (and optionally
auto-paste) into whatever window you're working in.
"""

import json
import os
import sys
import tkinter as tk
import tkinter.font as tkFont
from characters import CATEGORIES
from win32_utils import (
	enable_dpi_awareness, set_no_activate, send_paste,
	get_work_area, force_foreground, get_system_dpi, get_foreground_window_rect,
	start_hotkey_listener, check_hotkey_pressed, stop_hotkey_listener,
)


# === Config ===

# PyInstaller --onefile extracts to a temp dir; config goes next to the exe,
# bundled assets (icon) come from the extraction dir
if getattr(sys, "frozen", False):
	_DIR = os.path.dirname(sys.executable)
	_ASSETS = sys._MEIPASS
else:
	_DIR = os.path.dirname(os.path.abspath(__file__))
	_ASSETS = _DIR
CONFIG_PATH = os.path.join(_DIR, "config.json")


# === Theme: Dark Grey + Teal ===

C = {
	"bg":        "#1a1a1a",
	"titlebar":  "#141414",
	"surface":   "#1e1e1e",
	"btn":       "#262626",
	"btn_hover": "#303030",
	"btn_click": "#0a3530",
	"border":    "#333333",
	"text":      "#d4d4d4",
	"text_dim":  "#787878",
	"char":      "#e8e8e8",
	"teal":      "#2dd4bf",
	"teal_mid":  "#14b8a6",
	"teal_dim":  "#0d9488",
	"teal_dark": "#0a2e2a",
	"gold":      "#e6c440",
	"gold_dim":  "#b89a30",
	"gold_dark": "#2e2a0a",
	"amber":     "#e6a040",
	"amber_dim": "#b87830",
	"purple":    "#b48eff",
	"purple_dim":"#8a6bc0",
	"pattern":   "#1f1f1f",
	"tab":       "#222222",
	"tab_border": "#363636",
}

# === App Info ===

APP_INFO = {
	"name": "GlyphKit",
	"version": "1.1.0",
	"author": "Matt B\u00e4umli",
	"handle": "Lunyxium",
	"github": "https://github.com/Lunyxium",
	"year": 2026,
	"license": "MIT",
	"hotkey": "Ctrl + Alt + G",
}

# === Layout Defaults (designed at 150% / 144 DPI) ===

DESIGN_DPI = 144
DESIGN_COLUMNS = 27
DESIGN_BTN = 36
DESIGN_GAP = 3
DESIGN_PAD = 10
DESIGN_SNAP_DIST = 60
DESIGN_TITLEBAR_H = 36
DESIGN_STATUS_H = 38
GRID_ROWS = 3
MAX_RECENTS = 24

GLYPH_PRESETS = {
	"S": {"font": 11, "btn": 30},
	"M": {"font": 13, "btn": 36},
	"L": {"font": 16, "btn": 44},
}

OPACITY_PRESETS = {
	"off":  1.00,
	"high": 0.80,
	"mid":  0.66,
	"low":  0.50,
}

SCALE_STEPS = [0.80, 0.90, 1.00, 1.10, 1.25]

# Tab rows: grouped by theme. Only categories present in CATEGORIES are shown.
TAB_ROWS = [
	["Math", "Scripts", "Sets", "Logic", "Greek", "Arrows", "Fractions"],
	["Roman", "Shapes", "Boxes", "Typography", "Currency", "Science"],
]

# === Copy Modes ===

COPY_MODES = [
	{"key": "copy", "label": "COPY \u25cb", "fg": C["text_dim"],   "status": "Click to copy character to clipboard"},
	{"key": "auto", "label": "AUTO \u25cf", "fg": C["teal"],       "status": "Click to copy & paste into active window"},
	{"key": "html", "label": "HTML \u25c6", "fg": C["amber"],      "status": "Click to copy as HTML entity"},
	{"key": "code", "label": "U+ \u25c7",   "fg": C["purple"],     "status": "Click to copy Unicode codepoint"},
]


TEXT_Y_OFFSET = 2  # Pixels to shift text up from center (font descender compensation)


class TextCanvas(tk.Canvas):
	"""Canvas that renders centered text with vertical offset correction.

	Wraps tk.Canvas to behave like a Label for fg/bg configuration,
	but uses create_text for pixel-level vertical positioning.
	"""

	def __init__(self, parent, text, font, fg, y_offset=TEXT_Y_OFFSET, **kw):
		super().__init__(parent, highlightthickness=0, bd=0, **kw)
		self._font = font
		self._y_offset = y_offset
		self.update_idletasks()
		w = self.winfo_reqwidth() or int(kw.get("width", 36))
		h = self.winfo_reqheight() or int(kw.get("height", 24))
		self._text_id = self.create_text(
			w // 2, h // 2 - y_offset,
			text=text, fill=fg, font=font,
		)

	def set_fg(self, color):
		self.itemconfig(self._text_id, fill=color)

	def configure(self, **kw):
		fg = kw.pop("fg", None)
		kw.pop("font", None)  # Font changes ignored (no resize needed)
		if fg is not None:
			self.set_fg(fg)
		if kw:
			super().configure(**kw)

	config = configure


class GlyphKitApp:
	def __init__(self):
		enable_dpi_awareness()

		self.root = tk.Tk()
		self.root.withdraw()

		self._copy_mode = 1  # Default: AUTO
		self.current_cat = "Math"
		self.tabs = {}
		self.char_frame = None
		self.status_bar = None
		self.drag = {"x": 0, "y": 0, "active": False}
		self._status_default_color = None
		self.hover_active = False
		self._reset_timer = None
		self._transient_status = None
		self._transient_color = None
		self._delete_mode = False
		self._favorites = []
		self._recents = []
		self._char_order = self._build_char_order()
		self._search_var = tk.StringVar()
		self._search_var.trace_add("write", self._on_search)
		self._visible = True
		self._settings_win = None

		self._load_config()
		self._compute_layout()
		self.status_default = self._default_status_text()

		self._build()
		self.root.deiconify()
		self.root.after(50, self._init_hwnd)

	# === Scale Engine ===

	def _compute_layout(self):
		"""Compute all layout dimensions from DPI + user settings."""
		dpi = get_system_dpi()
		auto_scale = dpi / DESIGN_DPI
		user_scale = self._config.get("user_scale", 1.0)
		if user_scale not in SCALE_STEPS:
			user_scale = 1.0
		self._scale = auto_scale * user_scale
		self._user_scale = user_scale

		glyph_key = self._config.get("glyph_size", "M")
		if glyph_key not in GLYPH_PRESETS:
			glyph_key = "M"
		glyph = GLYPH_PRESETS[glyph_key]
		self._glyph_key = glyph_key

		s = self._scale
		self._btn_size = round(glyph["btn"] * s)
		self._glyph_font_size = round(glyph["font"] * s)
		self._gap = max(1, round(DESIGN_GAP * s))
		self._pad = round(DESIGN_PAD * s)
		self._titlebar_h = round(DESIGN_TITLEBAR_H * s)
		self._status_h = round(DESIGN_STATUS_H * s)
		self._snap_dist = round(DESIGN_SNAP_DIST * s)

		# Window width: fixed per scale (based on 27-col M-size design)
		design_w = DESIGN_COLUMNS * (DESIGN_BTN + DESIGN_GAP) + DESIGN_GAP + DESIGN_PAD * 2
		self.win_w = round(design_w * s)

		# Columns: derived from actual btn_size (adjusts for glyph size)
		usable = self.win_w - 2 * self._pad - self._gap
		self._columns = max(1, usable // (self._btn_size + self._gap))

		# Opacity
		opacity_key = self._config.get("idle_opacity", "mid")
		if opacity_key not in OPACITY_PRESETS:
			opacity_key = "mid"
		self._opacity_key = opacity_key
		self._idle_opacity = OPACITY_PRESETS[opacity_key]

		# Snapping
		self._snap_enabled = self._config.get("snap_enabled", True)
		self._snap_threshold = 30

		# Scaled font sizes for UI elements
		self._font_ui = round(9 * s)
		self._font_ui_small = round(8 * s)
		self._font_title = round(10 * s)
		self._font_close = round(14 * s)
		self._font_tab = round(9 * s)
		self._font_search = round(9 * s)
		self._font_search_icon = round(11 * s)
		self._font_about_title = round(12 * s)
		self._font_star = round(11 * s)
		self._font_icon = round(10 * s)

	def _init_hwnd(self):
		# Opacity first — attributes("-alpha") can reset extended window styles
		self._setup_opacity()
		self._hwnd = set_no_activate(self.root)
		self._setup_hotkey()
		self.root.bind("<Escape>", self._on_escape)

	@staticmethod
	def _build_char_order():
		"""Build global ordering index for favorites sorting."""
		order = {}
		idx = 0
		for cat_key in [k for row in TAB_ROWS for k in row]:
			if cat_key not in CATEGORIES:
				continue
			for char, name in CATEGORIES[cat_key]["chars"]:
				if char not in order:
					order[char] = (idx, name)
					idx += 1
		return order

	# === Config Persistence ===

	def _load_config(self):
		try:
			with open(CONFIG_PATH, "r") as f:
				self._config = json.load(f)
		except (FileNotFoundError, json.JSONDecodeError):
			self._config = {}
		self._copy_mode = self._config.get("copy_mode", 1)
		if self._copy_mode not in range(len(COPY_MODES)):
			self._copy_mode = 1
		self._favorites = self._config.get("favorites", [])
		self._recents = self._config.get("recents", [])

	def _save_config(self):
		self._config.update({
			"x": self.root.winfo_x(),
			"y": self.root.winfo_y(),
			"copy_mode": self._copy_mode,
			"favorites": self._favorites,
			"recents": self._recents,
			"user_scale": self._user_scale,
			"glyph_size": self._glyph_key,
			"idle_opacity": self._opacity_key,
			"fade_delay": self._config.get("fade_delay", 50),
			"snap_enabled": self._snap_enabled,
		})
		# Ensure hotkey default exists
		self._config.setdefault("hotkey", "ctrl+alt+g")
		try:
			with open(CONFIG_PATH, "w") as f:
				json.dump(self._config, f, indent=2, ensure_ascii=False)
		except OSError:
			pass

	def _quit(self):
		self._save_config()
		stop_hotkey_listener()
		self.root.quit()

	# === Build UI ===

	def _build(self):
		r = self.root
		r.title("GlyphKit")
		ico = os.path.join(_ASSETS, "glyphkit.ico")
		if os.path.exists(ico):
			r.iconbitmap(ico)
		r.overrideredirect(True)
		r.configure(bg=C["bg"])
		r.attributes("-topmost", True)

		self._build_titlebar()
		tk.Frame(r, height=1, bg=C["teal_dim"]).pack(fill="x")
		self._build_tabs()
		tk.Frame(r, height=1, bg=C["border"]).pack(fill="x")
		self._build_grid()
		tk.Frame(r, height=1, bg=C["border"]).pack(fill="x")
		self._build_status()

		self._show_cat("Math")
		self._lock_layout()
		self._apply_config()

	def _rebuild(self):
		"""Destroy and rebuild the entire UI with current scale settings."""
		# Save state
		cat = self.current_cat
		x, y = self.root.winfo_x(), self.root.winfo_y()

		# Destroy all children
		for w in self.root.winfo_children():
			w.destroy()
		self.tabs = {}

		# Recompute and rebuild
		self._load_config()
		self._compute_layout()
		self.status_default = self._default_status_text()
		self._build()

		# Restore position
		self.root.geometry(f"{self.win_w}x{self.win_h}+{x}+{y}")

		# Restore category
		if cat == "_favorites":
			self._show_favorites()
		elif cat == "_recents":
			self._show_recents()
		elif cat in CATEGORIES:
			self._show_cat(cat)

		# Reapply opacity, window styles, and bindings
		self._setup_opacity()
		self._hwnd = set_no_activate(self.root)
		self.root.bind("<Escape>", self._on_escape)

	def _lock_layout(self):
		"""Lock grid height and set explicit window geometry."""
		self.root.update()
		gpad = self._gap // 2 + 1
		row_h = self._btn_size + gpad * 2
		grid_h = GRID_ROWS * row_h + round(8 * self._scale)
		self.grid_container.configure(height=grid_h)
		self.grid_container.pack_propagate(False)
		self.root.update()
		self.win_h = self.root.winfo_reqheight()
		self.root.geometry(f"{self.win_w}x{self.win_h}")

	def _apply_config(self):
		"""Apply saved position and copy mode preference."""
		if "x" in self._config and "y" in self._config:
			self.root.geometry(
				f"{self.win_w}x{self.win_h}+{self._config['x']}+{self._config['y']}"
			)
		else:
			self._position_bottom()
		# Sync mode toggle display
		mode = COPY_MODES[self._copy_mode]
		self.titlebar.itemconfig(self._mode_id, text=mode["label"], fill=mode["fg"])

	# --- Titlebar ---

	def _build_titlebar(self):
		h = self._titlebar_h
		bar = tk.Canvas(
			self.root, height=h, width=self.win_w,
			bg=C["titlebar"], highlightthickness=0,
		)
		bar.pack(fill="x")
		self._draw_pattern(bar, self.win_w, h)

		bar.create_text(
			round(14 * self._scale), h // 2, text="GlyphKit",
			fill=C["teal_dim"], font=("Segoe UI", self._font_title, "bold"),
			anchor="w", tags="title",
		)
		bar.tag_bind("title", "<Enter>", lambda e: (
			bar.itemconfig("title", fill=C["teal"]),
			self._set_status(f"Toggle: Ctrl+Alt+G  ·  Close: Esc (while focused)"),
		))
		bar.tag_bind("title", "<Leave>", lambda e: (
			bar.itemconfig("title", fill=C["teal_dim"]),
			self._set_status(self.status_default),
		))

		# --- Close button ---
		cx = self.win_w - round(20 * self._scale)
		bar.create_text(
			cx, h // 2 - 2, text="\u00d7",
			fill=C["text_dim"], font=("Segoe UI", self._font_close), anchor="center", tags="close",
		)
		bar.tag_bind("close", "<Button-1>", lambda e: self._quit())
		bar.tag_bind("close", "<Enter>", lambda e: bar.itemconfig("close", fill="#ff6b6b"))
		bar.tag_bind("close", "<Leave>", lambda e: bar.itemconfig("close", fill=C["text_dim"]))

		# --- Settings gear (gold highlight, prominent) ---
		gear_font_size = max(10, round(self._font_ui * 1.35))
		gx = cx - round(48 * self._scale)
		self._gear_id = bar.create_text(
			gx, h // 2, text="\u2699",
			fill=C["text_dim"], font=("Segoe UI Symbol", gear_font_size, "bold"),
			anchor="center", tags="gear",
		)
		bar.tag_bind("gear", "<Button-1>", lambda e: self._toggle_settings())
		bar.tag_bind("gear", "<Enter>", lambda e: self._gear_hover_in())
		bar.tag_bind("gear", "<Leave>", lambda e: self._gear_hover_out())

		# --- Copy mode toggle (cycles through 4 modes) ---
		ax = gx - round(60 * self._scale)
		mode = COPY_MODES[self._copy_mode]
		self._mode_id = bar.create_text(
			ax, h // 2, text=mode["label"],
			fill=mode["fg"], font=("Segoe UI", self._font_ui_small, "bold"),
			anchor="center", tags="mode",
		)
		bar.tag_bind("mode", "<Button-1>", self._cycle_mode)
		bar.tag_bind("mode", "<Enter>", lambda e: self._mode_hover_in())
		bar.tag_bind("mode", "<Leave>", lambda e: self._mode_hover_out())

		# --- Drag ---
		bar.bind("<Button-1>", self._drag_start)
		bar.bind("<B1-Motion>", self._drag_move)
		bar.bind("<ButtonRelease-1>", self._drag_end)

		self.titlebar = bar

	def _gear_hover_in(self):
		is_open = self._settings_win and self._settings_win.winfo_exists()
		self.titlebar.itemconfig("gear", fill=C["gold"])
		self._set_status("Close settings" if is_open else "Open settings")

	def _gear_hover_out(self):
		is_open = self._settings_win and self._settings_win.winfo_exists()
		self.titlebar.itemconfig("gear", fill=C["gold_dim"] if is_open else C["text_dim"])
		self._set_status(self.status_default)

	def _draw_pattern(self, canvas, w, h):
		"""Draw subtle crosshatch pattern on canvas chrome."""
		spacing = round(14 * self._scale)
		col = C["pattern"]
		for i in range(-h, w + h, spacing):
			canvas.create_line(i, 0, i + h, h, fill=col, width=1)
			canvas.create_line(i + h, 0, i, h, fill=col, width=1)

	# --- Tabs (multi-row, button-style) ---

	def _make_tab(self, parent, key, text, side="left"):
		"""Create a button-style tab with a 1px border frame."""
		border = tk.Frame(parent, bg=C["tab_border"])
		border.pack(side=side, padx=round(3 * self._scale))
		bg = C["tab"]
		fg = C["text_dim"]
		font = ("Consolas", self._font_tab)
		f = tkFont.Font(family="Consolas", size=self._font_tab)
		tw = f.measure(text) + 4
		th = f.metrics("ascent") + f.metrics("descent") + 6
		tab = TextCanvas(
			border, text=text, font=font, fg=fg,
			bg=bg, width=tw, height=th, cursor="hand2",
			y_offset=1,
		)
		tab.pack(padx=1, pady=1)
		tab.bind("<Button-1>", lambda e, k=key: self._show_cat(k))
		tab.bind("<Enter>", lambda e, t=tab, k=key: (
			t.configure(fg=C["teal"]) if k != self.current_cat else None
		))
		tab.bind("<Leave>", lambda e, t=tab, k=key: (
			t.configure(fg=fg) if k != self.current_cat else None
		))
		self.tabs[key] = tab
		self._tab_borders[key] = border
		self._tab_defaults[key] = (bg, fg)

	def _build_tabs(self):
		self._tab_borders = {}
		self._tab_defaults = {}
		outer = tk.Frame(self.root, bg=C["bg"])
		outer.pack(fill="x", pady=(round(6 * self._scale), round(4 * self._scale)))

		for row_keys in TAB_ROWS:
			row_frame = tk.Frame(outer, bg=C["bg"])
			row_frame.pack(fill="x", pady=round(3 * self._scale), padx=self._pad)

			# Recent + Favorites right-aligned on first row
			if row_keys is TAB_ROWS[0]:
				self._build_special_tabs(row_frame)

			# Search bar right-aligned on second row
			if row_keys is TAB_ROWS[1]:
				self._build_search(row_frame)

			for key in row_keys:
				if key not in CATEGORIES:
					continue
				data = CATEGORIES[key]
				self._make_tab(row_frame, key, f" {data['icon']} {key} ")

	# --- Search ---

	def _build_search(self, parent):
		"""Search entry, packed right on the second tab row."""
		self._search_bg = "#1e2120"
		self._search_border_idle = "#363636"
		self._search_border_active = C["teal_dim"]
		self._search_glow_color = C["teal_dark"]

		self._search_glow = tk.Frame(parent, bg=C["bg"])
		self._search_glow.pack(side="right", padx=(round(6 * self._scale), round(3 * self._scale)))

		self._search_border = tk.Frame(self._search_glow, bg=self._search_border_idle)
		self._search_border.pack(padx=1, pady=1)

		inner = tk.Frame(self._search_border, bg=self._search_bg)
		inner.pack(padx=1, pady=1)

		self._search_icon = tk.Label(
			inner, text="\U0001f50d", bg=self._search_bg, fg="#506b65",
			font=("Segoe UI", self._font_search_icon),
		)
		self._search_icon.pack(side="left", padx=(round(5 * self._scale), 0), pady=(0, 1))

		self._search_entry = tk.Entry(
			inner, textvariable=self._search_var,
			bg=self._search_bg, fg="#b0b0b0", insertbackground=C["teal"],
			font=("Segoe UI", self._font_search), width=18,
			relief="flat", bd=0,
		)
		self._search_entry.pack(side="left", padx=(2, round(6 * self._scale)), pady=2)

		self._search_entry.bind("<Button-1>", self._search_activate)
		self._search_entry.bind("<FocusOut>", self._search_focus_out)

		self._set_search_placeholder()

	def _set_search_placeholder(self):
		"""Show placeholder text in search bar, hide cursor."""
		self._search_placeholder = True
		self._search_clearing = True
		self._search_entry.delete(0, "end")
		self._search_entry.insert(0, "search...")
		self._search_entry.configure(fg=C["text_dim"], insertbackground=self._search_bg)
		self._search_clearing = False
		self._search_border.configure(bg=self._search_border_idle)
		self._search_glow.configure(bg=C["bg"])
		self._search_icon.configure(fg="#506b65")
		self.root.focus_set()

	def _search_activate(self, _event=None):
		"""Force OS keyboard focus to our window so typing works."""
		if hasattr(self, "_hwnd"):
			force_foreground(self._hwnd)
		if self._search_placeholder:
			self._search_clearing = True
			self._search_entry.delete(0, "end")
			self._search_entry.configure(fg="#c8c8c8", insertbackground=C["teal"])
			self._search_placeholder = False
			self._search_clearing = False
		self._search_border.configure(bg=self._search_border_active)
		self._search_glow.configure(bg=self._search_glow_color)
		self._search_icon.configure(fg="#6b9e95")
		self._search_entry.focus_set()

	def _search_focus_out(self, _event=None):
		if not self._search_var.get().strip():
			self._set_search_placeholder()

	def _on_search(self, *_args):
		"""Filter all characters by name and show matches in grid."""
		if getattr(self, "_search_clearing", False):
			return
		if getattr(self, "_search_placeholder", False):
			return
		query = self._search_var.get().strip().lower()
		if not query:
			if self.current_cat == "_favorites":
				self._render_favorites()
			elif self.current_cat == "_recents":
				self._render_recents()
			else:
				self._fill_grid(CATEGORIES[self.current_cat]["chars"])
			return

		matches = []
		for cat_data in CATEGORIES.values():
			for char, name in cat_data["chars"]:
				if query in name.lower():
					matches.append((char, name))

		seen = set()
		unique = []
		for char, name in matches:
			if char not in seen:
				seen.add(char)
				unique.append((char, name))

		# Deselect all tabs
		self._deselect_all_tabs()

		if unique:
			self._fill_grid(unique)
		else:
			for w in self.char_frame.winfo_children():
				w.destroy()
			for col in range(self._columns):
				self.char_frame.columnconfigure(col, weight=0, minsize=0)
			self.char_frame.columnconfigure(0, weight=1)
			tk.Label(
				self.char_frame, text="No matches found",
				bg=C["bg"], fg=C["text_dim"], font=("Segoe UI", self._font_ui),
			).grid(row=0, column=0, sticky="w", padx=4, pady=8)

	# --- Tab Deselection Helper ---

	def _deselect_all_tabs(self):
		"""Reset all category, favorites, and recents tabs to default state."""
		for k, lbl in self.tabs.items():
			self._tab_borders[k].configure(bg=C["tab_border"])
			default_bg, default_fg = self._tab_defaults[k]
			lbl.configure(fg=default_fg, bg=default_bg, font=("Consolas", self._font_tab))
		if hasattr(self, "_fav_canvas"):
			self._fav_accent.configure(bg="#1a3a36")
			self._fav_set_colors(*self._fav_defaults, star="\u2606")
		if hasattr(self, "_recent_canvas"):
			self._recent_accent.configure(bg="#1a3a36")
			self._recent_set_colors(*self._recent_defaults)
		if hasattr(self, "_del_btn_frame"):
			self._del_btn_frame.destroy()
		if hasattr(self, "_clear_btn_frame"):
			self._clear_btn_frame.destroy()

	# --- Special Tabs (Recents + Favorites) ---

	def _build_special_tabs(self, parent):
		"""Build Recent and Favorites buttons, right-aligned on tab row 1."""
		tab_font = ("Consolas", self._font_tab)
		star_font = ("Segoe UI", self._font_star)
		icon_font = ("Segoe UI", self._font_icon)
		tf = tkFont.Font(family="Consolas", size=self._font_tab)
		sf = tkFont.Font(family="Segoe UI", size=self._font_star)
		rif = tkFont.Font(family="Segoe UI", size=self._font_icon)
		tab_h = tf.metrics("ascent") + tf.metrics("descent") + 6

		fav_bg = "#1a2422"
		fav_fg = C["teal_dim"]
		rec_bg = "#1a2224"
		rec_fg = C["teal_dim"]

		# --- Favorites tab (packed first = rightmost) ---
		fav_frame = tk.Frame(parent, bg=C["bg"])
		fav_frame.pack(side="right", padx=(8, 0))

		fav_w = sf.measure("\u2606 ") + tf.measure("Favorites") + 10
		fav_cvs = tk.Canvas(
			fav_frame, width=fav_w, height=tab_h,
			bg=fav_bg, highlightthickness=0, bd=0, cursor="hand2",
		)
		fav_cvs.pack(side="left")

		fav_accent = tk.Frame(fav_frame, bg="#1a3a36", width=3)
		fav_accent.pack(side="left", fill="y")

		star_x = sf.measure("\u2606") // 2 + 4
		fav_label_x = sf.measure("\u2606 ") + 5
		fav_cy = tab_h // 2 - 1
		self._fav_star_id = fav_cvs.create_text(
			star_x, fav_cy - 2, text="\u2606", fill=fav_fg, font=star_font,
		)
		self._fav_label_id = fav_cvs.create_text(
			fav_label_x, fav_cy, text="Favorites", fill=fav_fg, font=tab_font, anchor="w",
		)
		self._fav_canvas = fav_cvs
		self._fav_accent = fav_accent
		self._fav_defaults = (fav_fg, fav_bg)

		fav_cvs.bind("<Button-1>", lambda e: self._show_favorites())
		fav_cvs.bind("<Enter>", lambda e: self._fav_hover_in())
		fav_cvs.bind("<Leave>", lambda e: self._fav_hover_out())

		# --- Recent tab (left of favorites) ---
		rec_frame = tk.Frame(parent, bg=C["bg"])
		rec_frame.pack(side="right", padx=(9, 3))

		rec_w = rif.measure("\u21bb ") + tf.measure("Recent") + 10
		rec_cvs = tk.Canvas(
			rec_frame, width=rec_w, height=tab_h,
			bg=rec_bg, highlightthickness=0, bd=0, cursor="hand2",
		)
		rec_cvs.pack(side="left")

		rec_accent = tk.Frame(rec_frame, bg="#1a3a36", width=3)
		rec_accent.pack(side="left", fill="y")

		rec_icon_x = rif.measure("\u21bb") // 2 + 4
		rec_label_x = rif.measure("\u21bb ") + 5
		rec_cy = tab_h // 2 - 1
		self._recent_icon_id = rec_cvs.create_text(
			rec_icon_x, rec_cy - 1, text="\u21bb", fill=rec_fg, font=icon_font,
		)
		self._recent_label_id = rec_cvs.create_text(
			rec_label_x, rec_cy, text="Recent", fill=rec_fg, font=tab_font, anchor="w",
		)
		self._recent_canvas = rec_cvs
		self._recent_accent = rec_accent
		self._recent_defaults = (rec_fg, rec_bg)

		rec_cvs.bind("<Button-1>", lambda e: self._show_recents())
		rec_cvs.bind("<Enter>", lambda e: self._recent_hover_in())
		rec_cvs.bind("<Leave>", lambda e: self._recent_hover_out())

	# --- Favorites ---

	def _fav_set_colors(self, fg, bg, star=None):
		self._fav_canvas.configure(bg=bg)
		self._fav_canvas.itemconfig(self._fav_star_id, fill=fg)
		self._fav_canvas.itemconfig(self._fav_label_id, fill=fg)
		if star:
			self._fav_canvas.itemconfig(self._fav_star_id, text=star)

	def _fav_hover_in(self):
		if self.current_cat != "_favorites":
			self._fav_set_colors(C["teal"], self._fav_defaults[1])
		self._set_status("Right-click any character to add to Favorites")

	def _fav_hover_out(self):
		if self.current_cat == "_favorites":
			self._fav_set_colors(C["teal"], C["teal_dark"], star="\u2605")
		else:
			self._fav_set_colors(*self._fav_defaults, star="\u2606")
		self._set_status(self.status_default)

	def _show_favorites(self):
		"""Show the Favorites grid."""
		self._search_clearing = True
		self._search_var.set("")
		if hasattr(self, "_search_entry"):
			self._set_search_placeholder()
		self._search_clearing = False

		self._about_active = False
		if hasattr(self, "status_bar"):
			self.status_bar.itemconfig("about", fill=C["text_dim"])

		self._deselect_all_tabs()

		# Highlight favorites tab
		self.current_cat = "_favorites"
		self._fav_accent.configure(bg=C["teal"])
		self._fav_set_colors(C["teal"], C["teal_dark"], star="\u2605")

		self._delete_mode = False
		self._render_favorites()

	def _render_favorites(self):
		"""Render favorites grid with optional delete button."""
		for w in self.char_frame.winfo_children():
			w.destroy()

		for col in range(self._columns):
			self.char_frame.columnconfigure(col, weight=0, minsize=0)

		if not self._favorites:
			self.char_frame.columnconfigure(0, weight=1)
			tk.Label(
				self.char_frame,
				text="Right-click any character to add it here",
				bg=C["bg"], fg=C["text_dim"], font=("Segoe UI", self._font_ui),
			).grid(row=0, column=0, sticky="w", padx=4, pady=8)
			self._build_delete_btn(disabled=True)
			return

		sorted_favs = []
		for char in self._favorites:
			if char in self._char_order:
				idx, name = self._char_order[char]
				sorted_favs.append((idx, char, name))
		sorted_favs.sort(key=lambda x: x[0])

		gpad = self._gap // 2 + 1
		for i, (_idx, char, name) in enumerate(sorted_favs):
			r, col = divmod(i, self._columns)
			btn = TextCanvas(
				self.char_frame, text=char, fg=C["char"],
				font=("Segoe UI Symbol", self._glyph_font_size),
				bg=C["btn"], width=self._btn_size, height=self._btn_size,
				cursor="hand2",
			)
			btn.grid(row=r, column=col, padx=gpad, pady=gpad, sticky="nsew")
			if self._delete_mode:
				btn.bind("<Button-1>", lambda e, ch=char, nm=name: self._remove_favorite(ch, nm))
				btn.bind("<Enter>", lambda e, b=btn, nm=name: (
					b.configure(bg="#3a1a1a"),
					self._set_status(f"Click to remove: {nm}"),
				))
			else:
				btn.bind("<Button-1>", lambda e, b=btn, ch=char, nm=name: self._click_char(b, ch, nm))
				btn.bind("<Enter>", lambda e, b=btn, ch=char, nm=name: self._hover_in(b, ch, nm))
			btn.bind("<Leave>", lambda e, b=btn: self._hover_out(b))

		for col in range(self._columns):
			self.char_frame.columnconfigure(col, weight=1, minsize=self._btn_size)

		self._build_delete_btn(disabled=False)

	def _build_delete_btn(self, disabled=False):
		"""Add delete toggle button at bottom-right of grid container."""
		if hasattr(self, "_del_btn_frame"):
			self._del_btn_frame.destroy()

		self._del_btn_frame = tk.Frame(self.grid_container, bg=C["bg"])
		self._del_btn_frame.pack(anchor="e", padx=6, pady=(0, 2))

		if self._delete_mode:
			text = "\u2715  done"
			bg, fg = "#3a1515", "#e87040"
			hover_bg, hover_fg = "#4a1a1a", "#ff8855"
			hover_status = "Click to finish deleting"
		elif disabled:
			text = "\u2715"
			bg, fg = C["bg"], "#444444"
		else:
			text = "\u2715"
			bg, fg = C["bg"], "#666666"
			hover_bg, hover_fg = "#2a1a1a", "#e87040"
			hover_status = "Remove favorites"

		btn = tk.Label(
			self._del_btn_frame, text=f" {text} ",
			bg=bg, fg=fg, font=("Segoe UI", 9, "bold"),
			cursor="hand2" if not disabled else "",
		)
		btn.pack()

		if not disabled:
			btn.bind("<Button-1>", lambda e: self._toggle_delete_mode())
			btn.bind("<Enter>", lambda e: (
				btn.configure(bg=hover_bg, fg=hover_fg),
				self._set_status(hover_status),
			))
			btn.bind("<Leave>", lambda e: (
				btn.configure(bg=bg, fg=fg),
				self._set_status(self.status_default),
			))

	def _toggle_delete_mode(self):
		self._delete_mode = not self._delete_mode
		if self._delete_mode:
			self.status_default = "Delete active \u2014 click a character to remove it"
			self._status_default_color = "#e87040"
			self._set_status(self.status_default, "#e87040")
		else:
			self.status_default = self._default_status_text()
			self._status_default_color = None
			self._set_status(self.status_default)
		self._render_favorites()

	def _add_favorite(self, char, name):
		"""Add a character to favorites via right-click."""
		if char in self._favorites:
			self._set_status(f"Already in Favorites: {char}  ({name})", C["teal_dim"])
			return
		if len(self._favorites) >= 70:
			self._set_status("Favorites full (70/70)", "#e87040")
			return
		self._favorites.append(char)
		self._save_config()
		self._show_transient(f"Added to Favorites: {char}  ({name})", "#40c090")
		if self.current_cat == "_favorites":
			self._render_favorites()

	def _remove_favorite(self, char, name):
		"""Remove a character from favorites."""
		if char in self._favorites:
			self._favorites.remove(char)
			self._save_config()
			self._show_transient(f"Removed from Favorites: {char}  ({name})", "#e87040")
			if not self._favorites:
				self._delete_mode = False
			self._render_favorites()

	# --- Recents ---

	def _recent_set_colors(self, fg, bg):
		self._recent_canvas.configure(bg=bg)
		self._recent_canvas.itemconfig(self._recent_icon_id, fill=fg)
		self._recent_canvas.itemconfig(self._recent_label_id, fill=fg)

	def _recent_hover_in(self):
		if self.current_cat != "_recents":
			self._recent_set_colors(C["teal"], self._recent_defaults[1])
		self._set_status(f"Last {MAX_RECENTS} used characters \u2014 auto-updated on click")

	def _recent_hover_out(self):
		if self.current_cat == "_recents":
			self._recent_set_colors(C["teal"], C["teal_dark"])
		else:
			self._recent_set_colors(*self._recent_defaults)
		self._set_status(self.status_default)

	def _show_recents(self):
		"""Show the Recently Used grid."""
		self._search_clearing = True
		self._search_var.set("")
		if hasattr(self, "_search_entry"):
			self._set_search_placeholder()
		self._search_clearing = False

		self._about_active = False
		if hasattr(self, "status_bar"):
			self.status_bar.itemconfig("about", fill=C["text_dim"])

		self._deselect_all_tabs()
		self._delete_mode = False

		self.current_cat = "_recents"
		self._recent_accent.configure(bg=C["teal"])
		self._recent_set_colors(C["teal"], C["teal_dark"])

		self._render_recents()

	def _render_recents(self):
		"""Render the recently used characters grid."""
		for w in self.char_frame.winfo_children():
			w.destroy()

		for col in range(self._columns):
			self.char_frame.columnconfigure(col, weight=0, minsize=0)

		if not self._recents:
			self.char_frame.columnconfigure(0, weight=1)
			tk.Label(
				self.char_frame,
				text="Characters you use will appear here",
				bg=C["bg"], fg=C["text_dim"], font=("Segoe UI", self._font_ui),
			).grid(row=0, column=0, sticky="w", padx=4, pady=8)
			return

		gpad = self._gap // 2 + 1
		for i, char in enumerate(self._recents):
			r, col = divmod(i, self._columns)
			name = self._char_order.get(char, (0, char))[1]
			btn = TextCanvas(
				self.char_frame, text=char, fg=C["char"],
				font=("Segoe UI Symbol", self._glyph_font_size),
				bg=C["btn"], width=self._btn_size, height=self._btn_size,
				cursor="hand2",
			)
			btn.grid(row=r, column=col, padx=gpad, pady=gpad, sticky="nsew")
			btn.bind("<Button-1>", lambda e, b=btn, ch=char, nm=name: self._click_char(b, ch, nm))
			btn.bind("<Button-3>", lambda e, ch=char, nm=name: self._add_favorite(ch, nm))
			btn.bind("<Enter>", lambda e, b=btn, ch=char, nm=name: self._hover_in(b, ch, nm))
			btn.bind("<Leave>", lambda e, b=btn: self._hover_out(b))

		for col in range(self._columns):
			self.char_frame.columnconfigure(col, weight=1, minsize=self._btn_size)

		self._build_clear_btn()

	def _build_clear_btn(self):
		"""Add clear button at bottom-right of recents grid."""
		if hasattr(self, "_clear_btn_frame"):
			self._clear_btn_frame.destroy()

		self._clear_btn_frame = tk.Frame(self.grid_container, bg=C["bg"])
		self._clear_btn_frame.pack(anchor="e", padx=6, pady=(0, 2))

		if not self._recents:
			return

		text = "\u2715"
		bg, fg = C["bg"], "#666666"
		hover_bg, hover_fg = "#1a2a28", C["teal_dim"]
		hover_status = "Clear recent characters"

		btn = tk.Label(
			self._clear_btn_frame, text=f" {text} ",
			bg=bg, fg=fg, font=("Segoe UI", 9, "bold"),
			cursor="hand2",
		)
		btn.pack()

		btn.bind("<Button-1>", lambda e: self._clear_recents())
		btn.bind("<Enter>", lambda e: (
			btn.configure(bg=hover_bg, fg=hover_fg),
			self._set_status(hover_status),
		))
		btn.bind("<Leave>", lambda e: (
			btn.configure(bg=bg, fg=fg),
			self._set_status(self.status_default),
		))

	def _clear_recents(self):
		"""Clear all recent characters."""
		self._recents.clear()
		self._save_config()
		self._show_transient("Recent characters cleared", C["teal_dim"])
		self._render_recents()

	def _add_recent(self, char):
		"""Add a character to the front of the recents list."""
		if char in self._recents:
			self._recents.remove(char)
		self._recents.insert(0, char)
		self._recents = self._recents[:MAX_RECENTS]

	# --- Category ---

	def _show_cat(self, key):
		if hasattr(self, "_search_entry"):
			self._set_search_placeholder()
		self._about_active = False
		if hasattr(self, "status_bar"):
			self.status_bar.itemconfig("about", fill=C["text_dim"])
		self._delete_mode = False
		self.status_default = self._default_status_text()
		self._status_default_color = None

		self._deselect_all_tabs()

		self.current_cat = key
		self._tab_borders[key].configure(bg=C["teal_dim"])
		self.tabs[key].configure(fg=C["teal"], bg=C["teal_dark"])
		self._fill_grid(CATEGORIES[key]["chars"])

	def _show_about(self):
		"""Show compact app info that fits in the grid area."""
		for w in self.char_frame.winfo_children():
			w.destroy()

		for col in range(self._columns):
			self.char_frame.columnconfigure(col, weight=0, minsize=0)
		self.char_frame.columnconfigure(0, weight=1)

		info = APP_INFO
		dim = C["text_dim"]
		sep = "  |  "

		# Row 0: Name + version
		tk.Label(
			self.char_frame,
			text=f"{info['name']}  v{info['version']}",
			bg=C["bg"], fg=C["teal"], font=("Segoe UI", self._font_about_title, "bold"), anchor="w",
		).grid(row=0, column=0, sticky="w", padx=4, pady=(4, 2))

		# Row 1: Author + GitHub link + license + stats
		row1 = tk.Frame(self.char_frame, bg=C["bg"])
		row1.grid(row=1, column=0, sticky="w", padx=4, pady=(2, 2))

		tk.Label(
			row1, text=f"{info['author']}  /  ",
			bg=C["bg"], fg=dim, font=("Segoe UI", self._font_ui),
		).pack(side="left")

		handle = tk.Label(
			row1, text=info["handle"],
			bg=C["bg"], fg=C["teal_dim"], font=("Segoe UI", self._font_ui, "underline"), cursor="hand2",
		)
		handle.pack(side="left")
		handle.bind("<Button-1>", lambda e: os.startfile(info["github"]))
		handle.bind("<Enter>", lambda e: handle.configure(fg=C["teal"]))
		handle.bind("<Leave>", lambda e: handle.configure(fg=C["teal_dim"]))

		tk.Label(
			row1, text=f"{sep}{info['license']} License{sep}433 glyphs / 13 categories",
			bg=C["bg"], fg=dim, font=("Segoe UI", self._font_ui),
		).pack(side="left")

		# Row 2: Shortcuts + stack
		row2 = tk.Frame(self.char_frame, bg=C["bg"])
		row2.grid(row=2, column=0, sticky="w", padx=4, pady=(0, 4))
		tk.Label(
			row2,
			text=f"Toggle: {info['hotkey']}{sep}Close: Esc{sep}Python + tkinter + ctypes",
			bg=C["bg"], fg=dim, font=("Segoe UI", self._font_ui),
		).pack(side="left")

	def _toggle_about(self, _event=None):
		"""Toggle About view from footer link."""
		if self._about_active:
			self._about_active = False
			self.status_bar.itemconfig("about", fill=C["text_dim"])
			if self.current_cat == "_favorites":
				self._show_favorites()
			elif self.current_cat == "_recents":
				self._show_recents()
			else:
				self._show_cat(self.current_cat)
		else:
			self._about_active = True
			self.status_bar.itemconfig("about", fill=C["gold_dim"])
			self._deselect_all_tabs()
			self._show_about()

	# --- Character Grid ---

	def _build_grid(self):
		self.grid_container = tk.Frame(self.root, bg=C["bg"], padx=self._pad)
		self.grid_container.pack(fill="both", expand=True, pady=round(4 * self._scale))

		self._grid_canvas = tk.Canvas(
			self.grid_container, bg=C["bg"], highlightthickness=0, bd=0,
		)
		self._grid_canvas.pack(fill="both", expand=True)

		self.char_frame = tk.Frame(self._grid_canvas, bg=C["bg"])
		self._grid_canvas_win = self._grid_canvas.create_window(
			(0, 0), window=self.char_frame, anchor="nw",
		)

		self.char_frame.bind("<Configure>", self._on_grid_frame_configure)
		self._grid_canvas.bind("<Configure>", self._on_grid_canvas_configure)
		self._grid_canvas.bind("<Enter>", lambda e: self._bind_mousewheel())
		self._grid_canvas.bind("<Leave>", lambda e: self._unbind_mousewheel())

	def _on_grid_frame_configure(self, _event=None):
		self._grid_canvas.configure(scrollregion=self._grid_canvas.bbox("all"))
		self._update_scroll_indicator()

	def _on_grid_canvas_configure(self, event):
		self._grid_canvas.itemconfig(self._grid_canvas_win, width=event.width)
		self._update_scroll_indicator()

	def _update_scroll_indicator(self):
		"""Show a subtle scroll indicator if content overflows."""
		self._grid_canvas.delete("scroll_ind")
		bbox = self._grid_canvas.bbox("all")
		if not bbox:
			return
		content_h = bbox[3] - bbox[1]
		canvas_h = self._grid_canvas.winfo_height()
		if content_h > canvas_h + 2:
			# Small down-arrow indicator at bottom-right
			x = self._grid_canvas.winfo_width() - round(12 * self._scale)
			y = canvas_h - round(6 * self._scale)
			self._grid_canvas.create_text(
				x, y, text="\u25bc", fill=C["text_dim"],
				font=("Segoe UI", max(6, round(7 * self._scale))),
				tags="scroll_ind",
			)

	def _bind_mousewheel(self):
		self._grid_canvas.bind_all("<MouseWheel>", self._on_mousewheel)

	def _unbind_mousewheel(self):
		self._grid_canvas.unbind_all("<MouseWheel>")

	def _on_mousewheel(self, event):
		self._grid_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
		self._update_scroll_indicator()

	def _fill_grid(self, chars):
		# Hide frame during population to prevent loading flicker
		self.char_frame.pack_propagate(False) if self.char_frame.winfo_manager() == "pack" else None
		self._grid_canvas.itemconfig(self._grid_canvas_win, state="hidden")

		for w in self.char_frame.winfo_children():
			w.destroy()

		gpad = self._gap // 2 + 1
		for i, (char, name) in enumerate(chars):
			r, col = divmod(i, self._columns)
			btn = TextCanvas(
				self.char_frame, text=char, fg=C["char"],
				font=("Segoe UI Symbol", self._glyph_font_size),
				bg=C["btn"], width=self._btn_size, height=self._btn_size,
				cursor="hand2",
			)
			btn.grid(row=r, column=col, padx=gpad, pady=gpad, sticky="nsew")
			btn.bind("<Button-1>", lambda e, b=btn, ch=char, nm=name: self._click_char(b, ch, nm))
			btn.bind("<Button-3>", lambda e, ch=char, nm=name: self._add_favorite(ch, nm))
			btn.bind("<Enter>", lambda e, b=btn, ch=char, nm=name: self._hover_in(b, ch, nm))
			btn.bind("<Leave>", lambda e, b=btn: self._hover_out(b))

		for col in range(self._columns):
			self.char_frame.columnconfigure(col, weight=1, minsize=self._btn_size)

		# Show frame after population
		self._grid_canvas.itemconfig(self._grid_canvas_win, state="normal")
		self._grid_canvas.yview_moveto(0)

	# --- Status Bar ---

	def _build_status(self):
		h = self._status_h
		bar = tk.Canvas(
			self.root, height=h, width=self.win_w,
			bg=C["titlebar"], highlightthickness=0,
		)
		bar.pack(fill="x", side="bottom")
		self._draw_pattern(bar, self.win_w, h)

		self._status_id = bar.create_text(
			round(14 * self._scale), h // 2, text=self.status_default,
			fill=C["text_dim"], font=("Segoe UI", self._font_ui), anchor="w",
		)

		bar.create_text(
			self.win_w - round(14 * self._scale), h // 2, text="\u00a9 2026 Lunyxium",
			fill=C["text_dim"], font=("Segoe UI", self._font_ui), anchor="e",
		)

		sep_x = self.win_w - round(150 * self._scale)
		bar.create_text(
			sep_x, h // 2, text="\u25aa",
			fill=C["border"], font=("Segoe UI", max(5, round(6 * self._scale))), anchor="center",
		)

		# About link in footer
		self._about_id = bar.create_text(
			sep_x - round(24 * self._scale), h // 2, text="\u2139 About",
			fill=C["text_dim"], font=("Segoe UI", self._font_ui), anchor="e", tags="about",
		)
		self._about_active = False
		bar.tag_bind("about", "<Button-1>", self._toggle_about)
		bar.tag_bind("about", "<Enter>", lambda e: (
			bar.itemconfig("about", fill=C["gold"]),
			bar.configure(cursor="hand2"),
		))
		bar.tag_bind("about", "<Leave>", lambda e: (
			bar.itemconfig("about", fill=C["gold_dim"] if self._about_active else C["text_dim"]),
			bar.configure(cursor=""),
		))
		self.status_bar = bar

	def _default_status_text(self):
		mode = COPY_MODES[self._copy_mode]
		return mode["status"]

	def _set_status(self, text, color=None):
		if color is None and text == self.status_default:
			color = self._status_default_color
		self.status_bar.itemconfig(
			self._status_id, text=text, fill=color or C["text_dim"],
		)

	# === Interactions ===

	# --- Hover ---

	def _hover_in(self, btn, char, name):
		btn.configure(bg=C["btn_hover"])
		self.hover_active = True
		self._set_status(f"{char}  {name}  \u00b7  U+{ord(char):04X}")

	def _hover_out(self, btn):
		btn.configure(bg=C["btn"])
		self.hover_active = False
		if self._transient_status:
			self._set_status(self._transient_status, self._transient_color)
		else:
			self._set_status(self.status_default)

	# --- Click ---

	def _click_char(self, btn, char, name):
		# Visual feedback
		btn.configure(bg=C["btn_click"])
		self.root.after(150, lambda: btn.configure(
			bg=C["btn_hover"] if self.hover_active else C["btn"]
		))

		# Track in recents
		self._add_recent(char)

		mode_key = COPY_MODES[self._copy_mode]["key"]

		if mode_key == "html":
			value = f"&#x{ord(char):04X};"
			self.root.clipboard_clear()
			self.root.clipboard_append(value)
			self.root.update()
			self._show_transient(f"Copied: {value}  ({name})", C["amber"])
		elif mode_key == "code":
			value = f"U+{ord(char):04X}"
			self.root.clipboard_clear()
			self.root.clipboard_append(value)
			self.root.update()
			self._show_transient(f"Copied: {value}  ({name})", C["purple"])
		else:
			self.root.clipboard_clear()
			self.root.clipboard_append(char)
			self.root.update()
			self._show_transient(f"Copied: {char}  ({name})", C["teal"])

			if mode_key == "auto":
				self.root.after(50, send_paste)

		self._save_config()

	def _show_transient(self, text, color):
		"""Show a temporary status message that auto-clears after 2s."""
		self._transient_status = text
		self._transient_color = color
		self._set_status(text, color)
		if self._reset_timer:
			self.root.after_cancel(self._reset_timer)
		self._reset_timer = self.root.after(2000, self._clear_transient)

	def _clear_transient(self):
		self._reset_timer = None
		self._transient_status = None
		self._transient_color = None
		if not self.hover_active:
			self._set_status(self.status_default)

	# --- Copy Mode Toggle ---

	def _cycle_mode(self, event=None):
		"""Cycle through the 4 copy modes."""
		self._copy_mode = (self._copy_mode + 1) % len(COPY_MODES)
		mode = COPY_MODES[self._copy_mode]
		self.titlebar.itemconfig(self._mode_id, text=mode["label"], fill=mode["fg"])
		if not self._delete_mode:
			self.status_default = self._default_status_text()
			self._set_status(self.status_default)

	def _mode_hover_in(self):
		self.titlebar.itemconfig("mode", fill="#fff")
		mode = COPY_MODES[self._copy_mode]
		tips = {
			"copy": "COPY: copies character to clipboard",
			"auto": "AUTO: copies & pastes into active window",
			"html": "HTML: copies HTML entity (e.g. &#x00BD;)",
			"code": "U+: copies Unicode codepoint (e.g. U+00BD)",
		}
		self._set_status(tips[mode["key"]])

	def _mode_hover_out(self):
		mode = COPY_MODES[self._copy_mode]
		self.titlebar.itemconfig("mode", fill=mode["fg"])
		self._set_status(self.status_default)

	# === Opacity ===

	def _setup_opacity(self):
		"""Set idle opacity and bind mouse enter/leave with debounce."""
		self._opacity_timer = None
		self._fade_animating = False
		self._fade_delay = self._config.get("fade_delay", 50)
		self.root.attributes("-alpha", self._idle_opacity)
		self.root.bind("<Enter>", self._on_mouse_enter)
		self.root.bind("<Leave>", self._on_mouse_leave)

	def _on_mouse_enter(self, event=None):
		if self._opacity_timer:
			self.root.after_cancel(self._opacity_timer)
			self._opacity_timer = None
		self._fade_animating = False
		self.root.attributes("-alpha", 1.0)

	def _on_mouse_leave(self, event=None):
		if self._idle_opacity >= 1.0:
			return  # No fade when opacity is off
		# Don't fade while settings flyout is open
		if self._settings_win and self._settings_win.winfo_exists():
			return
		if self._opacity_timer:
			self.root.after_cancel(self._opacity_timer)
		self._opacity_timer = self.root.after(self._fade_delay, self._fade_out)

	def _fade_out(self):
		"""Gradually fade to idle opacity over ~300ms."""
		self._opacity_timer = None
		self._fade_animating = True
		self._fade_step(1.0, self._idle_opacity, 300, 20)

	def _fade_step(self, current, target, total_ms, step_ms):
		"""Animate opacity from current to target."""
		if not self._fade_animating:
			return
		diff = current - target
		steps_left = max(1, total_ms // step_ms)
		new_alpha = current - diff / steps_left
		if abs(new_alpha - target) < 0.02 or new_alpha <= target:
			self.root.attributes("-alpha", target)
			self._fade_animating = False
			return
		self.root.attributes("-alpha", new_alpha)
		self._opacity_timer = self.root.after(
			step_ms, lambda: self._fade_step(new_alpha, target, total_ms - step_ms, step_ms)
		)

	# === Escape to Close ===

	def _on_escape(self, event=None):
		"""Close the window on Escape, but only if it has OS keyboard focus."""
		if self.root.focus_get() is not None:
			self._quit()

	# === Global Hotkey ===

	def _setup_hotkey(self):
		"""Start global hotkey listener thread and begin polling."""
		start_hotkey_listener()
		self._poll_hotkey()

	def _poll_hotkey(self):
		"""Poll for global hotkey press from the listener thread."""
		if check_hotkey_pressed():
			self._toggle_visibility()
		self.root.after(100, self._poll_hotkey)

	def _toggle_visibility(self):
		"""Show or hide the window via global hotkey."""
		if self._visible:
			self._save_config()
			self.root.withdraw()
			self._visible = False
		else:
			self.root.deiconify()
			self._visible = True
			# Reapply opacity then styles — alpha can strip WS_EX flags
			self.root.attributes("-alpha", self._idle_opacity)
			self.root.after(10, lambda: set_no_activate(self.root))

	# === Drag & Snap ===

	def _drag_start(self, event):
		items = self.titlebar.find_overlapping(
			event.x - 2, event.y - 2, event.x + 2, event.y + 2,
		)
		for item in items:
			tags = self.titlebar.gettags(item)
			if "close" in tags or "mode" in tags or "title" in tags or "gear" in tags:
				return
		self.drag["x"] = event.x
		self.drag["y"] = event.y
		self.drag["active"] = True
		self.titlebar.configure(cursor="fleur")

	def _drag_move(self, event):
		if not self.drag["active"]:
			return
		x = self.root.winfo_x() + (event.x - self.drag["x"])
		y = self.root.winfo_y() + (event.y - self.drag["y"])

		# Window snapping (top/bottom of foreground window)
		if self._snap_enabled:
			x, y = self._apply_window_snap(x, y)

		self.root.geometry(f"{self.win_w}x{self.win_h}+{x}+{y}")

		# Close settings flyout during drag
		if self._settings_win and self._settings_win.winfo_exists():
			self._close_settings()

	def _apply_window_snap(self, x, y):
		"""Snap to top/bottom edges of the foreground window."""
		threshold = self._snap_threshold
		my_top = y
		my_bottom = y + self.win_h

		# Snap to taskbar / work area bottom
		_, work_top, _, work_bottom = get_work_area()
		if abs(my_bottom - work_bottom) < threshold:
			y = work_bottom - self.win_h
			return x, y
		if abs(my_top - work_top) < threshold:
			y = work_top
			return x, y

		# Snap to foreground window edges
		target = get_foreground_window_rect()
		if not target:
			return x, y
		tl, tt, tr, tb, thwnd = target
		if hasattr(self, "_hwnd") and thwnd == self._hwnd:
			return x, y

		# Only snap if horizontally overlapping
		my_left, my_right = x, x + self.win_w
		if my_right < tl or my_left > tr:
			return x, y

		# Snap GlyphKit bottom → target top (sit above)
		if abs(my_bottom - tt) < threshold:
			y = tt - self.win_h
		# Snap GlyphKit top → target bottom (sit below)
		elif abs(my_top - tb) < threshold:
			y = tb
		# Snap GlyphKit top → target top (align tops)
		elif abs(my_top - tt) < threshold:
			y = tt
		# Snap GlyphKit bottom → target bottom (align bottoms)
		elif abs(my_bottom - tb) < threshold:
			y = tb - self.win_h

		return x, y

	def _drag_end(self, event):
		if self.drag["active"]:
			self.drag["active"] = False
			self.titlebar.configure(cursor="")

	def _position_bottom(self):
		"""Position window at bottom-center of the screen."""
		self.root.update()
		left, _top, right, bottom = get_work_area()
		x = left + (right - left - self.win_w) // 2
		y = bottom - self.win_h
		self.root.geometry(f"{self.win_w}x{self.win_h}+{x}+{y}")

	# === Settings Flyout ===

	def _toggle_settings(self):
		if self._settings_win and self._settings_win.winfo_exists():
			self._close_settings()
		else:
			self._open_settings()

	def _open_settings(self):
		fw = self.win_w // 2
		fh = round(self.win_h * 0.90)
		main_x = self.root.winfo_x()
		main_y = self.root.winfo_y()

		# Final position: above main window, right-aligned
		fx = main_x + self.win_w - fw
		fy_target = main_y - fh

		# If not enough space above, open below
		_, work_top, _, _ = get_work_area()
		opens_above = fy_target >= work_top
		if not opens_above:
			fy_target = main_y + self.win_h

		win = tk.Toplevel(self.root)
		win.overrideredirect(True)
		win.attributes("-topmost", True)
		win.configure(bg=C["teal_dim"])  # Teal horseshoe outline

		self._settings_win = win
		self._settings_dirty = False

		# Gear highlight (gold)
		self.titlebar.itemconfig("gear", fill=C["gold_dim"])

		# Horseshoe border: 2px on top, left, right — open at bottom
		border_w = 2
		inner = tk.Frame(win, bg=C["bg"])
		inner.pack(fill="both", expand=True, padx=border_w, pady=(border_w, 0))

		# Build settings UI inside the inner frame
		self._build_settings_ui(inner, fw - 2 * border_w, fh)

		# Position the flyout
		win.geometry(f"{fw}x{fh}+{fx}+{fy_target}")

		# Apply WS_EX_NOACTIVATE after mapping
		win.after(50, lambda: set_no_activate(win))

		# Close on Escape
		win.bind("<Escape>", lambda e: self._close_settings())

	def _close_settings(self):
		if self._settings_win and self._settings_win.winfo_exists():
			self._settings_win.destroy()
		self._settings_win = None
		if hasattr(self, "titlebar"):
			self.titlebar.itemconfig("gear", fill=C["text_dim"])
		# Re-evaluate opacity: if mouse is not over main window, start fade
		self.root.after(100, self._check_mouse_after_settings)

	def _check_mouse_after_settings(self):
		"""After settings close, check if mouse is over main window and fade if not."""
		if self._idle_opacity >= 1.0:
			return
		try:
			mx = self.root.winfo_pointerx() - self.root.winfo_rootx()
			my = self.root.winfo_pointery() - self.root.winfo_rooty()
			inside = 0 <= mx <= self.win_w and 0 <= my <= self.win_h
			if not inside:
				self._on_mouse_leave()
			else:
				self.root.attributes("-alpha", 1.0)
		except Exception:
			pass

	def _build_settings_ui(self, win, fw, fh):
		"""Build the settings flyout with titlebar, 2-column layout, bordered boxes."""
		s = self._scale
		pad = round(8 * s)
		font_label = ("Segoe UI", self._font_ui, "bold")
		font_value = ("Segoe UI", self._font_ui)
		font_small = ("Segoe UI", max(7, self._font_ui - 1))
		box_bg = C["bg"]
		box_border = C["border"]

		# --- Bottom bar: separator + Apply button ---
		bottom = tk.Frame(win, bg=C["bg"])
		bottom.pack(side="bottom", fill="x")
		tk.Frame(bottom, height=2, bg=C["teal_dim"]).pack(fill="x", side="bottom")

		self._apply_frame = tk.Frame(bottom, bg=C["bg"])
		self._apply_frame.pack(fill="x", padx=pad, pady=round(4 * s))

		apply_border = tk.Frame(self._apply_frame, bg=C["border"])
		apply_border.pack(anchor="e")
		self._apply_border = apply_border

		self._apply_btn = tk.Label(
			apply_border, text="  Apply  ",
			bg=C["btn"], fg="#555555",
			font=font_label, padx=round(8 * s), pady=round(2 * s),
		)
		self._apply_btn.pack(padx=1, pady=1)
		self._apply_btn.bind("<Button-1>", lambda e: self._apply_settings() if self._settings_dirty else None)
		self._apply_btn.bind("<Enter>", lambda e: self._apply_hover_in())
		self._apply_btn.bind("<Leave>", lambda e: self._apply_hover_out())

		# --- Titlebar (same style as main window) ---
		th = self._titlebar_h
		title_bar = tk.Canvas(win, height=th, bg=C["titlebar"], highlightthickness=0)
		title_bar.pack(fill="x")
		self._draw_pattern(title_bar, fw, th)

		title_bar.create_text(
			round(14 * s), th // 2, text="Settings",
			fill=C["gold_dim"], font=("Segoe UI", self._font_ui, "bold"),
			anchor="w",
		)

		# Close X
		close_x = fw - round(20 * s)
		title_bar.create_text(
			close_x, th // 2 - 2, text="\u00d7",
			fill=C["text_dim"], font=("Segoe UI", self._font_close),
			anchor="center", tags="sclose",
		)
		title_bar.tag_bind("sclose", "<Button-1>", lambda e: self._close_settings())
		title_bar.tag_bind("sclose", "<Enter>", lambda e: title_bar.itemconfig("sclose", fill="#ff6b6b"))
		title_bar.tag_bind("sclose", "<Leave>", lambda e: title_bar.itemconfig("sclose", fill=C["text_dim"]))

		# Teal separator
		tk.Frame(win, height=1, bg=C["teal_dim"]).pack(fill="x")

		# --- Content area: 2 columns with grid for even split ---
		content = tk.Frame(win, bg=C["bg"])
		content.pack(fill="both", expand=True, padx=pad, pady=pad)
		content.columnconfigure(0, weight=1, uniform="settings")
		content.columnconfigure(1, weight=1, uniform="settings")

		left_col = tk.Frame(content, bg=C["bg"])
		left_col.grid(row=0, column=0, sticky="nsew", padx=(0, round(3 * s)))

		right_col = tk.Frame(content, bg=C["bg"])
		right_col.grid(row=0, column=1, sticky="nsew", padx=(round(3 * s), 0))
		content.rowconfigure(0, weight=1)

		# === LEFT COLUMN ===

		# --- Scale ---
		self._build_setting_box(
			left_col, "Scale", box_bg, box_border, font_label, lambda inner: (
				self._build_setting_slider(
					inner, SCALE_STEPS,
					["80%", "90%", "100%", "110%", "125%"],
					SCALE_STEPS.index(self._user_scale) if self._user_scale in SCALE_STEPS else 2,
					"_pending_scale", font_value, font_small,
				)
			), hover="Adjust overall UI size",
		)

		# --- Idle Opacity ---
		opacity_keys = list(OPACITY_PRESETS.keys())
		current_oi = opacity_keys.index(self._opacity_key) if self._opacity_key in opacity_keys else 2
		self._build_setting_box(
			left_col, "Idle Opacity", box_bg, box_border, font_label, lambda inner: (
				self._build_setting_slider(
					inner, opacity_keys,
					["Off", "High", "Mid", "Low"],
					current_oi, "_pending_opacity", font_value, font_small,
				)
			), hover="Transparency when idle",
		)

		# --- Fade Delay ---
		delay_steps = list(range(50, 1050, 50))  # 50ms to 1000ms, 20 steps
		current_delay = self._config.get("fade_delay", 50)
		delay_idx = min(len(delay_steps) - 1, max(0, (current_delay - 50) // 50))
		self._build_setting_box(
			left_col, "Fade Delay", box_bg, box_border, font_label, lambda inner: (
				self._build_setting_slider(
					inner, delay_steps,
					["Fast"] + [""] * (len(delay_steps) - 2) + ["Slow"],
					delay_idx, "_pending_fade_delay", font_value, font_small,
				)
			), hover="Delay before fade starts",
		)

		# === RIGHT COLUMN ===

		# --- Glyph Size ---
		self._build_setting_box(
			right_col, "Glyph Size", box_bg, box_border, font_label, lambda inner: (
				self._build_setting_buttons(
					inner, ["S", "M", "L"],
					self._glyph_key, "_pending_glyph", font_value,
				)
			), hover="S=small, M=default, L=large",
		)

		# --- Window Snapping ---
		self._build_setting_box(
			right_col, "Window Snapping", box_bg, box_border, font_label, lambda inner: (
				self._build_setting_toggle(
					inner, self._snap_enabled, "_pending_snap",
				)
			), hover="Snap to nearby window edges",
		)

		# (Apply button is in the bottom bar, built above)

	def _build_setting_box(self, parent, title, bg, border_color, font_title, build_fn, hover=""):
		"""Build a bordered settings box with title and content."""
		s = self._scale
		border = tk.Frame(parent, bg=border_color)
		border.pack(fill="both", expand=True, pady=(0, round(4 * s)))

		inner_frame = tk.Frame(border, bg=bg)
		inner_frame.pack(fill="x", padx=1, pady=1)

		# Title row
		title_row = tk.Frame(inner_frame, bg=bg)
		title_row.pack(fill="x", padx=round(6 * s), pady=(round(4 * s), round(1 * s)))
		lbl = tk.Label(title_row, text=title, bg=bg, fg=C["text"], font=font_title)
		lbl.pack(anchor="w")
		if hover:
			lbl.bind("<Enter>", lambda e: self._set_status(hover))
			lbl.bind("<Leave>", lambda e: self._set_status(self.status_default))

		# Content
		content = tk.Frame(inner_frame, bg=bg)
		content.pack(fill="x", padx=round(6 * s), pady=(0, round(5 * s)))
		build_fn(content)

	def _build_setting_slider(self, parent, values, labels, current_idx, attr, font_v, font_s):
		"""Build a discrete slider with fixed-width inline min/max labels."""
		s = self._scale
		n = len(values)
		setattr(self, attr, values[current_idx])

		min_text = labels[0] if labels and labels[0] else ""
		max_text = labels[-1] if labels and labels[-1] else ""

		# Layout: [min_label(fixed)] [track(expand)] [max_label(fixed)]
		row = tk.Frame(parent, bg=C["bg"])
		row.pack(fill="x")

		# Fixed-width label containers — generous for text, track fills the rest
		label_w = round(38 * s)

		lf = tk.Frame(row, bg=C["bg"], width=label_w, height=round(18 * s))
		lf.pack(side="left")
		lf.pack_propagate(False)
		tk.Label(lf, text=min_text, bg=C["bg"], fg=C["text_dim"],
			font=font_s, anchor="center").pack(fill="both", expand=True)

		rf = tk.Frame(row, bg=C["bg"], width=label_w, height=round(18 * s))
		rf.pack(side="right")
		rf.pack_propagate(False)
		tk.Label(rf, text=max_text, bg=C["bg"], fg=C["text_dim"],
			font=font_s, anchor="center").pack(fill="both", expand=True)

		track_h = round(20 * s)
		track = tk.Canvas(row, height=track_h, bg=C["bg"], highlightthickness=0, bd=0, cursor="hand2")
		track.pack(fill="x", expand=True, padx=round(2 * s))

		def _draw_slider():
			track.delete("all")
			tw = track.winfo_width()
			if tw < 10:
				track.after(50, _draw_slider)
				return
			cy = track_h // 2
			margin = round(8 * s)
			usable = tw - 2 * margin

			# Track line
			track.create_line(margin, cy, tw - margin, cy, fill=C["border"], width=round(2 * s))

			# Tick marks (only for sliders with few steps)
			if n <= 10:
				for i in range(n):
					x = margin + (usable * i // max(1, n - 1)) if n > 1 else margin
					tick_h = round(3 * s)
					track.create_line(x, cy - tick_h, x, cy + tick_h, fill=C["border"], width=1)

			# Active knob
			idx = values.index(getattr(self, attr)) if getattr(self, attr) in values else 0
			ax = margin + (usable * idx // max(1, n - 1)) if n > 1 else margin
			r = round(7 * s)
			track.create_oval(
				ax - r, cy - r, ax + r, cy + r,
				fill=C["teal"], outline=C["teal_mid"], width=round(2 * s),
			)

		def _on_click(event):
			tw = track.winfo_width()
			margin = round(8 * s)
			usable = tw - 2 * margin
			if usable <= 0:
				return
			ratio = max(0, min(1, (event.x - margin) / usable))
			idx = round(ratio * (n - 1))
			setattr(self, attr, values[idx])
			self._mark_settings_dirty()
			_draw_slider()

		track.bind("<Button-1>", _on_click)
		track.bind("<B1-Motion>", _on_click)
		track.bind("<Configure>", lambda e: _draw_slider())

	def _build_setting_buttons(self, parent, options, current, attr, font_v):
		"""Build a button-group setting (e.g., S/M/L)."""
		s = self._scale
		btn_frame = tk.Frame(parent, bg=C["bg"])
		btn_frame.pack(anchor="w", pady=(round(2 * s), 0))

		setattr(self, attr, current)
		btns = []

		def _select(opt):
			setattr(self, attr, opt)
			self._mark_settings_dirty()
			for b, o in btns:
				if o == opt:
					b.configure(bg=C["teal_dark"], fg=C["teal"])
				else:
					b.configure(bg=C["btn"], fg=C["text_dim"])

		for opt in options:
			b = tk.Label(
				btn_frame, text=f"  {opt}  ",
				bg=C["teal_dark"] if opt == current else C["btn"],
				fg=C["teal"] if opt == current else C["text_dim"],
				font=font_v, cursor="hand2",
			)
			b.pack(side="left", padx=(0, round(6 * s)))
			b.bind("<Button-1>", lambda e, o=opt: _select(o))
			btns.append((b, opt))

	def _build_setting_toggle(self, parent, current, attr):
		"""Build an on/off toggle switch."""
		s = self._scale
		setattr(self, attr, current)

		tw = round(40 * s)
		th = round(20 * s)
		toggle = tk.Canvas(parent, width=tw, height=th, bg=C["bg"], highlightthickness=0, bd=0, cursor="hand2")
		toggle.pack(anchor="w", pady=(round(2 * s), 0))

		def _draw():
			toggle.delete("all")
			on = getattr(self, attr)
			bg_col = C["teal_dim"] if on else C["border"]
			r = th // 2
			# Rounded track
			toggle.create_rectangle(r, 0, tw - r, th, fill=bg_col, outline="")
			toggle.create_oval(0, 0, th, th, fill=bg_col, outline="")
			toggle.create_oval(tw - th, 0, tw, th, fill=bg_col, outline="")
			# Knob
			knob_x = tw - r if on else r
			kr = r - 3
			toggle.create_oval(
				knob_x - kr, 3, knob_x + kr, th - 3,
				fill="#fff" if on else C["text_dim"], outline="",
			)

		def _click(event):
			setattr(self, attr, not getattr(self, attr))
			self._mark_settings_dirty()
			_draw()

		toggle.bind("<Button-1>", _click)
		_draw()

	def _apply_hover_in(self):
		if self._settings_dirty:
			self._apply_btn.configure(bg=C["gold"], fg=C["bg"])
			self._apply_border.configure(bg=C["gold_dim"])
			self._set_status("Apply changes and restart")
		else:
			self._set_status("No changes to apply")

	def _apply_hover_out(self):
		if self._settings_dirty:
			self._apply_btn.configure(bg=C["gold_dark"], fg=C["gold"])
			self._apply_border.configure(bg=C["gold_dim"])
		else:
			self._apply_btn.configure(bg=C["btn"], fg="#555555")
			self._apply_border.configure(bg=C["border"])
		self._set_status(self.status_default)

	def _mark_settings_dirty(self):
		"""Activate the Apply button when settings have been changed."""
		self._settings_dirty = True
		if hasattr(self, "_apply_btn"):
			self._apply_btn.configure(bg=C["gold_dark"], fg=C["gold"], cursor="hand2")
			self._apply_border.configure(bg=C["gold_dim"])

	def _apply_settings(self):
		"""Apply changed settings and rebuild the UI."""
		try:
			# Write pending values into instance vars AND config so both
			# _save_config and _load_config/_compute_layout see them
			if hasattr(self, "_pending_scale"):
				self._user_scale = self._pending_scale
				self._config["user_scale"] = self._pending_scale
			if hasattr(self, "_pending_opacity"):
				self._opacity_key = self._pending_opacity
				self._config["idle_opacity"] = self._pending_opacity
			if hasattr(self, "_pending_fade_delay"):
				self._config["fade_delay"] = self._pending_fade_delay
			if hasattr(self, "_pending_glyph"):
				self._glyph_key = self._pending_glyph
				self._config["glyph_size"] = self._pending_glyph
			if hasattr(self, "_pending_snap"):
				self._snap_enabled = self._pending_snap
				self._config["snap_enabled"] = self._pending_snap

			# Save config first
			self._save_config()

			# Show restart message briefly, then rebuild
			self._set_status("Applying settings\u2026", C["teal"])
			self.root.update()

			# Close settings (skip fade check — we're reopening immediately)
			if self._settings_win and self._settings_win.winfo_exists():
				self._settings_win.destroy()
			self._settings_win = True  # Truthy sentinel to suppress fade during rebuild
			self._rebuild()
			self._settings_win = None
			self._show_transient("Settings applied", C["teal"])
			# Keep at full opacity until settings reopen
			self.root.attributes("-alpha", 1.0)
			self._open_settings()
		except Exception as e:
			# Surface the error so it's not silently swallowed
			print(f"Settings apply error: {e}")
			import traceback
			traceback.print_exc()

	# === Run ===

	def run(self):
		self.root.mainloop()


def main():
	app = GlyphKitApp()
	app.run()


if __name__ == "__main__":
	main()
