# GCS Shimeji — Pixel Desktop Companion

**GCS Shimeji (Gesture & Cross-Device System)** — кроссплатформенный виртуальный пиксельный питомец с единым авторитетным состоянием, который перемещается между **Windows** (прозрачное окно поверх рабочего стола) и **Android** (Jetpack Compose) по локальной сети без облачных API, платных сервисов и обязательного интернета.

---

## Архитектура проекта

```text
.
├── server/
│   ├── hub.py                  # GCS Local Hub (asyncio WebSocket, mDNS/Zeroconf, QR-код)
│   ├── avatar_state.py         # Единственный источник истины (owner, position, animation, state, handoff_id)
│   ├── handoff_manager.py      # Двухфазная атомарная передача, таймаут 5–8с, откат, идемпотентность
│   └── device_manager.py       # Реестр устройств, переподключения, выбор целевого устройства
├── windows/
│   ├── main.py                 # Точка входа Windows-клиента (CLI, запуск встроенного Hub + GUI)
│   ├── desktop_pet.py          # Координатор окна, физики, Hub-клиента и подтверждения жестами
│   ├── transparent_window.py   # PySide6 прозрачное окно без рамок, click-through QRegion, мультимониторность
│   ├── pet_engine.py           # Физика, плавное перетаскивание, границы мониторов, сохранение позиции
│   ├── animations.py           # Загрузка Sprite Sheet, строго nearest-neighbor масштабирование, 9 анимаций
│   ├── network_client.py       # WebSocket-клиент с автопереподключением и фоновый EmbeddedHubRunner
│   ├── camera.py               # Интерфейсы камер: PS3 Eye (non-depth), Webcam, Android stream, Synthetic
│   └── hand_detection.py       # 2D RGB распознавание руки, pinch, receive_pet, confirmation-only gate
├── android/                    # Полноценный Android-клиент (Kotlin + Jetpack Compose + OkHttp + NsdManager)
│   ├── build.gradle.kts
│   ├── settings.gradle.kts
│   └── app/
│       └── src/main/java/com/gcs/shimeji/
├── shared/
│   ├── protocol.json           # Спецификация WebSocket-протокола GCS-Shimeji-WS
│   ├── avatar_config.json      # Конфигурация спрайт-листа, анимаций и поведения питомца
│   └── config_loader.py        # Типизированные загрузчики конфигураций
├── assets/
│   ├── avatar/
│   │   ├── generate_placeholder_sprites.py
│   │   └── placeholder_spritesheet.png
│   └── sounds/
├── tests/
│   ├── test_avatar_state.py
│   ├── test_handoff.py
│   ├── test_network.py
│   ├── test_windows_pet.py
│   └── test_camera_gestures.py
├── requirements.txt
├── pyproject.toml
└── run.bat
```

---

## Быстрый запуск на Windows

### Вариант 1: Через `run.bat` (рекомендуется)

Убедитесь, что установлен **Python 3.11+**, затем запустите:

```bat
run.bat
```

Скрипт автоматически создаст `.venv`, установит зависимости из `requirements.txt` и запустит Windows Shimeji вместе с локальным сервером **GCS Local Hub** (порт `8765`, mDNS и QR-код для подключения Android).

### Вариант 2: Ручной запуск

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux / macOS:
source .venv/bin/activate

pip install -r requirements.txt
python -m windows.main
```

### Полезные флаги CLI (`windows/main.py`)

| Флаг | Описание |
|---|---|
| `--standalone` | Локальный режим Windows Shimeji без запуска/подключения к Hub |
| `--hub-url ws://IP:8765` | Подключиться к уже запущенному GCS Local Hub |
| `--port 8765` | Порт встроенного GCS Local Hub |
| `--scale 4` | Целочисленный масштаб пиксельной графики (nearest-neighbor) |
| `--config path/to/avatar_config.json` | Загрузить стороннего пиксельного персонажа |
| `--camera webcam \| ps3_eye` | Включить живое управление жестами и подтверждение handoff через камеру |
| `--camera-index 0` | Индекс камеры (если подключено несколько камер) |
| `--ps3-mode vga_60 \| qvga_120` | Режим захвата Sony PS3 Eye (2D RGB, не depth-камера) |
| `--no-camera-preview` | Запустить отслеживание жестов без открытия окна HUD-превью |
| `--hand-model path/to/hand_landmarker.task` | Локальная модель MediaPipe для 21-точечного нейросетевого трекинга руки |
| `--list-cameras` | Показать доступные индексы камер и выйти |

### Отдельный запуск сервера GCS Local Hub

```bash
python -m server.hub --host 0.0.0.0 --port 8765 --qr
```

Флаг `--qr` выведет в консоль QR-код формата `gcs-shimeji://pair?ws=ws://<LAN-IP>:8765`, который можно открыть на Android.

---

## Android Web-версия (внутри приложения и в браузере)

`GCSLocalHub` автоматически поднимает встроенный локальный HTTP-сервер (`server/web_server.py`, порт `8766` по умолчанию, **полностью оффлайн в локальной Wi-Fi сети**):
- Отдаёт адаптивный пиксельный Web-клиент (`web/index.html`, `web/style.css`, `web/app.js`), динамический конфиг `/api/config` и актуальный PNG-спрайт `/assets/avatar/spritesheet.png` прямо с Windows-ПК (рендер на `<canvas>` строго без сглаживания — nearest-neighbor).
- **Внутри Android-приложения (`ShimejiWebScreen.kt`)**: приложение по умолчанию открывает Web-версию на весь экран внутри нативного `WebView` с JS-мостом `GcsAndroidBridge` (стабильный `device_id`, нативная виброотдача при передаче питомца), автопоиском Hub по **mDNS** и сохранением адреса ПК (при желании можно в один тап переключиться на нативный Compose-экран кнопкой `Native`).
- **Без сборки APK**: достаточно открыть `http://<LAN-IP-ПК>:8766` (или отсканировать QR-код из окна `Show Pairing QR / URL` на Windows) в браузере смартфона.

## Сборка и запуск Android-приложения

1. Откройте папку `android/` в **Android Studio** (JDK 17, Android SDK 34).
2. Подключите Android-смартфон в той же локальной Wi-Fi сети, что и Windows-ПК.
3. Соберите и установите приложение (`Run 'app'` или `./gradlew :app:installDebug`).
4. Подключение к Hub:
   - автоматически через **mDNS/NSD** (подхватывает `web_port` и сразу открывает Web-версию внутри приложения),
   - **или** отсканируйте QR-код с Windows (`http://<IP>:8766` / `gcs-shimeji://pair?...`),
   - **или** введите IP ПК вручную (например `192.168.1.42`).

---

## Двухфазная передача персонажа (Handoff)

1. Перетащите питомца к внешнему краю экрана (на Windows или Android) — или выберите отправку в меню.
2. Клиент-источник отправляет `handoff_request` с ключом идемпотентности.
3. **GCS Local Hub** создаёт уникальный `handoff_id`, атомарно переводит состояние в `HANDOFF_PENDING`, временно скрывает питомца у источника (`handoff_pending`) и отправляет предложение принимающему устройству (`handoff_offer`).
4. Принимающее устройство подтверждает передачу касанием, кликом или жестом открытой ладони (`handoff_confirm`).
5. Hub атомарно меняет `owner` и рассылает `handoff_complete` + `state_sync`.
6. Если подтверждение не пришло за **6 секунд** (настраивается в пределах 5–8 с) или одно из устройств отключилось — передача отменяется (`handoff_cancel`), владелец и прежнее состояние питомца полностью восстанавливаются.

---

## Замена пиксельного персонажа

Внешний вид полностью отделён от логики приложения:
1. Подготовьте RGBA Sprite Sheet с 9 строками анимаций (`idle`, `walk`, `run`, `jump`, `sleep`, `grab`, `handoff`, `receive`, `landing`).
2. Укажите путь к спрайту и параметры кадров в `shared/avatar_config.json` (для Android — также скопируйте файл в `android/app/src/main/assets/`).
3. Масштабирование всегда выполняется через **nearest-neighbor** без сглаживания.

---

## Камера и реальное управление жестами

- Поддерживаются **PS3 Eye** (`PS3EyeCamera`, 640×480@60fps / 320×240@120fps, приоритет `DirectShow` на Windows, явно `is_depth_camera = False`), обычные веб-камеры (`OpenCVCamera`) и поток с Android-камеры (`AndroidCameraStreamAdapter`).
- **Два локальных оффлайн-трекера 2D RGB:**
  - `OpenCVHandDetector` — работает сразу из коробки без скачивания моделей (YCrCb + HSV + калибровка фона `Calibrate BG` + сглаживание координат).
  - `MediaPipeHandDetector` — опциональный 21-точечный нейросетевой трекинг (автоматически активируется, если установлен `mediapipe` и рядом лежит локальный файл `assets/models/hand_landmarker.task` или передан `--hand-model`).
- **Живое управление питомцем (`windows/gesture_controller.py`) и HUD-превью (`windows/camera_preview.py`):**
  - **Щипок (`PINCH`)** — хватает питомца и перетаскивает его по рабочему столу за движением руки (разжатие отпускает).
  - **Указательный палец (`POINT`)** — подзывает питомца (идёт/бежит к координате X руки).
  - **Открытая ладонь (`RECEIVE_PET`)** — при активном входящем `handoff_offer` подтверждает принятие питомца; в обычном режиме будит спящего питомца или вызывает прыжок при резком взмахе вверх.
  - **Сжатый кулак (`FIST`, удержание ~0.7 с)** — укладывает питомца спать.
- **Инвариант безопасности**: `HandoffGestureConfirmationController` и `AndroidHandoffGestureGate` могут **только подтвердить** уже ожидающий входящий `handoff_id` и никогда не инициируют передачу самостоятельно.

---

## Автоматические тесты

```bash
pytest -v
```

Покрытие тестами:
- `tests/test_windows_pet.py` — пиксельный спрайт-лист, проверка отсутствия сглаживания (nearest-neighbor), click-through маска, 9 анимаций, hot-swap персонажа, мультимониторные границы, перетаскивание, гравитация, сохранение позиции.
- `tests/test_avatar_state.py` — авторитетность `owner`, запрет изменений от не-владельца, атомарный `commit` и `rollback`.
- `tests/test_handoff.py` — двухфазная передача Android ↔ Windows, откат по таймауту, идемпотентность дубликатов, обрыв соединения во время handoff, защита от неверного получателя.
- `tests/test_network.py` — реальные WebSocket-соединения, синхронизация состояния, дедупликация `message_id`, переподключение, `WindowsHubClient` + `EmbeddedHubRunner`, генерация QR-кода.
- `tests/test_camera_gestures.py` — адаптеры PS3 Eye / webcam / Android (non-depth), 2D распознавание `open_palm` (`receive_pet`) и `pinch`, проверка запрета инициации handoff камерой.
