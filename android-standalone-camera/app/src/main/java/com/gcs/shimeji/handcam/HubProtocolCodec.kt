package com.gcs.shimeji.handcam

import org.json.JSONArray
import org.json.JSONObject
import java.net.URLDecoder
import java.util.UUID

data class HubDeviceInfo(
    val deviceId: String,
    val deviceType: String,
    val deviceName: String,
    val connected: Boolean,
)

data class HubAvatarState(
    val owner: String?,
    val state: String,
    val animation: String,
    val facing: String,
    val emotion: String,
    val handoffId: String?,
)

sealed class HubEvent {
    data class RegisterAck(
        val deviceId: String,
        val avatarState: HubAvatarState,
        val devices: List<HubDeviceInfo>,
    ) : HubEvent()

    data class DeviceList(val devices: List<HubDeviceInfo>) : HubEvent()

    data class StateSync(val avatarState: HubAvatarState) : HubEvent()

    data class HandoffPending(
        val handoffId: String,
        val sourceDeviceId: String,
        val targetDeviceId: String,
    ) : HubEvent()

    data class HandoffOffer(
        val handoffId: String,
        val sourceDeviceId: String,
        val sourceDeviceName: String,
        val edge: String,
        val normalizedY: Float,
        val timeoutSeconds: Float,
        val expiresAtSec: Double,
        val emotion: String,
    ) : HubEvent()

    data class HandoffComplete(
        val handoffId: String,
        val previousOwner: String,
        val newOwner: String,
        val edge: String,
        val normalizedY: Float,
        val emotion: String,
    ) : HubEvent()

    data class HandoffCancel(
        val handoffId: String,
        val reason: String,
        val restoredOwner: String?,
        val emotion: String,
    ) : HubEvent()

    data class Ping(val sentAt: Double) : HubEvent()
    data class Error(val code: String, val message: String) : HubEvent()
    data object Ignored : HubEvent()
}

/**
 * Pure-Kotlin GCS-Shimeji-WS protocol codec shared with the PC GCS Local Hub.
 * 100% unit-testable on JVM without Android dependencies.
 */
object HubProtocolCodec {

    const val DEFAULT_WS_PORT = 8765

    /**
     * Normalize flexible user input into a valid `ws://host:port` WebSocket URL:
     * - `192.168.1.42` -> `ws://192.168.1.42:8765`
     * - `192.168.1.42:8765` -> `ws://192.168.1.42:8765`
     * - `ws://192.168.1.42:8765` -> `ws://192.168.1.42:8765`
     * - `http://192.168.1.42:8766` -> `ws://192.168.1.42:8765`
     * - `gcs-shimeji://pair?ws=ws%3A%2F%2F192.168.1.42%3A8765&...` -> `ws://192.168.1.42:8765`
     */
    fun parseHubUrl(rawInput: String): String? {
        val trimmed = rawInput.trim()
        if (trimmed.isEmpty()) return null

        // Handle gcs-shimeji://pair?... QR pairing URI
        if (trimmed.startsWith("gcs-shimeji://", ignoreCase = true)) {
            val queryIdx = trimmed.indexOf('?')
            if (queryIdx >= 0 && queryIdx + 1 < trimmed.length) {
                val query = trimmed.substring(queryIdx + 1)
                val params = query.split("&").mapNotNull { pair ->
                    val eq = pair.indexOf('=')
                    if (eq <= 0) null
                    else pair.substring(0, eq) to URLDecoder.decode(pair.substring(eq + 1), "UTF-8")
                }.toMap()
                val wsParam = params["ws"]
                if (!wsParam.isNullOrBlank()) {
                    return parseHubUrl(wsParam)
                }
                val host = params["host"]
                val port = params["port"]?.toIntOrNull() ?: DEFAULT_WS_PORT
                if (!host.isNullOrBlank()) {
                    return "ws://$host:$port"
                }
            }
            return null
        }

        // Handle http://host:8766 pasted from web companion
        if (trimmed.startsWith("http://", ignoreCase = true) || trimmed.startsWith("https://", ignoreCase = true)) {
            val stripped = trimmed.substringAfter("://").substringBefore('/')
            val hostPart = stripped.substringBefore(':')
            val rawPort = stripped.substringAfter(':', "").toIntOrNull()
            if (hostPart.isBlank()) return null
            val wsPort = when (rawPort) {
                null -> DEFAULT_WS_PORT
                8766 -> DEFAULT_WS_PORT
                else -> rawPort
            }
            return "ws://$hostPart:$wsPort"
        }

        // Handle ws:// or wss://
        if (trimmed.startsWith("ws://", ignoreCase = true) || trimmed.startsWith("wss://", ignoreCase = true)) {
            val scheme = trimmed.substringBefore("://").lowercase()
            val rest = trimmed.substringAfter("://").substringBefore('/')
            if (rest.isBlank()) return null
            return if (rest.contains(':')) "$scheme://$rest" else "$scheme://$rest:$DEFAULT_WS_PORT"
        }

        // Plain host or host:port
        val cleaned = trimmed.substringBefore('/')
        if (cleaned.contains(' ')) return null
        return if (cleaned.contains(':')) {
            "ws://$cleaned"
        } else {
            "ws://$cleaned:$DEFAULT_WS_PORT"
        }
    }

    private fun wrapEnvelope(type: String, payload: JSONObject): String {
        val env = JSONObject()
        env.put("type", type)
        env.put("message_id", UUID.randomUUID().toString().replace("-", ""))
        env.put("timestamp", System.currentTimeMillis() / 1000.0)
        env.put("payload", payload)
        return env.toString()
    }

    fun buildRegisterDevice(
        deviceId: String,
        deviceName: String,
        screenWidth: Int,
        screenHeight: Int,
        capabilities: List<String> = listOf("overlay_pet", "camera_gesture_control", "camera_gesture_confirm", "touch_confirm", "emotions"),
    ): String {
        val payload = JSONObject().apply {
            put("device_id", deviceId)
            put("device_type", "android")
            put("device_name", deviceName)
            put("screen_width", screenWidth)
            put("screen_height", screenHeight)
            put("capabilities", JSONArray(capabilities))
        }
        return wrapEnvelope("register_device", payload)
    }

    fun buildStateUpdate(
        deviceId: String,
        x: Float,
        y: Float,
        animation: String,
        state: String,
        facing: String,
        emotion: String,
    ): String {
        val safeAnim = if (animation in ALLOWED_ANIMATIONS) animation else "idle"
        val safeState = if (state in ALLOWED_STATES) state else "ACTIVE"
        val safeFacing = if (facing == "left") "left" else "right"
        val safeEmotion = emotion.lowercase().takeIf { it in ALLOWED_EMOTIONS } ?: "neutral"

        val pos = JSONObject().apply {
            put("x", x.toDouble())
            put("y", y.toDouble())
        }
        val payload = JSONObject().apply {
            put("device_id", deviceId)
            put("position", pos)
            put("animation", safeAnim)
            put("state", safeState)
            put("facing", safeFacing)
            put("emotion", safeEmotion)
        }
        return wrapEnvelope("state_update", payload)
    }

    fun buildHandoffRequest(
        sourceDeviceId: String,
        edge: String,
        normalizedY: Float,
        targetDeviceId: String? = null,
        idempotencyKey: String = "and-ho-${UUID.randomUUID().toString().take(12)}",
    ): String {
        val safeEdge = if (edge in setOf("left", "right", "top", "bottom")) edge else "right"
        val payload = JSONObject().apply {
            put("source_device_id", sourceDeviceId)
            if (!targetDeviceId.isNullOrBlank()) {
                put("target_device_id", targetDeviceId)
            }
            put("edge", safeEdge)
            put("normalized_y", normalizedY.coerceIn(0f, 1f).toDouble())
            put("idempotency_key", idempotencyKey)
        }
        return wrapEnvelope("handoff_request", payload)
    }

    fun buildHandoffConfirm(
        handoffId: String,
        deviceId: String,
        accepted: Boolean,
        confirmationSource: String = "touch",
        landingX: Float? = null,
        landingY: Float? = null,
    ): String {
        val safeSource = if (confirmationSource in setOf("touch", "click", "keyboard", "gesture")) {
            confirmationSource
        } else {
            "touch"
        }
        val payload = JSONObject().apply {
            put("handoff_id", handoffId)
            put("device_id", deviceId)
            put("accepted", accepted)
            put("confirmation_source", safeSource)
            if (landingX != null && landingY != null) {
                put(
                    "landing_position",
                    JSONObject().apply {
                        put("x", landingX.toDouble())
                        put("y", landingY.toDouble())
                    },
                )
            }
        }
        return wrapEnvelope("handoff_confirm", payload)
    }

    fun buildPong(sentAt: Double): String {
        val payload = JSONObject().apply {
            put("sent_at", sentAt)
            put("received_at", System.currentTimeMillis() / 1000.0)
        }
        return wrapEnvelope("pong", payload)
    }

    fun parseIncoming(rawJson: String): HubEvent {
        return runCatching {
            val env = JSONObject(rawJson)
            val type = env.optString("type", "")
            val payload = env.optJSONObject("payload") ?: JSONObject()

            when (type) {
                "register_ack" -> HubEvent.RegisterAck(
                    deviceId = payload.optString("device_id", ""),
                    avatarState = parseAvatarState(payload.optJSONObject("avatar_state")),
                    devices = parseDeviceList(payload.optJSONArray("devices")),
                )

                "device_list" -> HubEvent.DeviceList(
                    devices = parseDeviceList(payload.optJSONArray("devices")),
                )

                "state_sync" -> HubEvent.StateSync(
                    avatarState = parseAvatarState(payload),
                )

                "handoff_pending" -> HubEvent.HandoffPending(
                    handoffId = payload.optString("handoff_id", ""),
                    sourceDeviceId = payload.optString("source_device_id", ""),
                    targetDeviceId = payload.optString("target_device_id", ""),
                )

                "handoff_offer" -> {
                    val avatarState = parseAvatarState(payload.optJSONObject("avatar_state"))
                    HubEvent.HandoffOffer(
                        handoffId = payload.optString("handoff_id", ""),
                        sourceDeviceId = payload.optString("source_device_id", ""),
                        sourceDeviceName = payload.optString("source_device_name")
                            .ifBlank { payload.optString("source_device_id", "PC") },
                        edge = payload.optString("edge", "right"),
                        normalizedY = payload.optDouble("normalized_y", 0.5).toFloat().coerceIn(0f, 1f),
                        timeoutSeconds = payload.optDouble("timeout_seconds", 6.0).toFloat(),
                        expiresAtSec = payload.optDouble("expires_at", (System.currentTimeMillis() / 1000.0) + 6.0),
                        emotion = avatarState.emotion,
                    )
                }

                "handoff_complete" -> {
                    val avatarState = parseAvatarState(payload.optJSONObject("avatar_state"))
                    HubEvent.HandoffComplete(
                        handoffId = payload.optString("handoff_id", ""),
                        previousOwner = payload.optString("previous_owner", ""),
                        newOwner = payload.optString("new_owner", ""),
                        edge = payload.optString("edge", "right"),
                        normalizedY = payload.optDouble("normalized_y", 0.5).toFloat().coerceIn(0f, 1f),
                        emotion = avatarState.emotion,
                    )
                }

                "handoff_cancel" -> {
                    val avatarState = parseAvatarState(payload.optJSONObject("avatar_state"))
                    HubEvent.HandoffCancel(
                        handoffId = payload.optString("handoff_id", ""),
                        reason = payload.optString("reason", "cancelled"),
                        restoredOwner = payload.optString("restored_owner").ifBlank { avatarState.owner },
                        emotion = avatarState.emotion,
                    )
                }

                "ping" -> HubEvent.Ping(payload.optDouble("sent_at", System.currentTimeMillis() / 1000.0))

                "error" -> HubEvent.Error(
                    code = payload.optString("code", "UNKNOWN"),
                    message = payload.optString("message", "Hub error"),
                )

                else -> HubEvent.Ignored
            }
        }.getOrElse {
            HubEvent.Error("PARSE_ERROR", it.message ?: "Malformed hub message")
        }
    }

    private fun parseAvatarState(obj: JSONObject?): HubAvatarState {
        if (obj == null) {
            return HubAvatarState(null, "ACTIVE", "idle", "right", "neutral", null)
        }
        val ownerRaw = if (obj.isNull("owner")) null else obj.optString("owner").ifBlank { null }
        val handoffRaw = if (obj.isNull("handoff_id")) null else obj.optString("handoff_id").ifBlank { null }
        return HubAvatarState(
            owner = ownerRaw,
            state = obj.optString("state", "ACTIVE"),
            animation = obj.optString("animation", "idle"),
            facing = obj.optString("facing", "right"),
            emotion = obj.optString("emotion", "neutral").lowercase(),
            handoffId = handoffRaw,
        )
    }

    private fun parseDeviceList(arr: JSONArray?): List<HubDeviceInfo> {
        if (arr == null) return emptyList()
        val list = mutableListOf<HubDeviceInfo>()
        for (i in 0 until arr.length()) {
            val d = arr.optJSONObject(i) ?: continue
            list.add(
                HubDeviceInfo(
                    deviceId = d.optString("device_id", ""),
                    deviceType = d.optString("device_type", "unknown"),
                    deviceName = d.optString("device_name", ""),
                    connected = d.optBoolean("connected", false),
                ),
            )
        }
        return list
    }

    fun petEmotionFromProtocol(name: String): PetEmotion = when (name.lowercase()) {
        "happy" -> PetEmotion.HAPPY
        "love" -> PetEmotion.LOVE
        "excited" -> PetEmotion.EXCITED
        "scared" -> PetEmotion.SCARED
        "dizzy" -> PetEmotion.DIZZY
        "angry" -> PetEmotion.ANGRY
        "sleepy" -> PetEmotion.SLEEPY
        else -> PetEmotion.NEUTRAL
    }

    fun petEmotionToProtocol(emotion: PetEmotion): String = when (emotion) {
        PetEmotion.NEUTRAL -> "neutral"
        PetEmotion.HAPPY -> "happy"
        PetEmotion.LOVE -> "love"
        PetEmotion.EXCITED -> "excited"
        PetEmotion.SCARED -> "scared"
        PetEmotion.DIZZY -> "dizzy"
        PetEmotion.ANGRY -> "angry"
        PetEmotion.SLEEPY -> "sleepy"
    }

    private val ALLOWED_ANIMATIONS = setOf("idle", "walk", "run", "jump", "sleep", "grab", "handoff", "receive", "landing")
    private val ALLOWED_STATES = setOf("ACTIVE", "DRAGGING", "SLEEPING")
    private val ALLOWED_EMOTIONS = setOf("neutral", "happy", "love", "excited", "scared", "dizzy", "angry", "sleepy")
}
