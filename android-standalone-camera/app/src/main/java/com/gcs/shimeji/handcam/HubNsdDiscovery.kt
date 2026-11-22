package com.gcs.shimeji.handcam

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Handler
import android.os.Looper
import android.util.Log

/**
 * Discovers the PC's GCS Local Hub automatically on local Wi-Fi via mDNS/Zeroconf
 * (`_gcs-shimeji._tcp.`).
 */
class HubNsdDiscovery(
    context: Context,
    private val onHubFound: (wsUrl: String, serviceName: String) -> Unit,
    private val onStatus: (String) -> Unit,
) {
    private val nsdManager = context.applicationContext.getSystemService(Context.NSD_SERVICE) as? NsdManager
    private val mainHandler = Handler(Looper.getMainLooper())
    private var discoveryListener: NsdManager.DiscoveryListener? = null
    private var active = false

    fun startDiscovery() {
        val mgr = nsdManager
        if (mgr == null) {
            onStatus("Автопоиск mDNS недоступен на этом устройстве")
            return
        }
        stopDiscovery()
        active = true
        onStatus("Ищем ПК с GCS Shimeji в Wi-Fi...")

        val listener = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(regType: String) {
                Log.d(TAG, "NSD discovery started for $regType")
            }

            override fun onServiceFound(serviceInfo: NsdServiceInfo) {
                if (!serviceInfo.serviceType.contains("gcs-shimeji", ignoreCase = true)) return
                @Suppress("DEPRECATION")
                mgr.resolveService(
                    serviceInfo,
                    object : NsdManager.ResolveListener {
                        override fun onResolveFailed(info: NsdServiceInfo, errorCode: Int) {
                            Log.w(TAG, "Resolve failed ($errorCode) for ${info.serviceName}")
                        }

                        override fun onServiceResolved(info: NsdServiceInfo) {
                            val host = info.host?.hostAddress ?: return
                            val port = info.port.takeIf { it > 0 } ?: HubProtocolCodec.DEFAULT_WS_PORT
                            val wsUrl = "ws://$host:$port"
                            mainHandler.post {
                                onHubFound(wsUrl, info.serviceName)
                                onStatus("Найден ПК: $wsUrl")
                            }
                        }
                    },
                )
            }

            override fun onServiceLost(serviceInfo: NsdServiceInfo) = Unit

            override fun onDiscoveryStopped(serviceType: String) {
                active = false
            }

            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {
                active = false
                mainHandler.post { onStatus("Не удалось запустить поиск Wi-Fi (код $errorCode)") }
            }

            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {
                active = false
            }
        }
        discoveryListener = listener
        runCatching {
            mgr.discoverServices(SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, listener)
        }.onFailure {
            active = false
            onStatus("Ошибка автопоиска: ${it.message}")
        }

        // Stop discovery automatically after 10 seconds to save battery
        mainHandler.postDelayed({ stopDiscovery() }, 10_000L)
    }

    fun stopDiscovery() {
        val mgr = nsdManager ?: return
        val listener = discoveryListener ?: return
        discoveryListener = null
        if (active) {
            runCatching { mgr.stopServiceDiscovery(listener) }
            active = false
        }
    }

    companion object {
        private const val TAG = "HubNsdDiscovery"
        private const val SERVICE_TYPE = "_gcs-shimeji._tcp."
    }
}
