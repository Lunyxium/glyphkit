"""Win32 API utilities for focus prevention and input simulation."""

import ctypes
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

user32 = ctypes.windll.user32


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


# === Functions ===

def enable_dpi_awareness():
	"""Enable per-monitor DPI awareness for crisp rendering."""
	try:
		ctypes.windll.shcore.SetProcessDpiAwareness(1)
	except Exception:
		pass


def set_no_activate(root):
	"""Apply WS_EX_NOACTIVATE + WS_EX_TOOLWINDOW to prevent focus stealing.

	This mimics how the Windows On-Screen Keyboard works:
	the window receives mouse events but never becomes the foreground window,
	so the previously focused application keeps receiving keystrokes.
	"""
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
	# SPI_GETWORKAREA = 0x0030
	user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
	return rect.left, rect.top, rect.right, rect.bottom
