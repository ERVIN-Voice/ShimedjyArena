package com.gcs.shimeji

import android.content.Intent
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SmallFloatingActionButton
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.gcs.shimeji.ui.ShimejiScreen
import com.gcs.shimeji.ui.ShimejiWebScreen
import com.gcs.shimeji.viewmodel.ShimejiViewModel

class MainActivity : ComponentActivity() {
    private val viewModel: ShimejiViewModel by viewModels()

    /** Default to embedded Web version inside the Android app. */
    private var useWebMode by mutableStateOf(true)
    private var incomingDeepLink by mutableStateOf<String?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        handlePairingIntent(intent)

        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                if (useWebMode) {
                    ShimejiWebScreen(
                        incomingDeepLink = incomingDeepLink,
                        onSwitchToNative = { useWebMode = false },
                    )
                } else {
                    Box(modifier = Modifier.fillMaxSize()) {
                        ShimejiScreen(viewModel = viewModel)
                        SmallFloatingActionButton(
                            onClick = { useWebMode = true },
                            modifier = Modifier
                                .align(Alignment.BottomEnd)
                                .padding(14.dp)
                        ) {
                            Text("Web", fontSize = 11.sp)
                        }
                    }
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handlePairingIntent(intent)
    }

    private fun handlePairingIntent(intent: Intent?) {
        val uriString = intent?.dataString ?: return
        if (
            uriString.startsWith("gcs-shimeji://", ignoreCase = true) ||
            uriString.startsWith("http://", ignoreCase = true) ||
            uriString.startsWith("https://", ignoreCase = true)
        ) {
            incomingDeepLink = uriString
            if (!useWebMode) {
                viewModel.connectToHub(uriString)
            }
        }
    }
}
