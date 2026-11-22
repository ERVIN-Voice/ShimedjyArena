package com.gcs.shimeji.overlay

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Rect
import org.json.JSONObject

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

    companion object {
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
