package com.gcs.shimeji.handcam

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.util.UUID

data class HandCamSettings(
    val scale: Int = 3,
    val tiltPhysicsEnabled: Boolean = true,
    val tiltSensitivity: Float = 0.65f,
    val bounciness: Float = 0.65f,
    val hapticBonkEnabled: Boolean = true,
    val cameraControlEnabled: Boolean = true,
    val useFrontCamera: Boolean = true,
    val showGestureBadge: Boolean = true,
    val pcSyncEnabled: Boolean = false,
    val pcHubUrl: String = "",
)

object HandCamRuntimeState {
    private val _isRunning = MutableStateFlow(false)
    val isRunning: StateFlow<Boolean> = _isRunning.asStateFlow()

    private val _emotion = MutableStateFlow(PetEmotion.NEUTRAL)
    val emotion: StateFlow<PetEmotion> = _emotion.asStateFlow()

    private val _gestureLabel = MutableStateFlow("Рука не в кадре")
    val gestureLabel: StateFlow<String> = _gestureLabel.asStateFlow()

    private val _cameraStatus = MutableStateFlow("Остановлена")
    val cameraStatus: StateFlow<String> = _cameraStatus.asStateFlow()

    private val _hubConnected = MutableStateFlow(false)
    val hubConnected: StateFlow<Boolean> = _hubConnected.asStateFlow()

    private val _hubStatus = MutableStateFlow("Автономный режим (без ПК)")
    val hubStatus: StateFlow<String> = _hubStatus.asStateFlow()

    private val _hasPcPeer = MutableStateFlow(false)
    val hasPcPeer: StateFlow<Boolean> = _hasPcPeer.asStateFlow()

    private val _petOwnedLocally = MutableStateFlow(true)
    val petOwnedLocally: StateFlow<Boolean> = _petOwnedLocally.asStateFlow()

    fun setRunning(running: Boolean) {
        _isRunning.value = running
    }

    fun setEmotion(value: PetEmotion) {
        if (_emotion.value != value) _emotion.value = value
    }

    fun setGestureLabel(label: String) {
        if (_gestureLabel.value != label) _gestureLabel.value = label
    }

    fun setCameraStatus(status: String) {
        if (_cameraStatus.value != status) _cameraStatus.value = status
    }

    fun setHubState(connected: Boolean, status: String) {
        _hubConnected.value = connected
        _hubStatus.value = status
    }

    fun setHasPcPeer(hasPeer: Boolean) {
        _hasPcPeer.value = hasPeer
    }

    fun setPetOwnedLocally(owned: Boolean) {
        _petOwnedLocally.value = owned
    }
}

class HandCamPreferences(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun getOrCreateDeviceId(): String {
        val existing = prefs.getString(KEY_DEVICE_ID, null)
        if (!existing.isNullOrBlank()) return existing
        val fresh = "and-${UUID.randomUUID().toString().replace("-", "").take(10)}"
        prefs.edit().putString(KEY_DEVICE_ID, fresh).apply()
        return fresh
    }

    fun load(): HandCamSettings {
        return HandCamSettings(
            scale = prefs.getInt(KEY_SCALE, 3).coerceIn(2, 5),
            tiltPhysicsEnabled = prefs.getBoolean(KEY_TILT_ENABLED, true),
            tiltSensitivity = prefs.getFloat(KEY_TILT_SENSITIVITY, 0.65f).coerceIn(0.2f, 1.0f),
            bounciness = prefs.getFloat(KEY_BOUNCINESS, 0.65f).coerceIn(0.25f, 1.0f),
            hapticBonkEnabled = prefs.getBoolean(KEY_HAPTIC_BONK, true),
            cameraControlEnabled = prefs.getBoolean(KEY_CAMERA_ENABLED, true),
            useFrontCamera = prefs.getBoolean(KEY_FRONT_CAMERA, true),
            showGestureBadge = prefs.getBoolean(KEY_GESTURE_BADGE, true),
            pcSyncEnabled = prefs.getBoolean(KEY_PC_SYNC_ENABLED, false),
            pcHubUrl = prefs.getString(KEY_PC_HUB_URL, "") ?: "",
        )
    }

    fun save(settings: HandCamSettings) {
        prefs.edit()
            .putInt(KEY_SCALE, settings.scale.coerceIn(2, 5))
            .putBoolean(KEY_TILT_ENABLED, settings.tiltPhysicsEnabled)
            .putFloat(KEY_TILT_SENSITIVITY, settings.tiltSensitivity.coerceIn(0.2f, 1.0f))
            .putFloat(KEY_BOUNCINESS, settings.bounciness.coerceIn(0.25f, 1.0f))
            .putBoolean(KEY_HAPTIC_BONK, settings.hapticBonkEnabled)
            .putBoolean(KEY_CAMERA_ENABLED, settings.cameraControlEnabled)
            .putBoolean(KEY_FRONT_CAMERA, settings.useFrontCamera)
            .putBoolean(KEY_GESTURE_BADGE, settings.showGestureBadge)
            .putBoolean(KEY_PC_SYNC_ENABLED, settings.pcSyncEnabled)
            .putString(KEY_PC_HUB_URL, settings.pcHubUrl.trim())
            .apply()
    }

    companion object {
        private const val PREFS_NAME = "shimeji_hand_cam_prefs"
        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_SCALE = "scale"
        private const val KEY_TILT_ENABLED = "tilt_enabled"
        private const val KEY_TILT_SENSITIVITY = "tilt_sensitivity"
        private const val KEY_BOUNCINESS = "bounciness"
        private const val KEY_HAPTIC_BONK = "haptic_bonk"
        private const val KEY_CAMERA_ENABLED = "camera_enabled"
        private const val KEY_FRONT_CAMERA = "front_camera"
        private const val KEY_GESTURE_BADGE = "gesture_badge"
        private const val KEY_PC_SYNC_ENABLED = "pc_sync_enabled"
        private const val KEY_PC_HUB_URL = "pc_hub_url"
    }
}
