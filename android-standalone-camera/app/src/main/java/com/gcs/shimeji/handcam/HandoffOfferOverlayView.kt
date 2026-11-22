package com.gcs.shimeji.handcam

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.view.Gravity
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView

/**
 * Floating always-on-top overlay banner shown over any app when PC offers a pet handoff.
 * User can accept with one tap OR hands-free by showing an Open Palm (✋) to the camera.
 */
@SuppressLint("ViewConstructor")
class HandoffOfferOverlayView(
    context: Context,
    private val onAcceptTouch: () -> Unit,
    private val onDeclineTouch: () -> Unit,
) : LinearLayout(context) {

    private val titleView: TextView
    private val subtitleView: TextView

    init {
        orientation = VERTICAL
        val d = resources.displayMetrics.density
        val pad = (14 * d).toInt()
        setPadding(pad, pad, pad, pad)
        background = GradientDrawable().apply {
            setColor(Color.argb(240, 18, 25, 40))
            cornerRadius = 16 * d
            setStroke((2 * d).toInt(), Color.rgb(102, 252, 241))
        }

        titleView = TextView(context).apply {
            text = "✨ Чубрик летит с ПК!"
            setTextColor(Color.rgb(102, 252, 241))
            textSize = 15f
            typeface = Typeface.DEFAULT_BOLD
        }
        addView(titleView)

        subtitleView = TextView(context).apply {
            text = "Нажмите «Принять» или покажите ✋ ладонь камере"
            setTextColor(Color.rgb(210, 224, 245))
            textSize = 12f
            setPadding(0, (4 * d).toInt(), 0, (10 * d).toInt())
        }
        addView(subtitleView)

        val btnRow = LinearLayout(context).apply {
            orientation = HORIZONTAL
            gravity = Gravity.END
        }

        val declineBtn = Button(context).apply {
            text = "Отклонить"
            setTextColor(Color.WHITE)
            background = GradientDrawable().apply {
                setColor(Color.rgb(58, 69, 89))
                cornerRadius = 10 * d
            }
            setPadding((14 * d).toInt(), (6 * d).toInt(), (14 * d).toInt(), (6 * d).toInt())
            setOnClickListener { onDeclineTouch() }
        }

        val acceptBtn = Button(context).apply {
            text = "Принять ✋"
            setTextColor(Color.rgb(11, 20, 26))
            typeface = Typeface.DEFAULT_BOLD
            background = GradientDrawable().apply {
                setColor(Color.rgb(77, 224, 157))
                cornerRadius = 10 * d
            }
            setPadding((16 * d).toInt(), (6 * d).toInt(), (16 * d).toInt(), (6 * d).toInt())
            setOnClickListener { onAcceptTouch() }
        }

        val spacer = LayoutParams((8 * d).toInt(), 1)
        btnRow.addView(declineBtn)
        btnRow.addView(android.view.View(context), spacer)
        btnRow.addView(acceptBtn)
        addView(btnRow)
    }

    fun bindOffer(sourceName: String, edge: String, timeoutSec: Float) {
        val edgeRu = when (edge) {
            "left" -> "слева"
            "right" -> "справа"
            "top" -> "сверху"
            else -> "с экрана"
        }
        titleView.text = "✨ Чубрик летит с $sourceName!"
        subtitleView.text = "Принять ($edgeRu) за ${timeoutSec.toInt()} сек — тап или ✋ ладонь"
    }
}
