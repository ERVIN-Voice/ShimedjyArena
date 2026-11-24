"""PySide6 desktop control panel & system tray for Shimeji Browser Server (.exe)."""

from __future__ import annotations

import io
import logging
import webbrowser
from typing import Any

import qrcode
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from browser_pet import APP_NAME, DEFAULT_PORT
from browser_pet.server import ServerThread

logger = logging.getLogger("browser_pet.gui")


def _make_qr_pixmap(url: str, size: int = 164) -> QPixmap:
    qr = qrcode.QRCode(border=2, box_size=5)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0f172a", back_color="#ffffff")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    pm = QPixmap()
    pm.loadFromData(buf.getvalue(), "PNG")
    return pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)


def _make_app_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    # Cute cyan pixel pet icon with neon portal ring
    p.fillRect(4, 4, 56, 56, QColor("#101a30"))
    p.setPen(QColor("#4cc9f0"))
    p.drawRect(4, 4, 55, 55)
    p.fillRect(16, 16, 32, 32, QColor("#56c6e0"))
    p.fillRect(20, 20, 24, 8, QColor("#92e8f8"))
    p.fillRect(22, 26, 6, 8, QColor("#141824"))
    p.fillRect(36, 26, 6, 8, QColor("#141824"))
    p.fillRect(24, 28, 2, 2, QColor("#ffffff"))
    p.fillRect(38, 28, 2, 2, QColor("#ffffff"))
    p.end()
    return QIcon(pm)


class ServerControlWindow(QMainWindow):
    """Main desktop control panel for the standalone Shimeji Browser Server (.exe)."""

    state_updated = Signal(dict)

    def __init__(self, *, host: str = "0.0.0.0", port: int = DEFAULT_PORT, open_browser: bool = True) -> None:
        super().__init__()
        self._host = host
        self._requested_port = port
        self._tray_quitting = False
        self._primary_url = f"http://127.0.0.1:{port}"
        self._all_urls: list[str] = [self._primary_url]

        self.setWindowTitle(f"{APP_NAME} — Браузерный Чубрик между ПК")
        self.setMinimumSize(640, 490)
        self.resize(700, 530)
        icon = _make_app_icon()
        self.setWindowIcon(icon)

        self._apply_dark_style()
        self._build_ui()
        self._setup_tray(icon)

        self.state_updated.connect(self._on_state_snapshot)

        # Start embedded single-port HTTP + WebSocket server (auto-fallback port if busy)
        self._server_thread: ServerThread | None = None
        bound_port = self._start_server_with_fallback(port)
        self._primary_url = self._server_thread.server.primary_url  # type: ignore[union-attr]
        self._all_urls = self._server_thread.server.share_urls  # type: ignore[union-attr]
        self._refresh_urls_ui(bound_port)

        if open_browser:
            webbrowser.open(f"http://127.0.0.1:{bound_port}")

    def _start_server_with_fallback(self, preferred_port: int) -> int:
        candidates = [preferred_port, preferred_port + 1, preferred_port + 2, 0]
        last_err: BaseException | None = None
        for p in candidates:
            st = ServerThread(
                host=self._host,
                port=p,
                on_state_change=lambda snap: self.state_updated.emit(snap),
            )
            try:
                bound = st.start(timeout=5.0)
                self._server_thread = st
                return bound
            except BaseException as exc:
                last_err = exc
                st.stop()
        raise RuntimeError(f"Не удалось запустить сервер на порту {preferred_port}: {last_err}")

    def _apply_dark_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background-color: #0e1628;
                color: #eaf3ff;
                font-family: "Segoe UI", sans-serif;
                font-size: 13px;
            }
            QFrame#card {
                background-color: #16223b;
                border: 1px solid #2b426b;
                border-radius: 12px;
            }
            QPushButton {
                background-color: #1f3358;
                border: 1px solid #395c94;
                border-radius: 8px;
                padding: 8px 14px;
                font-weight: 600;
                color: #eaf3ff;
            }
            QPushButton:hover {
                background-color: #294678;
                border-color: #4cc9f0;
            }
            QPushButton#primaryBtn {
                background-color: #299ed8;
                border-color: #4cc9f0;
                color: #061322;
            }
            QPushButton#primaryBtn:hover {
                background-color: #4cc9f0;
            }
            QListWidget {
                background-color: #10192d;
                border: 1px solid #273c63;
                border-radius: 8px;
                padding: 4px;
            }
            QListWidget::item {
                padding: 6px 8px;
                border-bottom: 1px solid #1b2b4a;
            }
            """
        )

    def _build_ui(self) -> None:
        root = QWidget(self)
        self.setCentralWidget(root)
        main_lay = QVBoxLayout(root)
        main_lay.setContentsMargins(16, 16, 16, 16)
        main_lay.setSpacing(12)

        # Header
        header_lay = QHBoxLayout()
        title = QLabel("👾 Shimeji Browser Server")
        title.setStyleSheet("font-size: 20px; font-weight: 700; color: #72e6ff;")
        self._status_lbl = QLabel("🟢 Запуск...")
        self._status_lbl.setStyleSheet("font-size: 13px; font-weight: 600; color: #54e895;")
        header_lay.addWidget(title)
        header_lay.addStretch(1)
        header_lay.addWidget(self._status_lbl)
        main_lay.addLayout(header_lay)

        # Connection card (URL + QR)
        conn_card = QFrame()
        conn_card.setObjectName("card")
        conn_lay = QHBoxLayout(conn_card)
        conn_lay.setContentsMargins(14, 14, 14, 14)
        conn_lay.setSpacing(16)

        left_col = QVBoxLayout()
        left_col.setSpacing(8)
        hint = QLabel(
            "Откройте эту ссылку в браузере на <b>этом ПК</b> и на <b>любом другом ПК / телефоне</b> "
            "в той же Wi-Fi / LAN сети — и кидайте Чубрика прямо за край вкладки!"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #b7cae6;")
        left_col.addWidget(hint)

        self._url_lbl = QLabel("http://127.0.0.1:8787")
        self._url_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._url_lbl.setStyleSheet(
            "font-size: 17px; font-weight: 700; color: #ffd166; "
            "background: #0d1424; padding: 9px 12px; border-radius: 8px; border: 1px solid #355282;"
        )
        left_col.addWidget(self._url_lbl)

        self._extra_urls_lbl = QLabel("")
        self._extra_urls_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._extra_urls_lbl.setStyleSheet("font-size: 11px; color: #8ea8cc;")
        self._extra_urls_lbl.setWordWrap(True)
        left_col.addWidget(self._extra_urls_lbl)

        btn_row = QHBoxLayout()
        open_btn = QPushButton("🌐 Открыть вкладку")
        open_btn.setObjectName("primaryBtn")
        open_btn.clicked.connect(self._open_local_tab)

        copy_btn = QPushButton("📋 Копировать ссылку")
        copy_btn.clicked.connect(self._copy_primary_url)

        spawn_btn = QPushButton("➕ Ещё Чубрик")
        spawn_btn.clicked.connect(self._spawn_pet)

        btn_row.addWidget(open_btn)
        btn_row.addWidget(copy_btn)
        btn_row.addWidget(spawn_btn)
        left_col.addLayout(btn_row)
        left_col.addStretch(1)

        # QR side
        qr_col = QVBoxLayout()
        qr_col.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_lbl = QLabel()
        self._qr_lbl.setFixedSize(164, 164)
        self._qr_lbl.setStyleSheet("background: #ffffff; border-radius: 10px; padding: 4px;")
        self._qr_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_caption = QLabel("QR для телефона / ПК")
        qr_caption.setStyleSheet("font-size: 11px; color: #95afcf;")
        qr_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_col.addWidget(self._qr_lbl)
        qr_col.addWidget(qr_caption)

        conn_lay.addLayout(left_col, 1)
        conn_lay.addLayout(qr_col, 0)
        main_lay.addWidget(conn_card)

        # Live tabs & pets status
        live_card = QFrame()
        live_card.setObjectName("card")
        live_lay = QVBoxLayout(live_card)
        live_lay.setContentsMargins(14, 12, 14, 12)
        live_lay.setSpacing(8)

        stats_row = QHBoxLayout()
        self._stats_lbl = QLabel("🖥️ Подключено вкладок: 0   |   👾 Чубриков: 0   |   🚀 Перебросов: 0")
        self._stats_lbl.setStyleSheet("font-weight: 600; color: #cde1fa;")
        help_btn = QPushButton("🔥 Не открывается на 2-м ПК?")
        help_btn.clicked.connect(self._show_firewall_help)
        stats_row.addWidget(self._stats_lbl)
        stats_row.addStretch(1)
        stats_row.addWidget(help_btn)
        live_lay.addLayout(stats_row)

        self._tabs_list = QListWidget()
        live_lay.addWidget(self._tabs_list, 1)
        main_lay.addWidget(live_card, 1)

    def _setup_tray(self, icon: QIcon) -> None:
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip(f"{APP_NAME} — Браузерный Чубрик")
        menu = QMenu()
        act_show = QAction("Показать панель сервера", self)
        act_show.triggered.connect(self._restore_from_tray)
        act_open = QAction("🌐 Открыть вкладку с Чубриком", self)
        act_open.triggered.connect(self._open_local_tab)
        act_copy = QAction("📋 Копировать LAN-ссылку", self)
        act_copy.triggered.connect(self._copy_primary_url)
        act_quit = QAction("Выход (остановить сервер)", self)
        act_quit.triggered.connect(self._quit_app)

        menu.addAction(act_show)
        menu.addAction(act_open)
        menu.addAction(act_copy)
        menu.addSeparator()
        menu.addAction(act_quit)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._restore_from_tray()

    def _restore_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _refresh_urls_ui(self, port: int) -> None:
        self._status_lbl.setText(f"🟢 Работает · порт {port}")
        self._url_lbl.setText(self._primary_url)
        if len(self._all_urls) > 1:
            self._extra_urls_lbl.setText("Все адреса: " + "  |  ".join(self._all_urls))
        else:
            self._extra_urls_lbl.setText("Локальный адрес (подключите Wi-Fi/LAN для доступа с других ПК).")
        self._qr_lbl.setPixmap(_make_qr_pixmap(self._primary_url, 154))

    def _on_state_snapshot(self, snap: dict[str, Any]) -> None:
        tabs = snap.get("tabs", [])
        pets = snap.get("pets", [])
        throws = snap.get("total_throws", 0)
        self._stats_lbl.setText(
            f"🖥️ Подключено вкладок: {len(tabs)}   |   👾 Чубриков: {len(pets)}   |   🚀 Перебросов: {throws}"
        )
        self._tabs_list.clear()
        if not tabs:
            self._tabs_list.addItem(
                QListWidgetItem("Пока нет открытых вкладок — нажмите «🌐 Открыть вкладку» или откройте ссылку на другом ПК.")
            )
            return

        pets_by_tab: dict[str, list[str]] = {}
        for p in pets:
            owner = p.get("owner_tab_id")
            if owner:
                pets_by_tab.setdefault(owner, []).append(str(p.get("name", "Чубрик")))

        for idx, t in enumerate(tabs, start=1):
            tid = t.get("tab_id", "")
            label = t.get("label", tid)
            ip = t.get("remote_ip", "")
            who = pets_by_tab.get(tid, [])
            pet_str = ("  —  👾 Здесь: " + ", ".join(who)) if who else "  —  (портал готов)"
            item = QListWidgetItem(f"{idx}. 🖥️ {label}  [{ip or 'local'}]{pet_str}")
            self._tabs_list.addItem(item)

    def _open_local_tab(self) -> None:
        if self._server_thread:
            webbrowser.open(f"http://127.0.0.1:{self._server_thread.server.bound_port}")

    def _copy_primary_url(self) -> None:
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(self._primary_url)
        self._status_lbl.setText("📋 Ссылка скопирована!")

    def _spawn_pet(self) -> None:
        if self._server_thread:
            self._server_thread.spawn_pet()

    def _show_firewall_help(self) -> None:
        port = self._server_thread.server.bound_port if self._server_thread else DEFAULT_PORT
        QMessageBox.information(
            self,
            "Подключение с другого ПК",
            f"1) Убедитесь, что оба ПК подключены к одной сети Wi-Fi / роутеру (или Radmin VPN / ZeroTier / Tailscale).\n\n"
            f"2) При первом запуске Windows Defender спросит о доступе в сеть — нажмите «Разрешить» (для частных сетей).\n\n"
            f"3) Если всё равно не открывается, разрешите входящий TCP порт {port} в Брандмауэре Windows "
            f"(или откройте PowerShell от администратора и выполните):\n\n"
            f"New-NetFirewallRule -DisplayName \"Shimeji Browser Server\" -Direction Inbound -Protocol TCP -LocalPort {port} -Action Allow",
        )

    def closeEvent(self, event: Any) -> None:
        if not self._tray_quitting and self._tray.isVisible():
            event.ignore()
            self.hide()
            self._tray.showMessage(
                APP_NAME,
                "Сервер продолжает работать в системном трее. ПКМ по иконке → Выход, чтобы остановить.",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )
            return
        self._shutdown_server()
        event.accept()

    def _shutdown_server(self) -> None:
        if self._server_thread is not None:
            self._server_thread.stop()
            self._server_thread = None

    def _quit_app(self) -> None:
        self._tray_quitting = True
        self._shutdown_server()
        self._tray.hide()
        QApplication.quit()
