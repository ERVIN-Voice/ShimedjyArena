package com.gcs.shimeji.handcam

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.content.res.Configuration
import android.graphics.PixelFormat
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Build
import android.os.SystemClock
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.provider.Settings
import android.util.DisplayMetrics
import android.view.Choreographer
import android.view.Gravity
import android.view.Surface
import android.view.View
import android.view.WindowInsets
import android.view.WindowManager
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleService
import java.util.concurrent.atomic.AtomicReference
import kotlin.math.hypot
import kotlin.math.roundToInt

class ShimejiHandCamService : LifecycleService(), SensorEventListener, Choreographer.FrameCallback {

    private lateinit var windowManager: WindowManager
    private lateinit var sensorManager: SensorManager
    private lateinit var prefs: HandCamPreferences
    private lateinit var settings: HandCamSettings
    private lateinit var deviceId: String
    private lateinit var spriteSheet: PixelSpriteSheet
    private lateinit var physics: EmotionalPetPhysics
    private lateinit var petView: EmotionalPetOverlayView
    private lateinit var layoutParams: WindowManager.LayoutParams

    private var cameraTracker: CameraHandTracker? = null
    private val latestHandFrame = AtomicReference<HandGestureFrame?>(null)

    // Unified PC Hub Sync + Handoff overlay state
    private var hubClient: HubSyncClient? = null
    private var offerOverlayView: HandoffOfferOverlayView? = null
    private var offerAttached = false
    private var pendingIncomingOffer: HubEvent.HandoffOffer? = null
    private var isLocalOwner: Boolean = true
    private var isHandoffPendingOutgoing: Boolean = false
    private var hasOnlinePcPeer: Boolean = false
    private var lastHubStateSentMs: Long = 0L

    private var usingAccelerometerFallback = false
    private val filteredGravity = floatArrayOf(0f, 9.81f, 0f)

    private var choreographerRunning = false
    private var lastFrameNanos = 0L
    private var topInsetPx = 0
    private var bottomInsetPx = 0
    private var lastDisplayRotation = Surface.ROTATION_0
    private var viewAttached = false

    override fun onCreate() {
        super.onCreate()
        windowManager = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        sensorManager = getSystemService(Context.SENSOR_SERVICE) as SensorManager
        prefs = HandCamPreferences(this)
        settings = prefs.load()
        deviceId = prefs.getOrCreateDeviceId()

        startForegroundSafely()

        if (!Settings.canDrawOverlays(this)) {
            stopSelf()
            return
        }

        spriteSheet = PixelSpriteSheet.loadFromAssets(this)
        val bounds = measureUsableBounds()
        lastDisplayRotation = currentDisplayRotation()

        val petPx = computePetPixelSize(settings.scale)
        physics = EmotionalPetPhysics(
            screenWidth = bounds.width.toFloat(),
            screenHeight = bounds.height.toFloat(),
            petSizePx = petPx.toFloat(),
            onBonk = { impactSpeed -> vibrateBonk(impactSpeed) },
            onEdgeHandoff = { edge, normY -> triggerOutgoingHandoff(edge, normY) },
        )
        applySettingsToPhysics()

        petView = EmotionalPetOverlayView(
            context = this,
            spriteSheet = spriteSheet,
            physics = physics,
            onTouchPositionChanged = { syncWindowPosition() },
        )
        petView.updateSpritePixelSize(petPx)
        petView.showGestureBadge = settings.showGestureBadge

        val windowSize = petPx + petView.paddingPx * 2
        val windowType = overlayWindowType()

        layoutParams = WindowManager.LayoutParams(
            windowSize,
            windowSize,
            windowType,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = (physics.x - petView.paddingPx).roundToInt()
            y = (physics.y + topInsetPx - petView.paddingPx).roundToInt()
        }

        windowManager.addView(petView, layoutParams)
        viewAttached = true

        cameraTracker = CameraHandTracker(
            context = this,
            lifecycleOwner = this,
            onGestureFrame = { frame ->
                latestHandFrame.set(frame)
                HandCamRuntimeState.setGestureLabel(frame.gesture.russianLabel)
            },
            onStatusChanged = { status ->
                HandCamRuntimeState.setCameraStatus(status)
            },
        )

        hubClient = HubSyncClient(
            deviceId = deviceId,
            screenWidth = bounds.width,
            screenHeight = bounds.height,
            onEvent = { event -> handleHubEvent(event) },
            onConnectionStateChanged = { connected, statusLabel ->
                HandCamRuntimeState.setHubState(connected, statusLabel)
                if (!connected) {
                    hasOnlinePcPeer = false
                    HandCamRuntimeState.setHasPcPeer(false)
                    dismissIncomingOfferOverlay()
                    if (!settings.pcSyncEnabled) {
                        setPetOwnedLocally(true)
                    }
                    updateEdgeHandoffEligibility()
                }
            },
        )

        registerGravitySensor()
        syncCameraState()
        syncHubConnection()
        startFrameLoop()
        HandCamRuntimeState.setRunning(true)
        HandCamRuntimeState.setPetOwnedLocally(true)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        when (intent?.action) {
            ACTION_STOP -> {
                stopSelf()
                return START_NOT_STICKY
            }

            ACTION_RELOAD_SETTINGS -> {
                if (viewAttached) {
                    settings = prefs.load()
                    applySettingsToPhysics()
                    petView.showGestureBadge = settings.showGestureBadge
                    val newPetPx = computePetPixelSize(settings.scale)
                    physics.updatePetSize(newPetPx.toFloat())
                    petView.updateSpritePixelSize(newPetPx)
                    val newWinSize = newPetPx + petView.paddingPx * 2
                    layoutParams.width = newWinSize
                    layoutParams.height = newWinSize
                    syncWindowPosition()
                    syncCameraState()
                    syncHubConnection()
                }
            }

            ACTION_SEND_TO_PC -> {
                if (viewAttached) {
                    triggerOutgoingHandoff(edge = "right", normalizedY = 0.5f)
                }
            }

            ACTION_TEST_FALL -> if (viewAttached && isLocalOwner) physics.triggerFunnyFall(if (physics.facingRight) 1f else -1f)
            ACTION_EMOTE_LOVE -> if (viewAttached && isLocalOwner) physics.petWithLove()
            ACTION_EMOTE_HAPPY -> if (viewAttached && isLocalOwner) physics.triggerHappyJump()
            ACTION_EMOTE_ANGRY -> if (viewAttached && isLocalOwner) physics.makeAngry()
            ACTION_EMOTE_SLEEP -> if (viewAttached && isLocalOwner) physics.putToSleep()
        }
        return START_STICKY
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        if (!viewAttached) return
        val newRotation = currentDisplayRotation()
        val rotated = newRotation != lastDisplayRotation
        lastDisplayRotation = newRotation
        val bounds = measureUsableBounds()
        physics.updateScreenBounds(
            newWidth = bounds.width.toFloat(),
            newHeight = bounds.height.toFloat(),
            triggerRotationTumble = rotated && isLocalOwner,
        )
        syncWindowPosition()
    }

    override fun onDestroy() {
        HandCamRuntimeState.setRunning(false)
        HandCamRuntimeState.setCameraStatus("Остановлена")
        HandCamRuntimeState.setGestureLabel("Рука не в кадре")
        HandCamRuntimeState.setHubState(false, "Остановлена")
        HandCamRuntimeState.setHasPcPeer(false)
        stopFrameLoop()
        dismissIncomingOfferOverlay()
        runCatching { hubClient?.disconnect() }
        hubClient = null
        runCatching { cameraTracker?.stop() }
        cameraTracker = null
        runCatching { sensorManager.unregisterListener(this) }
        if (viewAttached) {
            runCatching { windowManager.removeView(petView) }
            viewAttached = false
        }
        super.onDestroy()
    }

    private fun overlayWindowType(): Int {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        } else {
            @Suppress("DEPRECATION")
            WindowManager.LayoutParams.TYPE_PHONE
        }
    }

    private fun syncHubConnection() {
        val client = hubClient ?: return
        if (!settings.pcSyncEnabled) {
            client.disconnect()
            isHandoffPendingOutgoing = false
            setPetOwnedLocally(true)
            updateEdgeHandoffEligibility()
            return
        }
        if (settings.pcHubUrl.isBlank()) {
            client.disconnect()
            HandCamRuntimeState.setHubState(false, "Укажите IP ПК или нажмите «Найти ПК в Wi-Fi»")
            updateEdgeHandoffEligibility()
            return
        }
        client.connect(settings.pcHubUrl)
    }

    private fun updateEdgeHandoffEligibility() {
        physics.edgeHandoffEnabled = settings.pcSyncEnabled &&
            (hubClient?.isConnected == true) &&
            hasOnlinePcPeer &&
            isLocalOwner &&
            !isHandoffPendingOutgoing
    }

    private fun setPetOwnedLocally(owned: Boolean) {
        isLocalOwner = owned
        HandCamRuntimeState.setPetOwnedLocally(owned)
        if (viewAttached) {
            petView.visibility = if (owned && !isHandoffPendingOutgoing) View.VISIBLE else View.GONE
        }
        updateEdgeHandoffEligibility()
    }

    private fun triggerOutgoingHandoff(edge: String, normalizedY: Float) {
        val client = hubClient ?: return
        if (!settings.pcSyncEnabled || !client.isConnected || !isLocalOwner || !hasOnlinePcPeer || isHandoffPendingOutgoing) {
            return
        }
        // Push latest emotion/position right before handoff so PC receives exact emotion
        client.sendStateUpdate(
            x = physics.x,
            y = physics.y,
            animation = physics.animationName,
            state = physics.protocolState(),
            facing = physics.protocolFacing(),
            emotion = physics.emotion,
        )
        if (client.sendHandoffRequest(edge = edge, normalizedY = normalizedY)) {
            isHandoffPendingOutgoing = true
            petView.visibility = View.GONE
            updateEdgeHandoffEligibility()
        }
    }

    private fun handleHubEvent(event: HubEvent) {
        when (event) {
            is HubEvent.RegisterAck -> {
                updatePeerPresence(event.devices)
                applyAuthoritativeState(event.avatarState)
            }

            is HubEvent.DeviceList -> {
                updatePeerPresence(event.devices)
            }

            is HubEvent.StateSync -> {
                applyAuthoritativeState(event.avatarState)
            }

            is HubEvent.HandoffPending -> {
                if (event.sourceDeviceId == deviceId) {
                    isHandoffPendingOutgoing = true
                    petView.visibility = View.GONE
                    updateEdgeHandoffEligibility()
                }
            }

            is HubEvent.HandoffOffer -> {
                pendingIncomingOffer = event
                showIncomingOfferOverlay(event)
            }

            is HubEvent.HandoffComplete -> {
                dismissIncomingOfferOverlay()
                isHandoffPendingOutgoing = false
                if (event.newOwner == deviceId) {
                    val incomingEmo = HubProtocolCodec.petEmotionFromProtocol(event.emotion)
                    physics.receiveFromHub(event.edge, event.normalizedY, incomingEmo)
                    setPetOwnedLocally(true)
                    syncWindowPosition()
                } else {
                    setPetOwnedLocally(false)
                }
            }

            is HubEvent.HandoffCancel -> {
                dismissIncomingOfferOverlay()
                isHandoffPendingOutgoing = false
                if (event.restoredOwner == deviceId) {
                    setPetOwnedLocally(true)
                    physics.setEmotion(
                        HubProtocolCodec.petEmotionFromProtocol(event.emotion),
                        2.5f,
                        "Вернулся!",
                    )
                } else if (event.restoredOwner != null) {
                    setPetOwnedLocally(false)
                }
            }

            is HubEvent.Error -> {
                if (isHandoffPendingOutgoing) {
                    isHandoffPendingOutgoing = false
                    setPetOwnedLocally(true)
                }
            }

            is HubEvent.Ping, HubEvent.Ignored -> Unit
        }
    }

    private fun updatePeerPresence(devices: List<HubDeviceInfo>) {
        hasOnlinePcPeer = devices.any { it.connected && it.deviceId != deviceId }
        HandCamRuntimeState.setHasPcPeer(hasOnlinePcPeer)
        updateEdgeHandoffEligibility()
    }

    private fun applyAuthoritativeState(state: HubAvatarState) {
        if (!settings.pcSyncEnabled) return
        val weOwn = state.owner == deviceId
        if (weOwn) {
            isHandoffPendingOutgoing = state.state == "HANDOFF_PENDING"
            setPetOwnedLocally(true)
            if (isHandoffPendingOutgoing && viewAttached) {
                petView.visibility = View.GONE
            }
        } else if (state.owner != null) {
            isHandoffPendingOutgoing = false
            setPetOwnedLocally(false)
        }
    }

    private fun showIncomingOfferOverlay(offer: HubEvent.HandoffOffer) {
        if (!viewAttached) return
        dismissIncomingOfferOverlay()
        val banner = HandoffOfferOverlayView(
            context = this,
            onAcceptTouch = { confirmIncomingOffer(accepted = true, source = "touch") },
            onDeclineTouch = { confirmIncomingOffer(accepted = false, source = "touch") },
        )
        banner.bindOffer(offer.sourceDeviceName, offer.edge, offer.timeoutSeconds)

        val d = resources.displayMetrics.density
        val lp = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            overlayWindowType(),
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.CENTER_HORIZONTAL
            y = topInsetPx + (18 * d).roundToInt()
        }
        runCatching {
            windowManager.addView(banner, lp)
            offerOverlayView = banner
            offerAttached = true
        }
    }

    private fun dismissIncomingOfferOverlay() {
        pendingIncomingOffer = null
        val banner = offerOverlayView
        offerOverlayView = null
        if (offerAttached && banner != null) {
            runCatching { windowManager.removeView(banner) }
        }
        offerAttached = false
    }

    private fun confirmIncomingOffer(accepted: Boolean, source: String) {
        val offer = pendingIncomingOffer ?: return
        dismissIncomingOfferOverlay()
        val client = hubClient ?: return
        if (accepted) {
            val incomingEmo = HubProtocolCodec.petEmotionFromProtocol(offer.emotion)
            physics.receiveFromHub(offer.edge, offer.normalizedY, incomingEmo)
            setPetOwnedLocally(true)
            syncWindowPosition()
            client.sendHandoffConfirm(
                handoffId = offer.handoffId,
                accepted = true,
                confirmationSource = source,
                landingX = physics.x,
                landingY = physics.y,
            )
        } else {
            client.sendHandoffConfirm(
                handoffId = offer.handoffId,
                accepted = false,
                confirmationSource = source,
            )
        }
    }

    private fun hasCameraPermission(): Boolean {
        return ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED
    }

    private fun syncCameraState() {
        if (!settings.cameraControlEnabled) {
            cameraTracker?.stop()
            latestHandFrame.set(null)
            petView.activeGesture = HandGesture.NONE
            HandCamRuntimeState.setCameraStatus("Выключена в настройках")
            HandCamRuntimeState.setGestureLabel("Камера выкл.")
            return
        }
        if (!hasCameraPermission()) {
            cameraTracker?.stop()
            HandCamRuntimeState.setCameraStatus("Нет разрешения на камеру")
            HandCamRuntimeState.setGestureLabel("Дайте доступ к камере")
            return
        }
        startForegroundSafely()
        cameraTracker?.start(frontCamera = settings.useFrontCamera)
    }

    private fun applySettingsToPhysics() {
        physics.tiltPhysicsEnabled = settings.tiltPhysicsEnabled
        physics.tiltSensitivity = settings.tiltSensitivity
        physics.bounciness = settings.bounciness
    }

    private fun computePetPixelSize(scale: Int): Int {
        val density = resources.displayMetrics.density
        return (32f * scale.coerceIn(2, 5) * density * 0.85f).roundToInt().coerceIn(72, 320)
    }

    private fun startFrameLoop() {
        if (choreographerRunning) return
        choreographerRunning = true
        lastFrameNanos = 0L
        Choreographer.getInstance().postFrameCallback(this)
    }

    private fun stopFrameLoop() {
        choreographerRunning = false
        Choreographer.getInstance().removeFrameCallback(this)
    }

    override fun doFrame(frameTimeNanos: Long) {
        if (!choreographerRunning || !viewAttached) return
        val dt = if (lastFrameNanos == 0L) {
            1f / 60f
        } else {
            ((frameTimeNanos - lastFrameNanos) / 1_000_000_000f).coerceIn(0.001f, 0.05f)
        }
        lastFrameNanos = frameTimeNanos

        // Check incoming handoff offer timeout + hands-free camera Open Palm confirmation
        val activeOffer = pendingIncomingOffer
        if (activeOffer != null) {
            val nowSec = System.currentTimeMillis() / 1000.0
            if (nowSec > activeOffer.expiresAtSec) {
                dismissIncomingOfferOverlay()
            } else if (settings.cameraControlEnabled) {
                val handFrame = latestHandFrame.get()
                if (handFrame != null && handFrame.detected && handFrame.gesture == HandGesture.OPEN_PALM) {
                    confirmIncomingOffer(accepted = true, source = "gesture")
                }
            }
        }

        if (isLocalOwner && !isHandoffPendingOutgoing) {
            if (settings.cameraControlEnabled) {
                val handFrame = latestHandFrame.get()
                if (handFrame != null) {
                    petView.activeGesture = handFrame.gesture
                    physics.applyHandGesture(handFrame, dt)
                } else {
                    petView.activeGesture = HandGesture.NONE
                }
            }

            physics.step(dt)
            HandCamRuntimeState.setEmotion(physics.emotion)
            petView.advanceAnimation(dt)
            syncWindowPosition()

            // Throttled live state & emotion sync to PC Hub (~8 Hz)
            val nowMs = SystemClock.uptimeMillis()
            if (settings.pcSyncEnabled && hubClient?.isConnected == true && nowMs - lastHubStateSentMs >= 125L) {
                lastHubStateSentMs = nowMs
                hubClient?.sendStateUpdate(
                    x = physics.x,
                    y = physics.y,
                    animation = physics.animationName,
                    state = physics.protocolState(),
                    facing = physics.protocolFacing(),
                    emotion = physics.emotion,
                )
            }
        }

        Choreographer.getInstance().postFrameCallback(this)
    }

    private fun syncWindowPosition() {
        if (!viewAttached) return
        val targetX = (physics.x - petView.paddingPx).roundToInt()
        val targetY = (physics.y + topInsetPx - petView.paddingPx).roundToInt()
        if (layoutParams.x != targetX || layoutParams.y != targetY) {
            layoutParams.x = targetX
            layoutParams.y = targetY
            runCatching { windowManager.updateViewLayout(petView, layoutParams) }
        }
    }

    private fun registerGravitySensor() {
        val gravitySensor = sensorManager.getDefaultSensor(Sensor.TYPE_GRAVITY)
        if (gravitySensor != null) {
            usingAccelerometerFallback = false
            sensorManager.registerListener(this, gravitySensor, SensorManager.SENSOR_DELAY_GAME)
        } else {
            val accel = sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)
            if (accel != null) {
                usingAccelerometerFallback = true
                sensorManager.registerListener(this, accel, SensorManager.SENSOR_DELAY_GAME)
            }
        }
    }

    override fun onSensorChanged(event: SensorEvent?) {
        if (event == null || !viewAttached || !isLocalOwner || isHandoffPendingOutgoing) return
        val rawX = event.values[0]
        val rawY = event.values[1]
        val rawZ = event.values[2]

        var shakeMag = 0f
        val gxSensor: Float
        val gySensor: Float

        if (usingAccelerometerFallback) {
            val alpha = 0.80f
            filteredGravity[0] = alpha * filteredGravity[0] + (1f - alpha) * rawX
            filteredGravity[1] = alpha * filteredGravity[1] + (1f - alpha) * rawY
            filteredGravity[2] = alpha * filteredGravity[2] + (1f - alpha) * rawZ
            val linX = rawX - filteredGravity[0]
            val linY = rawY - filteredGravity[1]
            val linZ = rawZ - filteredGravity[2]
            shakeMag = hypot(hypot(linX, linY), linZ)
            gxSensor = filteredGravity[0]
            gySensor = filteredGravity[1]
        } else {
            gxSensor = rawX
            gySensor = rawY
        }

        val rotation = currentDisplayRotation()
        val screenGx: Float
        val screenGy: Float
        when (rotation) {
            Surface.ROTATION_90 -> {
                screenGx = gySensor
                screenGy = gxSensor
            }
            Surface.ROTATION_180 -> {
                screenGx = gxSensor
                screenGy = -gySensor
            }
            Surface.ROTATION_270 -> {
                screenGx = -gySensor
                screenGy = -gxSensor
            }
            else -> {
                screenGx = -gxSensor
                screenGy = gySensor
            }
        }

        physics.setTiltGravity(screenGx, screenGy, shakeMagnitude = shakeMag)
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit

    private fun vibrateBonk(impactSpeed: Float) {
        if (!settings.hapticBonkEnabled) return
        val vibrator: Vibrator? = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val mgr = getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager
            mgr?.defaultVibrator
        } else {
            @Suppress("DEPRECATION")
            getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
        }
        if (vibrator == null || !vibrator.hasVibrator()) return
        val ms = if (impactSpeed > 650f) 32L else 18L
        runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                vibrator.vibrate(VibrationEffect.createOneShot(ms, VibrationEffect.DEFAULT_AMPLITUDE))
            } else {
                @Suppress("DEPRECATION")
                vibrator.vibrate(ms)
            }
        }
    }

    private data class ScreenBounds(val width: Int, val height: Int)

    private fun measureUsableBounds(): ScreenBounds {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val metrics = windowManager.currentWindowMetrics
            val insets = metrics.windowInsets.getInsetsIgnoringVisibility(
                WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout()
            )
            topInsetPx = insets.top
            bottomInsetPx = insets.bottom
            val b = metrics.bounds
            ScreenBounds(
                width = (b.width() - insets.left - insets.right).coerceAtLeast(320),
                height = (b.height() - insets.top - insets.bottom).coerceAtLeast(320),
            )
        } else {
            val dm = DisplayMetrics()
            @Suppress("DEPRECATION")
            windowManager.defaultDisplay.getMetrics(dm)
            topInsetPx = (24f * dm.density).roundToInt()
            bottomInsetPx = (24f * dm.density).roundToInt()
            ScreenBounds(
                width = dm.widthPixels.coerceAtLeast(320),
                height = (dm.heightPixels - topInsetPx - bottomInsetPx).coerceAtLeast(320),
            )
        }
    }

    private fun currentDisplayRotation(): Int {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            runCatching { display?.rotation }.getOrNull() ?: Surface.ROTATION_0
        } else {
            @Suppress("DEPRECATION")
            windowManager.defaultDisplay?.rotation ?: Surface.ROTATION_0
        }
    }

    private fun startForegroundSafely() {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Shimeji Hand Cam поверх экрана",
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = "Пиксельный чубрик поверх экрана + камера жестов + синхронизация с ПК"
            }
            manager.createNotificationChannel(channel)
        }

        val openAppIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val sendPcIntent = PendingIntent.getService(
            this,
            2,
            Intent(this, ShimejiHandCamService::class.java).apply { action = ACTION_SEND_TO_PC },
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val stopIntent = PendingIntent.getService(
            this,
            1,
            Intent(this, ShimejiHandCamService::class.java).apply { action = ACTION_STOP },
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

        val notification: Notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.star_on)
            .setContentTitle("Shimeji — Единая система (Телефон + ПК)")
            .setContentText("Жесты ладонью, эмоции и перенос чубрика на ПК по Wi-Fi")
            .setOngoing(true)
            .setContentIntent(openAppIntent)
            .addAction(android.R.drawable.ic_menu_send, "На ПК", sendPcIntent)
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, "Выключить", stopIntent)
            .build()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            val fgsTypes = if (hasCameraPermission() && settings.cameraControlEnabled) {
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE or ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA
            } else {
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
            }
            startForeground(NOTIF_ID, notification, fgsTypes)
        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R && hasCameraPermission() && settings.cameraControlEnabled) {
            startForeground(NOTIF_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA)
        } else {
            startForeground(NOTIF_ID, notification)
        }
    }

    companion object {
        private const val CHANNEL_ID = "shimeji_hand_cam_channel"
        private const val NOTIF_ID = 2042

        const val ACTION_START = "com.gcs.shimeji.handcam.ACTION_START"
        const val ACTION_STOP = "com.gcs.shimeji.handcam.ACTION_STOP"
        const val ACTION_RELOAD_SETTINGS = "com.gcs.shimeji.handcam.ACTION_RELOAD_SETTINGS"
        const val ACTION_SEND_TO_PC = "com.gcs.shimeji.handcam.ACTION_SEND_TO_PC"
        const val ACTION_TEST_FALL = "com.gcs.shimeji.handcam.ACTION_TEST_FALL"
        const val ACTION_EMOTE_LOVE = "com.gcs.shimeji.handcam.ACTION_EMOTE_LOVE"
        const val ACTION_EMOTE_HAPPY = "com.gcs.shimeji.handcam.ACTION_EMOTE_HAPPY"
        const val ACTION_EMOTE_ANGRY = "com.gcs.shimeji.handcam.ACTION_EMOTE_ANGRY"
        const val ACTION_EMOTE_SLEEP = "com.gcs.shimeji.handcam.ACTION_EMOTE_SLEEP"

        fun start(context: Context) {
            val intent = Intent(context, ShimejiHandCamService::class.java).apply {
                action = ACTION_START
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, ShimejiHandCamService::class.java))
        }

        fun sendAction(context: Context, action: String) {
            if (!HandCamRuntimeState.isRunning.value) return
            val intent = Intent(context, ShimejiHandCamService::class.java).apply {
                this.action = action
            }
            context.startService(intent)
        }
    }
}
