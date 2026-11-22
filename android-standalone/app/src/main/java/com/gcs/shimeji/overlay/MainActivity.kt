package com.gcs.shimeji.overlay

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat

class MainActivity : ComponentActivity() {

    private val hasOverlayPermissionState = mutableStateOf(false)
    private lateinit var prefs: OverlayPreferences

    private val notificationPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { _ ->
            // Service still starts cleanly even if user skips notification permission
            if (Settings.canDrawOverlays(this)) {
                ShimejiOverlayService.start(this)
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = OverlayPreferences(this)
        hasOverlayPermissionState.value = Settings.canDrawOverlays(this)

        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                OverlayControlScreen(
                    hasOverlayPermission = hasOverlayPermissionState.value,
                    initialSettings = prefs.load(),
                    onRequestOverlayPermission = { openOverlayPermissionSettings() },
                    onToggleOverlay = { currentlyRunning ->
                        if (currentlyRunning) {
                            ShimejiOverlayService.stop(this)
                        } else {
                            startOverlayWithChecks()
                        }
                    },
                    onTestFall = {
                        if (!OverlayRuntimeState.isRunning.value && Settings.canDrawOverlays(this)) {
                            startOverlayWithChecks()
                        } else {
                            ShimejiOverlayService.triggerTestFall(this)
                        }
                    },
                    onSettingsChanged = { updated ->
                        prefs.save(updated)
                        ShimejiOverlayService.reloadSettings(this)
                    },
                )
            }
        }
    }

    override fun onResume() {
        super.onResume()
        hasOverlayPermissionState.value = Settings.canDrawOverlays(this)
    }

    private fun openOverlayPermissionSettings() {
        val intent = Intent(
            Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
            Uri.parse("package:$packageName"),
        )
        startActivity(intent)
    }

    private fun startOverlayWithChecks() {
        if (!Settings.canDrawOverlays(this)) {
            openOverlayPermissionSettings()
            return
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val granted = ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.POST_NOTIFICATIONS,
            ) == PackageManager.PERMISSION_GRANTED
            if (!granted) {
                notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
                return
            }
        }
        ShimejiOverlayService.start(this)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun OverlayControlScreen(
    hasOverlayPermission: Boolean,
    initialSettings: OverlaySettings,
    onRequestOverlayPermission: () -> Unit,
    onToggleOverlay: (currentlyRunning: Boolean) -> Unit,
    onTestFall: () -> Unit,
    onSettingsChanged: (OverlaySettings) -> Unit,
) {
    val isRunning by OverlayRuntimeState.isRunning.collectAsState()
    var settings by remember { mutableStateOf(initialSettings) }

    fun updateSettings(next: OverlaySettings) {
        settings = next
        onSettingsChanged(next)
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Color(0xFF0F1420))
            .verticalScroll(rememberScrollState())
            .padding(18.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Text(
            text = "Shimeji Overlay",
            color = Color(0xFF66FCF1),
            fontSize = 26.sp,
            fontWeight = FontWeight.ExtraBold,
        )
        Text(
            text = "Пиксельный чубрик поверх всех приложений. Поверните или наклоните телефон — он потеряет равновесие, кувыркнётся и смешно шмякнется!",
            color = Color(0xFF93A7C8),
            fontSize = 14.sp,
            lineHeight = 20.sp,
        )

        // Step 1: Overlay permission card
        if (!hasOverlayPermission) {
            Card(
                colors = CardDefaults.cardColors(containerColor = Color(0xFF281D1E)),
                shape = RoundedCornerShape(16.dp),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Text(
                        text = "Нужно разрешение «Поверх других приложений»",
                        color = Color(0xFFFFB86C),
                        fontWeight = FontWeight.Bold,
                        fontSize = 15.sp,
                    )
                    Text(
                        text = "Без него Android не разрешает рисовать чубрика поверх рабочего стола и других приложений.",
                        color = Color(0xFFEEF3FF),
                        fontSize = 13.sp,
                    )
                    Button(
                        onClick = onRequestOverlayPermission,
                        colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFFFB86C)),
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("Открыть настройки разрешения", color = Color(0xFF14110F), fontWeight = FontWeight.Bold)
                    }
                }
            }
        }

        // Main ON / OFF + Test Fall card
        Card(
            colors = CardDefaults.cardColors(containerColor = Color(0xFF161D2E)),
            shape = RoundedCornerShape(16.dp),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(
                modifier = Modifier.padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = if (isRunning) "Статус: Гуляет поверх экрана" else "Статус: Выключен",
                        color = if (isRunning) Color(0xFF4DE09D) else Color(0xFF93A7C8),
                        fontWeight = FontWeight.Bold,
                        fontSize = 15.sp,
                    )
                }

                Button(
                    onClick = { onToggleOverlay(isRunning) },
                    enabled = hasOverlayPermission || isRunning,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = if (isRunning) Color(0xFFF25F5C) else Color(0xFF4DE09D),
                    ),
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(52.dp),
                ) {
                    Text(
                        text = if (isRunning) "Убрать чубрика с экрана" else "Запустить чубрика поверх экрана",
                        color = Color(0xFF0A111A),
                        fontSize = 15.sp,
                        fontWeight = FontWeight.ExtraBold,
                    )
                }

                OutlinedButton(
                    onClick = onTestFall,
                    enabled = isRunning,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text(
                        text = "Уронить чубрика (тест кувырка!)",
                        color = if (isRunning) Color(0xFF66FCF1) else Color(0xFF55627A),
                        fontWeight = FontWeight.Bold,
                    )
                }
            }
        }

        // Settings card
        Card(
            colors = CardDefaults.cardColors(containerColor = Color(0xFF161D2E)),
            shape = RoundedCornerShape(16.dp),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(
                modifier = Modifier.padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(14.dp),
            ) {
                Text(
                    text = "Настройки физики и размера",
                    color = Color(0xFF66FCF1),
                    fontWeight = FontWeight.Bold,
                    fontSize = 16.sp,
                )

                // Scale selector
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text("Размер пиксельного чубрика", color = Color(0xFFEEF3FF), fontSize = 14.sp)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        listOf(2, 3, 4, 5).forEach { scale ->
                            FilterChip(
                                selected = settings.scale == scale,
                                onClick = { updateSettings(settings.copy(scale = scale)) },
                                label = { Text("${scale}x") },
                            )
                        }
                    }
                }

                // Tilt tumble switch
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text("Смешное падение при повороте", color = Color(0xFFEEF3FF), fontSize = 14.sp)
                        Text(
                            "Реагирует на наклон, переворот и смену ориентации",
                            color = Color(0xFF93A7C8),
                            fontSize = 12.sp,
                        )
                    }
                    Switch(
                        checked = settings.tiltPhysicsEnabled,
                        onCheckedChange = { updateSettings(settings.copy(tiltPhysicsEnabled = it)) },
                    )
                }

                // Sensitivity slider
                if (settings.tiltPhysicsEnabled) {
                    Column {
                        Text(
                            text = "Чувствительность наклона: ${(settings.tiltSensitivity * 100).toInt()}%",
                            color = Color(0xFFEEF3FF),
                            fontSize = 13.sp,
                        )
                        Slider(
                            value = settings.tiltSensitivity,
                            onValueChange = { updateSettings(settings.copy(tiltSensitivity = it)) },
                            valueRange = 0.2f..1.0f,
                        )
                    }
                }

                // Bounciness slider
                Column {
                    Text(
                        text = "Прыгучесть и кувырки: ${(settings.bounciness * 100).toInt()}%",
                        color = Color(0xFFEEF3FF),
                        fontSize = 13.sp,
                    )
                    Slider(
                        value = settings.bounciness,
                        onValueChange = { updateSettings(settings.copy(bounciness = it)) },
                        valueRange = 0.25f..1.0f,
                    )
                }

                // Haptic bonk toggle
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text("Лёгкая вибрация при «БУМ!»", color = Color(0xFFEEF3FF), fontSize = 14.sp)
                    Switch(
                        checked = settings.hapticBonkEnabled,
                        onCheckedChange = { updateSettings(settings.copy(hapticBonkEnabled = it)) },
                    )
                }
            }
        }

        // Gestures hint card
        Card(
            colors = CardDefaults.cardColors(containerColor = Color(0xFF131A29)),
            shape = RoundedCornerShape(14.dp),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(
                modifier = Modifier.padding(14.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Text("Что умеет чубрик:", color = Color(0xFF4DE09D), fontWeight = FontWeight.Bold, fontSize = 14.sp)
                Text("• Поворот / наклон телефона → теряет равновесие, крутится и шмякается с звёздочками", color = Color(0xFFCBD7EE), fontSize = 13.sp)
                Text("• Схватить пальцем → можно таскать и кидать об стенки экрана", color = Color(0xFFCBD7EE), fontSize = 13.sp)
                Text("• 1 тап → прыжок, 2 тапа → смешной кувырок на месте", color = Color(0xFFCBD7EE), fontSize = 13.sp)
                Text("• Не мешает нажимать на экран вокруг него", color = Color(0xFFCBD7EE), fontSize = 13.sp)
            }
        }

        Spacer(Modifier.height(16.dp))
    }
}
