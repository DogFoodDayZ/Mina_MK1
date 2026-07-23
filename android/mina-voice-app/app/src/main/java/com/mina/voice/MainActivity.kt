package com.mina.voice

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import okhttp3.HttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import org.json.JSONObject
import java.util.Locale
import java.util.concurrent.TimeUnit

class MainActivity : ComponentActivity(), TextToSpeech.OnInitListener {
    private val minaReadableColors: ColorScheme = lightColorScheme(
        primary = Color(0xFF2D6CDF),
        onPrimary = Color(0xFFFFFFFF),
        background = Color(0xFFF5F7FB),
        onBackground = Color(0xFF111827),
        surface = Color(0xFFFFFFFF),
        onSurface = Color(0xFF111827),
        outline = Color(0xFF7C8AA3),
    )

    private val uiScope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val ioScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(6, TimeUnit.SECONDS)
        .readTimeout(12, TimeUnit.SECONDS)
        .writeTimeout(12, TimeUnit.SECONDS)
        .build()
    private lateinit var tts: TextToSpeech

    private var recognizer: SpeechRecognizer? = null
    private var shouldKeepListening = false
    private val restartHandler = Handler(Looper.getMainLooper())
    private var restartScheduled = false
    private var startListeningPending = false
    private var pausedForTextInput = false
    private var ttsReady = false

    private val logs = mutableStateListOf<String>()
    private var partialText by mutableStateOf("")
    private var apiBaseUrl by mutableStateOf(DEFAULT_API_URL)
    private var requireWakeWord by mutableStateOf(true)
    private var speakingEnabled by mutableStateOf(true)
    private var isListening by mutableStateOf(false)
    private var drivingModeEnabled by mutableStateOf(false)
    private var drivingCheckinMinutes by mutableStateOf(20)

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) {
            startContinuousListening()
        } else {
            appendLog("Permission denied: RECORD_AUDIO")
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        apiBaseUrl = loadApiUrl(this)
        drivingModeEnabled = loadDrivingModeEnabled(this)
        drivingCheckinMinutes = loadDrivingCheckinMinutes(this)
        tts = TextToSpeech(this, this)

        setContent {
            MaterialTheme(colorScheme = minaReadableColors) {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background,
                ) {
                    MinaVoiceScreen()
                }
            }
        }
    }

    override fun onInit(status: Int) {
        if (status == TextToSpeech.SUCCESS) {
            tts.language = Locale.US
            ttsReady = true
        } else {
            ttsReady = false
            appendLog("TTS init failed: $status")
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        stopListening()
        tts.stop()
        tts.shutdown()
        uiScope.cancel()
        ioScope.cancel()
    }

    private fun appendLog(line: String) {
        uiScope.launch {
            logs.add(0, line)
            if (logs.size > 120) {
                logs.removeLast()
            }
        }
    }

    private fun runOnMain(block: () -> Unit) {
        if (Looper.myLooper() == Looper.getMainLooper()) {
            block()
        } else {
            restartHandler.post { block() }
        }
    }

    private fun safeUiAction(tag: String, action: () -> Unit) {
        try {
            action()
        } catch (t: Throwable) {
            Log.e("MinaVoice", "UI action failed: $tag", t)
            appendLog("Action failed ($tag): ${t.message}")
        }
    }

    private fun safeSpeak(text: String, utteranceId: String) {
        if (!speakingEnabled || text.isBlank()) return
        runOnMain {
            if (!ttsReady) {
                appendLog("TTS not ready")
                return@runOnMain
            }
            try {
                tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, utteranceId)
            } catch (t: Throwable) {
                Log.e("MinaVoice", "TTS speak failed", t)
                appendLog("TTS error: ${t.message}")
            }
        }
    }

    private fun queueListeningRestart(delayMs: Long) {
        if (!shouldKeepListening || pausedForTextInput || restartScheduled) return
        restartScheduled = true
        restartHandler.postDelayed({
            restartScheduled = false
            beginListeningSession()
        }, delayMs)
    }

    private fun setTextInputFocused(focused: Boolean) {
        if (focused) {
            if (pausedForTextInput) return
            pausedForTextInput = true
            restartHandler.removeCallbacksAndMessages(null)
            restartScheduled = false
            recognizer?.cancel()
            isListening = false
            partialText = ""
        } else {
            if (!pausedForTextInput) return
            pausedForTextInput = false
            partialText = ""
            if (shouldKeepListening) {
                queueListeningRestart(250)
            }
        }
    }

    private fun hasAudioPermission(): Boolean {
        return ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
    }

    private fun ensureAudioPermissionAndStart() {
        if (hasAudioPermission()) {
            startContinuousListening()
        } else {
            permissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    private fun startContinuousListening() {
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            appendLog("Speech recognition not available on this device")
            return
        }

        shouldKeepListening = true
        pausedForTextInput = false

        if (recognizer == null) {
            try {
                recognizer = SpeechRecognizer.createSpeechRecognizer(this).apply {
                setRecognitionListener(object : RecognitionListener {
                    override fun onReadyForSpeech(params: Bundle?) {}
                    override fun onBeginningOfSpeech() {}
                    override fun onRmsChanged(rmsdB: Float) {}
                    override fun onBufferReceived(buffer: ByteArray?) {}
                    override fun onEndOfSpeech() {}

                    override fun onError(error: Int) {
                        runOnMain {
                            isListening = false
                            startListeningPending = false
                            queueListeningRestart(650)
                        }
                    }

                    override fun onResults(results: Bundle?) {
                        val heard = results
                            ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                            ?.firstOrNull()
                            ?.trim()
                            .orEmpty()
                        runOnMain {
                            isListening = false
                            startListeningPending = false
                            partialText = ""
                            if (heard.isNotBlank()) {
                                processTranscript(heard)
                            }
                            queueListeningRestart(350)
                        }
                    }

                    override fun onPartialResults(partialResults: Bundle?) {
                        val next = partialResults
                            ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                            ?.firstOrNull()
                            .orEmpty()
                        runOnMain {
                            partialText = next
                        }
                    }

                    override fun onEvent(eventType: Int, params: Bundle?) {}
                })
            }
            } catch (t: Throwable) {
                Log.e("MinaVoice", "Recognizer init failed", t)
                appendLog("Recognizer init error: ${t.message}")
                shouldKeepListening = false
                return
            }
        }

        beginListeningSession()
    }

    private fun beginListeningSession() {
        if (!shouldKeepListening || pausedForTextInput || startListeningPending || isListening) {
            return
        }
        val r = recognizer ?: return
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 3)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault())
        }
        startListeningPending = true
        try {
            isListening = true
            r.startListening(intent)
            startListeningPending = false
        } catch (e: Exception) {
            isListening = false
            startListeningPending = false
            appendLog("Recognizer start error: ${e.message}")
            queueListeningRestart(800)
        }
    }

    private fun stopListening() {
        shouldKeepListening = false
        pausedForTextInput = false
        startListeningPending = false
        restartScheduled = false
        isListening = false
        restartHandler.removeCallbacksAndMessages(null)
        recognizer?.cancel()
        recognizer?.destroy()
        recognizer = null
        partialText = ""
    }

    private fun processTranscript(spoken: String) {
        val normalized = spoken.trim()
        appendLog("You: $normalized")

        var command = normalized
        if (requireWakeWord) {
            val lower = normalized.lowercase(Locale.US)
            val wakeIndex = lower.indexOf("hey mina")
            val altWakeIndex = lower.indexOf("mina")
            if (wakeIndex >= 0) {
                command = normalized.substring(wakeIndex + "hey mina".length).trim()
            } else if (altWakeIndex >= 0) {
                command = normalized.substring(altWakeIndex + "mina".length).trim()
            } else {
                appendLog("Wake word not detected")
                return
            }
        }

        if (command.isBlank()) {
            appendLog("Wake word heard, waiting for command")
            return
        }

        sendToMina(command)
    }

    private fun sendToMina(command: String) {
        ioScope.launch {
            try {
                val normalizedBase = normalizeApiBaseUrl(apiBaseUrl)
                val processUrls = buildEndpointCandidates(normalizedBase, "/process")
                if (processUrls.isEmpty()) {
                    appendLog("Invalid API URL: $normalizedBase")
                    return@launch
                }
                val payload = JSONObject()
                    .put("input", command)
                    .put("speak_response", false)
                    .put("input_source", "voice")
                    .toString()

                var lastError: String? = null
                for (processUrl in processUrls) {
                    val request = Request.Builder()
                        .url(processUrl)
                        .post(payload.toRequestBody("application/json; charset=utf-8".toMediaType()))
                        .build()

                    httpClient.newCall(request).execute().use { response ->
                        val body = response.body?.string().orEmpty()
                        if (!response.isSuccessful) {
                            lastError = "${response.code} @ ${processUrl.encodedPath}"
                            return@use
                        }
                        val json = runCatching { JSONObject(body) }.getOrNull()
                        val reply = json?.optString("reply")?.takeIf { it.isNotBlank() }
                            ?: "(No reply)"
                        appendLog("Mina: $reply")
                        safeSpeak(reply, "mina-reply")
                        persistWorkingBaseUrlFrom(processUrl)
                        return@launch
                    }
                }

                appendLog("Mina request failed${if (lastError != null) ": $lastError" else ""}")
            } catch (e: Exception) {
                appendLog("Error: ${e.message}")
            }
        }
    }

    private fun sendCompanionCommand(command: String, speakResult: Boolean = false) {
        ioScope.launch {
            try {
                val normalizedBase = normalizeApiBaseUrl(apiBaseUrl)
                val processUrls = buildEndpointCandidates(normalizedBase, "/process")
                if (processUrls.isEmpty()) {
                    appendLog("Invalid API URL: $normalizedBase")
                    return@launch
                }
                val payload = JSONObject()
                    .put("input", command)
                    .put("speak_response", false)
                    .put("input_source", "voice")
                    .toString()

                var lastError: String? = null
                for (processUrl in processUrls) {
                    val request = Request.Builder()
                        .url(processUrl)
                        .post(payload.toRequestBody("application/json; charset=utf-8".toMediaType()))
                        .build()

                    httpClient.newCall(request).execute().use { response ->
                        val body = response.body?.string().orEmpty()
                        if (!response.isSuccessful) {
                            lastError = "${response.code} @ ${processUrl.encodedPath}"
                            return@use
                        }
                        val json = runCatching { JSONObject(body) }.getOrNull()
                        val reply = json?.optString("reply")?.takeIf { it.isNotBlank() }
                            ?: "(No reply)"
                        appendLog("Mina: $reply")
                        if (speakResult) {
                            safeSpeak(reply, "mina-companion")
                        }
                        persistWorkingBaseUrlFrom(processUrl)
                        return@launch
                    }
                }

                appendLog("Companion request failed${if (lastError != null) ": $lastError" else ""}")
            } catch (e: Exception) {
                appendLog("Companion command error: ${e.message}")
            }
        }
    }

    private fun applyDrivingMode(enable: Boolean, minutes: Int) {
        val safeMinutes = minutes.coerceIn(5, 180)
        drivingCheckinMinutes = safeMinutes
        saveDrivingCheckinMinutes(this, safeMinutes)

        drivingModeEnabled = enable
        saveDrivingModeEnabled(this, enable)

        if (enable) {
            appendLog("Driving mode enabled (every $safeMinutes minutes)")
            ensureAudioPermissionAndStart()
            sendCompanionCommand(
                command = "start my driving check-ins every $safeMinutes minutes",
                speakResult = true,
            )
        } else {
            appendLog("Driving mode disabled")
            sendCompanionCommand(
                command = "stop my driving check-ins",
                speakResult = false,
            )
        }
    }

    private fun checkApiStatus(candidateUrl: String) {
        ioScope.launch {
            try {
                val normalizedBase = normalizeApiBaseUrl(candidateUrl)
                if (isWildcardBindAddress(normalizedBase)) {
                    appendLog("0.0.0.0 cannot be reached from phone. Use your host IP, e.g. 100.x.x.x:8000 or 192.168.x.x:8000")
                    return@launch
                }
                val statusUrls = buildEndpointCandidates(normalizedBase, "/status")
                if (statusUrls.isEmpty()) {
                    appendLog("Invalid API URL: $normalizedBase")
                    return@launch
                }

                var lastFailure: String? = null
                for (statusUrl in statusUrls) {
                    val request = Request.Builder()
                        .url(statusUrl)
                        .get()
                        .build()

                    httpClient.newCall(request).execute().use { response ->
                        val body = response.body?.string().orEmpty()
                        if (!response.isSuccessful) {
                            lastFailure = "${response.code} @ ${statusUrl.encodedPath}"
                            return@use
                        }
                        val statusJson = runCatching { JSONObject(body) }.getOrNull()
                        val model = statusJson?.optString("active_model").orEmpty()
                        val activeBase = apiBaseFromEndpoint(statusUrl)
                        if (model.isNotBlank()) {
                            appendLog("API reachable at $activeBase (model: $model)")
                        } else {
                            appendLog("API reachable at $activeBase")
                        }
                        persistWorkingBaseUrl(activeBase)
                        return@launch
                    }
                }

                appendLog("API check failed${if (lastFailure != null) ": $lastFailure" else ""}")
            } catch (e: Exception) {
                appendLog("API check error: ${e.message}")
            }
        }
    }

    private fun buildEndpointCandidates(base: String, endpoint: String): List<HttpUrl> {
        val out = mutableListOf<HttpUrl>()
        val direct = (base.trimEnd('/') + endpoint).toHttpUrlOrNull()
        if (direct != null) out.add(direct)

        val parsedBase = base.toHttpUrlOrNull()
        if (parsedBase != null) {
            val rootBased = parsedBase.newBuilder()
                .encodedPath(endpoint)
                .query(null)
                .fragment(null)
                .build()
            if (out.none { it.toString() == rootBased.toString() }) {
                out.add(rootBased)
            }
        }

        return out
    }

    private fun apiBaseFromEndpoint(url: HttpUrl): String {
        return url.newBuilder()
            .encodedPath("/")
            .query(null)
            .fragment(null)
            .build()
            .toString()
            .trimEnd('/')
    }

    private fun persistWorkingBaseUrlFrom(endpointUrl: HttpUrl) {
        persistWorkingBaseUrl(apiBaseFromEndpoint(endpointUrl))
    }

    private fun persistWorkingBaseUrl(base: String) {
        if (base.isBlank() || base == apiBaseUrl) return
        apiBaseUrl = base
        saveApiUrl(this, base)
        appendLog("Using API URL: $base")
    }

    private fun normalizeApiBaseUrl(raw: String): String {
        val trimmed = raw.trim().trimEnd('/', '.', ',', ';')
        if (trimmed.isEmpty()) {
            return DEFAULT_API_URL
        }

        val dottedPort = Regex("""^(\d{1,3}(?:\.\d{1,3}){3})\.(\d{2,5})(/.*)?$""")
        val fixedDottedPort = dottedPort.replace(trimmed) { m ->
            val host = m.groupValues[1]
            val port = m.groupValues[2]
            val tail = m.groupValues.getOrElse(3) { "" }
            "$host:$port$tail"
        }

        val lower = fixedDottedPort.lowercase(Locale.US)
        val hasScheme = lower.startsWith("http://") || lower.startsWith("https://")
        val withScheme = if (hasScheme) fixedDottedPort else "http://$fixedDottedPort"
        if (withScheme.toHttpUrlOrNull() != null) {
            return withScheme
        }

        // Recover common typo form: IPv6-like hosts entered without [] brackets.
        val scheme = if (lower.startsWith("https://")) "https" else "http"
        val rest = if (hasScheme) fixedDottedPort.substringAfter("://") else fixedDottedPort
        val slashIdx = rest.indexOf('/')
        val authority = if (slashIdx >= 0) rest.substring(0, slashIdx) else rest
        val tail = if (slashIdx >= 0) rest.substring(slashIdx) else ""

        if (!authority.startsWith("[") && authority.count { it == ':' } >= 2) {
            val lastColon = authority.lastIndexOf(':')
            val hostPart = authority.substring(0, lastColon)
            val portPart = authority.substring(lastColon + 1)
            val repairedAuthority = if (portPart.all { it.isDigit() }) {
                "[$hostPart]:$portPart"
            } else {
                "[$authority]"
            }
            val repaired = "$scheme://$repairedAuthority$tail"
            if (repaired.toHttpUrlOrNull() != null) {
                return repaired.trimEnd('/')
            }
        }

        return withScheme
    }

    private fun isWildcardBindAddress(base: String): Boolean {
        val host = base.toHttpUrlOrNull()?.host?.lowercase(Locale.US) ?: return false
        return host == "0.0.0.0" || host == "::" || host == "[::]"
    }

    @Composable
    private fun MinaVoiceScreen() {
        var apiDraft by remember { mutableStateOf(apiBaseUrl) }
        var driveMinutesDraft by remember { mutableStateOf(drivingCheckinMinutes.toString()) }

        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            item {
                Text("Mina Android Voice Link", style = MaterialTheme.typography.headlineSmall)
            }

            item {
                OutlinedTextField(
                    value = apiDraft,
                    onValueChange = { apiDraft = it },
                    modifier = Modifier
                        .fillMaxWidth()
                        .onFocusChanged { setTextInputFocused(it.isFocused) },
                    label = { Text("MK1 API URL") },
                    singleLine = true
                )
            }

            item {
                Text(
                    "Use: http://phone-or-lan-ip:8000",
                    style = MaterialTheme.typography.bodySmall
                )
            }

            item {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    Button(
                        modifier = Modifier.weight(1f),
                        onClick = {
                            safeUiAction("save_api") {
                                val normalized = normalizeApiBaseUrl(apiDraft)
                                if (isWildcardBindAddress(normalized)) {
                                    appendLog("0.0.0.0 is a server bind address, not a phone destination. Use your host IP.")
                                    return@safeUiAction
                                }
                                if (buildEndpointCandidates(normalized, "/status").isEmpty()) {
                                    appendLog("Invalid API URL: $normalized")
                                    return@safeUiAction
                                }
                                apiBaseUrl = normalized
                                apiDraft = apiBaseUrl
                                saveApiUrl(this@MainActivity, apiBaseUrl)
                                appendLog("Saved API URL: $apiBaseUrl")
                            }
                        }
                    ) {
                        Text("Save API")
                    }

                    Button(
                        modifier = Modifier.weight(1f),
                        onClick = {
                            safeUiAction("check_api") {
                                checkApiStatus(apiDraft)
                            }
                        }
                    ) {
                        Text("Check API")
                    }
                }
            }

            item {
                OutlinedTextField(
                    value = driveMinutesDraft,
                    onValueChange = { driveMinutesDraft = it.filter { ch -> ch.isDigit() }.take(3) },
                    label = { Text("Check-in every (minutes)") },
                    singleLine = true,
                    modifier = Modifier
                        .fillMaxWidth()
                        .onFocusChanged { setTextInputFocused(it.isFocused) }
                )
            }

            item {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Text("Driving Mode", style = MaterialTheme.typography.bodyMedium)
                    Switch(
                        checked = drivingModeEnabled,
                        onCheckedChange = { enabled ->
                            safeUiAction("toggle_driving_mode") {
                                val parsed = driveMinutesDraft.toIntOrNull() ?: drivingCheckinMinutes
                                val safe = parsed.coerceIn(5, 180)
                                driveMinutesDraft = safe.toString()
                                applyDrivingMode(enabled, safe)
                            }
                        }
                    )
                }
            }

            item {
                Button(
                    modifier = Modifier.fillMaxWidth(),
                    onClick = {
                        safeUiAction("toggle_listening") {
                            if (isListening) stopListening() else ensureAudioPermissionAndStart()
                        }
                    }
                ) {
                    Text(if (isListening) "Stop Listening" else "Start Listening")
                }
            }

            item {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Text("Require 'Hey Mina'", style = MaterialTheme.typography.bodyMedium)
                    Switch(checked = requireWakeWord, onCheckedChange = { requireWakeWord = it })
                }
            }

            item {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Text("Speak Replies", style = MaterialTheme.typography.bodyMedium)
                    Switch(checked = speakingEnabled, onCheckedChange = { speakingEnabled = it })
                }
            }

            if (partialText.isNotBlank()) {
                item {
                    Card(modifier = Modifier.fillMaxWidth()) {
                        Text(
                            modifier = Modifier.padding(12.dp),
                            text = "Heard: $partialText",
                            style = MaterialTheme.typography.bodySmall,
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis
                        )
                    }
                }
            }

            item {
                Spacer(Modifier.height(4.dp))
                Text("Activity Log", style = MaterialTheme.typography.titleMedium)
            }

            items(logs.take(80)) { line ->
                Card(modifier = Modifier.fillMaxWidth()) {
                    Text(
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
                        text = line,
                        style = MaterialTheme.typography.bodySmall,
                        maxLines = 3,
                        overflow = TextOverflow.Ellipsis
                    )
                }
            }
        }
    }

    companion object {
        private const val PREFS = "mina_voice_prefs"
        private const val KEY_API_URL = "api_url"
        private const val KEY_DRIVING_MODE_ENABLED = "driving_mode_enabled"
        private const val KEY_DRIVING_CHECKIN_MINUTES = "driving_checkin_minutes"
        private const val DEFAULT_API_URL = "http://127.0.0.1:8000"

        private fun loadApiUrl(context: Context): String {
            return context.getSharedPreferences(PREFS, MODE_PRIVATE)
                .getString(KEY_API_URL, DEFAULT_API_URL)
                ?: DEFAULT_API_URL
        }

        private fun saveApiUrl(context: Context, value: String) {
            context.getSharedPreferences(PREFS, MODE_PRIVATE)
                .edit()
                .putString(KEY_API_URL, value)
                .apply()
        }

        private fun loadDrivingModeEnabled(context: Context): Boolean {
            return context.getSharedPreferences(PREFS, MODE_PRIVATE)
                .getBoolean(KEY_DRIVING_MODE_ENABLED, false)
        }

        private fun saveDrivingModeEnabled(context: Context, value: Boolean) {
            context.getSharedPreferences(PREFS, MODE_PRIVATE)
                .edit()
                .putBoolean(KEY_DRIVING_MODE_ENABLED, value)
                .apply()
        }

        private fun loadDrivingCheckinMinutes(context: Context): Int {
            val v = context.getSharedPreferences(PREFS, MODE_PRIVATE)
                .getInt(KEY_DRIVING_CHECKIN_MINUTES, 20)
            return v.coerceIn(5, 180)
        }

        private fun saveDrivingCheckinMinutes(context: Context, value: Int) {
            context.getSharedPreferences(PREFS, MODE_PRIVATE)
                .edit()
                .putInt(KEY_DRIVING_CHECKIN_MINUTES, value.coerceIn(5, 180))
                .apply()
        }
    }
}
