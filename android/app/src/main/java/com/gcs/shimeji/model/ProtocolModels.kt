package com.gcs.shimeji.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.util.UUID

val GcsJson = Json {
    ignoreUnknownKeys = true
    encodeDefaults = true
    explicitNulls = false
}

enum class ConnectionStatus {
    DISCONNECTED,
    CONNECTING,
    CONNECTED,
    RECONNECTING,
}

@Serializable
data class WireEnvelope(
    val type: String,
    @SerialName("message_id") val messageId: String = UUID.randomUUID().toString().replace("-", ""),
    val timestamp: Double = System.currentTimeMillis() / 1000.0,
    val payload: JsonObject = buildJsonObject {},
)

@Serializable
data class PositionDto(
    val x: Double = 0.0,
    val y: Double = 0.0,
)

@Serializable
data class AvatarStateDto(
    val owner: String? = null,
    val position: PositionDto = PositionDto(),
    val animation: String = "idle",
    val state: String = "ACTIVE",
    val facing: String = "right",
    @SerialName("handoff_id") val handoffId: String? = null,
    val version: Int = 1,
    @SerialName("updated_at") val updatedAt: Double = 0.0,
)

@Serializable
data class DeviceSummaryDto(
    @SerialName("device_id") val deviceId: String,
    @SerialName("device_type") val deviceType: String,
    @SerialName("device_name") val deviceName: String,
    @SerialName("screen_width") val screenWidth: Int = 0,
    @SerialName("screen_height") val screenHeight: Int = 0,
    val connected: Boolean = true,
)

@Serializable
data class RegisterAckPayload(
    @SerialName("device_id") val deviceId: String,
    @SerialName("hub_version") val hubVersion: String = "1.0.0",
    @SerialName("protocol_version") val protocolVersion: String = "1.0.0",
    @SerialName("avatar_state") val avatarState: AvatarStateDto,
    val devices: List<DeviceSummaryDto> = emptyList(),
)

@Serializable
data class DeviceListPayload(
    val devices: List<DeviceSummaryDto> = emptyList(),
)

@Serializable
data class HandoffPendingPayload(
    @SerialName("handoff_id") val handoffId: String,
    @SerialName("source_device_id") val sourceDeviceId: String,
    @SerialName("target_device_id") val targetDeviceId: String,
    @SerialName("expires_at") val expiresAt: Double,
    @SerialName("timeout_seconds") val timeoutSeconds: Double = 6.0,
)

@Serializable
data class HandoffOfferPayload(
    @SerialName("handoff_id") val handoffId: String,
    @SerialName("source_device_id") val sourceDeviceId: String,
    @SerialName("source_device_type") val sourceDeviceType: String = "windows",
    @SerialName("source_device_name") val sourceDeviceName: String = "Desktop",
    val edge: String = "right",
    @SerialName("normalized_y") val normalizedY: Double = 0.5,
    @SerialName("expires_at") val expiresAt: Double,
    @SerialName("timeout_seconds") val timeoutSeconds: Double = 6.0,
    @SerialName("avatar_state") val avatarState: AvatarStateDto? = null,
)

@Serializable
data class HandoffCompletePayload(
    @SerialName("handoff_id") val handoffId: String,
    @SerialName("previous_owner") val previousOwner: String,
    @SerialName("new_owner") val newOwner: String,
    val edge: String = "right",
    @SerialName("normalized_y") val normalizedY: Double = 0.5,
    @SerialName("confirmation_source") val confirmationSource: String? = null,
    @SerialName("avatar_state") val avatarState: AvatarStateDto,
)

@Serializable
data class HandoffCancelPayload(
    @SerialName("handoff_id") val handoffId: String,
    val reason: String,
    @SerialName("restored_owner") val restoredOwner: String? = null,
    @SerialName("avatar_state") val avatarState: AvatarStateDto,
)

@Serializable
data class HubErrorPayload(
    val code: String,
    val message: String,
    @SerialName("ref_message_id") val refMessageId: String? = null,
)

object ProtocolBuilder {
    fun registerDevice(
        deviceId: String,
        deviceName: String,
        screenWidth: Int,
        screenHeight: Int,
        capabilities: List<String> = listOf("touch_drag", "touch_confirm"),
    ): WireEnvelope = WireEnvelope(
        type = "register_device",
        payload = buildJsonObject {
            put("device_id", deviceId)
            put("device_type", "android")
            put("device_name", deviceName)
            put("screen_width", screenWidth)
            put("screen_height", screenHeight)
            put(
                "capabilities",
                kotlinx.serialization.json.JsonArray(
                    capabilities.map { kotlinx.serialization.json.JsonPrimitive(it) }
                )
            )
        }
    )

    fun stateUpdate(
        deviceId: String,
        x: Double,
        y: Double,
        animation: String,
        state: String,
        facing: String,
    ): WireEnvelope = WireEnvelope(
        type = "state_update",
        payload = buildJsonObject {
            put("device_id", deviceId)
            put("position", buildJsonObject {
                put("x", x)
                put("y", y)
            })
            put("animation", animation)
            put("state", state)
            put("facing", facing)
        }
    )

    fun handoffRequest(
        sourceDeviceId: String,
        edge: String,
        normalizedY: Double,
        targetDeviceId: String? = null,
        idempotencyKey: String = UUID.randomUUID().toString(),
    ): WireEnvelope = WireEnvelope(
        type = "handoff_request",
        payload = buildJsonObject {
            put("source_device_id", sourceDeviceId)
            if (targetDeviceId != null) {
                put("target_device_id", targetDeviceId)
            }
            put("edge", edge)
            put("normalized_y", normalizedY.coerceIn(0.0, 1.0))
            put("idempotency_key", idempotencyKey)
        }
    )

    fun handoffConfirm(
        handoffId: String,
        deviceId: String,
        accepted: Boolean,
        confirmationSource: String = "touch",
        landingX: Double? = null,
        landingY: Double? = null,
    ): WireEnvelope = WireEnvelope(
        type = "handoff_confirm",
        payload = buildJsonObject {
            put("handoff_id", handoffId)
            put("device_id", deviceId)
            put("accepted", accepted)
            put("confirmation_source", confirmationSource)
            if (landingX != null && landingY != null) {
                put("landing_position", buildJsonObject {
                    put("x", landingX)
                    put("y", landingY)
                })
            }
        }
    )

    fun ping(): WireEnvelope = WireEnvelope(
        type = "ping",
        payload = buildJsonObject {
            put("sent_at", System.currentTimeMillis() / 1000.0)
        }
    )
}
