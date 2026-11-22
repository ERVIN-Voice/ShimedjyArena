package com.gcs.shimeji.overlay

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs
import kotlin.random.Random

class OverlayPetPhysicsTest {

    @Test
    fun sidewaysTiltCausesFunnyTumbleBonkAndRecoveryWhenUpright() {
        var bonkCount = 0
        val physics = OverlayPetPhysics(
            screenWidth = 1080f,
            screenHeight = 2000f,
            petSizePx = 120f,
            random = Random(42),
            onBonk = { bonkCount += 1 },
        )

        // Hold upright first
        physics.setTiltGravity(0f, 9.81f)
        repeat(10) { physics.step(1f / 60f) }
        assertTrue(physics.mood == PetMood.IDLE || physics.mood == PetMood.WALK)

        // Tilt phone sharply to the right!
        physics.setTiltGravity(8.5f, 4.9f)
        assertEquals(PetMood.TUMBLING, physics.mood)
        assertNotNull(physics.comicText)

        var sawSpin = false
        var sawSplatOrDizzy = false
        repeat(220) {
            physics.step(1f / 60f)
            if (abs(physics.rotationDeg) > 25f) sawSpin = true
            if (physics.mood == PetMood.SPLAT || physics.mood == PetMood.DIZZY) {
                sawSplatOrDizzy = true
            }
        }

        assertTrue("Pet should tumble/spin when tilted", sawSpin)
        assertTrue("Pet should reach right wall under rightward gravity", physics.x > 800f)
        assertTrue("Pet should bonk on impact", bonkCount > 0)
        assertTrue("Pet should enter SPLAT or DIZZY after landing", sawSplatOrDizzy)

        // Return phone upright -> pet should eventually hop back upright onto the floor
        physics.setTiltGravity(0f, 9.81f)
        repeat(240) { physics.step(1f / 60f) }
        assertTrue(
            "Pet should recover to normal life on the floor once phone is upright again (mood=${physics.mood})",
            physics.mood in setOf(PetMood.IDLE, PetMood.WALK, PetMood.SLEEP),
        )
        assertEquals(0f, physics.rotationDeg, 0.5f)
        assertEquals(2000f - 120f, physics.y, 2f)
    }

    @Test
    fun screenOrientationRotationLaunchesTumbleAndClampsToNewBounds() {
        val physics = OverlayPetPhysics(
            screenWidth = 1080f,
            screenHeight = 2000f,
            petSizePx = 120f,
            random = Random(7),
        )

        physics.updateScreenBounds(newWidth = 2000f, newHeight = 1080f, triggerRotationTumble = true)
        assertEquals(PetMood.TUMBLING, physics.mood)

        repeat(240) { physics.step(1f / 60f) }
        assertTrue(physics.x in 0f..(2000f - 120f))
        assertTrue(physics.y in 0f..(1080f - 120f))
    }

    @Test
    fun upsideDownFlipDropsPetToCeiling() {
        val physics = OverlayPetPhysics(
            screenWidth = 1080f,
            screenHeight = 1600f,
            petSizePx = 120f,
            random = Random(13),
        )

        // Flip phone upside down (gravity points toward top of screen: gy < 0)
        physics.setTiltGravity(0.2f, -9.8f)
        assertEquals(PetMood.TUMBLING, physics.mood)

        repeat(180) { physics.step(1f / 60f) }
        assertTrue("Pet should fall up toward top ceiling edge (y=${physics.y})", physics.y < 300f)
    }

    @Test
    fun fingerFlingLaunchesTumbleAndStaysWithinBounds() {
        val physics = OverlayPetPhysics(
            screenWidth = 1080f,
            screenHeight = 1800f,
            petSizePx = 120f,
            random = Random(99),
        )
        physics.startDrag(500f, 1500f)
        assertEquals(PetMood.DRAGGED, physics.mood)

        // Fast flick up-right
        repeat(5) { physics.updateDrag(32f, -40f, 1f / 60f) }
        physics.endDrag()
        assertEquals(PetMood.TUMBLING, physics.mood)

        repeat(180) { physics.step(1f / 60f) }
        assertTrue(physics.x in 0f..(1080f - 120f))
        assertTrue(physics.y in 0f..(1800f - 120f))
    }
}
