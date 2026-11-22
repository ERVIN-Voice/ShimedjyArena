package com.gcs.shimeji.handcam

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.random.Random

class HandCamTests {

    private fun build21Hand(pose: String, cx: Float = 0.5f, cy: Float = 0.6f): List<Landmark2D> {
        val pts = MutableList(21) { Landmark2D(cx, cy) }
        pts[0] = Landmark2D(cx, cy + 0.16f) // wrist
        // Palm MCPs
        pts[1] = Landmark2D(cx - 0.06f, cy + 0.11f)
        pts[2] = Landmark2D(cx - 0.09f, cy + 0.06f)
        pts[5] = Landmark2D(cx - 0.06f, cy)
        pts[9] = Landmark2D(cx - 0.01f, cy - 0.01f)
        pts[13] = Landmark2D(cx + 0.04f, cy)
        pts[17] = Landmark2D(cx + 0.08f, cy + 0.03f)

        fun setFinger(mcpIdx: Int, extended: Boolean) {
            val mcp = pts[mcpIdx]
            if (extended) {
                pts[mcpIdx + 1] = Landmark2D(mcp.x, mcp.y - 0.07f)
                pts[mcpIdx + 2] = Landmark2D(mcp.x, mcp.y - 0.13f)
                pts[mcpIdx + 3] = Landmark2D(mcp.x, mcp.y - 0.19f)
            } else {
                pts[mcpIdx + 1] = Landmark2D(mcp.x, mcp.y - 0.02f)
                pts[mcpIdx + 2] = Landmark2D(mcp.x, mcp.y + 0.03f)
                pts[mcpIdx + 3] = Landmark2D(mcp.x, mcp.y + 0.06f)
            }
        }

        when (pose) {
            "open_palm" -> {
                pts[3] = Landmark2D(cx - 0.14f, cy + 0.02f)
                pts[4] = Landmark2D(cx - 0.19f, cy - 0.03f)
                setFinger(5, true)
                setFinger(9, true)
                setFinger(13, true)
                setFinger(17, true)
            }
            "fist" -> {
                pts[3] = Landmark2D(cx - 0.06f, cy + 0.05f)
                pts[4] = Landmark2D(cx - 0.04f, cy + 0.05f)
                setFinger(5, false)
                setFinger(9, false)
                setFinger(13, false)
                setFinger(17, false)
            }
            "pinch" -> {
                pts[3] = Landmark2D(cx - 0.09f, cy - 0.05f)
                pts[4] = Landmark2D(cx - 0.065f, cy - 0.10f)
                pts[6] = Landmark2D(cx - 0.06f, cy - 0.06f)
                pts[7] = Landmark2D(cx - 0.06f, cy - 0.09f)
                pts[8] = Landmark2D(cx - 0.06f, cy - 0.105f) // touching thumb tip
                setFinger(9, true)
                setFinger(13, true)
                setFinger(17, true)
            }
            "point" -> {
                pts[3] = Landmark2D(cx - 0.08f, cy + 0.05f)
                pts[4] = Landmark2D(cx - 0.03f, cy + 0.04f)
                setFinger(5, true)
                setFinger(9, false)
                setFinger(13, false)
                setFinger(17, false)
            }
            "peace" -> {
                pts[3] = Landmark2D(cx - 0.08f, cy + 0.05f)
                pts[4] = Landmark2D(cx - 0.03f, cy + 0.04f)
                setFinger(5, true)
                setFinger(9, true)
                setFinger(13, false)
                setFinger(17, false)
            }
        }
        return pts
    }

    @Test
    fun classifierRecognizesAllFiveHandGesturesIncludingFistBeforePinch() {
        val classifier = HandGestureClassifier(holdFrames = 2, smoothAlpha = 1.0f)

        val cases = listOf(
            "open_palm" to HandGesture.OPEN_PALM,
            "fist" to HandGesture.FIST,
            "pinch" to HandGesture.PINCH,
            "point" to HandGesture.POINT,
            "peace" to HandGesture.PEACE,
        )

        var ts = 100L
        for ((pose, expected) in cases) {
            classifier.reset()
            val hand = build21Hand(pose)
            classifier.processLandmarks(hand, ts, mirrorX = false)
            ts += 50L
            val res = classifier.processLandmarks(hand, ts, mirrorX = false)
            ts += 50L
            assertTrue("Expected hand detected for $pose", res.detected)
            assertEquals("Mismatch for pose=$pose", expected, res.gesture)
        }
    }

    @Test
    fun handGesturesDrivePetEmotionsAndMovement() {
        val physics = EmotionalPetPhysics(
            screenWidth = 1080f,
            screenHeight = 1920f,
            petSizePx = 120f,
            random = Random(11),
        )

        // 1) POINT to the right calls pet to run excitedly
        physics.x = 100f
        repeat(15) {
            physics.applyHandGesture(
                HandGestureFrame(detected = true, gesture = HandGesture.POINT, palmX = 0.85f, palmY = 0.6f),
                1f / 60f,
            )
            physics.step(1f / 60f)
        }
        assertEquals(PetEmotion.EXCITED, physics.emotion)
        assertTrue("Pet should move right toward pointed finger", physics.x > 140f)

        // 2) PINCH grabs pet and drags it into mid-air
        repeat(10) {
            physics.applyHandGesture(
                HandGestureFrame(detected = true, gesture = HandGesture.PINCH, palmX = 0.5f, palmY = 0.25f),
                1f / 60f,
            )
            physics.step(1f / 60f)
        }
        assertTrue(physics.isHandGrabbing)
        assertEquals(PetMood.DRAGGED, physics.mood)
        assertTrue("Pinch should lift pet up into the air", physics.y < 1400f)

        // Release pinch cleanly and let pet settle on floor
        physics.applyHandGesture(HandGestureFrame(detected = false, gesture = HandGesture.NONE), 1f / 60f)
        repeat(240) { physics.step(1f / 60f) }

        // 3) OPEN PALM wave pets with LOVE
        physics.applyHandGesture(
            HandGestureFrame(detected = true, gesture = HandGesture.OPEN_PALM, palmX = 0.5f, palmY = 0.5f, velocityX = 0.9f),
            1f / 60f,
        )
        assertEquals(PetEmotion.LOVE, physics.emotion)

        // 4) Sustained FIST puts pet to sleep (SLEEPY)
        repeat(50) {
            physics.applyHandGesture(
                HandGestureFrame(detected = true, gesture = HandGesture.FIST, palmX = 0.5f, palmY = 0.5f),
                1f / 60f,
            )
            physics.step(1f / 60f)
        }
        assertEquals(PetMood.SLEEP, physics.mood)
        assertEquals(PetEmotion.SLEEPY, physics.emotion)
    }

    @Test
    fun tiltTumbleTriggersScaredDizzyAngryAndLoveCalmsDown() {
        val physics = EmotionalPetPhysics(
            screenWidth = 1080f,
            screenHeight = 1920f,
            petSizePx = 120f,
            random = Random(3),
        )

        // Tilt sideways -> SCARED tumble
        physics.setTiltGravity(9.0f, 3.8f)
        assertEquals(PetMood.TUMBLING, physics.mood)
        assertEquals(PetEmotion.SCARED, physics.emotion)

        repeat(200) { physics.step(1f / 60f) }
        assertTrue(physics.emotion == PetEmotion.DIZZY || physics.mood == PetMood.SPLAT || physics.mood == PetMood.DIZZY)

        // Trigger anger then calm with love
        physics.makeAngry()
        assertEquals(PetEmotion.ANGRY, physics.emotion)
        physics.petWithLove()
        assertEquals(PetEmotion.LOVE, physics.emotion)
    }

    @Test
    fun hubProtocolCodecParsesUrlsAndSerializesUnifiedEmotionsAndHandoff() {
        assertEquals("ws://192.168.1.42:8765", HubProtocolCodec.parseHubUrl("192.168.1.42"))
        assertEquals("ws://192.168.1.42:8765", HubProtocolCodec.parseHubUrl("192.168.1.42:8765"))
        assertEquals("ws://192.168.1.42:8765", HubProtocolCodec.parseHubUrl("http://192.168.1.42:8766"))
        assertEquals(
            "ws://192.168.1.42:8765",
            HubProtocolCodec.parseHubUrl("gcs-shimeji://pair?ws=ws%3A%2F%2F192.168.1.42%3A8765&host=192.168.1.42&port=8765"),
        )

        val stateJson = HubProtocolCodec.buildStateUpdate(
            deviceId = "and-001",
            x = 120f,
            y = 480f,
            animation = "walk",
            state = "ACTIVE",
            facing = "left",
            emotion = "love",
        )
        assertTrue(stateJson.contains("\"emotion\":\"love\""))
        assertTrue(stateJson.contains("\"type\":\"state_update\""))

        val incomingOffer = """
            {
              "type": "handoff_offer",
              "message_id": "m1",
              "timestamp": 100.0,
              "payload": {
                "handoff_id": "ho-999",
                "source_device_id": "win-1",
                "source_device_name": "Gaming-PC",
                "edge": "right",
                "normalized_y": 0.4,
                "timeout_seconds": 6.0,
                "expires_at": 9999999999.0,
                "avatar_state": {"owner": "win-1", "state": "HANDOFF_PENDING", "animation": "handoff", "facing": "right", "emotion": "love"}
              }
            }
        """.trimIndent()
        val parsed = HubProtocolCodec.parseIncoming(incomingOffer) as HubEvent.HandoffOffer
        assertEquals("ho-999", parsed.handoffId)
        assertEquals("Gaming-PC", parsed.sourceDeviceName)
        assertEquals("love", parsed.emotion)
        assertEquals(PetEmotion.LOVE, HubProtocolCodec.petEmotionFromProtocol(parsed.emotion))
    }

    @Test
    fun edgeDragTriggersAndroidToPcHandoffAndReceiveRestoresEmotion() {
        val triggered = mutableListOf<Pair<String, Float>>()
        val physics = EmotionalPetPhysics(
            screenWidth = 1080f,
            screenHeight = 1920f,
            petSizePx = 120f,
            random = Random(7),
            onEdgeHandoff = { edge, ny -> triggered.add(edge to ny) },
        )
        physics.edgeHandoffEnabled = true
        physics.x = 955f
        physics.y = 800f

        physics.startDrag(955f, 800f)
        repeat(10) {
            physics.updateDrag(2f, 0f, 0.06f)
        }
        assertEquals(1, triggered.size)
        assertEquals("right", triggered[0].first)

        // Receive back from PC with ANGRY emotion preserved
        physics.receiveFromHub(edge = "right", normalizedY = 0.5f, incomingEmotion = PetEmotion.ANGRY)
        assertEquals(PetEmotion.ANGRY, physics.emotion)
        assertTrue(physics.x < 100f)
    }
}
