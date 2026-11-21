package com.gcs.shimeji.network

import java.net.URI
import java.net.URLDecoder

object PairingUriParser {
    private const val DEFAULT_PORT = 8765

    /**
     * Normalize any user/QR input into a valid WebSocket URL (`ws://host:port`).
     * Supports:
     * - `gcs-shimeji://pair?ws=ws%3A%2F%2F192.168.1.42%3A8765&host=192.168.1.42&port=8765`
     * - `ws://192.168.1.42:8765`
     * - `192.168.1.42:8765`
     * - `192.168.1.42`
     */
    fun parseToWebSocketUrl(rawInput: String): String? {
        val trimmed = rawInput.trim()
        if (trimmed.isEmpty()) return null

        if (trimmed.startsWith("gcs-shimeji://", ignoreCase = true)) {
            val uri = runCatching { URI(trimmed) }.getOrNull() ?: return null
            val params = parseQuery(uri.rawQuery.orEmpty())
            val wsParam = params["ws"]
            if (!wsParam.isNullOrBlank() && (wsParam.startsWith("ws://") || wsParam.startsWith("wss://"))) {
                return wsParam
            }
            val host = params["host"]
            val port = params["port"]?.toIntOrNull() ?: DEFAULT_PORT
            if (!host.isNullOrBlank()) {
                return "ws://$host:$port"
            }
            return null
        }

        if (trimmed.startsWith("ws://", ignoreCase = true) || trimmed.startsWith("wss://", ignoreCase = true)) {
            val uri = runCatching { URI(trimmed) }.getOrNull() ?: return null
            val host = uri.host ?: return null
            val port = if (uri.port > 0) uri.port else DEFAULT_PORT
            val scheme = uri.scheme.lowercase()
            return "$scheme://$host:$port"
        }

        // Bare host or host:port
        val parts = trimmed.split(":")
        return when (parts.size) {
            1 -> if (parts[0].isNotBlank()) "ws://${parts[0]}:$DEFAULT_PORT" else null
            2 -> {
                val host = parts[0].trim()
                val port = parts[1].trim().toIntOrNull() ?: return null
                if (host.isNotBlank() && port in 1..65535) "ws://$host:$port" else null
            }
            else -> null
        }
    }

    private fun parseQuery(rawQuery: String): Map<String, String> {
        if (rawQuery.isBlank()) return emptyMap()
        val result = mutableMapOf<String, String>()
        for (pair in rawQuery.split("&")) {
            val idx = pair.indexOf('=')
            if (idx <= 0) continue
            val key = URLDecoder.decode(pair.substring(0, idx), "UTF-8")
            val value = URLDecoder.decode(pair.substring(idx + 1), "UTF-8")
            result[key] = value
        }
        return result
    }
}
