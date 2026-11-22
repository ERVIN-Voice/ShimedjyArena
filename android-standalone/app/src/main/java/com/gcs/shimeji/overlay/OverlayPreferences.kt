package com.gcs.shimeji.overlay

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

data class OverlaySettings(
    val scale: Int = 3,
    val tiltPhysicsEnabled: Boolean = true,
    val tiltSensitivity: Float = 0.65f, // 0.2 (gentle) .. 1.0 (very sensitive)
    val bounciness: Float = 0.65f,      // 0.3 .. 1.0
    val hapticBonkEnabled: Boolean = true,
)

object OverlayRuntimeState {
    private val _isRunning = MutableStateFlow(false)
    val isRunning: StateFlow<Boolean> = _isRunning.asStateFlow()

    fun setRunning(running: Boolean) {
        _isRunning.value = running
    }
}

class OverlayPreferences(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    fun load(): OverlaySettings {
        return OverlaySettings(
            scale = prefs.getInt(KEY_SCALE, 3).coerceIn(2, 5),
            tiltPhysicsEnabled = prefs.getBoolean(KEY_TILT_ENABLED, true),
            tiltSensitivity = prefs.getFloat(KEY_TILT_SENSITIVITY, 0.65f).coerceIn(0.2f, 1.0f),
            bounciness = prefs.getFloat(KEY_BOUNCINESS, 0.65f).coerceIn(0.25f, 1.0f),
            hapticBonkEnabled = prefs.getBoolean(KEY_HAPTIC_BONK, true),
        )
    }

    fun save(settings: OverlaySettings) {
        prefs.edit()
            .putInt(KEY_SCALE, settings.scale.coerceIn(2, 5))
            .putBoolean(KEY_TILT_ENABLED, settings.tiltPhysicsEnabled)
            .putFloat(KEY_TILT_SENSITIVITY, settings.tiltSensitivity.coerceIn(0.2f, 1.0f))
            .putFloat(KEY_BOUNCINESS, settings.bounciness.coerceIn(0.25f, 1.0f))
            .putBoolean(KEY_HAPTIC_BONK, settings.hapticBonkEnabled)
            .apply()
    }

    companion object {
        private const val PREFS_NAME = "shimeji_standalone_overlay_prefs"
        private const val KEY_SCALE = "scale"
        private const val KEY_TILT_ENABLED = "tilt_enabled"
        private const val KEY_TILT_SENSITIVITY = "tilt_sensitivity"
        private const val KEY_BOUNCINESS = "bounciness"
        private const val KEY_HAPTIC_BONK = "haptic_bonk"
    }
}
