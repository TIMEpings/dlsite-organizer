"""Keep the native Windows window contract while Qt paints its client title bar.

Normal Qt window flags retain WS_CAPTION, WS_THICKFRAME, WS_SYSMENU and the
min/max boxes. NCCALCSIZE removes their visual frame only; Windows still owns
move/resize loops, double-click, system commands, system menu and Snap Layout.
No frameless flag, custom tiling, global event filter or external dependency.
"""

from __future__ import annotations

import ctypes
import math
import sys
from ctypes import wintypes
from enum import IntEnum
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication

if TYPE_CHECKING:
    from dlsite_organizer.ui.lightweight_window import LightweightWindow
    from dlsite_organizer.ui.main_window import MainWindow

    ChromeWindow = MainWindow | LightweightWindow


class Hit(IntEnum):
    CLIENT = 1
    CAPTION = 2
    SYSTEM_MENU = 3
    MAXIMIZE = 9
    LEFT = 10
    RIGHT = 11
    TOP = 12
    TOP_LEFT = 13
    TOP_RIGHT = 14
    BOTTOM = 15
    BOTTOM_LEFT = 16
    BOTTOM_RIGHT = 17


WM_NCCALCSIZE = 0x0083
WM_GETMINMAXINFO = 0x0024
WM_NCHITTEST = 0x0084
WM_NCMOUSEMOVE = 0x00A0
WM_NCLBUTTONDOWN = 0x00A1
WM_NCLBUTTONUP = 0x00A2
WM_NCRBUTTONUP = 0x00A5
WM_NCRBUTTONDOWN = 0x00A4
WM_SYSCOMMAND = 0x0112
SC_KEYMENU = 0xF100
WM_NCMOUSELEAVE = 0x02A2
WM_LBUTTONUP = 0x0202
WM_CAPTURECHANGED = 0x0215
TME_LEAVE = 0x0002
TME_NONCLIENT = 0x0010
MONITOR_DEFAULTTONEAREST = 2
SWP_FRAMECHANGED = 0x0020
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SM_CXSIZEFRAME = 32
SM_CXPADDEDBORDER = 92


class MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class TrackMouseEvent(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("hwndTrack", wintypes.HWND),
        ("dwHoverTime", wintypes.DWORD),
    ]


class MinMaxInfo(ctypes.Structure):
    _fields_ = [
        ("ptReserved", wintypes.POINT),
        ("ptMaxSize", wintypes.POINT),
        ("ptMaxPosition", wintypes.POINT),
        ("ptMinTrackSize", wintypes.POINT),
        ("ptMaxTrackSize", wintypes.POINT),
    ]


def native_minimum(width: int, height: int, ratio: float) -> tuple[int, int]:
    """Round physical tracking minima up so they never undershoot Qt's logical minimum."""
    return math.ceil(width * ratio), math.ceil(height * ratio)


def screen_point(lparam: int) -> tuple[int, int]:
    """GET_X/Y_LPARAM: signed physical screen coordinates, including left monitors."""
    return ctypes.c_short(lparam & 0xFFFF).value, ctypes.c_short((lparam >> 16) & 0xFFFF).value


def resize_hit(x: int, y: int, width: int, height: int, border: int) -> Hit | None:
    """Classify a physical window-relative point; right/bottom bounds are exclusive."""
    if not (0 <= x < width and 0 <= y < height):
        return None
    left, right = x < border, x >= width - border
    top, bottom = y < border, y >= height - border
    if top:
        return Hit.TOP_LEFT if left else Hit.TOP_RIGHT if right else Hit.TOP
    if bottom:
        return Hit.BOTTOM_LEFT if left else Hit.BOTTOM_RIGHT if right else Hit.BOTTOM
    return Hit.LEFT if left else Hit.RIGHT if right else None


def title_hit(
    point: QPoint,
    title: QRect,
    icon: QRect,
    minimize: QRect,
    maximize: QRect,
    close: QRect,
) -> Hit:
    """Qt logical client coordinates: client buttons must never become drag space."""
    if not title.contains(point):
        return Hit.CLIENT
    if maximize.contains(point):
        return Hit.MAXIMIZE
    if icon.contains(point):
        return Hit.SYSTEM_MENU
    if minimize.contains(point) or close.contains(point):
        return Hit.CLIENT
    return Hit.CAPTION


class WindowsChrome:
    """Per-window native-event adapter; loaded only for Qt's Windows platform."""

    @classmethod
    def create(cls, window: ChromeWindow) -> WindowsChrome | None:
        if sys.platform != "win32" or QApplication.platformName() != "windows":
            return None
        return cls(window)

    def __init__(self, window: ChromeWindow) -> None:
        self.window = window
        self._pressed = False
        self._attached = False
        # Explicit pointer-sized signatures prevent HWND truncation on 64-bit Windows.
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        signatures = {
            "GetWindowRect": ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            "ScreenToClient": ([wintypes.HWND, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
            "GetCursorPos": ([ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
            "GetDpiForWindow": ([wintypes.HWND], wintypes.UINT),
            "GetSystemMetricsForDpi": ([ctypes.c_int, wintypes.UINT], ctypes.c_int),
            "IsZoomed": ([wintypes.HWND], wintypes.BOOL),
            "MonitorFromWindow": ([wintypes.HWND, wintypes.DWORD], wintypes.HMONITOR),
            "GetMonitorInfoW": ([wintypes.HMONITOR, ctypes.POINTER(MonitorInfo)], wintypes.BOOL),
            "TrackMouseEvent": ([ctypes.POINTER(TrackMouseEvent)], wintypes.BOOL),
            "SetCapture": ([wintypes.HWND], wintypes.HWND),
            "ReleaseCapture": ([], wintypes.BOOL),
            "DefWindowProcW": (
                [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t],
                ctypes.c_ssize_t,
            ),
            "SetWindowPos": (
                [
                    wintypes.HWND,
                    wintypes.HWND,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_int,
                    wintypes.UINT,
                ],
                wintypes.BOOL,
            ),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes = args
            function.restype = result

    def attach(self) -> None:
        if self._attached:
            return
        self._attached = True
        # Recalculate the native frame after Qt has created the HWND.
        self.api.SetWindowPos(
            int(self.window.winId()),
            None,
            0,
            0,
            0,
            0,
            SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE,
        )

    def hit_test(self, hwnd: int, x: int, y: int) -> Hit:
        rect = wintypes.RECT()
        if not self.api.GetWindowRect(hwnd, ctypes.byref(rect)):
            return Hit.CLIENT
        if not self.api.IsZoomed(hwnd):
            dpi = self.api.GetDpiForWindow(hwnd) or 96
            border = max(
                round(6 * dpi / 96),
                self.api.GetSystemMetricsForDpi(SM_CXSIZEFRAME, dpi)
                + self.api.GetSystemMetricsForDpi(SM_CXPADDEDBORDER, dpi),
            )
            edge = resize_hit(
                x - rect.left, y - rect.top, rect.right - rect.left, rect.bottom - rect.top, border
            )
            if edge is not None:
                return edge
        client = wintypes.POINT(x, y)
        if not self.api.ScreenToClient(hwnd, ctypes.byref(client)):
            return Hit.CLIENT
        # Win32 screen/client points are physical; Qt child geometry is logical.
        # ScreenToClient avoids multiplying global Qt origins across mixed-DPI monitors.
        ratio = self.window.devicePixelRatioF()
        point = QPoint(int(client.x / ratio), int(client.y / ratio))
        bar = self.window.title_bar

        def bounds(widget) -> QRect:
            return QRect(widget.mapTo(self.window, QPoint()), widget.size())

        return title_hit(
            point,
            bounds(bar),
            bounds(bar.icon_button),
            bounds(bar.minimize_button),
            bounds(bar.maximize_button),
            bounds(bar.close_button),
        )

    def handle(self, message: int) -> tuple[bool, int]:
        msg = wintypes.MSG.from_address(message)
        hwnd = msg.hWnd
        if hwnd is None:
            return False, 0
        button = self.window.title_bar.maximize_button
        if msg.message == WM_GETMINMAXINFO:
            # Qt would add the now-hidden native frame margins to its client
            # minimum, causing restore at minimum height to grow the window.
            # Windows supplies the default monitor max bounds; replace only
            # tracking constraints with the actual full-client Qt dimensions.
            info = MinMaxInfo.from_address(msg.lParam)
            ratio = self.window.devicePixelRatioF()
            info.ptMinTrackSize.x, info.ptMinTrackSize.y = native_minimum(
                self.window.minimumWidth(), self.window.minimumHeight(), ratio
            )
            info.ptMaxTrackSize.x = min(
                info.ptMaxTrackSize.x, math.floor(self.window.maximumWidth() * ratio)
            )
            info.ptMaxTrackSize.y = min(
                info.ptMaxTrackSize.y, math.floor(self.window.maximumHeight() * ratio)
            )
            return True, 0
        if msg.message == WM_NCCALCSIZE:
            # RECT is the first field of NCCALCSIZE_PARAMS when wParam is true,
            # and the complete lParam otherwise. No other fields are modified.
            if self.api.IsZoomed(hwnd):
                info = MonitorInfo()
                info.cbSize = ctypes.sizeof(info)
                monitor = self.api.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
                if self.api.GetMonitorInfoW(monitor, ctypes.byref(info)):
                    rect = wintypes.RECT.from_address(msg.lParam)
                    rect.left, rect.top = info.rcWork.left, info.rcWork.top
                    rect.right, rect.bottom = info.rcWork.right, info.rcWork.bottom
            return True, 0
        if msg.message == WM_NCHITTEST:
            return True, int(self.hit_test(hwnd, *screen_point(msg.lParam)))
        if msg.wParam in (Hit.CAPTION, Hit.SYSTEM_MENU):
            if msg.message == WM_NCRBUTTONDOWN:
                # Prevent Qt's context-menu translation from capturing the
                # mouse and turning the following release into client input.
                return True, 0
            if msg.message == WM_NCRBUTTONUP:
                return True, self.api.DefWindowProcW(
                    hwnd, WM_SYSCOMMAND, SC_KEYMENU, ord(" ")
                )
        if msg.message == WM_NCMOUSEMOVE:
            button.set_native_hover(msg.wParam == Hit.MAXIMIZE)
            track = TrackMouseEvent(
                ctypes.sizeof(TrackMouseEvent), TME_LEAVE | TME_NONCLIENT, hwnd, 0
            )
            self.api.TrackMouseEvent(ctypes.byref(track))
            # Leave the message to Windows: the shell needs it to show Snap Layout.
        elif msg.message == WM_NCMOUSELEAVE:
            button.set_native_hover(False)
        elif msg.message == WM_NCLBUTTONDOWN and msg.wParam == Hit.MAXIMIZE:
            # HTMAXBUTTON receives non-client input, not Qt button events.
            # Capture guarantees release outside the button cancels activation.
            self._pressed = True
            button.setDown(True)
            self.api.SetCapture(hwnd)
            return True, 0
        elif msg.message in (WM_LBUTTONUP, WM_NCLBUTTONUP) and self._pressed:
            point = wintypes.POINT()
            activate = bool(self.api.GetCursorPos(ctypes.byref(point))) and (
                self.hit_test(hwnd, point.x, point.y) is Hit.MAXIMIZE
            )
            self._pressed = False
            button.setDown(False)
            self.api.ReleaseCapture()
            if activate:
                button.click()
            return True, 0
        elif msg.message == WM_CAPTURECHANGED:
            self._pressed = False
            button.setDown(False)
        return False, 0
