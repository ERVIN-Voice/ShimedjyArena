package com.gcs.shimeji.overlay

import kotlin.math.abs
import kotlin.math.hypot
import kotlin.math.sign
import kotlin.random.Random

enum class PetMood {
    IDLE,
    WALK,
    SLEEP,
    JUMP,
    DRAGGED,
    TUMBLING,
    SPLAT,
    DIZZY,
    GETTING_UP,
}

/**
 * Pure-Kotlin 60 FPS overlay pet physics engine featuring:
 * - Normal wandering, idling, sleeping, jumping, finger dragging & flinging
 * - Hilarious tilt / rotation tumbling: when the phone tilts or rotates, the pet loses
 *   footing, tumbles head-over-heels toward physical gravity, squashes & bounces on impact,
 *   sees dizzy stars, and then hops back upright!
 */
class OverlayPetPhysics(
    screenWidth: Float,
    screenHeight: Float,
    petSizePx: Float,
    private val random: Random = Random.Default,
    private val onBonk: ((impactSpeed: Float) -> Unit)? = null,
) {
    var screenWidth: Float = screenWidth.coerceAtLeast(petSizePx + 16f)
        private set
    var screenHeight: Float = screenHeight.coerceAtLeast(petSizePx + 16f)
        private set
    var petSizePx: Float = petSizePx.coerceAtLeast(32f)
        private set

    // Top-left position of the pet sprite in screen coordinates
    var x: Float = ((screenWidth - petSizePx) * 0.5f).coerceAtLeast(0f)
    var y: Float = (screenHeight - petSizePx).coerceAtLeast(0f)
    var vx: Float = 0f
    var vy: Float = 0f

    // Comedic visual transform properties
    var rotationDeg: Float = 0f
    var angularVelocityDeg: Float = 0f
    var scaleX: Float = 1f
    var scaleY: Float = 1f
    var facingRight: Boolean = true

    // Mood, animation name (matches shared/avatar_config.json), and comic caption
    var mood: PetMood = PetMood.IDLE
        private set
    var animationName: String = "idle"
        private set
    var comicText: String? = null
        private set
    var dizzyPhase: Float = 0f
        private set

    // Tunable settings
    var tiltPhysicsEnabled: Boolean = true
    var tiltSensitivity: Float = 0.65f
    var bounciness: Float = 0.65f

    // Normalized physical gravity vector in screen coordinates (upright = 0, +1)
    private var gxNorm: Float = 0f
    private var gyNorm: Float = 1f
    private var prevGxNorm: Float = 0f
    private var prevGyNorm: Float = 1f

    // Timers
    private var stateTimer: Float = 2.0f
    private var tumbleGraceTimer: Float = 0f
    private var comicTimer: Float = 0f
    private var bonkCooldown: Float = 0f

    // Drag tracking
    private var dragLastX: Float = 0f
    private var dragLastY: Float = 0f

    private val fallShouts = listOf("ААА!", "ОЙ!", "ВОУ!", "КУВЫРК!", "ПАДАЮ!")
    private val bonkShouts = listOf("БУМ!", "ШМЯК!", "УФ...", "@_@", "АЙ!")

    fun updateScreenBounds(newWidth: Float, newHeight: Float, triggerRotationTumble: Boolean = false) {
        val oldW = screenWidth.coerceAtLeast(1f)
        val oldH = screenHeight.coerceAtLeast(1f)
        val normX = (x / (oldW - petSizePx).coerceAtLeast(1f)).coerceIn(0f, 1f)
        val normY = (y / (oldH - petSizePx).coerceAtLeast(1f)).coerceIn(0f, 1f)

        screenWidth = newWidth.coerceAtLeast(petSizePx + 16f)
        screenHeight = newHeight.coerceAtLeast(petSizePx + 16f)

        x = normX * (screenWidth - petSizePx)
        y = normY * (screenHeight - petSizePx)

        if (triggerRotationTumble && tiltPhysicsEnabled && mood != PetMood.DRAGGED) {
            val dir = if (facingRight) 1f else -1f
            triggerFunnyFall(directionSign = dir, extraPop = 1.15f)
        }
    }

    fun updatePetSize(newSizePx: Float) {
        petSizePx = newSizePx.coerceAtLeast(32f)
        clampToBounds()
    }

    /**
     * Feed normalized physical gravity in screen coordinates:
     * - Upright portrait: gx = 0, gy = +1
     * - Tilted right: gx > 0
     * - Tilted left: gx < 0
     * - Upside-down: gy < 0
     */
    fun setTiltGravity(gx: Float, gy: Float, shakeMagnitude: Float = 0f) {
        prevGxNorm = gxNorm
        prevGyNorm = gyNorm

        val len = hypot(gx, gy)
        if (len > 0.15f) {
            gxNorm = (gx / len).coerceIn(-1f, 1f)
            gyNorm = (gy / len).coerceIn(-1f, 1f)
        }

        if (!tiltPhysicsEnabled || mood == PetMood.DRAGGED) return

        // Higher sensitivity -> smaller angle needed to make pet slip and tumble (~20° to ~40°)
        val tiltThreshold = 0.64f - (tiltSensitivity.coerceIn(0.2f, 1.0f) * 0.30f) // 0.34..0.58
        val gravityDelta = hypot(gxNorm - prevGxNorm, gyNorm - prevGyNorm)
        val isTiltedHard = abs(gxNorm) >= tiltThreshold || gyNorm < 0.42f
        val isSuddenFlip = gravityDelta >= 0.45f || shakeMagnitude >= 14.5f

        if ((isTiltedHard || isSuddenFlip) && mood != PetMood.TUMBLING && mood != PetMood.SPLAT) {
            val dir = when {
                abs(gxNorm) > 0.12f -> sign(gxNorm)
                facingRight -> 1f
                else -> -1f
            }
            enterTumble(
                kickVx = dir * (240f + abs(gxNorm) * 260f),
                kickVy = if (gyNorm < 0.4f) -220f else -280f,
                spinDegPerSec = dir * (380f + 260f * bounciness),
                shout = fallShouts[random.nextInt(fallShouts.size)],
            )
        }
    }

    /** Manually launch a funny slip & tumble (also used by "Тест падения!" button and orientation changes). */
    fun triggerFunnyFall(directionSign: Float = 1f, extraPop: Float = 1f) {
        val dir = if (directionSign >= 0f) 1f else -1f
        enterTumble(
            kickVx = dir * 320f * extraPop,
            kickVy = -460f * extraPop,
            spinDegPerSec = dir * (480f + 240f * bounciness),
            shout = fallShouts[random.nextInt(fallShouts.size)],
        )
    }

    private fun enterTumble(kickVx: Float, kickVy: Float, spinDegPerSec: Float, shout: String) {
        mood = PetMood.TUMBLING
        animationName = "jump"
        vx += kickVx
        vy = (vy * 0.3f) + kickVy
        angularVelocityDeg = spinDegPerSec
        facingRight = kickVx >= 0f
        tumbleGraceTimer = 0.25f
        showComic(shout, 1.1f)
    }

    fun startDrag(screenX: Float, screenY: Float) {
        mood = PetMood.DRAGGED
        animationName = "grab"
        vx = 0f
        vy = 0f
        angularVelocityDeg = 0f
        comicText = null
        dragLastX = screenX
        dragLastY = screenY
    }

    fun updateDrag(dx: Float, dy: Float, dt: Float) {
        if (mood != PetMood.DRAGGED) return
        x += dx
        y += dy
        clampToBounds()
        val safeDt = dt.coerceAtLeast(0.008f)
        val instVx = dx / safeDt
        val instVy = dy / safeDt
        vx = vx * 0.55f + instVx * 0.45f
        vy = vy * 0.55f + instVy * 0.45f
        // Cute dangling tilt while being dragged by finger
        rotationDeg = (-vx * 0.025f).coerceIn(-28f, 28f)
        if (abs(dx) > 1.5f) facingRight = dx >= 0f
    }

    fun endDrag() {
        if (mood != PetMood.DRAGGED) return
        val throwSpeed = hypot(vx, vy)
        if (throwSpeed > 420f) {
            val dir = if (abs(vx) > 20f) sign(vx) else (if (facingRight) 1f else -1f)
            mood = PetMood.TUMBLING
            animationName = "jump"
            angularVelocityDeg = dir * (throwSpeed * 0.55f).coerceIn(280f, 820f)
            tumbleGraceTimer = 0.12f
            showComic(fallShouts[random.nextInt(fallShouts.size)], 1.0f)
        } else {
            mood = PetMood.JUMP
            animationName = "jump"
            rotationDeg = 0f
        }
    }

    fun triggerHappyJump() {
        if (mood == PetMood.DRAGGED) return
        mood = PetMood.JUMP
        animationName = "jump"
        vy = -560f
        vx = (if (facingRight) 1f else -1f) * 90f
        scaleX = 0.82f
        scaleY = 1.24f
        rotationDeg = 0f
        angularVelocityDeg = 0f
    }

    fun step(rawDt: Float) {
        val dt = rawDt.coerceIn(0.001f, 0.05f)

        // Elastic recovery of squash-and-stretch back to (1, 1)
        val stretchEase = (dt * 11f).coerceAtMost(1f)
        scaleX += (1f - scaleX) * stretchEase
        scaleY += (1f - scaleY) * stretchEase

        if (bonkCooldown > 0f) bonkCooldown = (bonkCooldown - dt).coerceAtLeast(0f)
        if (tumbleGraceTimer > 0f) tumbleGraceTimer = (tumbleGraceTimer - dt).coerceAtLeast(0f)
        if (comicTimer > 0f) {
            comicTimer = (comicTimer - dt).coerceAtLeast(0f)
            if (comicTimer <= 0f) comicText = null
        }

        when (mood) {
            PetMood.DRAGGED -> {
                animationName = "grab"
            }

            PetMood.TUMBLING -> stepTumbling(dt)
            PetMood.SPLAT -> stepSplat(dt)
            PetMood.DIZZY -> stepDizzy(dt)
            PetMood.GETTING_UP -> stepGettingUp(dt)
            PetMood.JUMP -> stepJump(dt)
            PetMood.IDLE, PetMood.WALK, PetMood.SLEEP -> stepNormalLife(dt)
        }
    }

    private fun stepTumbling(dt: Float) {
        val gravityMag = 1650f
        val effGx = if (tiltPhysicsEnabled) gxNorm else 0f
        val effGy = if (tiltPhysicsEnabled) gyNorm else 1f

        vx += effGx * gravityMag * dt
        vy += effGy * gravityMag * dt

        // Slight air drag so it doesn't reach infinite speed
        vx *= (1f - 0.18f * dt)
        vy *= (1f - 0.18f * dt)

        x += vx * dt
        y += vy * dt
        rotationDeg = (rotationDeg + angularVelocityDeg * dt) % 360f

        val maxX = (screenWidth - petSizePx).coerceAtLeast(0f)
        val maxY = (screenHeight - petSizePx).coerceAtLeast(0f)
        val bounce = (0.38f + 0.32f * bounciness).coerceIn(0.35f, 0.72f)

        var hitSurface = false
        var impactSpeed = 0f
        var hitHorizontalWall = false

        if (x < 0f) {
            x = 0f
            impactSpeed = maxOf(impactSpeed, abs(vx))
            vx = abs(vx) * bounce
            angularVelocityDeg = -angularVelocityDeg * 0.72f + vy * 0.25f
            hitSurface = true
            hitHorizontalWall = true
        } else if (x > maxX) {
            x = maxX
            impactSpeed = maxOf(impactSpeed, abs(vx))
            vx = -abs(vx) * bounce
            angularVelocityDeg = -angularVelocityDeg * 0.72f - vy * 0.25f
            hitSurface = true
            hitHorizontalWall = true
        }

        if (y < 0f) {
            y = 0f
            impactSpeed = maxOf(impactSpeed, abs(vy))
            vy = abs(vy) * bounce
            angularVelocityDeg = angularVelocityDeg * 0.72f - vx * 0.35f
            hitSurface = true
        } else if (y > maxY) {
            y = maxY
            impactSpeed = maxOf(impactSpeed, abs(vy))
            vy = -abs(vy) * bounce
            // Rolling friction along floor
            vx *= 0.76f
            angularVelocityDeg = vx * 1.15f
            hitSurface = true
        }

        if (hitSurface && impactSpeed > 180f) {
            // Hilarious squash-and-stretch on impact!
            if (hitHorizontalWall) {
                scaleX = 0.62f
                scaleY = 1.38f
            } else {
                scaleX = 1.42f
                scaleY = 0.58f
            }
            if (bonkCooldown <= 0f) {
                bonkCooldown = 0.22f
                onBonk?.invoke(impactSpeed)
                showComic(bonkShouts[random.nextInt(bonkShouts.size)], 0.9f)
            }
        }

        // Settle into funny SPLAT pose once rebound slows down on a resting edge
        val speed = hypot(vx, vy)
        val restingOnAnyEdge = x <= 1f || x >= maxX - 1f || y <= 1f || y >= maxY - 1f
        if (restingOnAnyEdge && tumbleGraceTimer <= 0f && speed < 175f) {
            mood = PetMood.SPLAT
            animationName = "landing"
            vx = 0f
            vy = 0f
            angularVelocityDeg = 0f
            // Settle onto funny faceplant angle (-90°, +90°, or 180°)
            rotationDeg = snapToFunnySplatAngle(rotationDeg)
            scaleX = 1.35f
            scaleY = 0.66f
            stateTimer = 0.55f
            dizzyPhase = 0f
            showComic(bonkShouts[random.nextInt(bonkShouts.size)], 1.2f)
        }
    }

    private fun stepSplat(dt: Float) {
        dizzyPhase += dt * 5.5f
        stateTimer -= dt
        // If user tilts phone strongly to a new direction while splatted, tumble again!
        if (tiltPhysicsEnabled && isGravityPullingOffEdge()) {
            enterTumble(
                kickVx = gxNorm * 260f,
                kickVy = gyNorm * 220f,
                spinDegPerSec = (if (gxNorm >= 0f) 1f else -1f) * 440f,
                shout = fallShouts[random.nextInt(fallShouts.size)],
            )
            return
        }
        if (stateTimer <= 0f) {
            mood = PetMood.DIZZY
            animationName = "sleep"
            stateTimer = 0.95f
            showComic("@_@", 0.95f)
        }
    }

    private fun stepDizzy(dt: Float) {
        dizzyPhase += dt * 6.5f
        stateTimer -= dt
        if (tiltPhysicsEnabled && isGravityPullingOffEdge()) {
            enterTumble(
                kickVx = gxNorm * 260f,
                kickVy = gyNorm * 220f,
                spinDegPerSec = (if (gxNorm >= 0f) 1f else -1f) * 440f,
                shout = fallShouts[random.nextInt(fallShouts.size)],
            )
            return
        }
        if (stateTimer <= 0f) {
            // Wait until phone is reasonably upright before hopping back up cleanly
            val isUprightEnough = !tiltPhysicsEnabled || (abs(gxNorm) < 0.42f && gyNorm > 0.65f)
            if (isUprightEnough) {
                mood = PetMood.GETTING_UP
                animationName = "landing"
                vy = -210f
                scaleX = 0.85f
                scaleY = 1.2f
                stateTimer = 0.35f
            } else {
                // Still held sideways/upside-down: stay dizzy until tilted back!
                stateTimer = 0.25f
            }
        }
    }

    private fun stepGettingUp(dt: Float) {
        // Spring rotation smoothly back to 0 degrees while dropping to floor
        val ease = (dt * 12f).coerceAtMost(1f)
        rotationDeg = shortestAngleApproachZero(rotationDeg, ease)

        vy += 1500f * dt
        y += vy * dt
        val floorY = (screenHeight - petSizePx).coerceAtLeast(0f)
        if (y >= floorY) {
            y = floorY
            vy = 0f
        }

        stateTimer -= dt
        if (stateTimer <= 0f && y >= floorY - 1f) {
            rotationDeg = 0f
            angularVelocityDeg = 0f
            mood = PetMood.IDLE
            animationName = "idle"
            stateTimer = 1.6f
        }
    }

    private fun stepJump(dt: Float) {
        val floorY = (screenHeight - petSizePx).coerceAtLeast(0f)
        val maxX = (screenWidth - petSizePx).coerceAtLeast(0f)

        vy += 1500f * dt
        x += vx * dt
        y += vy * dt

        if (x < 0f) {
            x = 0f
            vx = abs(vx) * 0.5f
            facingRight = true
        } else if (x > maxX) {
            x = maxX
            vx = -abs(vx) * 0.5f
            facingRight = false
        }

        rotationDeg = shortestAngleApproachZero(rotationDeg, (dt * 10f).coerceAtMost(1f))

        if (y >= floorY) {
            y = floorY
            vy = 0f
            vx = 0f
            rotationDeg = 0f
            scaleX = 1.22f
            scaleY = 0.80f
            mood = PetMood.IDLE
            animationName = "landing"
            stateTimer = 0.4f
        }
    }

    private fun stepNormalLife(dt: Float) {
        val floorY = (screenHeight - petSizePx).coerceAtLeast(0f)
        val maxX = (screenWidth - petSizePx).coerceAtLeast(0f)

        // If somehow floating above floor, drop gently
        if (y < floorY - 1f) {
            mood = PetMood.JUMP
            animationName = "jump"
            return
        }
        y = floorY
        rotationDeg = shortestAngleApproachZero(rotationDeg, (dt * 10f).coerceAtMost(1f))

        stateTimer -= dt
        if (stateTimer <= 0f) {
            val roll = random.nextFloat()
            when {
                roll < 0.52f -> {
                    mood = PetMood.WALK
                    animationName = "walk"
                    facingRight = random.nextBoolean()
                    vx = (if (facingRight) 1f else -1f) * (70f + random.nextFloat() * 45f)
                    stateTimer = 1.8f + random.nextFloat() * 2.6f
                }
                roll < 0.84f -> {
                    mood = PetMood.IDLE
                    animationName = "idle"
                    vx = 0f
                    stateTimer = 1.5f + random.nextFloat() * 2.4f
                }
                else -> {
                    mood = PetMood.SLEEP
                    animationName = "sleep"
                    vx = 0f
                    stateTimer = 3.2f + random.nextFloat() * 3.0f
                }
            }
        }

        if (mood == PetMood.WALK) {
            x += vx * dt
            if (x <= 4f) {
                x = 4f
                facingRight = true
                vx = abs(vx)
            } else if (x >= maxX - 4f) {
                x = maxX - 4f
                facingRight = false
                vx = -abs(vx)
            }
        }
    }

    private fun isGravityPullingOffEdge(): Boolean {
        val maxX = (screenWidth - petSizePx).coerceAtLeast(0f)
        val maxY = (screenHeight - petSizePx).coerceAtLeast(0f)
        val pullRight = gxNorm > 0.52f && x < maxX - 24f
        val pullLeft = gxNorm < -0.52f && x > 24f
        val pullUp = gyNorm < -0.45f && y > 24f
        val pullDown = gyNorm > 0.55f && y < maxY - 24f
        return pullRight || pullLeft || pullUp || pullDown
    }

    private fun clampToBounds() {
        val maxX = (screenWidth - petSizePx).coerceAtLeast(0f)
        val maxY = (screenHeight - petSizePx).coerceAtLeast(0f)
        x = x.coerceIn(0f, maxX)
        y = y.coerceIn(0f, maxY)
    }

    private fun showComic(text: String, durationSec: Float) {
        comicText = text
        comicTimer = durationSec
    }

    companion object {
        internal fun snapToFunnySplatAngle(deg: Float): Float {
            val normalized = ((deg % 360f) + 360f) % 360f
            return when {
                normalized in 35f..145f -> 90f
                normalized in 145f..215f -> 180f
                normalized in 215f..325f -> -90f
                else -> if (normalized < 180f) 90f else -90f
            }
        }

        internal fun shortestAngleApproachZero(deg: Float, fraction: Float): Float {
            var norm = deg % 360f
            if (norm > 180f) norm -= 360f
            if (norm < -180f) norm += 360f
            return norm * (1f - fraction)
        }
    }
}
