package com.mina.voice.auto

import android.content.Intent
import androidx.car.app.CarContext
import androidx.car.app.CarAppService
import androidx.car.app.Screen
import androidx.car.app.Session
import androidx.car.app.model.Action
import androidx.car.app.model.ActionStrip
import androidx.car.app.model.Pane
import androidx.car.app.model.PaneTemplate
import androidx.car.app.model.Row
import androidx.car.app.model.Template
import androidx.car.app.validation.HostValidator
import com.mina.voice.MainActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.Locale
import java.util.concurrent.TimeUnit

class MinaCarAppService : CarAppService() {
    override fun createHostValidator(): HostValidator {
        // Restrict to hosts that the Car App Library considers safe for release.
        return HostValidator.ALLOW_ALL_HOSTS_VALIDATOR
    }

    override fun onCreateSession(): Session {
        return object : Session() {
            override fun onCreateScreen(intent: Intent): Screen {
                return MinaCarHomeScreen(carContext)
            }
        }
    }
}

private class MinaCarHomeScreen(carContext: CarContext) : Screen(carContext) {
    private val ioScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(4, TimeUnit.SECONDS)
        .readTimeout(6, TimeUnit.SECONDS)
        .writeTimeout(6, TimeUnit.SECONDS)
        .build()

    private var apiBaseUrl: String = loadApiUrl(carContext)
    private var apiStatusText: String = "Checking API..."

    init {
        refreshApiStatus()
    }

    private fun refreshApiStatus() {
        val base = normalizeApiBaseUrl(apiBaseUrl)
        val statusUrl = (base.trimEnd('/') + "/status")
        apiStatusText = "Checking: $base"
        invalidate()

        ioScope.launch {
            val next = try {
                val request = Request.Builder()
                    .url(statusUrl)
                    .get()
                    .build()
                httpClient.newCall(request).execute().use { response ->
                    if (response.isSuccessful) {
                        "Online (${response.code})"
                    } else {
                        "Unreachable (${response.code})"
                    }
                }
            } catch (e: Exception) {
                "Unreachable (${e.message ?: "error"})"
            }

            apiStatusText = next
            invalidate()
        }
    }

    private fun normalizeApiBaseUrl(raw: String): String {
        val trimmed = raw.trim().trimEnd('/', '.', ',', ';')
        if (trimmed.isEmpty()) return DEFAULT_API_URL

        val dottedPort = Regex("""^(\d{1,3}(?:\.\d{1,3}){3})\.(\d{2,5})(/.*)?$""")
        val fixed = dottedPort.replace(trimmed) { m ->
            val host = m.groupValues[1]
            val port = m.groupValues[2]
            val tail = m.groupValues.getOrElse(3) { "" }
            "$host:$port$tail"
        }

        val lower = fixed.lowercase(Locale.US)
        return if (lower.startsWith("http://") || lower.startsWith("https://")) fixed else "http://$fixed"
    }

    override fun onGetTemplate(): Template {
        val pane = Pane.Builder()
            .addRow(
                Row.Builder()
                    .setTitle("Mina API")
                    .addText(apiStatusText)
                    .addText(apiBaseUrl)
                    .build()
            )
            .addRow(
                Row.Builder()
                    .setTitle("Open Mina on phone")
                    .addText("Open full voice controls and setup")
                    .setOnClickListener {
                        val launchIntent = Intent(carContext, MainActivity::class.java).apply {
                            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                        }
                        carContext.startActivity(launchIntent)
                    }
                    .build()
            )
            .addRow(
                Row.Builder()
                    .setTitle("Refresh API Status")
                    .addText("Re-check MK1 reachability")
                    .setOnClickListener {
                        refreshApiStatus()
                    }
                    .build()
            )
            .build()

        val actions = ActionStrip.Builder()
            .addAction(
                Action.Builder()
                    .setTitle("Refresh")
                    .setOnClickListener { refreshApiStatus() }
                    .build()
            )
            .build()

        return PaneTemplate.Builder(pane)
            .setTitle("Mina Voice")
            .setHeaderAction(Action.APP_ICON)
            .setActionStrip(actions)
            .build()
    }

    companion object {
        private const val PREFS = "mina_voice_prefs"
        private const val KEY_API_URL = "api_url"
        private const val DEFAULT_API_URL = "http://127.0.0.1:8000"

        private fun loadApiUrl(context: CarContext): String {
            return context.getSharedPreferences(PREFS, CarContext.MODE_PRIVATE)
                .getString(KEY_API_URL, DEFAULT_API_URL)
                ?: DEFAULT_API_URL
        }
    }
}
