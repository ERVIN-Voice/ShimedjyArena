package com.gcs.shimeji.network

import java.net.URI
import java.net.URLDecoder

object PairingUriParser {
    private const val DEFAULT_PORT = 8765
    private const val DEFAULT_WEB_PORT = 8766

    /**
     * Normalize any user/QR input into the local HTTP Web Companion URL (`http://host:web_port`).
     * Supports:
     * - `http://192.168.1.42:8766` or `https://...`
     * - `gcs-shimeji://pair?ws=...&web=http%3A%2F%2F192.168.1.42%3A8766&web_port=8766`
     * - `ws://192.168.1.42:8765` (maps to `http://192.168.1.42:8766`)
     * - `192.168.1.42:8766`
     * - `192.168.1.42` (defaults to port 8766)
     */
    fun parseToWebAppUrl(rawInput: String): String? {
        val trimmed = rawInput.trim()
        if (trimmed.isEmpty()) return null

        if (trimmed.startsWith("http://", ignoreCase = true) || trimmed.startsWith("https://", ignoreCase = true)) {
            val uri = runCatching { URI(trimmed) }.getOrNull() ?: return null
            val host = uri.host ?: return null
            val port = if (uri.port > 0) uri.port else DEFAULT_WEB_PORT
            val scheme = uri.scheme.lowercase()
            return "$scheme://$host:$port"
        }

        if (trimmed.startsWith("gcs-shimeji://", ignoreCase = true)) {
            val uri = runCatching { URI(trimmed) }.getOrNull() ?: return null
            val params = parseQuery(uri.rawQuery.orEmpty())
            val webParam = params["web"]
            if (!webParam.isNullOrBlank() && (webParam.startsWith("http://") || webParam.startsWith("https://"))) {
                return webParam
            }
            val host = params["host"] ?: run {
                val wsParam = params["ws"]
                wsParam?.let { runCatching { URI(it).host }.getOrNull() }
            }
            val wsPort = params["port"]?.toIntOrNull() ?: DEFAULT_PORT
            val webPort = params["web_port"]?.toIntOrNull() ?: (wsPort + 1)
            if (!host.isNullOrBlank() && webPort in 1..65535) {
                return "http://$host:$webPort"
            }
            return null
        }

        if (trimmed.startsWith("ws://", ignoreCase = true) || trimmed.startsWith("wss://", ignoreCase = true)) {
            val uri = runCatching { URI(trimmed) }.getOrNull() ?: return null
            val host = uri.host ?: return null
            val wsPort = if (uri.port > 0) uri.port else DEFAULT_PORT
            return "http://$host:${wsPort + 1}"
        }

        val parts = trimmed.split(":")
        return when (parts.size) {
            1 -> if (parts[0].isNotBlank()) "http://${parts[0]}:$DEFAULT_WEB_PORT" else null
            2 -> {
                val host = parts[0].trim()
                val port = parts[1].trim().toIntOrNull() ?: return null
                if (host.isNotBlank() && port in 1..65535) {
                    val webPort = if (port == DEFAULT_PORT) DEFAULT_WEB_PORT else port
                    "http://$host:$webPort"
                } else {
                    null
                }
            }
            else -> null
        }
    }

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
