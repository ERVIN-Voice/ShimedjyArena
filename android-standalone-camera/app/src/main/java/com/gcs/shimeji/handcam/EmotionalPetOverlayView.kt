package com.gcs.shimeji.handcam

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
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.sin

/**
 * Floating pixel-art overlay view rendering:
 * - Nearest-neighbor pixel companion with tumble rotation & squash-and-stretch
 * - Crisp pixel-art emotion effects for all 8 PetEmotion states:
 *   LOVE (blush + rising pixel hearts), HAPPY/EXCITED (sparkles),
 *   SCARED (pixel sweat drop + shock lines), DIZZY (orbiting pixel stars),
 *   ANGRY (pulsing pixel anger vein), SLEEPY (floating pixel Zzz)
 * - Retro comic bubble ("ААА!", "БУМ!", "Мур! ♥", "ЭЙ!!")
 * - Direct touch drag, fling, tap-jump, double-tap-tumble
 */
@SuppressLint("ViewConstructor")
class EmotionalPetOverlayView(
    context: Context,
    private val spriteSheet: PixelSpriteSheet,
    private val physics: EmotionalPetPhysics,
    private val onTouchPositionChanged: () -> Unit,
) : View(context) {

    var paddingPx: Int = 50
        private set

    var showGestureBadge: Boolean = true
    var activeGesture: HandGesture = HandGesture.NONE

    private val spritePaint = Paint().apply {
        isFilterBitmap = false
        isAntiAlias = false
        isDither = false
    }

    private val fxPaint = Paint().apply {
        isAntiAlias = false
        style = Paint.Style.FILL
    }

    private val zzzPaint = Paint().apply {
        isAntiAlias = true
        color = Color.rgb(154, 212, 255)
        typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
        textAlign = Paint.Align.CENTER
    }

    private val bubbleBgPaint = Paint().apply {
        isAntiAlias = true
        color = Color.argb(228, 16, 22, 36)
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
        paddingPx = (petSizePx * 0.48f).toInt().coerceAtLeast(42)
        bubbleTextPaint.textSize = (petSizePx * 0.21f).coerceIn(20f, 32f)
        zzzPaint.textSize = (petSizePx * 0.20f).coerceIn(18f, 28f)
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
        val cy = height * 0.5f + paddingPx * 0.16f
        val px = (petSize / 32f).coerceAtLeast(3f)

        spriteSheet.fillSourceRect(currentAnim, animFrame, srcRect)

        // Draw nearest-neighbor pixel sprite + face blush overlay in local sprite space
        canvas.save()
        canvas.translate(cx, cy)
        canvas.rotate(physics.rotationDeg)
        val flipX = if (physics.facingRight) 1f else -1f
        canvas.scale(flipX * physics.scaleX, physics.scaleY)
        val half = petSize * 0.5f
        dstRect.set(-half, -half, half, half)
        canvas.drawBitmap(spriteSheet.bitmap, srcRect, dstRect, spritePaint)

        // Rosy pixel blush cheeks for LOVE & HAPPY emotions
        if (physics.emotion == PetEmotion.LOVE || physics.emotion == PetEmotion.HAPPY) {
            fxPaint.color = if (physics.emotion == PetEmotion.LOVE) {
                Color.argb(215, 255, 105, 165)
            } else {
                Color.argb(150, 255, 130, 175)
            }
            canvas.drawRect(-half + px * 8f, -half + px * 15f, -half + px * 11f, -half + px * 17f, fxPaint)
            canvas.drawRect(half - px * 11f, -half + px * 15f, half - px * 8f, -half + px * 17f, fxPaint)
        }
        canvas.restore()

        // Render emotion-specific pixel FX around the character
        when (physics.emotion) {
            PetEmotion.LOVE -> drawRisingHearts(canvas, cx, cy - petSize * 0.44f, px, physics.effectPhase)
            PetEmotion.HAPPY, PetEmotion.EXCITED -> drawSparkles(canvas, cx, cy - petSize * 0.38f, petSize * 0.44f, px, physics.effectPhase)
            PetEmotion.SCARED -> drawSweatDrop(canvas, cx + petSize * 0.34f, cy - petSize * 0.34f, px, physics.effectPhase)
            PetEmotion.DIZZY -> drawDizzyStars(canvas, cx, cy - petSize * 0.48f, petSize * 0.38f, px, physics.effectPhase)
            PetEmotion.ANGRY -> drawAngerVein(canvas, cx + petSize * 0.30f, cy - petSize * 0.42f, px, physics.effectPhase)
            PetEmotion.SLEEPY -> drawFloatingZzz(canvas, cx + petSize * 0.24f, cy - petSize * 0.42f, physics.effectPhase)
            PetEmotion.NEUTRAL -> Unit
        }

        // Comic speech bubble or subtle active hand gesture badge
        val caption = physics.comicText
        if (!caption.isNullOrEmpty()) {
            drawComicTag(canvas, cx, (cy - petSize * 0.62f).coerceAtLeast(24f), caption, emotionBorderColor())
        } else if (showGestureBadge && activeGesture != HandGesture.NONE) {
            val shortTag = when (activeGesture) {
                HandGesture.PINCH -> "🤏"
                HandGesture.OPEN_PALM -> "✋"
                HandGesture.POINT -> "☝️"
                HandGesture.PEACE -> "✌️"
                HandGesture.FIST -> "✊"
                HandGesture.NONE -> ""
            }
            if (shortTag.isNotEmpty()) {
                drawComicTag(canvas, cx, (cy - petSize * 0.60f).coerceAtLeast(24f), shortTag, Color.rgb(77, 224, 157))
            }
        }
    }

    private fun emotionBorderColor(): Int = when (physics.emotion) {
        PetEmotion.LOVE -> Color.rgb(255, 107, 168)
        PetEmotion.ANGRY -> Color.rgb(255, 85, 85)
        PetEmotion.SCARED -> Color.rgb(102, 196, 255)
        PetEmotion.DIZZY -> Color.rgb(255, 212, 82)
        PetEmotion.EXCITED, PetEmotion.HAPPY -> Color.rgb(77, 224, 157)
        else -> Color.rgb(102, 252, 241)
    }

    private fun drawPixelHeart(canvas: Canvas, cx: Float, cy: Float, u: Float, color: Int) {
        fxPaint.color = color
        // 5x4 classic pixel heart
        canvas.drawRect(cx - 2f * u, cy - u, cx - u, cy, fxPaint)
        canvas.drawRect(cx + u, cy - u, cx + 2f * u, cy, fxPaint)
        canvas.drawRect(cx - 2.5f * u, cy, cx + 2.5f * u, cy + u, fxPaint)
        canvas.drawRect(cx - 1.5f * u, cy + u, cx + 1.5f * u, cy + 2f * u, fxPaint)
        canvas.drawRect(cx - 0.5f * u, cy + 2f * u, cx + 0.5f * u, cy + 3f * u, fxPaint)
    }

    private fun drawRisingHearts(canvas: Canvas, cx: Float, topY: Float, px: Float, phase: Float) {
        val u = (px * 0.85f).coerceAtLeast(3f)
        for (i in 0 until 2) {
            val t = ((phase * 0.35f + i * 0.5f) % 1f)
            val hx = cx + (if (i == 0) -1f else 1f) * (px * 7f + sin(phase + i * 2f) * px * 2.5f)
            val hy = topY - t * px * 11f
            val alpha = ((1f - t) * 255f).toInt().coerceIn(40, 255)
            val col = if (i == 0) Color.argb(alpha, 255, 92, 148) else Color.argb(alpha, 255, 145, 196)
            drawPixelHeart(canvas, hx, hy, u, col)
        }
    }

    private fun drawSparkles(canvas: Canvas, cx: Float, topY: Float, radius: Float, px: Float, phase: Float) {
        val colors = intArrayOf(Color.rgb(255, 232, 102), Color.rgb(102, 252, 241))
        for (i in 0 until 3) {
            val pulse = 0.5f + 0.5f * sin(phase * 1.4f + i * 2.1f)
            val sx = cx + cos(i * 2.1f + 0.4f) * radius
            val sy = topY + sin(i * 1.8f) * (radius * 0.35f) - px * 2f
            val s = px * (0.7f + pulse * 0.8f)
            fxPaint.color = colors[i % colors.size]
            canvas.drawRect(sx - s * 1.6f, sy - s * 0.45f, sx + s * 1.6f, sy + s * 0.45f, fxPaint)
            canvas.drawRect(sx - s * 0.45f, sy - s * 1.6f, sx + s * 0.45f, sy + s * 1.6f, fxPaint)
        }
    }

    private fun drawSweatDrop(canvas: Canvas, sx: Float, sy: Float, px: Float, phase: Float) {
        val slide = ((phase * 0.4f) % 1f) * px * 5f
        val y = sy + slide
        val u = px * 0.9f
        fxPaint.color = Color.rgb(92, 200, 255)
        canvas.drawRect(sx - u * 0.5f, y - u * 2f, sx + u * 0.5f, y - u, fxPaint)
        canvas.drawRect(sx - u, y - u, sx + u, y + u, fxPaint)
        canvas.drawRect(sx - u * 1.3f, y, sx + u * 1.3f, y + u * 1.5f, fxPaint)
        fxPaint.color = Color.WHITE
        canvas.drawRect(sx - u * 0.7f, y, sx - u * 0.1f, y + u * 0.7f, fxPaint)
    }

    private fun drawDizzyStars(canvas: Canvas, cx: Float, cy: Float, radiusX: Float, px: Float, phase: Float) {
        val starColors = intArrayOf(
            Color.rgb(255, 224, 92),
            Color.rgb(102, 252, 241),
            Color.rgb(255, 168, 92),
        )
        val u = (px * 1.05f).coerceAtLeast(4f)
        for (i in 0 until 3) {
            val angle = phase * 1.2f + i * (Math.PI.toFloat() * 2f / 3f)
            val sx = cx + cos(angle) * radiusX
            val sy = cy + sin(angle) * (radiusX * 0.34f)
            fxPaint.color = starColors[i % starColors.size]
            canvas.drawRect(sx - u * 1.5f, sy - u * 0.5f, sx + u * 1.5f, sy + u * 0.5f, fxPaint)
            canvas.drawRect(sx - u * 0.5f, sy - u * 1.5f, sx + u * 0.5f, sy + u * 1.5f, fxPaint)
        }
    }

    private fun drawAngerVein(canvas: Canvas, ax: Float, ay: Float, px: Float, phase: Float) {
        val scale = 0.85f + 0.25f * sin(phase * 2.2f)
        val u = px * 0.9f * scale
        fxPaint.color = Color.rgb(255, 68, 68)
        // Classic 4-corner pixel anime anger cross mark
        // Top-left corner
        canvas.drawRect(ax - 2.2f * u, ay - 0.8f * u, ax - 0.6f * u, ay - 0.2f * u, fxPaint)
        canvas.drawRect(ax - 0.8f * u, ay - 2.2f * u, ax - 0.2f * u, ay - 0.6f * u, fxPaint)
        // Top-right corner
        canvas.drawRect(ax + 0.6f * u, ay - 0.8f * u, ax + 2.2f * u, ay - 0.2f * u, fxPaint)
        canvas.drawRect(ax + 0.2f * u, ay - 2.2f * u, ax + 0.8f * u, ay - 0.6f * u, fxPaint)
        // Bottom-left corner
        canvas.drawRect(ax - 2.2f * u, ay + 0.2f * u, ax - 0.6f * u, ay + 0.8f * u, fxPaint)
        canvas.drawRect(ax - 0.8f * u, ay + 0.6f * u, ax - 0.2f * u, ay + 2.2f * u, fxPaint)
        // Bottom-right corner
        canvas.drawRect(ax + 0.6f * u, ay + 0.2f * u, ax + 2.2f * u, ay + 0.8f * u, fxPaint)
        canvas.drawRect(ax + 0.2f * u, ay + 0.6f * u, ax + 0.8f * u, ay + 2.2f * u, fxPaint)
    }

    private fun drawFloatingZzz(canvas: Canvas, zx: Float, zy: Float, phase: Float) {
        val t1 = (phase * 0.22f) % 1f
        val t2 = ((phase * 0.22f) + 0.5f) % 1f
        zzzPaint.alpha = ((1f - t1) * 255f).toInt().coerceIn(30, 255)
        canvas.drawText("Z", zx + t1 * 18f, zy - t1 * 34f, zzzPaint)
        zzzPaint.alpha = ((1f - t2) * 255f).toInt().coerceIn(30, 255)
        canvas.drawText("z", zx + 10f + t2 * 16f, zy - t2 * 28f, zzzPaint)
    }

    private fun drawComicTag(canvas: Canvas, cx: Float, cy: Float, text: String, borderColor: Int) {
        val textW = bubbleTextPaint.measureText(text)
        val padH = 12f
        val boxH = bubbleTextPaint.textSize + 10f
        val halfW = (textW * 0.5f + padH).coerceAtLeast(30f)
        val top = (cy - boxH).coerceAtLeast(4f)
        val left = (cx - halfW).coerceAtLeast(4f)
        val right = (cx + halfW).coerceAtMost(width - 4f)
        bubbleRect.set(left, top, right, top + boxH)

        bubbleBorderPaint.color = borderColor
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
