package com.gcs.shimeji.camera

/**
 * Detected 2D hand landmarks from an Android front/rear RGB camera (non-depth).
 * Coordinates are normalized to [0.0, 1.0].
 */
data class NormalizedHandFrame(
    val detected: Boolean,
    val palmCenterX: Float = 0.5f,
    val palmCenterY: Float = 0.5f,
    val pinchDistance: Float = 1.0f,
    val openPalmConfidence: Float = 0.0f,
    val timestampMs: Long = System.currentTimeMillis(),
)

enum class AndroidGestureType {
    NONE,
    PINCH,
    RECEIVE_PET,
}

/**
 * Interface for future Android CameraX + on-device hand-tracking backends.
 */
interface AndroidCameraGestureSource {
    val isDepthCamera: Boolean get() = false
    fun start(onGesture: (AndroidGestureType, Float) -> Unit)
    fun stop()
}

/**
 * Safety gate ensuring camera gestures ONLY confirm an already-pending incoming
 * handoff offer and NEVER initiate a handoff by themselves.
 */
class AndroidHandoffGestureGate(
    private val minConfidence: Float = 0.72f,
    private val onConfirmReceive: (handoffId: String) -> Unit,
) {
    @Volatile
    private var activeOfferHandoffId: String? = null

    fun setPendingOffer(handoffId: String?) {
        activeOfferHandoffId = handoffId
    }

    fun onGestureDetected(gesture: AndroidGestureType, confidence: Float): Boolean {
        val pendingId = activeOfferHandoffId ?: return false
        if (gesture != AndroidGestureType.RECEIVE_PET || confidence < minConfidence) {
            return false
        }
        activeOfferHandoffId = null
        onConfirmReceive(pendingId)
        return true
    }
}
