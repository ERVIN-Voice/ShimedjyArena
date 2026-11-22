package com.gcs.shimeji.overlay

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Rect
import android.graphics.RectF
import android.graphics.Typeface
import android.os.SystemClock
import android.view.MotionEvent
import android.view.View
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.sin

/**
 * Compact floating overlay view that draws the pixel companion with:
 * - Strict nearest-neighbor scaling (no blur)
 * - Funny tumble rotation + squash-and-stretch on impact
 * - Orbiting pixel dizzy stars when splatted/dizzy
 * - Tiny comic speech tag ("ААА!", "БУМ!", "@_@")
 * - Touch drag, fling, and double-tap jump (only within the pet's own floating window)
 */
@SuppressLint("ViewConstructor")
class OverlayPetView(
    context: Context,
    private val spriteSheet: PixelSpriteSheet,
    private val physics: OverlayPetPhysics,
    private val onTouchPositionChanged: () -> Unit,
) : View(context) {

    var paddingPx: Int = 44
        private set

    private val spritePaint = Paint().apply {
        isFilterBitmap = false
        isAntiAlias = false
        isDither = false
    }

    private val starPaint = Paint().apply {
        isAntiAlias = false
        style = Paint.Style.FILL
    }

    private val bubbleBgPaint = Paint().apply {
        isAntiAlias = true
        color = Color.argb(225, 16, 22, 36)
        style = Paint.Style.FILL
    }

    private val bubbleBorderPaint = Paint().apply {
        isAntiAlias = true
        color = Color.rgb(102, 252, 241)
        strokeWidth = 2.5f
        style = Paint.Style.STROKE
    }

    private val bubbleTextPaint = Paint().apply {
        isAntiAlias = true
        color = Color.WHITE
        textSize = 26f
        typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
        textAlign = Paint.Align.CENTER
    }

    private val srcRect = Rect()
    private val dstRect = RectF()
    private val bubbleRect = RectF()

    private var currentAnim: String = "idle"
    private var animFrame: Int = 0
    private var animAccumulator: Float = 0f

    // Touch gesture tracking
    private var isDragging = false
    private var downRawX = 0f
    private var downRawY = 0f
    private var lastRawX = 0f
    private var lastRawY = 0f
    private var lastMoveTimeMs = 0L
    private var lastTapTimeMs = 0L
    private var movedDistance = 0f

    fun updateSpritePixelSize(petSizePx: Int) {
        paddingPx = (petSizePx * 0.44f).toInt().coerceAtLeast(36)
        bubbleTextPaint.textSize = (petSizePx * 0.22f).coerceIn(22f, 34f)
    }

    fun advanceAnimation(dt: Float) {
        val targetAnim = physics.animationName
        if (targetAnim != currentAnim) {
            currentAnim = targetAnim
            animFrame = 0
            animAccumulator = 0f
        }
        val spec = spriteSheet.getSpec(currentAnim)
        val frameDur = 1f / spec.fps.coerceAtLeast(1f)
        animAccumulator += dt
        while (animAccumulator >= frameDur) {
            animAccumulator -= frameDur
            if (animFrame + 1 < spec.frames) {
                animFrame += 1
            } else if (spec.loop) {
                animFrame = 0
            }
        }
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val petSize = physics.petSizePx
        val cx = width * 0.5f
        val cy = height * 0.5f + paddingPx * 0.18f

        spriteSheet.fillSourceRect(currentAnim, animFrame, srcRect)

        // Draw nearest-neighbor sprite with rotation, flip, and squash-and-stretch
        canvas.save()
        canvas.translate(cx, cy)
        canvas.rotate(physics.rotationDeg)
        val flipX = if (physics.facingRight) 1f else -1f
        canvas.scale(flipX * physics.scaleX, physics.scaleY)
        val half = petSize * 0.5f
        dstRect.set(-half, -half, half, half)
        canvas.drawBitmap(spriteSheet.bitmap, srcRect, dstRect, spritePaint)
        canvas.restore()

        // Orbiting pixel dizzy stars during SPLAT & DIZZY
        if (physics.mood == PetMood.SPLAT || physics.mood == PetMood.DIZZY) {
            drawDizzyStars(canvas, cx, cy - petSize * 0.48f, petSize * 0.36f, physics.dizzyPhase)
        }

        // Comic speech tag ("ААА!", "БУМ!", "@_@")
        val caption = physics.comicText
        if (!caption.isNullOrEmpty()) {
            drawComicTag(canvas, cx, (cy - petSize * 0.62f).coerceAtLeast(24f), caption)
        }
    }

    private fun drawDizzyStars(canvas: Canvas, cx: Float, cy: Float, radiusX: Float, phase: Float) {
        val starColors = intArrayOf(
            Color.rgb(255, 224, 92),
            Color.rgb(102, 252, 241),
            Color.rgb(255, 168, 92),
        )
        val px = (physics.petSizePx * 0.055f).coerceAtLeast(4f)
        for (i in 0 until 3) {
            val angle = phase + i * (Math.PI.toFloat() * 2f / 3f)
            val sx = cx + cos(angle) * radiusX
            val sy = cy + sin(angle) * (radiusX * 0.34f)
            starPaint.color = starColors[i % starColors.size]
            // Crisp pixel plus-shaped star
            canvas.drawRect(sx - px * 1.5f, sy - px * 0.5f, sx + px * 1.5f, sy + px * 0.5f, starPaint)
            canvas.drawRect(sx - px * 0.5f, sy - px * 1.5f, sx + px * 0.5f, sy + px * 1.5f, starPaint)
        }
    }

    private fun drawComicTag(canvas: Canvas, cx: Float, cy: Float, text: String) {
        val textW = bubbleTextPaint.measureText(text)
        val padH = 14f
        val boxH = bubbleTextPaint.textSize + 12f
        val halfW = (textW * 0.5f + padH).coerceAtLeast(36f)
        val top = (cy - boxH).coerceAtLeast(4f)
        val left = (cx - halfW).coerceAtLeast(4f)
        val right = (cx + halfW).coerceAtMost(width - 4f)
        bubbleRect.set(left, top, right, top + boxH)

        canvas.drawRoundRect(bubbleRect, 12f, 12f, bubbleBgPaint)
        canvas.drawRoundRect(bubbleRect, 12f, 12f, bubbleBorderPaint)
        val baseline = bubbleRect.centerY() - (bubbleTextPaint.descent() + bubbleTextPaint.ascent()) * 0.5f
        canvas.drawText(text, bubbleRect.centerX(), baseline, bubbleTextPaint)
    }

    @SuppressLint("ClickableViewAccessibility")
    override fun onTouchEvent(event: MotionEvent): Boolean {
        val now = SystemClock.uptimeMillis()
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                isDragging = true
                downRawX = event.rawX
                downRawY = event.rawY
                lastRawX = event.rawX
                lastRawY = event.rawY
                lastMoveTimeMs = now
                movedDistance = 0f
                physics.startDrag(event.rawX, event.rawY)
                return true
            }

            MotionEvent.ACTION_MOVE -> {
                if (!isDragging) return false
                val dx = event.rawX - lastRawX
                val dy = event.rawY - lastRawY
                val dt = ((now - lastMoveTimeMs).coerceAtLeast(8L)) / 1000f
                movedDistance += hypot(dx, dy)
                lastRawX = event.rawX
                lastRawY = event.rawY
                lastMoveTimeMs = now

                physics.updateDrag(dx, dy, dt)
                onTouchPositionChanged()
                return true
            }

            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                if (!isDragging) return false
                isDragging = false
                val totalShift = hypot(event.rawX - downRawX, event.rawY - downRawY)
                if (totalShift < 18f && movedDistance < 24f) {
                    // Quick tap or double tap!
                    val isDoubleTap = (now - lastTapTimeMs) <= 320L
                    lastTapTimeMs = now
                    physics.endDrag()
                    if (isDoubleTap) {
                        physics.triggerFunnyFall(directionSign = if (physics.facingRight) 1f else -1f)
                    } else {
                        physics.triggerHappyJump()
                    }
                } else {
                    physics.endDrag()
                }
                return true
            }
        }
        return super.onTouchEvent(event)
    }
}
