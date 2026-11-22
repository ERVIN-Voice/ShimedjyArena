package com.gcs.shimeji.handcam

import kotlin.math.abs
import kotlin.math.hypot
import kotlin.math.sign
import kotlin.random.Random

enum class PetMood {
    IDLE,
    WALK,
    RUN,
    SLEEP,
    JUMP,
    DRAGGED,
    TUMBLING,
    SPLAT,
    DIZZY,
    GETTING_UP,
}

enum class PetEmotion(val russianLabel: String) {
    NEUTRAL("Спокойный"),
    HAPPY("Радостный ✨"),
    LOVE("Влюблённый ♥"),
    EXCITED("В восторге!"),
    SCARED("Испуганный 💧"),
    DIZZY("Голова кружится ★"),
    ANGRY("Злится 💢"),
    SLEEPY("Сонный Zzz"),
}

/**
 * Pure-Kotlin 60 FPS overlay pet physics + emotions + camera hand gesture engine:
 * - Funny gravity/tilt/orientation tumbling (spin, squash-and-stretch bonk, dizzy stars, recovery hop)
 * - 8 expressive pixel emotions (NEUTRAL, HAPPY, LOVE, EXCITED, SCARED, DIZZY, ANGRY, SLEEPY)
 * - On-device hand gesture control (PINCH drag/fling, OPEN_PALM wave-pet/jump/wake, POINT call, PEACE celebrate, FIST sleep)
 */
class EmotionalPetPhysics(
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

    // Position & velocity
    var x: Float = ((screenWidth - petSizePx) * 0.5f).coerceAtLeast(0f)
    var y: Float = (screenHeight - petSizePx).coerceAtLeast(0f)
    var vx: Float = 0f
    var vy: Float = 0f

    // Visual transforms
    var rotationDeg: Float = 0f
    var angularVelocityDeg: Float = 0f
    var scaleX: Float = 1f
    var scaleY: Float = 1f
    var facingRight: Boolean = true

    // Mood, animation, emotion & comic tag
    var mood: PetMood = PetMood.IDLE
        private set
    var emotion: PetEmotion = PetEmotion.NEUTRAL
        private set
    var animationName: String = "idle"
        private set
    var comicText: String? = null
        private set
    var effectPhase: Float = 0f
        private set

    // Tunables
    var tiltPhysicsEnabled: Boolean = true
    var tiltSensitivity: Float = 0.65f
    var bounciness: Float = 0.65f

    // Hand gesture runtime state
    var isHandGrabbing: Boolean = false
        private set
    private var handPointTargetX: Float? = null
    private var fistHoldSec: Float = 0f
    private var palmActionCooldown: Float = 0f
    private var peaceCooldown: Float = 0f

    // Emotion timers & annoyance counter (repeated slams make pet adorably ANGRY!)
    private var emotionTimer: Float = 0f
    private var annoyanceScore: Float = 0f

    // Physical gravity vector in screen coordinates (upright = 0, +1)
    private var gxNorm: Float = 0f
    private var gyNorm: Float = 1f
    private var prevGxNorm: Float = 0f
    private var prevGyNorm: Float = 1f

    // Physics timers
    private var stateTimer: Float = 2.0f
    private var tumbleGraceTimer: Float = 0f
    private var comicTimer: Float = 0f
    private var bonkCooldown: Float = 0f

    private val fallShouts = listOf("ААА!", "ОЙ!", "ВОУ!", "КУВЫРК!", "ПАДАЮ!")
    private val bonkShouts = listOf("БУМ!", "ШМЯК!", "УФ...", "@_@", "АЙ!")
    private val loveShouts = listOf("Мур! ♥", "Хехе ♥", "Классно!", "Няя ♥")
    private val angryShouts = listOf("ЭЙ!!", "ХВАТИТ!", "ГРР!", "НУ ВСЁ!")
    private val happyShouts = listOf("Ура!", "Йоу!", "Хи-хи!", "Прыг!")

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
            triggerFunnyFall(directionSign = if (facingRight) 1f else -1f, extraPop = 1.15f)
        }
    }

    fun updatePetSize(newSizePx: Float) {
        petSizePx = newSizePx.coerceAtLeast(32f)
        clampToBounds()
    }

    fun setEmotion(newEmotion: PetEmotion, durationSec: Float = 3.2f, caption: String? = null) {
        emotion = newEmotion
        emotionTimer = durationSec
        if (caption != null) showComic(caption, minOf(durationSec, 1.35f))
    }

    /** Expressive manual triggers for UI buttons or interactions. */
    fun petWithLove() {
        annoyanceScore = 0f
        if (mood == PetMood.SLEEP || mood == PetMood.DIZZY || mood == PetMood.SPLAT) {
            mood = PetMood.IDLE
            rotationDeg = 0f
        }
        scaleX = 1.18f
        scaleY = 0.86f
        setEmotion(PetEmotion.LOVE, 3.6f, loveShouts[random.nextInt(loveShouts.size)])
    }

    fun makeAngry() {
        annoyanceScore = 3.2f
        if (mood == PetMood.SLEEP) mood = PetMood.IDLE
        vy = -240f
        scaleX = 1.24f
        scaleY = 0.82f
        setEmotion(PetEmotion.ANGRY, 4.0f, angryShouts[random.nextInt(angryShouts.size)])
    }

    fun putToSleep() {
        if (mood == PetMood.DRAGGED || mood == PetMood.TUMBLING) return
        mood = PetMood.SLEEP
        animationName = "sleep"
        vx = 0f
        vy = 0f
        rotationDeg = 0f
        stateTimer = 8.0f
        setEmotion(PetEmotion.SLEEPY, 8.0f, "Zzz...")
    }

    fun setTiltGravity(gx: Float, gy: Float, shakeMagnitude: Float = 0f) {
        prevGxNorm = gxNorm
        prevGyNorm = gyNorm

        val len = hypot(gx, gy)
        if (len > 0.15f) {
            gxNorm = (gx / len).coerceIn(-1f, 1f)
            gyNorm = (gy / len).coerceIn(-1f, 1f)
        }

        if (!tiltPhysicsEnabled || mood == PetMood.DRAGGED) return

        val tiltThreshold = 0.64f - (tiltSensitivity.coerceIn(0.2f, 1.0f) * 0.30f)
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

    fun triggerFunnyFall(directionSign: Float = 1f, extraPop: Float = 1f) {
        val dir = if (directionSign >= 0f) 1f else -1f
        isHandGrabbing = false
        handPointTargetX = null
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
        setEmotion(PetEmotion.SCARED, 2.5f, shout)
    }

    /**
     * Apply real-time camera hand gesture to the overlay pet.
     * Should be called every frame or every camera analysis update.
     */
    fun applyHandGesture(frame: HandGestureFrame, dt: Float) {
        if (!frame.detected || frame.gesture == HandGesture.NONE) {
            fistHoldSec = 0f
            handPointTargetX = null
            if (isHandGrabbing) {
                isHandGrabbing = false
                endDrag()
            }
            return
        }

        // Release pinch grab as soon as gesture switches away from PINCH
        if (isHandGrabbing && frame.gesture != HandGesture.PINCH) {
            isHandGrabbing = false
            endDrag()
        }

        when (frame.gesture) {
            HandGesture.PINCH -> {
                fistHoldSec = 0f
                handPointTargetX = null
                val targetX = (frame.palmX * (screenWidth - petSizePx)).coerceIn(0f, (screenWidth - petSizePx).coerceAtLeast(0f))
                val targetY = (frame.palmY * (screenHeight - petSizePx)).coerceIn(0f, (screenHeight - petSizePx).coerceAtLeast(0f))
                if (!isHandGrabbing) {
                    isHandGrabbing = true
                    startDrag(targetX, targetY)
                    setEmotion(PetEmotion.SCARED, 2.0f, "Ой! 🤏")
                }
                val follow = (dt * 12f).coerceIn(0.12f, 0.65f)
                val dx = (targetX - x) * follow
                val dy = (targetY - y) * follow
                updateDrag(dx, dy, dt.coerceAtLeast(0.016f))
            }

            HandGesture.OPEN_PALM -> {
                fistHoldSec = 0f
                handPointTargetX = null
                if (palmActionCooldown <= 0f) {
                    when {
                        // Quick upward palm flick -> happy jump!
                        frame.velocityY < -0.75f -> {
                            triggerHappyJump()
                            palmActionCooldown = 0.65f
                        }
                        // Sideways palm wave -> pet with LOVE & pixel hearts!
                        abs(frame.velocityX) > 0.45f -> {
                            petWithLove()
                            palmActionCooldown = 0.85f
                        }
                        // Calm open palm wakes up sleeping / dizzy / angry pet
                        mood == PetMood.SLEEP || emotion == PetEmotion.ANGRY || mood == PetMood.DIZZY -> {
                            petWithLove()
                            palmActionCooldown = 1.0f
                        }
                    }
                }
            }

            HandGesture.POINT -> {
                fistHoldSec = 0f
                if (mood != PetMood.TUMBLING && mood != PetMood.SPLAT && mood != PetMood.DRAGGED) {
                    val targetX = (frame.palmX * (screenWidth - petSizePx))
                        .coerceIn(0f, (screenWidth - petSizePx).coerceAtLeast(0f))
                    handPointTargetX = targetX
                    if (mood == PetMood.SLEEP) {
                        mood = PetMood.RUN
                        rotationDeg = 0f
                    }
                    if (emotion != PetEmotion.EXCITED) {
                        setEmotion(PetEmotion.EXCITED, 2.2f, "Бегу! ☝️")
                    }
                }
            }

            HandGesture.PEACE -> {
                fistHoldSec = 0f
                handPointTargetX = null
                if (peaceCooldown <= 0f && mood != PetMood.DRAGGED && mood != PetMood.TUMBLING) {
                    peaceCooldown = 1.6f
                    annoyanceScore = 0f
                    mood = PetMood.JUMP
                    animationName = "jump"
                    vy = -600f
                    vx = (if (facingRight) 1f else -1f) * 140f
                    scaleX = 0.78f
                    scaleY = 1.28f
                    setEmotion(PetEmotion.LOVE, 3.4f, "Йоу! ✌️♥")
                }
            }

            HandGesture.FIST -> {
                handPointTargetX = null
                fistHoldSec += dt
                if (fistHoldSec >= 0.65f && mood != PetMood.SLEEP && mood != PetMood.TUMBLING && mood != PetMood.DRAGGED) {
                    putToSleep()
                    fistHoldSec = 0f
                }
            }

            HandGesture.NONE -> Unit
        }
    }

    fun startDrag(screenX: Float, screenY: Float) {
        mood = PetMood.DRAGGED
        animationName = "grab"
        vx = 0f
        vy = 0f
        angularVelocityDeg = 0f
        handPointTargetX = null
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
        rotationDeg = (-vx * 0.025f).coerceIn(-30f, 30f)
        if (abs(dx) > 1.5f) facingRight = dx >= 0f
        if (hypot(vx, vy) > 520f && emotion != PetEmotion.SCARED) {
            setEmotion(PetEmotion.SCARED, 1.5f, "Полегче!")
        }
    }

    fun endDrag() {
        if (mood != PetMood.DRAGGED) return
        val throwSpeed = hypot(vx, vy)
        if (throwSpeed > 400f) {
            val dir = if (abs(vx) > 20f) sign(vx) else (if (facingRight) 1f else -1f)
            mood = PetMood.TUMBLING
            animationName = "jump"
            angularVelocityDeg = dir * (throwSpeed * 0.55f).coerceIn(280f, 820f)
            tumbleGraceTimer = 0.12f
            setEmotion(PetEmotion.SCARED, 2.2f, fallShouts[random.nextInt(fallShouts.size)])
        } else {
            mood = PetMood.JUMP
            animationName = "jump"
            rotationDeg = 0f
            setEmotion(PetEmotion.HAPPY, 1.8f)
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
        annoyanceScore = (annoyanceScore - 0.8f).coerceAtLeast(0f)
        setEmotion(PetEmotion.HAPPY, 2.5f, happyShouts[random.nextInt(happyShouts.size)])
    }

    fun step(rawDt: Float) {
        val dt = rawDt.coerceIn(0.001f, 0.05f)
        effectPhase += dt * 5.2f

        val stretchEase = (dt * 11f).coerceAtMost(1f)
        scaleX += (1f - scaleX) * stretchEase
        scaleY += (1f - scaleY) * stretchEase

        if (bonkCooldown > 0f) bonkCooldown = (bonkCooldown - dt).coerceAtLeast(0f)
        if (tumbleGraceTimer > 0f) tumbleGraceTimer = (tumbleGraceTimer - dt).coerceAtLeast(0f)
        if (palmActionCooldown > 0f) palmActionCooldown = (palmActionCooldown - dt).coerceAtLeast(0f)
        if (peaceCooldown > 0f) peaceCooldown = (peaceCooldown - dt).coerceAtLeast(0f)
        if (annoyanceScore > 0f) annoyanceScore = (annoyanceScore - dt * 0.28f).coerceAtLeast(0f)

        if (comicTimer > 0f) {
            comicTimer = (comicTimer - dt).coerceAtLeast(0f)
            if (comicTimer <= 0f) comicText = null
        }

        if (emotionTimer > 0f) {
            emotionTimer = (emotionTimer - dt).coerceAtLeast(0f)
            if (emotionTimer <= 0f && mood !in setOf(PetMood.SPLAT, PetMood.DIZZY, PetMood.SLEEP, PetMood.TUMBLING)) {
                emotion = PetEmotion.NEUTRAL
            }
        }

        when (mood) {
            PetMood.DRAGGED -> animationName = "grab"
            PetMood.TUMBLING -> stepTumbling(dt)
            PetMood.SPLAT -> stepSplat(dt)
            PetMood.DIZZY -> stepDizzy(dt)
            PetMood.GETTING_UP -> stepGettingUp(dt)
            PetMood.JUMP -> stepJump(dt)
            PetMood.IDLE, PetMood.WALK, PetMood.RUN, PetMood.SLEEP -> stepNormalLife(dt)
        }
    }

    private fun stepTumbling(dt: Float) {
        val gravityMag = 1650f
        val effGx = if (tiltPhysicsEnabled) gxNorm else 0f
        val effGy = if (tiltPhysicsEnabled) gyNorm else 1f

        vx += effGx * gravityMag * dt
        vy += effGy * gravityMag * dt
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
            vx *= 0.76f
            angularVelocityDeg = vx * 1.15f
            hitSurface = true
        }

        if (hitSurface && impactSpeed > 180f) {
            if (hitHorizontalWall) {
                scaleX = 0.62f
                scaleY = 1.38f
            } else {
                scaleX = 1.42f
                scaleY = 0.58f
            }
            if (bonkCooldown <= 0f) {
                bonkCooldown = 0.22f
                annoyanceScore += 1.15f
                onBonk?.invoke(impactSpeed)
                setEmotion(PetEmotion.DIZZY, 2.0f, bonkShouts[random.nextInt(bonkShouts.size)])
            }
        }

        val speed = hypot(vx, vy)
        val restingOnAnyEdge = x <= 1f || x >= maxX - 1f || y <= 1f || y >= maxY - 1f
        if (restingOnAnyEdge && tumbleGraceTimer <= 0f && speed < 175f) {
            mood = PetMood.SPLAT
            animationName = "landing"
            vx = 0f
            vy = 0f
            angularVelocityDeg = 0f
            rotationDeg = snapToFunnySplatAngle(rotationDeg)
            scaleX = 1.35f
            scaleY = 0.66f
            stateTimer = 0.55f
            setEmotion(PetEmotion.DIZZY, 2.2f, bonkShouts[random.nextInt(bonkShouts.size)])
        }
    }

    private fun stepSplat(dt: Float) {
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
            mood = PetMood.DIZZY
            animationName = "sleep"
            stateTimer = 0.90f
            setEmotion(PetEmotion.DIZZY, 1.5f, "@_@")
        }
    }

    private fun stepDizzy(dt: Float) {
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
            val isUprightEnough = !tiltPhysicsEnabled || (abs(gxNorm) < 0.42f && gyNorm > 0.65f)
            if (isUprightEnough) {
                mood = PetMood.GETTING_UP
                animationName = "landing"
                vy = -210f
                scaleX = 0.85f
                scaleY = 1.2f
                stateTimer = 0.35f
            } else {
                stateTimer = 0.25f
            }
        }
    }

    private fun stepGettingUp(dt: Float) {
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
            if (annoyanceScore >= 2.6f) {
                setEmotion(PetEmotion.ANGRY, 3.6f, angryShouts[random.nextInt(angryShouts.size)])
            } else {
                setEmotion(PetEmotion.HAPPY, 1.8f, "Фух!")
            }
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

        if (y < floorY - 1f) {
            mood = PetMood.JUMP
            animationName = "jump"
            return
        }
        y = floorY
        rotationDeg = shortestAngleApproachZero(rotationDeg, (dt * 10f).coerceAtMost(1f))

        // If user is pointing via camera, run toward target X excitedly!
        val pointX = handPointTargetX
        if (pointX != null) {
            val diff = pointX - x
            if (abs(diff) > 16f) {
                facingRight = diff >= 0f
                val speed = if (abs(diff) > 160f) 240f else 135f
                vx = sign(diff) * speed
                x = (x + vx * dt).coerceIn(0f, maxX)
                mood = if (speed > 180f) PetMood.RUN else PetMood.WALK
                animationName = if (speed > 180f) "run" else "walk"
                return
            } else {
                vx = 0f
                mood = PetMood.IDLE
                animationName = "idle"
                return
            }
        }

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
                    if (emotion == PetEmotion.SLEEPY) emotion = PetEmotion.NEUTRAL
                }
                roll < 0.84f -> {
                    mood = PetMood.IDLE
                    animationName = "idle"
                    vx = 0f
                    stateTimer = 1.5f + random.nextFloat() * 2.4f
                    if (emotion == PetEmotion.SLEEPY) emotion = PetEmotion.NEUTRAL
                }
                else -> {
                    mood = PetMood.SLEEP
                    animationName = "sleep"
                    vx = 0f
                    stateTimer = 3.5f + random.nextFloat() * 3.0f
                    setEmotion(PetEmotion.SLEEPY, stateTimer)
                }
            }
        }

        if (mood == PetMood.WALK || mood == PetMood.RUN) {
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
