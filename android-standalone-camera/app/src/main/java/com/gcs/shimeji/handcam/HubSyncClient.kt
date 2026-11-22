package com.gcs.shimeji.handcam

import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.concurrent.TimeUnit

/**
 * Resilient LAN WebSocket client connecting Android Shimeji Hand Cam directly to the PC GCS Local Hub.
 * Supports automatic reconnect with exponential backoff, heartbeat ping/pong, and single-owner handoff.
 */
class HubSyncClient(
    private val deviceId: String,
    private val screenWidth: Int,
    private val screenHeight: Int,
    private val onEvent: (HubEvent) -> Unit,
    private val onConnectionStateChanged: (connected: Boolean, statusLabel: String) -> Unit,
) {
    private val mainHandler = Handler(Looper.getMainLooper())
    private val client = OkHttpClient.Builder()
        .pingInterval(10, TimeUnit.SECONDS)
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()

    @Volatile
    private var ws: WebSocket? = null

    @Volatile
    private var desiredUrl: String? = null

    @Volatile
    private var shouldConnect: Boolean = false

    @Volatile
    var isConnected: Boolean = false
        private set

    private var reconnectDelayMs: Long = 1500L
    private val reconnectRunnable = Runnable { openSocketInternal() }

    private val deviceName: String = "${Build.MANUFACTURER} ${Build.MODEL}".trim().ifBlank { "Android-Phone" }

    fun connect(rawUrl: String) {
        val parsed = HubProtocolCodec.parseHubUrl(rawUrl)
        if (parsed == null) {
            disconnect()
            onConnectionStateChanged(false, "Неверный адрес ПК (пример: 192.168.1.10:8765)")
            return
        }
        if (shouldConnect && parsed == desiredUrl && (isConnected || ws != null)) {
            return
        }
        desiredUrl = parsed
        shouldConnect = true
        reconnectDelayMs = 1500L
        mainHandler.removeCallbacks(reconnectRunnable)
        closeSocketOnly()
        openSocketInternal()
    }

    fun disconnect() {
        shouldConnect = false
        desiredUrl = null
        mainHandler.removeCallbacks(reconnectRunnable)
        closeSocketOnly()
        if (isConnected) {
            isConnected = false
        }
        onConnectionStateChanged(false, "Автономный режим (без ПК)")
    }

    private fun closeSocketOnly() {
        val cur = ws
        ws = null
        if (cur != null) {
            runCatching { cur.close(1000, "Client disconnect") }
        }
    }

    private fun openSocketInternal() {
        val url = desiredUrl ?: return
        if (!shouldConnect) return

        onConnectionStateChanged(false, "Подключение к ПК ($url)...")
        val request = try {
            Request.Builder().url(url).build()
        } catch (e: Exception) {
            Log.w(TAG, "Invalid WebSocket URL: $url", e)
            onConnectionStateChanged(false, "Ошибка адреса: ${e.message}")
            return
        }

        ws = client.newWebSocket(
            request,
            object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    if (ws !== webSocket) return
                    isConnected = true
                    reconnectDelayMs = 1500L
                    val regJson = HubProtocolCodec.buildRegisterDevice(
                        deviceId = deviceId,
                        deviceName = deviceName,
                        screenWidth = screenWidth,
                        screenHeight = screenHeight,
                    )
                    webSocket.send(regJson)
                    mainHandler.post {
                        onConnectionStateChanged(true, "Подключён к ПК ($url)")
                    }
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    if (ws !== webSocket) return
                    val event = HubProtocolCodec.parseIncoming(text)
                    if (event is HubEvent.Ping) {
                        webSocket.send(HubProtocolCodec.buildPong(event.sentAt))
                        return
                    }
                    mainHandler.post { onEvent(event) }
                }

                override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                    webSocket.close(1000, null)
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    handleSocketDrop(webSocket, "Связь с ПК закрыта")
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    handleSocketDrop(webSocket, "ПК недоступен (${t.localizedMessage ?: "ожидание"})")
                }
            },
        )
    }

    private fun handleSocketDrop(failedSocket: WebSocket, reason: String) {
        if (ws !== failedSocket) return
        ws = null
        isConnected = false
        mainHandler.post {
            if (shouldConnect) {
                onConnectionStateChanged(false, "$reason — переподключение...")
                mainHandler.removeCallbacks(reconnectRunnable)
                mainHandler.postDelayed(reconnectRunnable, reconnectDelayMs)
                reconnectDelayMs = (reconnectDelayMs * 17L / 10L).coerceAtMost(10_000L)
            } else {
                onConnectionStateChanged(false, "Автономный режим (без ПК)")
            }
        }
    }

    fun sendStateUpdate(
        x: Float,
        y: Float,
        animation: String,
        state: String,
        facing: String,
        emotion: PetEmotion,
    ): Boolean {
        val socket = ws ?: return false
        if (!isConnected) return false
        val msg = HubProtocolCodec.buildStateUpdate(
            deviceId = deviceId,
            x = x,
            y = y,
            animation = animation,
            state = state,
            facing = facing,
            emotion = HubProtocolCodec.petEmotionToProtocol(emotion),
        )
        return socket.send(msg)
    }

    fun sendHandoffRequest(edge: String, normalizedY: Float, targetDeviceId: String? = null): Boolean {
        val socket = ws ?: return false
        if (!isConnected) return false
        val msg = HubProtocolCodec.buildHandoffRequest(
            sourceDeviceId = deviceId,
            edge = edge,
            normalizedY = normalizedY,
            targetDeviceId = targetDeviceId,
        )
        return socket.send(msg)
    }

    fun sendHandoffConfirm(
        handoffId: String,
        accepted: Boolean,
        confirmationSource: String,
        landingX: Float? = null,
        landingY: Float? = null,
    ): Boolean {
        val socket = ws ?: return false
        if (!isConnected) return false
        val msg = HubProtocolCodec.buildHandoffConfirm(
            handoffId = handoffId,
            deviceId = deviceId,
            accepted = accepted,
            confirmationSource = confirmationSource,
            landingX = landingX,
            landingY = landingY,
        )
        return socket.send(msg)
    }

    companion object {
        private const val TAG = "HubSyncClient"
    }
}
