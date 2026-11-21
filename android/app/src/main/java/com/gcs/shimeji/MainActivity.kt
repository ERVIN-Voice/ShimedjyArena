package com.gcs.shimeji

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import com.gcs.shimeji.ui.ShimejiScreen
import com.gcs.shimeji.viewmodel.ShimejiViewModel

class MainActivity : ComponentActivity() {
    private val viewModel: ShimejiViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        handlePairingIntent(intent)

        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                ShimejiScreen(viewModel = viewModel)
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handlePairingIntent(intent)
    }

    private fun handlePairingIntent(intent: Intent?) {
        val uriString = intent?.dataString ?: return
        if (uriString.startsWith("gcs-shimeji://", ignoreCase = true)) {
            viewModel.connectToHub(uriString)
        }
    }
}
