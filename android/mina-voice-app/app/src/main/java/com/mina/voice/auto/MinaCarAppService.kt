package com.mina.voice.auto

import android.content.Context
import android.content.Intent
import androidx.car.app.CarContext
import androidx.car.app.CarAppService
import androidx.car.app.Screen
import androidx.car.app.Session
import androidx.car.app.model.Action
import androidx.car.app.model.ActionStrip
import androidx.car.app.model.ItemList
import androidx.car.app.model.Pane
import androidx.car.app.model.PaneTemplate
import androidx.car.app.model.Row
import androidx.car.app.model.SearchTemplate
import androidx.car.app.model.Template
import androidx.car.app.validation.HostValidator
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
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
    private val apiClient = MinaCarApiClient(carContext)
    private var apiStatusText: String = "Checking API..."
    private var activityText: String = "Ready"

    init {
        refreshApiStatus()
    }

    private fun refreshApiStatus() {
        apiStatusText = "Checking..."
        invalidate()

        ioScope.launch {
            val next = apiClient.checkStatus()
            carContext.mainExecutor.execute {
                apiStatusText = next
                invalidate()
            }
        }
    }

    private fun runPreset(label: String, command: String) {
        activityText = "$label..."
        invalidate()
        ioScope.launch {
            val reply = apiClient.sendCommand(command)
            carContext.mainExecutor.execute {
                activityText = reply
                invalidate()
            }
        }
    }

    override fun onGetTemplate(): Template {
        val pane = Pane.Builder()
            .addRow(
                Row.Builder()
                    .setTitle("Mina API")
                    .addText(apiStatusText)
                    .addText(activityText.take(120))
                    .build()
            )
            .addRow(
                Row.Builder()
                    .setTitle("Ask Mina")
                    .addText("Type or use Android Auto dictation")
                    .setOnClickListener {
                        screenManager.push(MinaCommandScreen(carContext, apiClient))
                    }
                    .build()
            )
            .addRow(
                Row.Builder()
                    .setTitle("Start Driving Check-ins")
                    .addText("Have Mina check in every 20 minutes")
                    .setOnClickListener {
                        runPreset(
                            "Starting check-ins",
                            "Start driving mode with check-ins every 20 minutes"
                        )
                    }
                    .build()
            )
            .addRow(
                Row.Builder()
                    .setTitle("What's Next")
                    .addText("Read active reminders and next tasks")
                    .setOnClickListener {
                        runPreset("Checking tasks", "What are my active reminders and next tasks?")
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

}

private class MinaCommandScreen(
    carContext: CarContext,
    private val apiClient: MinaCarApiClient,
) : Screen(carContext) {
    private val ioScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var submittedText = ""
    private var replyText = "Say or type a command, then submit it."
    private var loading = false

    private val searchCallback = object : SearchTemplate.SearchCallback {
        override fun onSearchSubmitted(searchText: String) {
            val command = searchText.trim()
            if (command.isEmpty()) return
            submittedText = command
            loading = true
            invalidate()
            ioScope.launch {
                val reply = apiClient.sendCommand(command)
                carContext.mainExecutor.execute {
                    replyText = reply
                    loading = false
                    invalidate()
                }
            }
        }
    }

    override fun onGetTemplate(): Template {
        val builder = SearchTemplate.Builder(searchCallback)
            .setHeaderAction(Action.BACK)
            .setSearchHint("Ask Mina")
            .setShowKeyboardByDefault(false)
            .setInitialSearchText(submittedText)

        if (loading) {
            builder.setLoading(true)
        } else {
            builder.setItemList(
                ItemList.Builder()
                    .addItem(
                        Row.Builder()
                            .setTitle(if (submittedText.isBlank()) "Mina" else submittedText)
                            .addText(replyText.take(200))
                            .build()
                    )
                    .build()
            )
        }
        return builder.build()
    }
}

private class MinaCarApiClient(context: Context) {
    private val apiBaseUrl = normalizeApiBaseUrl(loadApiUrl(context))
    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(4, TimeUnit.SECONDS)
        .readTimeout(12, TimeUnit.SECONDS)
        .writeTimeout(8, TimeUnit.SECONDS)
        .build()

    fun checkStatus(): String {
        if (isLoopbackBase()) return "Set a reachable API URL in the phone app"
        val request = Request.Builder().url(apiBaseUrl.trimEnd('/') + "/status").get().build()
        return try {
            httpClient.newCall(request).execute().use { response ->
                if (response.isSuccessful) "Online" else "Unreachable (${response.code})"
            }
        } catch (e: Exception) {
            "Unreachable (${e.message ?: "error"})"
        }
    }

    fun sendCommand(command: String): String {
        if (isLoopbackBase()) return "Set a reachable API URL in the phone app first"
        val payload = JSONObject()
            .put("input", command)
            .put("speak_response", false)
            .put("input_source", "android_auto")
            .toString()
        return try {
            val request = Request.Builder()
                .url(apiBaseUrl.trimEnd('/') + "/process")
                .post(payload.toRequestBody("application/json; charset=utf-8".toMediaType()))
                .build()
            httpClient.newCall(request).execute().use { response ->
                val body = response.body?.string().orEmpty()
                if (!response.isSuccessful) return "Request failed (${response.code})"
                JSONObject(body).optString("reply").takeIf { it.isNotBlank() } ?: "No reply"
            }
        } catch (e: Exception) {
            "Request error (${e.message ?: "error"})"
        }
    }

    private fun isLoopbackBase(): Boolean {
        val lower = apiBaseUrl.lowercase(Locale.US)
        return lower.contains("127.0.0.1") || lower.contains("localhost") || lower.contains("0.0.0.0")
    }

    companion object {
        private const val PREFS = "mina_voice_prefs"
        private const val KEY_API_URL = "api_url"
        private const val DEFAULT_API_URL = "http://127.0.0.1:8000"

        private fun loadApiUrl(context: Context): String {
            return context.getSharedPreferences(PREFS, CarContext.MODE_PRIVATE)
                .getString(KEY_API_URL, DEFAULT_API_URL)
                ?: DEFAULT_API_URL
        }

        private fun normalizeApiBaseUrl(raw: String): String {
            val trimmed = raw.trim().trimEnd('/', '.', ',', ';')
            if (trimmed.isEmpty()) return DEFAULT_API_URL
            val dottedPort = Regex("""^(\d{1,3}(?:\.\d{1,3}){3})\.(\d{2,5})(/.*)?$""")
            val fixed = dottedPort.replace(trimmed) { match ->
                val tail = match.groupValues.getOrElse(3) { "" }
                "${match.groupValues[1]}:${match.groupValues[2]}$tail"
            }
            val lower = fixed.lowercase(Locale.US)
            return if (lower.startsWith("http://") || lower.startsWith("https://")) fixed else "http://$fixed"
        }
    }
}
