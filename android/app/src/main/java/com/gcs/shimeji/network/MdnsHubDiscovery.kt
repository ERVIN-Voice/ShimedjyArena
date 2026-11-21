package com.gcs.shimeji.network

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.util.Log
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

data class DiscoveredHub(
    val serviceName: String,
    val host: String,
    val port: Int,
    val webPort: Int = port + 1,
) {
    val wsUrl: String get() = "ws://$host:$port"
    val webUrl: String get() = "http://$host:$webPort"
}

/**
 * Discovers local GCS Shimeji Hub instances via Android mDNS / Network Service Discovery.
 */
class MdnsHubDiscovery(context: Context) {
    companion object {
        private const val TAG = "MdnsHubDiscovery"
        private const val SERVICE_TYPE = "_gcs-shimeji._tcp."
    }

    private val nsdManager = context.getSystemService(Context.NSD_SERVICE) as? NsdManager
    private var discoveryListener: NsdManager.DiscoveryListener? = null
    private var isDiscovering = false

    private val _hubs = MutableStateFlow<List<DiscoveredHub>>(emptyList())
    val hubs: StateFlow<List<DiscoveredHub>> = _hubs.asStateFlow()

    fun startDiscovery() {
        val manager = nsdManager ?: return
        if (isDiscovering) return

        val listener = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(regType: String) {
                isDiscovering = true
                Log.i(TAG, "mDNS discovery started for $regType")
            }

            override fun onServiceFound(serviceInfo: NsdServiceInfo) {
                if (serviceInfo.serviceType != SERVICE_TYPE) return
                @Suppress("DEPRECATION")
                manager.resolveService(
                    serviceInfo,
                    object : NsdManager.ResolveListener {
                        override fun onResolveFailed(info: NsdServiceInfo, errorCode: Int) {
                            Log.w(TAG, "mDNS resolve failed for ${info.serviceName}: code=$errorCode")
                        }

                        override fun onServiceResolved(info: NsdServiceInfo) {
                            val hostAddress = info.host?.hostAddress ?: return
                            val txtWebPort = runCatching {
                                info.attributes["web_port"]?.decodeToString()?.toIntOrNull()
                            }.getOrNull()
                            val found = DiscoveredHub(
                                serviceName = info.serviceName,
                                host = hostAddress,
                                port = info.port,
                                webPort = txtWebPort ?: (info.port + 1),
                            )
                            val updated = _hubs.value
                                .filterNot { it.serviceName == found.serviceName } + found
                            _hubs.value = updated
                            Log.i(TAG, "Resolved Hub via mDNS: ws=${found.wsUrl}, web=${found.webUrl}")
                        }
                    }
                )
            }

            override fun onServiceLost(serviceInfo: NsdServiceInfo) {
                _hubs.value = _hubs.value.filterNot { it.serviceName == serviceInfo.serviceName }
            }

            override fun onDiscoveryStopped(serviceType: String) {
                isDiscovering = false
            }

            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {
                isDiscovering = false
                Log.w(TAG, "mDNS startDiscoveryFailed: code=$errorCode")
            }

            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {
                isDiscovering = false
            }
        }

        discoveryListener = listener
        runCatching {
            manager.discoverServices(SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, listener)
        }.onFailure { err ->
            Log.w(TAG, "Unable to start mDNS discovery: ${err.message}")
        }
    }

    fun stopDiscovery() {
        val manager = nsdManager ?: return
        val listener = discoveryListener ?: return
        if (isDiscovering) {
            runCatching { manager.stopServiceDiscovery(listener) }
        }
        isDiscovering = false
        discoveryListener = null
    }
}
