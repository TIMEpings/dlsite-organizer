# Full-mode Windows title bar

Phase 2.1A replaces the full-mode native title-bar drawing with a 40-logical-pixel
Qt title bar. The persistent sidebar icon and two-line brand label are removed.
Pages, the About mascot, and the Lightweight window are unchanged.

## Native contract and ownership

`MainWindow` retains ordinary Qt window flags (observed `0x0800f001` on Qt 6.11.2),
including title, system menu, minimize, maximize and close hints. It does **not**
use `FramelessWindowHint`. The HWND retains `WS_CAPTION`, `WS_THICKFRAME`,
`WS_SYSMENU`, `WS_MINIMIZEBOX` and `WS_MAXIMIZEBOX`.

`TitleBar` paints identity/caption controls and emits requests. MainWindow owns
composition and connects close to `QWidget.close()`, preserving its existing
`closeEvent` and installed application lifecycle. Caption state follows
`WindowStateChange`, including changes initiated by Windows.

`ui/windows/native_chrome.py` is the per-window adapter:

- `WM_NCCALCSIZE` removes native frame drawing. When maximized, its client RECT
  uses the nearest monitor's `rcWork`, preserving the taskbar/reserved work area.
- `WM_NCHITTEST` identifies resize edges/corners, safe caption space, the system
  menu icon, and the maximize region. Minimize/close remain Qt client controls.
- `HTCAPTION` leaves move loops and double-click behavior to Windows;
  `HTSYSMENU` leaves icon-menu behavior to Windows.
  Caption/icon non-client right input bypasses Qt's context-menu capture;
  release invokes `DefWindowProcW` with the same `SC_KEYMENU` Space command
  used by Alt+Space. This shows the existing native menu, not a Qt replacement.
- `HTMAXBUTTON` and unconsumed `WM_NCMOUSEMOVE` allow the Windows Snap flyout.
  Non-client hover/leave messages update the painted button. Mouse capture
  bridges a maximize press/release to its Qt action; release outside or capture
  loss cancels the press.
- `SetWindowPos(SWP_FRAMECHANGED)` recalculates the frame once, before final
  client sizing. `WM_GETMINMAXINFO` translates Qt's tracking constraints to
  physical full-client dimensions without adding the removed frame. This hook
  is necessary: without it, restore at the 826-pixel minimum grew to 841 pixels.
  Windows supplies the default monitor maximum bounds. System commands remain
  native; no DWM API is needed.

The implementation follows Microsoft's [custom-frame guidance](https://learn.microsoft.com/en-us/windows/win32/dwm/customframe)
and [Snap Layout guidance](https://learn.microsoft.com/en-us/windows/apps/desktop/modernize/apply-snap-layout-menu).

Native screen/client coordinates are physical pixels. `ScreenToClient` is applied
before dividing by the window DPR to compare Qt logical child bounds. Resize
width uses `GetDpiForWindow` and `GetSystemMetricsForDpi`, with a six-logical-pixel
floor. Signed LPARAM extraction supports monitors left/above the primary.

Non-Windows and non-`windows` Qt platforms retain their native frame and hide the
custom title bar. They do not load `user32`. No external dependency is added.

## Presentation and geometry

The title bar uses existing surface, border, text, focus, error and blocked
tokens. Caption buttons are 46×40 logical pixels with painted line glyphs,
neutral hover/pressed states and red close hover. The existing icon is drawn at
20 logical pixels through `QIcon` for DPR-aware rendering.

Caption buttons use ClickFocus: they stay out of the page Tab sequence, expose
Chinese accessible names/tooltips, and support Space when focused, with a focus
outline. Native maximize hover uses the native event bridge. Page focus rings
and navigation keyboard behavior remain intact.

At 125%, settled `minimumSizeHint` is 806×826 and actual minimum is 960×826.
Default client size is 1160×826, increased only to accommodate the 40-pixel bar
above the existing 786-pixel page minimum. A deferred measurement after child
show/layout events prevents the initial, smaller unfinalized hint from becoming
the effective minimum. Initial HWND frame recalculation avoids adding old native
caption margins to the requested width.

## Windows UAT record — 2026-10-02

Environment: Windows 11 build 26300, PySide6/Qt 6.11.2, one 2560×1440 monitor,
Windows scaling 125%, Qt DPR 1.25. Real GUI input was exercised with an ignored
offline UAT host using real MainWindow, LightweightWindow and ApplicationLifecycle.
No DLsite requests, Explorer registration changes or native helper rebuilds.

| Behavior | Result / evidence |
| --- | --- |
| Normal / empty title-area drag | PASS; physical window origin moved by the input delta |
| Caption controls do not drag | PASS; minimize-to-maximize drag leaves geometry unchanged |
| Maximize press moved outside | PASS; no activation or movement |
| Double-click maximize / restore | PASS / PASS; Qt state and glyph follow both |
| Minimize / reactivate | PASS / PASS |
| Maximize / restore buttons | PASS / PASS |
| Left / right resize | PASS / PASS; width 1160→1140→1120 |
| Top / bottom resize | PASS / PASS; height 1000→980→960 |
| Top-left / top-right resize | PASS / PASS; 1120×960→1100×940→1080×920 |
| Bottom-left / bottom-right resize | PASS / PASS; →1060×900→1040×880 |
| Resize minimum / restore geometry | PASS; stops at 960×826; 1160×826→maximize→1160×826 |
| Alt+Space system menu | PASS; Restore, Move, Size, Minimize, Maximize, Close present |
| Icon system menu | PASS; left and right click open the native menu |
| Empty caption right-click / menu Close | PASS / PASS; native menu, then ordinary lifecycle exit |
| Work-area maximize | PASS; client is (0,0), 2048×1104 logical, matching `rcWork` 2560×1380 physical |
| Windows Snap Layout on maximize hover | PASS; actual Windows flyout appeared |
| Close normally | PASS; exit 0, server stop → database dispose → lock release |
| Close during local lookup worker | PASS; existing busy dialog; window/worker remain alive, no shutdown events |
| Close after worker finishes | PASS; ordinary lifecycle shutdown |
| Full ↔ Lightweight mode | PASS; full HWND reused, Lightweight retains native title bar |
| 125% alignment | PASS; compact identity, centered glyphs, no duplicated sidebar brand or shell gap |
| Multi-monitor / mixed DPI | NOT TESTED; only one monitor available |
| Actual 100% / 150% / 200% | NOT TESTED; no system scaling changes made |

The maximized HWND itself includes Windows' invisible sizing margins; client
bounds match the work area. This is not a client overlap of the taskbar.
Screenshots were inspected in the real GUI and are not tracked assets.

Automated checks: 84 targeted tests, 925 full-suite tests, zero skipped; Ruff,
Pyright, compileall, pip check and diff whitespace checks passed. Pure resize
classification covers 100%, 125%, 150%, 200% and signed screen origins. Offscreen
tests establish action dispatch, lifecycle/busy boundaries, routing, glyph state,
keyboard activation, construction and platform fallback, not native Snap behavior.

## Future validation

Retest the native contract when upgrading Qt or Windows. Windows 10 has no
Windows 11 Snap flyout. Actual mixed-DPI monitor transitions, other Windows
builds, taskbar configurations including auto-hide, and 200% final UAT remain
validation risks. Reuse in Lightweight is possible at the widget level, but the
native adapter and its smaller window constraints need a separate UAT pass.

Next phase: **Phase 2.1B — Visual Foundation Correction**. Scrollbars, Settings
groups, History, About updates, content widths and other page polish are deferred.
