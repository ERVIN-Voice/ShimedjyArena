package com.gcs.shimeji.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.FilterQuality
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.gcs.shimeji.model.ConnectionStatus
import com.gcs.shimeji.sprite.PixelSpriteSheet
import com.gcs.shimeji.viewmodel.ShimejiViewModel
import kotlinx.coroutines.delay
import kotlin.math.roundToInt

private val BgDark = Color(0xFF0F1420)
private val CardDark = Color(0xFF182032)
private val AccentCyan = Color(0xFF56C6E0)
private val AccentMint = Color(0xFF2ECC71)
private val AccentWarn = Color(0xFFF39C12)
private val AccentRed = Color(0xFFE74C3C)

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ShimejiScreen(viewModel: ShimejiViewModel) {
    val state by viewModel.uiState.collectAsState()
    val context = LocalContext.current
    val density = LocalDensity.current

    val spriteSheet = remember(context) {
        PixelSpriteSheet.loadFromAssets(context)
    }

    // Advance pixel animation frames based on configured FPS
    var frameIndex by remember { mutableIntStateOf(0) }
    LaunchedEffect(state.animation) {
        frameIndex = 0
        val spec = spriteSheet.specFor(state.animation)
        val frameDelayMs = (1000f / spec.fps.coerceAtLeast(1f)).toLong()
        while (true) {
            delay(frameDelayMs)
            frameIndex = if (frameIndex + 1 < spec.frames) {
                frameIndex + 1
            } else if (spec.loop) {
                0
            } else {
                spec.frames - 1
            }
        }
    }

    val petDisplayDp = 128.dp
    val petDisplayPx = with(density) { petDisplayDp.toPx() }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(BgDark)
            .padding(14.dp),
    ) {
        // Header + Connection Status Pill
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Column {
                Text(
                    text = "GCS Shimeji",
                    color = Color.White,
                    fontSize = 20.sp,
                    fontWeight = FontWeight.Bold,
                    fontFamily = FontFamily.Monospace,
                )
                Text(
                    text = "Device: ${state.deviceId.take(18)}",
                    color = Color(0xFF8FA3BF),
                    fontSize = 11.sp,
                    fontFamily = FontFamily.Monospace,
                )
            }
            StatusBadge(state.connectionStatus)
        }

        Spacer(modifier = Modifier.height(10.dp))

        // Hub Connection Card
        Card(
            colors = CardDefaults.cardColors(containerColor = CardDark),
            shape = RoundedCornerShape(12.dp),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(modifier = Modifier.padding(10.dp)) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    OutlinedTextField(
                        value = state.hubAddressInput,
                        onValueChange = viewModel::onAddressChanged,
                        label = { Text("Hub IP:Port or QR URI", fontSize = 11.sp) },
                        singleLine = true,
                        modifier = Modifier.weight(1f),
                    )
                    Spacer(modifier = Modifier.width(8.dp))
                    if (state.connectionStatus == ConnectionStatus.CONNECTED ||
                        state.connectionStatus == ConnectionStatus.CONNECTING ||
                        state.connectionStatus == ConnectionStatus.RECONNECTING
                    ) {
                        OutlinedButton(onClick = viewModel::disconnectFromHub) {
                            Text("Stop")
                        }
                    } else {
                        Button(
                            onClick = { viewModel.connectToHub() },
                            colors = ButtonDefaults.buttonColors(containerColor = AccentCyan),
                        ) {
                            Text("Connect", color = BgDark, fontWeight = FontWeight.Bold)
                        }
                    }
                }

                if (state.discoveredHubs.isNotEmpty()) {
                    Spacer(modifier = Modifier.height(6.dp))
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        state.discoveredHubs.forEach { hub ->
                            AssistChip(
                                onClick = { viewModel.connectToHub(hub.wsUrl) },
                                label = { Text("LAN: ${hub.host}:${hub.port}", fontSize = 11.sp) },
                            )
                        }
                    }
                }

                if (state.statusBanner != null) {
                    Spacer(modifier = Modifier.height(6.dp))
                    Text(
                        text = state.statusBanner!!,
                        color = AccentCyan,
                        fontSize = 12.sp,
                        fontFamily = FontFamily.Monospace,
                    )
                }
            }
        }

        Spacer(modifier = Modifier.height(10.dp))

        // Main Interactive Pet Stage
        Box(
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth()
                .clip(RoundedCornerShape(16.dp))
                .background(Color(0xFF131A2A))
                .border(
                    width = 2.dp,
                    color = when {
                        state.incomingOffer != null -> AccentMint
                        state.avatarState == "DRAGGING" -> AccentCyan
                        else -> Color(0xFF26334D)
                    },
                    shape = RoundedCornerShape(16.dp),
                )
                .onSizeChanged { size ->
                    viewModel.updateCanvasMetrics(
                        widthPx = size.width.toFloat(),
                        heightPx = size.height.toFloat(),
                        petSizePx = petDisplayPx,
                    )
                },
        ) {
            // Subtle edge transfer hint when dragging
            if (state.avatarState == "DRAGGING" && state.hasOnlinePeer) {
                Text(
                    text = "Drag to screen edge to transfer to Windows",
                    color = AccentCyan.copy(alpha = 0.8f),
                    fontSize = 12.sp,
                    fontFamily = FontFamily.Monospace,
                    modifier = Modifier
                        .align(Alignment.TopCenter)
                        .padding(top = 12.dp),
                )
            }

            // Pixel Pet Sprite (nearest-neighbor FilterQuality.None)
            if (state.isPetVisible) {
                val frameBitmap = spriteSheet.getFrame(
                    animation = state.animation,
                    frameIndex = frameIndex,
                    facingLeft = state.facingLeft,
                )

                Canvas(
                    modifier = Modifier
                        .offset {
                            IntOffset(
                                x = state.petX.roundToInt(),
                                y = state.petY.roundToInt(),
                            )
                        }
                        .size(petDisplayDp)
                        .pointerInput(state.isOwner, state.outgoingHandoffId) {
                            detectTapGestures(onTap = { viewModel.onPetTapped() })
                        }
                        .pointerInput(state.isOwner, state.outgoingHandoffId) {
                            detectDragGestures(
                                onDragStart = { viewModel.onPetDragStart() },
                                onDragEnd = { viewModel.onPetDragEnd() },
                                onDragCancel = { viewModel.onPetDragEnd() },
                                onDrag = { change, dragAmount ->
                                    change.consume()
                                    viewModel.onPetDragDelta(dragAmount.x, dragAmount.y)
                                },
                            )
                        },
                ) {
                    drawImage(
                        image = frameBitmap,
                        dstSize = IntSize(size.width.roundToInt(), size.height.roundToInt()),
                        filterQuality = FilterQuality.None, // Strict pixel-art nearest-neighbor!
                    )
                }
            } else if (state.incomingOffer == null) {
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    modifier = Modifier
                        .align(Alignment.Center)
                        .padding(24.dp),
                ) {
                    Text(
                        text = when {
                            state.outgoingHandoffId != null -> "Transferring pet..."
                            state.connectionStatus != ConnectionStatus.CONNECTED -> "Connect to GCS Local Hub"
                            state.ownerId != null -> "Pet is currently on ${state.ownerId}"
                            else -> "Waiting for Hub state..."
                        },
                        color = Color(0xFFB5C7DF),
                        fontSize = 15.sp,
                        fontWeight = FontWeight.SemiBold,
                        fontFamily = FontFamily.Monospace,
                    )
                    Spacer(modifier = Modifier.height(6.dp))
                    Text(
                        text = "Drag the pet to your Windows monitor edge to send it here.",
                        color = Color(0xFF7B8FA8),
                        fontSize = 12.sp,
                    )
                }
            }

            // Incoming Handoff Confirmation Overlay (Tap to Confirm)
            val offer = state.incomingOffer
            if (offer != null) {
                val progress = (state.offerRemainingSeconds / offer.timeoutSeconds.toFloat().coerceAtLeast(1f))
                    .coerceIn(0f, 1f)
                Card(
                    colors = CardDefaults.cardColors(containerColor = Color(0xEE102420)),
                    shape = RoundedCornerShape(16.dp),
                    modifier = Modifier
                        .align(Alignment.Center)
                        .padding(20.dp)
                        .fillMaxWidth()
                        .border(2.dp, AccentMint, RoundedCornerShape(16.dp))
                        .clickable { viewModel.acceptIncomingHandoff() },
                ) {
                    Column(
                        horizontalAlignment = Alignment.CenterHorizontally,
                        modifier = Modifier.padding(18.dp),
                    ) {
                        Text(
                            text = "Incoming Pixel Companion!",
                            color = AccentMint,
                            fontSize = 18.sp,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace,
                        )
                        Spacer(modifier = Modifier.height(6.dp))
                        Text(
                            text = "From ${offer.sourceDeviceName} (${offer.edge} edge) — ${String.format("%.1f", state.offerRemainingSeconds)}s left",
                            color = Color.White,
                            fontSize = 13.sp,
                        )
                        Spacer(modifier = Modifier.height(10.dp))
                        LinearProgressIndicator(
                            progress = { progress },
                            color = AccentMint,
                            trackColor = Color(0xFF234238),
                            modifier = Modifier.fillMaxWidth(),
                        )
                        Spacer(modifier = Modifier.height(14.dp))
                        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            Button(
                                onClick = viewModel::acceptIncomingHandoff,
                                colors = ButtonDefaults.buttonColors(containerColor = AccentMint),
                            ) {
                                Text("Tap to Receive", color = BgDark, fontWeight = FontWeight.Bold)
                            }
                            OutlinedButton(onClick = viewModel::declineIncomingHandoff) {
                                Text("Decline", color = Color.White)
                            }
                        }
                    }
                }
            }
        }

        Spacer(modifier = Modifier.height(10.dp))

        // Bottom Controls Bar
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = "Peers online: ${state.devices.count { it.connected && it.deviceId != state.deviceId }} | Anim: ${state.animation}",
                color = Color(0xFF93A8C4),
                fontSize = 12.sp,
                fontFamily = FontFamily.Monospace,
            )
            Button(
                onClick = { viewModel.initiateHandoff(edge = "right", normalizedY = 0.5) },
                enabled = state.isOwner && state.isPetVisible && state.hasOnlinePeer && state.outgoingHandoffId == null,
                colors = ButtonDefaults.buttonColors(containerColor = AccentCyan),
            ) {
                Text("Send to Windows", color = BgDark, fontWeight = FontWeight.Bold)
            }
        }
    }
}

@Composable
private fun StatusBadge(status: ConnectionStatus) {
    val (label, color) = when (status) {
        ConnectionStatus.CONNECTED -> "CONNECTED" to AccentMint
        ConnectionStatus.CONNECTING -> "CONNECTING" to AccentWarn
        ConnectionStatus.RECONNECTING -> "RECONNECTING" to AccentWarn
        ConnectionStatus.DISCONNECTED -> "OFFLINE" to AccentRed
    }
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .clip(RoundedCornerShape(20.dp))
            .background(color.copy(alpha = 0.16f))
            .padding(horizontal = 10.dp, vertical = 5.dp),
    ) {
        Box(
            modifier = Modifier
                .size(8.dp)
                .clip(CircleShape)
                .background(color),
        )
        Spacer(modifier = Modifier.width(6.dp))
        Text(
            text = label,
            color = color,
            fontSize = 11.sp,
            fontWeight = FontWeight.Bold,
            fontFamily = FontFamily.Monospace,
        )
    }
}
