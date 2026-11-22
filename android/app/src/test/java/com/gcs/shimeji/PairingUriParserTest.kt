package com.gcs.shimeji

import com.gcs.shimeji.network.PairingUriParser
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PairingUriParserTest {
    @Test
    fun parsesQrPairingUriWithEncodedWsParam() {
        val uri = "gcs-shimeji://pair?ws=ws%3A%2F%2F192.168.1.42%3A8765&host=192.168.1.42&port=8765&protocol=1.0.0"
        assertEquals("ws://192.168.1.42:8765", PairingUriParser.parseToWebSocketUrl(uri))
    }

    @Test
    fun parsesHostPortAndBareIp() {
        assertEquals("ws://192.168.0.15:9000", PairingUriParser.parseToWebSocketUrl("192.168.0.15:9000"))
        assertEquals("ws://192.168.0.15:8765", PairingUriParser.parseToWebSocketUrl("192.168.0.15"))
        assertEquals("ws://10.0.0.5:8765", PairingUriParser.parseToWebSocketUrl("ws://10.0.0.5:8765"))
    }

    @Test
    fun rejectsBlankAndInvalidPort() {
        assertNull(PairingUriParser.parseToWebSocketUrl("   "))
        assertNull(PairingUriParser.parseToWebSocketUrl("192.168.0.15:99999"))
    }

    @Test
    fun parsesWebAppUrlsFromHttpQrWsAndIp() {
        assertEquals(
            "http://192.168.1.42:8766",
            PairingUriParser.parseToWebAppUrl("http://192.168.1.42:8766"),
        )
        assertEquals(
            "http://192.168.1.42:8766",
            PairingUriParser.parseToWebAppUrl(
                "gcs-shimeji://pair?ws=ws%3A%2F%2F192.168.1.42%3A8765&web=http%3A%2F%2F192.168.1.42%3A8766&web_port=8766"
            ),
        )
        assertEquals(
            "http://192.168.1.42:8766",
            PairingUriParser.parseToWebAppUrl("ws://192.168.1.42:8765"),
        )
        assertEquals(
            "http://192.168.1.42:8766",
            PairingUriParser.parseToWebAppUrl("192.168.1.42"),
        )
        assertNull(PairingUriParser.parseToWebAppUrl("   "))
    }
}
