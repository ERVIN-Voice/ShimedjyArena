package com.gcs.shimeji.handcam

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
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
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
    private val hasCameraPermissionState = mutableStateOf(false)
    private lateinit var prefs: HandCamPreferences

    private val runtimePermissionsLauncher =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { _ ->
            refreshPermissions()
            if (Settings.canDrawOverlays(this)) {
                if (HandCamRuntimeState.isRunning.value) {
                    ShimejiHandCamService.sendAction(this, ShimejiHandCamService.ACTION_RELOAD_SETTINGS)
                } else {
                    ShimejiHandCamService.start(this)
                }
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = HandCamPreferences(this)
        refreshPermissions()

        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                HandCamControlScreen(
                    hasOverlayPermission = hasOverlayPermissionState.value,
                    hasCameraPermission = hasCameraPermissionState.value,
                    initialSettings = prefs.load(),
                    onRequestOverlayPermission = { openOverlayPermissionSettings() },
                    onRequestCameraPermission = { requestCameraAndNotificationPermissions() },
                    onToggleOverlay = { currentlyRunning ->
                        if (currentlyRunning) {
                            ShimejiHandCamService.stop(this)
                        } else {
                            startOverlayWithChecks()
                        }
                    },
                    onAction = { action ->
                        ShimejiHandCamService.sendAction(this, action)
                    },
                    onSettingsChanged = { updated ->
                        prefs.save(updated)
                        ShimejiHandCamService.sendAction(this, ShimejiHandCamService.ACTION_RELOAD_SETTINGS)
                    },
                )
            }
        }
    }

    override fun onResume() {
        super.onResume()
        refreshPermissions()
    }

    private fun refreshPermissions() {
        hasOverlayPermissionState.value = Settings.canDrawOverlays(this)
        hasCameraPermissionState.value = ContextCompat.checkSelfPermission(
            this,
            Manifest.permission.CAMERA,
        ) == PackageManager.PERMISSION_GRANTED
    }

    private fun openOverlayPermissionSettings() {
        val intent = Intent(
            Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
            Uri.parse("package:$packageName"),
        )
        startActivity(intent)
    }

    private fun requestCameraAndNotificationPermissions() {
        val needed = mutableListOf<String>()
        if (!hasCameraPermissionState.value) {
            needed.add(Manifest.permission.CAMERA)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val notifOk = ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.POST_NOTIFICATIONS,
            ) == PackageManager.PERMISSION_GRANTED
            if (!notifOk) needed.add(Manifest.permission.POST_NOTIFICATIONS)
        }
        if (needed.isNotEmpty()) {
            runtimePermissionsLauncher.launch(needed.toTypedArray())
        }
    }

    private fun startOverlayWithChecks() {
        if (!Settings.canDrawOverlays(this)) {
            openOverlayPermissionSettings()
            return
        }
        val needed = mutableListOf<String>()
        if (prefs.load().cameraControlEnabled && !hasCameraPermissionState.value) {
            needed.add(Manifest.permission.CAMERA)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val notifOk = ContextCompat.checkSelfPermission(
                this,
                Manifest.permission.POST_NOTIFICATIONS,
            ) == PackageManager.PERMISSION_GRANTED
            if (!notifOk) needed.add(Manifest.permission.POST_NOTIFICATIONS)
        }
        if (needed.isNotEmpty()) {
            runtimePermissionsLauncher.launch(needed.toTypedArray())
            return
        }
        ShimejiHandCamService.start(this)
    }
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
private fun HandCamControlScreen(
    hasOverlayPermission: Boolean,
    hasCameraPermission: Boolean,
    initialSettings: HandCamSettings,
    onRequestOverlayPermission: () -> Unit,
    onRequestCameraPermission: () -> Unit,
    onToggleOverlay: (currentlyRunning: Boolean) -> Unit,
    onAction: (String) -> Unit,
    onSettingsChanged: (HandCamSettings) -> Unit,
) {
    val isRunning by HandCamRuntimeState.isRunning.collectAsState()
    val currentEmotion by HandCamRuntimeState.emotion.collectAsState()
    val gestureLabel by HandCamRuntimeState.gestureLabel.collectAsState()
    val cameraStatus by HandCamRuntimeState.cameraStatus.collectAsState()

    var settings by remember { mutableStateOf(initialSettings) }

    fun updateSettings(next: HandCamSettings) {
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
            text = "Shimeji Hand Cam",
            color = Color(0xFF66FCF1),
            fontSize = 26.sp,
            fontWeight = FontWeight.ExtraBold,
        )
        Text(
            text = "Чубрик поверх всех приложений с живыми эмоциями, смешными падениями при повороте и управлением ладонью через камеру (100% оффлайн на телефоне).",
            color = Color(0xFF93A7C8),
            fontSize = 13.sp,
            lineHeight = 19.sp,
        )

        // Permissions cards
        if (!hasOverlayPermission) {
            Card(
                colors = CardDefaults.cardColors(containerColor = Color(0xFF281D1E)),
                shape = RoundedCornerShape(16.dp),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(
                    modifier = Modifier.padding(14.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text(
                        text = "Шаг 1: Разрешение «Поверх других приложений»",
                        color = Color(0xFFFFB86C),
                        fontWeight = FontWeight.Bold,
                        fontSize = 14.sp,
                    )
                    Button(
                        onClick = onRequestOverlayPermission,
                        colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFFFB86C)),
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("Открыть настройки наложения", color = Color(0xFF14110F), fontWeight = FontWeight.Bold)
                    }
                }
            }
        }

        if (!hasCameraPermission) {
            Card(
                colors = CardDefaults.cardColors(containerColor = Color(0xFF192438)),
                shape = RoundedCornerShape(16.dp),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(
                    modifier = Modifier.padding(14.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text(
                        text = "Шаг 2: Доступ к камере для управления ладонью",
                        color = Color(0xFF66FCF1),
                        fontWeight = FontWeight.Bold,
                        fontSize = 14.sp,
                    )
                    Text(
                        text = "Камера обрабатывается полностью локально на телефоне (ничего никуда не отправляется).",
                        color = Color(0xFFCBD7EE),
                        fontSize = 12.sp,
                    )
                    OutlinedButton(
                        onClick = onRequestCameraPermission,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("Разрешить камеру", color = Color(0xFF66FCF1), fontWeight = FontWeight.Bold)
                    }
                }
            }
        }

        // Main status + ON/OFF card
        Card(
            colors = CardDefaults.cardColors(containerColor = Color(0xFF161D2E)),
            shape = RoundedCornerShape(16.dp),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(
                modifier = Modifier.padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Text(
                    text = if (isRunning) "Статус: Гуляет поверх экрана" else "Статус: Выключен",
                    color = if (isRunning) Color(0xFF4DE09D) else Color(0xFF93A7C8),
                    fontWeight = FontWeight.Bold,
                    fontSize = 15.sp,
                )
                Text(
                    text = "Эмоция: ${currentEmotion.russianLabel}",
                    color = Color(0xFFFFB8D9),
                    fontSize = 13.sp,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(
                    text = "Камера: $cameraStatus  •  Жест: $gestureLabel",
                    color = Color(0xFF93A7C8),
                    fontSize = 12.sp,
                )

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
                        text = if (isRunning) "Выключить чубрика и камеру" else "Запустить чубрика поверх экрана",
                        color = Color(0xFF0A111A),
                        fontSize = 15.sp,
                        fontWeight = FontWeight.ExtraBold,
                    )
                }
            }
        }

        // Quick emotion & tumble test buttons
        Card(
            colors = CardDefaults.cardColors(containerColor = Color(0xFF161D2E)),
            shape = RoundedCornerShape(16.dp),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(
                modifier = Modifier.padding(14.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Text(
                    text = "Поиграть с эмоциями и физикой",
                    color = Color(0xFF66FCF1),
                    fontWeight = FontWeight.Bold,
                    fontSize = 15.sp,
                )
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    OutlinedButton(
                        enabled = isRunning,
                        onClick = { onAction(ShimejiHandCamService.ACTION_EMOTE_LOVE) },
                    ) { Text("♥ Погладить") }
                    OutlinedButton(
                        enabled = isRunning,
                        onClick = { onAction(ShimejiHandCamService.ACTION_EMOTE_HAPPY) },
                    ) { Text("✨ Прыг!") }
                    OutlinedButton(
                        enabled = isRunning,
                        onClick = { onAction(ShimejiHandCamService.ACTION_EMOTE_ANGRY) },
                    ) { Text("💢 Разозлить") }
                    OutlinedButton(
                        enabled = isRunning,
                        onClick = { onAction(ShimejiHandCamService.ACTION_EMOTE_SLEEP) },
                    ) { Text("😴 Усыпить") }
                    OutlinedButton(
                        enabled = isRunning,
                        onClick = { onAction(ShimejiHandCamService.ACTION_TEST_FALL) },
                    ) { Text("🌀 Уронить!") }
                }
            }
        }

        // Camera Hand Control settings card
        Card(
            colors = CardDefaults.cardColors(containerColor = Color(0xFF161D2E)),
            shape = RoundedCornerShape(16.dp),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(
                modifier = Modifier.padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Text(
                    text = "Управление ладонью через камеру",
                    color = Color(0xFF66FCF1),
                    fontWeight = FontWeight.Bold,
                    fontSize = 16.sp,
                )

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text("Включить трекинг ладони (AI)", color = Color(0xFFEEF3FF), fontSize = 14.sp)
                        Text("Распознаёт 21 точку ладони без интернета", color = Color(0xFF93A7C8), fontSize = 12.sp)
                    }
                    Switch(
                        checked = settings.cameraControlEnabled,
                        onCheckedChange = { updateSettings(settings.copy(cameraControlEnabled = it)) },
                    )
                }

                if (settings.cameraControlEnabled) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        FilterChip(
                            selected = settings.useFrontCamera,
                            onClick = { updateSettings(settings.copy(useFrontCamera = true)) },
                            label = { Text("Фронтальная") },
                        )
                        FilterChip(
                            selected = !settings.useFrontCamera,
                            onClick = { updateSettings(settings.copy(useFrontCamera = false)) },
                            label = { Text("Основная") },
                        )
                    }

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Мини-значок жеста над чубриком", color = Color(0xFFEEF3FF), fontSize = 13.sp)
                        Switch(
                            checked = settings.showGestureBadge,
                            onCheckedChange = { updateSettings(settings.copy(showGestureBadge = it)) },
                        )
                    }
                }
            }
        }

        // Physics & size settings card
        Card(
            colors = CardDefaults.cardColors(containerColor = Color(0xFF161D2E)),
            shape = RoundedCornerShape(16.dp),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(
                modifier = Modifier.padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Text(
                    text = "Физика падений и размер",
                    color = Color(0xFF66FCF1),
                    fontWeight = FontWeight.Bold,
                    fontSize = 16.sp,
                )

                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text("Размер чубрика", color = Color(0xFFEEF3FF), fontSize = 14.sp)
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

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text("Смешное падение при повороте/наклоне", color = Color(0xFFEEF3FF), fontSize = 14.sp)
                    Switch(
                        checked = settings.tiltPhysicsEnabled,
                        onCheckedChange = { updateSettings(settings.copy(tiltPhysicsEnabled = it)) },
                    )
                }

                if (settings.tiltPhysicsEnabled) {
                    Column {
                        Text("Чувствительность наклона: ${(settings.tiltSensitivity * 100).toInt()}%", color = Color(0xFFEEF3FF), fontSize = 13.sp)
                        Slider(
                            value = settings.tiltSensitivity,
                            onValueChange = { updateSettings(settings.copy(tiltSensitivity = it)) },
                            valueRange = 0.2f..1.0f,
                        )
                    }
                }

                Column {
                    Text("Прыгучесть и кувырки: ${(settings.bounciness * 100).toInt()}%", color = Color(0xFFEEF3FF), fontSize = 13.sp)
                    Slider(
                        value = settings.bounciness,
                        onValueChange = { updateSettings(settings.copy(bounciness = it)) },
                        valueRange = 0.25f..1.0f,
                    )
                }

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text("Вибрация при «БУМ!»", color = Color(0xFFEEF3FF), fontSize = 14.sp)
                    Switch(
                        checked = settings.hapticBonkEnabled,
                        onCheckedChange = { updateSettings(settings.copy(hapticBonkEnabled = it)) },
                    )
                }
            }
        }

        // Gesture guide card
        Card(
            colors = CardDefaults.cardColors(containerColor = Color(0xFF131A29)),
            shape = RoundedCornerShape(14.dp),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(
                modifier = Modifier.padding(14.dp),
                verticalArrangement = Arrangement.spacedBy(5.dp),
            ) {
                Text("Жесты ладонью перед камерой:", color = Color(0xFF4DE09D), fontWeight = FontWeight.Bold, fontSize = 14.sp)
                Text("• 🤏 Щипок — схватить и таскать чубрика в воздухе (резко отпустить — полетит в кувырок!)", color = Color(0xFFCBD7EE), fontSize = 12.sp)
                Text("• ✋ Помахать ладонью — погладить (♥ сердечки + румянец)", color = Color(0xFFCBD7EE), fontSize = 12.sp)
                Text("• ✋ Взмах ладонью вверх — весёлый прыжок (✨)", color = Color(0xFFCBD7EE), fontSize = 12.sp)
                Text("• ☝️ Указательный палец — позвать чубрика бежать к руке", color = Color(0xFFCBD7EE), fontSize = 12.sp)
                Text("• ✌️ Два пальца (Peace) — радостный прыжок с сердечками", color = Color(0xFFCBD7EE), fontSize = 12.sp)
                Text("• ✊ Удержать кулак — уложить спать (Zzz)", color = Color(0xFFCBD7EE), fontSize = 12.sp)
                Text("• Если часто ронять/швырять — чубрик разозлится (💢), погладьте ладонью, чтобы успокоить!", color = Color(0xFFFFB8D9), fontSize = 12.sp)
            }
        }

        Spacer(Modifier.height(16.dp))
    }
}
