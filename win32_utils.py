"""Win32 API utilities for focus prevention, input simulation, and hotkeys."""

import ctypes
import threading
from ctypes import wintypes

# === Window Style Constants ===

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = -1

# === SendInput Constants ===

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_V = 0x56

# === Global Hotkey Constants ===

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
VK_G = 0x47
HOTKEY_ID = 1

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32


# === Structures (correct alignment for x64) ===

ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class MOUSEINPUT(ctypes.Structure):
	_fields_ = [
		("dx", ctypes.c_long),
		("dy", ctypes.c_long),
		("mouseData", ctypes.c_ulong),
		("dwFlags", ctypes.c_ulong),
		("time", ctypes.c_ulong),
		("dwExtraInfo", ULONG_PTR),
	]


class KEYBDINPUT(ctypes.Structure):
	_fields_ = [
		("wVk", ctypes.c_ushort),
		("wScan", ctypes.c_ushort),
		("dwFlags", ctypes.c_ulong),
		("time", ctypes.c_ulong),
		("dwExtraInfo", ULONG_PTR),
	]


class HARDWAREINPUT(ctypes.Structure):
	_fields_ = [
		("uMsg", ctypes.c_ulong),
		("wParamL", ctypes.c_ushort),
		("wParamH", ctypes.c_ushort),
	]


class INPUT(ctypes.Structure):
	class _INPUT(ctypes.Union):
		_fields_ = [
			("mi", MOUSEINPUT),
			("ki", KEYBDINPUT),
			("hi", HARDWAREINPUT),
		]

	_anonymous_ = ("_input",)
	_fields_ = [("type", ctypes.c_ulong), ("_input", _INPUT)]


class MSG(ctypes.Structure):
	_fields_ = [
		("hwnd", ctypes.c_void_p),
		("message", ctypes.c_uint),
		("wParam", ULONG_PTR),
		("lParam", ctypes.c_longlong),
		("time", ctypes.c_ulong),
		("pt_x", ctypes.c_long),
		("pt_y", ctypes.c_long),
	]


# === Functions ===

def enable_dpi_awareness():
	"""Enable system-level DPI awareness.

	Uses level 1 (system-aware), not 2 (per-monitor) — tkinter's font
	rendering breaks with per-monitor DPI, producing inconsistent sizes
	across monitors. System-aware gives consistent behavior on primary.
	"""
	try:
		ctypes.windll.shcore.SetProcessDpiAwareness(1)
	except Exception:
		pass


def get_system_dpi():
	"""Get the system DPI. Returns 96 for 100%, 120 for 125%, 144 for 150%, etc."""
	try:
		dc = user32.GetDC(None)
		dpi = gdi32.GetDeviceCaps(dc, 88)  # LOGPIXELSX
		user32.ReleaseDC(None, dc)
		return dpi if dpi > 0 else 96
	except Exception:
		return 96


def get_foreground_window_rect():
	"""Get the visible bounding rect and hwnd of the current foreground window.

	Uses DwmGetWindowAttribute to get the actual visible frame (excluding
	the invisible drop shadow that Windows 10/11 adds to windows).
	Returns (left, top, right, bottom, hwnd) or None on failure.
	"""
	try:
		hwnd = user32.GetForegroundWindow()
		if not hwnd:
			return None
		rect = wintypes.RECT()
		# DWMWA_EXTENDED_FRAME_BOUNDS = 9 — gives visible frame without shadow
		hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
			hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect),
		)
		if hr != 0:
			# Fallback to regular GetWindowRect
			user32.GetWindowRect(hwnd, ctypes.byref(rect))
		return rect.left, rect.top, rect.right, rect.bottom, hwnd
	except Exception:
		return None


def set_no_activate(root):
	"""Apply WS_EX_NOACTIVATE + WS_EX_TOOLWINDOW to prevent focus stealing."""
	root.update_idletasks()
	hwnd = user32.GetParent(root.winfo_id())
	style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
	style |= WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
	user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
	user32.SetWindowPos(
		hwnd, HWND_TOPMOST, 0, 0, 0, 0,
		SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
	)
	return hwnd


def send_paste():
	"""Simulate Ctrl+V via SendInput to paste into the foreground window."""
	inputs = (INPUT * 4)()
	for i in range(4):
		inputs[i].type = INPUT_KEYBOARD

	inputs[0].ki.wVk = VK_CONTROL           # Ctrl down
	inputs[1].ki.wVk = VK_V                  # V down
	inputs[2].ki.wVk = VK_V                  # V up
	inputs[2].ki.dwFlags = KEYEVENTF_KEYUP
	inputs[3].ki.wVk = VK_CONTROL            # Ctrl up
	inputs[3].ki.dwFlags = KEYEVENTF_KEYUP

	user32.SendInput(4, ctypes.byref(inputs), ctypes.sizeof(INPUT))


def force_foreground(hwnd):
	"""Temporarily force the window to foreground so it receives keyboard input."""
	user32.SetForegroundWindow(hwnd)


def get_work_area():
	"""Get the usable screen area (excluding taskbar).

	Returns (left, top, right, bottom) of the primary monitor work area.
	"""
	rect = wintypes.RECT()
	user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
	return rect.left, rect.top, rect.right, rect.bottom


# === Global Hotkey (thread-based) ===

_hotkey_event = threading.Event()
_hotkey_thread = None


def _hotkey_thread_func():
	"""Dedicated thread with its own message loop for hotkey detection."""
	# Force message queue creation
	msg = MSG()
	user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)

	ok = user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_ALT, VK_G)
	if not ok:
		return

	while True:
		ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
		if ret <= 0:
			break
		if msg.message == WM_HOTKEY:
			_hotkey_event.set()


def start_hotkey_listener():
	"""Start the global hotkey listener thread. Returns True if started."""
	global _hotkey_thread
	_hotkey_thread = threading.Thread(target=_hotkey_thread_func, daemon=True)
	_hotkey_thread.start()
	return True


def check_hotkey_pressed():
	"""Check if the global hotkey was pressed (non-blocking)."""
	if _hotkey_event.is_set():
		_hotkey_event.clear()
		return True
	return False


def stop_hotkey_listener():
	"""Post quit message to the hotkey thread."""
	if _hotkey_thread and _hotkey_thread.is_alive():
		# PostThreadMessage to break GetMessageW loop
		tid = _hotkey_thread.ident
		if tid:
			user32.PostThreadMessageW(tid, 0x0012, 0, 0)  # WM_QUIT
