package com.gcs.shimeji.handcam

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.Rect
import org.json.JSONObject
import java.util.EnumMap

data class AnimRowSpec(
    val row: Int,
    val frames: Int,
    val fps: Float,
    val loop: Boolean,
)

class PixelSpriteSheet(
    val bitmap: Bitmap,
    val frameWidth: Int,
    val frameHeight: Int,
    val animations: Map<String, AnimRowSpec>,
) {
    private val recoloredCache = EnumMap<PetEmotion, Bitmap>(PetEmotion::class.java)

    fun getSpec(name: String): AnimRowSpec {
        return animations[name] ?: animations["idle"] ?: AnimRowSpec(0, 4, 4f, true)
    }

    fun fillSourceRect(animName: String, frameIndex: Int, outRect: Rect) {
        val spec = getSpec(animName)
        val col = frameIndex.coerceAtLeast(0) % spec.frames.coerceAtLeast(1)
        val left = col * frameWidth
        val top = spec.row * frameHeight
        outRect.set(left, top, left + frameWidth, top + frameHeight)
    }

    /**
     * Return a cached crisp pixel-art sprite sheet recolored for the active PetEmotion.
     * Replaces only the 3 body shades (#56C6E0, #92E8F8, #308AAE), keeping eyes, outline,
     * specular white highlights, and cheek accents untouched.
     */
    fun bitmapForEmotion(emotion: PetEmotion): Bitmap {
        if (emotion == PetEmotion.NEUTRAL) return bitmap
        recoloredCache[emotion]?.let { return it }
        val palette = EMOTION_PALETTES[emotion] ?: return bitmap
        val recolored = recolorBitmap(bitmap, palette)
        recoloredCache[emotion] = recolored
        return recolored
    }

    companion object {
        private val BASE_BODY = Color.rgb(86, 198, 224)
        private val BASE_HIGHLIGHT = Color.rgb(146, 232, 248)
        private val BASE_SHADE = Color.rgb(48, 138, 174)

        private data class BodyPalette(val body: Int, val highlight: Int, val shade: Int)

        private val EMOTION_PALETTES: Map<PetEmotion, BodyPalette> = mapOf(
            PetEmotion.HAPPY to BodyPalette(Color.rgb(255, 213, 79), Color.rgb(255, 241, 168), Color.rgb(212, 155, 26)),
            PetEmotion.LOVE to BodyPalette(Color.rgb(255, 110, 168), Color.rgb(255, 180, 210), Color.rgb(201, 59, 118)),
            PetEmotion.EXCITED to BodyPalette(Color.rgb(76, 232, 138), Color.rgb(166, 249, 197), Color.rgb(31, 164, 90)),
            PetEmotion.SCARED to BodyPalette(Color.rgb(122, 148, 232), Color.rgb(185, 202, 252), Color.rgb(71, 94, 179)),
            PetEmotion.DIZZY to BodyPalette(Color.rgb(180, 124, 255), Color.rgb(221, 194, 255), Color.rgb(123, 70, 199)),
            PetEmotion.ANGRY to BodyPalette(Color.rgb(255, 77, 77), Color.rgb(255, 154, 154), Color.rgb(189, 34, 34)),
            PetEmotion.SLEEPY to BodyPalette(Color.rgb(136, 140, 224), Color.rgb(191, 194, 245), Color.rgb(85, 89, 168)),
        )

        private fun recolorBitmap(src: Bitmap, palette: BodyPalette): Bitmap {
            val w = src.width
            val h = src.height
            val pixels = IntArray(w * h)
            src.getPixels(pixels, 0, w, 0, 0, w, h)
            val baseBodyRgb = BASE_BODY and 0x00FFFFFF
            val baseHiRgb = BASE_HIGHLIGHT and 0x00FFFFFF
            val baseShRgb = BASE_SHADE and 0x00FFFFFF
            val newBodyRgb = palette.body and 0x00FFFFFF
            val newHiRgb = palette.highlight and 0x00FFFFFF
            val newShRgb = palette.shade and 0x00FFFFFF

            for (i in pixels.indices) {
                val p = pixels[i]
                val alpha = p and -0x1000000 // 0xFF000000
                if (alpha == 0) continue
                when (p and 0x00FFFFFF) {
                    baseBodyRgb -> pixels[i] = alpha or newBodyRgb
                    baseHiRgb -> pixels[i] = alpha or newHiRgb
                    baseShRgb -> pixels[i] = alpha or newShRgb
                }
            }
            return Bitmap.createBitmap(pixels, w, h, Bitmap.Config.ARGB_8888)
        }
        fun loadFromAssets(context: Context): PixelSpriteSheet {
            val opts = BitmapFactory.Options().apply {
                inScaled = false
                inPreferredConfig = Bitmap.Config.ARGB_8888
            }
            val bmp = context.assets.open("placeholder_spritesheet.png").use { stream ->
                BitmapFactory.decodeStream(stream, null, opts)
            } ?: error("Missing placeholder_spritesheet.png in assets")

            var frameW = 32
            var frameH = 32
            val anims = mutableMapOf<String, AnimRowSpec>()

            runCatching {
                val rawJson = context.assets.open("avatar_config.json").bufferedReader().use { it.readText() }
                val root = JSONObject(rawJson)
                val visual = root.optJSONObject("visual")
                if (visual != null) {
                    frameW = visual.optInt("frame_width", 32)
                    frameH = visual.optInt("frame_height", 32)
                }
                val animObj = root.optJSONObject("animations")
                if (animObj != null) {
                    val keys = animObj.keys()
                    while (keys.hasNext()) {
                        val key = keys.next()
                        val item = animObj.optJSONObject(key) ?: continue
                        anims[key] = AnimRowSpec(
                            row = item.optInt("row", 0),
                            frames = item.optInt("frames", 4).coerceAtLeast(1),
                            fps = item.optDouble("fps", 6.0).toFloat().coerceAtLeast(1f),
                            loop = item.optBoolean("loop", true),
                        )
                    }
                }
            }

            if (anims.isEmpty()) {
                anims["idle"] = AnimRowSpec(0, 4, 4f, true)
                anims["walk"] = AnimRowSpec(1, 6, 8f, true)
                anims["run"] = AnimRowSpec(2, 6, 12f, true)
                anims["jump"] = AnimRowSpec(3, 3, 8f, false)
                anims["sleep"] = AnimRowSpec(4, 4, 2f, true)
                anims["grab"] = AnimRowSpec(5, 2, 6f, true)
                anims["handoff"] = AnimRowSpec(6, 4, 10f, false)
                anims["receive"] = AnimRowSpec(7, 4, 10f, false)
                anims["landing"] = AnimRowSpec(8, 3, 10f, false)
            }

            return PixelSpriteSheet(bmp, frameW, frameH, anims)
        }
    }
}
