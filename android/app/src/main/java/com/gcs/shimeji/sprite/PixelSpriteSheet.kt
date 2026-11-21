package com.gcs.shimeji.sprite

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import com.gcs.shimeji.model.GcsJson
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class AndroidAvatarConfigFile(
    val avatar: AndroidAvatarVisual,
    val animations: Map<String, AndroidAnimationSpec>,
)

@Serializable
data class AndroidAvatarVisual(
    val id: String = "placeholder",
    val name: String = "GCS Pixel Companion",
    @SerialName("frame_width") val frameWidth: Int = 32,
    @SerialName("frame_height") val frameHeight: Int = 32,
    @SerialName("default_scale") val defaultScale: Int = 4,
)

@Serializable
data class AndroidAnimationSpec(
    val row: Int,
    val frames: Int,
    val fps: Float = 6f,
    val loop: Boolean = true,
    val next: String? = null,
)

/**
 * Loads the pixel-art sprite sheet from Android assets with zero filtering/scaling,
 * slicing exact frames for FilterQuality.None nearest-neighbor rendering in Compose.
 */
class PixelSpriteSheet private constructor(
    val config: AndroidAvatarConfigFile,
    private val sheetBitmap: Bitmap,
) {
    private val frameCache = HashMap<String, ImageBitmap>()

    companion object {
        fun loadFromAssets(
            context: Context,
            configAssetName: String = "avatar_config.json",
            sheetAssetName: String = "placeholder_spritesheet.png",
        ): PixelSpriteSheet {
            val configText = context.assets.open(configAssetName).bufferedReader().use { it.readText() }
            val parsed = GcsJson.decodeFromString(AndroidAvatarConfigFile.serializer(), configText)

            val options = BitmapFactory.Options().apply {
                inScaled = false
                inPreferredConfig = Bitmap.Config.ARGB_8888
            }
            val bitmap = context.assets.open(sheetAssetName).use { stream ->
                BitmapFactory.decodeStream(stream, null, options)
            } ?: error("Failed to decode pixel sprite sheet asset: $sheetAssetName")

            return PixelSpriteSheet(parsed, bitmap)
        }
    }

    fun specFor(animation: String): AndroidAnimationSpec {
        return config.animations[animation] ?: config.animations["idle"]
            ?: AndroidAnimationSpec(row = 0, frames = 4, fps = 4f, loop = true)
    }

    fun getFrame(animation: String, frameIndex: Int, facingLeft: Boolean): ImageBitmap {
        val spec = specFor(animation)
        val idx = ((frameIndex % spec.frames) + spec.frames) % spec.frames
        val key = "$animation:$idx:$facingLeft"
        return frameCache.getOrPut(key) {
            val fw = config.avatar.frameWidth
            val fh = config.avatar.frameHeight
            val rawFrame = Bitmap.createBitmap(sheetBitmap, idx * fw, spec.row * fh, fw, fh)
            val oriented = if (facingLeft) {
                val matrix = Matrix().apply { preScale(-1f, 1f) }
                Bitmap.createBitmap(rawFrame, 0, 0, fw, fh, matrix, false).also {
                    if (it !== rawFrame) rawFrame.recycle()
                }
            } else {
                rawFrame
            }
            oriented.asImageBitmap()
        }
    }
}
