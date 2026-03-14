"""GlyphKit — Unicode character palette for Windows 11.

A compact, always-on-top character picker that doesn't steal focus
from the target application. Click a character to copy (and optionally
auto-paste) into whatever window you're working in.
"""

import json
import os
import tkinter as tk
import tkinter.font as tkFont
from characters import CATEGORIES
from win32_utils import enable_dpi_awareness, set_no_activate, send_paste, get_work_area, force_foreground


# === Config ===

_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_DIR, "config.json")


# === Theme: Dark Grey + Teal ===

C = {
	"bg":        "#1a1a1a",
	"titlebar":  "#141414",
	"surface":   "#1e1e1e",
	"btn":       "#262626",
	"btn_hover": "#303030",
	"btn_click": "#0a3530",
	"border":    "#2a2a2a",
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
	"pattern":   "#1c1c1c",
	"tab":       "#222222",
	"tab_border": "#2e2e2e",
}

# === App Info ===

APP_INFO = {
	"name": "GlyphKit",
	"version": "1.0.0",
	"author": "Lunyxium",
	"year": 2026,
	"description": "Unicode character palette for Windows 11",
	"tech": "Python \u00b7 tkinter \u00b7 ctypes \u00b7 zero dependencies",
	"license": "MIT",
}

# === Layout ===

COLUMNS = 26
BTN_SIZE = 36
GAP = 3
PAD = 10
SNAP_DIST = 60
TITLEBAR_H = 36
STATUS_H = 38
GRID_ROWS = 3

# Tab rows: grouped by theme. Only categories present in CATEGORIES are shown.
TAB_ROWS = [
	["Math", "Scripts", "Sets", "Logic", "Greek", "Arrows", "Fractions"],
	["Roman", "Shapes", "Boxes", "Typography", "Currency", "Science"],
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
		if fg:
			self.set_fg(fg)
		if kw:
			super().configure(**kw)

	config = configure


class GlyphKitApp:
	def __init__(self):
		enable_dpi_awareness()

		self.root = tk.Tk()
		self.root.withdraw()

		self.auto_paste = True
		self.current_cat = "Math"
		self.tabs = {}
		self.char_frame = None
		self.status_bar = None
		self.drag = {"x": 0, "y": 0, "active": False}
		self.status_default = self._default_status_text()
		self._status_default_color = None
		self.hover_active = False
		self._reset_timer = None
		self._delete_mode = False
		self._favorites = []
		self._char_order = self._build_char_order()
		self._search_var = tk.StringVar()
		self._search_var.trace_add("write", self._on_search)

		self.win_w = COLUMNS * (BTN_SIZE + GAP) + GAP + PAD * 2
		self.win_h = 0

		self._load_config()
		self._build()
		self.root.deiconify()
		self.root.after(50, self._init_hwnd)

	def _init_hwnd(self):
		self._hwnd = set_no_activate(self.root)

	@staticmethod
	def _build_char_order():
		"""Build global ordering index for favorites sorting."""
		order = {}
		# Build char→name lookup at the same time
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
		self.auto_paste = self._config.get("auto_paste", True)
		self._favorites = self._config.get("favorites", [])

	def _save_config(self):
		config = {
			"x": self.root.winfo_x(),
			"y": self.root.winfo_y(),
			"auto_paste": self.auto_paste,
			"favorites": self._favorites,
		}
		try:
			with open(CONFIG_PATH, "w") as f:
				json.dump(config, f)
		except OSError:
			pass

	def _quit(self):
		self._save_config()
		self.root.quit()

	# === Build UI ===

	def _build(self):
		r = self.root
		r.title("GlyphKit")
		r.overrideredirect(True)
		r.configure(bg=C["bg"])
		r.attributes("-topmost", True)

		self._build_titlebar()
		tk.Frame(r, height=1, bg=C["teal_dark"]).pack(fill="x")
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
		self.root.update_idletasks()
		# Measure actual row height from rendered buttons
		children = self.char_frame.winfo_children()
		if children:
			row_h = children[0].winfo_reqheight() + (GAP // 2 + 1) * 2
		else:
			row_h = BTN_SIZE + 4
		grid_h = GRID_ROWS * row_h + 8
		self.grid_container.configure(height=grid_h)
		self.grid_container.pack_propagate(False)
		# Set explicit window size
		self.root.update_idletasks()
		self.win_h = self.root.winfo_reqheight()
		self.root.geometry(f"{self.win_w}x{self.win_h}")

	def _apply_config(self):
		"""Apply saved position and auto-paste preference."""
		if "x" in self._config and "y" in self._config:
			self.root.geometry(
				f"{self.win_w}x{self.win_h}+{self._config['x']}+{self._config['y']}"
			)
		else:
			self._position_bottom()
		# Sync auto-paste toggle display
		if not self.auto_paste:
			self.titlebar.itemconfig(self._paste_id, text="COPY \u25cb", fill=C["text_dim"])

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
			fill=C["teal_dim"], font=("Segoe UI", 10, "bold"), anchor="w",
		)

		# --- Close button ---
		cx = self.win_w - 20
		bar.create_text(
			cx, TITLEBAR_H // 2 - 2, text="\u00d7",
			fill=C["text_dim"], font=("Segoe UI", 14), anchor="center", tags="close",
		)
		bar.tag_bind("close", "<Button-1>", lambda e: self._quit())
		bar.tag_bind("close", "<Enter>", lambda e: bar.itemconfig("close", fill="#ff6b6b"))
		bar.tag_bind("close", "<Leave>", lambda e: bar.itemconfig("close", fill=C["text_dim"]))

		# --- Auto-paste toggle ---
		ax = cx - 56
		self._paste_id = bar.create_text(
			ax, TITLEBAR_H // 2, text="AUTO \u25cf",
			fill=C["teal"], font=("Segoe UI", 8, "bold"), anchor="center", tags="auto",
		)
		bar.tag_bind("auto", "<Button-1>", self._toggle_paste)
		bar.tag_bind("auto", "<Enter>", lambda e: self._auto_hover_in())
		bar.tag_bind("auto", "<Leave>", lambda e: self._auto_hover_out())

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
		font = ("Segoe UI", 10)
		f = tkFont.Font(family="Segoe UI", size=10)
		tw = f.measure(text) + 8
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

			# Favorites + info right-aligned on first row
			if row_keys is TAB_ROWS[0]:
				self._build_fav_row(row_frame)

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
		"""Search entry, packed right on the first tab row."""
		self._search_bg = "#1e2120"
		self._search_border_idle = "#363636"
		self._search_border_active = C["teal_dim"]
		self._search_glow_color = C["teal_dark"]

		# Outer glow frame (visible only when active)
		self._search_glow = tk.Frame(parent, bg=C["bg"])
		self._search_glow.pack(side="right", padx=(6, 3))

		# Border frame (the visible outline)
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

		# Init placeholder
		self._set_search_placeholder()

	def _set_search_placeholder(self):
		"""Show placeholder text in search bar, hide cursor."""
		self._search_placeholder = True
		self._search_clearing = True
		self._search_entry.delete(0, "end")
		self._search_entry.insert(0, "search...")
		self._search_entry.configure(fg=C["text_dim"], insertbackground=self._search_bg)
		self._search_clearing = False
		# Idle outline, no glow, dim icon
		self._search_border.configure(bg=self._search_border_idle)
		self._search_glow.configure(bg=C["bg"])
		self._search_icon.configure(fg="#506b65")
		# Move focus away so blinking cursor disappears
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
		# Active outline + glow + icon highlight
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
			else:
				self._fill_grid(CATEGORIES[self.current_cat]["chars"])
			return

		# Collect matches from all categories
		matches = []
		for cat_data in CATEGORIES.values():
			for char, name in cat_data["chars"]:
				if query in name.lower():
					matches.append((char, name))

		# Deduplicate while preserving order
		seen = set()
		unique = []
		for char, name in matches:
			if char not in seen:
				seen.add(char)
				unique.append((char, name))

		# Deselect all tabs visually (including favorites)
		for k, lbl in self.tabs.items():
			self._tab_borders[k].configure(bg=C["tab_border"])
			default_bg, default_fg = self._tab_defaults[k]
			lbl.configure(fg=default_fg, bg=default_bg, font=("Segoe UI", 10))
		if hasattr(self, "_fav_canvas"):
			self._fav_accent.configure(bg="#1a3a36")
			self._fav_set_colors(self._fav_defaults[1], self._fav_defaults[0], star="\u2606")
		if hasattr(self, "_del_btn_frame"):
			self._del_btn_frame.destroy()

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

	# --- Favorites ---

	def _build_fav_row(self, parent):
		"""Build Favorites button + info icon, right-aligned on tab row 1."""
		# Favorites tab — accent bar style (packed first = rightmost)
		fav_frame = tk.Frame(parent, bg=C["bg"])
		fav_frame.pack(side="right", padx=(4, 0))

		# Info icon — raw Canvas for vertical centering (left of favorites)
		info_font = ("Segoe UI", 14, "bold")
		inf = tkFont.Font(family="Segoe UI", size=14, weight="bold")
		iw = inf.measure("\u24d8") + 8
		ih = inf.metrics("ascent") + inf.metrics("descent") + 6
		info = tk.Canvas(
			parent, width=iw, height=ih,
			bg=C["bg"], highlightthickness=0, bd=0, cursor="hand2",
		)
		info.pack(side="right", padx=(4, 2))
		self._info_text_id = info.create_text(
			iw // 2, ih // 2 - 3, text="\u24d8", fill="#555555", font=info_font,
		)
		info.bind("<Enter>", lambda e: (
			info.itemconfig(self._info_text_id, fill=C["teal_dim"]),
			self._set_status("Right-click any character to add to Favorites"),
		))
		info.bind("<Leave>", lambda e: (
			info.itemconfig(self._info_text_id, fill="#555555"),
			self._set_status(self.status_default),
		))

		fav_bg = "#1a2422"
		fav_fg = C["teal_dim"]
		star_font = ("Segoe UI", 15)
		label_font = ("Segoe UI", 10, "bold")
		f = tkFont.Font(family="Segoe UI", size=10, weight="bold")
		sf = tkFont.Font(family="Segoe UI", size=15)
		label_w = f.measure("Favorites") + sf.measure("\u2606 ") + 20
		label_h = f.metrics("ascent") + f.metrics("descent") + 6

		cvs = tk.Canvas(
			fav_frame, width=label_w, height=label_h,
			bg=fav_bg, highlightthickness=0, bd=0, cursor="hand2",
		)
		cvs.pack(side="left")

		accent = tk.Frame(fav_frame, bg="#1a3a36", width=3)
		accent.pack(side="left", fill="y")

		star_x = sf.measure("\u2606") // 2 + 6
		label_x = sf.measure("\u2606 ") + 8
		cy = label_h // 2 - 1
		self._fav_star_id = cvs.create_text(
			star_x, cy - 3, text="\u2606", fill=fav_fg, font=star_font,
		)
		self._fav_label_id = cvs.create_text(
			label_x, cy, text="Favorites", fill=fav_fg, font=label_font, anchor="w",
		)
		self._fav_canvas = cvs

		cvs.bind("<Button-1>", lambda e: self._show_favorites())
		cvs.bind("<Enter>", lambda e: self._fav_hover_in())
		cvs.bind("<Leave>", lambda e: self._fav_hover_out())
		self._fav_accent = accent
		self._fav_defaults = (fav_bg, fav_fg)

	def _fav_set_colors(self, fg, bg, star=None):
		self._fav_canvas.configure(bg=bg)
		self._fav_canvas.itemconfig(self._fav_star_id, fill=fg)
		self._fav_canvas.itemconfig(self._fav_label_id, fill=fg)
		if star:
			self._fav_canvas.itemconfig(self._fav_star_id, text=star)

	def _fav_hover_in(self):
		if self.current_cat != "_favorites":
			self._fav_set_colors(C["teal"], self._fav_defaults[0])

	def _fav_hover_out(self):
		if self.current_cat != "_favorites":
			self._fav_set_colors(*self._fav_defaults[::-1])

	def _show_favorites(self):
		"""Show the Favorites grid."""
		self._search_clearing = True
		self._search_var.set("")
		if hasattr(self, "_search_entry"):
			self._set_search_placeholder()
		self._search_clearing = False

		# Reset About
		self._about_active = False
		if hasattr(self, "status_bar"):
			self.status_bar.itemconfig("about", fill=C["text_dim"])

		# Deselect all category tabs
		for k, lbl in self.tabs.items():
			self._tab_borders[k].configure(bg=C["tab_border"])
			default_bg, default_fg = self._tab_defaults[k]
			lbl.configure(fg=default_fg, bg=default_bg)

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

		# Reset column configs
		for col in range(COLUMNS):
			self.char_frame.columnconfigure(col, weight=0, minsize=0)

		if not self._favorites:
			# Empty state hint
			self.char_frame.columnconfigure(0, weight=1)
			tk.Label(
				self.char_frame,
				text="Right-click any character to add it here",
				bg=C["bg"], fg=C["text_dim"], font=("Segoe UI", 10),
			).grid(row=0, column=0, sticky="w", padx=4, pady=8)
			self._build_delete_btn(disabled=True)
			return

		# Sort favorites by global category order
		sorted_favs = []
		for char in self._favorites:
			if char in self._char_order:
				idx, name = self._char_order[char]
				sorted_favs.append((idx, char, name))
		sorted_favs.sort(key=lambda x: x[0])

		# Render character buttons
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
		self._set_status(f"Added to Favorites: {char}  ({name})", "#40c090")
		# Reset status after delay
		if self._reset_timer:
			self.root.after_cancel(self._reset_timer)
		self._reset_timer = self.root.after(2000, self._reset_status)
		# Refresh if viewing favorites
		if self.current_cat == "_favorites":
			self._render_favorites()

	def _remove_favorite(self, char, name):
		"""Remove a character from favorites."""
		if char in self._favorites:
			self._favorites.remove(char)
			self._save_config()
			self._set_status(f"Removed from Favorites: {char}  ({name})", "#e87040")
			if self._reset_timer:
				self.root.after_cancel(self._reset_timer)
			self._reset_timer = self.root.after(2000, self._reset_status)
			# Auto-deactivate delete mode when no favorites left
			if not self._favorites:
				self._delete_mode = False
			self._render_favorites()

	def _show_cat(self, key):
		# Clear search and restore placeholder
		if hasattr(self, "_search_entry"):
			self._set_search_placeholder()
		# Reset About footer state
		self._about_active = False
		if hasattr(self, "status_bar"):
			self.status_bar.itemconfig("about", fill=C["text_dim"])
		# Reset favorites tab + delete mode
		self._delete_mode = False
		self.status_default = self._default_status_text()
		self._status_default_color = None
		if hasattr(self, "_fav_canvas"):
			self._fav_accent.configure(bg="#1a3a36")
			self._fav_set_colors(self._fav_defaults[1], self._fav_defaults[0], star="\u2606")
		# Remove delete button if present
		if hasattr(self, "_del_btn_frame"):
			self._del_btn_frame.destroy()
		self.current_cat = key
		for k, lbl in self.tabs.items():
			if k == key:
				self._tab_borders[k].configure(bg=C["teal_dim"])
				lbl.configure(fg=C["teal"], bg=C["teal_dark"])
			else:
				self._tab_borders[k].configure(bg=C["tab_border"])
				default_bg, default_fg = self._tab_defaults[k]
				lbl.configure(fg=default_fg, bg=default_bg)
		self._fill_grid(CATEGORIES[key]["chars"])

	def _show_about(self):
		"""Show compact app info that fits in the 2-row grid area."""
		for w in self.char_frame.winfo_children():
			w.destroy()

		# Reset column configs from _fill_grid (minsize=36 clips labels)
		for col in range(COLUMNS):
			self.char_frame.columnconfigure(col, weight=0, minsize=0)
		self.char_frame.columnconfigure(0, weight=1)

		info = APP_INFO
		tk.Label(
			self.char_frame,
			text=f"{info['name']}  v{info['version']}  \u2014  {info['description']}",
			bg=C["bg"], fg=C["teal"], font=("Segoe UI", 12, "bold"), anchor="w",
		).grid(row=0, column=0, sticky="w", padx=4, pady=(4, 2))

		row1 = tk.Frame(self.char_frame, bg=C["bg"])
		row1.grid(row=1, column=0, sticky="w", padx=4, pady=(2, 4))

		tk.Label(
			row1, text="Author: ",
			bg=C["bg"], fg=C["text_dim"], font=("Segoe UI", 9),
		).pack(side="left")

		author = tk.Label(
			row1, text=info["author"],
			bg=C["bg"], fg=C["teal_dim"], font=("Segoe UI", 9, "underline"), cursor="hand2",
		)
		author.pack(side="left")
		author.bind("<Button-1>", lambda e: __import__("os").startfile("https://github.com/Lunyxium"))
		author.bind("<Enter>", lambda e: author.configure(fg=C["teal"]))
		author.bind("<Leave>", lambda e: author.configure(fg=C["teal_dim"]))

		tk.Label(
			row1,
			text=f"  \u00b7  License: {info['license']}  \u00b7  {info['tech']}",
			bg=C["bg"], fg=C["text_dim"], font=("Segoe UI", 9),
		).pack(side="left")

	def _toggle_about(self, _event=None):
		"""Toggle About view from footer link."""
		if self._about_active:
			# Return to last category
			self._about_active = False
			self.status_bar.itemconfig("about", fill=C["text_dim"])
			if self.current_cat == "_favorites":
				self._show_favorites()
			else:
				self._show_cat(self.current_cat)
		else:
			self._about_active = True
			self.status_bar.itemconfig("about", fill=C["gold_dim"])
			# Deselect all category tabs + favorites
			for k, lbl in self.tabs.items():
				self._tab_borders[k].configure(bg=C["tab_border"])
				default_bg, default_fg = self._tab_defaults[k]
				lbl.configure(fg=default_fg, bg=default_bg)
			if hasattr(self, "_fav_canvas"):
				self._fav_accent.configure(bg="#1a3a36")
				self._fav_set_colors(self._fav_defaults[1], self._fav_defaults[0], star="\u2606")
			if hasattr(self, "_del_btn_frame"):
				self._del_btn_frame.destroy()
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
		if self.auto_paste:
			return "Click a character to copy & paste into active window"
		return "Click a character to copy to clipboard"

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
		self._set_status(self.status_default)

	# --- Click ---

	def _click_char(self, btn, char, name):
		# Visual feedback
		btn.configure(bg=C["btn_click"])
		self.root.after(150, lambda: btn.configure(
			bg=C["btn_hover"] if self.hover_active else C["btn"]
		))

		# Copy to clipboard
		self.root.clipboard_clear()
		self.root.clipboard_append(char)
		self.root.update()

		# Update status
		self.status_default = f"Copied: {char}  ({name})"
		self._set_status(self.status_default, C["teal"])

		# Auto-paste if enabled
		if self.auto_paste:
			self.root.after(50, send_paste)

		# Reset status after delay
		if self._reset_timer:
			self.root.after_cancel(self._reset_timer)
		self._reset_timer = self.root.after(2000, self._reset_status)

	def _reset_status(self):
		self._reset_timer = None
		self.status_default = self._default_status_text()
		if not self.hover_active:
			self._set_status(self.status_default)

	# --- Auto-Paste Toggle + Tooltip ---

	def _toggle_paste(self, event=None):
		self.auto_paste = not self.auto_paste
		if self.auto_paste:
			self.titlebar.itemconfig(self._paste_id, text="AUTO \u25cf", fill=C["teal"])
		else:
			self.titlebar.itemconfig(self._paste_id, text="COPY \u25cb", fill=C["text_dim"])
		# Update idle status text to reflect new mode
		if not self._delete_mode:
			self.status_default = self._default_status_text()
			self._set_status(self.status_default)

	def _auto_hover_in(self):
		self.titlebar.itemconfig("auto", fill="#fff")
		tip = "AUTO: copies & pastes into active window" if self.auto_paste \
			else "COPY: copies to clipboard only"
		self._set_status(tip)

	def _auto_hover_out(self):
		fill = C["teal"] if self.auto_paste else C["text_dim"]
		self.titlebar.itemconfig("auto", fill=fill)
		self._set_status(self.status_default)

	# === Drag & Snap ===

	def _drag_start(self, event):
		# Don't initiate drag when clicking titlebar buttons
		items = self.titlebar.find_overlapping(
			event.x - 2, event.y - 2, event.x + 2, event.y + 2,
		)
		for item in items:
			tags = self.titlebar.gettags(item)
			if "close" in tags or "auto" in tags:
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
		self.root.update_idletasks()
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
