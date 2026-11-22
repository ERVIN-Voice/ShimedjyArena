package com.gcs.shimeji.viewmodel

import android.app.Application
import android.os.Build
import android.provider.Settings
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.gcs.shimeji.camera.AndroidHandoffGestureGate
import com.gcs.shimeji.model.AvatarStateDto
import com.gcs.shimeji.model.ConnectionStatus
import com.gcs.shimeji.model.DeviceListPayload
import com.gcs.shimeji.model.DeviceSummaryDto
import com.gcs.shimeji.model.GcsJson
import com.gcs.shimeji.model.HandoffCancelPayload
import com.gcs.shimeji.model.HandoffCompletePayload
import com.gcs.shimeji.model.HandoffOfferPayload
import com.gcs.shimeji.model.HandoffPendingPayload
import com.gcs.shimeji.model.HubErrorPayload
import com.gcs.shimeji.model.ProtocolBuilder
import com.gcs.shimeji.model.RegisterAckPayload
import com.gcs.shimeji.model.WireEnvelope
import com.gcs.shimeji.network.DiscoveredHub
import com.gcs.shimeji.network.GcsWebSocketClient
import com.gcs.shimeji.network.MdnsHubDiscovery
import com.gcs.shimeji.network.PairingUriParser
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.json.decodeFromJsonElement
import java.util.UUID

data class ShimejiUiState(
    val deviceId: String = "",
    val hubAddressInput: String = "192.168.1.100:8765",
    val connectionStatus: ConnectionStatus = ConnectionStatus.DISCONNECTED,
    val discoveredHubs: List<DiscoveredHub> = emptyList(),
    val devices: List<DeviceSummaryDto> = emptyList(),
    val ownerId: String? = null,
    val isOwner: Boolean = false,
    val isPetVisible: Boolean = false,
    val petX: Float = 260f,
    val petY: Float = 520f,
    val animation: String = "idle",
    val avatarState: String = "ACTIVE",
    val facingLeft: Boolean = false,
    val outgoingHandoffId: String? = null,
    val incomingOffer: HandoffOfferPayload? = null,
    val offerRemainingSeconds: Float = 0f,
    val statusBanner: String? = null,
) {
    val hasOnlinePeer: Boolean
        get() = devices.any { it.connected && it.deviceId != deviceId }
}

class ShimejiViewModel(application: Application) : AndroidViewModel(application) {
    companion object {
        private const val TAG = "ShimejiViewModel"
        private const val EDGE_MARGIN_PX = 28f
        private const val STATE_SYNC_THROTTLE_MS = 80L
    }

    private val deviceId: String = run {
        val raw = runCatching {
            Settings.Secure.getString(application.contentResolver, Settings.Secure.ANDROID_ID)
        }.getOrNull()
        if (!raw.isNullOrBlank()) "android-$raw" else "android-${UUID.randomUUID().toString().take(8)}"
    }

    private val deviceName: String = "${Build.MANUFACTURER} ${Build.MODEL}".trim().ifEmpty { "Android Device" }
    private val displayMetrics = application.resources.displayMetrics

    private val wsClient = GcsWebSocketClient(
        deviceId = deviceId,
        deviceName = deviceName,
        screenWidth = displayMetrics.widthPixels,
        screenHeight = displayMetrics.heightPixels,
        scope = viewModelScope,
    )

    private val mdnsDiscovery = MdnsHubDiscovery(application)

    val gestureGate = AndroidHandoffGestureGate { handoffId ->
        confirmIncomingHandoff(handoffId, accepted = true, source = "gesture")
    }

    private val _uiState = MutableStateFlow(ShimejiUiState(deviceId = deviceId))
    val uiState: StateFlow<ShimejiUiState> = _uiState.asStateFlow()

    private var canvasWidthPx: Float = displayMetrics.widthPixels.toFloat()
    private var canvasHeightPx: Float = (displayMetrics.heightPixels * 0.65f)
    private var petSizePx: Float = 128f * displayMetrics.density
    private var lastSyncSentAtMs: Long = 0L
    private var offerCountdownJob: Job? = null
    private var edgeHandoffRequestedDuringDrag: Boolean = false

    init {
        viewModelScope.launch {
            wsClient.status.collect { status ->
                _uiState.update { cur ->
                    val lostConn = status == ConnectionStatus.DISCONNECTED
                    cur.copy(
                        connectionStatus = status,
                        incomingOffer = if (lostConn) null else cur.incomingOffer,
                        outgoingHandoffId = if (lostConn) null else cur.outgoingHandoffId,
                    )
                }
            }
        }
        viewModelScope.launch {
            wsClient.lastError.collect { err ->
                if (err != null) {
                    _uiState.update { it.copy(statusBanner = "Connection: $err") }
                }
            }
        }
        viewModelScope.launch {
            wsClient.incoming.collect { envelope ->
                handleHubMessage(envelope)
            }
        }
        viewModelScope.launch {
            mdnsDiscovery.hubs.collect { hubs ->
                _uiState.update { it.copy(discoveredHubs = hubs) }
            }
        }
        mdnsDiscovery.startDiscovery()
    }

    fun updateCanvasMetrics(widthPx: Float, heightPx: Float, petSizePx: Float) {
        if (widthPx > 0f && heightPx > 0f) {
            this.canvasWidthPx = widthPx
            this.canvasHeightPx = heightPx
            this.petSizePx = petSizePx
            _uiState.update { cur ->
                cur.copy(
                    petX = cur.petX.coerceIn(0f, (widthPx - petSizePx).coerceAtLeast(0f)),
                    petY = cur.petY.coerceIn(0f, (heightPx - petSizePx).coerceAtLeast(0f)),
                )
            }
        }
    }

    fun onAddressChanged(newAddress: String) {
        _uiState.update { it.copy(hubAddressInput = newAddress) }
    }

    fun connectToHub(addressOverride: String? = null) {
        val raw = (addressOverride ?: _uiState.value.hubAddressInput).trim()
        val parsed = PairingUriParser.parseToWebSocketUrl(raw)
        if (parsed == null) {
            _uiState.update { it.copy(statusBanner = "Invalid Hub address or QR URI") }
            return
        }
        _uiState.update { it.copy(hubAddressInput = parsed, statusBanner = "Connecting to $parsed...") }
        wsClient.connect(parsed)
    }

    fun disconnectFromHub() {
        offerCountdownJob?.cancel()
        gestureGate.setPendingOffer(null)
        wsClient.disconnect()
        _uiState.update { it.copy(statusBanner = "Disconnected") }
    }

    fun onPetDragStart() {
        val state = _uiState.value
        if (!state.isOwner || !state.isPetVisible || state.outgoingHandoffId != null) return
        edgeHandoffRequestedDuringDrag = false
        _uiState.update { it.copy(animation = "grab", avatarState = "DRAGGING") }
        sendStateUpdate(force = true)
    }

    fun onPetDragDelta(dx: Float, dy: Float) {
        val state = _uiState.value
        if (!state.isOwner || !state.isPetVisible || state.outgoingHandoffId != null) return

        val maxX = (canvasWidthPx - petSizePx).coerceAtLeast(0f)
        val maxY = (canvasHeightPx - petSizePx).coerceAtLeast(0f)
        val newX = (state.petX + dx).coerceIn(0f, maxX)
        val newY = (state.petY + dy).coerceIn(0f, maxY)
        val facingLeft = when {
            dx < -1.5f -> true
            dx > 1.5f -> false
            else -> state.facingLeft
        }

        _uiState.update {
            it.copy(
                petX = newX,
                petY = newY,
                facingLeft = facingLeft,
                animation = "grab",
                avatarState = "DRAGGING",
            )
        }
        sendStateUpdate(force = false)

        // Detect drag to edge to initiate Android -> Windows two-phase handoff
        if (!edgeHandoffRequestedDuringDrag && state.hasOnlinePeer) {
            val edge = when {
                newX <= EDGE_MARGIN_PX -> "left"
                newX >= maxX - EDGE_MARGIN_PX -> "right"
                newY <= EDGE_MARGIN_PX -> "top"
                else -> null
            }
            if (edge != null) {
                edgeHandoffRequestedDuringDrag = true
                val normalizedY = ((newY + petSizePx * 0.5f) / canvasHeightPx.coerceAtLeast(1f)).toDouble()
                initiateHandoff(edge = edge, normalizedY = normalizedY)
            }
        }
    }

    fun onPetDragEnd() {
        val state = _uiState.value
        if (!state.isOwner || state.outgoingHandoffId != null) return
        _uiState.update { it.copy(animation = "landing", avatarState = "ACTIVE") }
        sendStateUpdate(force = true)
        viewModelScope.launch {
            delay(420)
            _uiState.update { cur ->
                if (cur.isOwner && cur.animation == "landing") cur.copy(animation = "idle") else cur
            }
        }
    }

    fun onPetTapped() {
        val state = _uiState.value
        if (!state.isOwner || !state.isPetVisible || state.outgoingHandoffId != null) return
        _uiState.update { it.copy(animation = "jump", avatarState = "ACTIVE") }
        sendStateUpdate(force = true)
        viewModelScope.launch {
            delay(500)
            _uiState.update { cur ->
                if (cur.isOwner && cur.animation == "jump") cur.copy(animation = "idle") else cur
            }
        }
    }

    fun initiateHandoff(edge: String = "right", normalizedY: Double = 0.5) {
        val state = _uiState.value
        if (!state.isOwner || state.outgoingHandoffId != null) return
        if (!state.hasOnlinePeer) {
            _uiState.update { it.copy(statusBanner = "No Windows/peer device connected for handoff") }
            return
        }
        val req = ProtocolBuilder.handoffRequest(
            sourceDeviceId = deviceId,
            edge = edge,
            normalizedY = normalizedY,
        )
        wsClient.send(req)
        _uiState.update {
            it.copy(
                animation = "handoff",
                avatarState = "HANDOFF_PENDING",
                statusBanner = "Transferring pet to Windows... waiting for confirmation",
            )
        }
    }

    fun acceptIncomingHandoff() {
        val offer = _uiState.value.incomingOffer ?: return
        confirmIncomingHandoff(offer.handoffId, accepted = true, source = "touch")
    }

    fun declineIncomingHandoff() {
        val offer = _uiState.value.incomingOffer ?: return
        confirmIncomingHandoff(offer.handoffId, accepted = false, source = "touch")
    }

    private fun confirmIncomingHandoff(handoffId: String, accepted: Boolean, source: String) {
        val offer = _uiState.value.incomingOffer
        val spawnPos = computeArrivalPosition(offer?.edge ?: "right", offer?.normalizedY ?: 0.5)
        wsClient.send(
            ProtocolBuilder.handoffConfirm(
                handoffId = handoffId,
                deviceId = deviceId,
                accepted = accepted,
                confirmationSource = source,
                landingX = spawnPos.first.toDouble(),
                landingY = spawnPos.second.toDouble(),
            )
        )
        offerCountdownJob?.cancel()
        gestureGate.setPendingOffer(null)
        _uiState.update {
            it.copy(
                incomingOffer = null,
                offerRemainingSeconds = 0f,
                statusBanner = if (accepted) "Receiving pet..." else "Handoff declined",
            )
        }
    }

    private fun computeArrivalPosition(edge: String, normalizedY: Double): Pair<Float, Float> {
        val maxX = (canvasWidthPx - petSizePx).coerceAtLeast(0f)
        val maxY = (canvasHeightPx - petSizePx).coerceAtLeast(0f)
        val y = (normalizedY.toFloat().coerceIn(0.1f, 0.9f) * maxY)
        val inset = (EDGE_MARGIN_PX + 24f).coerceAtMost(maxX / 2f)
        val x = when (edge) {
            "right" -> inset
            "left" -> (maxX - inset).coerceAtLeast(0f)
            else -> maxX / 2f
        }
        return x to y
    }

    private fun sendStateUpdate(force: Boolean) {
        val now = System.currentTimeMillis()
        if (!force && (now - lastSyncSentAtMs) < STATE_SYNC_THROTTLE_MS) return
        lastSyncSentAtMs = now
        val cur = _uiState.value
        if (!cur.isOwner || cur.outgoingHandoffId != null) return
        wsClient.send(
            ProtocolBuilder.stateUpdate(
                deviceId = deviceId,
                x = cur.petX.toDouble(),
                y = cur.petY.toDouble(),
                animation = cur.animation,
                state = cur.avatarState,
                facing = if (cur.facingLeft) "left" else "right",
            )
        )
    }

    private fun handleHubMessage(envelope: WireEnvelope) {
        runCatching {
            when (envelope.type) {
                "register_ack" -> {
                    val ack = GcsJson.decodeFromJsonElement<RegisterAckPayload>(envelope.payload)
                    applyAuthoritativeState(ack.avatarState, devices = ack.devices)
                    _uiState.update { it.copy(statusBanner = "Connected to GCS Local Hub") }
                }
                "device_list" -> {
                    val list = GcsJson.decodeFromJsonElement<DeviceListPayload>(envelope.payload)
                    _uiState.update { it.copy(devices = list.devices) }
                }
                "state_sync" -> {
                    val sync = GcsJson.decodeFromJsonElement<AvatarStateDto>(envelope.payload)
                    applyAuthoritativeState(sync)
                }
                "handoff_pending" -> {
                    val pending = GcsJson.decodeFromJsonElement<HandoffPendingPayload>(envelope.payload)
                    if (pending.sourceDeviceId == deviceId) {
                        // Step 4: Temporarily hide avatar while awaiting target confirmation
                        _uiState.update {
                            it.copy(
                                outgoingHandoffId = pending.handoffId,
                                isPetVisible = false,
                                avatarState = "HANDOFF_PENDING",
                                statusBanner = "Waiting for confirmation (${pending.timeoutSeconds.toInt()}s)...",
                            )
                        }
                    }
                }
                "handoff_offer" -> {
                    val offer = GcsJson.decodeFromJsonElement<HandoffOfferPayload>(envelope.payload)
                    gestureGate.setPendingOffer(offer.handoffId)
                    startOfferCountdown(offer)
                    _uiState.update {
                        it.copy(
                            incomingOffer = offer,
                            offerRemainingSeconds = offer.timeoutSeconds.toFloat(),
                            statusBanner = "Incoming pet from ${offer.sourceDeviceName}! Tap to receive.",
                        )
                    }
                }
                "handoff_complete" -> {
                    val comp = GcsJson.decodeFromJsonElement<HandoffCompletePayload>(envelope.payload)
                    offerCountdownJob?.cancel()
                    gestureGate.setPendingOffer(null)
                    val weAreNewOwner = comp.newOwner == deviceId
                    val (spawnX, spawnY) = computeArrivalPosition(comp.edge, comp.normalizedY)
                    _uiState.update { cur ->
                        cur.copy(
                            ownerId = comp.newOwner,
                            isOwner = weAreNewOwner,
                            isPetVisible = weAreNewOwner,
                            outgoingHandoffId = null,
                            incomingOffer = null,
                            offerRemainingSeconds = 0f,
                            petX = if (weAreNewOwner) spawnX else cur.petX,
                            petY = if (weAreNewOwner) spawnY else cur.petY,
                            animation = if (weAreNewOwner) "receive" else comp.avatarState.animation,
                            avatarState = "ACTIVE",
                            statusBanner = if (weAreNewOwner) "Pet arrived on Android!" else "Pet moved to ${comp.newOwner}",
                        )
                    }
                    if (weAreNewOwner) {
                        viewModelScope.launch {
                            delay(650)
                            _uiState.update { cur ->
                                if (cur.isOwner && cur.animation == "receive") cur.copy(animation = "idle") else cur
                            }
                        }
                    }
                }
                "handoff_cancel" -> {
                    val cancel = GcsJson.decodeFromJsonElement<HandoffCancelPayload>(envelope.payload)
                    offerCountdownJob?.cancel()
                    gestureGate.setPendingOffer(null)
                    val restoredToUs = cancel.restoredOwner == deviceId
                    _uiState.update { cur ->
                        cur.copy(
                            ownerId = cancel.restoredOwner,
                            isOwner = restoredToUs,
                            isPetVisible = restoredToUs,
                            outgoingHandoffId = null,
                            incomingOffer = null,
                            offerRemainingSeconds = 0f,
                            animation = if (restoredToUs) "landing" else cancel.avatarState.animation,
                            avatarState = cancel.avatarState.state,
                            statusBanner = "Handoff cancelled (${cancel.reason})",
                        )
                    }
                }
                "error" -> {
                    val err = GcsJson.decodeFromJsonElement<HubErrorPayload>(envelope.payload)
                    _uiState.update { it.copy(statusBanner = "Hub: ${err.message}") }
                }
            }
        }.onFailure { ex ->
            Log.w(TAG, "Error handling '${envelope.type}': ${ex.message}")
        }
    }

    private fun applyAuthoritativeState(
        dto: AvatarStateDto,
        devices: List<DeviceSummaryDto>? = null,
    ) {
        val weOwn = dto.owner == deviceId
        val hiddenForTransfer = dto.state == "HANDOFF_PENDING" && weOwn
        _uiState.update { cur ->
            cur.copy(
                devices = devices ?: cur.devices,
                ownerId = dto.owner,
                isOwner = weOwn,
                isPetVisible = weOwn && !hiddenForTransfer,
                outgoingHandoffId = if (hiddenForTransfer) dto.handoffId else null,
                animation = if (weOwn && cur.avatarState == "DRAGGING") cur.animation else dto.animation,
                avatarState = dto.state,
                facingLeft = dto.facing == "left",
            )
        }
    }

    private fun startOfferCountdown(offer: HandoffOfferPayload) {
        offerCountdownJob?.cancel()
        offerCountdownJob = viewModelScope.launch {
            while (isActive) {
                val remaining = (offer.expiresAt - (System.currentTimeMillis() / 1000.0)).toFloat().coerceAtLeast(0f)
                _uiState.update { it.copy(offerRemainingSeconds = remaining) }
                if (remaining <= 0f) {
                    gestureGate.setPendingOffer(null)
                    _uiState.update { it.copy(incomingOffer = null) }
                    break
                }
                delay(100)
            }
        }
    }

    override fun onCleared() {
        super.onCleared()
        offerCountdownJob?.cancel()
        mdnsDiscovery.stopDiscovery()
        wsClient.release()
    }
}
