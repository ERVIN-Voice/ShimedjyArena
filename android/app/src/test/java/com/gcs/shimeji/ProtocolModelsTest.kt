package com.gcs.shimeji

import com.gcs.shimeji.camera.AndroidGestureType
import com.gcs.shimeji.camera.AndroidHandoffGestureGate
import com.gcs.shimeji.model.GcsJson
import com.gcs.shimeji.model.ProtocolBuilder
import com.gcs.shimeji.model.WireEnvelope
import kotlinx.serialization.encodeToString
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ProtocolModelsTest {
    @Test
    fun buildsValidRegisterAndHandoffEnvelopes() {
        val reg = ProtocolBuilder.registerDevice("and-1", "Pixel", 1080, 2400)
        val encoded = GcsJson.encodeToString(reg)
        val decoded = GcsJson.decodeFromString(WireEnvelope.serializer(), encoded)
        assertEquals("register_device", decoded.type)

        val ho = ProtocolBuilder.handoffRequest("and-1", "right", 0.5, idempotencyKey = "k-1")
        assertEquals("handoff_request", ho.type)
    }

    @Test
    fun gestureGateNeverFiresWithoutPendingOffer() {
        val confirmed = mutableListOf<String>()
        val gate = AndroidHandoffGestureGate(minConfidence = 0.7f) { id ->
            confirmed.add(id)
        }

        // No pending offer -> RECEIVE_PET must be ignored
        assertFalse(gate.onGestureDetected(AndroidGestureType.RECEIVE_PET, 0.99f))
        assertTrue(confirmed.isEmpty())

        // With pending offer -> PINCH must not confirm; RECEIVE_PET with sufficient confidence confirms once
        gate.setPendingOffer("ho-123")
        assertFalse(gate.onGestureDetected(AndroidGestureType.PINCH, 0.99f))
        assertFalse(gate.onGestureDetected(AndroidGestureType.RECEIVE_PET, 0.4f))
        assertTrue(gate.onGestureDetected(AndroidGestureType.RECEIVE_PET, 0.85f))
        assertEquals(listOf("ho-123"), confirmed)

        // Second gesture must not double-confirm
        assertFalse(gate.onGestureDetected(AndroidGestureType.RECEIVE_PET, 0.99f))
        assertEquals(1, confirmed.size)
    }
}
