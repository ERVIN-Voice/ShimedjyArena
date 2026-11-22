package com.gcs.shimeji.ui

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Color
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color as ComposeColor
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import com.gcs.shimeji.network.DiscoveredHub
import com.gcs.shimeji.network.MdnsHubDiscovery
import com.gcs.shimeji.network.PairingUriParser
import java.util.UUID

private const val PREFS_NAME = "gcs_shimeji_web_prefs"
private const val KEY_WEB_URL = "last_hub_web_url"
private const val KEY_DEVICE_ID = "stable_android_device_id"

/**
 * Native JavaScript bridge exposed as `window.GcsAndroidBridge` inside the WebView.
 */
class GcsAndroidBridge(
    private val context: Context,
    private val deviceId: String,
    private val onStatusUpdate: (status: String, message: String) -> Unit,
) {
    @JavascriptInterface
    fun getDeviceId(): String = deviceId

    @JavascriptInterface
    fun getDeviceName(): String {
        val model = Build.MODEL?.trim().orEmpty()
        return if (model.isNotEmpty()) "Android ($model)" else "Android App"
    }

    @JavascriptInterface
    fun vibrate(kind: String) {
        val vibrator: Vibrator? = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val mgr = context.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager
            mgr?.defaultVibrator
        } else {
            @Suppress("DEPRECATION")
            context.getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
        }
        if (vibrator == null || !vibrator.hasVibrator()) return

        val durationMs = when (kind) {
            "offer" -> 80L
            "confirm" -> 45L
            else -> 22L
        }
        runCatching {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                vibrator.vibrate(VibrationEffect.createOneShot(durationMs, VibrationEffect.DEFAULT_AMPLITUDE))
            } else {
                @Suppress("DEPRECATION")
                vibrator.vibrate(durationMs)
            }
        }
    }

    @JavascriptInterface
    fun onConnectionStatus(status: String, message: String) {
        Handler(Looper.getMainLooper()).post {
            onStatusUpdate(status, message)
        }
    }
}

@SuppressLint("SetJavaScriptEnabled")
@Composable
fun ShimejiWebScreen(
    incomingDeepLink: String?,
    onSwitchToNative: () -> Unit,
) {
    val context = LocalContext.current
    val prefs = remember { context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE) }
    val stableDeviceId = remember {
        prefs.getString(KEY_DEVICE_ID, null) ?: run {
            val generated = "android-app-${UUID.randomUUID().toString().take(8)}"
            prefs.edit().putString(KEY_DEVICE_ID, generated).apply()
            generated
        }
    }

    val mdns = remember { MdnsHubDiscovery(context.applicationContext) }
    val discoveredHubs by mdns.hubs.collectAsState()

    var activeWebUrl by remember { mutableStateOf(prefs.getString(KEY_WEB_URL, "") ?: "") }
    var inputText by remember { mutableStateOf(activeWebUrl.ifBlank { "192.168.1.100:8766" }) }
    var showConfigDrawer by remember { mutableStateOf(activeWebUrl.isBlank()) }
    var loadError by remember { mutableStateOf<String?>(null) }
    var webStatus by remember { mutableStateOf("idle") }
    var webViewRef by remember { mutableStateOf<WebView?>(null) }

    DisposableEffect(Unit) {
        mdns.startDiscovery()
        onDispose { mdns.stopDiscovery() }
    }

    // Handle QR / deep-link intents immediately
    LaunchedEffect(incomingDeepLink) {
        val raw = incomingDeepLink ?: return@LaunchedEffect
        val parsed = PairingUriParser.parseToWebAppUrl(raw) ?: return@LaunchedEffect
        activeWebUrl = parsed
        inputText = parsed
        prefs.edit().putString(KEY_WEB_URL, parsed).apply()
        loadError = null
        showConfigDrawer = false
    }

    // Auto-select the first discovered LAN Hub if user hasn't configured one yet
    LaunchedEffect(discoveredHubs) {
        if (activeWebUrl.isBlank() && discoveredHubs.isNotEmpty()) {
            val first = discoveredHubs.first()
            activeWebUrl = first.webUrl
            inputText = first.webUrl
            prefs.edit().putString(KEY_WEB_URL, first.webUrl).apply()
            loadError = null
            showConfigDrawer = false
        }
    }

    fun openUrl(raw: String) {
        val parsed = PairingUriParser.parseToWebAppUrl(raw)
        if (parsed != null) {
            activeWebUrl = parsed
            inputText = parsed
            prefs.edit().putString(KEY_WEB_URL, parsed).apply()
            loadError = null
            showConfigDrawer = false
            webViewRef?.loadUrl(parsed)
        } else {
            loadError = "Invalid Hub IP or URL. Example: 192.168.1.42 or http://192.168.1.42:8766"
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(ComposeColor(0xFF0F1420))
    ) {
        // Slim native wrapper control strip
        Surface(
            color = ComposeColor(0xFF121826),
            tonalElevation = 2.dp,
            modifier = Modifier.fillMaxWidth()
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 10.dp, vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text(
                    text = if (activeWebUrl.isBlank()) "GCS Web App — Select Hub" else "Web App • ${webStatus.uppercase()}",
                    color = ComposeColor(0xFF93A7C8),
                    fontSize = 11.sp,
                    fontWeight = FontWeight.SemiBold,
                )
                Row(verticalAlignment = Alignment.CenterVertically) {
                    TextButton(onClick = { showConfigDrawer = !showConfigDrawer }) {
                        Text(
                            text = if (showConfigDrawer) "Hide Hub" else "Hub IP",
                            color = ComposeColor(0xFF66FCF1),
                            fontSize = 12.sp
                        )
                    }
                    TextButton(onClick = {
                        loadError = null
                        if (activeWebUrl.isNotBlank()) webViewRef?.reload()
                    }) {
                        Text("Reload", color = ComposeColor(0xFF93A7C8), fontSize = 12.sp)
                    }
                    TextButton(onClick = onSwitchToNative) {
                        Text("Native", color = ComposeColor(0xFF93A7C8), fontSize = 12.sp)
                    }
                }
            }
        }

        // Collapsible Hub discovery + IP entry drawer
        if (showConfigDrawer) {
            HubConfigCard(
                inputText = inputText,
                onInputChange = { inputText = it },
                discoveredHubs = discoveredHubs,
                onConnect = { openUrl(inputText) },
                onSelectHub = { hub -> openUrl(hub.webUrl) }
            )
        }

        // Full-screen WebView hosting the live Windows GCS Web Client
        Box(modifier = Modifier.weight(1f).fillMaxWidth()) {
            if (activeWebUrl.isNotBlank()) {
                AndroidView(
                    modifier = Modifier.fillMaxSize(),
                    factory = { ctx ->
                        WebView(ctx).apply {
                            setBackgroundColor(Color.parseColor("#0F1420"))
                            settings.javaScriptEnabled = true
                            settings.domStorageEnabled = true
                            settings.databaseEnabled = true
                            settings.cacheMode = WebSettings.LOAD_NO_CACHE
                            settings.mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
                            settings.useWideViewPort = true
                            settings.loadWithOverviewMode = true
                            settings.setSupportZoom(false)

                            addJavascriptInterface(
                                GcsAndroidBridge(
                                    context = ctx.applicationContext,
                                    deviceId = stableDeviceId,
                                    onStatusUpdate = { status, _ -> webStatus = status }
                                ),
                                "GcsAndroidBridge"
                            )

                            webChromeClient = WebChromeClient()
                            webViewClient = object : WebViewClient() {
                                override fun onPageFinished(view: WebView?, url: String?) {
                                    super.onPageFinished(view, url)
                                }

                                override fun onReceivedError(
                                    view: WebView?,
                                    request: WebResourceRequest?,
                                    error: WebResourceError?,
                                ) {
                                    if (request?.isForMainFrame == true) {
                                        loadError = "Cannot reach $activeWebUrl (${error?.description ?: "network error"}).\nMake sure GCS Shimeji is running on Windows on the same Wi-Fi."
                                    }
                                }
                            }
                            loadUrl(activeWebUrl)
                            webViewRef = this
                        }
                    },
                    update = { webView ->
                        webViewRef = webView
                        if (webView.url != activeWebUrl && loadError == null) {
                            webView.loadUrl(activeWebUrl)
                        }
                    }
                )
            } else {
                // Waiting for Hub IP or mDNS discovery
                Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text(
                        text = "Searching Wi-Fi for Windows GCS Hub...\nOr enter your Windows PC IP above.",
                        color = ComposeColor(0xFF93A7C8),
                        fontSize = 14.sp,
                        modifier = Modifier.padding(24.dp)
                    )
                }
            }

            // Friendly retry card if Hub is offline/unreachable
            if (loadError != null) {
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .background(ComposeColor(0xDD0F1420))
                        .padding(20.dp),
                    contentAlignment = Alignment.Center
                ) {
                    Card(
                        colors = CardDefaults.cardColors(containerColor = ComposeColor(0xFF172338)),
                        shape = RoundedCornerShape(16.dp)
                    ) {
                        Column(
                            modifier = Modifier.padding(20.dp),
                            horizontalAlignment = Alignment.CenterHorizontally
                        ) {
                            Text(
                                text = "Waiting for Windows Hub",
                                color = ComposeColor(0xFF66FCF1),
                                fontSize = 18.sp,
                                fontWeight = FontWeight.Bold
                            )
                            Spacer(Modifier.height(8.dp))
                            Text(
                                text = loadError.orEmpty(),
                                color = ComposeColor(0xFFEEF3FF),
                                fontSize = 13.sp
                            )
                            Spacer(Modifier.height(14.dp))
                            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                                Button(
                                    onClick = {
                                        loadError = null
                                        if (activeWebUrl.isNotBlank()) webViewRef?.loadUrl(activeWebUrl)
                                    },
                                    colors = ButtonDefaults.buttonColors(containerColor = ComposeColor(0xFF4DE09D))
                                ) {
                                    Text("Retry", color = ComposeColor(0xFF091410), fontWeight = FontWeight.Bold)
                                }
                                OutlinedButton(onClick = { showConfigDrawer = true }) {
                                    Text("Change IP", color = ComposeColor(0xFF66FCF1))
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun HubConfigCard(
    inputText: String,
    onInputChange: (String) -> Unit,
    discoveredHubs: List<DiscoveredHub>,
    onConnect: () -> Unit,
    onSelectHub: (DiscoveredHub) -> Unit,
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 8.dp),
        colors = CardDefaults.cardColors(containerColor = ComposeColor(0xFF161D2E)),
        shape = RoundedCornerShape(12.dp)
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text(
                text = "Windows GCS Hub Address (Local Wi-Fi)",
                color = ComposeColor(0xFF66FCF1),
                fontSize = 13.sp,
                fontWeight = FontWeight.Bold
            )
            Spacer(Modifier.height(8.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = inputText,
                    onValueChange = onInputChange,
                    singleLine = true,
                    placeholder = { Text("192.168.1.42:8766") },
                    modifier = Modifier.weight(1f)
                )
                Spacer(Modifier.width(8.dp))
                Button(
                    onClick = onConnect,
                    colors = ButtonDefaults.buttonColors(containerColor = ComposeColor(0xFF66FCF1))
                ) {
                    Text("Open", color = ComposeColor(0xFF0B111C), fontWeight = FontWeight.Bold)
                }
            }
            if (discoveredHubs.isNotEmpty()) {
                Spacer(Modifier.height(8.dp))
                Text(
                    text = "Discovered on LAN:",
                    color = ComposeColor(0xFF93A7C8),
                    fontSize = 11.sp
                )
                Spacer(Modifier.height(4.dp))
                discoveredHubs.forEach { hub ->
                    OutlinedButton(
                        onClick = { onSelectHub(hub) },
                        modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp)
                    ) {
                        Text(
                            text = "${hub.serviceName}  (${hub.webUrl})",
                            color = ComposeColor(0xFF4DE09D),
                            fontSize = 12.sp
                        )
                    }
                }
            }
        }
    }
}
