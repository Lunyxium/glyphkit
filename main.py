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
	get_work_area, force_foreground,
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
	"version": "1.0.0",
	"author": "Matt B\u00e4umli",
	"handle": "Lunyxium",
	"github": "https://github.com/Lunyxium",
	"year": 2026,
	"license": "MIT",
	"hotkey": "Ctrl + Alt + G",
}

# === Layout ===

COLUMNS = 27
BTN_SIZE = 36
GAP = 3
PAD = 10
SNAP_DIST = 60
TITLEBAR_H = 36
STATUS_H = 38
GRID_ROWS = 3
IDLE_OPACITY = 0.66
MAX_RECENTS = 24

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
		self.status_default = self._default_status_text()
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

		self.win_w = COLUMNS * (BTN_SIZE + GAP) + GAP + PAD * 2
		self.win_h = 0

		self._load_config()
		self._build()
		self.root.deiconify()
		self.root.after(50, self._init_hwnd)

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
		self.status_default = self._default_status_text()

	def _save_config(self):
		config = {
			"x": self.root.winfo_x(),
			"y": self.root.winfo_y(),
			"copy_mode": self._copy_mode,
			"favorites": self._favorites,
			"recents": self._recents,
		}
		try:
			with open(CONFIG_PATH, "w") as f:
				json.dump(config, f)
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

	def _lock_layout(self):
		"""Lock grid height and set explicit window geometry."""
		self.root.update()
		children = self.char_frame.winfo_children()
		if children:
			row_h = children[0].winfo_reqheight() + (GAP // 2 + 1) * 2
		else:
			row_h = BTN_SIZE + 4
		grid_h = GRID_ROWS * row_h + 8
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
		bar = tk.Canvas(
			self.root, height=TITLEBAR_H, width=self.win_w,
			bg=C["titlebar"], highlightthickness=0,
		)
		bar.pack(fill="x")
		self._draw_pattern(bar, self.win_w, TITLEBAR_H)

		bar.create_text(
			14, TITLEBAR_H // 2, text="GlyphKit",
			fill=C["teal_dim"], font=("Segoe UI", 10, "bold"),
			anchor="w", tags="title",
		)
		bar.tag_bind("title", "<Enter>", lambda e: (
			bar.itemconfig("title", fill=C["teal"]),
			self._set_status(f"Toggle: Ctrl+Alt+G  \u00b7  Close: Esc (while focused)"),
		))
		bar.tag_bind("title", "<Leave>", lambda e: (
			bar.itemconfig("title", fill=C["teal_dim"]),
			self._set_status(self.status_default),
		))

		# --- Close button ---
		cx = self.win_w - 20
		bar.create_text(
			cx, TITLEBAR_H // 2 - 2, text="\u00d7",
			fill=C["text_dim"], font=("Segoe UI", 14), anchor="center", tags="close",
		)
		bar.tag_bind("close", "<Button-1>", lambda e: self._quit())
		bar.tag_bind("close", "<Enter>", lambda e: bar.itemconfig("close", fill="#ff6b6b"))
		bar.tag_bind("close", "<Leave>", lambda e: bar.itemconfig("close", fill=C["text_dim"]))

		# --- Copy mode toggle (cycles through 4 modes) ---
		ax = cx - 56
		mode = COPY_MODES[self._copy_mode]
		self._mode_id = bar.create_text(
			ax, TITLEBAR_H // 2, text=mode["label"],
			fill=mode["fg"], font=("Segoe UI", 8, "bold"),
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

	def _draw_pattern(self, canvas, w, h):
		"""Draw subtle crosshatch pattern on canvas chrome."""
		spacing = 14
		col = C["pattern"]
		for i in range(-h, w + h, spacing):
			canvas.create_line(i, 0, i + h, h, fill=col, width=1)
			canvas.create_line(i + h, 0, i, h, fill=col, width=1)

	# --- Tabs (multi-row, button-style) ---

	def _make_tab(self, parent, key, text, side="left"):
		"""Create a button-style tab with a 1px border frame."""
		border = tk.Frame(parent, bg=C["tab_border"])
		border.pack(side=side, padx=3)
		bg = C["tab"]
		fg = C["text_dim"]
		font = ("Consolas", 9)
		f = tkFont.Font(family="Consolas", size=9)
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
		outer.pack(fill="x", pady=(6, 4))

		for row_keys in TAB_ROWS:
			row_frame = tk.Frame(outer, bg=C["bg"])
			row_frame.pack(fill="x", pady=3, padx=PAD)

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
		self._search_glow.pack(side="right", padx=(6, 3))

		self._search_border = tk.Frame(self._search_glow, bg=self._search_border_idle)
		self._search_border.pack(padx=1, pady=1)

		inner = tk.Frame(self._search_border, bg=self._search_bg)
		inner.pack(padx=1, pady=1)

		self._search_icon = tk.Label(
			inner, text="\U0001f50d", bg=self._search_bg, fg="#506b65",
			font=("Segoe UI", 11),
		)
		self._search_icon.pack(side="left", padx=(5, 0), pady=(0, 1))

		self._search_entry = tk.Entry(
			inner, textvariable=self._search_var,
			bg=self._search_bg, fg="#b0b0b0", insertbackground=C["teal"],
			font=("Segoe UI", 9), width=18,
			relief="flat", bd=0,
		)
		self._search_entry.pack(side="left", padx=(2, 6), pady=2)

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
			for col in range(COLUMNS):
				self.char_frame.columnconfigure(col, weight=0, minsize=0)
			self.char_frame.columnconfigure(0, weight=1)
			tk.Label(
				self.char_frame, text="No matches found",
				bg=C["bg"], fg=C["text_dim"], font=("Segoe UI", 10),
			).grid(row=0, column=0, sticky="w", padx=4, pady=8)

	# --- Tab Deselection Helper ---

	def _deselect_all_tabs(self):
		"""Reset all category, favorites, and recents tabs to default state."""
		for k, lbl in self.tabs.items():
			self._tab_borders[k].configure(bg=C["tab_border"])
			default_bg, default_fg = self._tab_defaults[k]
			lbl.configure(fg=default_fg, bg=default_bg, font=("Consolas", 9))
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
		tab_font = ("Consolas", 9)
		star_font = ("Segoe UI", 11)
		icon_font = ("Segoe UI", 10)
		tf = tkFont.Font(family="Consolas", size=9)
		sf = tkFont.Font(family="Segoe UI", size=11)
		rif = tkFont.Font(family="Segoe UI", size=10)
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

		for col in range(COLUMNS):
			self.char_frame.columnconfigure(col, weight=0, minsize=0)

		if not self._favorites:
			self.char_frame.columnconfigure(0, weight=1)
			tk.Label(
				self.char_frame,
				text="Right-click any character to add it here",
				bg=C["bg"], fg=C["text_dim"], font=("Segoe UI", 10),
			).grid(row=0, column=0, sticky="w", padx=4, pady=8)
			self._build_delete_btn(disabled=True)
			return

		sorted_favs = []
		for char in self._favorites:
			if char in self._char_order:
				idx, name = self._char_order[char]
				sorted_favs.append((idx, char, name))
		sorted_favs.sort(key=lambda x: x[0])

		for i, (_idx, char, name) in enumerate(sorted_favs):
			r, col = divmod(i, COLUMNS)
			btn = TextCanvas(
				self.char_frame, text=char, fg=C["char"],
				font=("Segoe UI Symbol", 13),
				bg=C["btn"], width=BTN_SIZE, height=BTN_SIZE,
				cursor="hand2",
			)
			btn.grid(row=r, column=col, padx=GAP // 2 + 1, pady=GAP // 2 + 1, sticky="nsew")
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

		for col in range(COLUMNS):
			self.char_frame.columnconfigure(col, weight=1, minsize=BTN_SIZE)

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

		for col in range(COLUMNS):
			self.char_frame.columnconfigure(col, weight=0, minsize=0)

		if not self._recents:
			self.char_frame.columnconfigure(0, weight=1)
			tk.Label(
				self.char_frame,
				text="Characters you use will appear here",
				bg=C["bg"], fg=C["text_dim"], font=("Segoe UI", 10),
			).grid(row=0, column=0, sticky="w", padx=4, pady=8)
			return

		for i, char in enumerate(self._recents):
			r, col = divmod(i, COLUMNS)
			name = self._char_order.get(char, (0, char))[1]
			btn = TextCanvas(
				self.char_frame, text=char, fg=C["char"],
				font=("Segoe UI Symbol", 13),
				bg=C["btn"], width=BTN_SIZE, height=BTN_SIZE,
				cursor="hand2",
			)
			btn.grid(row=r, column=col, padx=GAP // 2 + 1, pady=GAP // 2 + 1, sticky="nsew")
			btn.bind("<Button-1>", lambda e, b=btn, ch=char, nm=name: self._click_char(b, ch, nm))
			btn.bind("<Button-3>", lambda e, ch=char, nm=name: self._add_favorite(ch, nm))
			btn.bind("<Enter>", lambda e, b=btn, ch=char, nm=name: self._hover_in(b, ch, nm))
			btn.bind("<Leave>", lambda e, b=btn: self._hover_out(b))

		for col in range(COLUMNS):
			self.char_frame.columnconfigure(col, weight=1, minsize=BTN_SIZE)

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

		for col in range(COLUMNS):
			self.char_frame.columnconfigure(col, weight=0, minsize=0)
		self.char_frame.columnconfigure(0, weight=1)

		info = APP_INFO
		dim = C["text_dim"]
		sep = "  |  "

		# Row 0: Name + version
		tk.Label(
			self.char_frame,
			text=f"{info['name']}  v{info['version']}",
			bg=C["bg"], fg=C["teal"], font=("Segoe UI", 12, "bold"), anchor="w",
		).grid(row=0, column=0, sticky="w", padx=4, pady=(4, 2))

		# Row 1: Author + GitHub link + license + stats
		row1 = tk.Frame(self.char_frame, bg=C["bg"])
		row1.grid(row=1, column=0, sticky="w", padx=4, pady=(2, 2))

		tk.Label(
			row1, text=f"{info['author']}  /  ",
			bg=C["bg"], fg=dim, font=("Segoe UI", 9),
		).pack(side="left")

		handle = tk.Label(
			row1, text=info["handle"],
			bg=C["bg"], fg=C["teal_dim"], font=("Segoe UI", 9, "underline"), cursor="hand2",
		)
		handle.pack(side="left")
		handle.bind("<Button-1>", lambda e: os.startfile(info["github"]))
		handle.bind("<Enter>", lambda e: handle.configure(fg=C["teal"]))
		handle.bind("<Leave>", lambda e: handle.configure(fg=C["teal_dim"]))

		tk.Label(
			row1, text=f"{sep}{info['license']} License{sep}433 glyphs / 13 categories",
			bg=C["bg"], fg=dim, font=("Segoe UI", 9),
		).pack(side="left")

		# Row 2: Shortcuts + stack
		row2 = tk.Frame(self.char_frame, bg=C["bg"])
		row2.grid(row=2, column=0, sticky="w", padx=4, pady=(0, 4))
		tk.Label(
			row2,
			text=f"Toggle: {info['hotkey']}{sep}Close: Esc{sep}Python + tkinter + ctypes",
			bg=C["bg"], fg=dim, font=("Segoe UI", 9),
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
		self.grid_container = tk.Frame(self.root, bg=C["bg"], padx=PAD)
		self.grid_container.pack(fill="both", expand=True, pady=4)
		self.char_frame = tk.Frame(self.grid_container, bg=C["bg"])
		self.char_frame.pack(fill="x", anchor="n")

	def _fill_grid(self, chars):
		for w in self.char_frame.winfo_children():
			w.destroy()

		for i, (char, name) in enumerate(chars):
			r, col = divmod(i, COLUMNS)
			btn = TextCanvas(
				self.char_frame, text=char, fg=C["char"],
				font=("Segoe UI Symbol", 13),
				bg=C["btn"], width=BTN_SIZE, height=BTN_SIZE,
				cursor="hand2",
			)
			btn.grid(row=r, column=col, padx=GAP // 2 + 1, pady=GAP // 2 + 1, sticky="nsew")
			btn.bind("<Button-1>", lambda e, b=btn, ch=char, nm=name: self._click_char(b, ch, nm))
			btn.bind("<Button-3>", lambda e, ch=char, nm=name: self._add_favorite(ch, nm))
			btn.bind("<Enter>", lambda e, b=btn, ch=char, nm=name: self._hover_in(b, ch, nm))
			btn.bind("<Leave>", lambda e, b=btn: self._hover_out(b))

		for col in range(COLUMNS):
			self.char_frame.columnconfigure(col, weight=1, minsize=BTN_SIZE)

	# --- Status Bar ---

	def _build_status(self):
		bar = tk.Canvas(
			self.root, height=STATUS_H, width=self.win_w,
			bg=C["titlebar"], highlightthickness=0,
		)
		bar.pack(fill="x", side="bottom")
		self._draw_pattern(bar, self.win_w, STATUS_H)

		self._status_id = bar.create_text(
			14, STATUS_H // 2, text=self.status_default,
			fill=C["text_dim"], font=("Segoe UI", 9), anchor="w",
		)

		bar.create_text(
			self.win_w - 14, STATUS_H // 2, text="\u00a9 2026 Lunyxium",
			fill=C["text_dim"], font=("Segoe UI", 9), anchor="e",
		)

		bar.create_text(
			self.win_w - 150, STATUS_H // 2, text="\u25aa",
			fill=C["border"], font=("Segoe UI", 6), anchor="center",
		)

		# About link in footer
		self._about_id = bar.create_text(
			self.win_w - 174, STATUS_H // 2, text="\u2139 About",
			fill=C["text_dim"], font=("Segoe UI", 9), anchor="e", tags="about",
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
		self.root.attributes("-alpha", IDLE_OPACITY)
		self.root.bind("<Enter>", self._on_mouse_enter)
		self.root.bind("<Leave>", self._on_mouse_leave)

	def _on_mouse_enter(self, event=None):
		if self._opacity_timer:
			self.root.after_cancel(self._opacity_timer)
			self._opacity_timer = None
		self.root.attributes("-alpha", 1.0)

	def _on_mouse_leave(self, event=None):
		if self._opacity_timer:
			self.root.after_cancel(self._opacity_timer)
		self._opacity_timer = self.root.after(50, self._fade_out)

	def _fade_out(self):
		self._opacity_timer = None
		self.root.attributes("-alpha", IDLE_OPACITY)

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
			self.root.attributes("-alpha", IDLE_OPACITY)
			self.root.after(10, lambda: set_no_activate(self.root))

	# === Drag & Snap ===

	def _drag_start(self, event):
		items = self.titlebar.find_overlapping(
			event.x - 2, event.y - 2, event.x + 2, event.y + 2,
		)
		for item in items:
			tags = self.titlebar.gettags(item)
			if "close" in tags or "mode" in tags or "title" in tags:
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
		self.root.geometry(f"{self.win_w}x{self.win_h}+{x}+{y}")

	def _drag_end(self, event):
		if self.drag["active"]:
			self.drag["active"] = False
			self.titlebar.configure(cursor="")
			self._snap_if_close()

	def _position_bottom(self):
		"""Position window at bottom-center of the screen."""
		self.root.update()
		left, _top, right, bottom = get_work_area()
		x = left + (right - left - self.win_w) // 2
		y = bottom - self.win_h
		self.root.geometry(f"{self.win_w}x{self.win_h}+{x}+{y}")

	def _snap_if_close(self):
		"""Snap to bottom screen edge if the window is close enough."""
		_, _, _, work_bottom = get_work_area()
		win_bottom = self.root.winfo_y() + self.win_h
		if abs(win_bottom - work_bottom) < SNAP_DIST:
			y = work_bottom - self.win_h
			self.root.geometry(f"{self.win_w}x{self.win_h}+{self.root.winfo_x()}+{y}")

	# === Run ===

	def run(self):
		self.root.mainloop()


def main():
	app = GlyphKitApp()
	app.run()


if __name__ == "__main__":
	main()
