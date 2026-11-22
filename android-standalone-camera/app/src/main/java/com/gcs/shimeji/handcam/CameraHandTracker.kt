package com.gcs.shimeji.handcam

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Matrix
import android.os.SystemClock
import android.util.Log
import android.util.Size
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import com.google.mediapipe.framework.image.BitmapImageBuilder
import com.google.mediapipe.tasks.core.BaseOptions
import com.google.mediapipe.tasks.vision.core.RunningMode
import com.google.mediapipe.tasks.vision.handlandmarker.HandLandmarker
import com.google.mediapipe.tasks.vision.handlandmarker.HandLandmarkerResult
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong

/**
 * Offline on-device hand tracker combining CameraX ImageAnalysis + MediaPipe HandLandmarker (21 pts).
 */
class CameraHandTracker(
    private val context: Context,
    private val lifecycleOwner: LifecycleOwner,
    private val onGestureFrame: (HandGestureFrame) -> Unit,
    private val onStatusChanged: (String) -> Unit,
) {
    private val classifier = HandGestureClassifier(holdFrames = 2, smoothAlpha = 0.42f)
    private var cameraProvider: ProcessCameraProvider? = null
    private var handLandmarker: HandLandmarker? = null
    private var analysisExecutor: ExecutorService? = null

    private val isRunning = AtomicBoolean(false)
    private val lastFrameMs = AtomicLong(0L)
    @Volatile
    private var useFrontCamera: Boolean = true

    fun start(frontCamera: Boolean) {
        if (isRunning.get() && useFrontCamera == frontCamera) return
        stop()
        useFrontCamera = frontCamera
        classifier.reset()

        val landmarker = createLandmarker() ?: return
        handLandmarker = landmarker
        analysisExecutor = Executors.newSingleThreadExecutor()
        isRunning.set(true)
        onStatusChanged("Запуск камеры...")

        val providerFuture = ProcessCameraProvider.getInstance(context)
        providerFuture.addListener(
            {
                if (!isRunning.get()) return@addListener
                runCatching {
                    val provider = providerFuture.get()
                    cameraProvider = provider
                    bindCamera(provider)
                }.onFailure { err ->
                    Log.e(TAG, "CameraX bind failed", err)
                    onStatusChanged("Ошибка камеры: ${err.localizedMessage ?: "недоступна"}")
                    stop()
                }
            },
            ContextCompat.getMainExecutor(context),
        )
    }

    fun stop() {
        isRunning.set(false)
        runCatching { cameraProvider?.unbindAll() }
        cameraProvider = null
        runCatching { handLandmarker?.close() }
        handLandmarker = null
        runCatching { analysisExecutor?.shutdownNow() }
        analysisExecutor = null
        classifier.reset()
        onGestureFrame(HandGestureFrame(detected = false, gesture = HandGesture.NONE, timestampMs = SystemClock.uptimeMillis()))
    }

    private fun createLandmarker(): HandLandmarker? {
        return runCatching {
            val baseOptions = BaseOptions.builder()
                .setModelAssetPath(MODEL_ASSET_NAME)
                .build()
            val options = HandLandmarker.HandLandmarkerOptions.builder()
                .setBaseOptions(baseOptions)
                .setRunningMode(RunningMode.LIVE_STREAM)
                .setNumHands(1)
                .setMinHandDetectionConfidence(0.45f)
                .setMinHandPresenceConfidence(0.45f)
                .setMinTrackingConfidence(0.45f)
                .setResultListener { result, _ -> handleMediaPipeResult(result) }
                .setErrorListener { err ->
                    Log.w(TAG, "MediaPipe runtime error", err)
                }
                .build()
            HandLandmarker.createFromOptions(context, options)
        }.onFailure { err ->
            Log.e(TAG, "Failed to initialize MediaPipe HandLandmarker", err)
            onStatusChanged("AI-модель ладони недоступна: ${err.localizedMessage ?: "нет файла"}")
        }.getOrNull()
    }

    private fun bindCamera(provider: ProcessCameraProvider) {
        val preferredSelector = if (useFrontCamera) {
            CameraSelector.DEFAULT_FRONT_CAMERA
        } else {
            CameraSelector.DEFAULT_BACK_CAMERA
        }
        val selector = when {
            provider.hasCamera(preferredSelector) -> preferredSelector
            provider.hasCamera(CameraSelector.DEFAULT_FRONT_CAMERA) -> {
                useFrontCamera = true
                CameraSelector.DEFAULT_FRONT_CAMERA
            }
            provider.hasCamera(CameraSelector.DEFAULT_BACK_CAMERA) -> {
                useFrontCamera = false
                CameraSelector.DEFAULT_BACK_CAMERA
            }
            else -> {
                onStatusChanged("Камера не найдена на устройстве")
                return
            }
        }

        val executor = analysisExecutor ?: return
        @Suppress("DEPRECATION")
        val imageAnalysis = ImageAnalysis.Builder()
            .setTargetResolution(Size(320, 240))
            .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
            .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888)
            .build()

        imageAnalysis.setAnalyzer(executor) { imageProxy ->
            analyzeImage(imageProxy)
        }

        provider.unbindAll()
        provider.bindToLifecycle(lifecycleOwner, selector, imageAnalysis)
        val camName = if (useFrontCamera) "Фронтальная" else "Основная"
        onStatusChanged("AI 21-точка активна ($camName)")
    }

    private fun analyzeImage(imageProxy: ImageProxy) {
        try {
            if (!isRunning.get()) return
            val landmarker = handLandmarker ?: return
            val now = SystemClock.uptimeMillis()
            // Cap analysis to ~18 FPS for smooth gestures + low battery usage
            if (now - lastFrameMs.get() < 55L) return
            lastFrameMs.set(now)

            val rawBitmap = imageProxy.toBitmap()
            val rotDeg = imageProxy.imageInfo.rotationDegrees
            val uprightBitmap = if (rotDeg != 0) {
                val matrix = Matrix().apply { postRotate(rotDeg.toFloat()) }
                val rotated = Bitmap.createBitmap(rawBitmap, 0, 0, rawBitmap.width, rawBitmap.height, matrix, false)
                if (rotated != rawBitmap) rawBitmap.recycle()
                rotated
            } else {
                rawBitmap
            }

            val mpImage = BitmapImageBuilder(uprightBitmap).build()
            landmarker.detectAsync(mpImage, now)
        } catch (err: Exception) {
            Log.w(TAG, "Frame analysis skipped: ${err.message}")
        } finally {
            imageProxy.close()
        }
    }

    private fun handleMediaPipeResult(result: HandLandmarkerResult) {
        if (!isRunning.get()) return
        val now = SystemClock.uptimeMillis()
        val firstHand = result.landmarks().firstOrNull()
        val points = firstHand?.map { Landmark2D(it.x(), it.y()) }
        val gestureFrame = classifier.processLandmarks(
            landmarks = points,
            timestampMs = now,
            mirrorX = useFrontCamera,
        )
        onGestureFrame(gestureFrame)
    }

    companion object {
        private const val TAG = "CameraHandTracker"
        private const val MODEL_ASSET_NAME = "hand_landmarker.task"
    }
}
