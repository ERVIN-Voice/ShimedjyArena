package com.gcs.shimeji.network

import android.util.Log
import com.gcs.shimeji.model.ConnectionStatus
import com.gcs.shimeji.model.GcsJson
import com.gcs.shimeji.model.ProtocolBuilder
import com.gcs.shimeji.model.WireEnvelope
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.encodeToString
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.concurrent.TimeUnit
import kotlin.math.min

/**
 * Resilient local-network WebSocket client connecting Android to GCS Local Hub.
 * Supports automatic device registration, heartbeats, and exponential-backoff reconnection.
 */
class GcsWebSocketClient(
    private val deviceId: String,
    private val deviceName: String,
    private val screenWidth: Int,
    private val screenHeight: Int,
    private val scope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.IO),
) {
    companion object {
        private const val TAG = "GcsWebSocketClient"
        private const val INITIAL_RECONNECT_MS = 1_000L
        private const val MAX_RECONNECT_MS = 15_000L
        private const val HEARTBEAT_INTERVAL_MS = 10_000L
    }

    private val okHttpClient = OkHttpClient.Builder()
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(15, TimeUnit.SECONDS)
        .build()

    @Volatile
    private var webSocket: WebSocket? = null

    @Volatile
    private var targetUrl: String? = null

    @Volatile
    private var shouldReconnect: Boolean = false

    private var reconnectAttempts: Int = 0
    private var reconnectJob: Job? = null
    private var heartbeatJob: Job? = null

    private val _status = MutableStateFlow(ConnectionStatus.DISCONNECTED)
    val status: StateFlow<ConnectionStatus> = _status.asStateFlow()

    private val _lastError = MutableStateFlow<String?>(null)
    val lastError: StateFlow<String?> = _lastError.asStateFlow()

    private val _incoming = MutableSharedFlow<WireEnvelope>(extraBufferCapacity = 64)
    val incoming: SharedFlow<WireEnvelope> = _incoming.asSharedFlow()

    fun connect(rawAddress: String): Boolean {
        val wsUrl = PairingUriParser.parseToWebSocketUrl(rawAddress) ?: return false
        disconnectInternal(allowReconnect = false)
        targetUrl = wsUrl
        shouldReconnect = true
        reconnectAttempts = 0
        _lastError.value = null
        openSocket(wsUrl)
        return true
    }

    fun disconnect() {
        shouldReconnect = false
        targetUrl = null
        disconnectInternal(allowReconnect = false)
        _status.value = ConnectionStatus.DISCONNECTED
    }

    fun send(envelope: WireEnvelope): Boolean {
        val ws = webSocket ?: return false
        val encoded = GcsJson.encodeToString(envelope)
        return ws.send(encoded)
    }

    private fun openSocket(url: String) {
        _status.value = if (reconnectAttempts == 0) {
            ConnectionStatus.CONNECTING
        } else {
            ConnectionStatus.RECONNECTING
        }

        val request = Request.Builder().url(url).build()
        webSocket = okHttpClient.newWebSocket(
            request,
            object : WebSocketListener() {
                override fun onOpen(ws: WebSocket, response: Response) {
                    if (ws !== webSocket) return
                    Log.i(TAG, "Connected to GCS Hub at $url")
                    reconnectAttempts = 0
                    _lastError.value = null
                    _status.value = ConnectionStatus.CONNECTED

                    val reg = ProtocolBuilder.registerDevice(
                        deviceId = deviceId,
                        deviceName = deviceName,
                        screenWidth = screenWidth,
                        screenHeight = screenHeight,
                    )
                    send(reg)
                    startHeartbeat()
                }

                override fun onMessage(ws: WebSocket, text: String) {
                    if (ws !== webSocket) return
                    runCatching {
                        GcsJson.decodeFromString(WireEnvelope.serializer(), text)
                    }.onSuccess { envelope ->
                        _incoming.tryEmit(envelope)
                    }.onFailure { err ->
                        Log.w(TAG, "Failed to parse Hub message: ${err.message}")
                    }
                }

                override fun onClosing(ws: WebSocket, code: Int, reason: String) {
                    ws.close(1000, null)
                }

                override fun onClosed(ws: WebSocket, code: Int, reason: String) {
                    if (ws !== webSocket) return
                    Log.i(TAG, "WebSocket closed: code=$code reason=$reason")
                    stopHeartbeat()
                    webSocket = null
                    scheduleReconnectIfNeeded()
                }

                override fun onFailure(ws: WebSocket, t: Throwable, response: Response?) {
                    if (ws !== webSocket) return
                    Log.w(TAG, "WebSocket failure: ${t.message}")
                    _lastError.value = t.message ?: "Connection failed"
                    stopHeartbeat()
                    webSocket = null
                    scheduleReconnectIfNeeded()
                }
            }
        )
    }

    private fun startHeartbeat() {
        stopHeartbeat()
        heartbeatJob = scope.launch {
            while (isActive && _status.value == ConnectionStatus.CONNECTED) {
                delay(HEARTBEAT_INTERVAL_MS)
                send(ProtocolBuilder.ping())
            }
        }
    }

    private fun stopHeartbeat() {
        heartbeatJob?.cancel()
        heartbeatJob = null
    }

    private fun scheduleReconnectIfNeeded() {
        val url = targetUrl
        if (!shouldReconnect || url == null) {
            _status.value = ConnectionStatus.DISCONNECTED
            return
        }
        reconnectJob?.cancel()
        _status.value = ConnectionStatus.RECONNECTING
        val delayMs = min(MAX_RECONNECT_MS, INITIAL_RECONNECT_MS shl min(reconnectAttempts, 4))
        reconnectAttempts += 1
        reconnectJob = scope.launch {
            delay(delayMs)
            if (shouldReconnect && targetUrl == url) {
                openSocket(url)
            }
        }
    }

    private fun disconnectInternal(allowReconnect: Boolean) {
        if (!allowReconnect) {
            reconnectJob?.cancel()
            reconnectJob = null
        }
        stopHeartbeat()
        webSocket?.close(1000, "Client disconnect")
        webSocket = null
    }

    fun release() {
        disconnect()
        okHttpClient.dispatcher.executorService.shutdown()
    }
}
