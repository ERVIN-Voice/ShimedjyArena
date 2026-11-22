package com.gcs.shimeji.handcam

import kotlin.math.hypot

data class Landmark2D(val x: Float, val y: Float)

enum class HandGesture(val russianLabel: String) {
    NONE("Рука не в кадре"),
    OPEN_PALM("✋ Ладонь"),
    PINCH("🤏 Щипок (хватает)"),
    POINT("☝️ Указательный (зовёт)"),
    PEACE("✌️ Мир (веселье!)"),
    FIST("✊ Кулак (спать)"),
}

data class HandGestureFrame(
    val detected: Boolean,
    val gesture: HandGesture,
    val palmX: Float = 0.5f,
    val palmY: Float = 0.5f,
    val velocityX: Float = 0f,
    val velocityY: Float = 0f,
    val extendedFingers: Int = 0,
    val timestampMs: Long = 0L,
)

/**
 * Pure-Kotlin 21-point hand landmark gesture classifier with EMA smoothing,
 * palm velocity estimation, and temporal debounce.
 *
 * Landmark indices follow MediaPipe Hand Landmarker (0=wrist, 4=thumb tip,
 * 8=index tip, 12=middle tip, 16=ring tip, 20=pinky tip).
 */
class HandGestureClassifier(
    private val holdFrames: Int = 2,
    private val smoothAlpha: Float = 0.42f,
) {
    private var smoothedX: Float? = null
    private var smoothedY: Float? = null
    private var lastTimestampMs: Long = 0L

    private var candidateGesture: HandGesture = HandGesture.NONE
    private var candidateCount: Int = 0
    private var stableGesture: HandGesture = HandGesture.NONE

    fun reset() {
        smoothedX = null
        smoothedY = null
        lastTimestampMs = 0L
        candidateGesture = HandGesture.NONE
        candidateCount = 0
        stableGesture = HandGesture.NONE
    }

    fun processLandmarks(
        landmarks: List<Landmark2D>?,
        timestampMs: Long,
        mirrorX: Boolean = true,
    ): HandGestureFrame {
        if (landmarks == null || landmarks.size < 21) {
            candidateGesture = HandGesture.NONE
            candidateCount = 0
            stableGesture = HandGesture.NONE
            smoothedX = null
            smoothedY = null
            lastTimestampMs = timestampMs
            return HandGestureFrame(
                detected = false,
                gesture = HandGesture.NONE,
                timestampMs = timestampMs,
            )
        }

        val pts = if (mirrorX) {
            landmarks.map { Landmark2D(1f - it.x, it.y) }
        } else {
            landmarks
        }

        val wrist = pts[0]
        val middleMcp = pts[9]
        val palmScale = hypot(middleMcp.x - wrist.x, middleMcp.y - wrist.y).coerceAtLeast(0.03f)

        // Palm center = average of wrist & four knuckle MCPs (0, 5, 9, 13, 17)
        val rawPalmX = ((pts[0].x + pts[5].x + pts[9].x + pts[13].x + pts[17].x) / 5f).coerceIn(0f, 1f)
        val rawPalmY = ((pts[0].y + pts[5].y + pts[9].y + pts[13].y + pts[17].y) / 5f).coerceIn(0f, 1f)

        val prevX = smoothedX
        val prevY = smoothedY
        val curX = if (prevX == null) rawPalmX else prevX + (rawPalmX - prevX) * smoothAlpha
        val curY = if (prevY == null) rawPalmY else prevY + (rawPalmY - prevY) * smoothAlpha

        var vx = 0f
        var vy = 0f
        if (prevX != null && prevY != null && lastTimestampMs > 0L && timestampMs > lastTimestampMs) {
            val dtSec = ((timestampMs - lastTimestampMs) / 1000f).coerceIn(0.01f, 0.25f)
            vx = (curX - prevX) / dtSec
            vy = (curY - prevY) / dtSec
        }
        smoothedX = curX
        smoothedY = curY
        lastTimestampMs = timestampMs

        val (rawGesture, extendedCount) = classifyPose(pts, palmScale)

        // Temporal debounce so transient frames don't flicker
        if (rawGesture == candidateGesture) {
            candidateCount += 1
        } else {
            candidateGesture = rawGesture
            candidateCount = 1
        }
        if (candidateCount >= holdFrames) {
            stableGesture = candidateGesture
        }

        return HandGestureFrame(
            detected = true,
            gesture = stableGesture,
            palmX = curX,
            palmY = curY,
            velocityX = vx,
            velocityY = vy,
            extendedFingers = extendedCount,
            timestampMs = timestampMs,
        )
    }

    companion object {
        internal fun classifyPose(pts: List<Landmark2D>, palmScale: Float): Pair<HandGesture, Int> {
            val wrist = pts[0]
            fun dist(a: Landmark2D, b: Landmark2D): Float = hypot(a.x - b.x, a.y - b.y)

            val indexExt = dist(pts[8], wrist) > dist(pts[6], wrist) * 1.12f
            val middleExt = dist(pts[12], wrist) > dist(pts[10], wrist) * 1.12f
            val ringExt = dist(pts[16], wrist) > dist(pts[14], wrist) * 1.12f
            val pinkyExt = dist(pts[20], wrist) > dist(pts[18], wrist) * 1.12f
            val thumbExt = dist(pts[4], pts[17]) > dist(pts[3], pts[17]) * 1.08f

            val fourExtended = listOf(indexExt, middleExt, ringExt, pinkyExt).count { it }
            val totalExtended = fourExtended + (if (thumbExt) 1 else 0)

            val pinchRatio = dist(pts[4], pts[8]) / palmScale
            val avgTipDist = (
                dist(pts[8], wrist) +
                    dist(pts[12], wrist) +
                    dist(pts[16], wrist) +
                    dist(pts[20], wrist)
                ) / (4f * palmScale)

            // CRITICAL: Classify FIST *before* PINCH because thumb & curled index are close in a fist!
            if (fourExtended == 0 && avgTipDist < 1.38f) {
                return HandGesture.FIST to totalExtended
            }

            // PINCH: thumb tip and index tip touching while hand is not a closed fist
            if (pinchRatio < 0.44f) {
                return HandGesture.PINCH to totalExtended
            }

            // PEACE / VICTORY: index & middle extended, ring & pinky curled
            if (indexExt && middleExt && !ringExt && !pinkyExt) {
                return HandGesture.PEACE to totalExtended
            }

            // POINT: only index extended
            if (indexExt && !middleExt && !ringExt && !pinkyExt) {
                return HandGesture.POINT to totalExtended
            }

            // OPEN PALM: 3+ main fingers extended (4-5 total)
            if (fourExtended >= 3 && avgTipDist >= 1.45f) {
                return HandGesture.OPEN_PALM to totalExtended
            }

            return HandGesture.NONE to totalExtended
        }
    }
}
